"""Isolated CP24 S-EVO runner with one local Muse Glimmer route and daily recovery snapshots."""
import os, sys, json, time, shutil, sqlite3, hashlib, subprocess, itertools, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from experiments.cp24 import cp24_runner as cp24
from experiments.cp24.meta_transport import LocalMetaTransport,MODEL,MODEL_PATH,MODEL_REVISION,INPUT_LIMIT,OUTPUT_LIMIT,LIFE_CALL_LIMIT
from skuld.app.runtime import Runtime
from skuld.loop.tool_subject import attach,state
from skuld.sim.providers import COMMON

LOCAL_BASE=Path(os.environ.get('SKULD_META_OUTPUT_DIR','/content/skuld-meta/runs'))
DRIVE_BASE=Path(os.environ.get('SKULD_META_ARCHIVE_DIR','/content/drive/MyDrive/skuld-meta-archive'))
SOURCE_IMPORT_REFERENCE='077315dae516e01bb1e3f54528eac23c6ee22d62'

def write_json(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    tmp.replace(p)

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()

def checkpoint(dest,day,rt,u,transport,reason):
    if not os.path.ismount('/content/drive') or not DRIVE_BASE.parent.is_dir():raise RuntimeError('DRIVE_NOT_MOUNTED')
    cp24_public=dest/'environment-private.json'
    if not cp24_public.is_file():raise RuntimeError('ENVIRONMENT_SNAPSHOT_MISSING')
    at=dest/'checkpoints';at.mkdir(exist_ok=True)
    index=len(list(at.glob('cp_*')))+1
    local=at/f'cp_{index:03d}_day{day:02d}'
    local.mkdir(exist_ok=False)
    source=sqlite3.connect(str(dest/'runtime/world.db'))
    target=sqlite3.connect(str(local/'world.db'))
    source.backup(target);source.close()
    if target.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('SQLITE_BACKUP_INVALID')
    target.close()
    for name in ['environment-private.json','identity.json','calls.jsonl','progress.json','errors.json']:
        f=dest/name
        if f.exists():shutil.copy2(f,local/name)
    receipt=dest/'runtime/receipts.jsonl'
    if receipt.exists():shutil.copy2(receipt,local/'receipts.jsonl')
    executions=rt.nodes(u,'ToolExecution')
    manifest={'reason':reason,'day':day,'at_utc':datetime.now(timezone.utc).isoformat(),
        'user_id':u,'state_turn':state(rt,u).get('turn'),'public_executions':len(executions),
        'last_execution_id':executions[-1].node_id if executions else None,
        'model':MODEL,'model_revision':MODEL_REVISION,'source_import_reference':SOURCE_IMPORT_REFERENCE,
        'files':{f.name:{'bytes':f.stat().st_size,'sha256':sha(f)} for f in local.iterdir() if f.is_file()}}
    write_json(local/'checkpoint_manifest.json',manifest)
    remote=DRIVE_BASE/dest.name/'checkpoints'/local.name
    remote.parent.mkdir(parents=True,exist_ok=True)
    if remote.exists():raise RuntimeError('CHECKPOINT_OVERWRITE_REFUSED')
    shutil.copytree(local,remote)
    for name,meta in manifest['files'].items():
        f=remote/name
        if f.stat().st_size!=meta['bytes'] or sha(f)!=meta['sha256']:raise RuntimeError('DRIVE_CHECKPOINT_VERIFY_FAILED:'+name)
    return manifest

def config():
    return {'gateway_key':'skuld-meta-local','default_route':'meta','max_model_calls':LIFE_CALL_LIMIT,
        'routes':{'meta':{'key':'local-placeholder-not-a-credential','base_url':'http://127.0.0.1:30000/v1',
          'model':MODEL,'provider':'local-sglang','transport':'chat_json_object'}},**COMMON,
        'tool_subject':True,'business_events':True,'tool_subject_arm':'S-EVO','sec_learner':False,
        'provider_format_retry':False,'max_input_tokens':INPUT_LIMIT,'max_output_tokens':OUTPUT_LIMIT,
        'input_tokenizer_path':str(Path(MODEL_PATH)/'tokenizer.json'),
        'reasoningbank_guidance':cp24.reasoningbank_guidance()}

def run(seed,phase,resume=False,turn_cap=None,restart_at_day=None):
    if phase not in ('E2E','E2E_R2','E2E_R3','CONFIRM','CONFIRM_R2') or seed not in (240101,240102):raise RuntimeError('RUN_ID_NOT_ALLOWED')
    if phase in ('CONFIRM','CONFIRM_R2') and turn_cap is not None:raise RuntimeError('CONFIRM_TURN_CAP_FORBIDDEN')
    if not os.path.ismount('/content/drive') or not DRIVE_BASE.parent.is_dir():raise RuntimeError('DRIVE_NOT_MOUNTED')
    cp24.check_public_dependencies()
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():raise RuntimeError('META_SOURCE_DIRTY')
    name=f'META_{phase}_S-EVO_seed{seed}'
    dest=LOCAL_BASE/name
    if resume:
        if not (dest/'identity.json').exists() or not (dest/'environment-private.json').exists():raise RuntimeError('RESUME_STATE_MISSING')
        if (dest/'score.json').exists() and json.loads((dest/'score.json').read_text()).get('finalized'):raise RuntimeError('FINALIZED_CANNOT_RESUME')
    else:dest.mkdir(parents=True,exist_ok=False)
    transport=cp24.PublicMCP(dest,seed,resume=resume)
    budget=LocalMetaTransport(dest,name)
    if budget.block:raise RuntimeError(budget.block)
    cfg=config();logical=[0]
    rt=Runtime(dest/'runtime',cfg,clock=lambda:(datetime(2026,1,5,tzinfo=timezone.utc)+timedelta(
        days=transport.last_day-1,minutes=transport.minute,microseconds=logical[0])).isoformat(),provider_transport=budget)
    u=json.loads((dest/'identity.json').read_text())['user_id'] if resume else rt.provision(name,True)['user_id']
    if resume:
        logical[0]=state(rt,u).get('turn',0)
        before=json.loads((dest/'restart_before.json').read_text()) if (dest/'restart_before.json').exists() else None
        if before and (before['user_id']!=u or [n.node_id for n in rt.nodes(u,'ToolExecution')][:len(before['execution_ids'])]!=before['execution_ids']):
            raise RuntimeError('RESTART_IDENTITY_OR_EFFECT_PREFIX_MISMATCH')
        write_json(dest/'restart_after.json',{'new_agent_pid':os.getpid(),'user_id':u,
            'inherited_turn':logical[0],'environment_day':transport.last_day,
            'execution_prefix_matched':True,'at_utc':datetime.now(timezone.utc).isoformat()})
    rt.tool_budget=budget;rt.tool_budget_arm=name;attach(rt,u,transport)
    if not resume:
        write_json(dest/'identity.json',{'user_id':u,'seed':seed,'arm':'S-EVO','phase':phase,
            'source_import_reference':SOURCE_IMPORT_REFERENCE,'model':MODEL,'model_revision':MODEL_REVISION})
        write_json(dest/'config.json',{k:v for k,v in cfg.items() if k not in ('routes','gateway_key')})
        write_json(dest/'tools.json',transport.tools())
        (dest/'briefing.md').write_text(transport.briefing(),encoding='utf-8')
    started=time.monotonic();errors=json.loads((dest/'errors.json').read_text()) if (dest/'errors.json').exists() else []
    outcome='running';consecutive=0;stalled=0;turn=int(state(rt,u).get('turn',0));last_cp_day=0
    try:
        for _ in itertools.count():
            if turn_cap is not None and turn>=turn_cap:outcome='e2e_turn_cap';break
            if transport.over():outcome='simulation_ended';break
            turn+=1
            before_execution=len(rt.nodes(u,'ToolExecution'));before_clock=(transport.last_day,transport.minute)
            try:
                logical[0]+=1;rt.loop_round(u);consecutive=0
            except Exception as exc:
                errors.append({'turn':turn,'day':transport.last_day,'type':type(exc).__name__,'message':str(exc)[:1000]})
                consecutive+=1;write_json(dest/'errors.json',errors)
                if str(exc)=='BUDGET_CALL_LIMIT':outcome='model_call_budget_exhausted';break
                if budget.block or type(exc).__name__=='BudgetStop' or 'STATE_BINDING_ERROR' in str(exc):
                    outcome='model_or_runtime_guard';break
                if consecutive>=5:outcome='consecutive_runtime_errors';break
            transport.last_day=int(transport._server.ENGINE.s['day'])
            transport.minute=int(transport._server.ENGINE.s['minute'])
            executions=rt.nodes(u,'ToolExecution')
            stalled=stalled+1 if len(executions)==before_execution and (transport.last_day,transport.minute)==before_clock else 0
            write_json(dest/'progress.json',{'day':transport.last_day,'turn':turn,'calls':budget.used.get(name,0),
                'public_executions':len(executions),'last_execution_id':executions[-1].node_id if executions else None,
                'stalled_rounds':stalled,'model_block':budget.block,'agent_pid':os.getpid()})
            if transport.last_day>last_cp_day:
                checkpoint(dest,transport.last_day,rt,u,transport,'DAILY_SAFE_BOUNDARY');last_cp_day=transport.last_day
            print('PROGRESS',name,'day',transport.last_day,'turn',turn,'calls',budget.used.get(name,0),flush=True)
            if stalled>=5:outcome='no_public_tool_progress';break
            if restart_at_day and transport.last_day>=restart_at_day and phase in ('CONFIRM','CONFIRM_R2') and not resume:
                write_json(dest/'restart_before.json',{'old_agent_pid':os.getpid(),'user_id':u,'day':transport.last_day,
                    'turn':state(rt,u).get('turn'),'execution_ids':[n.node_id for n in executions],
                    'at_utc':datetime.now(timezone.utc).isoformat()})
                outcome='planned_process_restart';break
        if transport.over():outcome='simulation_ended'
    finally:
        terminal=transport.over()
        score=transport.finalize() if terminal else transport._server.ENGINE.score(finalize=False)
        write_json(dest/'score.json',{'official':score,'harness_outcome':outcome,'elapsed_seconds_this_process':time.monotonic()-started,
            'requests':budget.used.get(name,0),'errors':errors,'finalized':terminal,'model_block':budget.block})
        write_json(dest/'subject_state.json',state(rt,u))
        checkpoint(dest,transport.last_day,rt,u,transport,'PROCESS_EXIT_'+outcome)
        rt.close();transport.close()
    print('RUN_EXIT',name,outcome,'day',transport.last_day,'calls',budget.used.get(name,0),flush=True)
    return outcome

if __name__=='__main__':
    a=argparse.ArgumentParser()
    a.add_argument('--seed',type=int,required=True);a.add_argument('--phase',choices=['E2E','E2E_R2','E2E_R3','CONFIRM','CONFIRM_R2'],required=True)
    a.add_argument('--resume',action='store_true');a.add_argument('--turn-cap',type=int);a.add_argument('--restart-at-day',type=int)
    args=a.parse_args();run(args.seed,args.phase,args.resume,args.turn_cap,args.restart_at_day)
