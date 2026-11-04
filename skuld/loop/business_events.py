"""Public business observations, unique future windows and graph-based experience selection.
No simulator imports or hidden state. All agent-facing state is native subject graph data.
"""
import json,re,math
from typing import Literal
from pydantic import Field,ValidationError
from skuld.app.models import Strict
from skuld.kernel.primitives import dumps
from skuld.kernel.schemas import ModalState,AuthorityClass
class RuleProposal(Strict):
 target_type:Literal['inventory_zero_next_day','realized_units_next_day']
 product_id:str|None
 slot_id:str|None
 location:str|None
 quantity_min:int|None=Field(ge=0)
 quantity_max:int|None=Field(ge=0)
 price_min_eur:float|None=Field(default=None,ge=0)
 price_max_eur:float|None=Field(default=None,ge=0)
 horizon_days:Literal[1]
 action_assumptions:Literal['NO_RESTOCK_OR_REPLACEMENT','NO_RESTOCK_OR_REPLACEMENT_OR_REPRICE']
 probability:float|None=Field(default=None,ge=0,le=1)
 expected_units:float|None=Field(default=None,ge=0)
 parent_id:str|None
 evidence_ids:list[str]=Field(min_length=1,max_length=8)
 reason:str=Field(max_length=800)
def latest(rt,u,kind):
 out={}
 for n in rt.nodes(u,kind):
  k=n.semantic_identity_key
  if k not in out or (n.content.get('version',0),n.created_at,n.node_id)>(out[k].content.get('version',0),out[k].created_at,out[k].node_id):out[k]=n
 return list(out.values())
def entity(rt,u,role,key,src):
 ident='business-entity:'+role+':'+key;ns=rt.graph.nodes_by_identity(u,[ident])
 return ns[0] if ns else rt.node(u,'BusinessEntity',ident,{'role':role,'entity_id':key},src)
def canonical(rt,u,name):
 vals={n.content['product_id'] for n in latest(rt,u,'ProductAlias') if name.casefold() in (n.content['name'].casefold(),n.content['product_id'].casefold())}
 return next(iter(vals)) if len(vals)==1 else None
def alias(rt,u,pid,name,obs,src):
 rt.node(u,'ProductAlias','alias:'+pid+':'+name.casefold(),{'product_id':pid,'name':name,'evidence_id':obs.node_id},src)
 return entity(rt,u,'product',pid,src)
def tasks(rt,u):return [n for n in latest(rt,u,'Task') if n.content.get('business_task')]
def task(rt,u,key,fields,event,src):
 old=next((n for n in tasks(rt,u) if n.semantic_identity_key=='business-task:'+key),None)
 if old and old.content.get('business_status')=='FULFILLED' and fields.get('business_status')=='WAITING_EXTERNAL':return old
 c={**(old.content if old else {}),**fields,'business_task':True,'evidence_id':event.node_id,'version':(old.content.get('version',0) if old else 0)+1};c['status']='completed' if c['business_status']=='FULFILLED' else 'active'
 n=rt.node(u,'Task','business-task:'+key,c,src);e=entity(rt,u,fields.get('role','business'),key,src);rt.hyper(u,'RESPONSIBILITY_BUSINESS_EVENT',[('responsibility',n),('object',e),('evidence',event)],src)
 if c['status']=='completed':rt.store._conn.execute('DELETE FROM runtime_live_responsibilities WHERE user_id=? AND identity_key=?',(u,n.semantic_identity_key))
 return n
def emit(rt,u,obs,src,typ,key,fields,roles,coverage='EXPLICIT',effective=None):
 ents=[entity(rt,u,a,b,src) for a,b in roles];n=rt.node(u,'PublicEvent',key,{'event_id':key,'event_type':typ,'subject_id':u,'run_id':rt.runtime_id if hasattr(rt,'runtime_id') else u,'entity_refs':[{'role':a,'id':b} for a,b in roles],'observed_at':src.received_at,'effective_at':effective,'typed_fields':fields,'source_observation_id':obs.node_id,'source_spans':[fields.pop('_span')] if '_span' in fields else [{'start':0,'end':len(obs.content['text'])}],'public_coverage':coverage,'factual_status':'OBSERVED'},src,observed=True,authority=AuthorityClass.TOOL_OBSERVED)
 fact=rt.node(u,'Fact','business-fact:'+key,{'event_id':n.node_id,'fields':fields,'source_observation_id':obs.node_id,'object_bindings':roles},src,observed=True,authority=AuthorityClass.TOOL_OBSERVED)
 belief=rt.node(u,'Belief','business-belief:'+key,{'fact_id':fact.node_id,'event_id':n.node_id,'status':'SOURCE_REPORT'},src);rt.edge(u,'SUPPORTED_BY',belief,fact,src)
 rt.hyper(u,'PUBLIC_BUSINESS_EVIDENCE',[('event',n),('observation',obs),('fact',fact)]+[(a,e) for (a,_),e in zip(roles,ents)],src)
 return n

