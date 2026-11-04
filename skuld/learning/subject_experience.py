"""CP21 bounded responsibility SEC bridge; supplier truth and business consequences stay distinct."""
import copy,json
from pathlib import Path
from .sec_evo_atomic import Pool17
from .resource_calibration import POOL_FIELDS
FIELDS=('processed','goals','predictions','experiences','opportunities','model_ids','issue_ids','version','shadow_done','pending_shadow','cp22')
class State:
 def __init__(self,data=None):
  if data is None:
   self.pool=Pool17(json.loads(Path(__file__).with_name('resource_initial.json').read_text()),'EVO-MIX');self.processed=[];self.goals={};self.predictions={};self.experiences={};self.opportunities={};self.model_ids=[];self.issue_ids=[];self.version=0;self.shadow_done=False;self.pending_shadow=None;self.cp22={}
  else:
   if data['schema']!=1:raise ValueError('SUBJECT_EXPERIENCE_SCHEMA')
   for k in FIELDS:setattr(self,k,copy.deepcopy(data.get(k,{})))
   self.pool=Pool17.__new__(Pool17)
   for k in POOL_FIELDS:setattr(self.pool,k,copy.deepcopy(data['pool'][k]))
   self.pool.seen=set(data['pool']['seen'])
 def snapshot(self):return {'schema':1,**{k:copy.deepcopy(getattr(self,k)) for k in FIELDS},'pool':{**{k:copy.deepcopy(getattr(self.pool,k)) for k in POOL_FIELDS},'seen':sorted(self.pool.seen)}}
def load(rt,u,cid):
 rt.store._conn.execute('CREATE TABLE IF NOT EXISTS subject_experience_state(user_id TEXT NOT NULL,channel_id TEXT NOT NULL,data_json TEXT NOT NULL,PRIMARY KEY(user_id,channel_id))')
 row=rt.store._conn.execute('SELECT data_json FROM subject_experience_state WHERE user_id=? AND channel_id=?',(u,cid)).fetchone();return State(json.loads(row[0]) if row else None)
def save(rt,u,cid,s):
 rt.store._conn.execute('INSERT INTO subject_experience_state VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET data_json=excluded.data_json',(u,cid,json.dumps(s.snapshot())))
def experience_table(experiences):
 """Lossless wire representation: no event, field, number or original reference is removed."""
 from collections import Counter
 import re
 keys=sorted({k for e in experiences for k in e})
 common={k:copy.deepcopy(experiences[0][k]) for k in keys if experiences and all(k in e and e[k]==experiences[0].get(k) for e in experiences)}
 columns=[k for k in keys if k not in common]
 # These records share a schema. Missing values must not be silently converted into null.
 if any(set(e)!=set(keys) for e in experiences):raise ValueError('SEC_EXPERIENCE_SCHEMA_MISMATCH')
 counts=Counter()
 def count(v):
  if isinstance(v,str) and len(v)>32:counts[v]+=1
  elif isinstance(v,dict):
   for x in v.values():count(x)
  elif isinstance(v,list):
   for x in v:count(x)
 count(common)
 for e in experiences:
  for k in columns:
   if k!='experience_id':count(e[k])
 aliases={v:'R'+str(i+1) for i,v in enumerate(v for v,n in counts.items() if n>1 or re.fullmatch(r'[a-z_]+_[0-9a-f]{32,64}',v))}
 def pack(v):
  if isinstance(v,str) and v in aliases:return {'ref':aliases[v]}
  if isinstance(v,dict):return {k:pack(x) for k,x in v.items()}
  if isinstance(v,list):return [pack(x) for x in v]
  return v
 return {'format':'lossless-experience-table-v1','instructions':'Each row maps positionally to columns and inherits common fields. {"ref":"R#"} denotes the exact original string in references, not a new evidence ID. All events and fields are present. Cite the literal experience_id column in issues; never cite an R# alias. Supplier truth, final delivery, execution and fees retain separate columns.','common':pack(common),'columns':columns,'rows':[[e[k] if k=='experience_id' else pack(e[k]) for k in columns] for e in experiences],'references':{a:v for v,a in aliases.items()}}

def sec_wire_view(view, input_node_id):
 """Keep exact opaque IDs in the persisted input registry; send bijective typed aliases.
 Experience IDs remain literal and admission remains strict, so no response guessing occurs.
 """
 wire=copy.deepcopy(view);table=wire['experiences'];refs=table.pop('references')
 table['reference_registry_node_id']=input_node_id
 table['reference_types']={alias:value.rsplit('_',1)[0] for alias,value in refs.items()}
 table['instructions']='Rows map positionally to columns and inherit common fields. Each {"ref":"R#"} is a unique exact node identity within this request, with its type in reference_types; equal aliases mean the same node. The complete alias-to-original-ID registry is durably stored with reference_registry_node_id. All event fields and facts are retained. Cite literal experience_id column values only, never R# aliases. Supplier truth, delivery, fees and execution are distinct.'
 return wire

class ResponsibilitySECBridge:
 """Replaces only the missing responsibility-to-candidate bridge, not differential causal credit."""
 @staticmethod
 def view(rt,u,s,day):
  cells={}
  for e in s.pool.history:
   key=json.dumps(e['features'],sort_keys=True);z=cells.setdefault(key,{'features':e['features'],'ready':0,'delayed':0});z['delayed' if e['y'] else 'ready']+=1
  experiences=[rt.graph.get_node(u,x).content|{'experience_id':x} for x in s.experiences.values()]
  return {'stage':'SEC_ATTRIBUTION','day':day,'observed_count':len(s.processed),'condition_counts':list(cells.values()),'experiences':experience_table(experiences),'candidate_support':[{'id':h['id'],'born_day':h['born_day'],'status':h['status'],'future_n':len(h['scores']),'future_mean_log_loss':sum(v['loss'] for v in h['scores'].values())/len(h['scores']) if h['scores'] else None} for h in s.pool.hypotheses],
   'contract':'Identify at most two bounded problems for investigation and candidate comparison using own actual experiences. Cite 1-4 displayed experience_ids per issue, scope is a subset of binary f1/f2/f3. Locus CONDITION_MODEL, INFORMATION_GAP, DECISION_TRADEOFF, EXECUTION, or UNCERTAIN. priority 0..1 is attention only, not fitness. Supplier_y alone scores supplier forecasts; on_time, actual fees and execution status are separate. One loss does not prove probability wrong or alternative action superior. No fabricated causal credit or future labels.'}
