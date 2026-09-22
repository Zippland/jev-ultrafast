"""Paid frozen-state diagnostics; variants are probes, not production context pruning."""
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.tracing import CURRENT_TRACE, Trace

TRANSLATIONS = [
    'Please clear the calculator, then calculate two plus three. Stop.',
    'Do not continue. Cancel that calculation.',
    'Now start again. Clear the calculator, then calculate seven plus eight.',
]


def replace_speech(value, speech):
    if isinstance(value, dict):
        return {k: speech if k in {'speech', 'chronological_current_speech'} else replace_speech(v, speech)
                for k, v in value.items()}
    if isinstance(value, list):
        return [replace_speech(v, speech) for v in value]
    return value


def main():
    load_environment()
    source = Path('artifacts/voice-audio/20260921-204734-265ab1/events.jsonl')
    choices = [e for e in map(json.loads, source.read_text().splitlines()) if e['type'] == 'choice']
    samples = [next(e for e in choices if e['seq'] == 173), choices[-1]]
    trace = Trace('artifacts/addressing/restart-context-v1')
    token = CURRENT_TRACE.set(trace)
    rows = []
    try:
        for sample in samples:
            for variant in ['baseline', 'latest_speech_only', 'translated_speech', 'without_history']:
                body = copy.deepcopy(sample['request'])
                speech = body['state']['speech']
                if variant == 'latest_speech_only':
                    body = replace_speech(body, speech[-1:])
                elif variant == 'translated_speech':
                    assert len(speech) == len(TRANSLATIONS)
                    speech = [{**s, 'text': text} for s, text in zip(speech, TRANSLATIONS, strict=True)]
                    body = replace_speech(body, speech)
                elif variant == 'without_history':
                    body['state']['executed_actions'] = []
                response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                op, target = selected_answers(response['answers'], body['questions'])
                label = (body['questions'][op['choice'].lower() + '_target']['criteria'][target['choice']]
                         if target else None)
                row = {'seq': sample['seq'], 'variant': variant, 'operation': op['choice'], 'target': label}
                rows.append(row)
                trace.emit('evaluation', **row)
                (trace.folder / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
