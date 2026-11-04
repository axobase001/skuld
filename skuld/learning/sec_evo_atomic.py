"""CP17 exact atomic edits and finite-domain partition identity. No world labels in grammar/search."""
import copy,itertools,json,math
from .sec_evo import Pool,FEATURES,validate_tree,leaf,canonical
DOMAIN=list(itertools.product((0,1),repeat=3))
def signature(tree):
 validate_tree(tree);ids={};out=[]
 for bits in DOMAIN:
  k=leaf(tree,dict(zip(FEATURES,bits)))
  if k not in ids:ids[k]=len(ids)
  out.append(ids[k])
 return ''.join(map(str,out))
def enumerate_space():
 def enum(depth):
  ts=[{'leaf':True}]
  if depth:
   children=enum(depth-1)
   ts += [{'feature':f,'equals':v,'yes':a,'no':b} for f in FEATURES for v in (0,1) for a in children for b in children]
  return ts
 all_trees=enum(2);reps={}
 for t in sorted(all_trees,key=lambda x:(validate_tree(x),canonical(x))):reps.setdefault(signature(t),t)
 return {'syntax_count':len(all_trees),'domain':DOMAIN,'representatives':[reps[k] for k in sorted(reps)],'signatures':sorted(reps)}
def paths(t,path=None):
 path=[] if path is None else path
 return [{'path':path,'type':'leaf' if 'leaf' in t else 'test'}]+([] if 'leaf' in t else paths(t['yes'],path+['yes'])+paths(t['no'],path+['no']))
def node(t,path):
 if not isinstance(path,list) or len(path)>2 or any(s not in ('yes','no') for s in path):raise ValueError('INVALID_PATH')
 for side in path:
  if 'leaf' in t:raise ValueError('PATH_THROUGH_LEAF')
  t=t[side]
 return t
OP_FIELDS={
 'SPLIT_LEAF':{'parent_id','parent_version','node_path','feature','equals'},
 'REPLACE_TEST':{'parent_id','parent_version','node_path','feature','equals'},
 'PRUNE_TO_LEAF':{'parent_id','parent_version','node_path'},
 'PROMOTE_CHILD':{'parent_id','parent_version','node_path','child_side'},
 'COPY_SUBTREE':{'recipient_parent_id','recipient_version','recipient_path','donor_parent_id','donor_version','donor_path'}}
def compile_edit(op,parents):
 if not isinstance(op,dict) or op.get('operation') not in OP_FIELDS:raise ValueError('UNKNOWN_OPERATION')
 kind=op['operation']
 if set(op)!=(OP_FIELDS[kind]|{'operation','reason'}):raise ValueError('OPERATION_FIELDS_OR_MULTIPLE_EDITS')
 lookup={p['id']:p for p in parents}
 def parent(id_,version):
  if id_ not in lookup:raise ValueError('PARENT_NOT_DISPLAYED')
  p=lookup[id_]
  if type(version) is not int or version!=p['version']:raise ValueError('PARENT_VERSION')
  return p
 if kind=='COPY_SUBTREE':
  p=parent(op['recipient_parent_id'],op['recipient_version']);q=parent(op['donor_parent_id'],op['donor_version']);path=op['recipient_path'];replacement=copy.deepcopy(node(q['tree'],op['donor_path']));parent_ids=[p['id'],q['id']]
 else:
  p=parent(op['parent_id'],op['parent_version']);path=op['node_path'];parent_ids=[p['id']]
 original=node(p['tree'],path)
 if kind in ('SPLIT_LEAF','REPLACE_TEST'):
  if op['feature'] not in FEATURES or type(op['equals']) is not int or op['equals'] not in (0,1):raise ValueError('FEATURE_OUT_OF_SCOPE')
  if kind=='SPLIT_LEAF':
   if original!={'leaf':True}:raise ValueError('SPLIT_REQUIRES_LEAF')
   replacement={'feature':op['feature'],'equals':op['equals'],'yes':{'leaf':True},'no':{'leaf':True}}
  else:
   if 'leaf' in original:raise ValueError('REPLACE_REQUIRES_TEST')
   replacement={**copy.deepcopy(original),'feature':op['feature'],'equals':op['equals']}
 elif kind=='PRUNE_TO_LEAF':replacement={'leaf':True}
 elif kind=='PROMOTE_CHILD':
  if 'leaf' in original or op['child_side'] not in ('yes','no'):raise ValueError('INVALID_CHILD')
  replacement=copy.deepcopy(original[op['child_side']])
 t=copy.deepcopy(p['tree'])
 if not path:t=replacement
 else:node(t,path[:-1])[path[-1]]=replacement
 try:validate_tree(t)
 except ValueError as e:raise ValueError('RESULT_DEPTH_OR_FEATURE_SCOPE') from e
 if signature(t)==signature(p['tree']):raise ValueError('NO_CHANGE')
 return {'tree':t,'parents':parent_ids,'operation':'CROSSOVER' if kind=='COPY_SUBTREE' else 'MUTATE','reason':op['reason'],'target':'SUPPLIER_DELAYED','horizon_days':3,'source':'resource_simulation'}
