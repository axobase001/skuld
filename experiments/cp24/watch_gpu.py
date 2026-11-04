import json,subprocess,time,datetime
from pathlib import Path
r=Path('/content/skuld_meta_validation_20261104_r2');l=r/'runs/META_CONFIRM_S-EVO_seed240101'
while True:
 try:
  raw=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,utilization.gpu,power.draw','--format=csv,noheader,nounits'],text=True,timeout=10).strip().splitlines()[0].split(',')
  x={'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'memory_mib':float(raw[0]),'utilization_pct':float(raw[1]),'power_w':float(raw[2])}
  with (r/'gpu_usage.jsonl').open('a') as f:f.write(json.dumps(x)+'\n')
 except Exception:pass
 if (l/'score.json').exists() and 'RUN_EXIT' in (r/'continuation.log').read_text():break
 time.sleep(30)