def ingest_sales_report(rt,u,obs,src,pre_call_day,post_call_day):
 args=obs.content['arguments'];text=obs.content['text'];location=str(args.get('location') or '').upper();events=[]
 if args.get('days')!=1 or not location:return events
 lines=text.splitlines();header=next((i for i,line in enumerate(lines) if line.strip()=='Sales for the last 1 day(s):'),None)
 if header is None:return events
 known_location=any(e.content['entity_id']==location and e.content['role']=='location' for e in rt.nodes(u,'BusinessEntity'))
 common_clock=pre_call_day is not None and post_call_day in (pre_call_day,pre_call_day+1)
 if args.get('by_product') is False:
  column=next((i for i in range(header+1,len(lines)) if re.fullmatch(r'\s*day\s+date\s+weather\s+units\s+revenue\s*',lines[i])),None)
  parsed=[];unparsed=[]
  if column is not None:
   for line in lines[column+1:]:
    if not line.strip():break
    m=re.fullmatch(r'\s*(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(.+?)\s+(\d+)\s+\u20ac([\d,]+\.\d{2})\s*',line)
    if not m:unparsed.append(line);continue
    parsed.append({'day':int(m[1]),'date':m[2],'weather':m[3].strip(),'units':int(m[4]),'revenue_eur':float(m[5].replace(',',''))})
  coverage='COMPLETE' if common_clock and known_location and column is not None and len(parsed)==1 and not unparsed and parsed[0]['day']==pre_call_day-1 else 'UNKNOWN'
  fields={'location':location,'pre_call_day':pre_call_day,'post_call_day':post_call_day,'report_day':parsed[0]['day'] if len(parsed)==1 else None,'report_date':parsed[0]['date'] if len(parsed)==1 else None,'total_units':parsed[0]['units'] if len(parsed)==1 else None,'total_revenue_eur':parsed[0]['revenue_eur'] if len(parsed)==1 else None,'coverage_status':coverage,'unparsed_rows':unparsed,'date_binding':'EXPLICIT_PUBLIC_DAILY_REPORT_ROW'}
  events.append(emit(rt,u,obs,src,'SALES_DAY_CERTIFICATE','sales-day-cert:'+obs.node_id,fields,[('location',location)],coverage=coverage))
  if coverage=='COMPLETE':
   current_order=(obs.content.get('turn',0),obs.content.get('action_index',0))
   complete_products={e.content['source_observation_id'] for e in rt.nodes(u,'PublicEvent') if e.content.get('event_type')=='SALES_REPORT_COVERAGE' and e.content['typed_fields'].get('coverage_status')=='COMPLETE'}
   for prior in rt.nodes(u,'ToolObservation'):
    pc=prior.content;pa=pc.get('arguments',{})
    if (pc.get('tool')!='get_sales_report' or pc.get('is_error') or pa.get('days')!=1 or pa.get('by_product') is not True or str(pa.get('location') or '').upper()!=location or pc.get('call_before_day')!=pre_call_day or (pc.get('turn',0),pc.get('action_index',0))>=current_order or prior.node_id in complete_products):continue
    prior_day=re.search(r'\[Day (\d+)/30',pc['text'])
    events.extend(ingest_sales_report(rt,u,prior,src,pre_call_day,int(prior_day[1]) if prior_day else None))
  return events
 if args.get('by_product') is not True:return events
 certificates=[e for e in rt.nodes(u,'PublicEvent') if e.content['event_type']=='SALES_DAY_CERTIFICATE' and e.content['typed_fields'].get('location')==location and e.content['typed_fields'].get('pre_call_day')==pre_call_day and e.content['typed_fields'].get('coverage_status')=='COMPLETE']
 certificate=certificates[-1] if certificates else None
 cert=certificate.content['typed_fields'] if certificate else {}
 column=next((i for i in range(header+1,len(lines)) if re.fullmatch(r'\s*product\s+units\s+revenue\s*',lines[i])),None)
 parsed={};unparsed=[];unknown_aliases=[]
 if column is not None:
  for line in lines[column+1:]:
   if not line.strip():break
   m=re.fullmatch(r'\s*(.+?)\s{2,}(\d+)\s+\u20ac([\d,]+\.\d{2})\s*',line)
   if not m:unparsed.append(line);continue
   pid=canonical(rt,u,m[1].strip())
   if pid is None:unknown_aliases.append(m[1].strip());continue
   if pid in parsed:unparsed.append(line);continue
   parsed[pid]={'units':int(m[2]),'revenue_eur':float(m[3].replace(',','')),'display_name':m[1].strip()}
 totals_agree=certificate is not None and sum(x['units'] for x in parsed.values())==cert.get('total_units') and abs(sum(x['revenue_eur'] for x in parsed.values())-cert.get('total_revenue_eur',-1))<.005
 coverage='COMPLETE' if common_clock and known_location and column is not None and not unparsed and not unknown_aliases and totals_agree else 'UNKNOWN'
 cover=emit(rt,u,obs,src,'SALES_REPORT_COVERAGE','sales-report:'+obs.node_id,{'location':location,'pre_call_day':pre_call_day,'post_call_day':post_call_day,'report_day':cert.get('report_day'),'report_date':cert.get('report_date'),'certificate_event_id':certificate.node_id if certificate else None,'requested_days':1,'by_product':True,'coverage_status':coverage,'reason':'PAIRED_PUBLIC_REPORT_TOTALS_MATCH' if coverage=='COMPLETE' else 'MISSING_OR_MISMATCHED_PUBLIC_DAY_CERTIFICATE_OR_ALIAS','unparsed_rows':unparsed,'unknown_aliases':unknown_aliases,'row_count':len(parsed),'date_binding':'EXPLICIT_PAIRED_PUBLIC_DAILY_REPORT'},[('location',location)],coverage=coverage);events.append(cover)
 if coverage!='COMPLETE':return events
 report_day=cert['report_day'];sales={}
 for pid,row in parsed.items():
  fields={'location':location,'product_id':pid,'report_day':report_day,'report_date':cert['report_date'],'pre_call_day':pre_call_day,'post_call_day':post_call_day,'units':row['units'],'revenue_eur':row['revenue_eur'],'display_name':row['display_name'],'coverage_event_id':cover.node_id,'certificate_event_id':certificate.node_id,'coverage_status':'COMPLETE','zero_semantics':'EXPLICIT_ROW'}
  event=emit(rt,u,obs,src,'REALIZED_PRODUCT_SALES','sales:'+location+':'+pid+':day'+str(report_day)+':'+obs.node_id,fields,[('location',location),('product',pid)],effective='day:'+str(report_day));events.append(event);sales[pid]=event
 for op in rt.nodes(u,'BusinessOpportunity'):
  if op.content['target_type']!='realized_units_next_day' or op.content['location']!=location or settled(rt,u,op.node_id):continue
  if report_day<op.content['target_day']:continue
  if report_day>op.content['target_day']:
   settle(rt,u,op,'UNKNOWN',None,cover,src,'TARGET_DAY_REPORT_NOT_OBSERVED',False);continue
  changed=sales_condition_change(rt,u,op)
  if changed:
   settle(rt,u,op,'CENSORED',None,changed,src,'CONDITION_CHANGED_BEFORE_TARGET_END',False);continue
  pid=op.content['product_id'];event=sales.get(pid)
  if event is None:
   fields={'location':location,'product_id':pid,'report_day':report_day,'report_date':cert['report_date'],'pre_call_day':pre_call_day,'post_call_day':post_call_day,'units':0,'revenue_eur':0.0,'display_name':None,'coverage_event_id':cover.node_id,'certificate_event_id':certificate.node_id,'coverage_status':'COMPLETE','zero_semantics':'ABSENT_FROM_COMPLETE_SINGLE_DAY_REPORT'}
   event=emit(rt,u,obs,src,'REALIZED_PRODUCT_SALES','sales:'+location+':'+pid+':day'+str(report_day)+':'+obs.node_id,fields,[('location',location),('product',pid)],effective='day:'+str(report_day));events.append(event)
  settle(rt,u,op,'RESOLVED',event.content['typed_fields']['units'],event,src,'COMPLETE_PAIRED_PUBLIC_SINGLE_DAY_REPORT',False)
 return events

