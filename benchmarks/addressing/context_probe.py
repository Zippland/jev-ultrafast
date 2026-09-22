"""Paid snapshot experiment: repeat observed regional text beside compatible candidates."""
import argparse
import copy
import json
import os
import re
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace

# Only decodes our serialized AX rows; never interprets user speech.
ROW = re.compile(r'^\[(\d+)\] (\S+) (.*?) parent=(\S+) value=(.*?) actions=', re.M)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output directory')
    load_environment()
    cases = json.loads(args.cases.read_text())
    trace = Trace(args.output)
    token = CURRENT_TRACE.set(trace)
    results = []
    try:
        for case in cases:
            body = copy.deepcopy(case['body'])
            rows = {m[0]: {'role': m[1], 'label': m[2], 'parent': m[3], 'value': m[4]}
                    for m in ROW.findall(body['state']['observations'][0]['text'])}
            displays = [{'region': rows.get(row['parent'], {}).get('label', ''), 'text': row['value']}
                        for row in rows.values() if row['role'] == 'AXStaticText' and row['value']]
            for question in body['questions'].values():
                for candidate in question['criteria'].values():
                    if isinstance(candidate, dict):
                        candidate['nearby_observed_text'] = displays
            response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
            op, target = selected_answers(response['answers'], body['questions'])
            label = body['questions'].get(op['choice'].lower() + '_target', {}).get('criteria', {}).get(
                (target or {}).get('choice'))
            result = {'seq': case['seq'], 'observed_context': displays, 'operation': op['choice'], 'target': label}
            results.append(result)
            trace.emit('evaluation', **result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
