"""Saved-request Runtime E2E for CP24 SEC reflection. Written before product change."""
import copy
import json
import sys
from pathlib import Path
from skuld.app.runtime import Runtime
from skuld.kernel.primitives import dumps
from skuld.loop import business_events as be
from skuld.sim.providers import offline_config

R1 = Path('/content/cp24_dev_r1')
REF = R1 / 'CP24_DEV_S-FRESH_seed1/runtime/receipts.jsonl'
CAP = 32768


def task_from(request):
    wrapper = json.loads(request['messages'][-1]['content'])
    task = wrapper['task']
    return wrapper, json.loads(task) if isinstance(task, str) else task


def decode_table(value):
    if isinstance(value, list):
        return value
    assert isinstance(value, dict) and set(value) >= {'columns', 'rows'}
    return [dict(zip(value['columns'], row)) for row in value['rows']]


def example_ids(learning):
    group = learning['observed_groups']
    ix = group['columns'].index('examples')
    return {e['settlement_id'] for row in group['rows'] for e in row[ix]}


def check_learning(original, view, require_bounded):
    old = original['observed_groups']
    new = view['observed_groups']
    assert old['columns'] == new['columns'] and len(old['rows']) == len(new['rows'])
    ix = old['columns'].index('examples')
    for before, after in zip(old['rows'], new['rows']):
        assert before[:ix] == after[:ix] and before[ix+1:] == after[ix+1:], 'group aggregate changed'
        source = before[ix]
        chosen = after[ix]
        assert source and chosen
        if require_bounded: assert len(chosen) <= 2
        assert all(e in source for e in chosen), 'example not in actual observed group'
        assert source[-1] in chosen, 'newest actual example lost'
        if any(e['outcome'] != source[-1]['outcome'] for e in source[:-1]):
            assert any(e['outcome'] != source[-1]['outcome'] for e in chosen), 'known contrast hidden'
    for name in ('resolved_count', 'unique_day_clusters', 'pending_future_windows',
                 'pending_window_count', 'active_candidate_plans', 'censored_count',
                 'unknown_count'):
        assert original[name] == view[name], 'complete evidence summary changed: ' + name
    return example_ids(view)


def cases():
    rejected = []
    for line in REF.open(encoding='utf-8'):
        rec = json.loads(line)
        if rec.get('kind') == 'provider_preflight' and rec.get('reason') == 'INPUT_TOKEN_LIMIT_REQUIRED_CONTEXT':
            rejected.append((rec['logical_ref'], rec['request']))
    assert len(rejected) == 5
    assert {name for name, _ in rejected} == {f'public-tools:{i}:reflect' for i in range(99, 104)}
    # A genuinely sent later-stage reflection is an unaffected positive path.
    later = []
    for line in (R1 / 'calls.jsonl').open(encoding='utf-8'):
        rec = json.loads(line)
        if rec.get('trajectory') != 'CP24_DEV_S-EVO_seed1' or rec.get('state') != 'RETURNED':
            continue
        request = rec['request']
        try:
            _, task = task_from(request)
        except (KeyError, TypeError, ValueError):
            continue
        if task.get('stage') == 'PUBLIC_TOOL_REFLECT':
            later.append((rec['index'], request))
    assert later
    return rejected + [('sent_late_S-EVO_'+str(later[-1][0]), later[-1][1])]


def run(root):
    root.mkdir(exist_ok=False)
    cfg = {**offline_config(20), 'max_input_tokens': CAP,
           'max_output_tokens': 6144,
           'input_tokenizer_path': str(R1 / 'tokenizer.json')}
    def no_transport(*args, **kwargs):
        raise AssertionError('NO_PROVIDER_CALL_IN_E2E')
    rt = Runtime(root / 'runtime', cfg, provider_transport=no_transport)
    checks = []
    try:
        for index, (name, original_request) in enumerate(cases()):
            wrapper, original = task_from(original_request)
            assert original['stage'] == 'PUBLIC_TOOL_REFLECT'
            view = copy.deepcopy(original)
            original_learning = view['learning_evidence']
            select = getattr(be, 'reflection_learning_view', lambda x: x)
            view['learning_evidence'] = select(original_learning)
            selected_ids = check_learning(original_learning, view['learning_evidence'], hasattr(be, 'reflection_learning_view'))
            view['learning_evidence_ids'] = sorted(selected_ids)
            prompt = be.render_reflection_context(view)
            rendered = json.loads(prompt)
            assert decode_table(rendered['experiences']) == original['experiences'], 'complete experiences changed'
            assert decode_table(rendered['responsibilities']) == original['responsibilities'], 'responsibilities changed'
            assert rendered['learning_evidence']['observed_groups']['rows']
            current = copy.deepcopy(original_request)
            current['messages'][-1]['content'] = dumps({'task': prompt, 'schema': wrapper['schema']})
            fitted = rt._fit_product_payload(current)
            size = rt._input_size(fitted)
            assert size <= CAP and fitted['max_tokens'] == 6144
            _, final_task = task_from(fitted)
            assert final_task['stage'] == 'PUBLIC_TOOL_REFLECT'
            # Reconstruct the visible example IDs from the wire's documented tuple format.
            group = final_task['learning_evidence']['observed_groups']
            ix = group['columns'].index('examples')
            example_columns = final_task['learning_evidence'].get('example_tuple_columns')
            if example_columns:
                assert example_columns == ['settlement_id', 'target_day', 'outcome']
                visible_ids = {e[0] for row in group['rows'] for e in row[ix]}
            else:
                visible_ids = {e['settlement_id'] for row in group['rows'] for e in row[ix]}
            assert visible_ids == selected_ids, 'admission IDs differ from actual wire'
            report = {'name': name, 'final_input_tokens': size,
                      'example_ids_visible': len(visible_ids),
                      'group_rows': len(group['rows']),
                      'experiences': len(original['experiences']),
                      'responsibilities': len(original['responsibilities']),
                      'source_kind': 'saved_unsent_preflight' if index < 5 else 'saved_sent_late_reflection',
                      'provider_calls': 0}
            checks.append(report)
            (root / f'wire_{index+1:02}.json').write_text(json.dumps(fitted, ensure_ascii=False), encoding='utf-8')
        (root / 'checks.json').write_text(json.dumps({'cases': checks, 'all_within_limit': True,
                                                    'paid_calls': 0}, indent=2), encoding='utf-8')
        print('SEC_CONTEXT_E2E_PASS', [(r['name'], r['final_input_tokens']) for r in checks])
    except Exception as exc:
        (root / 'failure.json').write_text(json.dumps({'passed_before_failure': checks,
            'exception_type': type(exc).__name__, 'exception': str(exc),
            'paid_calls': 0}, indent=2), encoding='utf-8')
        raise
    finally:
        rt.close()


if __name__ == '__main__':
    run(Path(sys.argv[1]))
