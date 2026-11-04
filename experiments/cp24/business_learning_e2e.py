"""Native Runtime E2E: public sales evidence -> SEC candidate -> later outcome -> restart."""
import json
import sys
from pathlib import Path
from skuld.app.runtime import Runtime
from skuld.sim.providers import offline_config, completion
from skuld.loop.tool_subject import attach
from skuld.loop import business_events as be


def walk(value):
    if isinstance(value, str):
        try:
            yield from walk(json.loads(value))
        except (ValueError, TypeError):
            return
    elif isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


class PublicFixture:
    def __init__(self, broken_certificate_day=None):
        self.broken_certificate_day = broken_certificate_day
        self.day = 1
        self.last_day = 1
        self.minute = 540
        self.calls = []

    def tools(self):
        return [{'name': name, 'description': 'public fixture tool', 'parameters': {'type': 'object'}}
                for name in ('read_email', 'get_machine_inventory', 'get_sales_report')]

    def briefing(self):
        return 'Labelled public-tool fixture; no hidden future results are exposed to the agent.'

    def over(self):
        return False

    def call(self, name, args):
        self.calls.append((self.day, name, args))
        footer_day = self.day + (1 if name == 'get_sales_report' and args.get('by_product') is True and self.day == 5 else 0)
        self.last_day = footer_day
        self.minute = 540
        footer = f'\n\n[Day {footer_day}/30 - test 09:00 - balance \u20ac1,500.00]'
        if name == 'read_email':
            return {'text': 'cola-500ml: Cola 500ml' + footer, 'is_error': False}
        if name == 'get_machine_inventory':
            rows = '\n'.join(f'AI-HOUSE:DRINKS:A{i} large  Cola 500ml    8 10 \u20ac1.80'
                             for i in range(1, 4))
            return {'text': rows + footer, 'is_error': False}
        assert name == 'get_sales_report' and args.get('days') == 1 and args.get('location') == 'AI-HOUSE' and type(args.get('by_product')) is bool
        assert self.day >= 2
        sold_day = self.day - 1
        units = 2 + sold_day % 3
        if args['by_product'] is False:
            body = (f'Sales for the last 1 day(s):\n\n'
                    f' day date        weather   units revenue\n'
                    f' {sold_day} {"UNKNOWN" if self.day == self.broken_certificate_day else f"2026-01-{sold_day:02d}"} sunny     {units} \u20ac{units * 1.8:.2f}\n\n'
                    f'Total: {units} units, \u20ac{units * 1.8:.2f} (\u20ac{units * 1.8:.2f}/day)')
        else:
            body = (f'Sales for the last 1 day(s):\n\n'
                    f'product                              units      revenue\n'
                    f'Cola 500ml                               {units}       \u20ac{units * 1.8:.2f}')
        return {'text': body + footer, 'is_error': False}


class OfflineModel:
    def __init__(self, transport):
        self.transport = transport
        self.requests = []

    def __call__(self, endpoint, key, payload, *args):
        self.requests.append(payload)
        context = next(x for x in walk(payload) if x.get('stage') in ('PUBLIC_TOOL_DECIDE', 'PUBLIC_TOOL_REFLECT'))
        if context['stage'] == 'PUBLIC_TOOL_REFLECT':
            experiences = context['experiences']
            if isinstance(experiences, dict):
                experiences = [dict(zip(experiences['columns'], row)) for row in experiences['rows']]
            refs = [x['node_id'] for x in experiences if x['node_id'].startswith('toolobservation')]
            candidates = []
            rule = {'target_type': 'realized_units_next_day', 'product_id': 'cola-500ml',
                    'slot_id': None, 'location': 'AI-HOUSE', 'quantity_min': 1,
                    'quantity_max': None, 'price_min_eur': None, 'price_max_eur': None,
                    'horizon_days': 1, 'action_assumptions': 'NO_RESTOCK_OR_REPLACEMENT_OR_REPRICE',
                    'probability': None, 'expected_units': 3.0, 'parent_id': None,
                    'evidence_ids': refs[-1:], 'reason': 'prospective fixture candidate'}
            if self.transport.day == 2:
                candidates = [rule]
            elif self.transport.day == 4 and context.get('candidate_parents'):
                parent = context['candidate_parents'][0]['candidate_id']
                candidates = [{**rule, 'expected_units': 4.0, 'parent_id': parent,
                               'reason': 'different numeric prediction'}]
            return completion({'attribution': 'Public sales only; no private label',
                               'evidence_ids': refs[-1:], 'memory_items': [], 'candidates': candidates})
        actions = ([{'tool': 'read_email', 'arguments': {}}] if self.transport.day == 1 else [])
        actions += [{'tool': 'get_machine_inventory', 'arguments': {}}]
        if self.transport.day >= 2:
            actions += [{'tool': 'get_sales_report', 'arguments':
                         {'days': 1, 'by_product': False, 'location': 'AI-HOUSE'}},
                        {'tool': 'get_sales_report', 'arguments':
                         {'days': 1, 'by_product': True, 'location': 'AI-HOUSE'}}]
        return completion({'actions': actions, 'ledger': 'public sales fixture', 'plan': 'check sales',
                           'predictions': [], 'responsibilities': [], 'reason': 'observe public evidence',
                           'used_evidence_ids': [], 'used_candidate_ids': [],
                           'responsibility_ids': context['business_state']['goal_ids']})


