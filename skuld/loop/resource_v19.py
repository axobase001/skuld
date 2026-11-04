"""CP19 two-stage independent forecasts, persistent calibration and unchanged execution."""
import json,copy,math
from typing import Any,Literal
from pydantic import Field,ValidationError
from .resource_v14 import Decision,Branch,admit_risks
from .resource_loop import Strict,RULES,record,execute
from .resource_context import execution_contract
from skuld.learning.resource_calibration import load_state,save_state,build_values
class RawBranchSchema(Branch):continuation:Literal['HOLD_TO_TARGET','NO_FURTHER_RESOURCE_ACTION','UNSPECIFIED']
class Forecast19(Strict):
 supplier_forecasts:list[Any]=Field(description='At most one per currently revealed unresolved object: {object_id,target_day,issued_day,state_version,event:SUPPLIER_DELAYED,p_supplier_delayed:0..1,evidence_ids:[current observation ID]}. Duplicate/invalid entries invalidate that object; no averaging.')
 branches:list[Any]=Field(max_length=3,description='Optional action branches; each must match branch_schema in task. Local branch admission, not a second supplier sample.')
 reason:str
CONTRACT={'supplier_forecasts':'One per currently published, condition-revealed, unresolved object; event SUPPLIER_DELAYED; target_day exact; issued_day=current day; state_version=current state. p_supplier_delayed in [0,1]; evidence_ids nonempty current observation only. No forecast for unrevealed future commitments. Missing/duplicate/invalid is recorded, never repaired.', 'branch_schema':RawBranchSchema.model_json_schema(), 'continuation':'HOLD_TO_TARGET requires target backup after action, continuously held through target. NO_FURTHER_RESOURCE_ACTION means no subsequent manual resource change; existing backup remains held automatically. UNSPECIFIED has no unique final probability. Branch supplier probability is raw only, never a calibration sample.'}

def facts(board,source_id):
 return {k:copy.deepcopy(board[k]) for k in ('day','state_version','slot','open_responsibilities','future_commitments','legal_actions','costs')}|{'supplier_history':[{k:e[k] for k in ('object_id','condition','observed_day','target_day','supplier_result')} for e in board['results']],'action_history':[{k:a[k] for k in ('day','action','object_id','fee','before_slot','after_slot')} for a in board['actions']],'rules':RULES,'source_observation':source_id,'legal_evidence_ids':[source_id],'output_contract':CONTRACT}

def admit_supplier(rt,u,board,src,items,receipt,state):
 groups={};valid={};forecasts=[];tasks={t['object_id']:t for t in board['open_responsibilities']}
 for i,x in enumerate(items):
  oid=x.get('object_id') if isinstance(x,dict) else None
  if not isinstance(oid,str):record(rt,u,'SupplierRiskRejection',f"{board['day']}:{i}",{'day':board['day'],'index':i,'reason':'INVALID_OBJECT','raw':x},src);continue
  groups.setdefault(oid,[]).append((i,x))
 for oid,entries in groups.items():
  i,x=entries[0];reason=None;t=tasks.get(oid);p=x.get('p_supplier_delayed')
  if len(entries)!=1:reason='DUPLICATE_OBJECT'
  elif not t:reason='OBJECT_NOT_CURRENT_REVEALED'
  elif set(x)!={'object_id','target_day','issued_day','state_version','event','p_supplier_delayed','evidence_ids'}:reason='CANONICAL_FIELDS'
  elif x['event']!='SUPPLIER_DELAYED':reason='WRONG_EVENT'
  elif x['target_day']!=t['target_day'] or x['issued_day']!=board['day'] or x['issued_day']>=t['target_day']:reason='INVALID_TIME'
  elif x['state_version']!=board['state_version']:reason='STATE_VERSION'
  elif type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1:reason='INVALID_PROBABILITY'
  elif not isinstance(x['evidence_ids'],list) or not x['evidence_ids'] or any(e!=src.source_event_id for e in x['evidence_ids']):reason='INVALID_EVIDENCE_IDS'
  if reason:record(rt,u,'SupplierRiskRejection',f"{board['day']}:{i}",{'day':board['day'],'object_id':oid,'indices':[j for j,_ in entries],'reason':reason,'raw':[z for _,z in entries]},src);continue
  h=state.pool.risk(t);lam=state.cal['lambda'];f={**x,'p_H':h,'p_L':p,'lambda':lam,'p_E':h+lam*(p-h),'pool_version':len(state.pool.history),'calibration_version':state.cal['version'],'provider_receipt_id':receipt,'channel_id':src.channel_id if hasattr(src,'channel_id') else None}
  n=record(rt,u,'SupplierRiskForecast',f"{board['day']}:{oid}",f,src);f['forecast_id']=n.node_id
  state.cal['pending'].setdefault(oid,[]).append(f);valid[oid]=p;forecasts.append(f)
 return valid,forecasts

