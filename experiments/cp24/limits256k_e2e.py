"""CP24 R4 pre-code RED: actual FIX1 day25 REFLECT and parent RESERVED ledger. Zero API."""
from pathlib import Path
import copy,json,sys
sys.path.insert(0,'/content/skuld_cp24_r4')
from skuld.app.runtime import Runtime,model_output_limit
from skuld.sim.providers import offline_config
ROOT=Path('/content/skuld_cp24_r4')
OUT=Path('/content/cp24_review/CP24_R4_256K_RED')
SOURCE=Path('/content/cp24_dev_r3')
CAP=256000
OUT.mkdir(parents=True,exist_ok=True)
rows=[json.loads(line) for line in (SOURCE/'CP24_DEV_S-EVO_seed1/runtime/receipts.jsonl').open() if line.strip()]
failed=[x for x in rows if x.get('kind')=='provider_preflight' and x.get('stage')=='PRODUCT_TOOL_REFLECT' and x.get('reason')=='INPUT_TOKEN_LIMIT_REQUIRED_CONTEXT']
assert failed,'Missing actual FIX1 late REFLECT preflight'
record=failed[0];request=copy.deepcopy(record['request'])
wrapper=json.loads(request['messages'][-1]['content']);original=json.loads(wrapper['task']) if isinstance(wrapper['task'],str) else wrapper['task']
assert original['stage']=='PUBLIC_TOOL_REFLECT' and request['max_tokens']==6144
sent=[0]
def no_transport(*a,**kw):sent[0]+=1;raise AssertionError('ZERO_API_SENTINEL')
config={**offline_config(20),'max_input_tokens':CAP,'max_output_tokens':CAP,'input_tokenizer_path':str(SOURCE/'tokenizer.json')}
rt=Runtime(OUT/'runtime',config,provider_transport=no_transport)
try:
    requested=copy.deepcopy(request);requested['max_tokens']=CAP
    fitted=rt._fit_product_payload(requested)
    final_size=rt._input_size(fitted)
    final_wrapper=json.loads(fitted['messages'][-1]['content'])
    final_task=json.loads(final_wrapper['task']) if isinstance(final_wrapper['task'],str) else final_wrapper['task']
    critical=('stage','arm','experiences','responsibilities','learning_evidence','learning_evidence_ids','candidate_parents','rules','run_budget_state')
    missing=[k for k in critical if k not in final_task or final_task[k]!=original.get(k)]
    try:output_limit=model_output_limit(config);output_error=None
    except Exception as exc:output_limit=None;output_error=type(exc).__name__+': '+str(exc)
finally:rt.close()
old_manifest=json.loads((SOURCE/'RUN_MANIFEST_FROZEN.json').read_text())
new_manifest=json.loads((ROOT/'experiments/cp24/RUN_MANIFEST.json').read_text())
ledger=SOURCE/'calls.jsonl';calls=[json.loads(line) for line in ledger.open() if line.strip()]
reserved=[x for x in calls if x.get('state')=='RESERVED']
assert len(calls)==139 and len(reserved)==1,'FIX1 ledger changed during RED preparation'
expected_prior=old_manifest['prior_cumulative_including_unknown_reserve']+len(calls)
new_prior=new_manifest.get('prior_cumulative_including_unknown_reserve',0)
parent_contracts=new_manifest.get('r2_parent_ledgers',[])+new_manifest.get('r3_parent_ledgers',[])+new_manifest.get('r4_parent_ledgers',[])
contract=next((x for x in parent_contracts if x.get('path')==str(ledger)),None)
reserve=(CAP*2+CAP*8)/1e6
checks={'source':str(SOURCE/'CP24_DEV_S-EVO_seed1/runtime/receipts.jsonl'),'logical_ref':record['logical_ref'],
  'original_reason':record['reason'],'original_output_limit':request['max_tokens'],
  'final_wire_tokens_at_256k':final_size,'final_wire_within_input_cap':final_size<=CAP,
  'final_output_wire_max_tokens':fitted['max_tokens'],'critical_task_fields_preserved':not missing,'critical_fields_changed':missing,
  'model_output_limit_result':output_limit,'model_output_limit_error':output_error,'provider_transport_calls':sent[0],
  'fix1_parent_ledger_rows':len(calls),'fix1_parent_reserved_rows':len(reserved),
  'fix1_parent_reserved_cny':sum(float(x['reserved_cny']) for x in reserved),
  'expected_r4_cumulative_prior_including_fix1':expected_prior,'actual_r4_cumulative_prior':new_prior,
  'fix1_parent_contract_present':contract is not None,'fix1_parent_contract_rows':contract.get('persisted_rows') if contract else None,
  'new_per_request_conservative_reserve_cny':reserve,'old_budget_authorization_unchanged':True}
(OUT/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
print(json.dumps({k:checks[k] for k in ('logical_ref','final_wire_tokens_at_256k','critical_fields_changed','model_output_limit_error','provider_transport_calls','fix1_parent_ledger_rows','fix1_parent_reserved_rows','expected_r4_cumulative_prior_including_fix1','actual_r4_cumulative_prior','fix1_parent_contract_present','new_per_request_conservative_reserve_cny')},ensure_ascii=False))
failures=[]
if not (final_size<=CAP and not missing and fitted['max_tokens']==CAP and sent[0]==0):failures.append('final_wire_or_zero_API_failure')
if output_limit!=CAP:failures.append('model_output_limit_still_caps_at_6144')
if not (new_prior>=expected_prior and contract and contract.get('persisted_rows')==len(calls)):failures.append('FIX1_parent_ledger_including_RESERVED_not_protected')
assert not failures,'RED: '+', '.join(failures)