def run(arm, out, broken_certificate_day=None):
    t = PublicFixture(broken_certificate_day)
    model = OfflineModel(t)
    cfg = {**offline_config(60), 'tool_subject': True, 'business_events': True,
           'tool_subject_arm': arm, 'sec_learner': False,
           'provider_format_retry': False, 'max_input_tokens': 32768, 'max_output_tokens': 6144}
    rt = Runtime(out / 'runtime', cfg, provider_transport=model)
    u = rt.provision('CP24 public sales fixture ' + arm, True)['user_id']
    attach(rt, u, t)
    for day in range(1, 9):
        t.day = day
        t.last_day = day
        rt.loop_round(u)
    sales_ops = [n for n in rt.nodes(u, 'BusinessOpportunity') if n.content['target_type'] == 'realized_units_next_day']
    assert sales_ops, 'No prospective public sales target was issued'
    assert len({(n.content['location'], n.content['product_id'], n.content['target_day']) for n in sales_ops}) == len(sales_ops)
    settlements = [n for n in rt.nodes(u, 'BusinessSettlement') if n.content['opportunity_id'] in {o.node_id for o in sales_ops}]
    assert any(n.content['status'] == 'RESOLVED' for n in settlements), 'Public report never settled sales target'
    for settlement in settlements:
        if settlement.content['status'] != 'RESOLVED':
            continue
        assert isinstance(settlement.content['outcome'], int)
        evidence = [rt.graph.get_node(u, x) for x in settlement.content['evidence_ids']]
        assert any(x and x.content.get('event_type') == 'REALIZED_PRODUCT_SALES' for x in evidence)
        assert any(x and x.content.get('event_type') == 'SALES_DAY_CERTIFICATE' for x in evidence)
        assert all(x.content['typed_fields']['report_day'] == settlement.content['day_cluster']
                   for x in evidence if x and x.content.get('event_type') == 'REALIZED_PRODUCT_SALES')
    cross = [x for x in rt.nodes(u, 'PublicEvent') if x.content['event_type'] == 'REALIZED_PRODUCT_SALES'
             and x.content['typed_fields']['report_day'] == 4]
    if broken_certificate_day is None:
        assert cross and any(rt.graph.get_node(u, x.content['source_observation_id']).content['call_before_day'] == 5
                             and '[Day 6/30' in rt.graph.get_node(u, x.content['source_observation_id']).content['text']
                             for x in cross), 'Post-call day must not relabel prior-day sales'
    else:
        affected = [n for n in settlements if rt.graph.get_node(u, n.content['opportunity_id']).content['target_day'] == broken_certificate_day - 1]
        assert affected and all(n.content['status'] == 'UNKNOWN' for n in affected), 'Unpaired report cannot settle a target'
        assert any(n.content['event_type'] == 'SALES_REPORT_COVERAGE' and n.content['typed_fields']['coverage_status'] == 'UNKNOWN'
                   for n in rt.nodes(u, 'PublicEvent'))
    candidates = rt.nodes(u, 'BusinessCandidate')
    assert any(n.content['rule']['target_type'] == 'realized_units_next_day' for n in candidates)
    child = [n for n in candidates if n.content.get('parent_id')]
    if arm == 'S-EVO':
        assert child and child[0].content['semantic_signature'] != candidates[0].content['semantic_signature']
        assert child[0].content['validation_count_at_birth'] == 0
    else:
        assert not child
        assert all(not context.get('candidate_parents') for request in model.requests
                   for context in walk(request) if context.get('stage') == 'PUBLIC_TOOL_REFLECT')
    scores = [n for n in rt.nodes(u, 'BusinessRuleScore')
              if rt.graph.get_node(u, n.content['opportunity_id']).content['target_type'] == 'realized_units_next_day']
    assert scores and len({(n.content['candidate_id'], n.content['opportunity_id']) for n in scores}) == len(scores)
    assert all(rt.graph.get_node(u, n.content['opportunity_id']).content['issued_at'] < n.created_at for n in scores)
    current = {(n.node_id, n.content['semantic_signature']) for n in candidates}
    calls_before = len(t.calls)
    rt.close()
    rt = Runtime(out / 'runtime', cfg, provider_transport=model)
    attach(rt, u, t)
    assert {(n.node_id, n.content['semantic_signature']) for n in rt.nodes(u, 'BusinessCandidate')} == current
    assert len(t.calls) == calls_before
    (out / 'checks.json').write_text(json.dumps({'arm': arm, 'sales_opportunities': len(sales_ops),
        'resolved_sales': sum(n.content['status'] == 'RESOLVED' for n in settlements),
        'candidate_count': len(candidates), 'child_count': len(child), 'sales_scores': len(scores),
        'restart_without_reexecution': True, 'unknown_due_to_unpaired': broken_certificate_day is not None,
        'paid_calls': 0}, indent=2))
    (out / 'requests.json').write_text(json.dumps(model.requests))
    rt.close()


if __name__ == '__main__':
    root = Path(sys.argv[1])
    root.mkdir(exist_ok=False)
    for arm in ('S-EVO', 'S-FRESH'):
        path = root / arm
        path.mkdir()
        run(arm, path)
    unknown = root / 'S-EVO-UNKNOWN'
    unknown.mkdir()
    run('S-EVO', unknown, broken_certificate_day=5)
    print('CP24_BUSINESS_LEARNING_E2E_PASS')
