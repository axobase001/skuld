"""CP24 zero-API replay from a sealed CP23 formal stopped-life snapshot.

Read-only public state and SQLite are copied; no hidden environment state or
public business tool is invoked. The fixture answers only after capturing the
full wire request. Original failed life stays untouched.
"""
import json,sqlite3,sys,re
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
from skuld.app.runtime import Runtime
from skuld.sim.providers import offline_config,completion
from skuld.loop.tool_subject import attach,state
from skuld.loop.business_events import current_slots

src=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=False)
(out/'runtime').mkdir();old=sqlite3.connect(f'file:{src / "world_consistent.db"}?mode=ro',uri=True);fresh=sqlite3.connect(out/'runtime/world.db');old.backup(fresh);fresh.close();old.close()
config={**offline_config(10000),**json.loads((src/'config.json').read_text())};config['input_tokenizer_path']='/content/cp24_tokenizer.json';config['provider_format_retry']=False
user=json.loads((src/'identity.json').read_text())['user_id'];progress=json.loads((src/'progress.json').read_text());name=src.name.split('_STOPPED')[0]
class Public:
 def __init__(self):self.last_day=progress['day'];self.minute=540;self.calls=[]
 def tools(self):return json.loads((src/'tools.json').read_text())
 def briefing(self):return (src/'briefing.md').read_text()
 def over(self):return False
 def call(self,name,args):self.calls.append([name,args]);raise RuntimeError('ZERO_API_REPLAY_CANNOT_EXECUTE_PUBLIC_TOOL')
class Model:
 def __init__(self):self.requests=[]
 def __call__(self,endpoint,key,payload,*args):
  self.requests.append(payload)
  if 'PUBLIC_TOOL_REFLECT' in json.dumps(payload):return completion({'attribution':'offline fixture only','evidence_ids':[],'memory_items':[],'candidates':[]})
  return completion({'actions':[],'ledger':'offline fixture','plan':'offline fixture','predictions':[],'responsibilities':[],'reason':'offline fixture','used_evidence_ids':[],'used_candidate_ids':[],'responsibility_ids':[]})
t=Public();m=Model();logical=[progress['turn']]
clock=lambda:(datetime(2026,1,5,tzinfo=timezone.utc)+timedelta(days=t.last_day-1,minutes=t.minute,microseconds=logical[0])).isoformat()
rt=Runtime(out/'runtime',config,clock=clock,provider_transport=m)
rt.config['routes'][rt.meta(user)['route']]=rt.config['routes']['scripted']
last=rt.nodes(user,'ToolObservation')[-1:]
if last:
 text=last[0].content.get('text','');matches=re.findall(r'(\d{2}):(\d{2})[^\n]*balance',text,re.I)
 if matches:t.minute=int(matches[-1][0])*60+int(matches[-1][1])
rt.tool_budget=SimpleNamespace(used={name:progress['calls']});rt.tool_budget_arm=name;attach(rt,user,t)
expected=current_slots(rt,user,progress['day']);failure=None
try:rt.loop_round(user)
except Exception as exc:failure={'type':type(exc).__name__,'message':str(exc)}
(out/'wire_requests.json').write_text(json.dumps(m.requests,ensure_ascii=False,indent=2))
(out/'replay_result.json').write_text(json.dumps({'source':str(src),'original_error':json.loads((src/'errors.json').read_text()),'failure':failure,'actual_model_requests':len(m.requests),'public_tool_calls':len(t.calls),'expected_current_slots':len(expected),'request_token_counts':[rt._input_size(x) for x in m.requests]},ensure_ascii=False,indent=2))
assert failure is None,failure
assert m.requests,'NO_ACTUAL_MODEL_REQUEST'
assert not t.calls,'PUBLIC_TOOL_EXECUTED'
def task(req):
 for msg in req['messages']:
  try:outer=json.loads(msg['content']);v=outer.get('task');v=json.loads(v) if isinstance(v,str) else v
  except (KeyError,ValueError,TypeError):continue
  if isinstance(v,dict) and v.get('stage')=='PUBLIC_TOOL_DECIDE':return v
 raise AssertionError('NO_DECIDE_INPUT')
ctx=task(m.requests[0]);inv=ctx['business_state']['current_inventory']
assert inv['row_count']==len(expected),('SLOT_COUNT_CHANGED',inv['row_count'],len(expected))
assert ctx['legal_reference_ids'] and ctx['business_state']['responsibilities']['rows']
assert rt._input_size(m.requests[0])<=32768,'WIRE_LIMIT'
(out/'checks.json').write_text(json.dumps({'same_current_slot_count':True,'actual_decide_wire_sent':True,'within_wire_token_limit':True,'no_public_calls':True,'original_score_not_replaced':True},indent=2))
rt.close();print('LATE_CONTEXT_REPLAY_GREEN',name,len(expected),len(m.requests))
