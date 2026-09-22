"""Literal CU replacement invariants; no desktop or model calls."""
import copy

import pytest

from jev_ultrafast.keyboard_fill import replace_field
from jev_ultrafast.mcp_client import MCPError


class Editor:
    def __init__(self, value='', suffix=''):
        self.target = {'kind': 'fill', 'label': 'Field', 'role': 'AXTextArea',
                       'value': value, 'element_index': '9'}
        self.state = {'relay_pid': 1, 'title': 'Document', 'actions': [copy.deepcopy(self.target)],
                      'focused_index': '9', 'selected_text': None}
        self.calls = []
        self.suffix = suffix

    def read(self):
        return copy.deepcopy(self.state)

    def call(self, tool, args):
        self.calls.append((tool, args))
        field = self.state['actions'][0]
        if tool == 'press_key' and args['key'] == 'super+a':
            self.state['selected_text'] = field['value']
        elif tool == 'type_text':
            field['value'] = args['text'] + self.suffix
            self.state['selected_text'] = self.suffix or None
            self.state['focused_index'] = None if self.suffix else '9'
        elif tool == 'press_key' and args['key'] == 'BackSpace':
            field['value'] = field['value'][:-len(self.state['selected_text'])]
            self.state['selected_text'] = None
            self.state['focused_index'] = '9'
        elif tool == 'click':
            self.state['focused_index'] = args['element_index']
        else:
            raise AssertionError('Unexpected primitive')

    def replace(self, text):
        return replace_field(self.read(), self.target, text, self.read, self.call)


def test_literal_fill_removes_only_selected_extra_suffix_and_never_submits():
    editor = Editor(suffix='/search?q=old')
    result = editor.replace('https://example.org')
    assert result['actions'][0]['value'] == 'https://example.org'
    assert editor.calls == [('type_text', {'text': 'https://example.org'}), ('press_key', {'key': 'BackSpace'})]


def test_existing_multiline_text_is_replaced_after_verified_full_selection():
    editor = Editor('旧内容\n第二行')
    result = editor.replace('新内容\n第二行')
    assert result['actions'][0]['value'] == '新内容\n第二行'
    assert editor.calls[0] == ('press_key', {'key': 'super+a'})
    assert len(editor.calls) == 2


def test_already_satisfied_field_requires_no_mutation():
    editor = Editor('same')
    editor.replace('same')
    assert editor.calls == []


def test_lost_selection_prevents_typing_and_is_not_retried():
    editor = Editor('old')
    def call(tool, args):
        editor.calls.append((tool, args))
        editor.state['selected_text'] = 'o'
    with pytest.raises(MCPError, match='full selection'):
        replace_field(editor.read(), editor.target, 'new', editor.read, call)
    assert editor.calls == [('press_key', {'key': 'super+a'})]


def test_changed_window_after_focus_does_not_receive_text():
    editor = Editor('old')
    editor.state['focused_index'] = None
    def call(tool, args):
        editor.calls.append((tool, args))
        editor.state['title'] = 'Other document'
    with pytest.raises(MCPError, match='window changed'):
        replace_field(editor.read(), editor.target, 'new', editor.read, call)
    assert len(editor.calls) == 1 and editor.calls[0][0] == 'click'


def test_input_failure_never_replays_mutation():
    editor = Editor()
    def call(tool, args):
        editor.calls.append((tool, args))
        raise MCPError('uncertain transport result')
    with pytest.raises(MCPError, match='uncertain'):
        replace_field(editor.read(), editor.target, 'new', editor.read, call)
    assert len(editor.calls) == 1


def test_ambiguous_selection_does_not_delete_text():
    editor = Editor(suffix='suffix')
    original = editor.call
    def call(tool, args):
        original(tool, args)
        editor.state['actions'].append({**editor.target, 'label': 'Other', 'value': 'suffix'})
    with pytest.raises(MCPError, match='focus'):
        replace_field(editor.read(), editor.target, 'new', editor.read, call)
    assert len(editor.calls) == 1
