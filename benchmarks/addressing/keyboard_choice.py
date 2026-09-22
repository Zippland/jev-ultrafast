"""Paid no-GUI test of discoverability of the existing literal keyboard input tool."""
import argparse
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.actions import TEXT_ARGUMENTS
from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import field_text, post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace

DESCRIPTION = ('Enter a contiguous sequence of literal characters through the current app keyboard input, '
               'including numbers, operators, or prose accepted by that app. Existing content is preserved; '
               'this does not clear fields, run code, or press shortcuts. A text model supplies the characters.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--variants', nargs='+', default=['baseline', 'keyboard_description'],
                        choices=['baseline', 'keyboard_description', 'efficient_input'])
    args = parser.parse_args()
    load_environment()
    trace = Trace(args.output)
    token = CURRENT_TRACE.set(trace)
    (args.output / 'probe.py').write_text(Path(__file__).read_text())
    (args.output / 'cases.json').write_bytes(args.cases.read_bytes())
    records = []
    try:
        for case in json.loads(args.cases.read_text()):
            for variant in args.variants:
                body = copy.deepcopy(case['body'])
                if variant != 'baseline':
                    body['questions']['operation']['criteria']['INSERT_TEXT'] = DESCRIPTION
                if variant == 'efficient_input':
                    body['questions']['operation']['instructions']['rules'] += (
                        ' When a contiguous literal input can accomplish the same intended interaction, '
                        'prefer INSERT_TEXT to clicking individual character controls. Do not skip '
                        'prerequisites, confirmations or needed observations; use only supported input.')
                result = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                op, target = selected_answers(result['answers'], body['questions'])
                label = body['questions'].get(op['choice'].lower() + '_target', {}).get(
                    'criteria', {}).get((target or {}).get('choice'))
                record = {'seq': case['seq'], 'variant': variant, 'operation': op['choice'], 'target': label}
                if op['choice'] == 'INSERT_TEXT':
                    state = body['state']
                    try:
                        text, details = field_text({
                            'goal': {'chronological_current_speech': state['speech']},
                            'field': {'app': label['app'], 'label': label['element'],
                                      'role': label['role'], 'value': label['current_value']},
                            'page': {'title': 'Observed apps', 'text': state['observations']},
                            'recent_actions': state['executed_actions'], 'argument': TEXT_ARGUMENTS['insert']})
                        record.update(text=text, text_latency_ms=details['latency_ms'])
                    except Exception as exc:
                        record['text_error'] = f'{type(exc).__name__}: {exc}'
                trace.emit('evaluation', **record)
                records.append(record)
                (args.output / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
