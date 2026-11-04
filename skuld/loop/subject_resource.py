"""Domain integration inside the normal subject loop. No observation or outer-tick bypass."""
import json,copy,math
from typing import Any,Literal
from pydantic import Field
from .resource_loop import Strict,record,execute
from .resource_v14 import Decision
from .resource_v20 import select,minimum,action
from .resource_value import value_table
from .resource_context import execution_contract
from skuld.kernel.primitives import ProviderRequest
from skuld.learning.subject_experience import load,save,sec_wire_view
from skuld.learning.sec_evo import features,loss
from skuld.learning.sec_evo_atomic import parent_view,paths,signature
from skuld.kernel.schemas import AuthorityClass,ModalState
class Issue(Strict):
 experience_ids:list[str]=Field(min_length=1,max_length=4)
 scope:dict[str,int]
 locus:Literal['CONDITION_MODEL','INFORMATION_GAP','DECISION_TRADEOFF','EXECUTION','UNCERTAIN']
 priority:float=Field(ge=0,le=1)
 reason:str
class Attribution(Strict):issues:list[Issue]=Field(max_length=2)
class Variants(Strict):items:list[Any]=Field(max_length=4)
def publish(rt,u,cid,s,src):
 s.version+=1
 from .subject_cp22 import executable_pool
 published=executable_pool(rt,s);ws=published.weights();ids=[]
 for h in published.hypotheses:
  n=record(rt,u,'Hypothesis','supplier-model:'+cid+':'+h['id'],{'kind':'SUPPLIER_EXPERIENCE_MODEL','candidate_id':h['id'],'publication_version':s.version,'structure_version':h['version'],'tree':h['spec']['tree'],'counts':copy.deepcopy(h['counts']),'weight':ws.get(h['id'],0),'status':h['status'],'born_day':h['born_day'],'parents':h['spec']['parents'],'origin_receipt':h['receipt'],'prospective_n':len(h['scores']),'scope':{'source_id':cid,'target':'SUPPLIER_DELAYED','features':['f1','f2','f3'],'horizon_days':3},'parameter_truth':'only observed original supplier labels'},src);ids.append(n.node_id)
 s.model_ids=ids
 if rt.config.get("cp22"):
  from .subject_cp22 import model_links
  model_links(rt,u,cid,s,src,published)

