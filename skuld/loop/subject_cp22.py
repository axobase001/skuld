"""CP22 native subject integration. Public evidence only; no simulator/task generator imports."""
import copy,json,math,uuid
from typing import Any,Literal
from pydantic import Field
from .resource_loop import Strict,record
from skuld.kernel.primitives import ProviderRequest
from skuld.kernel.schemas import SubjectHyperedge,SubjectHyperedgeParticipant
from skuld.kernel.admission.semantic_ir import SemanticEventIRDocument,parse_semantic_event_ir
from skuld.learning.subject_experience import experience_table,sec_wire_view,save,POOL_FIELDS
from skuld.learning.sec_evo_atomic import Pool17,parent_view,paths,all_atomic,compile_edit,signature
from skuld.learning.sec_evo import features,canonical

def dump(p):return {**{k:copy.deepcopy(getattr(p,k)) for k in POOL_FIELDS},'seen':sorted(p.seen)}
def restore(d):
 p=Pool17.__new__(Pool17)
 for k in POOL_FIELDS:setattr(p,k,copy.deepcopy(d[k]))
 p.seen=set(d['seen']);return p

def fixed(p):
 q=restore(dump(p));q.hypotheses=[h for h in q.hypotheses if h['status']=='ACTIVE'];q.mode='FIXED-MIX';return q

def executable_pool(rt,s):return restore(s.cp22['fixed']) if rt.config.get('cp22') and s.cp22.get('branch')=='Self-Frozen' else s.pool

def observe_aux(rt,s,b,day):
 # Save pre-update structures, then update their parameters with the CURRENT actual result.
 prev=fixed(executable_pool(rt,s));prev.observe(b['results'],day);s.cp22['current_old_projection']=dump(prev)
 if s.cp22.get('branch')=='Self-Frozen':s.cp22['fixed']=dump(prev)
 for sh in s.cp22.get('shadows',[]):
  q=restore(sh['pool']);q.observe(b['results'],day);sh['pool']=dump(q)

def register_aux(rt,s,t,day):
 if s.cp22.get('branch')=='Self-Frozen':
  q=restore(s.cp22['fixed']);q.register(t,day);s.cp22['fixed']=dump(q)
 for sh in s.cp22.get('shadows',[]):
  q=restore(sh['pool']);q.register(t,day);sh['pool']=dump(q)

def hyper(rt,u,kind,participants,src):
 ps=list(dict.fromkeys((role,nid) for role,nid in participants if nid))
 edge=SubjectHyperedge('hyper_'+uuid.uuid4().hex,u,kind,tuple(SubjectHyperedgeParticipant(a,b) for a,b in ps),rt.clock(),src.source_event_id,rt.clock())
 return rt.graph.append_hyperedge(edge)

def scene_body(t,b,cid,target='SUPPLIER_DELAYED',raw=None,ir=None):
 return {'object_id':t['object_id'],'roles':['supplier','responsibility_owner','backup_resource'],'intent':'assess_supplier_risk_and_deliver','target_variable':target,'features':features(t),'source_id':cid,'issued_day':b['day'],'target_day':t['target_day'],'booking_deadline':t['booking_deadline'],'horizon_days':3,'resource':{'capacity':1,'occupied':b['slot'] is not None,'holder':b['slot']['object_id'] if b['slot'] else None},'allowed_actions':[a for a in b['legal_actions'] if a['object_id'] in (None,t['object_id'])],'costs':{k:t[k] for k in ('reserve_fee','cancel_fee','miss_loss')},'permission':'existing_READ_and_simulated_STANDING_only','raw_input':raw,'semantic_ir':ir,'unknown_fields':[] if len(features(t))==3 else ['future_conditions'],'semantic_coordinates':{'roles':['supplier','responsibility_owner','backup_resource'],'intent':'assess_supplier_risk_and_deliver','target':target,'horizon':3,'resource_kind':'one_shared_slot'}}

def model_links(rt,u,cid,s,src,pool):
 prior=set(s.cp22.get('published',[]));active={h['id'] for h in pool.hypotheses if h['status']=='ACTIVE'};new=active-prior
 mapping={rt.graph.get_node(u,x).content['candidate_id']:x for x in s.model_ids}
 # Actual versioned model relations in native hypergraph; each is subsequently queried by recall.
 for h in pool.hypotheses:
  if h['status'] not in ('ACTIVE','PROBATION'):continue
  nid=mapping[h['id']];parents=[mapping[x] for x in h['spec']['parents'] if x in mapping]
  support=list(h['scores'])[-4:];ep=[s.experiences[x] for x in support if x in s.experiences]
  scope=record(rt,u,'SceneSignature','model-scope:'+h['id']+':'+str(s.version),{'semantic_coordinates':{'roles':['supplier','responsibility_owner','backup_resource'],'intent':'assess_supplier_risk_and_deliver','target':'SUPPLIER_DELAYED','horizon':3,'resource_kind':'one_shared_slot'},'source_id':cid,'target_variable':'SUPPLIER_DELAYED','condition_structure':h['spec']['tree'],'hypothesis_id':h['id'],'structure_version':h['version'],'version':s.version,'scope_kind':'SUPPLIER_NOT_FINAL_DELIVERY'},src)
  edge=hyper(rt,u,'EXECUTABLE_EXPERIENCE_MODEL',[('model',nid),('scope',scope.node_id)]+[('parent',x) for x in parents]+[('observed_support',x) for x in ep],src)
  if h['id'] in new:
   patch=record(rt,u,'ModelPatch',h['id']+':'+str(s.version),{'candidate_id':h['id'],'published_version':s.version,'model_node':nid,'hyperedge_id':edge.hyperedge_id,'parents':h['spec']['parents'],'day':next(f['value']['day'] for f in src.content['data']['facts'] if f['key']=='board'),'promotion_events':[e for e in s.pool.events if e.get('id')==h['id'] and e['type']=='PROMOTION'],'kind':'INITIAL' if h['born_day']<0 else 'STRUCTURE_PUBLICATION'},src)
   if h['born_day']>=0 and s.cp22.get('branch')!='Self-Frozen' and s.cp22.get('current_old_projection'):
    s.cp22.setdefault('shadows',[]).append({'patch_id':patch.node_id,'candidate_id':h['id'],'pool':copy.deepcopy(s.cp22['current_old_projection']),'sampled':[]})
 s.cp22['published']=sorted(active)

