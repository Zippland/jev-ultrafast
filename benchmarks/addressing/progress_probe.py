"""Paid Jev-only probe: evaluate a concrete candidate before repeating it. No GUI."""
import argparse
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, validate_choice
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output directory')
    load_environment()
    trace = Trace(args.output)
    token = CURRENT_TRACE.set(trace)
    results = []
    question = {'type': 'choice', 'instructions': {
        'question': 'Would this exact candidate action make progress toward the current request from the CURRENT '
                    'observed values, or has its effect already been achieved?',
        'focus': 'Judge the effect of this particular action, not whether the entire task is finished. '
                 'Use observed values and chronological speech; an old tool return is not proof of success.'},
        'criteria': {'PROGRESS': 'The effect of this action is still needed for the current request.',
                     'ALREADY_DONE': 'This action has already achieved its effect; repetition is unnecessary.',
                     'UNSUPPORTED': 'This action does not serve the current request or evidence is insufficient.'}}
    try:
        for case in json.loads(args.cases.read_text()):
            for target in ('1', '14'):
                candidate = case['body']['questions']['click_target']['criteria'][target]
                body = {'model': case['body']['model'], 'state': {
                    **case['body']['state'], 'candidate_action': {'operation': 'CLICK', 'target': candidate}},
                    'questions': {'progress': question}}
                response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                answer = validate_choice(response['answers'].get('progress', {}), question['criteria'])
                record = {'seq': case['seq'], 'target': candidate, 'answer': answer}
                results.append(record)
                trace.emit('evaluation', **record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
        (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
