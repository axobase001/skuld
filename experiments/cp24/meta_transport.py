"""Local Muse Glimmer transport for the isolated CP24 validation."""
import json, os, time, threading, ipaddress
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
from transformers import AutoTokenizer

MODEL='muse-glimmer'
MODEL_PATH='/content/models/muse_glimmer_30b_bf16'
MODEL_REVISION='cec94fcfd6512f2cfe708138c32533b346ea4f69'
SERVICE_URL='http://127.0.0.1:30000/v1/chat/completions'
INPUT_LIMIT=65536
OUTPUT_LIMIT=8192
LIFE_CALL_LIMIT=240

def utc(): return datetime.now(timezone.utc).isoformat()

class LocalMetaTransport:
    def __init__(self, root, name):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.name=name; self.path=self.root/'calls.jsonl'; self.lock=threading.RLock()
        self.rows=[json.loads(x) for x in self.path.read_text().splitlines()] if self.path.exists() else []
        self.used={name:max((x.get('local_call_index',0) for x in self.rows),default=0)}; self.block=None
        started={(x.get('local_call_index'),x.get('attempt')) for x in self.rows if x.get('state')=='STARTED'}
        ended={(x.get('local_call_index'),x.get('attempt')) for x in self.rows if x.get('state')!='STARTED'}
        if started-ended: self.block='UNRESOLVED_LOCAL_REQUEST'
        self.tokenizer=AutoTokenizer.from_pretrained(MODEL_PATH,local_files_only=True,use_fast=True)
        if not getattr(self.tokenizer,'chat_template',None): raise RuntimeError('MODEL_CHAT_TEMPLATE_MISSING')
    def _append(self,row):
        with self.path.open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n'); f.flush(); os.fsync(f.fileno())
        self.rows.append(dict(row))
    def __call__(self,endpoint,key,payload,*unused):
        with self.lock:
            if self.block: raise RuntimeError(self.block)
            parsed=urlparse(endpoint)
            if endpoint!=SERVICE_URL or parsed.hostname!='127.0.0.1' or payload.get('model')!=MODEL or key!='local-placeholder-not-a-credential':
                self.block='LOCAL_MODEL_ROUTE_MISMATCH'; raise RuntimeError(self.block)
            if self.used[self.name]>=LIFE_CALL_LIMIT:
                self.block='LIFE_CALL_LIMIT'; raise RuntimeError(self.block)
            wire=json.loads(json.dumps(payload,ensure_ascii=False))
            if wire.get('response_format')!={'type':'json_object'}: raise RuntimeError('JSON_CONTRACT_MISMATCH')
            embedded=json.loads(wire['messages'][-1]['content'])
            schema=embedded.get('schema')
            if not isinstance(schema,dict): raise RuntimeError('EMBEDDED_SCHEMA_MISSING')
            wire['response_format']={'type':'json_schema','json_schema':{'name':'CP24Decision','schema':schema,'strict':True}}
            wire['temperature']=1.0; wire['top_p']=0.95
            wire['messages'][0]['content']='Reasoning strength: low. '+wire['messages'][0]['content']
            encoded=self.tokenizer.apply_chat_template(wire['messages'],tokenize=True,add_generation_prompt=True)
            ids=encoded['input_ids'] if hasattr(encoded,'keys') else encoded
            n=len(ids[0]) if ids and isinstance(ids[0],list) else len(ids)
            if n>INPUT_LIMIT or n+int(wire['max_tokens'])>131072:
                self.block='META_INPUT_CONTEXT_LIMIT'; raise RuntimeError(self.block)
            if int(wire['max_tokens'])>OUTPUT_LIMIT: raise RuntimeError('META_OUTPUT_LIMIT')
            local_index=self.used[self.name]+1; self.used[self.name]=local_index
        for attempt in range(1,4):
            row={'local_call_index':local_index,'attempt':attempt,'trajectory':self.name,'started_at_utc':utc(),
                 'model_revision':MODEL_REVISION,'backend':'sglang-v0.5.23','input_tokens_exact_template':n,
                 'request':wire,'state':'STARTED'}
            with self.lock:self._append(row)
            began=time.monotonic()
            try:
                response=httpx.post(endpoint,headers={'Authorization':'Bearer '+key},json=wire,timeout=300)
                elapsed=time.monotonic()-began
                raw=response.json(); choice=(raw.get('choices') or [{}])[0]
                ok=response.status_code==200 and raw.get('model')==MODEL and choice.get('finish_reason')=='stop'
                row.update(state='RETURNED',http_status=response.status_code,elapsed_seconds=elapsed,
                    local_backend_request_id=raw.get('id'),served_model=raw.get('model'),
                    usage=raw.get('usage'),finish_reason=choice.get('finish_reason'),response=raw,
                    finished_at_utc=utc(),valid_transport=ok)
                with self.lock:self._append(row)
                if not ok:
                    self.block='LOCAL_BACKEND_RESPONSE_INVALID'
                    return {'error_type':self.block,'error_detail':'status/model/finish contract failed'}
                return {'http_status':response.status_code,'body':response.text,'headers':dict(response.headers)}
            except (httpx.ConnectError,httpx.ConnectTimeout,httpx.ReadTimeout,httpx.RemoteProtocolError) as exc:
                row.update(state='UNKNOWN_LOCAL_GENERATION',error_type=type(exc).__name__,elapsed_seconds=time.monotonic()-began,finished_at_utc=utc())
                with self.lock:self._append(row)
                if attempt==3:
                    self.block='LOCAL_TRANSPORT_RETRIES_EXHAUSTED'
                    return {'error_type':type(exc).__name__,'error_detail':'local generation unavailable after bounded retries'}
                time.sleep(min(2**attempt,4))
            except Exception as exc:
                row.update(state='FAILED',error_type=type(exc).__name__,error_detail=str(exc)[:500],finished_at_utc=utc())
                with self.lock:self._append(row)
                self.block='LOCAL_TRANSPORT_DETERMINISTIC_FAILURE'
                return {'error_type':type(exc).__name__,'error_detail':str(exc)[:500]}