def prepare(rt,u,read_results,number):
 cid=rt.config['subject_resource_channel'];src=read_results.get(cid)
 if src is None:raise ValueError('SUBJECT_RESOURCE_REQUIRES_THIS_ROUND_AUTHORIZED_OBSERVATION')
 b=next(f['value'] for f in src.content['data']['facts'] if f['key']=='board');day=b['day'];s=load(rt,u,cid)
 if any(n.content['day']==day for n in rt.nodes(u,'SubjectResourceDecision')):return None
 with rt.store.transaction():
  old_models=list(s.model_ids)
  for e in b['results']:
   oid=e['object_id']
   if oid in s.processed:continue
   if e['target_day']>day:raise ValueError('FUTURE_RESULT')
   f=s.predictions.get(oid)
   if f is None:raise ValueError('MISSING_ISSUED_FORECAST')
   y=int(e['supplier_result']=='delayed');decisions=[n for n in rt.nodes(u,'SubjectResourceDecision') if oid in n.content['responsibility_objects']];acts=[a for a in b['actions'] if a['object_id']==oid];fees={'reservation':sum(a['fee'] for a in acts if a['action']=='RESERVE'),'cancellation':sum(a['fee'] for a in acts if a['action']=='RELEASE'),'miss':e['miss_cost']}
   z=record(rt,u,'SupplierForecastSettlement',oid,{'prediction_id':f['prediction_id'],'supplier_y':y,'p_issued':f['p_issued'],'brier':(f['p_issued']-y)**2,'log_loss':loss(f['p_issued'],y),'observed_day':day,'observation_id':src.source_event_id},src)
   if rt.config.get('action_risk_calibration'):
    from .action_risk import settle
    settle(rt,u,cid,e,day,src)
   gid=s.goals[oid];status=rt._responsibility_status(u,gid,src,status='COMPLETED',business_success=e['on_time'],supplier_result=e['supplier_result'],closed_at=rt.clock(),outcome='ON_TIME' if e['on_time'] else 'MISSED_DEADLINE',objective_complete=bool(e['on_time']))
   rt.product._work_update(u,gid,src,status='SETTLED',initial_pending=False)
   from .subject_cp22 import semantic_consequences
   experience=record(rt,u,'ResponsibilityExperience',oid,{'object_id':oid,'goal_id':gid,'features':features(e),'prediction_id':f['prediction_id'],'prediction_version':f['model_version'],'p_issued':f['p_issued'],'prediction_observation_id':f['observation_id'],'first_activation_ref':(f.get('activation_snapshot') or {}).get('recall_id'),'first_prediction_contributor_ids':f['hypothesis_node_ids'],'semantic_query_ids':[q.node_id for q in rt.nodes(u,'SemanticQueryResult') if q.content['frame']['object_id']==oid],'semantic_query_consequences':semantic_consequences(rt,u,oid,e),'settlement_id':z.node_id,'observation_id':src.source_event_id,'target_day':e['target_day'],'observed_day':day,'supplier_result':e['supplier_result'],'supplier_y':y,'brier':(f['p_issued']-y)**2,'final_on_time':e['on_time'],'backup_effective':e['backup_effective'],'fees':fees,'responsibility_loss':sum(fees.values()),'responsibility_state_id':status.node_id,'decision_ids':[n.node_id for n in decisions],'execution':[{'day':a['day'],'action':a['action'],'fee':a['fee']} for a in acts],'interpretation':'Supplier truth, business result and actual execution are distinct; no causal optimum label.'},src)
   s.experiences[oid]=experience.node_id;s.processed.append(oid)
  before_events=len(s.pool.events)
  if rt.config.get('cp22'):
   from .subject_cp22 import observe_aux
   observe_aux(rt,s,b,day)
  s.pool.observe(b['results'],day)
  for e in s.pool.events[before_events:]:record(rt,u,'CandidateSelection',str(day)+':'+str(len(rt.nodes(u,'CandidateSelection'))),e,src)
  if not rt.config.get("cp22") and not s.shadow_done and s.pending_shadow is None and any(h['born_day']>=0 and len(h['scores'])>=4 for h in s.pool.hypotheses):s.pending_shadow={'day':day,'previous_model_ids':old_models,'trigger':'first child with four subsequent scored objects'}
  for t in b['open_responsibilities']:
   oid=t['object_id']
   if oid in s.goals:continue
   from datetime import timedelta
   from skuld.kernel.primitives import instant
   text='承担公开对象 '+oid+' 的按时交付与共享备援取舍；如失败如实结案，供应商事实单独结算。'
   n=rt.node(u,'Task','resource-responsibility:'+oid,{'text':text,'object_id':oid,'status':'active','due_at':(instant(rt.clock())+timedelta(days=t['target_day']-day)).isoformat()},src,modal=ModalState.INTENDED,authority=AuthorityClass.USER_FIRST_PERSON);s.goals[oid]=n.node_id
   rt.set_responsibility_contract(u,n.node_id,{'kind':'shared_resource','object_id':oid,'allowed_source_ids':[cid],'source_key':'board','public_task':t,'completion':'ACTUAL_TARGET_RESULT'})
  publish(rt,u,cid,s,src)
  if rt.config.get('cp22'):
   from .subject_cp22 import episode_links
   episode_links(rt,u,s,src)
  save(rt,u,cid,s)
 return {'kind':'subject_resource_cycle','cycle_id':'subject-resource:'+str(day),'source_id':src.source_event_id,'goal_ids':[s.goals[t['object_id']] for t in b['open_responsibilities']],'board':b,'channel_id':cid,'trigger':'RESPONSIBILITY_EXPERIENCE'},src

