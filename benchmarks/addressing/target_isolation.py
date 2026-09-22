"""Paid question-isolation probe on frozen failures, without GUI dispatch."""
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, validate_choice
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    source = Path('artifacts/voice-audio/20260921-204734-265ab1/events.jsonl')
    events = [json.loads(line) for line in source.read_text().splitlines()]
    trace = Trace('artifacts/addressing/target-isolation-v1')
    token = CURRENT_TRACE.set(trace)
    rows = []
    try:
        for seq in [173, 422]:
            original = next(e['request'] for e in events if e['seq'] == seq)
            for variant in ['all_heads', 'operation_and_click', 'click_only']:
                body = copy.deepcopy(original)
                if variant != 'all_heads':
                    heads = {'click_target', 'operation'} if variant == 'operation_and_click' else {'click_target'}
                    body['questions'] = {k: v for k, v in body['questions'].items() if k in heads}
                response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                answer = validate_choice(response['answers']['click_target'],
                                         body['questions']['click_target']['criteria'])
                row = {'seq': seq, 'variant': variant,
                       'hypothetical_click_target': body['questions']['click_target']['criteria'][answer['choice']],
                       'operation': response['answers'].get('operation', {}).get('choice'),
                       'usage': response.get('usage', {})}
                rows.append(row)
                trace.emit('evaluation', **row)
                (trace.folder / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
