"""Focused L1 responsibility lifecycle; no planner or general semantic judge."""
import json
from decimal import Decimal
from datetime import datetime

def plan_time_error(value,target):
 try:
  parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
 except (ValueError,TypeError,AttributeError):return "PLAN_TIME_INVALID"
 if parsed.tzinfo is None or parsed.utcoffset() is None:return "PLAN_TIME_ZONE_REQUIRED"
 return None if parsed==datetime.fromisoformat(target.replace("Z","+00:00")) else "PLAN_TIME_MISMATCH"

from skuld.kernel.primitives import instant,nid,dumps
from skuld.kernel.schemas import AuthorityClass
class ResponsibilityMixin:
 def _latest_record(self,u,kind,key):
  return max((n for n in self.graph.nodes_by_identity(u,[key]) if n.node_type==kind),key=lambda n:n.content.get('seq',0),default=None)
 def responsibility_contract(self,u,gid):
  n=self._latest_record(u,'ResponsibilityContract','contract:'+gid);return dict(n.content) if n else {}
 def set_responsibility_contract(self,u,gid,contract):
  goal=self.graph.get_node(u,gid)
  if not goal or goal.authority_class!=AuthorityClass.USER_FIRST_PERSON:raise ValueError('explicit user responsibility required')
  allowed=contract.get('allowed_source_ids',[])
  for cid in allowed:
   if not self.store._conn.execute('SELECT 1 FROM runtime_sources WHERE user_id=? AND source_id=?',(u,cid)).fetchone():raise ValueError('unregistered responsibility source')
  old=self.responsibility_contract(u,gid)
  src=self.source(u,'ResponsibilityContract',gid,{'goal_id':gid,'public_requirement':goal.content['text']},at=self.clock())
  node=self.node(u,'ResponsibilityContract','contract:'+gid,{**contract,'goal_id':gid,'seq':old.get('seq',0)+1},src)
  self.product._work_update(u,gid,src,status='PENDING',initial_pending=True,work_version=self.responsibility_work_version(u,gid),recovery_rounds=0,resume_reason='NEW_EXPLICIT_RESPONSIBILITY')
  self.wake_now(u)
  return node
 def responsibility_state(self,u,gid):
  n=self._latest_record(u,'ResponsibilityState','responsibility-state:'+gid);return dict(n.content) if n else {'status':'OPEN','seq':0,'goal_id':gid}
 def _responsibility_status(self,u,gid,source,**updates):
  old=self.responsibility_state(u,gid);content={**old,**updates,'seq':old['seq']+1,'helpful':None}
  n=self.node(u,'ResponsibilityState','responsibility-state:'+gid,content,source)
  self._receipt_line({'kind':'responsibility_state','at':self.clock(),'user_id':u,**content,'node_id':n.node_id});return n
 def set_quiet(self,u,quiet):
  if type(quiet) is not bool:raise ValueError('quiet must be boolean')
  old=self._latest_record(u,'NotificationState','quiet');seq=old.content['seq']+1 if old else 1
  if old and old.content['quiet']==quiet:return old
  src=self.source(u,'UserQuietState',str(seq),{'quiet':quiet},at=self.clock());return self.node(u,'NotificationState','quiet',{'quiet':quiet,'seq':seq},src,authority=AuthorityClass.USER_FIRST_PERSON)
 def quiet(self,u):
  n=self._latest_record(u,'NotificationState','quiet');return bool(n and n.content['quiet'])
 def _quote_evidence(self,u,gid):
  spec=self.responsibility_contract(u,gid);nodes=[]
  for cid in spec.get('allowed_source_ids',[]):
   nodes += [n for n in self.graph.nodes_by_identity(u,[cid+':'+spec.get('source_key','report')]) if n.node_type=='Belief' and self.active(u,n) and self.provider_eligible(u,n)]
  n=max(nodes,key=lambda n:n.created_at,default=None)
  if not n:return None,None
  try:data=json.loads(n.content['value'])
  except (ValueError,TypeError):return n,None
  if spec.get('object_id') and data.get('object')!=spec['object_id']:return None,None
  return n,data
 def _accept_legacy_artifact(self,u,artifact,source):
  for gid in artifact.content['goal_ids']:
   spec=self.responsibility_contract(u,gid)
   if spec.get('kind')!='quote' or artifact.content['kind']!='PREPARE':continue
   evidence,v=self._quote_evidence(u,gid);reason=None
   if not v or v.get('object')!=spec.get('object_id'):reason='OBJECT_OR_SOURCE_MISSING'
   elif evidence.node_id not in artifact.content['evidence_ids']:reason='CURRENT_SOURCE_NOT_CITED'
   elif v.get('delivery')!='both_on_time' or v.get('currency')!='CNY':reason='UNSUPPORTED_QUOTE_CONTRACT'
   else:
    A,B=Decimal(str(v['A'])),Decimal(str(v['B']));fmt=lambda x:format(x,'f').rstrip('0').rstrip('.') if '.' in format(x,'f') else format(x,'f')
    expected=['对象：'+v['object'],'版本：'+v['version'],'供应商甲：'+fmt(A)+' CNY','供应商乙：'+fmt(B)+' CNY','来源时间：'+v['source_time'],'差额：'+fmt(abs(A-B))+' CNY','推荐：'+('甲' if A<B else '乙' if B<A else '两家等价'),'交货：两家均可按期']
    actual=[x.strip() for x in artifact.content['content'].replace('\\n','\n').splitlines() if x.strip()]
    if actual!=expected:reason='VISIBLE_QUOTE_CONTENT_MISMATCH'
   goal=self.graph.get_node(u,gid);due=goal.content.get('due_at')
   if due and instant(artifact.created_at)>instant(due):reason='LATE_ARTIFACT'
   if reason:
    self._receipt_line({'kind':'content_acceptance','user_id':u,'goal_id':gid,'artifact_id':artifact.node_id,'at':self.clock(),'status':'REJECTED','reason':reason})
    self.product._work_update(u,gid,source,status='NEEDS_REPAIR',resume_reason=reason,rejections=[{'index':0,'reason':reason}]);continue
   self._responsibility_status(u,gid,source,status='READY',artifact_id=artifact.node_id,evidence_id=evidence.node_id,version=v['version'],object_id=v['object'],accepted_at=self.clock())
   self.product._work_update(u,gid,source,status='CONTENT_READY')
   self._receipt_line({'kind':'content_acceptance','user_id':u,'goal_id':gid,'artifact_id':artifact.node_id,'at':self.clock(),'status':'ACCEPTED','version':v['version'],'helpful':None})
   self.advance_responsibilities(u)
 def advance_responsibilities(self,u):
  closed=[]
  for specnode in self.nodes(u,'ResponsibilityContract'):
   spec=specnode.content;gid=spec['goal_id']
   if spec.get('kind')!='quote' or self.responsibility_contract(u,gid)['seq']!=spec['seq']:continue
   state=self.responsibility_state(u,gid)
   if spec.get('required_components'):
    if self._advance_components(u,gid,spec):closed.append(gid)
    continue
   if state['status'] not in ('READY','COMPLETED'):continue
   evidence,v=self._quote_evidence(u,gid)
   if not v:continue
   if evidence.node_id!=state.get('evidence_id'):
    src=self.source(u,'ResponsibilityVersionChange',gid+':'+str(state['seq']),{'previous_state':state},at=self.clock())
    self._responsibility_status(u,gid,src,status='OPEN',reason='SOURCE_CHANGED',previous_completion=state.get('completed_at'))
    self.product._work_update(u,gid,src,status='PENDING',resume_reason='SOURCE_CHANGED',recovery_rounds=0);continue
   if state['status']=='COMPLETED':continue
   goal=self.graph.get_node(u,gid);due=goal.content.get('due_at');mode=spec.get('completion')
   mature=mode=='EXACT_VERSION' and v.get('version')==spec.get('target_version') or mode=='LATEST_AT_DEADLINE' and (v.get('final') is True or due and instant(self.clock())>=instant(due))
   if not mature:continue
   # The boundary requires an actual successful sample at this tick, not old current belief alone.
   samples=self.graph.source_events_by_ref_prefix(u,'ExternalObservation',evidence.content['source_id']+':')
   fresh=any(e.received_at==self.clock() and e.content.get('fresh') for e in samples)
   if not fresh:continue
   src=self.source(u,'ObjectiveResponsibilityCompletion',gid+':'+v['version'],{'artifact_id':state['artifact_id'],'evidence_id':evidence.node_id},at=self.clock())
   self._responsibility_status(u,gid,src,status='COMPLETED',completed_at=self.clock(),objective_complete=True);closed.append(gid)
   if spec.get('notify_required'):
    a=self.graph.get_node(u,state['artifact_id']);self._deliver(u,{'ref':a.node_id,'kind':'ARTIFACT','title':v['object']+' '+v['version']+' 已完成','body':a.content['content']},[gid],0,presence=self.presence(u),kind='PREPARE')
  return closed
 def delivery_applicable(self,u,item,goals):
  for gid in goals:
   spec=self.responsibility_contract(u,gid)
   if not spec:continue
   if not spec.get('notify_required'):return False
   if spec.get('kind')!='quote':
    art=self.graph.get_node(u,item['ref'])
    if art:
     ids=art.content.get('evidence_ids',[]);current={n['node_id'] for n in self.product.reconstruct(u,only=ids).records()}
     if not set(ids)<=current:return False
   if spec.get('kind')=='quote':
    state=self.responsibility_state(u,gid)
    if state['status']!='COMPLETED' or state.get('artifact_id')!=item['ref']:return False
  return True
 def delivery_key(self,u,item,goals):
  art=self.graph.get_node(u,item['ref']);parts=[]
  for gid in goals:
   spec=self.responsibility_contract(u,gid);parts.append((gid,spec.get('object_id'),self.responsibility_state(u,gid).get('version')))
  return nid('notification',u,parts,[] if any(p[2] for p in parts) else (art.content.get('evidence_ids',[]) if art else item['ref']))

 def responsibility_available(self,u,gid):
  spec=self.responsibility_contract(u,gid)
  return not spec.get('object_id') or spec.get('kind')!='quote' or self._quote_evidence(u,gid)[1] is not None
 def needs_preparation(self,u,gid):
  if not self.responsibility_available(u,gid):return False
  state=self.responsibility_state(u,gid);spec=self.responsibility_contract(u,gid)
  if state['status']=='COMPLETED':return False
  if spec.get('required_components'):return any(k not in state.get('components',{}) for k in ('quote','future_plan'))
  return state['status']!='READY'
 def responsibility_work_version(self,u,gid):
  spec=self.responsibility_contract(u,gid);keys={spec.get('source_key','report'),spec.get('forecast_key'),spec.get('condition_key')}-{None};ids=[]
  for cid in spec.get('allowed_source_ids',[]):
   for key in sorted(keys):
    ns=[n for n in self.graph.nodes_by_identity(u,[cid+':'+key]) if n.node_type=='Belief' and self.active(u,n) and (not spec.get('object_id') or n.content.get('object_id') in (None,spec['object_id']))]
    if ns:ids.append(max(ns,key=lambda n:n.created_at).node_id)
  missing=[k for k in ('quote','future_plan') if k not in self.responsibility_state(u,gid).get('components',{})] if spec.get('required_components') else []
  return dumps([spec.get('object_id'),ids,missing])
 def _component_sync_work(self,u,gid,source,**extra):
  old=self.product._work(u,gid);version=self.responsibility_work_version(u,gid)
  change={'work_version':version}
  if not old or old.content.get('work_version')!=version:change['recovery_rounds']=0
  self.product._work_update(u,gid,source,**change,**extra)
 def _component_record(self,u,gid,name,value,source):
  parts=dict(self.responsibility_state(u,gid).get('components',{}));parts[name]=value
  ready=all(k in parts for k in ('quote','future_plan'))
  self._responsibility_status(u,gid,source,components=parts,status='READY' if ready else 'OPEN')
 def accept_artifact(self,u,artifact,source):
  gid=artifact.content['goal_ids'][0] if artifact.content['goal_ids'] else None
  spec=self.responsibility_contract(u,gid) if gid else {}
  if not spec.get('required_components'):return self._accept_legacy_artifact(u,artifact,source)
  if artifact.content['kind']!='PREPARE':return
  name=artifact.content.get('component','quote');ev,v=self._quote_evidence(u,gid);reason=None
  if name in self.responsibility_state(u,gid).get('components',{}):
   self._receipt_line({'kind':'component_acceptance','at':self.clock(),'goal_id':gid,'artifact_id':artifact.node_id,'component':name,'status':'ALREADY_SATISFIED'});return
  if not v:reason='OBJECT_OR_SOURCE_MISSING'
  elif name=='quote' and ev.node_id not in artifact.content['evidence_ids']:reason='CURRENT_SOURCE_NOT_CITED'
  elif name=='quote':
   A,B=Decimal(str(v['A'])),Decimal(str(v['B']));fmt=lambda n:format(n,'f')
   expected=['对象：'+v['object'],'版本：'+v['version'],'供应商甲：'+fmt(A)+' CNY','供应商乙：'+fmt(B)+' CNY','来源时间：'+v['source_time'],'差额：'+fmt(abs(A-B))+' CNY','推荐：'+('甲' if A<B else '乙' if B<A else '两家等价'),'交货：两家均可按期']
   if v.get('currency')!='CNY' or v.get('delivery')!='both_on_time':reason='UNSUPPORTED_QUOTE_CONTRACT'
   elif artifact.content['content'].strip().splitlines()!=expected:reason='VISIBLE_QUOTE_CONTENT_MISMATCH'
  elif name=='future_plan':
   plan=artifact.content.get('future_plan') or {};condition=spec.get('condition_code','')
   current={n['content'].get('key'):n for n in self.product.reconstruct(u,focus=[gid]).facts if n['node_type']=='Belief' and n['content'].get('source_id') in spec['allowed_source_ids'] and n['content'].get('object_id')==spec['object_id'] and n.get('provider_eligible')}
   required=[current.get(spec['condition_key']),current.get(spec['forecast_key'])]
   if any(n is None or n['node_id'] not in artifact.content['evidence_ids'] for n in required):reason='PLAN_CURRENT_EVIDENCE_NOT_CITED'
   elif instant(artifact.created_at)>=instant(spec['forecast_at']):reason='PLAN_NOT_BEFORE_TARGET'
   elif plan_time_error(plan.get('next_check_at'),spec['forecast_at']):reason=plan_time_error(plan.get('next_check_at'),spec['forecast_at'])
   elif not condition or condition not in plan.get('basis','') or spec['forecast_key'] not in plan.get('pending',''):reason='PLAN_BASIS_OR_PENDING_MISSING'
   elif plan.get('judgment') not in ('ready','delayed','UNCERTAIN'):reason='PLAN_JUDGMENT_INVALID'
   elif plan.get('ready_action') not in ('核对供应商排期后确认交付','人工核实后再确认交付') or plan.get('delayed_action') not in ('联系备选供应商并重新核对交期','通知负责人延期并联系备选供应商'):reason='PLAN_BRANCH_ACTION_MISSING'
  else:reason='UNKNOWN_COMPONENT'
  due=self.graph.get_node(u,gid).content.get('due_at')
  if due and instant(artifact.created_at)>instant(due):reason='LATE_COMPONENT'
  self._receipt_line({'kind':'component_acceptance','at':self.clock(),'goal_id':gid,'artifact_id':artifact.node_id,'component':name,'status':'REJECTED' if reason else 'ACCEPTED','reason':reason,'object_id':spec['object_id'],'origin':'HOST_RULE'})
  if reason:
   self._component_sync_work(u,gid,source,status='NEEDS_REPAIR',resume_reason=reason,rejections=[{'component':name,'reason':reason,'required_fields':(['basis containing '+spec.get('condition_code',''),'pending naming '+spec.get('forecast_key','')] if reason=='PLAN_BASIS_OR_PENDING_MISSING' else ['next_check_at='+spec.get('forecast_at','')] if reason=='PLAN_TIME_MISMATCH' else [spec.get('condition_key',''),spec.get('forecast_key','')] if reason=='PLAN_CURRENT_EVIDENCE_NOT_CITED' else [])}]);return
  self._component_record(u,gid,name,{'artifact_id':artifact.node_id,'evidence_id':ev.node_id,'object_id':v['object'],'version':v['version'],'observed_at':ev.content['observed_at'],'accepted_at':self.clock(),'origin':'MODEL_ARTIFACT_HOST_CHECK',**({'evidence_ids':[n['node_id'] for n in required]} if name=='future_plan' else {})},source)
  self._component_sync_work(u,gid,source,status='PENDING' if self.needs_preparation(u,gid) else 'CONTENT_READY',resume_reason='MISSING_COMPONENT' if self.needs_preparation(u,gid) else 'WAIT_FOR_VERIFICATION')
  self.advance_responsibilities(u)
 def _advance_components(self,u,gid,spec):
  state=self.responsibility_state(u,gid);parts=dict(state.get('components',{}));ev,v=self._quote_evidence(u,gid)
  if not v:return False # Different object or missing read never invalidates the old object's completion.
  if 'quote' in parts and parts['quote']['evidence_id']!=ev.node_id:
   parts.pop('quote');parts.pop('deadline_verification',None)
   src=self.source(u,'ResponsibilityVersionChange',gid+':'+ev.node_id,{'object_id':v['object'],'version':v['version']},at=self.clock())
   self._responsibility_status(u,gid,src,status='OPEN',components=parts,reason='SAME_OBJECT_QUOTE_CHANGED')
   self._component_sync_work(u,gid,src,status='PENDING',resume_reason='SAME_OBJECT_QUOTE_CHANGED');state=self.responsibility_state(u,gid)
  if state['status']=='COMPLETED':return False
  samples=self.graph.source_events_by_ref_prefix(u,'ExternalObservation',ev.content['source_id']+':')
  samples=[e for e in samples if e.content.get('fresh') and e.content.get('object_id')==spec['object_id']]
  targets=[e for e in samples if instant(e.received_at)==instant(spec['forecast_at'])]
  if 'target_observation' not in parts and targets:
   sample=targets[-1];field=next((f for f in sample.content['data']['facts'] if f['key']==spec['forecast_key']),None)
   if field and field['value'] in spec.get('forecast_values',['ready','delayed']):
    self._component_record(u,gid,'target_observation',{'source_event_id':sample.source_event_id,'object_id':spec['object_id'],'field':spec['forecast_key'],'value':field['value'],'at':sample.received_at,'origin':'HOST_OBSERVATION'},sample);parts=self.responsibility_state(u,gid)['components']
  due=self.graph.get_node(u,gid).content.get('due_at')
  current=next((e for e in samples if e.received_at==self.clock()),None)
  if not current or not due or instant(self.clock())<instant(due) or not all(k in parts for k in ('quote','future_plan','target_observation')):return False
  self._component_record(u,gid,'deadline_verification',{'source_event_id':current.source_event_id,'evidence_id':ev.node_id,'object_id':v['object'],'version':v['version'],'at':self.clock(),'origin':'HOST_RULE'},current)
  self._responsibility_status(u,gid,current,status='COMPLETED',completed_at=self.clock(),objective_complete=True,artifact_id=parts['quote']['artifact_id'],version=v['version'],object_id=v['object'])
  if spec.get('notify_required'):
   quote=self.graph.get_node(u,parts['quote']['artifact_id']);plan=self.graph.get_node(u,parts['future_plan']['artifact_id'])
   self._deliver(u,{'ref':quote.node_id,'kind':'ARTIFACT','title':v['object']+' 完整责任已完成','body':quote.content['content']+'\n\n'+plan.content['content']+'\n\n实际目标结果：'+str(parts['target_observation']['value'])+'；观察时间：'+parts['target_observation']['at']+'；到期核验时间：'+self.clock()},[gid],0,presence=self.presence(u),kind='PREPARE')
  return True