def semantic_consequences(rt,u,oid,result):
 out=[]
 for n in rt.nodes(u,'SemanticQueryResult'):
  c=n.content;f=c['frame']
  if f['object_id']!=oid:continue
  matched=(f['target_variable']=='SUPPLIER_DELAYED' or (f['premise']=='NO_BACKUP' and not result['backup_effective']) or (f['premise']=='RESERVE_AND_HOLD' and result['backup_effective']))
  out.append({'query_id':n.node_id,'input_id':f['input_id'],'supplier_activation_ref':c['supplier_activation']['recall_id'],'target':f['target_variable'],'premise':f['premise'],'issued_value':c['value'],'status':'RESOLVED' if matched else 'NOT_EXECUTED','observed_y':int(result['supplier_result']=='delayed') if f['target_variable']=='SUPPLIER_DELAYED' else int(result['on_time']) if matched else None,'no_counterfactual_truth':True})
 return out

def episode_links(rt,u,s,src):
 done=set(s.cp22.get('linked_episodes',[]))
 for oid,eid in s.experiences.items():
  if oid in done:continue
  e=rt.graph.get_node(u,eid).content;f=rt.graph.get_node(u,e['prediction_id']).content
  snap=f.get('activation_snapshot',{});scene_id=snap.get('scene_id')
  parts=[('experience',eid),('responsibility',e['goal_id']),('frozen_prediction',e['prediction_id']),('supplier_settlement',e['settlement_id']),('responsibility_outcome',e['responsibility_state_id'])]+[('prediction_contributor',x) for x in f['hypothesis_node_ids']]+[('decision',x) for x in e['decision_ids']]
  if scene_id:parts.append(('scene',scene_id))
  if snap.get('recall_id'):parts.append(('first_activation',snap['recall_id']))
  for did in e['decision_ids']:
   d=rt.graph.get_node(u,did).content
   for a in d['computed']['risk'].get('activation_snapshots',{}).values():
    if a.get('recall_id'):parts.append(('action_activation',a['recall_id']))
  for q in rt.nodes(u,'SemanticQueryResult'):
   if q.content['frame']['object_id']==oid:parts.append(('semantic_query',q.node_id))
  hyper(rt,u,'RESPONSIBILITY_EXPERIENCE',parts,src);done.add(oid)
 s.cp22['linked_episodes']=sorted(done)

def coordinates(scene):
 c=scene['semantic_coordinates'];return set(['target:'+c['target'],'intent:'+c['intent'],'horizon:'+str(c['horizon']),'resource:'+c['resource_kind']]+['role:'+x for x in c['roles']])

def checked_support(rt,u,eid):
 e=rt.graph.get_node(u,eid)
 if e is None or e.node_type!='ResponsibilityExperience':raise ValueError('INVALID_SUPPORT_EXPERIENCE')
 c=e.content;required={('experience',eid),('responsibility',c['goal_id']),('frozen_prediction',c['prediction_id']),('supplier_settlement',c['settlement_id']),('responsibility_outcome',c['responsibility_state_id'])}|{('decision',x) for x in c['decision_ids']}
 edges=[x for x in rt.graph.find_hyperedges_by_participant(u,eid) if x.relation_type=='RESPONSIBILITY_EXPERIENCE' and any(p.role=='experience' and p.node_id==eid for p in x.participants)]
 if not edges or not any(required <= {(p.role,p.node_id) for p in x.participants} for x in edges):raise ValueError('SUPPORT_PARTICIPANT_BINDING')
 for field,kind in [('prediction_id','SupplierForecast'),('settlement_id','SupplierForecastSettlement')]:
  n=rt.graph.get_node(u,c[field])
  if n is None or n.node_type!=kind:raise ValueError('SUPPORT_NODE_TYPE')
 if rt.graph.get_node(u,c['settlement_id']).content['prediction_id']!=c['prediction_id']:raise ValueError('SUPPORT_SETTLEMENT_PREDICTION')
 return c

