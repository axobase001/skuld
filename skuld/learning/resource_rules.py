"""CP20 declared supplier rules. Facts train parameters; only accepted proposals change trees."""
import copy,json,math,itertools
from pathlib import Path
from .sec_evo import features,leaf,validate_tree,loss,FEATURES
from .sec_evo_atomic import Pool17,signature
from .resource_calibration import POOL_FIELDS
FIELDS=('role','predictor_kind','tree','model_version','statistics_version','versions','observed','predictions','settlements','opportunities','first_init','first_init_consumed','inbox')
class State:
 def __init__(self,role,data=None):
  if data is None:
   self.role=role;self.predictor_kind='TABULAR' if role=='Tabular' else 'HOST_FIXED_MIX';self.tree=None;self.model_version=0;self.statistics_version=0
   self.versions=[{'model_version':0,'kind':self.predictor_kind,'tree':None,'parent':None,'effective_day':0,'change':'INITIAL','origin_receipt':None,'training_count':0}]
   self.observed={};self.predictions={};self.settlements={};self.opportunities={};self.first_init=None;self.first_init_consumed=False;self.inbox=None
   self.host=Pool17(json.loads(Path(__file__).with_name('resource_initial.json').read_text()),'FIXED-MIX')
  else:
   if data['schema_version']!=1 or data['role']!=role:raise ValueError('RULE_STATE_PROTOCOL_OR_ROLE')
   for k in FIELDS:setattr(self,k,copy.deepcopy(data[k]))
   self.host=Pool17.__new__(Pool17)
   for k in POOL_FIELDS:setattr(self.host,k,copy.deepcopy(data['host'][k]))
   self.host.seen=set(data['host']['seen'])
 def snapshot(self):return {'schema_version':1,**{k:copy.deepcopy(getattr(self,k)) for k in FIELDS},'host':{**{k:copy.deepcopy(getattr(self.host,k)) for k in POOL_FIELDS},'seen':sorted(self.host.seen)}}
 def counts(self,tree):
  out={}
  for e in self.observed.values():
   k=leaf(tree,e['features']);z=out.setdefault(k,{'ready':0,'delayed':0});z['delayed' if e['y'] else 'ready']+=1
  return out
 def probability(self,t,tree=None,kind=None):
  kind=self.predictor_kind if kind is None else kind;tree=self.tree if kind=='TREE' and tree is None else tree;x=features(t)
  events=list(self.observed.values())
  if len(x)<3:return (1+sum(e['y'] for e in events))/(2+len(events))
  if kind=='HOST_FIXED_MIX':return self.host.risk(t)
  if kind=='TABULAR':ys=[e['y'] for e in events if e['features']==x]
  else:ys=[e['y'] for e in events if leaf(tree,e['features'])==leaf(tree,x)]
  return (1+sum(ys))/(2+len(ys))
 def observe(self,results,day):
  added=[]
  for t in results:
   oid=t['object_id']
   if oid in self.observed:continue
   if t['target_day']>day:raise ValueError('FUTURE_SUPPLIER_LABEL')
   e={'object_id':oid,'features':features(t),'target_day':t['target_day'],'observed_day':day,'y':int(t['supplier_result']=='delayed'),'cohort':t['cohort']}
   self.observed[oid]=e;self.statistics_version+=1;added.append(e)
  self.host.observe(results,day)
  return added
 def apply(self,proposal,receipt,day,user):
  p=proposal;allowed={x['settlement_id'] for x in self.settlements.values()}
  if p['parent_model_version']!=self.model_version:raise ValueError('PARENT_MODEL_VERSION')
  refs=p['evidence_settlement_ids']
  if len(refs)>4 or any(x not in allowed for x in refs):raise ValueError('EVIDENCE_SETTLEMENT_SCOPE')
  if p['operation']=='KEEP':
   if p['tree'] is not None:raise ValueError('KEEP_REQUIRES_NULL_TREE')
   return 'KEEP'
  if not refs:raise ValueError('REPLACE_REQUIRES_SETTLED_EVIDENCE')
  validate_tree(p['tree']);sig=signature(p['tree'])
  if self.tree is not None and signature(self.tree)==sig:return 'NO_STRUCTURAL_CHANGE'
  change='FIRST_ADOPTION' if self.tree is None else 'RETURN_TO_OLD_STRUCTURE' if any(v['tree'] is not None and signature(v['tree'])==sig for v in self.versions) else 'REPLACEMENT'
  parent=self.model_version;self.model_version+=1;self.tree=copy.deepcopy(p['tree']);self.predictor_kind='TREE'
  self.versions.append({'model_version':self.model_version,'kind':'TREE','tree':copy.deepcopy(self.tree),'parent':parent,'effective_day':day,'change':change,'origin_receipt':receipt,'training_count':self.statistics_version,'training_leaf_counts':self.counts(self.tree),'evidence_settlement_ids':refs,'reason':p['reason']})
  if self.first_init is None:self.first_init={'event_id':'first_init:'+receipt,'origin_user':user,'origin_receipt':receipt,'effective_day':day,'tree':copy.deepcopy(self.tree),'origin_model_version':self.model_version}
  return change
 def consume(self,day):
  if self.role!='FrozenFirst' or self.first_init_consumed or self.inbox is None:return False
  e=self.inbox
  if e['effective_day']!=day:raise ValueError('FIRST_INIT_EFFECTIVE_DAY')
  self.tree=copy.deepcopy(e['tree']);validate_tree(self.tree);self.predictor_kind='TREE';self.model_version=1;self.first_init=copy.deepcopy(e);self.first_init_consumed=True
  self.versions.append({'model_version':1,'kind':'TREE','tree':copy.deepcopy(self.tree),'parent':0,'effective_day':day,'change':'SHARED_FIRST_INITIALIZATION','origin_receipt':e['origin_receipt'],'origin_user':e['origin_user'],'training_count':self.statistics_version,'training_leaf_counts':self.counts(self.tree)})
  return True

