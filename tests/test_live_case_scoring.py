import pytest

from scripts.check_live_browser_cases import calculator_matches


@pytest.mark.parametrize('observed,expected,passed', [
    ('(-8)', '-8', True), ('-8', '-8', True), ('0.2500', '0.25', True),
    ('19', '19', True), ('4×7', '28', False), ('ln(28)', '28', False),
    ('(4+7)', '11', False), ('(-9)', '-8', False), ('NaN', '0', False),
    ('Infinity', '0', False), (None, '0', False), ('', '0', False),
])
def test_numeric_display_not_expression_evaluation(observed, expected, passed):
    assert calculator_matches(observed, expected) is passed


def test_display_excludes_history_and_expression():
    from scripts.check_live_browser_cases import calculator_value
    page = {'text': '\n'.join([
        '[0] AXWindow 计算器 parent=none value= actions=',
        '[1] AXScrollArea 结果 parent=0 value= actions=',
        '[2] AXStaticText  parent=1 value=4×7 actions=',
        '[3] AXScrollArea 输入 parent=0 value= actions=',
        '[4] AXGroup  parent=3 value= actions=',
        '[5] AXStaticText  parent=4 value=\u200e28 actions=',
        '[6] AXStaticText  parent=0 value=999 actions=',
    ])}
    assert calculator_value(page) == '28'
    page['text'] += '\n[7] AXStaticText  parent=3 value=2 actions='
    with pytest.raises(ValueError, match='ambiguous'):
        calculator_value(page)


def test_textedit_body_is_exact_and_bound_to_document_url():
    from jev_ultrafast.mcp_client import MCPError
    from jev_ultrafast.relay import read_relay_page
    from scripts.check_live_browser_cases import text_document_value
    raw = ('App=com.apple.TextEdit (pid 42)\nWindow: "Test.txt", App: 文本编辑.\n'
           '0 标准窗口 Test.txt, URL: file:///owned/Test.txt\n'
           '\t1 文本输入区 (settable) Value: 甲方\n乙方, ID: body\n')
    def parse(text):
        return read_relay_page({'content': [{'type': 'text', 'text': text}]},
                               'com.apple.TextEdit', None, {'set_value': {}}, full_tools=True)
    page = parse(raw)
    assert text_document_value(page, 'file:///owned/Test.txt') == '甲方\n乙方'
    with pytest.raises(MCPError, match='document URL'):
        text_document_value(parse(raw.replace('Test.txt\n', 'Test.txt.backup\n')), 'file:///owned/Test.txt')
    with pytest.raises(MCPError, match='document URL'):
        text_document_value(page, 'file:///other/Test.txt')
    page['actions'] = []
    with pytest.raises(ValueError, match='unique writable body'):
        text_document_value(page, 'file:///owned/Test.txt')


def test_stage_gated_speech_cannot_be_overtaken_by_later_timed_correction():
    from scripts.check_live_browser_cases import ready_events
    events = [{'at_ms': 0}, {'at_ms': 0, 'during': 'choice'}, {'at_ms': 1000}]
    assert list(ready_events(events, [{'index': 0}], 1500, 0, None, 2)) == []
    assert [i for i, _ in ready_events(events, [{'index': 0}], 2200, 0, 'choice', .2)] == [1, 2]
    assert list(ready_events(events, [{'index': 0}], 2200, 0, 'choice', .05)) == []


def test_action_gated_speech_preserves_order_and_does_not_repeat_delivered_events():
    from scripts.check_live_browser_cases import ready_events
    events = [{'at_ms': 0}, {'at_ms': 0, 'after_actions': 2}, {'at_ms': 50}]
    assert list(ready_events(events, [{'index': 0}], 1000, 1, None, 2)) == []
    assert [i for i, _ in ready_events(events, [{'index': 0}], 1000, 2, None, 2)] == [1, 2]
    assert list(ready_events(events, [{'index': 0}, {'index': 1}, {'index': 2}], 2000, 3, None, 2)) == []