def ingest(rt,u,obs,src):
 new_windows=[]
 c=obs.content;tool=c['tool'];text=c['text'];args=c['arguments'];clocks=rt.nodes(u,'BusinessClock');states=rt.nodes(u,'ToolSubjectState');pre_call_day=c.get('call_before_day') if c.get('call_before_day') is not None else clocks[-1].content['day'] if clocks else states[-1].content['day'] if states else None;match=re.search(r'\[Day (\d+)/30',text);post_call_day=int(match[1]) if match else None;day=pre_call_day if pre_call_day is not None and post_call_day in (pre_call_day,pre_call_day+1) else post_call_day if pre_call_day is None else None;body=text.split('\n\n[Day ')[0];events=[]
 if post_call_day is not None:rt.node(u,'BusinessClock','business-clock',{'day':post_call_day,'observed_at':src.received_at,'pre_call_day':pre_call_day},src)
 obj=None
 try:obj=json.loads(body)
 except (ValueError,TypeError):pass
 business_error=c['is_error'] or isinstance(obj,dict) and bool(obj.get('error')) or bool(re.search(r'^(?:Error:|Cannot |Unknown |Insufficient |Invalid |No matching)',body,re.I))
 # A rejected complaint may still have sent money. Record the bank effect separately.
 if tool=='send_payment':
  amount=re.search(r'Sent €([\d.]+) for (CMP-\d+)',body)
  if amount:
   key=args.get('reference','UNKNOWN');confirmed=bool(re.search(r'customer has confirmed the refund and withdrawn the complaint',body,re.I))
   ev=emit(rt,u,obs,src,'PAYMENT_RECEIPT','payment:'+obs.node_id,{'reference':key,'amount_eur':float(amount[1]),'cash_effect_eur':-float(amount[1]),'business_status':'REFUND_CONFIRMED' if confirmed else 'PAYMENT_WITHOUT_CONFIRMED_FULFILLMENT','day':day},[('complaint',key)]);events.append(ev)
   if confirmed:task(rt,u,amount[2],{'role':'complaint','object_id':amount[2],'text':'Refund '+amount[2],'business_status':'FULFILLED','paid_eur':float(amount[1]),'fulfilled_day':day},ev,src)
 if business_error:
  events.append(emit(rt,u,obs,src,'BUSINESS_REJECTED','rejection:'+obs.node_id,{'tool':tool,'status':'REJECTED'},[]));return events
 # Aliases only from public catalog, storage, or actual loading receipt.
 if tool in ('read_email','search_web'):
  for m in re.finditer(r'^([a-z0-9]+(?:-[a-z0-9]+)+):\s+([^\n]+)$',body,re.M):alias(rt,u,m[1],m[2].strip(),obs,src)
 if tool=='get_sales_report':events.extend(ingest_sales_report(rt,u,obs,src,pre_call_day,post_call_day))
 if tool=='get_storage_inventory':
  for m in re.finditer(r'^([a-z0-9-]+)\s+(.+?)\s+(small|large)\s+(\d+)\s+€([\d.]+)\s*$',body,re.M):
   pid,name,size,qty,cost=m.groups();alias(rt,u,pid,name.strip(),obs,src);events.append(emit(rt,u,obs,src,'DEPOT_QUANTITY',f'depot:{pid}:day{day}',{'product_id':pid,'quantity':int(qty),'unit':'units','cost_eur':float(cost),'day':day,'_span':{'start':m.start(),'end':m.end()}},[('product',pid)]))
 if tool=='order_goods' and isinstance(obj,dict) and obj.get('order_id') and 'paid' in obj:
  oid=obj['order_id'];ev=emit(rt,u,obs,src,'ORDER_ACCEPTED','order:'+oid,{'order_id':oid,'supplier_id':args.get('supplier_id'),'paid_eur':obj['paid'],'promised_day':obj.get('delivery_day'),'lines':obj.get('lines',[]),'day':day,'status':'ACCEPTED'},[('order',oid),('supplier',args['supplier_id'])]);events.append(ev);task(rt,u,oid,{'role':'order','object_id':oid,'text':'Track publicly reported delivery of '+oid,'business_status':'WAITING_EXTERNAL','promised_day':obj.get('delivery_day'),'lines':obj.get('lines',[]),'paid_eur':obj['paid']},ev,src)
 if tool=='read_email':
  m=re.search(r'Delivery for (PO-\d+) has been dropped at your depot',body)
  if m:
   oid=m[1];lines=[{'quantity':int(q),'name':n.strip(),'product_id':canonical(rt,u,n.strip())} for q,n in re.findall(r'^\s+(\d+) x (.+)$',body,re.M)];ev=emit(rt,u,obs,src,'DELIVERY_REPORTED','delivery:'+oid,{'order_id':oid,'lines':lines,'day_observed':day,'delivery_time':'UNKNOWN'},[('order',oid)],effective=None);events.append(ev)
   old=next((t for t in tasks(rt,u) if t.content.get('object_id')==oid),None);expected={x['product_id']:x['quantity'] for x in old.content.get('lines',[])} if old else {};got={x['product_id']:x['quantity'] for x in lines if x['product_id']};complete=bool(expected) and all(got.get(k,0)>=v for k,v in expected.items());task(rt,u,oid,{'role':'order','object_id':oid,'text':'Track publicly reported delivery of '+oid,'business_status':'FULFILLED' if complete else 'UNKNOWN','delivery_observation':obs.node_id,'delivery_time':'UNKNOWN','reported_lines':lines},ev,src)
  ids=re.findall(r'CMP-\d+',body)
  if ids and re.search(r'refund|charged|complaint',body,re.I):
   for cid in set(ids):
    old=next((t for t in tasks(rt,u) if t.content.get('object_id')==cid),None)
    if old and old.content.get('business_status')=='FULFILLED':continue
    ev=emit(rt,u,obs,src,'REFUND_REQUEST','complaint:'+cid,{'complaint_id':cid,'day':day,'status':'REPORTED'},[('complaint',cid)]);events.append(ev);task(rt,u,cid,{'role':'complaint','object_id':cid,'text':'Assess and fulfill public complaint '+cid,'business_status':'OPEN'},ev,src)
 if tool=='run_marketing':
  m=re.search(r'Simulated (prints|slack|mail) campaign at ([A-Z-]+); cost \u20ac([\d,.]+); traffic multiplier (\d+(?:\.\d+)?)(?=\.?(?:\s|$))',body)
  if m:events.append(emit(rt,u,obs,src,'MARKETING_CONFIRMED','marketing:'+obs.node_id,{'location':m[2],'channel':m[1],'cost_eur':float(m[3].replace(',','')),'traffic_multiplier_reported':float(m[4]),'day':day},[('location',m[2])]))
 if tool=='set_price':
  m=re.search(r'Slot ([\w:-]+) \((.+?)\) is now €(\d+(?:\.\d{1,2})?)',body)
  if m:events.append(emit(rt,u,obs,src,'SLOT_PRICE_CONFIRMED','price:'+obs.node_id,{'slot_id':m[1],'price_eur':float(m[3].replace(',','')),'day':day},[('slot',m[1])]))
 if tool in ('restock_machine','swap_item'):
  m=re.search(r'Loaded (\d+) x (.+?) into slot ([\w:-]+) \((\d+)/(\d+)\)',body)
  if m:
   alias(rt,u,args['product_id'],m[2],obs,src);ev=emit(rt,u,obs,src,'RESTOCK_CONFIRMED','restock:'+obs.node_id,{'slot_id':m[3],'product_id':args['product_id'],'loaded':int(m[1]),'quantity':int(m[4]),'capacity':int(m[5]),'day':day,'price_status':'NOT_SET' if 'has no price set' in body else 'UNCHANGED'},[('slot',m[3]),('product',args['product_id'])]);events.append(ev)
   for op in rt.nodes(u,'BusinessOpportunity'):
    if op.content['slot_id']==m[3] and not settled(rt,u,op.node_id):settle(rt,u,op,'CENSORED',None,ev,src,'INTERVENING_RESTOCK_OR_REPLACEMENT',False)
 if tool=='clear_slot' and re.search(r'(Returned \d+ x .* from slot|was already empty)',body):
  slot=args['slot'];events.append(emit(rt,u,obs,src,'SLOT_CLEARED','clear:'+obs.node_id,{'slot_id':slot,'product_id':None,'quantity':0,'price_eur':None,'day':day},[('slot',slot)]))
 if tool in ('restock_machine','swap_item') and 'already full' in body:
  m=re.search(r'Slot ([\w:-]+) is already full \((\d+) units\)',body)
  if m:events.append(emit(rt,u,obs,src,'SLOT_FULL_REPORTED','full:'+obs.node_id,{'slot_id':m[1],'quantity':int(m[2]),'day':day},[('slot',m[1])]))
 if tool=='get_machine_inventory' and day is not None:
  pending_by_slot={}
  for op in rt.nodes(u,'BusinessOpportunity'):
   if not settled(rt,u,op.node_id):pending_by_slot.setdefault(op.content['slot_id'],[]).append(op)
  matches=list(re.finditer(r'^([A-Z-]+:(?:SNACK|DRINKS):[A-D][1-3])\s+(small|large)\s+(.+?)\s+(\d+)\s+(\d+)\s+(€?[\d.]+|NOT SET|-)\s*$',body,re.M))
  if len(matches)!=len(re.findall(r'^[A-Z-]+:(?:SNACK|DRINKS):[A-D][1-3]\s',body,re.M)):raise ValueError('STATE_BINDING_ERROR: unsupported public inventory row format')
  for m in matches:
   slot,size,name,qty,cap,price=m.groups();pid=canonical(rt,u,name.strip());fields={'slot_id':slot,'location':slot.split(':')[0],'product_id':pid,'display_name':name.strip(),'quantity':int(qty),'capacity':int(cap),'unit':'units','price_eur':None if price in ('-','NOT SET') else float(price.lstrip('€')),'price_status':'UNBOUND' if price=='-' else 'NOT_SET' if price=='NOT SET' else 'OBSERVED','day':day,'_span':{'start':m.start(),'end':m.end()}}
   ev=emit(rt,u,obs,src,'SLOT_SNAPSHOT',f'slot:{slot}:day{day}',fields,[('slot',slot),('location',slot.split(':')[0])]+([('product',pid)] if pid else []));events.append(ev)
   for op in pending_by_slot.get(slot,[]):
    if day==op.content['target_day']:settle(rt,u,op,'RESOLVED' if pid==op.content['product_id'] else 'CENSORED',int(qty=='0') if pid==op.content['product_id'] else None,ev,src,'FIRST_MATCHING_DAY_SNAPSHOT' if pid==op.content['product_id'] else 'PRODUCT_CHANGED_OR_UNRESOLVED',False)
    elif day>op.content['target_day']:settle(rt,u,op,'UNKNOWN',None,ev,src,'NO_TARGET_DAY_OBSERVATION',False)
   if pid and day<30:new_windows.append(ev)
 if tool in ('get_machine_inventory','restock_machine','swap_item','clear_slot','get_sales_report') and day is not None:
  for c in rt.nodes(u,'BusinessCandidate'):rule_state(rt,u,c,src,day)
 if new_windows:
  cache={'candidates':rt.nodes(u,'BusinessCandidate'),'models':models(rt,u),'outcomes':[x.content['outcome'] for x in rt.nodes(u,'BusinessSettlement') if x.content['status']=='RESOLVED']}
  for ev in new_windows:issue(rt,u,ev,src,cache)
  issue_sales(rt,u,src,day)
 return events