def load_state(rt,u,cid):
 rt.store._conn.execute('CREATE TABLE IF NOT EXISTS resource_rule_state (user_id TEXT NOT NULL,channel_id TEXT NOT NULL,data_json TEXT NOT NULL,PRIMARY KEY(user_id,channel_id))')
 row=rt.store._conn.execute('SELECT data_json FROM resource_rule_state WHERE user_id=? AND channel_id=?',(u,cid)).fetchone()
 return State(rt.config['resource_rule_role'],json.loads(row[0]) if row else None)
def save_state(rt,u,cid,s):
 rt.store._conn.execute('INSERT INTO resource_rule_state VALUES (?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET data_json=excluded.data_json',(u,cid,json.dumps(s.snapshot())))
def offer_initialization(rt,u,cid,event):
 with rt.store.transaction():
  s=load_state(rt,u,cid)
  if s.role!='FrozenFirst' or event['origin_user']!=rt.config['revision_origin_user']:raise ValueError('FIRST_INIT_ORIGIN')
  if s.inbox is not None and s.inbox['event_id']!=event['event_id']:raise ValueError('FIRST_INIT_ALREADY_BOUND')
  if s.first_init_consumed:return
  s.inbox=copy.deepcopy(event);save_state(rt,u,cid,s)

def feedback_view(s,day):
 current=copy.deepcopy(s.versions[-1]);current['leaf_counts']=s.counts(s.tree) if s.tree is not None else None
 if s.tree is None:
  weights=s.host.weights();current['host_pool']=[{'id':h['id'],'tree':h['spec']['tree'],'beta_delayed_ready':h['counts'],'weight':weights[h['id']]} for h in s.host.hypotheses if h['status']=='ACTIVE']
 cells=[];groups={};prefix=[];settled=[]
 for bits in itertools.product((0,1),repeat=3):
  x=dict(zip(FEATURES,bits));ys=[e['y'] for e in s.observed.values() if e['features']==x];cells.append([*bits,len(ys)-sum(ys),sum(ys)])
 for e in s.observed.values():
  oid=e['object_id'];prefix.append([oid,*[e['features'][f] for f in FEATURES],e['target_day'],e['observed_day'],e['y']]);f=s.predictions.get(oid);z=s.settlements.get(oid)
  if f is None or z is None:continue
  settled.append([z['settlement_id'],f['prediction_id'],oid,f['issued_day'],f['model_version'],f['p_issued'],e['y'],z['brier'],z['log_loss']])
  k=tuple(e['features'][f] for f in FEATURES)+(f['model_version'],);g=groups.setdefault(k,[]);g.append(z)
 history=[]
 for v in s.versions:
  zs=[z for oid,z in s.settlements.items() if s.predictions[oid]['model_version']==v['model_version']]
  history.append({**v,'prospective_n':len(zs),'prospective_brier':sum(z['brier'] for z in zs)/len(zs) if zs else None,'prospective_log_loss':sum(z['log_loss'] for z in zs)/len(zs) if zs else None})
 # Numeric precision is retained for forecasts; IDs remain exact and traceable.
 return {'stage':'MODEL_REVISE','cutoff_day':day,'matured_main':sum(e['cohort']=='main' for e in s.observed.values()),'statistics_version':s.statistics_version,'predictor':current,
 'condition_counts':{'columns':['f1','f2','f3','ready','delayed'],'rows':cells},
 'supplier_prefix':{'columns':['object_id','f1','f2','f3','target_day','observed_day','y_delayed'],'rows':prefix},
 'settled_predictions':{'columns':['settlement_id','prediction_id','object_id','issued_day','model_version','p_issued','y','brier','log_loss'],'rows':settled},
 'prequential_errors':{'columns':['f1','f2','f3','issued_model_version','n','mean_brier','mean_log_loss'],'rows':[[*k,len(zs),sum(z['brier'] for z in zs)/len(zs),sum(z['log_loss'] for z in zs)/len(zs)] for k,zs in sorted(groups.items())]},
 'revision_history':history,'prior_opportunities':[{'matured_main':k,'status':v['status'],'proposal':v.get('proposal'),'error':v.get('error')} for k,v in s.opportunities.items() if v['status']!='prepared'],
 'contract':{'task':'Maintain an executable conditional SUPPLIER_DELAYED grouping. KEEP or propose ONE complete tree. Facts compute Beta(1,1) probabilities. Evaluate issued errors against their original versions; historical refit is training, not past success. No actions, costs or probabilities in output.',
 'tree_grammar':'leaf {"leaf":true}; node {"feature":"f1|f2|f3","equals":0|1,"yes":tree,"no":tree}; max depth 2, max 4 leaves. Fields are literal JSON keys; no arbitrary code.',
 'parent_model_version':s.model_version,'evidence':'REPLACE must cite 1 to 4 exact settlement_id values displayed in settled_predictions; KEEP may cite none. KEEP tree=null. No extra fields.',
 'adoption':'Legal tree directly replaces current grouping; no fit-based admission threshold. Equivalent current partition keeps version. First tree may match HOST candidate. Returning to an older non-current tree is allowed. Host is a mixture, not a declared tree.'}}
