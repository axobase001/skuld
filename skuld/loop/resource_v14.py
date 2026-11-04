"""CP14 explicit probability events and common auxiliary admission; two-call review control."""
import json
from typing import Any,Literal
from pydantic import Field
from .resource_loop import Strict,compact,record,execute,RULES
class Decision(Strict):
 action:Literal['RESERVE','RELEASE','WAIT'];object_id:str|None;state_version:int;risk_estimates:list[Any];used_prediction_ids:list[str];reason:str
class Branch(Strict):
 action:Literal['RESERVE','RELEASE','WAIT'];object_id:str|None;state_version:int;target_object_id:str;target_day:int;p_final_on_time:float=Field(ge=0,le=1);p_supplier_delayed:float=Field(ge=0,le=1);assumptions:list[str];evidence_ids:list[str]=Field(description='References to the current observation snapshot only: use legal_evidence_ids supplied in this request. Business object IDs, field paths and historical Prediction IDs are not allowed. Nonempty; each branch is validated independently.');consequence:str
class Forecast(Strict):branches:list[Branch]=Field(max_length=3);reason:str
EVENT_RULES='p_supplier_delayed (compatible p_delayed) is probability original supplier is delayed at that object target, independent of backup. p_final_on_time is probability FINAL on-time delivery conditional on the specified action and continuing-hold assumptions. Effective backup can make FINAL on-time even if supplier delayed. Do not exchange these event labels. risk_estimates items: {object_id,p_supplier_delayed:0..1 or null,basis:OBSERVED or UNKNOWN}. Public future commitments may receive UNKNOWN-condition priors, never invented observed conditions. Unknown/unpublished object auxiliary items are rejected independently. No optimal-action calculation is supplied.'
def view_of(board):
 v=compact(board);v['rules']=RULES.replace('Probability estimates concern ORIGINAL supplier delayed, not backup-assisted final delivery.','')+' '+EVENT_RULES
 v['object_scope']={'current':[t['object_id'] for t in board['open_responsibilities']],'public_future':[t['object_id'] for t in board['future_commitments']]}
 v['cost_facts']={'held_slot':board['slot'],'paid_nonrefundable_reservation_total':board['costs']['reservation'],'paid_cancellation_total':board['costs']['cancellation'],'objects':[]}
 for t in board['open_responsibilities']+board['future_commitments']:
  paid=sum(a['fee'] for a in board['actions'] if a['action']=='RESERVE' and a['object_id']==t['object_id']);v['cost_facts']['objects'].append({'object_id':t['object_id'],'already_paid_nonrefundable':paid,'new_RESERVE_fee':t['reserve_fee'],'new_RELEASE_fee':t['cancel_fee'],'held':bool(board['slot'] and board['slot']['object_id']==t['object_id']),'booking_deadline':t['booking_deadline'],'target_day':t['target_day'],'miss_loss':t['miss_loss']})
 return v
def admit_risks(rt,u,items,board,src):
 allowed={t['object_id']:t for t in board['open_responsibilities']+board['future_commitments']};future={t['object_id'] for t in board['future_commitments']};accepted=[]
 for i,x in enumerate(items):
  reason=None
  if not isinstance(x,dict):reason='AUX_NOT_OBJECT'
  elif x.get('object_id') not in allowed:reason='AUX_OUTSIDE_CURRENT_AND_PUBLIC_FUTURE'
  else:
   obj=x['object_id'];p=x.get('p_supplier_delayed',x.get('p_delayed'));basis=x.get('basis','OBSERVED')
   if p is not None and (type(p) not in (int,float) or not 0<=p<=1):reason='AUX_INVALID_PROBABILITY'
   elif obj in future and (basis!='UNKNOWN' or any(k in x for k in ['condition','observed_condition','supplier_result'])):reason='FUTURE_CONDITION_NOT_OBSERVED'
   elif basis not in ('OBSERVED','UNKNOWN'):reason='AUX_INVALID_BASIS'
  if reason:record(rt,u,'ResourceRiskRejection',str(board['day'])+':'+str(i),{'day':board['day'],'index':i,'reason':reason,'item':x},src);continue
  a={'object_id':obj,'p_supplier_delayed':p,'basis':basis,'target_day':allowed[obj]['target_day']};accepted.append(a)
  record(rt,u,'ResourceRiskEstimate',str(board['day'])+':'+str(i),{**a,'day':board['day'],'event':'SUPPLIER_DELAYED','observation_source_event_id':src.source_event_id},src)
 return accepted

