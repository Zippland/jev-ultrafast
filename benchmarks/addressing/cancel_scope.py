"""Paid cancellation interpretation probe on an actual failure; no GUI calls."""
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace

CANCELLATION = ('A request to stop or cancel ends pending work; it does not ask to clear, undo, '
                'or otherwise modify application state. Choose LISTEN unless a later request authorizes new work.')


def main():
    load_environment()
    source = Path('artifacts/voice-audio/20260921-204344-e4058e/events.jsonl')
    events = [json.loads(line) for line in source.read_text().splitlines()]
    original = next(e['request'] for e in events if e['seq'] == 90)
    trace = Trace('artifacts/addressing/cancel-scope-v1')
    token = CURRENT_TRACE.set(trace)
    results = []
    try:
        for variant in ['baseline', 'cancellation_scope']:
            for repeat in range(3):
                body = copy.deepcopy(original)
                if variant == 'cancellation_scope':
                    body['questions']['operation']['instructions']['rules'] += '\n' + CANCELLATION
                response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                operation, target = selected_answers(response['answers'], body['questions'])
                record = {'variant': variant, 'repeat': repeat, 'operation': operation['choice'],
                          'target': target['choice'] if target else None}
                results.append(record)
                trace.emit('evaluation', **record)
                (trace.folder / 'results.json').write_text(json.dumps(results, indent=2))
                print(json.dumps(record), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
