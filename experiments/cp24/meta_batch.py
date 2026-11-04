"""Run one frozen CP24 Meta life, with a real agent process restart for seed 240101."""
import sys,json,subprocess,argparse,os
from pathlib import Path
HERE=Path(__file__).resolve().parent
OUT=Path(os.environ.get('SKULD_META_OUTPUT_DIR','/content/skuld-meta/runs'))

def run_child(seed,resume=False,restart_at_day=None):
    cmd=[sys.executable,str(HERE/'meta_runner.py'),'--seed',str(seed),'--phase','CONFIRM_R2']
    if resume:cmd.append('--resume')
    if restart_at_day:cmd.extend(['--restart-at-day',str(restart_at_day)])
    p=subprocess.Popen(cmd,cwd=HERE.parents[1]);print('AGENT_PROCESS',p.pid,'RESUME',resume,flush=True)
    code=p.wait();print('AGENT_EXIT',p.pid,code,flush=True)
    if code:raise RuntimeError('AGENT_PROCESS_FAILED:'+str(code))
    score=json.loads((OUT/f'META_CONFIRM_R2_S-EVO_seed{seed}'/'score.json').read_text())
    return p.pid,score

def main(seed):
    if seed not in (240101,240102):raise RuntimeError('SEED_NOT_FROZEN')
    dest=OUT/f'META_CONFIRM_R2_S-EVO_seed{seed}'
    if dest.exists():raise RuntimeError('LIFE_ALREADY_STARTED')
    first_pid,first=run_child(seed,restart_at_day=10 if seed==240101 else None)
    if seed==240101 and first['harness_outcome']=='planned_process_restart':
        marker=json.loads((dest/'restart_before.json').read_text())
        if marker['old_agent_pid']!=first_pid:raise RuntimeError('RESTART_PID_MISMATCH')
        second_pid,last=run_child(seed,resume=True)
        if second_pid==first_pid:raise RuntimeError('PROCESS_NOT_RESTARTED')
        after=json.loads((dest/'restart_after.json').read_text())
        if after['new_agent_pid']!=second_pid or not after['execution_prefix_matched']:
            raise RuntimeError('RESTART_PREFIX_VERIFICATION_FAILED')
    else:last=first;second_pid=None
    status={'seed':seed,'phase':'CONFIRM_R2','first_agent_pid':first_pid,'resumed_agent_pid':second_pid,
        'finalized':last['finalized'],'harness_outcome':last['harness_outcome'],
        'requests':last['requests'],'official':last['official']}
    path=dest/'batch_status.json';path.write_text(json.dumps(status,ensure_ascii=False,indent=2))
    print('BATCH_RESULT',json.dumps({k:status[k] for k in ('seed','finalized','harness_outcome','requests')},ensure_ascii=False),flush=True)
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--seed',type=int,required=True);main(a.parse_args().seed)