def world_context(product,u,s,b,src,key):
 rt=product.j;cid=rt.config['subject_resource_channel'];beliefs=[n.node_id for n in rt.graph.nodes_by_identity(u,[cid+':board']) if n.node_type=='Belief']
 ids=list(s.model_ids)+list(s.issue_ids)+[s.goals[t['object_id']] for t in b['open_responsibilities']]+beliefs[-1:]
 state=product.reconstruct(u,only=ids);snapshot=product.world.observe(u,key,src,state);ctx=product.world.context(u,snapshot)
 if rt.config.get('cp22'):
  from .subject_cp22 import recall
  ctx['resource_recall']=recall(rt,u,s,b,src,ctx,key)
 return ctx
def consumers(product,u,s,b,context):
 rt=product.j;risks=product.world.resource_risks(u,context,b);episodes=[rt.graph.get_node(u,id_).content|{'experience_id':id_} for id_ in s.experiences.values()];issues=[rt.graph.get_node(u,id_).content|{'node_id':id_} for id_ in s.issue_ids]
 if rt.config.get('cp22'):
  recalled_ids={eid for row in context.get('resource_recall',{}).values() for candidate in row['retrieved'][:3] for eid in candidate['evidence_ids']}
  if recalled_ids:episodes=[e for e in episodes if e['experience_id'] in recalled_ids]
 focus=product.resource_attention(b,risks,episodes,issues)
 if rt.config.get('cp22'):focus['recall_ids']=[row.get('recall_id') for row in context.get('resource_recall',{}).values()];focus['evidence_selection']='native activated subgraph support; cold-start ordinary episodes'
 action_risk=None
 if rt.config.get('action_risk_calibration'):
  from .action_risk import action_view
  action_risk=action_view(rt,u,b,risks)
 class Provider:
  def risk(self,t):return action_risk[t['object_id']]['used_p'] if action_risk is not None else risks['risks'][t['object_id']]
  def public_state(self):return {'world_state_id':context['state_id'],'model_versions':risks['model_versions']}
 table=value_table(b,Provider())
 if action_risk is not None:
  from .action_risk import decorate
  table=decorate(b,table)
 return {'action_risk':action_risk,'risk':risks,'attention':focus,'table':table,'selected':action(select(table)) if table else None,'minimum':[action(q) for q in minimum(table)] if table else []}