def activate(sc,models,owned,prior,experienced):
 """One kernel for production and read-only projected structure shadows."""
 from skuld.learning.sec_evo import leaf
 if len(sc['features'])!=3:
  return {'retrieved':[],'activated_subgraphs':[],'activated_candidate_ids':[],'primary':None,'challengers':[],'fallback':'PUBLIC_OBSERVED_PRIOR_UNKNOWN_CONDITION','contributors':[],'mass':0.,'p_final':prior,'candidate_votes':{},'candidate_forecast_only':[],'target_variable':sc['target_variable'],'source_id':sc['source_id'],'horizon_days':sc['horizon_days'],'parameter_mode':'observed_global_prior_for_unreleased_conditions','host_contribution':{'role':'observation_prior','effective_weight':1.,'p_h':prior,'formula':'(1+observed_delayed)/(2+observed_results)'}}
 unique={};all_active=[];ps={}
 for row in models:
  h=row['content'];hid=h['candidate_id']
  if h['status'] not in ('ACTIVE','PROBATION'):continue
  aa,bb=h['counts'].get(leaf(h['tree'],sc['features']),[1,1]);ps[hid]=aa/(aa+bb) if len(sc['features'])==3 else prior
  if h['status']!='ACTIVE':continue
  if hid in unique:raise ValueError('DUPLICATE_AUTHORITATIVE_MODEL')
  links=owned(row);valid=[]
  for link in links:
   anchor=link['scope']
   if anchor['hypothesis_id']!=hid or anchor['version']!=h['publication_version'] or anchor['condition_structure']!=h['tree'] or anchor.get('structure_version',1)!=h.get('structure_version',1):raise ValueError('MODEL_SCOPE_VERSION_BINDING')
   if anchor['source_id']!=h['scope']['source_id'] or anchor['target_variable']!=h['scope']['target']:raise ValueError('MODEL_SCOPE_SOURCE_TARGET')
   if anchor['source_id']!=sc['source_id'] or anchor['target_variable']!=sc['target_variable'] or h['scope']['horizon_days']!=sc['horizon_days']:continue
   tok=coordinates(sc);oth=coordinates(anchor);semantic=len(tok&oth)/len(tok|oth);tree=h['tree'];path=[]
   while 'leaf' not in tree and len(sc['features'])==3:
    path.append({'feature':tree['feature'],'value':sc['features'][tree['feature']]});tree=tree['yes' if sc['features'][tree['feature']]==tree['equals'] else 'no']
   valid.append({'subgraph_id':link['id'],'version':h['publication_version'],'structure_version':h.get('structure_version',1),'hypothesis_id':hid,'model_node_id':row['node_id'],'semantic_score':semantic,'topology_match':{'roles':True,'horizon':True,'condition_path':path},'applicability':True,'future_fitness':{'n':h['prospective_n'],'weight':h['weight']},'evidence_ids':link['support'],'sort_key':[-semantic,-len(path),-h['weight'],hid]})
  if not links:raise ValueError('MISSING_AUTHORITATIVE_MODEL_EDGE')
  # Merge duplicate valid links before ranking. Edge order cannot create votes.
  if valid:
   c=min(valid,key=lambda c:c['subgraph_id']);c['evidence_ids']=sorted({v for x in valid for v in x['evidence_ids']});unique[hid]=c;all_active.append(row)
 eligible=sorted(unique.values(),key=lambda c:c['sort_key'])[:5];selected=eligible[:3] if experienced else []
 fallback=None if selected else 'ORIGINAL_HOST_MIX_NO_EXPERIENCE_OR_MATCH'
 contributors=selected or list(sorted(unique.values(),key=lambda c:c['hypothesis_id']))
 if not contributors:raise ValueError('NO_TARGET_MATCHED_ACTIVE_MODEL')
 mass=sum(c['future_fitness']['weight'] for c in contributors)
 if not math.isfinite(mass) or mass<=0:raise ValueError('INVALID_ACTIVATION_MASS')
 terms=[{**c,'global_weight':c['future_fitness']['weight'],'effective_weight':c['future_fitness']['weight']/mass,'p_h':ps[c['hypothesis_id']],'role':'prediction_contributor'} for c in contributors]
 p=sum(c['effective_weight']*c['p_h'] for c in terms)
 return {'selection_rule':'semantic_match_then_path_specificity_then_existing_prospective_weight_then_id','ranked_candidate_ids':[c['hypothesis_id'] for c in sorted(unique.values(),key=lambda c:c['sort_key'])],'retrieved':eligible,'activated_subgraphs':[x['subgraph_id'] for x in selected],'activated_candidate_ids':[x['hypothesis_id'] for x in selected],'primary':selected[0]['subgraph_id'] if selected else None,'challengers':[x['subgraph_id'] for x in selected[1:]],'fallback':fallback,'contributors':terms,'mass':mass,'p_final':p,'candidate_votes':ps,'candidate_forecast_only':[row['node_id'] for row in models if row['content']['status']=='PROBATION'],'target_variable':sc['target_variable'],'source_id':sc['source_id'],'horizon_days':sc['horizon_days'],'parameter_mode':'observed_global_prior_for_unreleased_conditions' if len(sc['features'])!=3 else 'condition_leaf_posterior'}

def recalled(rt,u,s,b,src,context,key,recording=True,projection=None,query_frame=None):
 models=[r for r in context['records'] if r['content'].get('kind')=='SUPPLIER_EXPERIENCE_MODEL'];out={};prior=(1+sum(t['supplier_result']=='delayed' for t in b['results']))/(2+len(b['results']))
 def owned(row):
  if projection is not None:return projection[row['node_id']]
  links=[]
  for edge in rt.graph.find_hyperedges_by_participant(u,row['node_id']):
   if edge.relation_type!='EXECUTABLE_EXPERIENCE_MODEL' or not any(p.role=='model' and p.node_id==row['node_id'] for p in edge.participants):continue
   scopes=[rt.graph.get_node(u,p.node_id) for p in edge.participants if p.role=='scope']
   if len(scopes)!=1 or scopes[0] is None:raise ValueError('MODEL_SCOPE_MISSING')
   from skuld.kernel.primitives import instant
   if instant(scopes[0].created_at)>instant(rt.clock()):raise ValueError('FUTURE_SCOPE')
   support=sorted({p.node_id for p in edge.participants if p.role=='observed_support'})
   for eid in support:checked_support(rt,u,eid)
   links.append({'id':edge.hyperedge_id,'scope':scopes[0].content,'support':support})
  return links
 for t in b['open_responsibilities']+b['future_commitments']:
  if query_frame and t['object_id']!=query_frame['object_id']:continue
  sc=scene_body(t,b,rt.config['subject_resource_channel'])
  if query_frame:sc.update(input_id=query_frame['input_id'],query_frame=query_frame)
  sn=record(rt,u,'SceneSignature',key+':'+t['object_id'],sc,src) if recording else None
  row=activate(sc,models,owned,prior,bool(s.processed));row.update(scene_id=sn.node_id if sn else None,input_id=sc.get('input_id'),object_id=t['object_id'],day=b['day'],permissions_inferred=False,source='native_hyperedge_participant_query',scene=sc)
  if row.get('host_contribution'):
   row['host_contribution'].update(source_id=rt.config['subject_resource_channel'],source_observation_id=src.source_event_id if src else None,observed_results=len(b['results']),observed_delayed=sum(t['supplier_result']=='delayed' for t in b['results']),replay_without_new_observation=src is None)
  if recording:
   node=record(rt,u,'RecallActivation',key+':'+t['object_id'],row,src);row['recall_id']=node.node_id
   hyper(rt,u,'ACTIVATED_FOR_TRACE',[('scene',sn.node_id),('recall',node.node_id)]+[('prediction_contributor',x['model_node_id']) for x in row['contributors']],src)
  out[t['object_id']]=row
 return out
