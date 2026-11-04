"""CP24 product E2E contract, written before tool_subject changes.

Two isolated Runtime lives: raw public inventory then a partial update; and a
supposed information email whose actual response creates a paid order. Each
keeps its SQLite, complete model wire requests and tool/action receipts.
"""
import json, sys
from pathlib import Path
from skuld.app.runtime import Runtime
from skuld.sim.providers import offline_config, completion
from skuld.loop.tool_subject import attach

LOCATIONS=['AI-HOUSE','CITY-MART','EAST-MART','NORTH-MART','SOUTH-MART','WEST-MART']
def inv_rows(locations, quantity=5):
    return '\n'.join(f'{loc}:DRINKS:{col}{row} large  Cola 500ml    {quantity} 10 1.80'
                     for loc in locations for col in 'ABCD' for row in range(1,4))
class Transport:
    def __init__(self, mode): self.mode=mode;self.calls=[];self.day=1
    def tools(self):
        names=['get_machine_inventory','get_status','check_offers','send_email','order_goods','restock_machine']
        return [{'name':n,'description':'fixture public tool','parameters':{'type':'object'}} for n in names]
    def briefing(self): return 'Public tool E2E fixture, not economic-score evidence.'
    def over(self): return False
    def call(self,name,args):
        self.calls.append([name,args]);foot='\n\n[Day 1/30 — test 09:00 — balance €1,500.00]'
        if name=='get_machine_inventory':
            rows=inv_rows(LOCATIONS) if not args.get('machine_id') else inv_rows([args['machine_id']],7)
            return {'text':rows+foot,'is_error':False}
        if name=='check_offers':return {'text':'Public offer: supplier quote 20 Cola units at 1.00 each; total 20.00 EUR.'+foot,'is_error':False}
        if name=='send_email':return {'text':'Order PO-123 confirmed from this message. Paid €20.00.'+foot,'is_error':False}
        if name=='order_goods':return {'text':'{"order_id":"PO-124","paid":30.0,"delivery_day":3,"lines":[]}'+foot,'is_error':False}
        if name=='restock_machine':return {'text':'Cannot restock: insufficient depot inventory.'+foot,'is_error':False}
        return {'text':'Current balance €1480.00'+foot,'is_error':False}
class Model:
    def __init__(self,mode):self.mode=mode;self.requests=[]
    def __call__(self,endpoint,key,payload,*args):
        self.requests.append(payload);turn=len(self.requests)
        if self.mode=='inventory':
            actions=[{'tool':'get_machine_inventory','arguments':{}}] if turn==1 else ([{'tool':'get_machine_inventory','arguments':{'machine_id':LOCATIONS[0]}}] if turn==2 else [])
        elif self.mode=='stagnant':
            actions=[{'tool':'restock_machine','arguments':{'slot':'AI-HOUSE:DRINKS:A1','product_id':'cola-500ml','quantity':5},'assumption_refs':{'quantity':5}}]
        else:
            if turn==1:actions=[{'tool':'check_offers','arguments':{'supplier_id':'supplier','quantity':20}}]
            elif turn==2:
                ctx=task(payload);ref=ctx['recent_complete_observations'][-1]['node_id'];ref=ctx.get('reference_dictionary',{}).get(ref,ref)
                actions=[{'tool':'send_email','arguments':{'to':'supplier@example.test','subject':'request quote','body':'Please quote 20 cases'},'transaction_contract':{'object_id':'supplier@example.test','quantity':20,'max_cost_eur':25,'source_observation_id':ref}}, {'tool':'order_goods','arguments':{'supplier_id':'supplier','items':{'cola-500ml':20}},'transaction_contract':{'object_id':'supplier','quantity':20,'max_cost_eur':25,'source_observation_id':ref}}, {'tool':'get_status','arguments':{}}]
            elif turn==3:actions=[{'tool':'restock_machine','arguments':{'slot':'AI-HOUSE:DRINKS:A1','product_id':'cola-500ml','quantity':5}}]
            elif turn==4:
                ctx=task(payload);ref=next(r['node_id'] for r in ctx['recent_complete_observations'] if r['content']['tool']=='check_offers');ref=ctx.get('reference_dictionary',{}).get(ref,ref)
                actions=[{'tool':'order_goods','arguments':{'supplier_id':'supplier','items':{'cola-500ml':20}},'transaction_contract':{'object_id':'supplier','quantity':20,'max_cost_eur':25,'source_observation_id':ref}}]
            else:actions=[]
        return completion({'actions':actions,'ledger':'fixture ledger','plan':'fixture plan','predictions':[],'responsibilities':[],'reason':'fixture','used_evidence_ids':[],'used_candidate_ids':[],'responsibility_ids':[]})
