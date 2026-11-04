"""Bounded joint resource decisions through Runtime acquisition and authorized agency execution."""
import json
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict
class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Risk(Strict):
 object_id:str;p_delayed:float|None=Field(ge=0,le=1)
class Decision(Strict):
 action:Literal['RESERVE','RELEASE','WAIT'];object_id:str|None;state_version:int;risk_estimates:list[Risk];used_prediction_ids:list[str];reason:str
class Branch(Strict):
 action:Literal['RESERVE','RELEASE','WAIT'];object_id:str|None;state_version:int;target_object_id:str;target_day:int;p_on_time:float=Field(ge=0,le=1);assumptions:list[str];consequence:str
class Forecast(Strict):branches:list[Branch]=Field(max_length=3);reason:str
RULES=('Minimize total reservation + cancellation + missed-delivery business loss across responsibilities. One backup slot; one effective manual change per day. RESERVE deadline inclusive, held until target. A RESERVE accepted by the booking deadline and continuously held through target guarantees on-time final delivery even when the original supplier is delayed. Passing the booking deadline does not invalidate an existing reservation; it only forbids a new reservation. RELEASE removes that guarantee. Reservation fee is already paid and nonrefundable, so it is a sunk cost when deciding whether to keep a held slot; RELEASE pays the object-specific additional cancel_fee, not the cumulative cancellation total. Automatic target release is free. Future commitments have no revealed condition. Infer risk only from observed supplier outcomes; successful backup does NOT imply original supplier ready. Choose exactly one legal action. Consider opportunity cost to the next two public commitments. Probability estimates concern ORIGINAL supplier delayed, not backup-assisted final delivery. Unknown outcomes are unknown. No prose substitutes for an action.')
def compact(board):
 counts={}
 for e in board['results']:
  c=counts.setdefault(e['condition'],dict(observed=0,delayed=0,ready=0));c['observed']+=1;c[e['supplier_result']]+=1
 return {k:board[k] for k in ('day','state_version','slot','open_responsibilities','future_commitments','legal_actions','costs')} | {'observed_risk_counts':counts,'recent_complete_events':board['results'][-8:],'recent_actions':board['actions'][-8:],'rules':RULES,'history_selection':'all actually sampled supplier outcomes counted once; last 8 complete outcomes and actions; identical B/C'}
def record(rt,u,kind,key,value,source):return rt.node(u,kind,key,value,source)
def execute(rt,u,cid,d,src):
 if d.action=='WAIT':return {'status':'WAIT','ok':True}
 rt.channel(u,cid).check_operation(d.action,{'object_id':d.object_id,'state_version':d.state_version})
 mandate=rt.mandate_for(u,cid,d.action)
 if mandate is None:raise ValueError('NO_SIMULATION_MANDATE')
 dec=record(rt,u,'Decision','resource:'+str(d.state_version),d.model_dump(),src)
 goal=record(rt,u,'Goal','resource:'+d.object_id,{'text':'Manage the shared backup for '+d.object_id,'status':'active'},src)
 artifact=record(rt,u,'PreparedArtifact','resource:'+dec.node_id,{'content':d.reason,'object_id':d.object_id,'state_version':d.state_version},src)
 action=rt._ground(u,dec.node_id,goal,artifact,cid,d.action,{'object_id':d.object_id,'state_version':d.state_version},1.0,src)
 record(rt,u,'ProductWork','resource:'+action.node_id,{'kind':'EXECUTE','action_id':action.node_id,'channel_id':cid,'operation':d.action,'parameters':{'object_id':d.object_id,'state_version':d.state_version},'artifact_node_id':artifact.node_id,'level':4,'risk_class':1},src)
 approval,result=rt.run_under_mandate(u,action.node_id,mandate,src)
 return {**result,'action_id':action.node_id,'approval_id':approval,'ok':result['status']=='SUCCEEDED'}
