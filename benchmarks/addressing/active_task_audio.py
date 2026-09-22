"""Paid real GUI experiment: text-derived active task before Jev, production unchanged.

Pass the same WAV/output arguments as scripts.check_voice_audio. Original speech,
observations and history remain in each request. Cached summaries are per exact
speech ledger, not a substring or keyword based intent policy.
"""
import json
import os
import runpy
from functools import lru_cache

from jev_ultrafast import mixed
from jev_ultrafast.model import parse_text_argument, post_json
from jev_ultrafast.questions import SPEECH_CONTEXT

PROMPT = ('Convert chronological user speech into a concise statement of the currently authorized task. '
          'Keep all still-active requirements and resolve corrections and cancellations. Preserve references '
          'needed for the current task. Do not plan actions, choose tools, infer UI state, or carry over '
          'cancelled requests. If cancelled or incomplete, explicitly describe that status instead of an empty string. '
          'Return JSON with exactly one string key text; describe the task in the user language.'
          + '\n' + SPEECH_CONTEXT)


@lru_cache(maxsize=32)
def active_task(serialized_speech):
    result = post_json(os.environ['TEXT_MODEL_BASE_URL'].rstrip('/') + '/chat/completions',
                       os.environ['TEXT_MODEL_API_KEY'], {
        'model': os.environ['TEXT_MODEL'], 'max_tokens': 700,
        'reasoning': {'enabled': False}, 'response_format': {'type': 'json_object'},
        'messages': [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': serialized_speech}],
    })
    return parse_text_argument(result['choices'][0]['message']['content'])


def main():
    original = mixed.build_request

    def build(observation, bindings, speech, history, **kwargs):
        body, mapped = original(observation, bindings, speech, history, **kwargs)
        summary = active_task(json.dumps(speech, ensure_ascii=False, sort_keys=True))
        for question in body['questions'].values():
            instructions = question.get('instructions')
            if isinstance(instructions, dict) and 'goal' in instructions:
                instructions['goal'] = summary
        body['state']['active_task_summary'] = summary
        return body, mapped

    mixed.build_request = build
    try:
        runpy.run_module('scripts.check_voice_audio', run_name='__main__')
    finally:
        mixed.build_request = original


if __name__ == '__main__':
    main()