def settled(rt,u,oid):return any(n.node_type=='BusinessSettlement' for n in rt.graph.nodes_by_identity(u,[oid]))
def models(rt,u):return {n.content['candidate_id']:n for n in latest(rt,u,'Hypothesis') if n.content.get('business_rule')}
def match_rule(rule,fields):
 target=fields.get('target_type','inventory_zero_next_day')
 if rule['target_type']!=target or rule['horizon_days']!=1:return False
 if any(rule.get(k) is not None and rule[k]!=fields.get(k) for k in ('product_id','slot_id','location')):return False
 q=fields.get('quantity')
 if not isinstance(q,int) or q<=0:return False
 if rule.get('quantity_min') is not None and q<rule['quantity_min']:return False
 if rule.get('quantity_max') is not None and q>rule['quantity_max']:return False
 price=fields.get('price_eur')
 if rule.get('price_min_eur') is not None or rule.get('price_max_eur') is not None:
  if not isinstance(price,(int,float)):return False
  if rule.get('price_min_eur') is not None and price<rule['price_min_eur']:return False
  if rule.get('price_max_eur') is not None and price>rule['price_max_eur']:return False
 return True

def executable(rule):
 d={k:v for k,v in rule.items() if k not in ('parent_id','evidence_ids','reason')};d.setdefault('price_min_eur',None);d.setdefault('price_max_eur',None)
 if d['target_type']=='inventory_zero_next_day':d.pop('expected_units',None)
 else:d.pop('probability',None)
 for k in ('quantity_min','price_min_eur'):
  if d.get(k)==0:d[k]=None
 return d

def verification_plan(rule):
 if rule['target_type']=='inventory_zero_next_day':
  return {'target_type':rule['target_type'],'tool':'get_machine_inventory','arguments':{},'read_on_day':'TARGET_DAY','coverage':'matching slot/product snapshot','invalidation':['restock_machine','swap_item'],'ordinary_tool_time':True}
 return {'target_type':rule['target_type'],'tool':'get_sales_report','day_certificate_arguments':{'days':1,'by_product':False,'location':rule['location']},'arguments':{'days':1,'by_product':True,'location':rule['location']},'requires_two_public_reads':True,'read_on_day':'TARGET_DAY_PLUS_ONE','coverage':'explicit dated daily public row paired with same-day product aggregate and matching totals; stable product alias and location required','invalidation':['restock_machine','swap_item','clear_slot','set_price','run_marketing'],'ordinary_tool_time':True}

def candidate_plan(candidate):
 plan=candidate.content.get('verification_plan')
 if plan is not None:return plan
 rule=candidate.content['rule']
 if rule['target_type']!='inventory_zero_next_day':raise ValueError('SALES_CANDIDATE_VERIFICATION_PLAN_MISSING')
 return {**verification_plan(rule),'legacy_derived_at_read':True}

def distinct_witness(rt,u,child,parent):
 # Candidate expressions, not their names or rationales, must differ on a legal input.
 snapshots=[e.content['typed_fields'] for e in latest(rt,u,'PublicEvent') if e.content['event_type']=='SLOT_SNAPSHOT']
 for observed in snapshots:
  base={**observed,'target_type':child['target_type']}
  if child['target_type']=='realized_units_next_day':base['slot_id']=None
  cap=max(1,int(observed.get('capacity') or 20))
  quantities={int(observed.get('quantity') or 1),1}
  for r in (child,parent):
   for k in ('quantity_min','quantity_max'):
    if r.get(k) is not None:quantities.update((max(1,int(r[k])),max(1,int(r[k])+1)))
  prices={observed.get('price_eur')}
  for r in (child,parent):
   for k in ('price_min_eur','price_max_eur'):
    if r.get(k) is not None:prices.update((float(r[k]),float(r[k])+0.01))
  for q in sorted(v for v in quantities if 0<v<=cap):
   for price in prices:
    if price is not None and price<0:continue
    f={**base,'quantity':q,'price_eur':price}
    left=child.get('probability') if match_rule(child,f) and child['target_type']=='inventory_zero_next_day' else child.get('expected_units') if match_rule(child,f) else None
    right=parent.get('probability') if match_rule(parent,f) and parent['target_type']=='inventory_zero_next_day' else parent.get('expected_units') if match_rule(parent,f) else None
    if left!=right:return {'feature':{k:f.get(k) for k in ('product_id','slot_id','location','quantity','price_eur','target_type')},'child_output':left,'parent_output':right}
 return None

def rule_state(rt,u,c,src,day):
 scores=[n for n in rt.nodes(u,'BusinessRuleScore') if n.content['candidate_id']==c.node_id]
 def loss(n,key):return n.content.get(key,n.content.get({'loss':'brier','reference_loss':'reference_brier','parent_loss':'parent_brier'}[key]))
 paired=[n for n in scores if loss(n,'reference_loss') is not None]
 common=paired if c.content['parent_id'] is None else [n for n in paired if loss(n,'parent_loss') is not None]
 clusters={rt.graph.get_node(u,n.content['settlement_id']).content['day_cluster'] for n in common}
 delta=sum(loss(n,'loss')-loss(n,'reference_loss') for n in common)/len(common) if common else None
 parent_delta=sum(loss(n,'loss')-loss(n,'parent_loss') for n in common)/len(common) if common and c.content['parent_id'] is not None else None
 eligible=len(common)>=4 and len(clusters)>=2
 gain=eligible and delta < -1e-12 and (c.content['parent_id'] is None or parent_delta is not None and parent_delta < -1e-12)
 old=models(rt,u).get(c.node_id)
 if gain:status='PUBLISHED';reason='COMMON_FUTURE_GAIN'
 elif len(common)>=8 and len(clusters)>=2:status='RETIRED_NO_GAIN';reason='EIGHT_COMMON_NO_GAIN'
 elif day-c.content['birth_day']>=7:status='DORMANT';reason='SEVEN_DAY_INSUFFICIENT_OR_WITHDRAWN'
 else:status='PENDING';reason='WAITING_FOR_COMMON_FUTURE_EVIDENCE'
 version=(old.content['version'] if old else 0)+1
 return rt.node(u,'Hypothesis','business-model:'+c.node_id,{'business_rule':True,'candidate_id':c.node_id,'version':version,'status':status,'status_reason':reason,'validation_count':len(scores),'paired_count':len(common),'day_clusters':len(clusters),'mean_paired_delta':delta,'mean_parent_delta':parent_delta,'loss_kind':'BRIER' if c.content['rule']['target_type']=='inventory_zero_next_day' else 'NORMALIZED_SQUARED_ERROR','rule':c.content['rule'],'verification_plan':candidate_plan(c),'evidence_score_ids':[n.node_id for n in scores[-8:]]},src,modal=ModalState.HYPOTHETICAL)