recall=recalled

class BoundReference(Strict):
 experience_id:str
 prediction_version:int
 role:Literal['SAME_CONDITION_SUPPORT','COUNTEREVIDENCE','CONTRAST','CONTEXT']
 asserted_values:dict[str,Any]
class BoundIssue(Strict):
 locus:Literal['CONDITION_MODEL','ACTION_CONSEQUENCE','DECISION_TRADEOFF','EXECUTION','INFORMATION_GAP','UNCERTAIN']
 target_variable:Literal['SUPPLIER_DELAYED','FINAL_ON_TIME','RESERVATION_COST','CANCELLATION_COST','MISS_COST']
 scope_predicates:dict[str,int]
 references:list[BoundReference]=Field(min_length=1,max_length=4)
 priority:float=Field(ge=0,le=1)
 question_or_hypothesis:str
 prospective_condition_signature:str
class Attribution22(Strict):issues:list[BoundIssue]=Field(max_length=2)
class EditChoice(Strict):
 edit_id:str
 question_or_hypothesis:str
 references:list[BoundReference]=Field(default_factory=list,max_length=4,description='Cite displayed experience_id and its exact frozen prediction_version, with at least one exact asserted_values field. Missing or invalid references reject only this choice. No inferred ID/version repair.')
class Variants22(Strict):items:list[EditChoice]=Field(max_length=4)

def validate_issue(c,shown):
 scope=c['scope_predicates'];errors=[]
 if any(k not in ('f1','f2','f3') or type(v) is not int or v not in (0,1) for k,v in scope.items()):errors.append('INVALID_SCOPE')
 for i,r in enumerate(c['references']):
  e=shown.get(r['experience_id'])
  if e is None:errors.append(f'{i}:NOT_DISPLAYED');continue
  if r['prediction_version']!=e['prediction_version']:errors.append(f'{i}:ISSUED_VERSION_MISMATCH')
  if r['role'] in ('SAME_CONDITION_SUPPORT','COUNTEREVIDENCE') and any(e['features'].get(k)!=v for k,v in scope.items()):errors.append(f'{i}:CONDITION_MISMATCH')
  if not r['asserted_values']:errors.append(f'{i}:EMPTY_FACT_CLAIM')
  for field,value in r['asserted_values'].items():
   actual=e
   for k in field.split('.'):
    if not isinstance(actual,dict) or k not in actual:actual=object();break
    actual=actual[k]
   if actual!=value:errors.append(f'{i}:FIELD_MISMATCH:{field}')
 return errors

def validate_choice(choice,shown):
 if not choice['references']:return ['MISSING_EXPERIENCE_REFERENCE']
 return validate_issue({'scope_predicates':{},'references':choice['references']},shown)

def candidate_handoff(generation,view,shown,attribution_input_id):
 # Pass the exact bounded SEC episodes, not a second independent attention sample.
 generation.pop('selected_experiences',None)
 generation['experiences']=copy.deepcopy(view['experiences'])
 generation['attribution_input_id']=attribution_input_id
 generation['legal_experience_references']=[{'experience_id':eid,'prediction_version':e['prediction_version']} for eid,e in shown.items()]
 generation['reference_contract']='asserted_values keys are exact dotted paths into the displayed episode: supplier_result, supplier_y, final_on_time, object_id, features.f1, features.f2, features.f3, fees.reservation, fees.cancellation, fees.miss. Do not flatten fees.miss into miss. One exact factual field is sufficient; cite only fields actually used. prediction_version is the exact legal_experience_references value, never a day. No host correction.'
 generation['contract']+=' Each proposed edit must cite its own displayed experiences, exact frozen prediction_version and factual asserted_values. The experience table is the same bounded evidence just supplied to SEC, including query consequences. Rejected SEC claims are not facts; examine the original experiences yourself. State a testable question, not an established causal conclusion. KEEP is valid. Evidence references indicate proposal basis, not validation credit; new candidates start with zero future scores.'
 return generation

def catalogue(parents,seen):
 groups={}
 for op in all_atomic(parents):
  try:spec=compile_edit(op,parents);sig=signature(spec['tree'])
  except ValueError:continue
  if sig in seen:continue
  groups.setdefault(sig,[]).append(op)
 out=[]
 for i,(sig,ops) in enumerate(sorted(groups.items())):
  op=ops[0];pid=op.get('parent_id',op.get('recipient_parent_id'));path=op.get('node_path',op.get('recipient_path'));tree=next(p['tree'] for p in parents if p['id']==pid);predicates=[]
  for side in path:predicates.append({'feature':tree['feature'],'equals':tree['equals'],'branch':side});tree=tree[side]
  out.append({'edit_id':'E'+str(i+1),'operation':op,'path_predicates':predicates,'result_partition':sig,'before_partition':signature(next(p['tree'] for p in parents if p['id']==pid)),'equivalent_legal_origins':ops,'result_tree':compile_edit(op,parents)['tree']})
 return out

