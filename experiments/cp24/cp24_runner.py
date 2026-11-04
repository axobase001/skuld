"""CP24 Prosus v3 development runner. The product receives public MCP only.

The 30-day score uses the frozen official evaluator after the life. Historical
CP23 raw results stay separate; this file creates new subjects and databases.
"""
import os,sys,json,asyncio,threading,time,subprocess,runpy,itertools,sqlite3,re
from pathlib import Path
from datetime import datetime,timezone,timedelta
import httpx
from skuld.app.runtime import Runtime
from skuld.sim.providers import real_config
from skuld.loop.tool_subject import attach,state

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(os.environ.get('CP24_OUTPUT_ROOT','/content/cp24_dev_r4'))
UP=Path('/content/vending-bench')
SIM=UP/'tasks/vending-bench/environment/sim-server'
BENCHMARK_HEAD='f9a1d7efde193de2242ed5afb1f3a326e362c75d'
RB_HEAD='ed80611788292ea739f1effd31f16c53823b8a0d'
MANIFEST=ROOT/'experiments/cp24/RUN_MANIFEST.json'

def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def check_public_dependencies():
 benchmark=subprocess.check_output(['git','rev-parse','HEAD'],cwd=UP,text=True).strip()
 bank=subprocess.check_output(['git','rev-parse','HEAD'],cwd='/content/reasoning-bank-cp24',text=True).strip()
 if benchmark!=BENCHMARK_HEAD or bank!=RB_HEAD:
  raise RuntimeError('PUBLIC_DEPENDENCY_COMMIT_MISMATCH')
 return benchmark,bank

class PublicMCP:
 def __init__(self,dest,seed,resume=False):
  for k in list(os.environ):
   if k.startswith('VENDING_'):del os.environ[k]
  os.environ.update(VENDING_SEED=str(seed),VENDING_STATE_PATH=str(dest/'environment-private.json'),VENDING_RESUME='1' if resume else '0')
  sys.path[:0]=[str(SIM),str(UP/'scripts')]
  import server,sync_config
  self._server=server;self._briefing=sync_config.render();self.loop=asyncio.new_event_loop();self.thread=threading.Thread(target=self.loop.run_forever,daemon=True);self.thread.start();self.last_day=1;self.minute=540
  from fastmcp import Client
  self.client=Client(server.mcp);self._run(self.client.__aenter__());self.last_day=server.ENGINE.s['day'];self.minute=server.ENGINE.s['minute'];ts=self._run(self.client.list_tools());self._tools=[{'name':t.name,'description':t.description,'parameters':t.inputSchema} for t in ts];assert len(ts)==22
 def _run(self,coro):return asyncio.run_coroutine_threadsafe(coro,self.loop).result(timeout=60)
 def tools(self):return self._tools
 def briefing(self):return self._briefing
 def call(self,name,args):
  import re
  x=self._run(self.client.call_tool(name,args,raise_on_error=False));text='\n'.join(c.text for c in x.content if hasattr(c,'text'));m=re.findall(r'\bDay (\d+)(?:/30| of 30)',text)
  if m:self.last_day=int(m[-1])
  clock=re.findall(r'(\d{2}):(\d{2}) — balance',text)
  if clock:self.minute=int(clock[-1][0])*60+int(clock[-1][1])
  return {'text':text,'is_error':bool(x.is_error)}
 def over(self):return self._server.ENGINE.over
 def finalize(self):return self._server.ENGINE.score(finalize=True)
 def close(self):self._run(self.client.__aexit__(None,None,None));self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join()