def admit(rt,u,proposals,sec,src,day,visible_ids=None):
 known=models(rt,u);born=[]
 for i,proposal in enumerate(proposals):
  try:rule=proposal.model_dump() if hasattr(proposal,'model_dump') else RuleProposal.model_validate(proposal).model_dump()
  except (ValidationError,KeyError,TypeError,ValueError) as exc:
   rt.node(u,'BusinessCandidateRejected',src.source_event_id+':'+str(i),{'index':i,'errors':['SCHEMA_INVALID:'+str(exc)[:300]],'proposal':proposal},src);continue
  errors=[]
  if rule['target_type']=='inventory_zero_next_day':
   if rule['probability'] is None:errors.append('BINARY_PROBABILITY_REQUIRED')
   if rule['expected_units'] is not None:errors.append('NUMERIC_VALUE_WRONG_TARGET')
   if rule['action_assumptions']!='NO_RESTOCK_OR_REPLACEMENT':errors.append('ACTION_CONTRACT_MISMATCH')
  else:
   if rule['expected_units'] is None:errors.append('SALES_EXPECTED_UNITS_REQUIRED')
   if rule['probability'] is not None:errors.append('BINARY_PROBABILITY_WRONG_TARGET')
   if rule['action_assumptions']!='NO_RESTOCK_OR_REPLACEMENT_OR_REPRICE':errors.append('ACTION_CONTRACT_MISMATCH')
   if not rule['product_id'] or not rule['location'] or rule['slot_id'] is not None:errors.append('SALES_REQUIRES_LOCATION_PRODUCT_SCOPE')
  if rule['quantity_max']==0:errors.append('ZERO_ONLY_SCOPE_IS_DETERMINISTIC_NOT_DEPLETION_LEARNING')
  if rule['quantity_min'] is not None and rule['quantity_max'] is not None and rule['quantity_min']>rule['quantity_max']:errors.append('EMPTY_SCOPE')
  if rule['price_min_eur'] is not None and rule['price_max_eur'] is not None and rule['price_min_eur']>rule['price_max_eur']:errors.append('EMPTY_PRICE_SCOPE')
  refs=[rt.graph.get_node(u,x) for x in rule['evidence_ids']]
  if visible_ids is not None and not set(rule['evidence_ids'])<=visible_ids:errors.append('EVIDENCE_NOT_IN_REQUEST')
  if any(n is None or n.node_type not in ('ToolObservation','PublicEvent','BusinessRuleScore','BusinessSettlement') for n in refs):errors.append('INVALID_EVIDENCE')
  parent=next((c for c in rt.nodes(u,'BusinessCandidate') if c.node_id==rule['parent_id']),None) if rule['parent_id'] else None
  if rule['parent_id'] and parent is None:errors.append('INVALID_PARENT')
  if rt.config.get('tool_subject_arm')=='S-FRESH' and rule['parent_id']:errors.append('FRESH_REQUIRES_INDEPENDENT_ROOT')
  for role in ('product_id','slot_id','location'):
   if rule[role] and not any(e.content['entity_id']==rule[role] for e in rt.nodes(u,'BusinessEntity')):errors.append('UNOBSERVED_SCOPE_'+role)
  functional=executable(rule);signature=json.dumps(functional,sort_keys=True,separators=(',',':'))
  existing=next((c for c in rt.nodes(u,'BusinessCandidate') if json.dumps(executable(c.content['rule']),sort_keys=True,separators=(',',':'))==signature),None)
  if existing and not errors:
   rt.node(u,'CandidateSupport','support:'+src.source_event_id+':'+str(i),{'candidate_id':existing.node_id,'sec_id':sec.node_id,'proposal':rule,'reason':'IDENTICAL_EXECUTABLE_MERGED'},src);continue
  related_parent_id=None;witness=None
  if parent and not errors:
   if parent.content['rule']['target_type']!=rule['target_type']:errors.append('PARENT_TARGET_MISMATCH')
   elif not overlapping(rule,parent.content['rule']):
    related_parent_id=parent.node_id;rule['parent_id']=None
   else:
    witness=distinct_witness(rt,u,functional,parent.content['rule'])
    if witness is None:errors.append('NO_DISTINCT_LEGAL_WITNESS')
  family=(rule['target_type'],rule['product_id'],rule['location'])
  live=[c for c in rt.nodes(u,'BusinessCandidate') if (c.content['rule']['target_type'],c.content['rule'].get('product_id'),c.content['rule'].get('location'))==family and known.get(c.node_id) and known[c.node_id].content['status']=='PENDING']
  if len(live)>=4:errors.append('PENDING_FAMILY_POOL_FULL')
  if errors:
   rt.node(u,'BusinessCandidateRejected',src.source_event_id+':'+str(i),{'index':i,'errors':errors,'proposal':rule},src);continue
  plan=verification_plan(functional)
  c=rt.node(u,'BusinessCandidate',src.source_event_id+':'+str(i),{'rule':functional,'parent_id':rule['parent_id'],'related_parent_id':related_parent_id,'reference_id':rule['parent_id'],'origin_sec_id':sec.node_id,'evidence_refs':rule['evidence_ids'],'semantic_signature':signature,'distinct_witness':witness,'verification_plan':plan,'birth_day':day,'birth_time':src.received_at,'training_cutoff':src.received_at,'validation_count_at_birth':0},src,modal=ModalState.HYPOTHETICAL)
  target=entity(rt,u,'target',rule['target_type'],src);roles=[('model',c),('target',target),('sec',sec)]+[('support',e) for e in refs]
  for k,role in [('product_id','product'),('slot_id','slot'),('location','location')]:
   if functional[k]:roles.append((role,entity(rt,u,role,functional[k],src)))
  if rule['parent_id']:roles.append(('parent',parent))
  if related_parent_id:roles.append(('related_parent',parent))
  rt.hyper(u,'BUSINESS_EXPERIENCE_MODEL',roles,src);rule_state(rt,u,c,src,day);born.append(c)
 if born:
  snapshots={}
  for e in latest(rt,u,'PublicEvent'):
   f=e.content['typed_fields']
   if e.content['event_type']=='SLOT_SNAPSHOT' and f.get('day')==day and f.get('product_id'):snapshots[f['slot_id']]=e
  for e in snapshots.values():issue(rt,u,e,src)
  issue_sales(rt,u,src,day)
 return born

def _issue_forecasts(rt,u,op,event,src,cache=None):
 if settled(rt,u,op.node_id):return
 f=op.content['feature_snapshot'];target=op.content['target_type']
 cache=cache or {'candidates':rt.nodes(u,'BusinessCandidate'),'models':models(rt,u)}
 ms=cache['models']
 if not isinstance(f.get('quantity'),int) or f['quantity']<=0:return
 applicable={c.node_id:c for c in cache['candidates'] if c.node_id in ms and match_rule(c.content['rule'],f) and ms[c.node_id].content['status'] not in ('DORMANT','ARCHIVED','RETIRED_NO_GAIN')}
 for cid,c in applicable.items():
  if rt.graph.nodes_by_identity(u,[op.node_id+':'+cid]):continue
  parent=next((x for x in cache['candidates'] if x.node_id==c.content['parent_id'] and match_rule(x.content['rule'],f)),None)
  history=[]
  for settlement in rt.nodes(u,'BusinessSettlement'):
   if settlement.content['status']!='RESOLVED' or settlement.created_at>=src.received_at:continue
   previous=rt.graph.get_node(u,settlement.content['opportunity_id']);pf=previous.content['feature_snapshot']
   if previous.content['target_type']!=target or previous.content['action_conditions']!=op.content['action_conditions']:continue
   if not match_rule(c.content['rule'],pf):continue
   if pf.get('price_status')!=f.get('price_status'):continue
   if target=='realized_units_next_day' and pf.get('price_eur')!=f.get('price_eur'):continue
   history.append(settlement)
  base={'candidate_id':cid,'candidate_version':ms[cid].content['version'],'opportunity_id':op.node_id,'parent_id':parent.node_id if parent else None,'information_cutoff':src.received_at,'target_day':op.content['target_day'],'object_binding':{'slot_id':f.get('slot_id'),'location':f.get('location'),'product_id':f.get('product_id'),'price_status':f.get('price_status'),'price_eur':f.get('price_eur'),'quantity':f['quantity']},'target_type':target,'verification_plan':candidate_plan(c)}
  if target=='inventory_zero_next_day':
   ref=(1+sum(h.content['outcome'] for h in history))/(2+len(history))
   base.update(p_issued=c.content['rule']['probability'],reference_id='MATCHED_BETA_1_1_OBSERVED_PREFIX',reference_p=ref,reference_version=len(history),reference_settlement_ids=[h.node_id for h in history],reference_status='NO_MATCHED_HISTORY' if not history else 'MATCHED_HISTORY',parent_p=parent.content['rule']['probability'] if parent else None,loss_kind='BRIER')
  else:
   ref=sum(h.content['outcome'] for h in history)/len(history) if history else None
   base.update(expected_units_issued=c.content['rule']['expected_units'],reference_id='MATCHED_OBSERVED_MEAN_PREFIX',reference_expected_units=ref,reference_version=len(history),reference_settlement_ids=[h.node_id for h in history],reference_status='NO_MATCHED_HISTORY' if not history else 'MATCHED_HISTORY',parent_expected_units=parent.content['rule']['expected_units'] if parent else None,normalizer=max(1,f['quantity']),loss_kind='NORMALIZED_SQUARED_ERROR')
  pred=rt.node(u,'BusinessForecast',op.node_id+':'+cid,base,src,modal=ModalState.CONDITIONAL)
  rt.hyper(u,'BUSINESS_FORECAST_TARGET',[('model',c),('forecast',pred),('opportunity',op),('evidence',event)],src)

def issue(rt,u,event,src,cache=None):
 f=event.content['typed_fields'];key=f"inventory:{f['slot_id']}:{f['product_id']}:day{f['day']+1}"
 existing=rt.graph.nodes_by_identity(u,['opportunity:'+key])
 op=existing[0] if existing else rt.node(u,'BusinessOpportunity','opportunity:'+key,{'event_key':key,'target_type':'inventory_zero_next_day','slot_id':f['slot_id'],'location':f['location'],'product_id':f['product_id'],'target_day':f['day']+1,'issued_at':src.received_at,'feature_snapshot':f,'information_cutoff':src.received_at,'action_conditions':'NO_RESTOCK_OR_REPLACEMENT','source_event_id':event.node_id},src,modal=ModalState.CONDITIONAL)
 _issue_forecasts(rt,u,op,event,src,cache)

