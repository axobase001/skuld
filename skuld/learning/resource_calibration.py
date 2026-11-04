"""CP19 persistent source calibration. Only observed supplier outcomes train it."""
import json,copy
from pathlib import Path
from .sec_evo_atomic import Pool17
from skuld.loop.resource_value import value_table
POOL_FIELDS=('mode','hypotheses','history','forecasts','events','proposals','event_tasks','links','last_rejections','compilations')
class State:
 def __init__(self,online,data=None):
  self.online=online
  if data is None:
   self.pool=Pool17(json.loads(Path(__file__).with_name('resource_initial.json').read_text()),'FIXED-MIX')
   self.cal={'A':0.,'B':0.,'lambda':0.,'tau':1.,'version':0,'pending':{},'processed':[]};self.version=0
  else:
   if data['schema_version']!=1 or data['online']!=online:raise ValueError('CALIBRATION_MODE_OR_PROTOCOL_MISMATCH')
   self.pool=Pool17.__new__(Pool17)
   for k in POOL_FIELDS:setattr(self.pool,k,copy.deepcopy(data['pool'][k]))
   self.pool.seen=set(data['pool']['seen']);self.cal=copy.deepcopy(data['calibration']);self.version=data['version']
 def snapshot(self):return {'schema_version':1,'online':self.online,'version':self.version,'pool':{**{k:copy.deepcopy(getattr(self.pool,k)) for k in POOL_FIELDS},'seen':sorted(self.pool.seen)},'calibration':copy.deepcopy(self.cal)}
 def public(self):return {'version':self.cal['version'],'A':self.cal['A'],'B':self.cal['B'],'stored_lambda':self.cal['lambda'],'tau':1.,'online':self.online,'processed_objects':len(self.cal['processed']),'pool_version':len(self.pool.history)}
 def settle(self,results,day):
  updates=[]
  for e in results:
   oid=e['object_id']
   if oid in self.cal['processed']:continue
   if e['target_day']>day:raise ValueError('FUTURE_SUPPLIER_RESULT')
   rows=self.cal['pending'].pop(oid,[])
   if any(x['issued_day']>=e['target_day'] or x['target_day']!=e['target_day'] for x in rows):raise ValueError('INVALID_FROZEN_TARGET')
   y=int(e['supplier_result']=='delayed');m=len(rows);a=sum((x['p_L']-x['p_H'])*(y-x['p_H']) for x in rows)/m if m else 0.;b=sum((x['p_L']-x['p_H'])**2 for x in rows)/m if m else 0.;before=self.public()
   if self.online:
    self.cal['A']+=a;self.cal['B']+=b;self.cal['lambda']=min(1.,max(0.,self.cal['A']/(1+self.cal['B'])))
   self.cal['processed'].append(oid);self.cal['version']+=1
   updates.append({'object_id':oid,'target_day':e['target_day'],'observed_day':day,'y':y,'label':'ORIGINAL_SUPPLIER_DELAYED','m':m,'delta_A':a,'delta_B':b,'applied':self.online,'before':before,'after':self.public(),'frozen_forecasts':rows,'host_rule_computed':True})
  self.pool.observe(results,day)
  return updates

def load_state(rt,u,cid):
 rt.store._conn.execute('CREATE TABLE IF NOT EXISTS resource_calibration_state (user_id TEXT NOT NULL, channel_id TEXT NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(user_id,channel_id))')
 row=rt.store._conn.execute('SELECT data_json FROM resource_calibration_state WHERE user_id=? AND channel_id=?',(u,cid)).fetchone()
 return State(bool(rt.config['resource_calibration_online']),json.loads(row[0]) if row else None)
def save_state(rt,u,cid,state):
 state.version+=1
 rt.store._conn.execute('INSERT INTO resource_calibration_state VALUES (?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET data_json=excluded.data_json',(u,cid,json.dumps(state.snapshot())))

class RiskProvider:
 def __init__(self,risks,provenance):self.risks=risks;self.provenance=provenance
 def risk(self,t):return self.risks[t['object_id']]
 def public_state(self):return self.provenance

def build_values(board,state,model_risks,override=None):
 lam=state.cal['lambda'] if override is None else override;risks={};rows={}
 current={t['object_id'] for t in board['open_responsibilities']}
 for t in board['open_responsibilities']+board['future_commitments']:
  oid=t['object_id'];h=state.pool.risk(t);p=model_risks.get(oid) if oid in current else None;eff=h if p is None else h+lam*(p-h)
  rows[oid]={'p_H':h,'p_L':p,'p_effective':eff,'effective_lambda':lam,'fallback':'UNREVEALED_CONDITION_PRIOR' if oid not in current else 'NO_VALID_MODEL_FORECAST' if p is None else None,'calibration_version':state.cal['version'],'pool_version':len(state.pool.history)};risks[oid]=eff
 provider=RiskProvider(risks,{**state.public(),'effective_lambda':lam,'control_view':override==0})
 table=value_table(board,provider)
 return {'risks':risks,'risk_inputs':rows,'table':[{k:v for k,v in x.items() if k not in ('risk_inputs','risk_provenance','sunk_paid_costs_excluded')} for x in table],'calibration':provider.public_state()}