def operational(board,branches,risks):
 out=[]
 for b in branches:
  target=b['target_object_id'];held=board['slot']['object_id'] if board['slot'] else None
  if b['action']=='RESERVE':held=b['object_id']
  elif b['action']=='RELEASE':held=None
  condition=b['continuation'];known=None;error=None
  if condition=='HOLD_TO_TARGET':
   if held!=target:error='HOLD_WITHOUT_TARGET_BACKUP'
   else:known=1.
  elif condition=='NO_FURTHER_RESOURCE_ACTION':known=1. if held==target else 1-risks[target]
  else:error='CONDITION_UNDEFINED'
  out.append({'raw_branch_id':b['node_id'],'target_object_id':target,'target_day':b['target_day'],'action':b['action'],'object_id':b['object_id'],'state_version':board['state_version'],'continuation':condition,'event':'FINAL_ON_TIME','effective_supplier_risk':risks[target],'known_rule_probability':known,'raw_probability':b['p_final_on_time'],'semantic_error':error,'raw_rule_disagreement':known is not None and abs(known-b['p_final_on_time'])>1e-8,'rule':'continuous effective backup => 1; no backup and no further intervention => 1 - effective supplier risk','host_rule_computed':True})
 return out

def decision_view(board,src_id,state,model,raw,branches,handoff,override=None):
 values=build_values(board,state,model,override);v=facts(board,src_id);v.pop('output_contract');v.update(raw_first_prediction=raw,predictions=branches,prediction_handoff=handoff,risk_inputs=values['risk_inputs'],calibration=values['calibration'],common_value_table=values['table'],operational_forecasts=operational(board,branches,values['risks']),stage='RESOURCE_DECIDE',calculation_contract='Expected NEW reservation/cancellation plus missed-delivery loss over SAME public horizon; already-paid fees sunk. This tick uses p_effective consistently. You choose, calculator does not execute. Raw probabilities are preserved, not established facts. Second-stage risk estimates cannot train or change this tick risk.');return v,values

def settle_branches(rt,u,board,src):
 done={n.content['raw_branch_id'] for n in rt.nodes(u,'OperationalSettlement')};results={e['object_id']:e for e in board['results']}
 for n in rt.nodes(u,'OperationalForecast'):
  b=n.content;oid=b['target_object_id']
  if n.node_id in done or oid not in results:continue
  choice=next((c.content for c in rt.nodes(u,'ResourceChoice') if c.content['day']==b['day']),None);selected=choice and choice['execution'].get('ok') and choice['decision']['action']==b['action'] and choice['decision']['object_id']==b['object_id']
  changes=[a for a in board['actions'] if b['day']<a['day']<=b['target_day'] and a['action']!='WAIT']
  if b['continuation']=='HOLD_TO_TARGET':changes=[a for a in changes if a['action']=='RELEASE' and a['object_id']==oid]
  status='UNDEFINED' if b['known_rule_probability'] is None else 'NOT_EXECUTED' if not selected else 'INTERVENED' if changes else 'RESOLVED'
  e=results[oid];record(rt,u,'OperationalSettlement',n.node_id,{'raw_branch_id':n.node_id,'source_raw_branch_id':b['raw_branch_id'],'day':board['day'],'target_object_id':oid,'status':status,'observed_value':e['on_time'] if status=='RESOLVED' else None,'raw_probability':b['raw_probability'],'derived_probability':b['known_rule_probability'],'interventions':changes,'observation':src.source_event_id},src)

