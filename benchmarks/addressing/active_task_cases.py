"""Paid semantic handoff probes; expected meaning is for human review, not sent to models."""
import json

from benchmarks.addressing.active_task_audio import active_task
from jev_ultrafast.demo import load_environment
from jev_ultrafast.tracing import CURRENT_TRACE, Trace

CASES = [
    ('cancelled', ['在计算器里计算二加三。', '停，不要继续了。'], 'No pending calculation or undo.'),
    ('restart', ['在计算器里计算二加三。', '取消。', '现在重新开始，计算七加八。'], 'Only calculate seven plus eight.'),
    ('quoted_cancel', ['在文本编辑中写入这句话：“不要继续了，取消刚才的计算。”'],
     'Write the quoted sentence literally; do not cancel the writing task.'),
    ('background', ['在文本编辑里写入“项目进展顺利”。', '小王，你先不要继续了，等我开完会再说。'],
     'Preserve writing task; later sentence addresses Xiao Wang.'),
    ('multi_app_correction', ['把计算器里的结果写到文本编辑里，再打开浏览器查一下上海天气。',
                              '天气改成北京，其他照旧。'],
     'Preserve copying calculator result into TextEdit and change only weather city to Beijing.'),
    ('reference', ['在文本编辑写三行：周一开会，周二开发，周三测试。', '把第二行改成周二休息，其他不变。'],
     'Three lines retained: 周一开会 / 周二休息 / 周三测试.'),
    ('cancel_one_branch', ['在文本编辑写“完成”，再打开浏览器查天气。', '不用查天气了，文字照常写。'],
     'Write 完成; cancel only browser weather task.'),
    ('partial', ['在文本编辑中写入'], 'Incomplete dictation: content not provided; do not invent it.'),
]


def main():
    load_environment()
    trace = Trace('artifacts/addressing/active-task-coverage-v3')
    token = CURRENT_TRACE.set(trace)
    rows = []
    try:
        for name, utterances, expected in CASES:
            speech = [{'id': str(i), 'text': text, 'final': name != 'partial', 'source': 'voice', 'version': i + 1}
                      for i, text in enumerate(utterances)]
            row = {'case': name, 'speech': speech, 'expected_meaning': expected}
            try:
                row['summary'] = active_task(json.dumps(speech, ensure_ascii=False, sort_keys=True))
            except Exception as exc:
                row['error'] = f'{type(exc).__name__}: {exc}'
            rows.append(row)
            trace.emit('evaluation', **row)
            (trace.folder / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(json.dumps({k: row[k] for k in ('case', 'summary', 'error') if k in row},
                             ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
