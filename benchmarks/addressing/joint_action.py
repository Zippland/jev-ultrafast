"""Paid, no-GUI comparison of speculative heads versus joint observed actions."""
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers, validate_choice
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    cases = json.loads(Path('artifacts/addressing/shared-targets-v1/cases.json').read_text())
    trace = Trace('artifacts/addressing/joint-action-v1')
    (trace.folder / 'probe.py').write_text(Path(__file__).read_text())
    (trace.folder / 'cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2))
    token = CURRENT_TRACE.set(trace)
    rows = []
    try:
        for case in cases:
            for variant in ('shared_heads', 'joint_action'):
                body = copy.deepcopy(case['body'])
                body['state']['available_targets'] = {
                    name.removesuffix('_target').upper(): question['criteria']
                    for name, question in body['questions'].items() if name.endswith('_target')}
                joint, mapped = {}, {}
                for operation, description in body['questions']['operation']['criteria'].items():
                    targets = body['state']['available_targets'].get(operation, {'none': None})
                    for target_id, target in targets.items():
                        key = str(len(joint) + 1)
                        joint[key] = {'operation': operation, 'meaning': description, 'target': target}
                        mapped[key] = (operation, target)
                if len(joint) > 255:
                    raise ValueError('Joint action space exceeds 255; no truncation')
                if variant == 'joint_action':
                    body['questions'] = {'action': {'type': 'choice', 'criteria': joint,
                                         'instructions': body['questions']['operation']['instructions']}}
                response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                if variant == 'joint_action':
                    answer = validate_choice(response['answers']['action'], joint)
                    operation, target = mapped[answer['choice']]
                else:
                    op, selected = selected_answers(response['answers'], body['questions'])
                    operation = op['choice']
                    target = body['state']['available_targets'].get(operation, {}).get((selected or {}).get('choice'))
                row = {'case': case['id'], 'variant': variant, 'operation': operation,
                       'target': target, 'joint_candidate_count': len(joint)}
                rows.append(row)
                (trace.folder / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
