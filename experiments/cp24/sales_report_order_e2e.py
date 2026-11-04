"""Runtime E2E for CP24's saved public report receipts. Written before product edit."""
import json
import sys
from pathlib import Path
from skuld.app.runtime import Runtime
from skuld.sim.providers import offline_config, completion
from skuld.loop.tool_subject import attach

EVIDENCE = Path(__file__).parent / 'evidence'
PRODUCT = (EVIDENCE / 'sales_product_day25_real.txt').read_text(encoding='utf-8')
CERT = (EVIDENCE / 'sales_day_cert_day25_real.txt').read_text(encoding='utf-8')
RESTOCK = (EVIDENCE / 'restock_sparkling_day24_real.txt').read_text(encoding='utf-8')
ALIASES = ('energy-drink-500ml: Energy Drink 500ml\n'
           'cola-500ml: Cola 500ml\n'
           'oat-latte-can: Oat Latte Can 250ml\n'
           'sparkling-water-500ml: Sparkling Water 500ml')

def stage(payload):
    outer = json.loads(next(m['content'] for m in payload['messages'] if m['role'] == 'user'))
    return json.loads(outer['task'])

class Transport:
    def __init__(self, scenario):
        self.scenario = scenario
        self.phase = 'seed'
        self.day = 23
        self.last_day = 23
        self.minute = 540
        self.calls = []
    def tools(self):
        return [{'name': name, 'description': 'fixed public-tool E2E fixture',
                 'parameters': {'type': 'object'}} for name in
                ('read_email', 'get_machine_inventory', 'get_sales_report', 'restock_machine')]
    def briefing(self):
        return 'Fixed public receipts from the old S-EVO seed1 life; no hidden world state.'
    def over(self):
        return False
    def call(self, name, args):
        self.calls.append({'phase': self.phase, 'tool': name, 'arguments': args})
        if name == 'read_email':
            body = ALIASES + '\n\n[Day 23/30 - fixture 09:00 - balance EUR 1500]'
        elif name == 'get_machine_inventory':
            body = ('AI-HOUSE:DRINKS:A2 large Sparkling Water 500ml 5 10 \u20ac1.80'
                    '\n\n[Day 23/30 - fixture 09:25 - balance EUR 1500]')
        elif name == 'restock_machine':
            body = RESTOCK
        else:
            assert name == 'get_sales_report' and args['days'] == 1
            if args['by_product']:
                body = PRODUCT
            else:
                body = CERT
                if self.scenario == 'wrong_date':
                    body = body.replace('  24 2026-01-28', '  23 2026-01-28', 1)
                if self.scenario == 'wrong_total':
                    body = body.replace('  24 2026-01-28  cold         27',
                                        '  24 2026-01-28  cold         28', 1)
        return {'text': body, 'is_error': False}

class Model:
    def __init__(self, transport):
        self.t = transport
        self.requests = []
    def __call__(self, endpoint, key, payload, *args):
        self.requests.append(payload)
        task = stage(payload)
        if task['stage'] == 'PUBLIC_TOOL_REFLECT':
            return completion({'attribution': 'Public receipt fixture only', 'evidence_ids': [],
                               'memory_items': [], 'candidates': []})
        assert task['stage'] == 'PUBLIC_TOOL_DECIDE'
        if self.t.phase == 'seed':
            actions = [{'tool': 'read_email', 'arguments': {}},
                       {'tool': 'get_machine_inventory', 'arguments': {}}]
        elif self.t.phase == 'restock':
            actions = [{'tool': 'restock_machine', 'arguments':
                        {'product_id': 'sparkling-water-500ml', 'quantity': 10,
                         'slot': 'AI-HOUSE:DRINKS:A2'}}]
        else:
            cert_loc = 'MAIN-LOUNGE' if self.t.scenario == 'wrong_location' else 'AI-HOUSE'
            cert = {'tool': 'get_sales_report', 'arguments':
                    {'days': 1, 'by_product': False, 'location': cert_loc}}
            product = {'tool': 'get_sales_report', 'arguments':
                       {'days': 1, 'by_product': True, 'location': 'AI-HOUSE'}}
            actions = [cert, product] if self.t.scenario == 'forward' else [product, cert]
        return completion({'actions': actions, 'ledger': 'fixed receipt fixture',
                           'plan': 'read the two public reports', 'predictions': [],
                           'responsibilities': [], 'reason': 'check the saved receipts',
                           'used_evidence_ids': [], 'used_candidate_ids': [],
                           'responsibility_ids': task['business_state']['goal_ids']})