def sales_features(rt,u,day):
 grouped={}
 for row in current_slots(rt,u,day):
  if row.get('quantity_status')!='OBSERVED' or row.get('quantity_confirmed_day')!=day or not row.get('product_id') or row.get('quantity') is None:continue
  key=(row['location'],row['product_id']);grouped.setdefault(key,[]).append(row)
 for (location,pid),rows in grouped.items():
  prices={r.get('price_eur') for r in rows};statuses={r.get('price_status') for r in rows}
  if len(prices)!=1 or statuses!={'OBSERVED'}:continue
  price=next(iter(prices))
  if price is None:continue
  yield {'target_type':'realized_units_next_day','location':location,'product_id':pid,'slot_id':None,'quantity':sum(r['quantity'] for r in rows),'price_eur':price,'price_status':'OBSERVED','day':day,'stocked_slot_ids':[r['slot_id'] for r in rows],'source_event_ids':[r['event_id'] for r in rows],'source_observation_ids':list(dict.fromkeys(r['observation_id'] for r in rows))}

def issue_sales(rt,u,src,day):
 if day>=29:return
 for f in sales_features(rt,u,day):
  if f['quantity']<=0:continue
  key=f"sales:{f['location']}:{f['product_id']}:day{day+1}"
  event=rt.graph.get_node(u,f['source_event_ids'][0]);existing=rt.graph.nodes_by_identity(u,['opportunity:'+key])
  op=existing[0] if existing else rt.node(u,'BusinessOpportunity','opportunity:'+key,{'event_key':key,'target_type':'realized_units_next_day','slot_id':None,'location':f['location'],'product_id':f['product_id'],'target_day':day+1,'report_read_day':day+2,'issued_at':src.received_at,'feature_snapshot':f,'information_cutoff':src.received_at,'action_conditions':'NO_RESTOCK_OR_REPLACEMENT_OR_REPRICE','source_event_id':event.node_id},src,modal=ModalState.CONDITIONAL)
  _issue_forecasts(rt,u,op,event,src)

def sales_condition_change(rt,u,op):
 f=op.content['feature_snapshot'];slots=set(f['stocked_slot_ids'])
 for event in rt.nodes(u,'PublicEvent'):
  if event.created_at<=op.created_at or event.content['event_type'] not in ('RESTOCK_CONFIRMED','SLOT_CLEARED','SLOT_PRICE_CONFIRMED','MARKETING_CONFIRMED'):continue
  x=event.content['typed_fields']
  if x.get('day') is None or x['day']>op.content['target_day']:continue
  if event.content['event_type']=='MARKETING_CONFIRMED' and x.get('location')==f['location']:return event
  slot=x.get('slot_id');pid=x.get('product_id')
  if slot in slots or slot and slot.split(':')[0]==f['location'] and pid==f['product_id']:return event
 return None

def settle(rt,u,op,status,y,event,src,reason,update=True):
 if settled(rt,u,op.node_id):return
 n=rt.node(u,'BusinessSettlement',op.node_id,{'opportunity_id':op.node_id,'event_key':op.content['event_key'],'status':status,'outcome':y,'outcome_time':src.received_at if status=='RESOLVED' else None,'evidence_ids':[event.node_id]+([event.content['typed_fields']['certificate_event_id']] if status=='RESOLVED' and op.content['target_type']=='realized_units_next_day' else []),'reason':reason,'day_cluster':op.content['target_day'],'target_type':op.content['target_type']},src)
 forecasts=rt.graph.nodes_by_identity(u,[op.node_id+':'+c.node_id for c in rt.nodes(u,'BusinessCandidate')])
 for pred in forecasts:
  if pred.node_type!='BusinessForecast':continue
  c=rt.graph.get_node(u,pred.content['candidate_id'])
  if status=='RESOLVED':
   if op.content['target_type']=='inventory_zero_next_day':
    p=pred.content['p_issued'];rp=pred.content['reference_p'];pl=(p-y)**2;rl=None if rp is None else (rp-y)**2;parent=pred.content.get('parent_p');pal=None if parent is None else (parent-y)**2
    fields={'brier':pl,'reference_brier':rl,'parent_brier':pal,'metric':'BRIER'}
    relation='BUSINESS_FUTURE_SUPPORT' if y==int(p>=.5) else 'BUSINESS_FUTURE_COUNTEREVIDENCE'
   else:
    scale=pred.content['normalizer'];p=pred.content['expected_units_issued'];rp=pred.content['reference_expected_units'];parent=pred.content.get('parent_expected_units');pl=((p-y)/scale)**2;rl=None if rp is None else ((rp-y)/scale)**2;pal=None if parent is None else ((parent-y)/scale)**2
    fields={'normalized_sq_error':pl,'reference_normalized_sq_error':rl,'parent_normalized_sq_error':pal,'metric':'NORMALIZED_SQUARED_ERROR','normalizer':scale}
    relation='BUSINESS_FUTURE_SUPPORT' if abs(p-y)<1e-12 else 'BUSINESS_FUTURE_COUNTEREVIDENCE'
   score=rt.node(u,'BusinessRuleScore',pred.node_id,{'candidate_id':c.node_id,'forecast_id':pred.node_id,'opportunity_id':op.node_id,'settlement_id':n.node_id,'target_type':op.content['target_type'],'loss':pl,'reference_loss':rl,'parent_loss':pal,'outcome':y,**fields},src)
   rt.hyper(u,relation,[('model',c),('forecast',pred),('settlement',n),('score',score)],src)
  if update:rule_state(rt,u,c,src,op.content['target_day'])

def current_slots(rt,u,day):
 slots={}
 for e in rt.nodes(u,'PublicEvent'):
  typ=e.content['event_type'];f=e.content['typed_fields'];slot=f.get('slot_id')
  if not slot or typ not in ('SLOT_SNAPSHOT','RESTOCK_CONFIRMED','SLOT_PRICE_CONFIRMED','SLOT_CLEARED','SLOT_FULL_REPORTED'):continue
  row=slots.setdefault(slot,{'slot_id':slot,'location':slot.split(':')[0],'product_id':None,'quantity':None,'price_eur':None,'quantity_status':'UNKNOWN','price_status':'UNKNOWN','state_version':0})
  row['state_version']+=1;row['event_id']=e.node_id;row['observation_id']=e.content['source_observation_id'];row['source_event_type']=typ
  observed=e.content['observed_at'];observed_day=f.get('day')
  if typ=='SLOT_SNAPSHOT':
   row.update({k:v for k,v in f.items() if k!='_span'});row['quantity_status']='OBSERVED';row['price_status']=f.get('price_status','UNBOUND' if f.get('product_id') is None else 'NOT_SET' if f['price_eur'] is None else 'OBSERVED')
   for k in ('quantity','price_eur','product_id'):
    row[k+'_source_event_id']=e.node_id;row[k+'_confirmed_at']=observed;row[k+'_confirmed_day']=observed_day
  elif typ=='RESTOCK_CONFIRMED':
   if row['product_id']!=f['product_id']:
    row['price_eur']=None;row['price_status']='UNKNOWN';row['price_eur_source_event_id']=e.node_id;row['price_eur_confirmed_at']=observed;row['price_eur_confirmed_day']=observed_day
   row['product_id']=f['product_id'];row['quantity']=f['quantity'];row['capacity']=f.get('capacity',row.get('capacity'));row['quantity_status']='OBSERVED'
   if f.get('price_status')=='NOT_SET':row['price_eur']=None;row['price_status']='NOT_SET';row['price_eur_source_event_id']=e.node_id;row['price_eur_confirmed_at']=observed;row['price_eur_confirmed_day']=observed_day
   for k in ('quantity','product_id'):row[k+'_source_event_id']=e.node_id;row[k+'_confirmed_at']=observed;row[k+'_confirmed_day']=observed_day
  elif typ=='SLOT_PRICE_CONFIRMED':
   row['price_eur']=f['price_eur'];row['price_status']='OBSERVED';row['price_eur_source_event_id']=e.node_id;row['price_eur_confirmed_at']=observed;row['price_eur_confirmed_day']=observed_day
  elif typ=='SLOT_CLEARED':
   row.update(product_id=None,quantity=0,price_eur=None,quantity_status='OBSERVED',price_status='UNBOUND')
   for k in ('quantity','price_eur','product_id'):row[k+'_source_event_id']=e.node_id;row[k+'_confirmed_at']=observed;row[k+'_confirmed_day']=observed_day
  elif typ=='SLOT_FULL_REPORTED':
   row['quantity']=f['quantity'];row['quantity_status']='OBSERVED';row['quantity_source_event_id']=e.node_id;row['quantity_confirmed_at']=observed;row['quantity_confirmed_day']=observed_day
 for row in slots.values():
  if row.get('quantity_confirmed_day') is not None and row['quantity_confirmed_day']<day and row['quantity_status']=='OBSERVED':
   row['last_known_quantity']=row['quantity'];row['quantity']=None;row['quantity_status']='STALE'
 return list(slots.values())

