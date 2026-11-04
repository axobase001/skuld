"""CP20: one periodic MODEL_REVISE, shared deterministic Runtime resource execution."""
import copy,json
from typing import Any,Literal
from pydantic import Field,StrictInt
from .resource_loop import Strict,record,execute
from .resource_v14 import Decision
from .resource_value import value_table
from .resource_context import execution_contract
from skuld.learning.resource_rules import load_state,save_state,feedback_view
from skuld.learning.sec_evo import loss
class Proposal(Strict):
 operation:Literal['KEEP','REPLACE']
 parent_model_version:StrictInt
 tree:Any
 evidence_settlement_ids:list[str]=Field(max_length=4)
 reason:str
class Provider:
 def __init__(self,s,tree,kind):self.s=s;self.tree=tree;self.kind=kind
 def risk(self,t):return self.s.probability(t,self.tree,self.kind)
 def public_state(self):return {'kind':self.kind,'statistics_version':self.s.statistics_version}
def table_for(s,b,version=None):
 v=s.versions[-1] if version is None else version;p=Provider(s,v['tree'],v['kind']);risks={t['object_id']:p.risk(t) for t in b['open_responsibilities']+b['future_commitments']};table=value_table(b,p)
 return {'model_version':v['model_version'],'kind':v['kind'],'tree':v['tree'],'statistics_version':s.statistics_version,'risks':risks,'table':[{k:x for k,x in q.items() if k not in ('risk_inputs','risk_provenance','sunk_paid_costs_excluded')} for q in table]}
def action(q):return {'action':q['action'],'object_id':q['object_id']}
def minimum(table):
 m=min(q['expected_incremental_loss'] for q in table);return [q for q in table if abs(q['expected_incremental_loss']-m)<=1e-8]
def select(table):
 qs=minimum(table);return next((q for q in qs if q['action']=='WAIT'),min(qs,key=lambda q:(q['action'],str(q['object_id']))))
def shadow_bundle(s,b,node):
 a=table_for(s,b);first=s.versions[1] if s.model_version else s.versions[0];bv=table_for(s,b,first);cv=table_for(s,b,s.versions[-2]) if s.model_version else None
 for v in [a,bv,cv]:
  if v is not None:v['minimum']=[action(q) for q in minimum(v['table'])];v['selected']=action(select(v['table']))
 def compare(x):
  if x is None:return None
  return {'risk_changed':any(abs(a['risks'][k]-x['risks'][k])>1e-12 for k in a['risks']),'minimum_changed':a['minimum']!=x['minimum'],'selector_changed':a['selected']!=x['selected'],'actual_new_exclusive_minimum':a['selected'] not in x['minimum']}
 return {'day':b['day'],'state_version':b['state_version'],'A':a,'B':bv,'C':cv,'AB':compare(bv),'AC':compare(cv),'empty_slot':b['slot'] is None,'eligible_reserve':[x for x in b['legal_actions'] if x['action']=='RESERVE'],'deadlines':[[t['object_id'],t['booking_deadline']] for t in b['open_responsibilities']],'tie':len(a['minimum'])>1,'actual_matches_selector':action(node['decision'])==a['selected'],'mode':'READ_ONLY_SAME_OBSERVED_PREFIX_NO_EXECUTION'}
