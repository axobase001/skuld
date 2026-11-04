"""Zero-API illustrative fixtures against delivered source; not old model trajectory replay."""
import json,unittest,copy
from pathlib import Path
from skuld.policy.policies import RulePolicy
from tests.s3.fixtures import LoopProvider,propose,candidate
from tests.s3.scenes import PriceWorld,prepare
out=Path('/content/skuld/experiments/cp24/evidence/legacy_pre');out.mkdir(parents=True,exist_ok=True)
rows=[{'kind':'PREPARE','score':0.9,'probability':0.9,'cites_inferred':False,'required_preconditions_observed':True},{'kind':'INVESTIGATE','score':0.1,'probability':None,'cites_inferred':False}]
a=RulePolicy().choose(rows,{})
rows2=copy.deepcopy(rows);rows2[0]['cites_inferred']=True
b=RulePolicy().choose(rows2,{})
result={'scope':'local fixtures only; no original S5 requests replayed','policy':{'without_auxiliary_inference':rows[a]['kind'],'with_auxiliary_inference':rows2[b]['kind'],'all_other_fields_identical':True}}
for name,mixed in [('valid_only',False),('valid_plus_invalid_unselectable_WAIT',True)]:
 case=unittest.TestCase()
 def bad_wait(inner):
  x=candidate(inner,'WAIT','wait','do nothing');x['prediction_ids']=['prediction_NOT_OFFERED'];return x
 provider=LoopProvider(propose=propose(prepare(),bad_wait) if mixed else propose(prepare()))
 try:
  p=PriceWorld(case,tau=1.,provider=provider)
  try:
   r=p.bump();error=None
  except Exception as e:
   r=None;error=f'{type(e).__name__}: {e}'
  result[name]={'error':error,'loop_result':r,'product_candidates':[n.to_dict() for n in p.w.rt.nodes(p.w.u,'ProductCandidate')],'provider_stages':provider.stages}
 finally:case.doCleanups()
(out/'remaining_regressions.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'policy':result['policy'],'valid_only':{'error':result['valid_only']['error'],'candidates':len(result['valid_only']['product_candidates'])},'mixed':{'error':result['valid_plus_invalid_unselectable_WAIT']['error'],'candidates':len(result['valid_plus_invalid_unselectable_WAIT']['product_candidates'])}},ensure_ascii=False,indent=2))
