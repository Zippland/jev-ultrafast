"""Paid frozen-request diagnostic; never calls desktop or browser tools."""
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    source = Path('artifacts/voice-audio/20260921-194750-54324d/events.jsonl')
    events = [json.loads(line) for line in source.read_text().splitlines()]
    trace = Trace('artifacts/addressing/raise-loop-v1')
    (trace.folder / 'probe.py').write_text(Path(__file__).read_text())
    token = CURRENT_TRACE.set(trace)
    records = []
    try:
        for seq in (59, 93):
            original = next(e['request'] for e in events if e['seq'] == seq)
            for mode in ('baseline', 'without_workbench_text', 'raise_scope', 'both'):
                body = copy.deepcopy(original)
                if mode in {'without_workbench_text', 'both'}:
                    for page in body['state']['observations']:
                        page['text'] = 'Agent recording/debugging workbench. Browser chrome controls remain in targets.'
                if mode in {'raise_scope', 'both'}:
                    for name, question in body['questions'].items():
                        if name == 'secondary_action_target':
                            for candidate in question['criteria'].values():
                                if candidate.get('role') == 'AXWindow':
                                    candidate['help'] = ('Raise changes native window stacking only. '
                                                         'It does not activate the application or navigate to a URL.')
                            body['state']['available_targets']['SECONDARY_ACTION'] = question['criteria']
                result = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                op, target = selected_answers(result['answers'], body['questions'])
                label = body['questions'].get(op['choice'].lower() + '_target', {}).get('criteria', {}).get(
                    (target or {}).get('choice'))
                row = {'seq': seq, 'mode': mode, 'operation': op['choice'], 'target': label}
                records.append(row)
                (trace.folder / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