def run(root, scenario):
    dest = root / scenario
    dest.mkdir()
    t = Transport(scenario)
    m = Model(t)
    cfg = {**offline_config(20), 'tool_subject': True, 'business_events': True,
           'tool_subject_arm': 'S-EVO', 'sec_learner': False,
           'provider_format_retry': False, 'max_input_tokens': 32768,
           'max_output_tokens': 6144,
           'input_tokenizer_path': '/content/cp03/tokenizer.json'}
    rt = Runtime(dest / 'runtime', cfg, provider_transport=m)
    u = rt.provision('CP24 report pair ' + scenario, True)['user_id']
    attach(rt, u, t)
    try:
        rt.loop_round(u)
        opp = [n for n in rt.nodes(u, 'BusinessOpportunity')
               if n.content['target_type'] == 'realized_units_next_day'
               and n.content['target_day'] == 24 and n.content['product_id'] == 'sparkling-water-500ml'
               and n.content['location'] == 'AI-HOUSE']
        assert len(opp) == 1, 'prospective day24 sales opportunity missing'
        if scenario == 'intervention':
            t.phase = 'restock'
            t.day = t.last_day = 24
            rt.loop_round(u)
            assert any(n.content['event_type'] == 'RESTOCK_CONFIRMED'
                       for n in rt.nodes(u, 'PublicEvent'))
        t.phase = 'reports'
        t.day = t.last_day = 25
        rt.loop_round(u)
        settlements = [n for n in rt.nodes(u, 'BusinessSettlement')
                       if n.content['opportunity_id'] == opp[0].node_id]
        product_obs = [n for n in rt.nodes(u, 'ToolObservation')
                       if n.content['tool'] == 'get_sales_report'
                       and n.content['arguments']['by_product'] is True]
        cert_obs = [n for n in rt.nodes(u, 'ToolObservation')
                    if n.content['tool'] == 'get_sales_report'
                    and n.content['arguments']['by_product'] is False]
        assert len(product_obs) == len(cert_obs) == 1
        assert product_obs[0].content['text'] == PRODUCT
        if scenario not in ('wrong_date', 'wrong_total'):
            assert cert_obs[0].content['text'] == CERT
        if scenario in ('reverse', 'forward'):
            assert len(settlements) == 1 and settlements[0].content['status'] == 'RESOLVED'
            assert settlements[0].content['outcome'] == 5
            evidence = [rt.graph.get_node(u, x) for x in settlements[0].content['evidence_ids']]
            assert {n.content['event_type'] for n in evidence} == {'REALIZED_PRODUCT_SALES',
                                                                   'SALES_DAY_CERTIFICATE'}
            assert next(n for n in evidence if n.content['event_type'] == 'REALIZED_PRODUCT_SALES').content['source_observation_id'] == product_obs[0].node_id
            assert next(n for n in evidence if n.content['event_type'] == 'SALES_DAY_CERTIFICATE').content['source_observation_id'] == cert_obs[0].node_id
        elif scenario == 'intervention':
            assert len(settlements) == 1 and settlements[0].content['status'] == 'CENSORED'
            assert settlements[0].content['outcome'] is None
        else:
            assert not settlements or all(n.content['status'] != 'RESOLVED' for n in settlements)
        events = [n for n in rt.nodes(u, 'PublicEvent') if n.content['event_type'] == 'SALES_REPORT_COVERAGE']
        assert events
        (dest / 'checks.json').write_text(json.dumps({'scenario': scenario,
            'settlements': [{'status': n.content['status'], 'outcome': n.content['outcome']}
                            for n in settlements], 'sales_coverage': [n.content['typed_fields']['coverage_status']
                            for n in events], 'request_count': len(m.requests),
            'public_tool_calls': len(t.calls), 'paid_calls': 0}, indent=2))
    except Exception as exc:
        (dest / 'failure.json').write_text(json.dumps({'scenario': scenario,
            'exception_type': type(exc).__name__, 'exception': str(exc),
            'settlements': [{'status': n.content['status'], 'reason': n.content['reason']}
                for n in rt.nodes(u, 'BusinessSettlement')]}, indent=2))
        raise
    finally:
        (dest / 'requests.json').write_text(json.dumps(m.requests, ensure_ascii=False))
        (dest / 'public_calls.json').write_text(json.dumps(t.calls, ensure_ascii=False))
        rt.close()

if __name__ == '__main__':
    root = Path(sys.argv[1])
    root.mkdir(exist_ok=False)
    for scenario in ('reverse', 'forward', 'wrong_date', 'wrong_location', 'wrong_total', 'intervention'):
        run(root, scenario)
    print('CP24_SALES_REPORT_ORDER_E2E_PASS')
