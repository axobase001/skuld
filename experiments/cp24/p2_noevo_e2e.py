"""Zero-API native Runtime/PublicMCP S-NOEVO E2E, frozen before code."""
import json,sys
from pathlib import Path
from skuld.app.runtime import Runtime
from skuld.sim.providers import offline_config,completion
from skuld.loop.tool_subject import attach
from experiments.cp24.cp24_runner import PublicMCP
class Model:
 def __init__(self):self.requests=[];self.rt=None;self.user=None
 def __call__(self,endpoint,key,payload,*args):
  self.requests.append(payload)
  wire=payload['messages'][-1]['content']
  if 'PUBLIC_TOOL_REFLECT' in wire:
   evidence=self.rt.nodes(self.user,'ToolObservation')[-1].node_id
   return completion({'attribution':'Observed day transition; future demand remains uncertain','evidence_ids':[evidence],
     'memory_items':[{'lesson':'Check public stock and price after day transition','scope':'public operations','uncertainty':'Unknown future demand'}],
     'candidates':[]})
  action='wait_for_next_day' if len([r for r in self.requests if 'PUBLIC_TOOL_DECIDE' in r['messages'][-1]['content']])==1 else 'get_status'
  return completion({'actions':[{'tool':action,'arguments':{}}], 'ledger':'Observe public state','plan':'Continue operations',
    'predictions':[],'responsibilities':[],'reason':'offline interface fixture',
    'used_evidence_ids':[],'used_candidate_ids':[],'responsibility_ids':[]})
def main(out):
 out.mkdir(parents=True,exist_ok=False);t=PublicMCP(out,1,resume=False);m=Model()
 cfg={**offline_config(8),'tool_subject':True,'business_events':True,'tool_subject_arm':'S-NOEVO',
      'sec_learner':False,'provider_format_retry':False,'max_input_tokens':256000,'max_output_tokens':256000,
      'input_tokenizer_path':'/content/cp24_tokenizer.json'}
 rt=Runtime(out/'runtime',cfg,provider_transport=m);u=rt.provision('P2 NOEVO E2E',True)['user_id'];m.rt=rt;m.user=u;attach(rt,u,t)
 try:
  rt.loop_round(u);rt.loop_round(u)
  memories=rt.nodes(u,'ToolReasoningMemory');assert memories,'NO_ORDINARY_FEEDBACK_MEMORY'
  assert not rt.nodes(u,'BusinessCandidate'),'UNEXPECTED_EVOLUTIONARY_CANDIDATE'
  assert any('Check public stock and price' in str(x) for x in m.requests[-1:]),'FEEDBACK_NOT_IN_NEXT_DECIDE_REQUEST'
  assert rt.nodes(u,'ToolExecution'),'NO_PUBLIC_TOOL_EXECUTION'
  final_wire=m.requests[-1]['messages'][-1]['content']
  assert all(x in final_wire for x in ('ASSUMPTION_UNKNOWN','STALE','get_machine_inventory','quantity_status')),'STALE_RECOVERY_CONTRACT_NOT_ON_ACTUAL_WIRE'
  (out/'checks.json').write_text(json.dumps({'offline_requests':len(m.requests),'memory_nodes':len(memories),'candidate_nodes':0,'public_executions':len(rt.nodes(u,'ToolExecution'))},indent=2))
  print('P2_NOEVO_E2E_GREEN')
 finally:rt.close();t.close()
if __name__=='__main__':main(Path(sys.argv[1]))