def steps(product,u,job,src):
 from skuld.app.runtime import ProviderFailure,ProviderFormatFailure
 rt=product.j;cid=job['channel_id'];b=job['board'];day=b['day'];s=load(rt,u,cid);key=job['cycle_id'];done=[n for n in rt.nodes(u,'ProductCycle') if n.semantic_identity_key==key]
 if done:return done[-1].content
 n=len([e for e in s.pool.history if e['object_id'] in s.experiences and rt.graph.get_node(u,s.experiences[e['object_id']]).content['object_id'] in s.goals]);op=str(n)
 if rt.config.get('cp22'):
  from .subject_cp22 import learning_steps
  yield from learning_steps(product,u,job,src,s,n)
 elif n in rt.config['subject_sec_nodes'] and op not in s.opportunities:
  s.opportunities[op]={'day':day,'status':'PREPARED','actual_calls':[]};save(rt,u,cid,s);entry=s.opportunities[op]
  view=product.experience_sec.view(rt,u,s,day);input_node=record(rt,u,'SECAttributionInput',op,view,src);wire_view=sec_wire_view(view,input_node.node_id)
  try:
   answer,receipt=yield ProviderRequest(u,'SEC_ATTRIBUTION',json.dumps(wire_view,separators=(',',':')),Attribution,key+':sec')
   entry['actual_calls'].append(receipt.node_id);s.issue_ids=[]
   for i,issue in enumerate(answer.issues):
    c=issue.model_dump()
    if any(e not in s.experiences.values() for e in c['experience_ids']) or any(k not in ('f1','f2','f3') or type(v) is not int or v not in (0,1) for k,v in c['scope'].items()):record(rt,u,'SECRejection',op+':'+str(i),{'reason':'SCOPE_OR_OWN_EXPERIENCE_REFERENCE','raw':c,'receipt_id':receipt.node_id},src);continue
    node=record(rt,u,'Hypothesis','sec-concern:'+op+':'+str(i),{'kind':'SEC_RESPONSIBILITY_CONCERN',**c,'provider_receipt_id':receipt.node_id,'attribution_status':'MODEL_EXPLANATION_NOT_IDENTIFIED_CAUSAL_CREDIT'},src);s.issue_ids.append(node.node_id)
   entry['status']='ATTRIBUTED';save(rt,u,cid,s)
   ctx=world_context(product,u,s,b,src,key+':attribution');pre=consumers(product,u,s,b,ctx);parents=parent_view(s.pool)
   for h in s.pool.hypotheses:
    if h['born_day']>=0 and h['status']=='ACTIVE' and h['id'] not in [p['id'] for p in parents]:parents.append({'id':h['id'],'version':h['version'],'tree':h['spec']['tree'],'paths':paths(h['spec']['tree']),'validation_n':len(h['scores'])})
   from skuld.learning.sec_evo_atomic import OP_FIELDS
   generation={'stage':'SEC_VARIANTS','day':day,'parents':parents,'tried_partitions':[signature(h['spec']['tree']) for h in s.pool.hypotheses],'last_rejections':s.pool.last_rejections,'sec_issues':[rt.graph.get_node(u,id_).content for id_ in s.issue_ids],'attention':pre['attention'],'selected_experiences':[rt.graph.get_node(u,id_).content|{'experience_id':id_} for id_ in pre['attention']['retrieved_experience_ids']],'condition_counts':view['condition_counts'],'support':view['candidate_support'],'operation_fields':{k:sorted(v|{'operation','reason'}) for k,v in OP_FIELDS.items()},'contract':'Propose up to4 bounded atomic mutations/crossovers to investigate SEC concerns. Empty items is allowed. Use displayed exact parents/versions/paths; change one allowed atomic operation. New hypotheses will only compete on future-issued forecasts, never receive past validation scores. No probabilities or actions. Depth2, binary f1/f2/f3, maximum4 leaves. Avoid previously tried partitions. Selection is unchanged Pool17 matched-future log loss with minimum4 common events, not your explanation.'}
   record(rt,u,'SECCandidateInput',op,generation,src)
   variants,receipt2=yield ProviderRequest(u,'SEC_VARIANTS',json.dumps(generation,separators=(',',':')),Variants,key+':variants');entry['actual_calls'].append(receipt2.node_id)
   before=len(s.pool.hypotheses);before_events=len(s.pool.events);s.pool.ingest({'mode':'EVO-MIX','items':variants.items,'input':{'parents':parents},'receipt':receipt2.node_id},day)
   for h in s.pool.hypotheses[before:]:record(rt,u,'CandidateBirth',h['id'],{'candidate_id':h['id'],'born_day':day,'scope':{'source_id':cid,'target':'SUPPLIER_DELAYED'},'spec':h['spec'],'receipt_id':receipt2.node_id,'sec_issue_ids':list(s.issue_ids),'training_cutoff_count':h['training_cutoff_count'],'validation_count_at_birth':len(h['scores'])},src)
   for event in s.pool.events[before_events:]:record(rt,u,'CandidateSelection',op+':'+str(len(rt.nodes(u,'CandidateSelection'))),event,src)
   entry.update(status='CANDIDATES_EVALUATING',new_ids=[h['id'] for h in s.pool.hypotheses[before:]],rejections=copy.deepcopy(s.pool.last_rejections))
  except (ProviderFailure,ProviderFormatFailure) as exc:entry.update(status='MODEL_FAILURE',error=str(exc))
  publish(rt,u,cid,s,src);save(rt,u,cid,s);record(rt,u,'SECOpportunity',op,entry,src)
 context=world_context(product,u,s,b,src,key+':decision');computed=consumers(product,u,s,b,context)
 if s.pending_shadow and b['open_responsibilities']:
  shadow=copy.deepcopy(context);old=[rt.graph.get_node(u,id_) for id_ in s.pending_shadow['previous_model_ids']];shadow['records']=[row for row in shadow['records'] if row['content'].get('kind')!='SUPPLIER_EXPERIENCE_MODEL']+[{'category':'hypotheses','node_id':node.node_id,'content':node.content} for node in old]
  before=consumers(product,u,s,b,shadow);record(rt,u,'LearningEdgeShadow',key,{'trigger':s.pending_shadow,'blocked_edge':'candidate publication -> world model risks -> internal attention / value calculation','board':b,'actual':computed,'prior_publication':before,'same_current_public_facts_and_sec_concerns':True,'executes':False,'business_benefit':None},src);s.shadow_done=True;s.pending_shadow=None
 predicted=[]
 for t in b['open_responsibilities']:
  oid=t['object_id'];s.pool.register(t,day)
  if rt.config.get('cp22'):
   from .subject_cp22 import register_aux
   register_aux(rt,s,t,day)
  if oid in s.predictions:continue
  p=computed['risk']['risks'][oid]
  if rt.config.get('cp22'):s.pool.forecasts[oid]['system_p']=p
  if abs(p-s.pool.forecasts[oid]['system_p'])>1e-10:raise ValueError('PUBLISHED_WORLD_MODEL_POOL_MISMATCH')
  f={'object_id':oid,'goal_id':s.goals[oid],'issued_day':day,'target_day':t['target_day'],'features':features(t),'p_issued':p,'model_version':s.version,'observation_id':src.source_event_id,'world_state_id':context['state_id'],'hypothesis_node_ids':[x['model_node_id'] for x in computed['risk'].get('activation_snapshots',{}).get(oid,{}).get('contributors',[])] if rt.config.get('cp22') else s.model_ids,'activation_snapshot':computed['risk'].get('activation_snapshots',{}).get(oid),'published_model_ids':s.model_ids,'candidate_forecasts':s.pool.forecasts[oid]['candidates']};node=record(rt,u,'SupplierForecast',oid,f,src);f['prediction_id']=node.node_id;s.predictions[oid]=f;predicted.append(node.node_id)
 save(rt,u,cid,s)
 loop={'predicted':predicted,'inferred':[],'actions':[]}
 if b['open_responsibilities']:
  focus=record(rt,u,'ExperienceAttention',key,computed['attention']|{'world_state_id':context['state_id'],'model_versions':computed['risk']['model_versions']},src)
  consumption=record(rt,u,'LearningConsumption',key,{'day':day,'world_state_id':context['state_id'],'hypothesis_node_ids':computed['risk']['hypothesis_node_ids'],'publication_versions':computed['risk']['model_versions'],'attention_node_id':focus.node_id,'retrieved_experience_ids':computed['attention']['retrieved_experience_ids'],'responsibility_ids':job['goal_ids'],'risks':computed['risk']['risks']},src)
  d=Decision(**computed['selected'],state_version=b['state_version'],risk_estimates=[],used_prediction_ids=[],reason='Shared value selector consumes native world-model hypothesis projection, with separate SEC internal attention.')
  result=execute(rt,u,cid,d,src);trace=execution_contract(rt,u,b,d,computed['table'],result)
  node=record(rt,u,'SubjectResourceDecision',key,{'day':day,'board':b,'responsibility_objects':[t['object_id'] for t in b['open_responsibilities']],'decision_source':'DETERMINISTIC_VALUE_SELECTOR','decision':d.model_dump(),'consumption_id':consumption.node_id,'world_state_id':context['state_id'],'models':s.model_ids,'computed':computed,'execution':result,'execution_contract':trace},src)
  if rt.config.get('action_risk_calibration'):
   from .action_risk import snapshot
   snapshot(rt,u,b,computed,node,src,s.goals,s.predictions)
  loop['decision']={'kind':'EXECUTE' if d.action!='WAIT' else 'WAIT','level':4 if d.action!='WAIT' else 0,'candidate_id':None,'decision_id':node.node_id,'mandate_id':None,'goal_ids':job['goal_ids']};loop['actions']=[result['action_id']] if result.get('action_id') else []
  for gid in job['goal_ids']:rt.product._work_update(u,gid,src,status='ACTIVE_RESOURCE',initial_pending=False)
  if not result.get('ok'):raise ValueError('SUBJECT_RESOURCE_EXECUTION_FAILURE')
 if rt.config.get('cp22'):
  from .subject_cp22 import finish_cycle
  finish_cycle(product,u,s,b,src,context,computed)
  save(rt,u,cid,s)
 final={'cycle_id':key,'loop':loop,'status':'DONE'};record(rt,u,'ProductCycle',key,final,src);return final