def parent_view(pool):
 weights=pool.weights();hs=sorted([h for h in pool.hypotheses if h['status']=='ACTIVE'],key=lambda h:-weights[h['id']])[:4]
 return [{'id':h['id'],'version':h['version'],'tree':h['spec']['tree'],'paths':paths(h['spec']['tree']),'weight':weights[h['id']],'validation_n':len(h['scores'])} for h in hs]
def all_atomic(parents):
 for p in parents:
  for n in p['paths']:
   common={'parent_id':p['id'],'parent_version':p['version'],'node_path':n['path'],'reason':'syntax enumeration only'}
   if n['type']=='leaf':
    for f in FEATURES:
     for v in (0,1):yield {**common,'operation':'SPLIT_LEAF','feature':f,'equals':v}
   else:
    for f in FEATURES:
     for v in (0,1):yield {**common,'operation':'REPLACE_TEST','feature':f,'equals':v}
    yield {**common,'operation':'PRUNE_TO_LEAF'}
    for side in ('yes','no'):yield {**common,'operation':'PROMOTE_CHILD','child_side':side}
   for q in parents:
    for donor in q['paths']:yield {'operation':'COPY_SUBTREE','recipient_parent_id':p['id'],'recipient_version':p['version'],'recipient_path':n['path'],'donor_parent_id':q['id'],'donor_version':q['version'],'donor_path':donor['path'],'reason':'syntax enumeration only'}
def search_context(pool,evo=False):
 seen={signature(h['spec']['tree']) for h in pool.hypotheses};all_sigs=set(enumerate_space()['signatures'])
 ctx={'tried_partitions':[{'signature':signature(h['spec']['tree']),'id':h['id'],'status':h['status']} for h in pool.hypotheses],'last_rejections':pool.last_rejections,'domain_order':[dict(zip(FEATURES,x)) for x in DOMAIN],'search_status':'SPACE_EXHAUSTED' if seen==all_sigs else 'AVAILABLE'}
 if evo:
  pv=parent_view(pool);unseen=set()
  for op in all_atomic(pv):
   try:s=signature(compile_edit(op,pv)['tree'])
   except ValueError:continue
   if s not in seen:unseen.add(s)
  ctx['parents']=pv;ctx['legal_unseen_neighbor_count']=len(unseen)
  if not unseen and ctx['search_status']!='SPACE_EXHAUSTED':ctx['search_status']='LOCAL_NEIGHBORHOOD_EXHAUSTED'
 return ctx