def evidence_view(product,u,s,day):
 full=product.experience_sec.view(product.j,u,s,day);rt=product.j
 if s.cp22.get('branch')=='Self-Frozen':
  q=executable_pool(rt,s);full['candidate_support']=[{'id':h['id'],'born_day':h['born_day'],'status':h['status'],'future_n':len(h['scores']),'future_mean_log_loss':sum(v['loss'] for v in h['scores'].values())/len(h['scores']) if h['scores'] else None} for h in q.hypotheses]
 es=[rt.graph.get_node(u,eid).content|{'experience_id':eid} for eid in s.experiences.values()]
 # Fixed coverage: most recent observed episode per condition cell, then latest overall, max12.
 chosen={}
 for e in es:chosen[canonical(e['features'])]=e
 selected=list(chosen.values());ids={e['experience_id'] for e in selected}
 for e in reversed(es):
  if len(selected)>=12:break
  if e['experience_id'] not in ids:selected.append(e);ids.add(e['experience_id'])
 selected.sort(key=lambda e:(e['observed_day'],e['object_id']))
 full.update(experiences=experience_table(selected),shown_count=len(selected),selection_rule='latest episode per all observed condition cells, then latest overall to12; stable observed-day/object order',all_raw_experiences_persisted=True)
 full['legal_experience_references']=[{'experience_id':e['experience_id'],'prediction_version':e['prediction_version']} for e in selected]
 full['contract']='Cite ONLY displayed experience_id and frozen prediction_version. Use the exact legal_experience_references pair; prediction_version is NOT issued_day, observed_day or current world version. Use asserted_values with exact dotted fields (features.f1, fees.reservation, fees.cancellation, fees.miss, supplier_result, final_on_time, object_id). Every factual assertion must be in asserted_values. SAME_CONDITION_SUPPORT and COUNTEREVIDENCE must match scope_predicates; CONTRAST may differ. question_or_hypothesis is a question, not a factual summary or established causal truth. Supplier labels, backup delivery and costs are independent. At most2 issues; KEEP allowed. No unseen references.'
 return full,{e['experience_id']:e for e in selected}

SEMANTIC_CONTRACT = """Parse source-grounded SemanticEventIRDocument; literal fragments only. No probabilities, model IDs, permissions or answers. Exactly one affirmative DIRECT REQUEST/QUESTION for the actual question, relation SUPPLIER_DELAYED or FINAL_ON_TIME, object and target_ref = entity local ID whose surface is the exact public object ID. Mentioned but not requested objects stay separate. Negated/quoted requests are NOT affirmative requests. Unknown target uses UNKNOWN. Preserve temporal_refs (surface '交付目标' or literal dayN) and negation/contrast separately.
Exact kind -> modality pairs are mandatory: HYPOTHETICAL -> HYPOTHETICAL; CONDITIONAL -> CONDITIONAL; QUESTION -> QUESTIONED; QUOTE -> QUOTED; REPORTED_SPEECH -> REPORTED; COUNTERFACTUAL -> COUNTERFACTUAL. Never mix HYPOTHETICAL kind with CONDITIONAL modality. For an explicit hypothetical successful reservation by deadline and holding through target: separate HYPOTHETICAL event relation RESERVE_AND_HOLD, modality HYPOTHETICAL, POSITIVE DIRECT, object/target_ref same entity. For explicit hypothetical no backup: separate HYPOTHETICAL event relation NO_BACKUP, modality HYPOTHETICAL, POSITIVE DIRECT (positive assertion of that premise). A CONDITIONAL event must instead use modality CONDITIONAL. This is only an event representation contract, not a probability or action answer. Connect premise event to question event using relation_type CONDITION_FOR, participants roles condition and query. Do not infer either premise if absent. No real action is authorized. Keep quoted/negated alternatives separate; never silently convert them to the primary question."""

