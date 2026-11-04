"""SEC-EVO-01: bounded declarative risk hypotheses, prequential evidence, no executable model code."""
import json,math,copy
FEATURES=('f1','f2','f3')
def features(task):
 c=task.get('condition')
 if isinstance(c,str):
  try:c=json.loads(c)
  except ValueError:c={}
 return {f:c[f] for f in FEATURES if isinstance(c,dict) and type(c.get(f)) is int and c[f] in (0,1)}
def validate_tree(t,depth=0):
 if t=={'leaf':True}:return 1
 if not isinstance(t,dict) or set(t)!= {'feature','equals','yes','no'} or depth>=2 or t['feature'] not in FEATURES or type(t['equals']) is not int or t['equals'] not in (0,1):raise ValueError('INVALID_DECLARATIVE_TREE')
 return validate_tree(t['yes'],depth+1)+validate_tree(t['no'],depth+1)
def leaf(t,x,path=''):
 if 'leaf' in t:return path or 'root'
 if t['feature'] not in x:return 'MISSING_COMMON_FALLBACK'
 branch='yes' if x[t['feature']]==t['equals'] else 'no'
 return leaf(t[branch],x,path+branch+'/')
def canonical(t):return json.dumps(t,sort_keys=True,separators=(',',':'))
def loss(p,y):return -math.log(max(1e-12,p if y else 1-p))
class Pool:
 def __init__(self,initial,mode):
  self.mode=mode;self.hypotheses=[];self.history=[];self.forecasts={};self.events=[];self.proposals=[];self.seen=set();self.event_tasks={}
  self.add(initial,-1,'INITIAL',initial=True)
  if not self.hypotheses:raise ValueError('No valid initial hypotheses; no invented replacement')
 def weights(self):
  hs=[h for h in self.hypotheses if h['status']=='ACTIVE'];m=max(h['logw'] for h in hs);z=sum(math.exp(h['logw']-m) for h in hs)
  return {h['id']:math.exp(h['logw']-m)/z for h in hs}
 def probability(self,h,x):
  k=leaf(h['spec']['tree'],x);a,b=h['counts'].get(k,[1,1]);return a/(a+b)
 def risk(self,task):
  x=features(task)
  if len(x)<3:
   # Public future conditions are unknown: same unconditional training prior for every learner.
   ys=[e['y'] for e in self.history] if self.mode!='STATIC' else []
   return (1+sum(ys))/(2+len(ys))
  if self.mode=='TABULAR':
   ys=[e['y'] for e in self.history if e['features']==x];return (1+sum(ys))/(2+len(ys))
  hs=[h for h in self.hypotheses if h['status']=='ACTIVE'];ws=self.weights()
  if self.mode in ('SINGLE','STATIC'):
   h=hs[0] if self.mode=='STATIC' else max(hs,key=lambda h:(ws[h['id']],-h['complexity'],h['id']))
   return self.probability(h,x)
  return sum(ws[h['id']]*self.probability(h,x) for h in hs)
 def add(self,specs,day,receipt,initial=False):
  limit=8 if initial else 4
  if not initial:
   for h in self.hypotheses:
    if h['status']=='PROBATION':h['status']='ARCHIVED';self.events.append({'day':day,'type':'ARCHIVED','id':h['id'],'reason':'CAPACITY_UNVERIFIED' if len(h['scores'])<4 else 'NOT_BETTER_ON_MATCHED_EVENTS'})
  for i,s in enumerate(specs[:limit]):
   try:
    if not isinstance(s,dict) or set(s)!= {'tree','parents','operation','reason','target','horizon_days','source'}:raise ValueError('SPEC_FIELDS')
    complexity=validate_tree(s['tree'])
    if s['target']!='SUPPLIER_DELAYED' or s['horizon_days']!=3 or s['source']!='resource_simulation':raise ValueError('SCOPE')
    if not isinstance(s['parents'],list) or len(s['parents'])>2:raise ValueError('PARENTS')
    if s['operation'] not in ('INITIAL','FRESH','MUTATE','REFINE','CROSSOVER'):raise ValueError('OPERATION')
    ids={h['id'] for h in self.hypotheses}
    if any(p not in ids for p in s['parents']):raise ValueError('PARENT_NOT_AVAILABLE')
    if s['operation'] in ('MUTATE','REFINE') and len(s['parents'])!=1:raise ValueError('ONE_PARENT_REQUIRED')
    if s['operation']=='CROSSOVER' and len(s['parents'])!=2:raise ValueError('TWO_PARENTS_REQUIRED')
    if s['operation'] in ('INITIAL','FRESH') and s['parents']:raise ValueError('INDEPENDENT_PARENTS_FORBIDDEN')
    if any(canonical(h['spec']['tree'])==canonical(s['tree']) for h in self.hypotheses):raise ValueError('DUPLICATE_STRUCTURE')
    if s['parents']:
     parents=[next(h['spec']['tree'] for h in self.hypotheses if h['id']==p) for p in s['parents']]
     def subtrees(t):return [t]+([] if 'leaf' in t else subtrees(t['yes'])+subtrees(t['no']))
     def changes(t,donor=None):
      opts=[]
      if donor is not None:opts+=copy.deepcopy(subtrees(donor))
      elif 'leaf' in t:
       opts += [{'feature':f,'equals':v,'yes':{'leaf':True},'no':{'leaf':True}} for f in FEATURES for v in (0,1)]
      else:
       opts += [{'leaf':True},t['yes'],t['no']]
       opts += [{**t,'feature':f,'equals':v} for f in FEATURES for v in (0,1)]
      if 'leaf' not in t:
       for side in ('yes','no'):
        opts += [{**t,side:c} for c in changes(t[side],donor)]
      return opts
     allowed=changes(parents[0],parents[1] if s['operation']=='CROSSOVER' else None)
     if canonical(s['tree']) not in {canonical(t) for t in allowed}:raise ValueError('NOT_ONE_BOUNDED_EDIT_OR_COMPATIBLE_CROSSOVER')
    h={'id':'H%03d'%(len(self.hypotheses)+1),'version':1,'spec':copy.deepcopy(s),'born_day':day,'training_cutoff_count':len(self.history),'receipt':receipt,'counts':{},'scores':{},'status':'ACTIVE' if initial else 'PROBATION','logw':0.,'complexity':complexity}
    for e in self.history:self.train(h,e)
    self.hypotheses.append(h);self.events.append({'day':day,'type':'BIRTH','id':h['id'],'parents':s['parents'],'training_count':len(self.history),'validation_count':0,'receipt':receipt})
   except (ValueError,TypeError) as exc:self.events.append({'day':day,'type':'REJECT','index':i,'error':str(exc),'receipt':receipt,'candidate':s})
 def train(self,h,e):
  k=leaf(h['spec']['tree'],e['features']);ab=h['counts'].setdefault(k,[1,1]);ab[0 if e['y'] else 1]+=1
 def register(self,t,day):
  oid=t['object_id']
  if oid in self.forecasts:return
  x=features(t);ws=self.weights();preds={h['id']:{'p':self.probability(h,x),'parameter_version':sum(sum(v)-2 for v in h['counts'].values()),'weight':ws.get(h['id'],0),'status':h['status']} for h in self.hypotheses if h['status'] in ('ACTIVE','PROBATION') and h['born_day']<=day}
  self.event_tasks[oid]=copy.deepcopy(t)
  self.forecasts[oid]={'object_id':oid,'issued_day':day,'target_day':t['target_day'],'features':x,'system_p':self.risk(t),'candidates':preds}
 def observe(self,results,day):
  for t in results:
   oid=t['object_id']
   if oid in self.seen:continue
   if t['target_day']>day:raise ValueError('FUTURE_RESULT')
   self.seen.add(oid);e={'object_id':oid,'features':features(t),'y':int(t['supplier_result']=='delayed'),'observed_day':day,'target_day':t['target_day']};self.history.append(e)
   f=self.forecasts.get(oid)
   if f and f['issued_day']<t['target_day']:
    p=f['system_p'];e.update(p=p,log_loss=loss(p,e['y']),brier=(p-e['y'])**2)
    for h in self.hypotheses:
     item=f['candidates'].get(h['id'])
     if item:
      l=loss(item['p'],e['y']);h['scores'][oid]={'p':item['p'],'y':e['y'],'loss':l,'issued_day':f['issued_day'],'observed_day':day}
      if h['status']=='ACTIVE' and self.mode!='STATIC':h['logw']-=l
   if self.mode!='STATIC':
    for h in self.hypotheses:
     if h['status']!='ARCHIVED':self.train(h,e)
   self.promote(day)
 def promote(self,day):
  for child in [h for h in self.hypotheses if h['status']=='PROBATION' and len(h['scores'])>=4]:
   comparisons=[]
   for old in [h for h in self.hypotheses if h['status']=='ACTIVE']:
    common=sorted(set(child['scores'])&set(old['scores']))
    if len(common)<4:continue
    cl=sum(child['scores'][x]['loss'] for x in common)/len(common);ol=sum(old['scores'][x]['loss'] for x in common)/len(common)
    if cl<ol-1e-12 or abs(cl-ol)<=1e-12 and child['complexity']<old['complexity']:comparisons.append((ol-cl,old,common,cl,ol))
   if not comparisons:continue
   _,old,common,cl,ol=max(comparisons,key=lambda x:(x[0],x[1]['id']));old['status']='ARCHIVED'
   remaining=[h['logw'] for h in self.hypotheses if h['status']=='ACTIVE'];m=max(remaining) if remaining else 0.;z=m+math.log(sum(math.exp(v-m) for v in remaining)) if remaining else 0.
   child['logw']=z+math.log(.05/.95);child['status']='ACTIVE'
   self.events.append({'day':day,'type':'PROMOTION','id':child['id'],'replaces':old['id'],'shared_events':common,'child_loss':cl,'old_loss':ol,'initial_weight':.05})
 def public_state(self):
  ws=self.weights();return {'mode':self.mode,'matured':len(self.history),'risk_target':'SUPPLIER_DELAYED','active':[{'id':h['id'],'tree':h['spec']['tree'],'weight':ws[h['id']],'validation_n':len(h['scores']),'born_day':h['born_day']} for h in self.hypotheses if h['status']=='ACTIVE'],'probation':[h['id'] for h in self.hypotheses if h['status']=='PROBATION']}
 def dump(self):return {'mode':self.mode,'hypotheses':self.hypotheses,'history':self.history,'forecasts':self.forecasts,'events':self.events,'proposals':self.proposals}