def task(payload):
    for m in payload['messages']:
        try:x=json.loads(m['content'])
        except (ValueError,TypeError):continue
        t=x.get('task')
        if isinstance(t,str):
            try:t=json.loads(t)
            except ValueError:continue
        if isinstance(t,dict) and t.get('stage')=='PUBLIC_TOOL_DECIDE':return t
    raise AssertionError('NO_ACTUAL_DECIDE_TASK')
def run(out,mode):
    dest=out/mode;dest.mkdir();transport=Transport(mode);model=Model(mode)
    cfg={**offline_config(20),'tool_subject':True,'business_events':True,'tool_subject_arm':'S-EVO','sec_learner':False,'provider_format_retry':False,'max_input_tokens':32768,'max_output_tokens':6144,'input_tokenizer_path':'/content/cp24_tokenizer.json'}
    rt=Runtime(dest/'runtime',cfg,provider_transport=model);u=rt.provision(mode,True)['user_id'];attach(rt,u,transport)
    rounds=3 if mode!='side_effect' else 4
    for _ in range(rounds):rt.loop_round(u)
    (dest/'requests.json').write_text(json.dumps(model.requests,ensure_ascii=False,indent=2))
    (dest/'tools.json').write_text(json.dumps(transport.calls,ensure_ascii=False,indent=2))
    (dest/'receipts.json').write_text(json.dumps({kind:[n.content for n in rt.nodes(u,kind)] for kind in ('ToolExecution','ToolPendingReplan','ToolSideEffect','ToolDecision')},ensure_ascii=False,indent=2))
    if mode=='inventory':
        req=task(model.requests[2]);table=req['business_state']['current_inventory']
        assert table['row_count']==72,table.get('row_count')
        assert all('event_id' in table['columns'] or 'event_id' in table.get('same_as_columns',{}) for _ in [0])
        assert len(json.dumps(model.requests[2],ensure_ascii=False))<120000
        assert 'raw_text_preserved_in_observation' in json.dumps(req), 'MISSING_SOURCE_POINTER'
        assert all(not (r.get('content',{}).get('tool')=='get_machine_inventory' and r.get('content',{}).get('text','').count(':DRINKS:')>12) for r in req['recent_complete_observations']), 'OLD_FULL_RAW_INVENTORY_DUPLICATED'
    elif mode=='stagnant':
        assert len(model.requests)==2,len(model.requests)
        assert not transport.calls,transport.calls
        assert rt.nodes(u,'ToolRoundDeferred')
        assert rt.nodes(u,'LoopReceipt')[-1].content['decisions'][0]['kind']=='PUBLIC_TOOL_DEFERRED'
    else:
        names=[x[0] for x in transport.calls]
        assert names==['check_offers','send_email','get_status','restock_machine','order_goods'],names
        side=rt.nodes(u,'ToolSideEffect');assert side and side[0].content['effect_type']=='PAID_ORDER',side
        pending=rt.nodes(u,'ToolPendingReplan');assert any(n.content['action']['tool']=='order_goods' for n in pending)
        executions=rt.nodes(u,'ToolExecution');reject=next(e for e in executions if rt.graph.get_node(u,e.content['observation_id']).content['tool']=='restock_machine');assert reject.content['business_rejected'] and reject.content['success'] is False
        assert any(n.content.get('event_type')=='BUSINESS_REJECTED' for n in rt.nodes(u,'PublicEvent'))
        over=executions[-1];assert over.content['success'] is False and over.content['side_effect_id']
        effect=rt.graph.get_node(u,over.content['side_effect_id']);assert effect.content['limit_breached'] and effect.content['amount_eur']==30.0
    rt.close()
    return {'actual_model_requests':len(model.requests),'public_tool_calls':len(transport.calls),'mode':mode}
if __name__=='__main__':
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
    checks=[]
    for mode in ('inventory','side_effect','stagnant'):
        checks.append(run(out,mode))
    (out/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
    print('CP24_TOOL_RUNTIME_E2E_PASS',checks)