def parent_ledger_block(manifest,full=False):
 """Keep both invalid CP24 ledgers fixed; neither old runner may spend beside R2."""
 for contract in manifest['r2_parent_ledgers']:
  label=contract['batch'];path=Path(contract['path'])
  try:st=path.stat()
  except OSError:return label+'_LEDGER_MISSING'
  if st.st_size!=contract['size_bytes'] or st.st_mtime_ns!=contract['mtime_ns']:
   return label+'_LEDGER_DRIFT'
  old_root=contract['source_root']
  for proc in Path('/proc').iterdir():
   if not proc.name.isdigit():continue
   try:
    if (proc/'stat').read_text().split()[2]=='Z':continue
    if os.readlink(proc/'cwd')!=old_root:continue
    cmd=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='ignore')
   except OSError:continue
   if 'experiments.cp24.cp24_runner' in cmd or 'experiments.cp24.run_matrix' in cmd:
    return label+'_PROCESS_ACTIVE'
  if full:
   try:rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
   except (OSError,ValueError):return label+'_LEDGER_DRIFT'
   unknown=[row for row in rows if row.get('state') in ('RESERVED','UNKNOWN_CHARGE')]
   if len(unknown)!=int(contract.get('persisted_unknown_rows',0)) or any(row.get('state') not in ('RETURNED','RESERVED','UNKNOWN_CHARGE') for row in rows):
    return label+'_UNRECONCILED_CHARGE'
   if len(rows)!=contract['persisted_rows']:return label+'_LEDGER_DRIFT'
   cost=sum(float(row.get('charge_estimate_cny',row.get('reserved_cny',0))) for row in rows)
   if abs(cost-contract['persisted_estimated_cny'])>1e-8:return label+'_LEDGER_DRIFT'
 return None

def preflight_global_limits(manifest,full_parent=False):
 """Read persisted R2 receipts and both frozen invalid-batch ledgers."""
 prior_block=parent_ledger_block(manifest,full=full_parent)
 if prior_block:return prior_block
 path=OUT/'calls.jsonl'
 rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []
 used=len(rows);old=int(manifest['prior_cumulative_including_unknown_reserve'])
 if any(row.get('state') in ('RESERVED','UNKNOWN_CHARGE') for row in rows):
  return 'UNRECONCILED_PREVIOUS_REQUEST'
 if used>=int(manifest['p1_request_cap']):return 'P1_BATCH_REQUEST_LIMIT'
 if old+used>=int(manifest['cumulative_request_cap']):return 'CUMULATIVE_REQUEST_LIMIT'
 estimate=sum(float(row.get('charge_estimate_cny',row.get('reserved_cny',0))) for row in rows)
 next_reserve=(256000*2+256000*8)/1e6
 if estimate+next_reserve>float(manifest['internal_p1_guard_cny']):
  return 'P1_INTERNAL_MONEY_GUARD'
 return None

