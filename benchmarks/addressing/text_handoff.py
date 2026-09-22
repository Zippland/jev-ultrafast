"""Paid frozen literal generation comparison; no computer/browser mutations."""
import copy
import json
from pathlib import Path

from jev_ultrafast.actions import DESCRIPTIONS
from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import field_text
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    source = Path('artifacts/addressing/tool-cost-text-v1/events.jsonl')
    events = [json.loads(line) for line in source.read_text().splitlines()]
    request = next(e['request']['body'] for e in events if e['type'] == 'model.start')
    context = json.loads(request['messages'][1]['content'])
    trace = Trace('artifacts/addressing/text-handoff-v1')
    token = CURRENT_TRACE.set(trace)
    results = []
    try:
        for variant in ['baseline', 'operation_contract']:
            for repeat in range(3):
                candidate = copy.deepcopy(context)
                if variant == 'operation_contract':
                    candidate['operation'] = {'name': 'INSERT_TEXT', 'description': DESCRIPTIONS['INSERT_TEXT']}
                value, details = field_text(candidate)
                result = {'variant': variant, 'repeat': repeat, 'text': value, **details}
                results.append(result)
                trace.emit('evaluation', **result)
                (trace.folder / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
                print(json.dumps({'variant': variant, 'text': value, 'latency_ms': details['latency_ms']}), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
