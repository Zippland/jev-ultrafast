"""Paid no-GUI comparison: expose all observed targets in shared Jev state."""
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    cases = json.loads(Path('artifacts/addressing/policy-scope-v1/cases.json').read_text())
    cases += [{'id': 'calculator/' + str(c['seq']), 'body': c['body']} for c in
              json.loads(Path('artifacts/addressing/normal-calculator-cases.json').read_text())]
    trace = Trace('artifacts/addressing/shared-targets-v1')
    (trace.folder / 'probe.py').write_text(Path(__file__).read_text())
    (trace.folder / 'cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2))
    token = CURRENT_TRACE.set(trace)
    records = []
    try:
        for case in cases:
            for variant in ('baseline', 'shared_targets'):
                body = copy.deepcopy(case['body'])
                if variant == 'shared_targets':
                    body['state']['available_targets'] = {
                        name.removesuffix('_target').upper(): question['criteria']
                        for name, question in body['questions'].items() if name.endswith('_target')}
                result = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                op, target = selected_answers(result['answers'], body['questions'])
                label = body['questions'].get(op['choice'].lower() + '_target', {}).get('criteria', {}).get(
                    (target or {}).get('choice'))
                row = {'case': case['id'], 'variant': variant, 'operation': op['choice'], 'target': label}
                records.append(row)
                (trace.folder / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