def scene(rt,u,src):
 clocks=rt.nodes(u,'BusinessClock');day=clocks[-1].content['day'] if clocks else 1
 for c in rt.nodes(u,'BusinessCandidate'):
  old=models(rt,u).get(c.node_id)
  if old and old.content['status']=='PENDING' and day-c.content['birth_day']>=7:rule_state(rt,u,c,src,day)
 events=latest(rt,u,'PublicEvent');inventory=current_slots(rt,u,day)
 snap={f['slot_id']:f for f in inventory if f['quantity_status']=='OBSERVED'};sales=list(sales_features(rt,u,day))
 goals=[t for t in tasks(rt,u) if t.content['business_status'] not in ('FULFILLED','FAILED')]
 targets={kind:entity(rt,u,'target',kind,src) for kind in ('inventory_zero_next_day','realized_units_next_day')}
 ms=models(rt,u);retrieved={};queried=[]
 for kind,target in targets.items():
  for h in rt.graph.find_hyperedges_by_participant(u,target.node_id):
   if h.relation_type!='BUSINESS_EXPERIENCE_MODEL':continue
   ids=[p.node_id for p in h.participants if p.role=='model']
   if len(ids)!=1:continue
   c=rt.graph.get_node(u,ids[0]);state=ms.get(c.node_id)
   if not state or state.content['status']!='PUBLISHED':continue
   pool=list(snap.values()) if kind=='inventory_zero_next_day' else sales
   matches=[e for e in pool if e.get('quantity_confirmed_day',day)==day and match_rule(c.content['rule'],e)]
   if not matches:continue
   queried.append(h.hyperedge_id)
   matched=list(dict.fromkeys(e['event_id'] if kind=='inventory_zero_next_day' else eid for e in matches for eid in ([e['event_id']] if kind=='inventory_zero_next_day' else e['source_event_ids'])))
   retrieved[c.node_id]={'node_id':c.node_id,'version':state.content['version'],'state_id':state.node_id,'status':state.content['status'],'rule':c.content['rule'],'target_type':kind,'expected_units':c.content['rule'].get('expected_units'),'verification_plan':candidate_plan(c),'paired_count':state.content['paired_count'],'mean_paired_delta':state.content['mean_paired_delta'],'loss_kind':state.content.get('loss_kind','BRIER' if kind=='inventory_zero_next_day' else 'NORMALIZED_SQUARED_ERROR'),'matched_event_ids':matched,'relation_id':h.hyperedge_id}
 selected=[]
 for kind in targets:
  selected+=sorted((x for x in retrieved.values() if x['target_type']==kind),key=lambda x:(x['mean_paired_delta'],x['node_id']))[:4]
 active=[];forecasts_by_op={}
 for forecast in rt.nodes(u,'BusinessForecast'):forecasts_by_op.setdefault(forecast.content['opportunity_id'],[]).append(forecast)
 for op in rt.nodes(u,'BusinessOpportunity'):
  if settled(rt,u,op.node_id):continue
  for forecast in forecasts_by_op.get(op.node_id,[]):
   candidate=rt.graph.get_node(u,forecast.content['candidate_id']);status=ms.get(candidate.node_id)
   if not status or status.content['status'] in ('DORMANT','ARCHIVED','RETIRED_NO_GAIN'):continue
   active.append({'candidate_id':candidate.node_id,'opportunity_id':op.node_id,'target_type':op.content['target_type'],'target_day':op.content['target_day'],'report_read_day':op.content.get('report_read_day'),'object_binding':forecast.content['object_binding'],'verification_plan':candidate_plan(candidate),'status':status.content['status']})
 active=sorted(active,key=lambda x:(x['report_read_day'] or x['target_day'],x['candidate_id']))[:8]
 record=rt.node(u,'BusinessScene',src.source_event_id,{'day':day,'goal_ids':[g.node_id for g in goals],'target_ids':[x.node_id for x in targets.values()],'slot_event_ids':[e['event_id'] for e in snap.values()],'queried_relations':queried,'input_exposed':[x['node_id'] for x in selected],'verification_opportunity_ids':[x['opportunity_id'] for x in active]},src)
 rt.hyper(u,'CURRENT_RESPONSIBILITY_SCENE',[('scene',record)]+[('target',x) for x in targets.values()]+[('responsibility',g) for g in goals],src)
 return {'scene_id':record.node_id,'day':day,'goal_ids':[g.node_id for g in goals],'responsibilities':[{'node_id':t.node_id,**t.content} for t in tasks(rt,u)],'transactions':[{'event_id':e.node_id,'event_type':e.content['event_type'],**e.content['typed_fields'],'observation_id':e.content['source_observation_id']} for e in events if e.content['event_type'] in ('ORDER_ACCEPTED','PAYMENT_RECEIPT','DELIVERY_REPORTED')],'current_inventory':inventory,'current_sales_features':sales,'verification_plans':active,'models':selected,'model_states':[{'node_id':v.node_id,**v.content} for v in ms.values()],'queried_relations':queried}


def overlapping(a,b):
 if a['target_type']!=b['target_type']:return False
 if any(a.get(k) is not None and b.get(k) is not None and a[k]!=b[k] for k in ('product_id','slot_id','location')):return False
 for lo,hi in (('quantity_min','quantity_max'),('price_min_eur','price_max_eur')):
  low=max(a.get(lo) or 0,b.get(lo) or 0)
  high=min(a.get(hi) if a.get(hi) is not None else float('inf'),b.get(hi) if b.get(hi) is not None else float('inf'))
  if low>high:return False
 return True

def table(rows):
 columns=list(dict.fromkeys(k for row in rows for k in row))
 return {'columns':columns,'rows':[[row.get(k) for k in columns] for row in rows]}

def inventory_table(rows):
 full=table(rows)
 if not full['rows']:return full
 columns=full['columns'];values=full['rows'];positions={name:i for i,name in enumerate(columns)}
 constants={name:values[0][i] for i,name in enumerate(columns) if all(row[i]==values[0][i] for row in values)}
 kept=[];same_as={}
 for name in columns:
  if name in constants:continue
  alias=next((prior for prior in kept if all(row[positions[name]]==row[positions[prior]] for row in values)),None)
  if alias is None:kept.append(name)
  else:same_as[name]=alias
 return {'columns':kept,'rows':[[row[positions[name]] for name in kept] for row in values],
         'constant_columns':constants,'same_as_columns':same_as,'row_count':len(values)}