def query_frame(doc,b,input_id):
 entities={e['local_id']:e for e in doc['entities']};events={e['local_id']:e for e in doc['events']}
 req=[e for e in doc['events'] if e['kind'] in ('REQUEST','QUESTION') and e['source_mode']=='DIRECT' and e['modality'] in ('QUESTIONED','INTENDED') and e['polarity'] in ('POSITIVE','UNRESOLVED')]
 if len(req)!=1:raise ValueError('AMBIGUOUS_OR_NO_PRIMARY_REQUEST')
 e=req[0]
 if e['relation'] not in ('SUPPLIER_DELAYED','FINAL_ON_TIME'):raise ValueError('UNSUPPORTED_TARGET')
 if e['object']!=e['target_ref'] or e['object'] not in entities:raise ValueError('UNBOUND_PRIMARY_OBJECT')
 oid=entities[e['object']]['surface'];ts=[t for t in b['open_responsibilities'] if t['object_id']==oid]
 if len(ts)!=1:raise ValueError('OBJECT_NOT_PUBLIC_OPEN')
 t=ts[0];premises=[];premise_ids=[]
 for rel in doc['relations']:
  if rel['relation_type']!='CONDITION_FOR':continue
  parts={p['role']:p['ref'] for p in rel['participants']}
  if parts.get('query')!=e['local_id']:continue
  c=events.get(parts.get('condition'))
  if not c or c['kind'] not in ('CONDITIONAL','HYPOTHETICAL') or c['modality'] not in ('CONDITIONAL','HYPOTHETICAL') or c['source_mode']!='DIRECT' or c['polarity']!='POSITIVE' or c['object']!=e['object'] or c['target_ref']!=e['object']:raise ValueError('INVALID_ACTION_PREMISE_BINDING')
  if c['relation'] not in ('RESERVE_AND_HOLD','NO_BACKUP'):raise ValueError('UNSUPPORTED_ACTION_PREMISE')
  premises.append(c['relation']);premise_ids.append(c['local_id'])
 if len(premises)>1:raise ValueError('AMBIGUOUS_PREMISES')
 if e['relation']=='FINAL_ON_TIME' and not premises:raise ValueError('FINAL_REQUIRES_EXPLICIT_PREMISE')
 if e['relation']=='SUPPLIER_DELAYED' and premises:raise ValueError('SUPPLIER_TARGET_CANNOT_BE_INTERVENED')
 temporals={x['local_id']:x for x in doc['temporals']};time_evidence=[]
 import re
 for tid in e['temporal_refs']:
  surface=temporals[tid]['surface'];m=re.fullmatch(r'day(\d+)',surface)
  if surface not in ('交付目标','目标时点','交付目标时点') and not (m and int(m.group(1))==t['target_day']):raise ValueError('UNSUPPORTED_OR_WRONG_TARGET_TIME')
  time_evidence.append({'ir_ref':tid,'surface':surface,'resolved_day':t['target_day']})
 return {'input_id':input_id,'object_id':oid,'target_variable':e['relation'],'premise':premises[0] if premises else None,'mode':'CONDITIONAL_QUERY' if premises else 'SUPPLIER_QUERY','event_ref':e['local_id'],'object_ref':e['object'],'premise_refs':premise_ids,'target_day':t['target_day'],'booking_deadline':t['booking_deadline'],'issued_day':b['day'],'state_version':b['state_version'],'temporal_evidence':time_evidence,'field_sources':{'object':'IR entity surface','target':'IR primary relation','premise':'IR CONDITION_FOR relation','target_day':'public object board, validated against explicit IR time when present','booking_deadline':'public object board'},'nonprimary_events':[x for x in doc['events'] if x['local_id'] not in [e['local_id']]+premise_ids],'permissions_inferred':False,'expires':'THIS_TRACE_ONLY'}

def consume_query(rt,u,s,b,src,frame,key):
 from .subject_resource import world_context
 ctx=world_context(rt.product,u,s,b,src,key+':context')
 rc=recalled(rt,u,s,b,src,ctx,key,query_frame=frame)[frame['object_id']]
 p=rc['p_final'];premise=frame['premise'];target=frame['target_variable']
 # Public ResourceWorld rule: on_time = backup_effective OR supplier_ready.
 if target=='SUPPLIER_DELAYED':answer=p;formula='p_supplier_delayed';contributors=rc['contributors'];rule=None
 elif premise=='RESERVE_AND_HOLD':answer=1.;formula='P(on_time | legal successful reserve and held to target)=1';contributors=[];rule='ResourceWorld.advance: backup_effective OR supplier_ready'
 elif premise=='NO_BACKUP':answer=1-p;formula='P(on_time | no_backup)=1-p_supplier_delayed';contributors=rc['contributors'];rule='ResourceWorld.advance: backup_effective OR supplier_ready'
 else:raise ValueError('QUERY_CONSUMER_UNCOVERED')
 body={'frame':frame,'supplier_activation':rc,'supplier_p_unchanged':p,'value':answer,'formula':formula,'public_rule':rule,'prediction_contributor_ids':[x['model_node_id'] for x in contributors],'context_only_supplier_ids':[x['model_node_id'] for x in rc['contributors']] if not contributors else [],'executes':False,'conditional_not_actual_fact':bool(premise),'actual_action_unchanged':True}
 node=record(rt,u,'SemanticQueryResult',key,body,src)
 hyper(rt,u,'SEMANTIC_QUERY_CONSUMPTION',[('input',frame['input_id']),('result',node.node_id),('supplier_activation',rc['recall_id'])]+[('prediction_contributor',x['model_node_id']) for x in contributors],src)
 return node

def semantic_text(b,which):
 # Input generator only chooses public names; consumer receives raw text, never this selection.
 ts=b['open_responsibilities'];a=ts[0]['object_id'];second=ts[1]['object_id'] if len(ts)>1 else None
 if which=='D1':return f'请判断对象 {a} 在交付目标时原供应商是否延迟。'
 if which=='D2':return f'对于对象 {a}，我想知道交付目标时原供应商会不会延误。'
 if which=='D3':
  if second is None:raise ValueError('SECOND_PUBLIC_OBJECT_UNAVAILABLE')
  return f'当前有对象 {a} 和 {second}；这次只询问 {second} 在交付目标时原供应商是否延迟，不询问 {a}。'
 if which=='D4':return f'对于对象 {a}，假设在合法预订截止前成功预约备援并持续持有到交付目标，请判断该条件下最终是否准时交付。这是假设，不执行预约。'
 if which=='D5':return f'对于对象 {a}，假设不使用备援，请判断交付目标时最终是否准时交付。这是假设，不改变已有预约。'
 if which=='D6':return f'有人引用了“对象 {a} 最终是否准时交付”；我不是在请求这个判断，也没有指定要询问什么。'
 raise ValueError('UNKNOWN_SEMANTIC_TEMPLATE')

def semantic_once(rt,u,s,b,src,text,key):
 from skuld.app.runtime import ProviderFailure,ProviderFormatFailure
 raw=record(rt,u,'SceneInput',key,{'raw_input':text,'day':b['day'],'readonly':True},src)
 try:
  answer,receipt=yield ProviderRequest(u,'SCENE_SEMANTIC_IR',json.dumps({'raw_text':text,'contract':SEMANTIC_CONTRACT},ensure_ascii=False),SemanticEventIRDocument,key)
  parsed=parse_semantic_event_ir(answer.model_dump(mode='json'),raw_text=text,raw_model_output_id=receipt.node_id);doc=parsed.document.model_dump(mode='json');frame=query_frame(doc,b,raw.node_id)
  node=record(rt,u,'SemanticSceneAdmission',key,{'frame':frame,'raw_input':text,'semantic_ir':doc,'receipt_id':receipt.node_id},src)
  hyper(rt,u,'SEMANTIC_SCENE',[('raw_input',raw.node_id),('admitted_scene',node.node_id)],src)
 except (ValueError,ProviderFailure,ProviderFormatFailure) as exc:
  record(rt,u,'SemanticSceneRejection',key,{'input_id':raw.node_id,'error':str(exc),'no_template_replacement':True},src);return
 # Host integrity faults in projection are not model parse failures.
 consume_query(rt,u,s,b,src,frame,key)

