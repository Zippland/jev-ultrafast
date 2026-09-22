"""Paid paired replay of frozen failures; never dispatches computer/browser actions."""
import copy
import json
import os
from pathlib import Path

from benchmarks.addressing.active_task_audio import active_task
from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    events = list(map(json.loads, Path(
        'artifacts/voice-audio/20260921-204734-265ab1/events.jsonl').read_text().splitlines()))
    trace = Trace('artifacts/addressing/active-task-repeat-v1')
    token = CURRENT_TRACE.set(trace)
    rows = []
    try:
        for seq in [173, 422]:
            original = next(e['request'] for e in events if e['seq'] == seq)
            summary = active_task(json.dumps(original['state']['speech'], ensure_ascii=False, sort_keys=True))
            for repeat in range(3):
                for variant in ['original', 'active_task']:
                    body = copy.deepcopy(original)
                    if variant == 'active_task':
                        for question in body['questions'].values():
                            instructions = question.get('instructions')
                            if isinstance(instructions, dict) and 'goal' in instructions:
                                instructions['goal'] = summary
                        body['state']['active_task_summary'] = summary
                    result = post_json('https://api.typesafe.ai/v1/systemone',
                                       os.environ['TYPESAFE_API_KEY'], body)
                    operation, target = selected_answers(result['answers'], body['questions'])
                    label = (body['questions'][operation['choice'].lower() + '_target']['criteria'][target['choice']]
                             if target else None)
                    row = dict(seq=seq, repeat=repeat, variant=variant, summary=summary,
                               operation=operation['choice'], target=label)
                    rows.append(row)
                    trace.emit('evaluation', **row)
                    (trace.folder / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                    print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
