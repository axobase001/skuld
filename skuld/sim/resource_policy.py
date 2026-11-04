"""Cheap reference: own observed conditional counts and bounded public lookahead."""
from functools import lru_cache

def choose(board):
 counts={}
 for e in board['results']:
  c=counts.setdefault(e['condition'],[1,2]);c[0]+=int(e['supplier_result']=='delayed');c[1]+=1
 known=board['open_responsibilities'];future=board['future_commitments'];tasks={t['object_id']:t for t in known+future};day=board['day'];end=day+3
 def risk(t):
  if 'condition' not in t:return .5
  a,b=counts.get(t['condition'],[1,2]);return a/b
 # Incremental avoidable losses only. Enumerate one legal manual change per day.
 @lru_cache(None)
 def solve(d,held):
  if d>end:return 0,('WAIT',None)
  matured=[t for t in tasks.values() if t['target_day']==d]
  loss=sum(0 if t['object_id']==held else risk(t)*t['miss_loss'] for t in matured)
  if held and tasks[held]['target_day']<=d:held=None
  acts=[('WAIT',None)]
  if held:acts.append(('RELEASE',held))
  else:acts += [('RESERVE',t['object_id']) for t in tasks.values() if t['release_day']<=d<=t['booking_deadline']]
  choices=[]
  for a,o in acts:
   h=held;fee=0
   if a=='RESERVE':h=o;fee=tasks[o]['reserve_fee']
   elif a=='RELEASE':h=None;fee=tasks[o]['cancel_fee']
   if d==end:
    tail=sum(0 if t['object_id']==h else risk(t)*t['miss_loss'] for t in tasks.values() if t['target_day']>end)
    value=loss+fee+tail
   else:value=loss+fee+solve(d+1,h)[0]
   choices.append((value,(a,o)))
  return min(choices,key=lambda x:(x[0],x[1][0]!='WAIT',str(x[1])))
 held=board['slot']['object_id'] if board['slot'] else None
 _,(action,obj)=solve(day,held)
 return action,obj
