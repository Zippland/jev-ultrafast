"""Manual paid text-provider comparison; frozen requests, no desktop operations."""
import argparse
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import MissingTextArgument, field_text
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--models', nargs='+', default=['inception/mercury-2.5', 'google/gemini-2.5-flash-lite'])
    args = parser.parse_args()
    load_environment()
    source = Path('artifacts/voice-audio/20260921-220028-9abf5d/events.jsonl')
    events = [json.loads(line) for line in source.open()]
    body = next(e['request']['body'] for e in events
                if e['type'] == 'model.start' and 'messages' in e['request'].get('body', {}))
    original = json.loads(body['messages'][1]['content'])
    cases = [('recorded_google', original, ['https://www.google.com', 'https://www.google.com/'])]
    for name, speech, expected in [
        ('literal', ['在文本编辑里原样写：明天十点开会。'], ['明天十点开会。']),
        ('correction', ['写明天十点开会。', '改成十一点。'], ['明天十一点开会。']),
        ('quoted_stop', ['原样写下这句话：别再操作了，我说的是台词。'], ['别再操作了，我说的是台词。']),
        ('missing', ['在文本编辑里面写'], [None]),
    ]:
        context = copy.deepcopy(original)
        context['field'] = {'app': '文本编辑', 'label': '正文', 'role': 'AXTextArea', 'value': ''}
        context['page'] = {'title': 'Untitled', 'text': 'Empty text document'}
        context['recent_actions'] = []
        context['goal']['chronological_current_speech'] = [
            {'id': str(i), 'text': text, 'final': True, 'source': 'voice', 'version': i + 1}
            for i, text in enumerate(speech)]
        cases.append((name, context, expected))
    trace = Trace(args.output)
    token = CURRENT_TRACE.set(trace)
    rows = []
    try:
        for model in args.models:
            os.environ['TEXT_MODEL'] = model
            for name, context, expected in cases:
                details, error, value = {}, None, None
                try:
                    value, details = field_text(context)
                except MissingTextArgument:
                    pass
                except Exception as exc:
                    error = f'{type(exc).__name__}: {exc}'
                row = {'model': model, 'case': name, 'value': value, 'error': error,
                       'passed': error is None and value in expected, **details}
                rows.append(row)
                trace.emit('text_comparison', context=context, expected=expected, **row)
                print(json.dumps({k: v for k, v in row.items() if k not in ('usage', 'attempt_usage')},
                                 ensure_ascii=False), flush=True)
        (trace.folder / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
