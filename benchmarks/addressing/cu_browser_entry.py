"""Paid model-only replay of a recorded Chrome AX state; no tools execute."""
import json
from pathlib import Path

from jev_ultrafast.automatic import AutomaticDesktop
from jev_ultrafast.demo import load_environment
from jev_ultrafast.mixed import choose_mixed
from jev_ultrafast.relay import read_relay_page
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    load_environment()
    source = Path('artifacts/voice-audio/20260921-201134-56c122/events.jsonl')
    events = list(map(json.loads, source.read_text().splitlines()))
    sample = next(e for e in events if e['seq'] == 51)
    tools = dict.fromkeys(['click', 'set_value', 'press_key', 'type_text',
                          'perform_secondary_action', 'scroll', 'select_text'], {})
    page = read_relay_page(sample['result'], 'com.google.Chrome', None, tools, full_tools=True,
                           excluded_origins=('http://127.0.0.1:8767',))
    assert page['agent_control_surface']
    catalog = next(e for e in events if e['type'] == 'inventory')
    catalog = {**catalog, 'tabs': [], 'browser_connected': None, 'execution_mode': 'computer_use', 'errors': []}
    trace = Trace('artifacts/addressing/cu-browser-entry-v1')
    token = CURRENT_TRACE.set(trace)
    try:
        desktop = AutomaticDesktop(lambda: catalog, [], trace)
        key = desktop.key('app:com.google.Chrome')
        class Frozen:
            def observe(self, screenshot=False):
                return page
        desktop.adapters[key] = Frozen()
        desktop.active = key
        observation = desktop.observe()
        # Discard the live read-only foreground sensor; this fixture does not assert focus.
        observation['desktop_focus'] = None
        trace.emit('fixture', source=str(source), seq=sample['seq'], observation=observation)
        for text in ['打开浏览器，打开谷歌。', '打开一个新窗口，访问 https://www.google.com。',
                     'Open a new browser window and open Google in it.']:
            for repeat in range(2):
                speech = [{'id': 's', 'text': text, 'final': True, 'source': 'voice', 'version': 1}]
                action, detail = choose_mixed(observation, desktop.bindings, speech, [])
                trace.emit('choice', version=1, action=action, **detail)
                print(json.dumps({'text': text, 'repeat': repeat, 'action': action,
                                  'latency_ms': detail['latency_ms']}, ensure_ascii=False), flush=True)
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
