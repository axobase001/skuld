"""Native Runtime E2E for the actual CP24 S-FRESH turn75 public receipt.
Written and run RED before the product parser change. No paid API calls.
"""
import json,sys
from pathlib import Path
from skuld.app.runtime import Runtime
from skuld.sim.providers import offline_config,completion
from skuld.loop.tool_subject import attach

FIXTURE=Path(__file__).parent/'evidence/marketing_turn75_public_response.txt'
class Transport:
    last_day=20
    minute=765
    def __init__(self,text):self.text=text;self.calls=[]
    def tools(self):return [{'name':'run_marketing','description':'public simulated marketing tool','parameters':{'type':'object'}}]
    def briefing(self):return 'E2E public-tool receipt fixture; no private world or scoring.'
    def over(self):return False
    def call(self,name,args):
        self.calls.append({'tool':name,'arguments':args,'text':self.text,'is_error':False})
        return {'text':self.text,'is_error':False}
class Model:
    def __init__(self):self.requests=[]
    def __call__(self,endpoint,key,payload,*args):
        self.requests.append(payload)
        outer=json.loads(next(m['content'] for m in payload['messages'] if m['role']=='user'))
        task=json.loads(outer['task'])
        if task['stage']=='PUBLIC_TOOL_REFLECT':
            return completion({'attribution':'fixture: marketing receipt only','evidence_ids':[],'memory_items':[],'candidates':[]})
        assert task['stage']=='PUBLIC_TOOL_DECIDE'
        return completion({'actions':[{'tool':'run_marketing','arguments':{'channel':'slack','location':'MAIN-LOUNGE','message':'Fresh snacks and cold drinks today'}}],
            'ledger':'fixture','plan':'fixture','predictions':[],'responsibilities':[],'reason':'fixture',
            'used_evidence_ids':[],'used_candidate_ids':[],'responsibility_ids':[]})
def run(root,name,text,expect_event):
    dest=root/name;dest.mkdir();transport=Transport(text);model=Model()
    cfg={**offline_config(10),'tool_subject':True,'business_events':True,'tool_subject_arm':'S-FRESH',
        'sec_learner':False,'provider_format_retry':False,'max_input_tokens':32768,'max_output_tokens':6144,
        'input_tokenizer_path':'/content/cp03/tokenizer.json'}
    rt=Runtime(dest/'runtime',cfg,provider_transport=model)
    u=rt.provision(name,True)['user_id'];attach(rt,u,transport)
    try:
        rt.loop_round(u)
        attempts=rt.nodes(u,'ToolAttempt');observations=rt.nodes(u,'ToolObservation');executions=rt.nodes(u,'ToolExecution')
        events=[e for e in rt.nodes(u,'PublicEvent') if e.content.get('event_type')=='MARKETING_CONFIRMED']
        assert len(transport.calls)==1 and len(attempts)==len(observations)==len(executions)==1
        assert executions[0].content['observation_id']==observations[0].node_id
        assert observations[0].content['text']==text
        assert len(events)==int(expect_event)
        if expect_event:assert events[0].content['typed_fields']['traffic_multiplier_reported']==1.15
        (dest/'checks.json').write_text(json.dumps({'scenario':name,'model_requests':len(model.requests),'public_calls':len(transport.calls),
            'marketing_events':len(events),'complete_execution':True},indent=2))
    except Exception as exc:
        (dest/'red_failure.json').write_text(json.dumps({'scenario':name,'exception_type':type(exc).__name__,'exception':str(exc),
            'attempts':len(rt.nodes(u,'ToolAttempt')),'observations':len(rt.nodes(u,'ToolObservation')),
            'executions':len(rt.nodes(u,'ToolExecution'))},indent=2))
        raise
    finally:
        (dest/'requests.json').write_text(json.dumps(model.requests,ensure_ascii=False,indent=2))
        (dest/'tools.json').write_text(json.dumps(transport.calls,ensure_ascii=False,indent=2))
        rt.close()
if __name__=='__main__':
    root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
    text=FIXTURE.read_text()
    run(root,'real_sentence_period',text,True)
    run(root,'malformed_two_periods',text.replace('traffic multiplier 1.15.','traffic multiplier 1.15..'),False)
    print('MARKETING_RECEIPT_E2E_PASS')
