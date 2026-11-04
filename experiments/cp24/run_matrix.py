"""Fixed CP24 development matrix orchestrator; one independent process per life."""
import json,subprocess,sys
from pathlib import Path
from experiments.cp24.cp24_runner import OUT,ROOT,MANIFEST,write,preflight_global_limits,expected_product_head

def main():
 manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
 if manifest.get('phase') not in ('DEV','CONFIRM') or manifest.get('freeze_pending') or not manifest.get('product_commit'):
  raise RuntimeError('RUN_MANIFEST_NOT_FROZEN')
 current=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 if current!=expected_product_head(manifest):raise RuntimeError('PRODUCT_COMMIT_DRIFT')
 status=subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).splitlines()
 if any(row!=' M experiments/cp24/RUN_MANIFEST.json' for row in status):
  raise RuntimeError('PRODUCT_SOURCE_DIRTY:'+repr(status[:5]))
 prior_block=preflight_global_limits(manifest,full_parent=True)
 if prior_block:raise RuntimeError('PREEXISTING_BUDGET_BLOCK:'+prior_block)
 OUT.mkdir(parents=True,exist_ok=True)
 frozen=OUT/'RUN_MANIFEST_FROZEN.json'
 if frozen.exists():
  if frozen.read_bytes()!=MANIFEST.read_bytes():raise RuntimeError('RUN_MANIFEST_DRIFT')
 else:frozen.write_bytes(MANIFEST.read_bytes())
 results=[]
 for spec in manifest['order']:
  seed=int(spec['seed'])
  for arm in spec['arms']:
   name=f"CP24_{manifest['phase']}_{arm}_seed{seed}"
   dest=OUT/name
   if dest.exists():
    if not (dest/'score.json').exists():raise RuntimeError(f'UNSEALED_PREFIX_REQUIRES_EXACT_RECOVERY:{name}')
    score=json.loads((dest/'score.json').read_text(encoding='utf-8'))
    results.append({'name':name,'seed':seed,'arm':arm,'score':score,'status':'ALREADY_RECORDED'})
    write(OUT/'matrix_progress.json',results)
    if score.get('budget_block'):
     print('BUDGET_STOP_AT_RECORDED_LIFE',name,score['budget_block'],flush=True)
     return
    continue
   global_block=preflight_global_limits(manifest)
   if global_block:
    results.append({'name':name,'seed':seed,'arm':arm,'status':'NOT_STARTED_GLOBAL_BUDGET_STOP',
      'budget_block':global_block})
    write(OUT/'matrix_progress.json',results)
    print('BUDGET_STOP_BEFORE_NEW_LIFE',name,global_block,flush=True)
    return
   log=OUT/(name+'.log')
   with log.open('w',encoding='utf-8') as stream:
    proc=subprocess.run([sys.executable,'-m','experiments.cp24.cp24_runner',arm,str(seed),'--phase',manifest['phase']],
      cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
   score=json.loads((dest/'score.json').read_text(encoding='utf-8')) if (dest/'score.json').exists() else None
   results.append({'name':name,'seed':seed,'arm':arm,'returncode':proc.returncode,
     'score':score,'status':'RECORDED' if score is not None else 'CRASH_UNSEALED'})
   write(OUT/'matrix_progress.json',results)
   if proc.returncode or score is None:raise RuntimeError(f'RUN_STOPPED:{name}:see {log}')
   if score.get('budget_block'):
    print('BUDGET_STOP_AFTER_LIFE',name,score['budget_block'],flush=True)
    return
   print('LIFE_RECORDED',name,score.get('harness_outcome'),score.get('requests'),flush=True)
 print('CP24_MATRIX_RECORDED',manifest['phase'],len(results),flush=True)
if __name__=='__main__':main()
