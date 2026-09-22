"""Paid frozen-state experiment: measured tool cost facts, no GUI dispatch."""
import copy
import json
import os
import statistics
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    sources = [Path('artifacts/voice-audio') / name / 'events.jsonl' for name in [
        '20260921-203051-ae93f6', '20260921-203314-e5ce03']]
    timings, samples = {}, []
    for source in sources:
        events = [json.loads(line) for line in source.read_text().splitlines()]
        starts = {}
        for event in events:
            if event['type'] == 'mcp.start':
                starts[event['span']] = event['request']['tool']
            if event['type'] == 'mcp.end' and event['span'] in starts:
                tool = starts[event['span']]
                timings.setdefault(tool, []).append(event['latency_ms'])
        if source.parent.name == '20260921-203314-e5ce03':
            samples = [e for e in events if e['type'] == 'choice' and e['seq'] in [67, 84, 118]]
    costs = {k: {'median_ms': statistics.median(v), 'samples': len(v)} for k, v in timings.items()}
    trace = Trace('artifacts/addressing/tool-cost-v1')
    token = CURRENT_TRACE.set(trace)
    results = []
    try:
        for sample in samples:
            for variant in ['baseline', 'measured_costs']:
                body = copy.deepcopy(sample['request'])
                if variant == 'measured_costs':
                    body['state']['measured_execution_costs'] = {
                        'public_mcp_tools': costs,
                        'execution': 'Each native mutation is preceded by get_app_state. INSERT_TEXT uses type_text; '
                                     'CLICK uses click. A contiguous literal input is one type_text call. '
                                     'These are past measurements, not guarantees or instructions '
                                     'to skip prerequisites.'
                    }
                response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                op, target = selected_answers(response['answers'], body['questions'])
                record = {'source_seq': sample['seq'], 'variant': variant, 'operation': op['choice'],
                          'target': target['choice'] if target else None}
                results.append(record)
                trace.emit('evaluation', **record)
                (trace.folder / 'results.json').write_text(json.dumps({'costs': costs, 'results': results}, indent=2))
                print(json.dumps(record), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