def resource_tick(rt,u):
 from skuld.app.runtime import ProviderFailure,ProviderFormatFailure
 rows=rt.store._conn.execute("SELECT channel_id FROM runtime_channels WHERE user_id=? AND kind='resource_simulation'",(u,)).fetchall()
 if len(rows)!=1:raise ValueError('ONE_ISOLATED_RESOURCE_CHANNEL_REQUIRED')
 cid=rows[0][0];obs=rt.observe_channel(u,cid);b=next(o['value'] for o in obs if o['key']=='board');day=b['day'];src=rt.graph.get_source_event(u,rt._last_loop_sample[(u,cid)]['source_id']);s=load_state(rt,u,cid)
 if any(n.content['day']==day for n in rt.nodes(u,'RuleTick')):return
 with rt.store.transaction():
  for e in s.observe(b['results'],day):
   oid=e['object_id'];f=s.predictions.get(oid)
   if f:
    if f['issued_day']>=e['target_day']:raise ValueError('NOT_PRE_TARGET_FORECAST')
    z={**e,'prediction_id':f['prediction_id'],'issued_model_version':f['model_version'],'p_issued':f['p_issued'],'brier':(f['p_issued']-e['y'])**2,'log_loss':loss(f['p_issued'],e['y']),'host_rule_computed':True};n=record(rt,u,'RuleForecastSettlement',oid,z,src);z['settlement_id']=n.node_id;s.settlements[oid]=z
   record(rt,u,'ResourceOutcome',oid,next(x for x in b['results'] if x['object_id']==oid)|{'observed_source_event_id':src.source_event_id},src)
  for t in b['open_responsibilities']:s.host.register(t,day)
  save_state(rt,u,cid,s);record(rt,u,'RuleTick',str(day),{'day':day,'statistics_version':s.statistics_version,'state_version':b['state_version']},src)
 n=sum(e['cohort']=='main' for e in s.observed.values());key=str(n)
 if s.role=='Revision' and n in rt.config['resource_revision_nodes'] and key not in s.opportunities:
  entry={'day':day,'matured_main':n,'status':'prepared','history':['prepared'],'parent_model_version':s.model_version};s.opportunities[key]=entry
  with rt.store.transaction():save_state(rt,u,cid,s)
  view=feedback_view(s,day);record(rt,u,'RuleRevisionInput',key,view,src)
  before={row[0] for row in rt.store._conn.execute('SELECT receipt_id FROM runtime_provider_receipts WHERE user_id=?',(u,))}
  try:
   response,receipt=rt.call(u,'MODEL_REVISE',json.dumps(view,ensure_ascii=False,separators=(',',':')),Proposal,'resource20:'+key)
   entry['history'].extend(['sent','returned']);entry.update(status='returned',provider_receipt_id=receipt.node_id,proposal=response.model_dump())
   try:entry['status']=s.apply(response.model_dump(),receipt.node_id,day,u);entry['history'].append('applied' if entry['status'] in ('FIRST_ADOPTION','REPLACEMENT','RETURN_TO_OLD_STRUCTURE') else entry['status'])
   except ValueError as exc:entry.update(status='REJECTED',error=str(exc));entry['history'].append('failed')
  except (ProviderFailure,ProviderFormatFailure) as exc:
   entry.update(status='FAILED',error=str(exc));entry['history'].append('failed')
   new=[row for row in rt.store._conn.execute('SELECT receipt_id,attempt_json FROM runtime_provider_receipts WHERE user_id=?',(u,)) if row[0] not in before]
   if new:entry['provider_receipt_id']=new[-1][0];entry['receipt_status']=json.loads(new[-1][1])['receipt']['status']
  persisted=load_state(rt,u,cid).opportunities[key]
  if 'ledger_index' in persisted:entry['ledger_index']=persisted['ledger_index'];entry['actual_request_sent']=True
  else:entry['actual_request_sent']=False
  entry['resulting_model_version']=s.model_version
  with rt.store.transaction():save_state(rt,u,cid,s);record(rt,u,'RuleRevisionResult',key,entry,src)
 with rt.store.transaction():
  if s.consume(day):record(rt,u,'RuleSharedInitialization',s.first_init['event_id'],s.first_init|{'own_statistics_version':s.statistics_version,'origin':'REVISION_FIRST_TREE_NOT_OWN_MODEL_CALL'},src)
  for t in b['open_responsibilities']:
   oid=t['object_id']
   if oid in s.predictions:continue
   f={'object_id':oid,'features':__import__('skuld.learning.sec_evo',fromlist=['features']).features(t),'target_day':t['target_day'],'issued_day':day,'cohort':t['cohort'],'p_issued':s.probability(t),'predictor_kind':s.predictor_kind,'model_version':s.model_version,'statistics_version':s.statistics_version,'origin_model_receipt':s.versions[-1]['origin_receipt']};node=record(rt,u,'RuleForecast',oid,f,src);f['prediction_id']=node.node_id;s.predictions[oid]=f
  save_state(rt,u,cid,s)
 if not b['open_responsibilities']:return
 values=table_for(s,b);q=select(values['table']);d=Decision(**action(q),state_version=b['state_version'],risk_estimates=[],used_prediction_ids=[],reason='DETERMINISTIC_VALUE_SELECTOR: minimum Q, WAIT preferred within 1e-8, stable action/object tie break')
 result=execute(rt,u,cid,d,src);trace=execution_contract(rt,u,b,d,values['table'],result)
 content={'day':day,'board':b,'decision_source':'DETERMINISTIC_VALUE_SELECTOR','risk_model_version':s.model_version,'statistics_version':s.statistics_version,'origin_model_receipt':s.versions[-1]['origin_receipt'],'decision':d.model_dump(),'values':values,'minimum':[action(x) for x in minimum(values['table'])],'selected_Q':q['expected_incremental_loss'],'execution':result,'execution_contract':trace}
 record(rt,u,'RuleDecision',str(day),content,src)
 if s.role=='Revision':record(rt,u,'RuleCounterfactual',str(day),shadow_bundle(s,b,content),src)
 if not result.get('ok'):raise RuntimeError('DETERMINISTIC_RUNTIME_EXECUTION_FAILED:'+str(result))
