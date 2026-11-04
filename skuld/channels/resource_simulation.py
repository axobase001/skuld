"""Explicitly authorized isolated simulation operations, not real procurement."""
import json,httpx
from urllib.parse import urlparse
from .base import PolledChannel
class ResourceSimulationChannel(PolledChannel):
 kind='resource_simulation';OPERATIONS={'READ':0,'RESERVE':1,'RELEASE':1}
 @classmethod
 def check_config(cls,runtime,config):
  u=urlparse(config['url'])
  if u.scheme!='http' or u.hostname!='127.0.0.1':raise ValueError('simulation requires loopback HTTP')
  return dict(config)
 def sample(self):
  res=httpx.get(self.config['url'],timeout=10);res.raise_for_status()
  return {'board':(res.json(),self.runtime.clock(),{'simulation':True,'url':self.config['url']})}
 def check_operation(self,operation,parameters):
  if operation not in ('RESERVE','RELEASE') or set(parameters)!={'object_id','state_version'} or not isinstance(parameters['object_id'],str) or type(parameters['state_version'])!=int:raise ValueError('invalid simulation action parameters')
 def perform(self,operation,parameters,content):
  self.check_operation(operation,parameters);res=httpx.post(self.config['url'],json={'action':operation,**parameters},timeout=10);res.raise_for_status();return res.json()
