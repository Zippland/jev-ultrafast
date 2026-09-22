"""Bridge-owned literal replacement via public CU primitives; never submits or retries."""
from .mcp_client import MCPError
from .tracing import CURRENT_TRACE


def replace_field(page, target, text, read, call):
    if not isinstance(text, str) or not text:
        raise ValueError('Keyboard fill requires a nonempty literal value')
    if target['role'] != 'AXTextArea' and any(c in text for c in '\r\n\t'):
        raise ValueError('Multiline/control text requires an observed multiline field')
    identity = (page['relay_pid'], page['title'])

    def field(state, *, require_focus=False):
        if state.get('agent_control_surface') or (state['relay_pid'], state['title']) != identity:
            raise MCPError('Keyboard fill window changed; no further input permitted')
        fields = [a for a in state['actions'] if a['kind'] == 'fill']
        candidates = [a for a in fields if all(a[k] == target[k] for k in ('role', 'label'))]
        if len(candidates) != 1:
            raise MCPError('Keyboard fill target is missing or ambiguous; no further input permitted')
        current = candidates[0]
        if require_focus and state['focused_index'] != current['element_index']:
            selection = state.get('selected_text')
            owners = [a for a in fields if selection and selection in a['value']]
            if state['focused_index'] is not None or owners != [current]:
                raise MCPError('Keyboard fill focus cannot be established; no further input permitted')
            trace = CURRENT_TRACE.get()
            if trace:
                trace.emit('fill.focus_inferred', element_index=current['element_index'],
                           basis='unique editable field containing current selected text')
        return current

    def step(tool, arguments):
        call(tool, arguments)
        return read()

    current = field(page)
    if current['value'] == text:
        return page
    if page['focused_index'] != current['element_index']:
        page = step('click', {'element_index': current['element_index']})
        current = field(page, require_focus=True)
        if current['value'] != target['value']:
            raise MCPError('Keyboard fill value changed while focusing; no text typed')
    if current['value'] and page.get('selected_text') != current['value']:
        page = step('press_key', {'key': 'super+a'})
        current = field(page, require_focus=True)
        if current['value'] != target['value'] or page.get('selected_text') != current['value']:
            raise MCPError('Keyboard fill full selection not verified; no text typed')
    field(page, require_focus=True)
    page = step('type_text', {'text': text})
    current = field(page)
    selection = page.get('selected_text')
    if current['value'] != text and selection and current['value'] == text + selection:
        field(page, require_focus=True)
        page = step('press_key', {'key': 'BackSpace'})
        current = field(page)
    if current['value'] != text:
        raise MCPError('Keyboard fill literal readback differs; no retry or submission performed')
    return page
