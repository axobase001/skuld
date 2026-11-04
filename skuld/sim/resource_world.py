"""Isolated one-slot business simulator. Only snapshot is exposed to the product."""
import copy,json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class ResourceWorld:
 def __init__(self,tasks):
  self.tasks=copy.deepcopy(tasks);self.day=-1;self.slot=None;self.state_version=0;self.manual_day=None;self.results=[];self.actions=[];self.costs=dict(reservation=0,cancellation=0,miss=0);self.lock=threading.RLock()
 def advance(self,day):
  with self.lock:
   if day<self.day:raise ValueError('clock reversal')
   for t in sorted(self.tasks,key=lambda t:t['target_day']):
    if self.day<t['target_day']<=day:
     backed=bool(self.slot and self.slot['object_id']==t['object_id']);on_time=backed or t['supplier_result']=='ready';miss=0 if on_time else t['miss_loss'];self.costs['miss']+=miss
     self.results.append({**t,'observed_day':t['target_day'],'backup_effective':backed,'on_time':on_time,'miss_cost':miss})
     if backed:self.slot=None
     self.state_version+=1
   if day!=self.day:self.day=day;self.state_version+=1
 def snapshot(self):
  with self.lock:
   active=[{k:v for k,v in t.items() if k!='supplier_result'} for t in self.tasks if t['release_day']<=self.day<t['target_day']]
   future=[{k:v for k,v in t.items() if k not in ('supplier_result','condition')} for t in self.tasks if self.day<t['release_day']<=self.day+2]
   legal=[dict(action='WAIT',object_id=None)]
   if self.manual_day!=self.day:
    if self.slot:legal.append(dict(action='RELEASE',object_id=self.slot['object_id']))
    else:legal += [dict(action='RESERVE',object_id=t['object_id']) for t in active if self.day<=t['booking_deadline']]
   return copy.deepcopy(dict(day=self.day,state_version=self.state_version,slot=self.slot,open_responsibilities=active,future_commitments=future,commitments=future,legal_actions=legal,results=self.results,actions=self.actions,costs=self.costs))
 def apply(self,action,obj,version):
  with self.lock:
   before=self.snapshot();why=None
   if version!=self.state_version:why='STALE_STATE_VERSION'
   elif dict(action=action,object_id=obj) not in before['legal_actions']:why='ILLEGAL_ACTION_OR_DEADLINE_OR_DAILY_LIMIT'
   if why:return dict(ok=False,error=why,state_version=self.state_version)
   record=dict(day=self.day,action=action,object_id=obj,state_version=version,before_slot=copy.deepcopy(self.slot),fee=0)
   if action!='WAIT':
    t=next(t for t in self.tasks if t['object_id']==obj);self.manual_day=self.day
    if action=='RESERVE':
     record['fee']=t['reserve_fee'];self.costs['reservation']+=record['fee'];self.slot=dict(object_id=obj,release_day=t['target_day'],reserved_day=self.day)
    else:record['fee']=t['cancel_fee'];self.costs['cancellation']+=record['fee'];self.slot=None
    self.state_version+=1
   record['after_slot']=copy.deepcopy(self.slot);self.actions.append(record)
   return dict(ok=True,effect=record,state_version=self.state_version)
class ResourceServer:
 def __init__(self,world):
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def answer(self,x):
    b=json.dumps(x).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
   def do_GET(self):self.answer(world.snapshot())
   def do_POST(self):
    v=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.answer(world.apply(v['action'],v['object_id'],v['state_version']))
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.url='http://127.0.0.1:'+str(self.server.server_port);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
 def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