def resource_tick(rt,u):
 from skuld.app.runtime import ProviderFailure,ProviderFormatFailure,structured_text
 rows=rt.store._conn.execute("SELECT channel_id FROM runtime_channels WHERE user_id=? AND kind='resource_simulation'",(u,)).fetchall()
 if len(rows)!=1:raise ValueError('one isolated resource channel required')
 cid=rows[0][0];obs=rt.observe_channel(u,cid);board=next(o['value'] for o in obs if o['key']=='board');day=board['day'];src=rt.graph.get_source_event(u,rt._last_loop_sample[(u,cid)]['source_id']);state=load_state(rt,u,cid)
 if any(n.content['day']==day for n in rt.nodes(u,'ResourceTick')):return
 with rt.store.transaction():
  known={n.content['object_id'] for n in rt.nodes(u,'ResourceOutcome')}
  for e in board['results']:
   if e['object_id'] not in known:record(rt,u,'ResourceOutcome',e['object_id'],{**e,'source_id':cid,'observation_source_event_id':src.source_event_id,'sampled_at':rt.clock()},src)
  for update in state.settle(board['results'],day):record(rt,u,'CalibrationSettlement',update['object_id'],update,src)
  for t in board['open_responsibilities']:state.pool.register(t,day)
  settle_branches(rt,u,board,src);save_state(rt,u,cid,state);record(rt,u,'ResourceTick',str(day),{'day':day,'state_version':board['state_version']},src)
 if not board['open_responsibilities'] or rt.config.get('resource_observe_only'):return
 calls=0
 def call(stage,model,view):
  nonlocal calls
  calls+=1
  if calls>2:raise RuntimeError('two decision requests max')
  return rt.call(u,stage,json.dumps({**view,'stage':stage},ensure_ascii=False,separators=(',',':')),model,'resource19:'+str(day)+':'+str(calls))
 raw=None;model={};branches=[];handoff={'status':'NO_BRANCHES','rejections':[],'legal_evidence_ids':[src.source_event_id]};forecasts=[]
 try:
  response,receipt=call('RESOURCE_PREDICT',Forecast19,facts(board,src.source_event_id));row=rt.store._conn.execute('SELECT raw_json FROM runtime_provider_receipts WHERE receipt_id=?',(receipt.node_id,)).fetchone();raw=structured_text(json.loads(row[0]));record(rt,u,'RawPrediction',str(day),{'day':day,'raw':raw,'provider_receipt_id':receipt.node_id},src)
  with rt.store.transaction():
   model,forecasts=admit_supplier(rt,u,board,src,response.supplier_forecasts,receipt.node_id,state);save_state(rt,u,cid,state)
  for i,item in enumerate(response.branches):
   n=record(rt,u,'RawBranch',f'{day}:{i}',{'day':day,'index':i,'raw':item,'provider_receipt_id':receipt.node_id},src)
   try:b=RawBranchSchema.model_validate(item)
   except ValidationError as e:handoff['rejections'].append({'index':i,'error':'BRANCH_FORMAT','detail':str(e)});continue
   t=next((t for t in board['open_responsibilities'] if t['object_id']==b.target_object_id),None);errors=[]
   if not t:errors.append('TARGET_SCOPE')
   elif t['target_day']!=b.target_day:errors.append('TARGET_TIME')
   if b.state_version!=board['state_version']:errors.append('STATE_VERSION')
   if {'action':b.action,'object_id':b.object_id} not in board['legal_actions']:errors.append('ACTION_SCOPE')
   if not b.evidence_ids or any(e!=src.source_event_id for e in b.evidence_ids):errors.append('EVIDENCE_SCOPE')
   if errors:handoff['rejections'].append({'index':i,'errors':errors});continue
   branches.append({'node_id':n.node_id,**b.model_dump()})
  handoff['status']='ACCEPTED' if branches else 'ALL_REJECTED' if response.branches else 'NO_BRANCHES'
 except (ProviderFailure,ProviderFormatFailure) as e:
  handoff['status']='PREDICT_FAILURE';handoff['error']=str(e);record(rt,u,'ResourceFailure',f'{day}:predict',{'day':day,'stage':'predict','error':str(e)},src)
 view,values=decision_view(board,src.source_event_id,state,model,raw,branches,handoff)
 for oid,x in values['risk_inputs'].items():
  if oid in {t['object_id'] for t in board['open_responsibilities']}:record(rt,u,'SystemRiskForecast',f'{day}:{oid}',{'day':day,'object_id':oid,'target_day':next(t['target_day'] for t in board['open_responsibilities'] if t['object_id']==oid),**x},src)
 for i,x in enumerate(view['operational_forecasts']):record(rt,u,'OperationalForecast',f'{day}:{i}',{'day':day,**x,'calibration_version':state.cal['version']},src)
 shadow,_=decision_view(board,src.source_event_id,state,model,raw,branches,handoff,0)
 record(rt,u,'CalibrationDecisionView',str(day),{'day':day,'board':board,'model_risks':model,'actual':view,'shadow':shadow,'state_version':state.version},src)
 hook=getattr(rt,'resource_decision_snapshot',None)
 if hook:hook(u,cid,day,board,view,shadow)
 try:d,receipt=call('RESOURCE_DECIDE',Decision,view)
 except (ProviderFailure,ProviderFormatFailure) as e:record(rt,u,'ResourceFailure',f'{day}:decide',{'day':day,'stage':'decide','error':str(e)},src);return
 error=None
 if d.state_version!=board['state_version'] or {'action':d.action,'object_id':d.object_id} not in board['legal_actions']:error='INVALID_ACTION_OR_STATE'
 elif any(x not in [b['node_id'] for b in branches] for x in d.used_prediction_ids):error='INVALID_PREDICTION_REFERENCE'
 if error:record(rt,u,'ResourceFailure',f'{day}:decide',{'day':day,'stage':'decide','error':error,'raw_decision':d.model_dump()},src);return
 admit_risks(rt,u,d.risk_estimates,board,src)
 result=execute(rt,u,cid,d,src);trace=execution_contract(rt,u,board,d,values['table'],result)
 record(rt,u,'ResourceChoice',str(day),{'day':day,'decision':d.model_dump(),'provider_receipt_id':receipt.node_id,'observation_source_event_id':src.source_event_id,'execution':result,'execution_contract':trace,'calibration_version':state.cal['version'],'effective_risks':values['risk_inputs']},src)
 if result.get('status')=='IN_DOUBT':raise RuntimeError('simulation action outcome unknown')