def pack_context(ctx):
 # Reversible columnar representation: every source value is retained, no truncation.
 out=json.loads(json.dumps(ctx))
 # Superseded successful reads of the same public query are history, not additional
 # current facts. Preserve failed reads and every action receipt; retain all raw IDs.
 selected=[];seen=set();superseded=[]
 for record in reversed(out.get('recent_complete_observations',[])):
  c=record.get('content',{});key=(c.get('tool'),json.dumps(c.get('arguments',{}),sort_keys=True))
  replaceable=c.get('tool') in ('get_status','get_machine_inventory','get_storage_inventory','get_marketing_report') and not c.get('is_error')
  if replaceable and key in seen:superseded.append(record['node_id']);continue
  if replaceable:seen.add(key)
  selected.append(record)
 out['recent_complete_observations']=list(reversed(selected));out['superseded_read_observation_ids']=superseded
 # Do not serialize the same current inventory twice: retain all parsed row facts,
 # original header/footer and exact raw-observation backpointer.
 if 'business_state' in out:
  inventory=out['business_state'].get('current_inventory',[])
  for record in out.get('recent_complete_observations',[]):
   content=record.get('content',{});text=content.get('text','')
   if content.get('tool')!='get_machine_inventory':continue
   matches=list(re.finditer(r'^([A-Z-]+:(?:SNACK|DRINKS):[A-D][1-3])\s+(small|large)\s+(.+?)\s+(\d+)\s+(\d+)\s+(€?[\d.]+|NOT SET|-)\s*$',text,re.M))
   rows={x['slot_id']:x for x in inventory if x['observation_id']==record['node_id']}
   if not matches or len(rows)!=len(matches):continue
   if any(m[1] not in rows or rows[m[1]]['quantity']!=int(m[4]) or rows[m[1]]['display_name']!=m[3].strip() or rows[m[1]]['capacity']!=int(m[5]) or rows[m[1]]['price_eur']!=(None if m[6] in ('NOT SET','-') else float(m[6].lstrip('€'))) for m in matches):continue
   content.pop('text');content['original_header']=text[:matches[0].start()];content['original_footer']=text[matches[-1].end():]
   content['inventory_rows_reference']={'table':'business_state.current_inventory','where_observation_id':record['node_id'],'row_count':len(rows),'size_column':{m[1]:m[2] for m in matches}}
   content['raw_text_preserved_in_observation']=record['node_id']
 if 'business_state' in out:
  b=dict(out['business_state'])
  for key in ('current_inventory','responsibilities','transactions'):
   if isinstance(b.get(key),list):b[key]=inventory_table(b[key]) if key=='current_inventory' else table(b[key])
  out['business_state']=b
 # Identical order-line objects are shared by task and transaction projections.
 objects={};frequencies={}
 def collect(x):
  if isinstance(x,(dict,list)):
   k=json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'))
   if len(k)>200:frequencies[k]=frequencies.get(k,0)+1;objects[k]=x
   for v in (x.values() if isinstance(x,dict) else x):collect(v)
 collect(out);shared={k:'v'+str(i) for i,(k,n) in enumerate(frequencies.items()) if n>1}
 def share(x):
  if isinstance(x,(dict,list)):
   k=json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'))
   if k in shared:return {'shared_value_ref':shared[k]}
   return {k:share(v) for k,v in x.items()} if isinstance(x,dict) else [share(v) for v in x]
  return x
 out=share(out);out['shared_values']={v:objects[k] for k,v in shared.items()}
 counts={}
 def count(x):
  if isinstance(x,str) and re.fullmatch(r'[a-z][a-z0-9_]*_[a-f0-9]{24,}',x):counts[x]=counts.get(x,0)+1
  elif isinstance(x,list):
   for v in x:count(v)
  elif isinstance(x,dict):
   for v in x.values():count(v)
 count(out);refs={v:'@r'+str(i) for i,(v,n) in enumerate(counts.items()) if n>1}
 def encode(x):
  if isinstance(x,str):return refs.get(x,x)
  if isinstance(x,list):return [encode(v) for v in x]
  if isinstance(x,dict):return {k:encode(v) for k,v in x.items()}
  return x
 out=encode(out);out['reference_dictionary']={v:k for k,v in refs.items()}
 out['representation']='Lossless tables: columns name each row value. For business_state.current_inventory, each row is complete after copying each same_as_columns target from its source column and adding constant_columns; row_count is the total number of slots. shared_value_ref names the identical value in shared_values. @r references abbreviate full node IDs in reference_dictionary. Resolve them when reading; output citation fields must use the FULL node ID value from that dictionary, never the @r abbreviation. No fields or observations are truncated.'
 return out

def reflection_items(items):
 return items[:3],[{'index':i,'reason':'MEMORY_ITEM_LIMIT_3','item':x} for i,x in enumerate(items) if i>=3]

def learning_evidence(rt,u):
 opportunities={n.node_id:n for n in rt.nodes(u,'BusinessOpportunity')}
 settlements=rt.nodes(u,'BusinessSettlement');resolved=[n for n in settlements if n.content['status']=='RESOLVED']
 # All full events remain in the graph; SEC sees aggregate counts and recent exact citations.
 groups={}
 for n in resolved:
  op=opportunities[n.content['opportunity_id']];f=op.content['feature_snapshot'];kind=op.content['target_type'];key=(kind,f['product_id'],f['location'],f['quantity'])
  g=groups.setdefault(key,{'target_type':kind,'product_id':key[1],'location':key[2],'initial_quantity':key[3],'observed_count':0,'outcome_sum':0,'zero_count':0,'examples':[]})
  g['observed_count']+=1;g['outcome_sum']+=n.content['outcome'];g['zero_count']+=int(n.content['outcome']==0)
  g['examples'].append({'settlement_id':n.node_id,'target_day':op.content['target_day'],'outcome':n.content['outcome']})
 for g in groups.values():
  g['mean_observed_outcome']=g['outcome_sum']/g['observed_count']
  g['examples']=g['examples'][-8:]
 pending=[{'opportunity_id':op.node_id,'target_type':op.content['target_type'],'slot_id':op.content.get('slot_id'),'location':op.content.get('location'),'product_id':op.content['product_id'],'target_day':op.content['target_day'],'report_read_day':op.content.get('report_read_day')} for op in opportunities.values() if not settled(rt,u,op.node_id)]
 active=[];states=models(rt,u)
 for c in rt.nodes(u,'BusinessCandidate'):
  st=states.get(c.node_id)
  if st and st.content['status'] in ('PENDING','PUBLISHED'):
   active.append({'candidate_id':c.node_id,'parent_id':c.content['parent_id'],'target_type':c.content['rule']['target_type'],'status':st.content['status'],'validation_count':st.content['validation_count'],'verification_plan':candidate_plan(c)})
 return {'resolved_count':len(resolved),'unique_day_clusters':len({n.content['day_cluster'] for n in resolved}),'observed_groups':table(list(groups.values())),'pending_future_windows':table(pending[:24]),'pending_window_count':len(pending),'active_candidate_plans':active[:12],'censored_count':sum(n.content['status']=='CENSORED' for n in settlements),'unknown_count':sum(n.content['status']=='UNKNOWN' for n in settlements),'interpretation':'Observed public outcomes only. Sales are realized units, not latent demand. Days and slots are dependent. Examples are at most eight recent complete settlement IDs per group; all earlier full events persist in graph. Historical training evidence cannot validate newly born candidates. Follow-up uses ordinary public tools and time.'}


def render_context(ctx):
 return json.dumps(pack_context(ctx),ensure_ascii=False,separators=(',',':'))


def reflection_learning_view(learning):
 """Bound exact SEC examples without changing any all-event group statistic."""
 out=json.loads(json.dumps(learning))
 group=out['observed_groups'];example_column=group['columns'].index('examples') if group['rows'] else None
 before=0;after=0
 for row in group['rows']:
  examples=row[example_column];before+=len(examples)
  if not examples:continue
  latest=examples[-1]
  contrast=next((e for e in reversed(examples[:-1]) if e['outcome']!=latest['outcome']),None)
  row[example_column]=([contrast] if contrast else [])+[latest]
  after+=len(row[example_column])
 out['example_view']={'rule':'LATEST_AND_LATEST_DIFFERENT_OBSERVED_OUTCOME','max_per_group':2,
  'available_examples_before_view':before,'exact_examples_in_view':after,
  'all_resolved_events_in_group_statistics':True,
  'full_events_persisted_as':'BusinessSettlement with source-linked BusinessOpportunity'}
 return out


def render_reflection_context(ctx):
 # The prompt preserves complete recent experience content while representing
 # repeated outer keys once. Group examples stay exact but are bounded by the
 # fixed view; group counts, sums and means still cover every resolved event.
 out=json.loads(json.dumps(ctx))
 out['experiences']=table(out['experiences'])
 group=out['learning_evidence']['observed_groups'];example_column=group['columns'].index('examples') if group['rows'] else None
 for row in group['rows']:
  row[example_column]=[[e['settlement_id'],e['target_day'],e['outcome']] for e in row[example_column]]
 out['learning_evidence']['example_tuple_columns']=['settlement_id','target_day','outcome']
 out['learning_evidence_ids']={'reference':'learning_evidence.observed_groups rows, examples column, tuple element 0',
  'contract':'All and only full settlement_id strings in those visible tuples are legal learning-evidence references. Other complete experience IDs retain their existing contract.'}
 out['reflection_representation']='experiences is a lossless table: each row has the listed columns, including full node_id and content. learning_evidence example tuples follow example_tuple_columns; all group statistics include every resolved event, while the exact example view is fixed and bounded. Full prior events remain in the persistent graph.'
 return json.dumps(out,ensure_ascii=False,separators=(',',':'))
