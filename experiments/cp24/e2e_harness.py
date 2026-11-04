"""Pre-code CP24 E2E: official public MCP -> native Runtime -> persisted evidence -> restart.

Fixture model responses are marked offline. This verifies transport and persistence, not model quality.
"""
import json, subprocess, sys
from pathlib import Path
from skuld.app.runtime import Runtime
from skuld.sim.providers import offline_config, completion
from skuld.loop.tool_subject import attach, state
from experiments.cp24.cp24_runner import PublicMCP

BENCHMARK_HEAD = 'f9a1d7efde193de2242ed5afb1f3a326e362c75d'

class OfflineModel:
 def __init__(self): self.requests=[]
 def __call__(self, endpoint, key, payload, *args):
  self.requests.append(payload)
  return completion({'actions':[{'tool':'get_status','arguments':{}}],
   'ledger':'Offline E2E only','plan':'Check public status',
   'predictions':[],'responsibilities':[],'reason':'E2E fixture'})

def main():
 out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
 head=subprocess.check_output(['git','rev-parse','HEAD'],cwd='/content/vending-bench',text=True).strip()
 assert head==BENCHMARK_HEAD, (head,BENCHMARK_HEAD)
 t=PublicMCP(out,1,resume=False)
 try:
  names={x['name'] for x in t.tools()}
  assert len(names)==22 and {'get_status','get_sales_report','wait_for_next_day'}-names==set()
  model=OfflineModel()
  cfg={**offline_config(4),'tool_subject':True,'business_events':True,
       'tool_subject_arm':'B-RB','sec_learner':False,
       'max_input_tokens':32768,'max_output_tokens':6144}
  rt=Runtime(out/'runtime',cfg,provider_transport=model)
  u=rt.provision('CP24 E2E isolated subject',True)['user_id']
  attach(rt,u,t)
  rt.loop_round(u)
  assert rt.nodes(u,'ToolDecision') and rt.nodes(u,'ToolObservation') and rt.nodes(u,'ToolExecution')
  assert any(n.content.get('tool')=='get_status' for n in rt.nodes(u,'ToolObservation'))
  assert len(model.requests)==1
  before=state(rt,u); observations=len(rt.nodes(u,'ToolObservation'))
  rt.close()
  rt2=Runtime(out/'runtime',cfg,provider_transport=model);attach(rt2,u,t)
  assert state(rt2,u)==before and len(rt2.nodes(u,'ToolObservation'))==observations
  rt2.close()
  (out/'offline_requests.json').write_text(json.dumps(model.requests,ensure_ascii=False,indent=2))
  (out/'checks.json').write_text(json.dumps({'public_benchmark_commit':head,'public_tool_count':len(names),
    'native_runtime_path':True,'restart_preserves_state':True,'actual_model_calls':0},indent=2))
  print('CP24_OFFLINE_E2E_OK',out)
 finally: t.close()
if __name__=='__main__':main()