def resource_tick(rt,u):
 from skuld.app.runtime import ProviderFailure,ProviderFormatFailure,structured_text
 rows=rt.store._conn.execute("SELECT channel_id FROM runtime_channels WHERE user_id=? AND kind='resource_simulation'",(u,)).fetchall()
 if len(rows)!=1:raise ValueError('one isolated resource channel required')
 cid=rows[0][0];obs=rt.observe_channel(u,cid);board=next(o['value'] for o in obs if o['key']=='board');day=board['day'];src=rt.graph.get_source_event(u,rt._last_loop_sample[(u,cid)]['source_id'])
 seen={n.content['object_id'] for n in rt.nodes(u,'ResourceOutcome')}
 for e in board['results']:
  if e['object_id'] not in seen:record(rt,u,'ResourceOutcome',e['object_id'],{**e,'source_id':cid,'observation_source_event_id':src.source_event_id,'sampled_at':rt.clock(),'status':'COMPLETED' if e['on_time'] else 'FAILED'},src)
 outcomes={e['object_id']:e for e in board['results']};settled={n.content['prediction_id'] for n in rt.nodes(u,'ResourceForecastSettlement')}
 for p in rt.nodes(u,'Prediction'):
  b=p.content
  if b.get('kind')!='resource_conditional_v14' or p.node_id in settled or b['target_object_id'] not in outcomes:continue
  choice=next((n.content for n in rt.nodes(u,'ResourceChoice') if n.content['day']==b['day']),None);selected=bool(choice and choice['execution'].get('ok') and choice['decision']['action']==b['action'] and choice['decision']['object_id']==b['object_id']);interventions=[a for a in board['actions'] if b['day']<a['day']<=b['target_day'] and a['action']!='WAIT'];status='NOT_EXECUTED' if not selected else 'INTERVENED' if interventions else 'RESOLVED';e=outcomes[b['target_object_id']]
  record(rt,u,'ResourceForecastSettlement',p.node_id,{'prediction_id':p.node_id,'status':status,'event':'FINAL_ON_TIME','target_object_id':b['target_object_id'],'observed_value':e['on_time'] if status=='RESOLVED' else None,'outcome':e if status=='RESOLVED' else None,'interventions':interventions,'squared_error':(b['p_final_on_time']-int(e['on_time']))**2 if status=='RESOLVED' else None,'observation_source_event_id':src.source_event_id,'host_rule_computed':True},src)
 riskdone={n.content['estimate_id'] for n in rt.nodes(u,'SupplierRiskSettlement')}
 for n in rt.nodes(u,'ResourceRiskEstimate'):
  a=n.content;e=outcomes.get(a['object_id'])
  if n.node_id in riskdone or e is None or a['day']>=a['target_day'] or a['p_supplier_delayed'] is None:continue
  record(rt,u,'SupplierRiskSettlement',n.node_id,{'estimate_id':n.node_id,'event':'SUPPLIER_DELAYED','object_id':a['object_id'],'target_day':a['target_day'],'observed_value':e['supplier_result']=='delayed','squared_error':(a['p_supplier_delayed']-int(e['supplier_result']=='delayed'))**2,'observation_source_event_id':src.source_event_id,'host_rule_computed':True},src)
 pool=getattr(rt,'sec_evo_pool',None)
 if pool is not None:
  pool.observe(board['results'],day)
  for task in board['open_responsibilities']:
   if task['release_day']==day:pool.register(task,day)
 if any(n.content['day']==day for n in rt.nodes(u,'ResourceTick')):return
 record(rt,u,'ResourceTick',str(day),{'day':day,'state_version':board['state_version'],'observation_source_event_id':src.source_event_id},src)
 if not board['open_responsibilities'] or rt.config.get('resource_observe_only'):return
 view=view_of(board);view['source_observation']=src.source_event_id;view['recent_own_decisions']=[n.content['decision'] for n in rt.nodes(u,'ResourceChoice')][-4:];view['recent_conditional_settlements']=[n.content for n in rt.nodes(u,'ResourceForecastSettlement')][-4:]
 if pool is not None:
  view['learned_risk_state']=pool.public_state()
  view['learned_risk_inputs']={t['object_id']:pool.risk(t) for t in board['open_responsibilities']+board['future_commitments']}
  view['rules']=view['rules'].replace('No optimal-action calculation is supplied.','A common public-horizon value table is supplied in round two. You choose the action; calculator does not execute for you.')
  view['learning_contract']='Compiled conditional risk hypotheses learn only observed ORIGINAL supplier outcomes. Known resource rules stay fixed. Risk inputs are versioned host calculations, not your own probability reports. You may disagree, but do not silently replace their provenance.'
 arm=rt.config['resource_arm'];predictions=[];calls=0
 if arm=='B-review':view['recent_own_drafts']=[n.content['raw_draft'] for n in rt.nodes(u,'ResourceDraft')][-4:]
 def call(stage,model):
  nonlocal calls
  calls+=1
  if calls>2:raise RuntimeError('resource tick request bound')
  from .resource_context import historical_view
  bounded=historical_view(rt,u,view)
  return rt.call(u,stage,json.dumps({**bounded,'stage':stage,'predictions':predictions},ensure_ascii=False,separators=(',',':')),model,'resource14:'+str(day)+':'+str(calls))
 if arm=='B-review':
  view['review_instruction']='First call propose a decision with your analysis; do not execute. Second call autonomously review the raw draft and all facts, including fees, sunk costs, guarantee, deadline, slot and public future commitments, then give final action. Freely compare future consequences; you may reject your own draft.'
  try:
   draft,receipt=call('RESOURCE_DRAFT',Decision);raw=rt.store._conn.execute('SELECT raw_json FROM runtime_provider_receipts WHERE receipt_id=?',(receipt.node_id,)).fetchone()[0];text=structured_text(json.loads(raw));record(rt,u,'ResourceDraft',str(day),{'day':day,'raw_draft':text,'provider_receipt_id':receipt.node_id,'executed':False},src);view['raw_first_draft']=text
  except (ProviderFailure,ProviderFormatFailure) as exc:view['first_round_failure']=str(exc);record(rt,u,'ResourceFailure',str(day)+':draft',{'day':day,'stage':'draft','error':str(exc)},src)
 elif arm=='C':
  view['legal_evidence_ids']=[src.source_event_id]
  view['evidence_reference_example']={'evidence_ids':[src.source_event_id]}
  view['evidence_reference_contract']='evidence_ids refers to the current observation snapshot only. Use a nonempty list drawn from legal_evidence_ids. Business object IDs, input field paths and historical Prediction IDs are not legal references. The example explains reference format only, not probabilities or actions. Valid provenance does not prove a prediction correct.'
  view['rules']+=' '+view['evidence_reference_contract']
  handoff={'status':'NO_BRANCHES','parsed':False,'raw_branch_count':None,'accepted_prediction_ids':[],'rejections':[],'legal_evidence_ids':[src.source_event_id],'reference_contract':view['evidence_reference_contract']}
  try:
   response,receipt=call('RESOURCE_PREDICT',Forecast);handoff.update(parsed=True,raw_branch_count=len(response.branches),predict_receipt_id=receipt.node_id)
   for i,b in enumerate(response.branches):
    errors=[];t=next((t for t in board['open_responsibilities'] if t['object_id']==b.target_object_id),None)
    if not b.evidence_ids:errors.append('MISSING_EVIDENCE_IDS')
    elif any(e!=src.source_event_id for e in b.evidence_ids):errors.append('EVIDENCE_OUTSIDE_LEGAL_IDS')
    if not t:errors.append('TARGET_OBJECT_OUT_OF_SCOPE')
    if {'action':b.action,'object_id':b.object_id} not in board['legal_actions']:errors.append('ACTION_OBJECT_OUT_OF_SCOPE')
    if b.state_version!=board['state_version']:errors.append('STATE_VERSION_MISMATCH')
    if t and t['target_day']!=b.target_day:errors.append('TARGET_TIME_MISMATCH')
    if errors:
     handoff['rejections'].append({'index':i,'errors':errors})
     record(rt,u,'ResourceRejection',str(day)+':branch:'+str(i),{'day':day,'index':i,'reason':errors[0],'errors':errors,'candidate':b.model_dump()},src);continue
    n=record(rt,u,'Prediction',str(day)+':branch:'+str(i),{'kind':'resource_conditional_v14','day':day,**b.model_dump(),'provider_receipt_id':receipt.node_id,'event':'FINAL_ON_TIME','intervention':'explicit_action_condition'},src);predictions.append({'node_id':n.node_id,**b.model_dump()})
   handoff['accepted_prediction_ids']=[p['node_id'] for p in predictions]
   handoff['status']='ACCEPTED' if predictions else 'ALL_REJECTED' if response.branches else 'NO_BRANCHES'
  except (ProviderFailure,ProviderFormatFailure) as exc:
   handoff['status']='FORMAT_FAILURE' if isinstance(exc,ProviderFormatFailure) else 'PROVIDER_FAILURE';handoff['error']=str(exc);view['first_round_failure']=str(exc);record(rt,u,'ResourceFailure',str(day)+':predict',{'day':day,'stage':'predict','error':str(exc)},src)
  view['prediction_handoff']=handoff
  record(rt,u,'ResourcePredictionHandoff',str(day),{'day':day,**handoff,'admitted_branches':len(predictions)},src)
 if pool is not None:
  from .resource_value import value_table
  values=value_table(board,pool)
  view['common_value_table']=[{k:v for k,v in row.items() if k not in ('risk_provenance','risk_inputs','sunk_paid_costs_excluded')} for row in values]
  view['calculation_contract']='Each row compares the SAME public task scope through the SAME final target. Paid fees are sunk and excluded. Values include new reservation/cancel fees and expected missed delivery losses, assuming an optimal legal continuation over currently public tasks only. Unknown future conditions use the shared learned unconditional prior. This is expected cost, not observed reward.'
  differences=[]
  for branch in predictions:
   guaranteed=(branch['action']=='RESERVE' and branch['object_id']==branch['target_object_id']) or (branch['action']=='WAIT' and board['slot'] and board['slot']['object_id']==branch['target_object_id'])
   if guaranteed and branch['p_final_on_time']!=1:
    differences.append({'prediction_id':branch['node_id'],'field':'p_final_on_time','model_value':branch['p_final_on_time'],'known_rule_value':1,'condition':'continuous effective hold until target; later RELEASE is intervention'})
  view['known_rule_discrepancies']=differences
  record(rt,u,'ExperienceValueTable',str(day),{'day':day,'risk_state':pool.public_state(),'risk_inputs':view['learned_risk_inputs'],'table':view['common_value_table'],'known_rule_discrepancies':differences},src)
 while calls<2:
  try:d,receipt=call('RESOURCE_DECIDE',Decision)
  except (ProviderFailure,ProviderFormatFailure) as exc:record(rt,u,'ResourceFailure',str(day)+':decide:'+str(calls),{'day':day,'stage':'decide','error':str(exc)},src);view['previous_attempt_error']=str(exc);continue
  accepted=admit_risks(rt,u,d.risk_estimates,board,src);error=None
  if d.state_version!=board['state_version'] or {'action':d.action,'object_id':d.object_id} not in board['legal_actions']:error='INVALID_ACTION_OR_STATE'
  elif any(x not in [p['node_id'] for p in predictions] for x in d.used_prediction_ids):error='INVALID_PREDICTION_REFERENCE'
  if error:record(rt,u,'ResourceFailure',str(day)+':decide:'+str(calls),{'day':day,'stage':'decide','error':error},src);view['previous_attempt_error']=error;continue
  if pool is not None:
   mismatch=[{'object_id':x['object_id'],'model_p':x['p_supplier_delayed'],'calculator_p':view['learned_risk_inputs'][x['object_id']]} for x in accepted if x['p_supplier_delayed'] is not None and abs(x['p_supplier_delayed']-view['learned_risk_inputs'][x['object_id']])>1e-9]
   record(rt,u,'ExperienceRiskDisagreement',str(day),{'day':day,'items':mismatch,'calculator_inputs_unchanged':True},src)
  d.risk_estimates=accepted;result=execute(rt,u,cid,d,src)
  from .resource_context import execution_contract
  trace=execution_contract(rt,u,board,d,values if pool is not None else [],result)
  record(rt,u,'ResourceChoice',str(day),{'day':day,'decision':d.model_dump(),'provider_receipt_id':receipt.node_id,'observation_source_event_id':src.source_event_id,'execution':result,'execution_contract':trace},src)
  if result.get('status')=='IN_DOUBT':raise RuntimeError('simulation action outcome unknown')
  return