def resource_tick(rt,u):
 from skuld.app.runtime import ProviderFailure,ProviderFormatFailure
 rows=rt.store._conn.execute("SELECT channel_id FROM runtime_channels WHERE user_id=? AND kind='resource_simulation'",(u,)).fetchall()
 if len(rows)!=1:raise ValueError('one isolated resource channel required')
 cid=rows[0][0];observations=rt.observe_channel(u,cid);board=next(o['value'] for o in observations if o['key']=='board');day=board['day']
 src=rt.graph.get_source_event(u,rt._last_loop_sample[(u,cid)]['source_id'])
 # Only actually sampled public results enter either arm's persistent history.
 seen={n.content['object_id'] for n in rt.nodes(u,'ResourceOutcome')}
 for e in board['results']:
  if e['object_id'] not in seen:record(rt,u,'ResourceOutcome',e['object_id'],{**e,'source_id':cid,'observation_source_event_id':src.source_event_id,'sampled_at':rt.clock(),'status':'COMPLETED' if e['on_time'] else 'FAILED'},src)
 settled={n.content['prediction_id'] for n in rt.nodes(u,'ResourceForecastSettlement')}
 outcomes={e['object_id']:e for e in board['results']}
 for p in rt.nodes(u,'Prediction'):
  b=p.content
  if b.get('kind')!='resource_conditional' or p.node_id in settled or b['target_object_id'] not in outcomes:continue
  choice=next((n.content for n in rt.nodes(u,'ResourceChoice') if n.content['day']==b['day']),None)
  selected=bool(choice and choice['execution'].get('ok') and choice['decision']['action']==b['action'] and choice['decision']['object_id']==b['object_id'])
  interventions=[a for a in board['actions'] if b['day']<a['day']<=b['target_day'] and a['action']!='WAIT']
  status='NOT_EXECUTED' if not selected else 'INTERVENED' if interventions else 'RESOLVED'
  e=outcomes[b['target_object_id']];record(rt,u,'ResourceForecastSettlement',p.node_id,{'prediction_id':p.node_id,'status':status,'target_object_id':b['target_object_id'],'outcome':e if status=='RESOLVED' else None,'interventions':interventions,'squared_error':(b['p_on_time']-int(e['on_time']))**2 if status=='RESOLVED' else None,'observation_source_event_id':src.source_event_id,'host_rule_computed':True},src)
 if any(n.content['day']==day for n in rt.nodes(u,'ResourceTick')):return
 record(rt,u,'ResourceTick',str(day),{'day':day,'state_version':board['state_version'],'observation_source_event_id':src.source_event_id},src)
 if not board['open_responsibilities'] or rt.config.get('resource_observe_only'):return
 view=compact(board);view['source_observation']=src.source_event_id;view['joint_object_scope']=[t['object_id'] for t in board['open_responsibilities']];view['recent_conditional_settlements']=[n.content for n in rt.nodes(u,'ResourceForecastSettlement')][-6:]
 predictions=[];calls=0
 def call(stage,model):
  nonlocal calls
  if calls>=2:raise RuntimeError('resource tick request bound')
  calls+=1;v={**view,'stage':stage,'predictions':predictions}
  return rt.call(u,stage,json.dumps(v,ensure_ascii=False),model,'resource:'+str(day)+':'+str(calls))
 if rt.config.get('predictive_modeling',True):
  try:
   response,receipt=call('RESOURCE_PREDICT',Forecast)
   for i,b in enumerate(response.branches):
    t=next((t for t in board['open_responsibilities'] if t['object_id']==b.target_object_id),None)
    if b.state_version!=board['state_version'] or {'action':b.action,'object_id':b.object_id} not in board['legal_actions'] or not t or t['target_day']!=b.target_day:
     record(rt,u,'ResourceRejection',str(day)+':branch:'+str(i),{'day':day,'reason':'INVALID_CONDITIONAL_SCOPE','candidate':b.model_dump()},src);continue
    n=record(rt,u,'Prediction',str(day)+':branch:'+str(i),{'kind':'resource_conditional','day':day,**b.model_dump(),'provider_receipt_id':receipt.node_id,'intervention':'explicit_action_condition'},src);predictions.append({'node_id':n.node_id,**b.model_dump()})
  except (ProviderFailure,ProviderFormatFailure) as exc:record(rt,u,'ResourceFailure',str(day)+':predict',{'day':day,'stage':'predict','error':str(exc)},src)
 while calls<2:
  try:
   d,receipt=call('RESOURCE_DECIDE',Decision)
   if d.state_version!=board['state_version'] or {'action':d.action,'object_id':d.object_id} not in board['legal_actions']:raise ValueError('INVALID_ACTION_OR_STATE')
   if any(x.object_id not in view['joint_object_scope'] for x in d.risk_estimates):raise ValueError('RISK_OBJECT_OUTSIDE_SCOPE')
   if any(x not in [p['node_id'] for p in predictions] for x in d.used_prediction_ids):raise ValueError('INVALID_PREDICTION_REFERENCE')
   result=execute(rt,u,cid,d,src);record(rt,u,'ResourceChoice',str(day),{'day':day,'decision':d.model_dump(),'provider_receipt_id':receipt.node_id,'observation_source_event_id':src.source_event_id,'execution':result},src)
   if result.get('status')=='IN_DOUBT':raise RuntimeError('simulation action outcome unknown')
   return
  except (ProviderFailure,ProviderFormatFailure,ValueError) as exc:
   record(rt,u,'ResourceFailure',str(day)+':decide:'+str(calls),{'day':day,'stage':'decide','error':str(exc)},src);view['previous_attempt_error']=str(exc)