class Pool17(Pool):
 def __init__(self,initial,mode):
  self.links=[];self.last_rejections=[];self.compilations=[];super().__init__(initial,mode)
 def probability(self,h,x):
  if len(x)!=3:return (1+sum(e['y'] for e in self.history))/(2+len(self.history)) if self.mode!='STATIC' else .5
  return super().probability(h,x)
 def add(self,specs,day,receipt,initial=False):
  self.last_rejections=[]
  if not initial:
   for h in self.hypotheses:
    if h['status']=='PROBATION':h['status']='ARCHIVED';self.events.append({'day':day,'type':'ARCHIVED','id':h['id'],'reason':'CAPACITY_UNVERIFIED' if len(h['scores'])<4 else 'NOT_BETTER_ON_MATCHED_EVENTS'})
  limit=len(specs) if initial else 4
  for i,s in enumerate(specs[:limit]):
   try:
    if not isinstance(s,dict) or set(s)!={'tree','parents','operation','reason','target','horizon_days','source'}:raise ValueError('SPEC_FIELDS')
    complexity=validate_tree(s['tree'])
    if s['target']!='SUPPLIER_DELAYED' or s['horizon_days']!=3 or s['source']!='resource_simulation':raise ValueError('SCOPE')
    sig=signature(s['tree']);old=next((h for h in self.hypotheses if h['partition']==sig),None)
    if old:
     link={'day':day,'receipt':receipt,'index':i,'reason':'NO_NEW_PARTITION','existing_id':old['id'],'parents':s['parents'],'partition':sig,'original_spec':s};self.links.append(link);raise ValueError('NO_NEW_PARTITION')
    h={'id':'H%03d'%(len(self.hypotheses)+1),'version':1,'spec':copy.deepcopy(s),'partition':sig,'born_day':day,'training_cutoff_count':len(self.history),'receipt':receipt,'counts':{},'scores':{},'status':'ACTIVE' if initial else 'PROBATION','logw':0.,'complexity':complexity}
    for e in self.history:self.train(h,e)
    self.hypotheses.append(h);self.events.append({'day':day,'type':'BIRTH','id':h['id'],'parents':s['parents'],'training_count':len(self.history),'validation_count':0,'receipt':receipt,'partition':sig})
   except (ValueError,TypeError,KeyError) as exc:
    reject={'day':day,'type':'REJECT','index':i,'error':str(exc),'receipt':receipt,'candidate':s};self.events.append(reject);self.last_rejections.append({'index':i,'reason':str(exc)})
 def ingest(self,result,day):
  compiled=[];fail=[]
  for i,item in enumerate(result.get('items',[])[:4]):
   try:
    if result['mode']!='EVO-MIX' and (not isinstance(item,dict) or item.get('parents')!=[] or item.get('operation')!='FRESH'):raise ValueError('FRESH_REQUIRES_INDEPENDENT_STRUCTURE')
    spec=compile_edit(item,result['input']['parents']) if result['mode']=='EVO-MIX' else item;validate_tree(spec['tree']);compiled.append((i,spec))
   except (ValueError,KeyError,TypeError) as exc:fail.append({'index':i,'reason':str(exc)})
  self.add([s for i,s in compiled],day,result['receipt'])
  for e in self.last_rejections:e['index']=compiled[e['index']][0]
  self.last_rejections=fail+self.last_rejections
  if result.get('failure'):self.last_rejections.append({'index':None,'reason':'SERVICE_OR_PARSE_FAILURE:'+result['failure']})
  self.compilations.append({'day':day,'receipt':result['receipt'],'mode':result['mode'],'raw_items':result.get('items',[]),'compiled':[{'original_index':i,'spec':s,'partition':signature(s['tree'])} for i,s in compiled],'rejections':copy.deepcopy(self.last_rejections)})
  self.proposals.append({'day':day,'matured':len(self.history),'receipt':result['receipt']})
 def public_state(self):
  state=super().public_state()
  for row in state['active']:
   h=next(h for h in self.hypotheses if h['id']==row['id']);row.update(structure_version=h['version'],partition=h['partition'],parameter_version=sum(sum(v)-2 for v in h['counts'].values()))
  return state
 def dump(self):return {**super().dump(),'links':self.links,'last_rejections':self.last_rejections,'compilations':self.compilations}