def semantic_steps(rt,u,s,b,src):
 matured=len(s.processed)
 if matured not in (8,32) or str(matured) in s.cp22.setdefault('nl_done',[]):return
 s.cp22['nl_done'].append(str(matured));save(rt,u,rt.config['subject_resource_channel'],s)
 seed=rt.config.get('cp22_r1_seed',220201)
 which={220201:('D1','D3'),220202:('D2','D4'),220203:('D1','D5')}[seed][int(matured==32)]
 if seed==220203 and matured==8:
  oid=b['open_responsibilities'][0]['object_id'];text=f'对于对象 {oid}，不要判断备援后的最终交付；只问交付目标时原供应商是否延迟。'
 else:text=semantic_text(b,which)
 yield from semantic_once(rt,u,s,b,src,text,'formal:'+str(matured))
 save(rt,u,rt.config['subject_resource_channel'],s)

def learning_steps(product,u,job,src,s,n):
 from .subject_resource import publish,world_context,consumers
 from skuld.app.runtime import ProviderFailure,ProviderFormatFailure
 rt=product.j;cid=job['channel_id'];b=job['board'];day=b['day'];op=str(n);key=job['cycle_id']
 yield from semantic_steps(rt,u,s,b,src)
 if n not in rt.config['subject_sec_nodes'] or op in s.opportunities:return
 entry={'day':day,'status':'PREPARED','actual_calls':[]};s.opportunities[op]=entry;save(rt,u,cid,s)
 view,shown=evidence_view(product,u,s,day);node=record(rt,u,'SECAttributionInput',op,view,src);attribution_input_id=node.node_id
 try:
  answer,receipt=yield ProviderRequest(u,'SEC_ATTRIBUTION',json.dumps(sec_wire_view(view,node.node_id),separators=(',',':')),Attribution22,key+':sec');entry['actual_calls'].append(receipt.node_id);s.issue_ids=[]
  for i,issue in enumerate(answer.issues):
   c=issue.model_dump();errors=validate_issue(c,shown)
   if errors:record(rt,u,'SECRejection',op+':'+str(i),{'errors':errors,'raw':c,'receipt_id':receipt.node_id,'verified_fact':False},src);continue
   node=record(rt,u,'Hypothesis','sec-concern:'+op+':'+str(i),{'kind':'SEC_RESPONSIBILITY_CONCERN',**c,'scope':c['scope_predicates'],'experience_ids':[r['experience_id'] for r in c['references']],'reason':c['question_or_hypothesis'],'provider_receipt_id':receipt.node_id,'verified_fields_only':True,'causal_truth':False},src);s.issue_ids.append(node.node_id)
   hyper(rt,u,'BOUND_SEC_QUESTION',[('question',node.node_id)]+[('evidence_'+r['role'],r['experience_id']) for r in c['references']],src)
  save(rt,u,cid,s);ctx=world_context(product,u,s,b,src,key+':attribution');pre=consumers(product,u,s,b,ctx)
  parents=parent_view(s.pool)
  for h in s.pool.hypotheses:
   if h['born_day']>=0 and h['status']=='ACTIVE' and h['id'] not in [p['id'] for p in parents]:parents.append({'id':h['id'],'version':h['version'],'tree':h['spec']['tree'],'paths':paths(h['spec']['tree']),'validation_n':len(h['scores'])})
  full=catalogue(parents,{signature(h['spec']['tree']) for h in s.pool.hypotheses});start=((n//8-1)*16)%max(1,len(full));display=(full[start:]+full[:start])[:16]
  cat=record(rt,u,'LegalEditCatalogue',op,{'parents':parents,'all_entries':full,'shown_edit_ids':[x['edit_id'] for x in display],'rule':'signature order; rotating16; no labels/scores for order'},src)
  generation={'stage':'SEC_VARIANTS','day':day,'catalogue_id':cat.node_id,'catalogue_total':len(full),'shown_count':len(display),'legal_edits':[{k:v for k,v in x.items() if k!='equivalent_legal_origins'} for x in display],'parents':parents,'sec_issues':[rt.graph.get_node(u,x).content for x in s.issue_ids],'attention':pre['attention'],'selected_experiences':[rt.graph.get_node(u,x).content|{'experience_id':x} for x in pre['attention']['retrieved_experience_ids']],'condition_counts':view['condition_counts'],'candidate_support':view['candidate_support'],'projection_branch':s.cp22.get('branch','Prefix'),'contract':'Choose up to4 distinct DISPLAYED edit_id values and state a testable question. KEEP empty allowed. The directory is syntax-only, not an optimum answer. No new operation JSON, no probabilities. Parents/paths/versions and resulting partitions are fixed by each ID. New candidates validate only on later issued events. Self-Frozen candidate learning is shadow-only.'}
  generation=candidate_handoff(generation,view,shown,attribution_input_id)
  candidate_input=record(rt,u,'SECCandidateInput',op,generation,src);answer,receipt=yield ProviderRequest(u,'SEC_VARIANTS',json.dumps(sec_wire_view(generation,candidate_input.node_id),separators=(',',':')),Variants22,key+':variants');entry['actual_calls'].append(receipt.node_id)
  lookup={x['edit_id']:x for x in display};items=[];used=set();accepted_choices={}
  for i,c in enumerate(answer.items):
   if c.edit_id not in lookup or c.edit_id in used:record(rt,u,'CandidateChoiceRejection',op+':'+str(i),{'raw':c.model_dump(),'reason':'NOT_DISPLAYED_OR_DUPLICATE','receipt_id':receipt.node_id},src);continue
   errors=validate_choice(c.model_dump(),shown)
   if errors:record(rt,u,'CandidateChoiceRejection',op+':'+str(i),{'original_index':i,'raw':c.model_dump(),'errors':errors,'receipt_id':receipt.node_id},src);continue
   accepted_choices[lookup[c.edit_id]['result_partition']]=c.model_dump()
   used.add(c.edit_id);edit=copy.deepcopy(lookup[c.edit_id]['operation']);edit['reason']=c.question_or_hypothesis;items.append(edit)
  before=len(s.pool.hypotheses);be=len(s.pool.events);s.pool.ingest({'mode':'EVO-MIX','items':items,'input':{'parents':parents},'receipt':receipt.node_id},day)
  for h in s.pool.hypotheses[before:]:
   accepted_choice=accepted_choices[signature(h['spec']['tree'])]
   birth=record(rt,u,'CandidateBirth',h['id'],{'candidate_id':h['id'],'born_day':day,'spec':h['spec'],'receipt_id':receipt.node_id,'catalogue_id':cat.node_id,'raw_choices':[x.model_dump() for x in answer.items],'sec_issue_ids':list(s.issue_ids),'candidate_input_id':candidate_input.node_id,'experience_references':accepted_choice['references'],'accepted_choice':accepted_choice,'validation_count_at_birth':0,'training_cutoff_count':h['training_cutoff_count'],'shadow_only':s.cp22.get('branch')=='Self-Frozen'},src)
   hyper(rt,u,'CANDIDATE_FROM_EXPERIENCE',[('candidate_birth',birth.node_id),('catalogue',cat.node_id)]+[('sec_question',x) for x in s.issue_ids]+[('proposal_evidence',x['experience_id']) for x in accepted_choice['references']],src)
  for e in s.pool.events[be:]:record(rt,u,'CandidateSelection',op+':'+str(len(rt.nodes(u,'CandidateSelection'))),e,src)
  entry.update(status='CANDIDATES_EVALUATING',new_ids=[h['id'] for h in s.pool.hypotheses[before:]],rejections=s.pool.last_rejections)
 except (ProviderFailure,ProviderFormatFailure) as exc:entry.update(status='MODEL_FAILURE',error=str(exc))
 publish(rt,u,cid,s,src);save(rt,u,cid,s);record(rt,u,'SECOpportunity',op,entry,src)

def finish_cycle(product,u,s,b,src,context,computed):
 rt=product.j
 for sh in s.cp22.get('shadows',[]):
  used=any(sh['candidate_id'] in r['activated_candidate_ids'] for r in context.get('resource_recall',{}).values())
  if not used or len(sh['sampled'])>=2 or b['day'] in sh['sampled']:continue
  q=restore(sh['pool']);ws=q.weights();old=copy.deepcopy(context);rows=[];projection={}
  for h in q.hypotheses:
   nodes=[n for n in rt.nodes(u,'Hypothesis') if n.content.get('kind')=='SUPPLIER_EXPERIENCE_MODEL' and n.content['candidate_id']==h['id'] and n.content['tree']==h['spec']['tree'] and n.content['status']=='ACTIVE']
   base=max(nodes,key=lambda n:n.content['publication_version']);c=copy.deepcopy(base.content);c.update(counts=copy.deepcopy(h['counts']),weight=ws[h['id']],status='ACTIVE',publication_version='shadow-current-prefix:'+str(b['day']),prospective_n=len(h['scores']))
   nid='shadow:'+base.node_id;rows.append({'category':'hypotheses','node_id':nid,'content':c})
   edges=[e for e in rt.graph.find_hyperedges_by_participant(u,base.node_id) if e.relation_type=='EXECUTABLE_EXPERIENCE_MODEL' and any(p.role=='model' and p.node_id==base.node_id for p in e.participants)]
   if not edges:raise ValueError('SHADOW_NATIVE_OWNERSHIP_MISSING')
   edge=min(edges,key=lambda e:e.hyperedge_id);scope=copy.deepcopy(next(rt.graph.get_node(u,p.node_id).content for p in edge.participants if p.role=='scope'));scope['version']=c['publication_version']
   support=[s.experiences[oid] for oid in list(h['scores'])[-4:] if oid in s.experiences]
   for eid in support:checked_support(rt,u,eid)
   projection[nid]=[{'id':'shadow:'+edge.hyperedge_id,'scope':scope,'support':support,'derived_from_model':base.node_id}]
  old['records']=[r for r in old['records'] if r['content'].get('kind')!='SUPPLIER_EXPERIENCE_MODEL']+rows
  old['resource_recall']=recalled(rt,u,s,b,src,old,'shadow',False,projection=projection)
  from .subject_resource import consumers
  prior=consumers(product,u,s,b,old);record(rt,u,'StructureConsumptionShadow',sh['patch_id']+':'+str(b['day']),{'patch_id':sh['patch_id'],'day':b['day'],'candidate_id':sh['candidate_id'],'current_prefix_n':len(q.history),'actual':computed,'old_structure':prior,'derived_models':rows,'derived_ownership':projection,'shared_kernel':'subject_cp22.activate','mask':['executable_structure_set','derived_activation'],'unchanged':['public_board','current_observation_prefix','permissions','selector'],'executes':False,'business_benefit':None},src);sh['sampled'].append(b['day'])