def make_budget(name,life_cap=180):
 """Reuse the CP03 raw provider receipt transport with a CP24 wallet gate."""
 from dotenv import load_dotenv
 load_dotenv('/content/deepseek.env')
 manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
 cap=float(manifest['internal_p1_guard_cny'])
 old=int(manifest['prior_cumulative_including_unknown_reserve'])
 request_cap=min(int(manifest['p1_request_cap']),int(manifest['cumulative_request_cap'])-old)
 if request_cap<=0:raise RuntimeError('CUMULATIVE_REQUEST_LIMIT')
 source_token=Path('/content/cp24_tokenizer.json')
 if not source_token.exists():raise RuntimeError('FROZEN_TOKENIZER_MISSING')
 bootstrap=Path('/content/cp03');bootstrap.mkdir(parents=True,exist_ok=True)
 boot_token=bootstrap/'tokenizer.json'
 if not boot_token.exists():boot_token.write_bytes(source_token.read_bytes())
 OUT.mkdir(parents=True,exist_ok=True)
 token=OUT/'tokenizer.json'
 if not token.exists():token.write_bytes(source_token.read_bytes())
 from experiments.cp03 import run_real as base
 base.OUT=OUT
 base.CAP_CNY=cap
 class Budget(base.Budget):
  def __init__(self):
   super().__init__();self.arm=name
   self.request_limit=request_cap;self.trajectory_request_limit=life_cap
   self.input_limit=256000;self.output_limit=256000
   self.wallet_balance=None;self.wallet_ledger_baseline=self.cost()
   self.wallet_guard=threading.RLock();self.wallet_checks=0
   self.block=preflight_global_limits(manifest)
  def save(self):
   for i,row in enumerate(self.rows):row['cumulative_index_after_known_baseline']=old+i+1
   super().save()
  def refresh_wallet(self,key):
   response=httpx.get('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+key},timeout=30)
   response.raise_for_status();body=response.json()
   balances=[float(x['total_balance']) for x in body.get('balance_infos',[]) if x.get('currency')=='CNY']
   if not balances:raise RuntimeError('CNY_BALANCE_UNAVAILABLE')
   self.wallet_balance=sum(balances);self.wallet_ledger_baseline=self.cost();self.wallet_checks+=1
   with (OUT/'wallet_checks.jsonl').open('a',encoding='utf-8') as f:
    f.write(json.dumps({'trajectory':name,'at_utc':datetime.now(timezone.utc).isoformat(),
      'balance_cny':self.wallet_balance,'new_p1_ledger_estimated_cny':self.wallet_ledger_baseline},ensure_ascii=False)+'\n')
  def transport(self,endpoint,key,payload,*args):
   with self.wallet_guard:
    prior_block=parent_ledger_block(manifest)
    if prior_block:self.block=prior_block;raise base.BudgetStop(self.block)
    if self.block:raise base.BudgetStop(self.block)
    if len(self.rows)>=request_cap:
     self.block='P1_BATCH_REQUEST_LIMIT';raise base.BudgetStop(self.block)
    if old+len(self.rows)>=int(manifest['cumulative_request_cap']):
     self.block='CUMULATIVE_REQUEST_LIMIT';raise base.BudgetStop(self.block)
    if self.used.get(name,0)>=life_cap:
     raise base.BudgetStop('TRAJECTORY_REQUEST_LIMIT')
    reserve=(self.input_limit*2+payload['max_tokens']*8)/1e6
    if self.cost()+reserve>cap:
     self.block='P1_INTERNAL_MONEY_GUARD';raise base.BudgetStop(self.block)
    if name.startswith('CP24_DIAG_') and sum(
      row['trajectory'].startswith('CP24_DIAG_') for row in self.rows
    )>=int(manifest['extra_diagnostic_requests_max']):
     self.block='P1_DIAGNOSTIC_REQUEST_LIMIT';raise base.BudgetStop(self.block)
    if self.wallet_balance is None or self.used.get(name,0)%5==0:
     try:self.refresh_wallet(key)
     except (httpx.HTTPError,KeyError,TypeError,ValueError,RuntimeError) as exc:
      self.block='WALLET_BALANCE_QUERY_FAILED';raise base.BudgetStop(self.block) from exc
    available=min(self.wallet_balance,self.wallet_balance-(self.cost()-self.wallet_ledger_baseline))
    if available-reserve<float(manifest['wallet_min_remaining_cny']):
     self.block='API_BALANCE_GUARD';raise base.BudgetStop(self.block)
    result=super().transport(endpoint,key,payload,*args)
    if self.rows and self.rows[-1].get('state')=='UNKNOWN_CHARGE':
     self.block='UNKNOWN_PROVIDER_CHARGE'
    elif result.get('http_status')==200:
     actual=(self.rows[-1].get('response') or {}).get('model')
     expected=manifest['expected_response_model_id']
     if actual!=expected:
      self.block='ROUTE_MODEL_MISMATCH'
      self.rows[-1]['model_contract']={'expected':expected,'actual':actual,'status':'MISMATCH'}
      self.save()
      raise base.BudgetStop(self.block)
    return result
 return Budget()

def reasoningbank_guidance():
 path=Path('/content/reasoning-bank-cp24/WebArena/prompts/memory_instruction.py')
 prompts=runpy.run_path(str(path))
 return (prompts['SUCCESSFUL_SI']+'\n'+prompts['FAILED_SI']).replace('web navigation','simulated business operation')+'\nAdaptation: only operation outcomes visible so far; not final task success. Unknown remains uncertain. Output memory_items objects in the supplied JSON schema.'

def expected_product_head(manifest):
  # P2 source snapshot restored in new Colab; historical Git object was
  # explicitly unverified in the frozen manifest.
  if manifest.get('historical_git_object_verified') is not False:
   return manifest['product_commit']
  receipt=json.loads((OUT/'RECOVERY_SOURCE.json').read_text(encoding='utf-8'))
  if receipt['original_product_reference']!=manifest['product_commit'] or receipt['product_import_reference']!=manifest['product_import_commit']:
   raise RuntimeError('RECOVERY_SOURCE_REFERENCE_MISMATCH')
  return receipt['recovery_head']

def run_life(arm,seed,phase='DEV',turn_cap=None,resume=False):
 if arm not in ('S-EVO','S-FRESH','S-NOEVO','B-RB'):raise ValueError('ARM_NOT_PRELOCKED')
 if phase not in ('DEV','DIAG','CONFIRM'):raise ValueError('PHASE_NOT_PRELOCKED')
 manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
 if manifest.get('freeze_pending') or not manifest.get('product_commit'):
  raise RuntimeError('PRODUCT_FREEZE_NOT_RECORDED')
 current=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 if current!=expected_product_head(manifest):raise RuntimeError('PRODUCT_COMMIT_DRIFT')
 status=subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).splitlines()
 if any(row!=' M experiments/cp24/RUN_MANIFEST.json' for row in status):
  raise RuntimeError('PRODUCT_SOURCE_DIRTY:'+repr(status[:5]))
 frozen=OUT/'RUN_MANIFEST_FROZEN.json'
 if frozen.exists():
  if frozen.read_bytes()!=MANIFEST.read_bytes():raise RuntimeError('RUN_MANIFEST_DRIFT')
 else:
  OUT.mkdir(parents=True,exist_ok=True);frozen.write_bytes(MANIFEST.read_bytes())
 check_public_dependencies()
 name=f'CP24_{phase}_{arm}_seed{seed}';dest=OUT/name
 global_block=preflight_global_limits(manifest,full_parent=True)
 if global_block:raise RuntimeError('PREEXISTING_BUDGET_BLOCK:'+global_block)
 budget=make_budget(name,int(manifest['extra_diagnostic_requests_max']) if phase=='DIAG' else 180)
 if budget.block:raise RuntimeError('PREEXISTING_BUDGET_BLOCK:'+budget.block)
 key=os.environ.get('DEEPSEEK_API_KEY','')
 if not key:raise RuntimeError('PROVIDER_KEY_MISSING')
 try:budget.refresh_wallet(key)
 except (httpx.HTTPError,KeyError,TypeError,ValueError,RuntimeError) as exc:
  raise RuntimeError('WALLET_BALANCE_QUERY_FAILED') from exc
 first_reserve=(256000*2+256000*8)/1e6
 if budget.wallet_balance-first_reserve<float(manifest['wallet_min_remaining_cny']):
  raise RuntimeError('API_BALANCE_GUARD')
 if resume:
  if not (dest/'identity.json').exists() or not (dest/'environment-private.json').exists():
   raise RuntimeError('RESUME_CHECKPOINT_MISSING')
  if (dest/'score.json').exists() and json.loads((dest/'score.json').read_text()).get('finalized'):
   raise RuntimeError('SEALED_RUN_CANNOT_RESUME')
 else:dest.mkdir(parents=True,exist_ok=False)
 transport=PublicMCP(dest,seed,resume=resume)
 cfg={**real_config(180),'tool_subject':True,'business_events':True,
      'tool_subject_arm':arm,'sec_learner':False,'provider_format_retry':False,
      'max_input_tokens':256000,'max_output_tokens':256000,
      'input_tokenizer_path':str(OUT/'tokenizer.json'),
      'reasoningbank_guidance':reasoningbank_guidance()}
 logical=[0]
 rt=Runtime(dest/'runtime',cfg,
  clock=lambda:(datetime(2026,1,5,tzinfo=timezone.utc)+timedelta(
   days=transport.last_day-1,minutes=transport.minute,microseconds=logical[0])).isoformat(),
  provider_transport=budget.transport)
 u=json.loads((dest/'identity.json').read_text())['user_id'] if resume else rt.provision(name,True)['user_id']
 if resume:logical[0]=state(rt,u).get('turn',0)
 rt.tool_budget=budget;rt.tool_budget_arm=name;attach(rt,u,transport)
 if not resume:
  write(dest/'identity.json',{'user_id':u,'arm':arm,'seed':seed,'phase':phase})
  write(dest/'config.json',{k:v for k,v in cfg.items() if k not in ('routes','gateway_key')})
  write(dest/'tools.json',transport.tools());(dest/'briefing.md').write_text(transport.briefing(),encoding='utf-8')
 start=time.time();outcome='running';errors=json.loads((dest/'errors.json').read_text()) if resume and (dest/'errors.json').exists() else []
 consecutive=0;stalled=0
 try:
  for turn in itertools.count(1):
   if turn_cap is not None and turn>turn_cap:outcome='diagnostic_turn_cap';break
   if transport.over():outcome='simulation_ended';break
   before_execution=len(rt.nodes(u,'ToolExecution'));before_clock=(transport.last_day,transport.minute)
   try:
    logical[0]+=1;rt.loop_round(u);consecutive=0
   except Exception as exc:
    errors.append({'turn':turn,'type':type(exc).__name__,'message':str(exc)})
    consecutive+=1;write(dest/'errors.json',errors)
    if type(exc).__name__=='BudgetStop' or budget.block or 'STATE_BINDING_ERROR' in str(exc):
     if str(exc)=='TRAJECTORY_REQUEST_LIMIT':outcome='trajectory_request_limit'
     elif budget.block=='API_BALANCE_GUARD':outcome='api_balance_stop'
     else:outcome='budget_or_runtime_stop'
     break
    if consecutive>=5:outcome='consecutive_runtime_errors';break
   stalled=stalled+1 if len(rt.nodes(u,'ToolExecution'))==before_execution and (transport.last_day,transport.minute)==before_clock else 0
   write(dest/'progress.json',{'day':transport.last_day,'turn':turn,'calls':budget.used.get(name,0),
    'batch_calls':len(budget.rows),'stalled_rounds':stalled,'wallet_last_observed_cny':budget.wallet_balance,
    'batch_estimated_cny':budget.cost(),'budget_block':budget.block})
   if stalled>=5:outcome='no_public_tool_progress';break
   print(name,'day',transport.last_day,'turn',turn,'calls',budget.used.get(name,0),flush=True)
  if transport.over():outcome='simulation_ended'
 finally:
  terminal=transport.over()
  score=transport.finalize() if terminal else transport._server.ENGINE.score(finalize=False)
  write(dest/'score.json',{'official':score,'harness_outcome':outcome,'elapsed_seconds':time.time()-start,
   'requests':budget.used.get(name,0),'errors':errors,'finalized':terminal,'budget_block':budget.block})
  write(dest/'subject_state.json',state(rt,u))
  rt.close();transport.close()
  source=sqlite3.connect(str(dest/'runtime/world.db'));backup=sqlite3.connect(str(dest/'world_consistent.db'))
  source.backup(backup);source.close()
  if backup.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('SQLITE_BACKUP_INVALID')
  kinds=[row[0] for row in backup.execute('select distinct node_type from subject_nodes_v200 where user_id=?',(u,))]
  for kind in kinds:
   write(dest/(kind+'.json'),[json.loads(x[0]) for x in backup.execute(
    'select data_json from subject_nodes_v200 where user_id=? and node_type=?',(u,kind))])
  backup.close()
 print('FINAL',name,json.dumps(score,ensure_ascii=False),flush=True)
 return dest

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('arm');parser.add_argument('seed',type=int)
 parser.add_argument('--phase',default='DEV');parser.add_argument('--resume',action='store_true')
 parser.add_argument('--turn-cap',type=int)
 args=parser.parse_args();run_life(args.arm,args.seed,args.phase,args.turn_cap,args.resume)
