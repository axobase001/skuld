"""CP18 bounded historical projections; current facts and current model output stay intact."""
import json

def historical_view(rt,u,view):
 view=dict(view)
 view['recent_own_decisions']=[{'node_id':n.node_id,'provider_receipt_id':n.content['provider_receipt_id'],'day':n.content['day'],**{k:v for k,v in n.content['decision'].items() if k!='reason'}} for n in rt.nodes(u,'ResourceChoice')[-4:]]
 if 'recent_own_drafts' in view:
  drafts=[]
  for n in rt.nodes(u,'ResourceDraft')[-4:]:
   d=json.loads(n.content['raw_draft']);drafts.append({'node_id':n.node_id,'provider_receipt_id':n.content['provider_receipt_id'],'day':n.content['day'],**{k:v for k,v in d.items() if k!='reason'}})
  view['recent_own_drafts']=drafts
 view['recent_conditional_settlements']=[{'node_id':n.node_id,**{k:v for k,v in n.content.items() if k not in ('outcome',)}} for n in rt.nodes(u,'ResourceForecastSettlement')[-4:]]
 view['history_selection']='CP18 fixed rule shared by B/C: last4 own decisions/drafts retain exact structured fields and record references, omit OLD explanation prose; last4 settlements omit duplicated outcome body; full originals persisted. Current draft, admitted branches, competing tasks, fees, deadlines, slot, risk inputs and current value table are not truncated. All actually observed supplier counts and last8 complete outcomes/actions remain.'
 return view

def execution_contract(rt,u,board,decision,values,result):
 row=next((q for q in values if q['action']==decision.action and q['object_id']==decision.object_id),None)
 chosen=row['expected_incremental_loss'] if row else None;minimum=min((q['expected_incremental_loss'] for q in values),default=None)
 effect={'before_slot':board['slot'],'after_slot':board['slot'],'fee':0,'state_version':board['state_version']}
 if result.get('ok') and decision.action!='WAIT':
  outcome=next(n for n in rt.nodes(u,'ActionOutcome') if n.node_id==result['outcome_id']);effect=outcome.content['connector_result']['effect']
 return {'authority':'structured action/object_id/state_version only; reason never overrides action','action':decision.action,'object_id':decision.object_id,'requested_state_version':decision.state_version,'before_slot':effect['before_slot'],'after_slot':effect['after_slot'],'actual_new_fee':effect['fee'],'chosen_Q':chosen,'minimum_Q':minimum,'chosen_minus_minimum_Q':chosen-minimum if chosen is not None else None,'execution_ok':result.get('ok',False),'outcome_id':result.get('outcome_id')}
