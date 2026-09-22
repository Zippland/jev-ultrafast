"""Explicit real-CU keyboard replacement probe; no model calls or MCP modifications."""
import argparse
import json

import httpx

from jev_ultrafast.mcp_client import MCPClient
from jev_ultrafast.relay import read_relay_page
from jev_ultrafast.tracing import Trace, TracedMCP
from jev_ultrafast.voice_demo import relay_command
from scripts.check_voice_ui import Token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--value', required=True)
    args = parser.parse_args()
    def require_idle():
        with httpx.Client(base_url='http://127.0.0.1:8767', trust_env=False, timeout=5) as public:
            token = Token()
            token.feed(public.get('/').text)
            public.headers['X-Voice-Token'] = token.value
            state = public.get('/api/state').json()
            if state['voice']['status'] in {'loading', 'starting', 'recording', 'finishing'} or (
                    state.get('session') or {}).get('enabled'):
                raise RuntimeError('Public workbench active; no further test operation permitted')

    require_idle()
    trace = Trace(args.output)
    client = TracedMCP(MCPClient(relay_command(), timeout=60), trace)
    app = 'com.google.Chrome'

    def read():
        require_idle()
        return read_relay_page(client.call('get_app_state', {'app': app}), app, None,
                               client.tools, full_tools=True)

    def key(value):
        require_idle()
        client.call('press_key', {'app': app, 'key': value})
        return read()

    def focused(page, expected=None):
        fields = [a for a in page['actions'] if a['kind'] == 'fill']
        candidates = [a for a in fields if a['element_index'] == page['focused_index']]
        if page['focused_index'] is None and expected and page['selected_text']:
            # A diagnostic inference, recorded explicitly rather than reported as AX focus.
            candidates = [a for a in fields if page['selected_text'] in a['value']]
            if len(candidates) == 1 and all(candidates[0][k] == expected[k] for k in ('role', 'label')):
                trace.emit('focus_inference', basis='unique editable field containing current selected text',
                           element_index=candidates[0]['element_index'], selected_text=page['selected_text'])
            else:
                candidates = []
        if len(candidates) != 1:
            raise RuntimeError('No unique focused editable field; input stopped')
        return candidates[0]

    try:
        read()
        page = key('super+n')
        target = focused(page)
        page = key('super+a')
        field = focused(page, target)
        assert (field['role'], field['label']) == (target['role'], target['label'])
        assert not field['value'] or page['selected_text'] == field['value'], 'Full selection not verified'
        require_idle()
        client.call('type_text', {'app': app, 'text': args.value})
        page = read()
        field = focused(page, target)
        assert (field['role'], field['label']) == (target['role'], target['label'])
        # Remove only a fully observed, selected suffix beyond the requested literal.
        # This compares tool state, never parses user intent or recognizes websites.
        if page['selected_text'] and field['value'] == args.value + page['selected_text']:
            page = key('BackSpace')
            field = focused(page, target)
            assert (field['role'], field['label']) == (target['role'], target['label'])
        assert field['value'] == args.value, 'Literal replacement not verified; no submission'
        trace.emit('literal_verified', value=field['value'], title=page['title'])
        page = key('Return')
        result = {'title': page['title'], 'text': page['text'], 'value': args.value}
        trace.emit('final_observation', **result)
        (trace.folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({'title': page['title'], 'value_before_submission': args.value}, ensure_ascii=False))
    finally:
        client.close()


if __name__ == '__main__':
    main()
