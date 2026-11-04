"""Action-time supplier risk: persistent prequential calibration and value explanations."""
from .resource_loop import record
from .resource_value import value_table
from skuld.learning.sec_evo import features

def calibrated(p,rows,day,source_id):
 unique={}
 for x in rows:
  if not x['training_eligible'] or x['source_id']!=source_id or x['observed_day']>day or x['target_day']>day:continue
  key=(x['source_id'],x['object_id'],x['target_day'])
  if key in unique and unique[key]['snapshot_id']!=x['snapshot_id']:raise ValueError('DUPLICATE_DEADLINE_FORECAST')
  unique[key]=x
 bin_id=min(4,int(p*5));matched=[x for x in unique.values() if min(4,int(x['raw_p']*5))==bin_id];n=len(matched);ready=len(unique)>=16 and n>0
 return {'raw_p':p,'used_p':(20*p+sum(x['supplier_y'] for x in matched))/(20+n) if ready else p,'status':'FITTED' if ready else 'WARMUP' if len(unique)<16 else 'EMPTY_BIN','scheme':'fixed5bin_raw_probability_centered_strength20_warmup16','n_total':len(unique),'bin':bin_id,'bin_n':n,'bin_y':sum(x['supplier_y'] for x in matched),'training_snapshot_ids':[x['snapshot_id'] for x in sorted(matched,key=lambda x:(x['observed_day'],x['object_id']))],'max_training_observed_day':max((x['observed_day'] for x in unique.values()),default=None)}

def action_view(rt,u,b,risks):
 rows=[n.content for n in rt.nodes(u,'ActionRiskSettlement')];out={};cid=rt.config['subject_resource_channel']
 for t in b['open_responsibilities']+b['future_commitments']:
  oid=t['object_id'];p=risks['risks'][oid]
  if len(features(t))==3:out[oid]=calibrated(p,rows,b['day'],cid)
  else:out[oid]={'raw_p':p,'used_p':p,'status':'UNKNOWN_CONDITION_UNCHANGED','training_snapshot_ids':[],'n_total':0,'bin_n':0,'max_training_observed_day':None}
 return out

def decorate(board,table):
 if not table:return table
 wait=next(q['expected_incremental_loss'] for q in table if q['action']=='WAIT');tasks={t['object_id']:t for t in board['open_responsibilities']+board['future_commitments']};result=[]
 for q in table:
  extra={'net_advantage_vs_wait':wait-q['expected_incremental_loss'],'scope':'current public tasks and fixed current risk vector; no future truth'}
  if q['action']=='RESERVE':
   oid=q['object_id'];t=tasks[oid];p=q['risk_inputs'][oid];direct=p*t['miss_loss']-t['reserve_fee'];extra.update(direct_expected_saving=direct,opportunity_cost_vs_wait=direct-extra['net_advantage_vs_wait'])
   def advantage(prob):
    class P:
     def risk(self,task):return prob if task['object_id']==oid else q['risk_inputs'][task['object_id']]
     def public_state(self):return {}
    vals=value_table(board,P());own=next(v['expected_incremental_loss'] for v in vals if v['action']=='RESERVE' and v['object_id']==oid);other=min(v['expected_incremental_loss'] for v in vals if not(v['action']=='RESERVE' and v['object_id']==oid));return other-own
   low,high=0.,1.
   if advantage(high)<=1e-8:threshold=None
   elif advantage(low)>1e-8:threshold=0.
   else:
    for _ in range(20):
     middle=(low+high)/2
     if advantage(middle)>1e-8:high=middle
     else:low=middle
    threshold=high
   extra.update(strict_advantage_threshold=threshold,probability_margin=None if threshold is None else p-threshold,threshold_contract='change this object risk only; strict advantage over every other legal action, WAIT wins ties; 2^-20 resolution')
  result.append({**q,'decision_margin':extra})
 return result

def snapshot(rt,u,b,computed,decision,src,goals,predictions):
 cid=rt.config['subject_resource_channel'];ids=[]
 for t in b['open_responsibilities']:
  oid=t['object_id'];raw=computed['risk']['activation_snapshots'][oid];a=computed['action_risk'][oid]
  node=record(rt,u,'ActionRiskSnapshot',str(b['day'])+':'+oid,{'source_id':cid,'object_id':oid,'goal_id':goals[oid],'issued_day':b['day'],'booking_deadline':t['booking_deadline'],'target_day':t['target_day'],'training_eligible':b['day']==t['booking_deadline'],'action_window_open':b['day']<=t['booking_deadline'],'first_prediction_id':predictions[oid]['prediction_id'],'observation_id':src.source_event_id,'decision_id':decision.node_id,'state_version':b['state_version'],'raw_activation':raw,'calibration':a,'raw_p':a['raw_p'],'used_p':a['used_p'],'actual_action':decision.content['decision'],'value_table':computed['table'],'parameter_updates':'host prequential supplier labels only; not model generated'},src);ids.append(node.node_id)
 return ids

def settle(rt,u,cid,e,day,src):
 existing={n.content['snapshot_id'] for n in rt.nodes(u,'ActionRiskSettlement')};y=int(e['supplier_result']=='delayed')
 for n in rt.nodes(u,'ActionRiskSnapshot'):
  c=n.content
  if c['source_id']!=cid or c['object_id']!=e['object_id'] or n.node_id in existing:continue
  if c['target_day']!=e['target_day'] or c['issued_day']>=e['target_day'] or e['target_day']>day:raise ValueError('ACTION_RISK_TEMPORAL_BINDING')
  record(rt,u,'ActionRiskSettlement',n.node_id,{'snapshot_id':n.node_id,'decision_id':c['decision_id'],'source_id':cid,'object_id':e['object_id'],'target_day':e['target_day'],'observed_day':day,'observation_id':src.source_event_id,'supplier_y':y,'supplier_result':e['supplier_result'],'raw_p':c['raw_p'],'used_p':c['used_p'],'raw_brier':(c['raw_p']-y)**2,'used_brier':(c['used_p']-y)**2,'training_eligible':c['training_eligible'],'truth_contract':'observed original supplier only; final delivery and monetary losses are not labels'},src)
