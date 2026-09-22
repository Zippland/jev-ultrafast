"""Paid no-GUI replay of a failed text request with strict structured output."""
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json
from jev_ultrafast.tracing import CURRENT_TRACE, Trace

SCHEMA = {'type': 'json_schema', 'json_schema': {'name': 'literal_text', 'strict': True,
          'schema': {'type': 'object', 'properties': {'text': {'type': ['string', 'null']}},
                     'required': ['text'], 'additionalProperties': False}}}


def main():
    load_environment()
    source = Path('artifacts/live-browser/v2/B04/20260921-182832-0f89a2/events.jsonl')
    requests = [e['request'] for e in map(json.loads, source.read_text().splitlines())
                if e['type'] == 'model.start' and 'openrouter.ai/' in e['request'].get('url', '')]
    assert len(requests) == 1
    trace = Trace('artifacts/addressing/text-schema-v1')
    (trace.folder / 'probe.py').write_text(Path(__file__).read_text())
    token = CURRENT_TRACE.set(trace)
    records = []
    try:
        for variant in ('baseline', 'strict'):
            for repeat in range(3):
                request = copy.deepcopy(requests[0])
                if variant == 'strict':
                    request['body']['response_format'] = SCHEMA
                    request['body']['provider'] = {'require_parameters': True}
                result = post_json(request['url'], os.environ['TEXT_MODEL_API_KEY'], request['body'])
                raw = result['choices'][0]['message']['content']
                try:
                    parsed = json.loads(raw)
                    valid = isinstance(parsed, dict) and set(parsed) == {'text'} and isinstance(parsed['text'], str)
                except (ValueError, TypeError):
                    valid = False
                row = {'variant': variant, 'repeat': repeat, 'valid': valid, 'raw': raw}
                records.append(row)
                (trace.folder / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
