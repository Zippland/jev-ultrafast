"""Paid read-only replay of failed real snapshots with alternative history representations."""
import argparse
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output directory')
    load_environment()
    events = list(map(json.loads, args.trace.read_text().splitlines()))
    history, pages, pending, cases = [], None, None, []
    for event in events:
        if event['type'] == 'observation.end':
            new_pages = {key: page['text'] for key, page in event['pages'].items()}
            if pending is not None:
                history[pending]['observed_change'] = pages != new_pages
                pending = None
            pages = new_pages
        elif event['type'] in {'route.returned', 'action.returned'}:
            history.append({k: event.get(k) for k in ('kind', 'label', 'text', 'version')})
            if event['type'] == 'action.returned':
                pending = len(history) - 1
        elif event['type'] == 'choice' and history and history[-1].get('observed_change') is False:
            cases.append({'seq': event['seq'], 'body': event['request'], 'effects': copy.deepcopy(history[-24:])})
    cases = cases[:2]
    if len(cases) != 2:
        raise ValueError('Need two recorded choices after unchanged observations')
    trace = Trace(args.output)
    (args.output / 'cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2))
    token = CURRENT_TRACE.set(trace)
    results = []
    try:
        for case in cases:
            for variant in ('baseline', 'effects', 'no_history', 'attempted_history'):
                body = copy.deepcopy(case['body'])
                original = body['state']['executed_actions']
                if len(original) != len(case['effects']):
                    raise ValueError('History alignment changed')
                if variant in ('effects', 'attempted_history'):
                    for item, observed in zip(original, case['effects'], strict=True):
                        if any(item.get(k) != observed.get(k) for k in ('kind', 'label', 'text', 'version')):
                            raise ValueError('History alignment mismatch')
                        if 'observed_change' in observed:
                            item['observed_change'] = observed['observed_change']
                if variant == 'no_history':
                    body['state']['executed_actions'] = []
                if variant == 'attempted_history':
                    body['state']['attempted_actions'] = body['state'].pop('executed_actions')
                response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                op, target = selected_answers(response['answers'], body['questions'])
                label = body['questions'].get(op['choice'].lower() + '_target', {}).get('criteria', {}).get(
                    (target or {}).get('choice'))
                record = {'seq': case['seq'], 'variant': variant, 'operation': op['choice'], 'target': label,
                          'confidence': op['confidence']}
                results.append(record)
                trace.emit('evaluation', **record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
        (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
