"""Frozen native Chrome observations, with/without this agent's own UI subtree."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jev_ultrafast.automatic import AutomaticDesktop
from jev_ultrafast.demo import load_environment
from jev_ultrafast.mixed import build_request
from jev_ultrafast.model import post_json, selected_answers
from jev_ultrafast.relay import read_relay_page
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    events = [json.loads(line) for line in Path(
        'artifacts/voice-audio/20260921-200336-74185d/events.jsonl').read_text().splitlines()]
    cases = [e for e in events if e['type'] == 'choice' and e['operation'] == 'SECONDARY_ACTION'][:2]
    trace = Trace('artifacts/addressing/self-observation-v1')
    (trace.folder / 'probe.py').write_text(Path(__file__).read_text())
    token = CURRENT_TRACE.set(trace)
    rows = []
    try:
        for case in cases:
            inventory = [e for e in events if e['seq'] < case['seq'] and e['type'] == 'inventory'][-1]
            observed = [e for e in events if e['seq'] < case['seq'] and e['type'] == 'observation.end'][-1]
            page = next(iter(observed['pages'].values()))
            tools = {action['tool']: {} for action in page['actions'] if 'tool' in action}
            state = case['request']['state']
            focus = (state.get('desktop_focus', {}).get('frontmost_app') or {}).get('id')
            for name, origins in [('baseline', ()), ('exclude_control_ui', ('http://127.0.0.1:8767',))]:
                parsed = read_relay_page({'content': [{'type': 'text', 'text': page['relay_text']}]},
                    'com.google.Chrome', None, tools, full_tools=True, excluded_origins=origins)
                desktop = AutomaticDesktop(lambda: inventory, [], trace)
                desktop.active = desktop.key('app:com.google.Chrome')
                desktop.adapters[desktop.active] = SimpleNamespace(observe=lambda screenshot=False: parsed)
                with patch('jev_ultrafast.automatic.foreground_app', return_value=focus):
                    observation = desktop.observe()
                body, mapped = build_request(observation, desktop.bindings, state['speech'], state['executed_actions'])
                result = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
                op, target = selected_answers(result['answers'], body['questions'])
                action = mapped.get(op['choice'].lower() + '_target', {}).get((target or {}).get('choice'))
                row = {'source_seq': case['seq'], 'variant': name, 'operation': op['choice'],
                       'target': action and action['label'], 'page_text_chars': len(parsed['text']),
                       'targets': len(observation['actions'])}
                rows.append(row)
                (trace.folder / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
