import copy

import pytest

from jev_ultrafast.browser import StalePage
from jev_ultrafast.mcp_client import MCPError
from jev_ultrafast.relay import RelayComputer, read_relay_page

APP = "com.apple.TextEdit"
TITLE = "TEST.txt"
TOOLS = {name: {"inputSchema": {"properties": dict.fromkeys(fields, {})}} for name, fields in {
    "get_app_state": ["app"], "click": ["app", "element_index"],
    "set_value": ["app", "element_index", "value"],
    "perform_secondary_action": ["app", "element_index", "action"],
}.items()}


def result(value="第一行\n第二行", title=TITLE):
    return {"content": [{"type": "text", "text": (
        "Computer Use state (CUA App Version: 1001103)\n<app_state>\n"
        f"App=/System/Applications/TextEdit.app/ (bundleID {APP}, pid 42)\n"
        f'Window: "{title}", App: 文本编辑.\n'
        f"0 标准窗口 {title}, URL: file:///TEST.txt, Secondary Actions: Raise\n"
        "\t1 滚动区 Secondary Actions: Scroll Up, Scroll Down\n"
        f"\t\t2 文本输入区 (settable) Value: {value}, ID: First Text View\n"
        "\t3 按钮 (disabled) 不可用\n"
        "\t4 关闭按钮\n"
        "5 menu bar\n\t6 文件\n\n"
        f"The focused UI element is 2 文本输入区 (settable) Value: {value}, ID: First Text View\n"
        "</app_state>"
    )}]}


class Client:
    tools = TOOLS

    def __init__(self, state=None, fail=False):
        self.state = state or result()
        self.calls = []
        self.fail = fail

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_app_state":
            return copy.deepcopy(self.state)
        if self.fail:
            raise MCPError("uncertain native response")
        return {"content": [{"type": "text", "text": "ok"}]}


def test_multiline_values_and_supported_targets_are_preserved():
    page = read_relay_page(result(), APP, TITLE, TOOLS, document_url="file:///TEST.txt")
    fields = [a for a in page["actions"] if a["kind"] == "fill"]
    assert len(fields) == 1 and fields[0]["value"] == "第一行\n第二行"
    assert fields[0]["element_index"] == "2"
    assert not any(a["element_index"] in {"3", "4", "5", "6"} for a in page["actions"])
    assert {a.get("relay_secondary_action") for a in page["actions"]} >= {"Scroll Up", "Scroll Down"}


def test_native_selection_is_preserved_and_part_of_state_freshness():
    response = result()
    response['content'][0]['text'] += '\nSelected text: ```\n/search?q=old\n```\n\nNote: Selection metadata.'
    page = read_relay_page(response, APP, TITLE, TOOLS)
    assert page['selected_text'] == '/search?q=old'
    changed = copy.deepcopy(response)
    changed['content'][0]['text'] = changed['content'][0]['text'].replace('/search?q=old', 'other selection')
    fresh = read_relay_page(changed, APP, TITLE, TOOLS)
    assert fresh['fingerprint'] != page['fingerprint']
    assert fresh['actions'] == page['actions']
    assert 'Selected text' not in fresh['text']
    from jev_ultrafast.mixed import observation_context
    context = observation_context({'surfaces': {'cu:app': page}}, {'app': {'name': 'Test'}})
    assert context[0]['selected_text'] == '/search?q=old'


def test_browser_link_and_combobox_wire_roles_are_actionable():
    response = result()
    raw = response['content'][0]['text']
    raw = raw.replace('2 文本输入区 (settable)', '2 组合框 (settable)')
    raw = raw.replace('3 按钮 (disabled) 不可用', '3 link Google 首页, Value: https://www.google.com/')
    response['content'][0]['text'] = raw
    page = read_relay_page(response, APP, TITLE, TOOLS)
    assert {a['kind'] for a in page['actions'] if a['element_index'] == '2'} == {'click', 'fill'}
    link = next(a for a in page['actions'] if a['element_index'] == '3')
    assert link['role'] == 'AXLink' and link['kind'] == 'click'
    assert link['value'] == 'https://www.google.com/'


def test_official_action_return_tree_has_the_same_identity_and_values():
    response = result()
    text = response["content"][0]["text"].split("<app_state>\n", 1)[1].split("</app_state>", 1)[0]
    text = text.replace(f"App=/System/Applications/TextEdit.app/ (bundleID {APP}, pid 42)", f"App={APP} (pid 42)")
    response["content"][0]["text"] = text
    page = read_relay_page(response, APP, TITLE, TOOLS, document_url="file:///TEST.txt")
    assert next(a for a in page["actions"] if a["kind"] == "fill")["value"] == "第一行\n第二行"


def test_control_help_survives_bridge_and_target_question():
    from jev_ultrafast.model import operation_questions

    response = result()
    response["content"][0]["text"] = response["content"][0]["text"].replace(
        "\t3 按钮 (disabled) 不可用",
        "\t3 按钮 Description: 删除, Help: 删除输入的上个数字或操作（长按全部删除）, ID: Delete")
    page = read_relay_page(response, APP, TITLE, TOOLS)
    action = next(a for a in page["actions"] if a["element_index"] == "3")
    assert action["help"] == "删除输入的上个数字或操作（长按全部删除）"
    assert action["help"] in page["text"]
    questions = operation_questions({"CLICK": "Click"}, {"CLICK": {"1": action}}, "clear")
    assert questions["click_target"]["criteria"]["1"]["help"] == action["help"]


def test_wrong_window_document_or_ambiguous_indices_fail_before_action():
    with pytest.raises(MCPError, match="key window"):
        read_relay_page(result(title="USER.txt"), APP, TITLE, TOOLS)
    with pytest.raises(MCPError, match="document URL"):
        read_relay_page(result(), APP, TITLE, TOOLS, document_url="file:///USER.txt")
    broken = result()
    broken["content"][0]["text"] = broken["content"][0]["text"].replace("\t3 按钮", "\t9 按钮")
    with pytest.raises(MCPError, match="Ambiguous"):
        read_relay_page(broken, APP, TITLE, TOOLS)


def test_dispatch_reacquires_state_and_passes_only_public_arguments():
    client = Client()
    adapter = RelayComputer(APP, expected_title=TITLE, client=client)
    page = adapter.observe()
    field = next(a for a in page["actions"] if a["kind"] == "fill")
    adapter.act(field, page, text="新值")
    assert [name for name, _ in client.calls] == ["get_app_state", "get_app_state", "set_value"]
    assert client.calls[-1][1] == {"app": APP, "element_index": "2", "value": "新值"}


def test_changed_value_prevents_dispatch_and_uncertain_action_is_not_retried():
    client = Client()
    adapter = RelayComputer(APP, expected_title=TITLE, client=client)
    page = adapter.observe()
    field = next(a for a in page["actions"] if a["kind"] == "fill")
    client.state = result("外部修改")
    with pytest.raises(StalePage):
        adapter.act(field, page, text="新值")
    assert all(name == "get_app_state" for name, _ in client.calls)
    client.state, client.fail = result(), True
    with pytest.raises(MCPError, match="uncertain"):
        adapter.act(field, page, text="新值")
    assert [name for name, _ in client.calls].count("set_value") == 1
    assert adapter.events[-1]["status"] == "uncertain"


def test_per_app_connections_do_not_share_action_leases():
    from jev_ultrafast.mcp_client import AppMCPClient

    class LeaseClient(Client):
        def __init__(self, command, timeout):
            super().__init__()
            self.closed = False
            self.app = None

        def call(self, name, arguments):
            if name == "get_app_state":
                self.app = arguments["app"]
            else:
                assert self.app == arguments["app"]
                self.app = None
            return super().call(name, arguments)

        def close(self):
            self.closed = True

    client = AppMCPClient(["one", "two"], ["offline"], factory=LeaseClient)
    client.call("get_app_state", {"app": "one"})
    client.call("get_app_state", {"app": "two"})
    client.call("click", {"app": "one", "element_index": "2"})
    assert client.clients["two"].app == "two"
    with pytest.raises(MCPError, match="bound"):
        client.call("click", {"app": "foreign", "element_index": "2"})
    client.close()
    assert client.closed and all(c.closed for c in client.clients.values())


def test_stale_replan_reuses_read_but_dispatch_still_checks_new_external_changes():
    client = Client()
    adapter = RelayComputer(APP, expected_title=TITLE, client=client)
    page = adapter.observe()
    field = next(a for a in page["actions"] if a["kind"] == "fill")
    client.state = result("第一次外部修改")
    with pytest.raises(StalePage):
        adapter.act(field, page, text="不应写入")
    page = adapter.observe()
    assert len(client.calls) == 2
    field = next(a for a in page["actions"] if a["kind"] == "fill")
    assert field["value"] == "第一次外部修改"

    client.state = result("第二次外部修改")
    with pytest.raises(StalePage):
        adapter.act(field, page, text="仍不应写入")
    assert len(client.calls) == 3
    page = adapter.observe()
    assert "第二次外部修改" in page["text"]
    assert len(client.calls) == 3
    # The snapshot is single-use. An unrelated later observation reads again.
    client.state = result("第三次外部修改")
    assert "第三次外部修改" in adapter.observe()["text"]
    assert len(client.calls) == 4
    assert all(name == "get_app_state" for name, _ in client.calls)


def test_returned_tree_drives_next_choice_but_next_action_still_reacquires_lease():
    class ReturningClient(Client):
        def call(self, name, arguments):
            response = super().call(name, arguments)
            if name == "set_value":
                self.state = result(arguments["value"])
                response = copy.deepcopy(self.state)
                body = response["content"][0]["text"]
                response["content"][0]["text"] = body.split("<app_state>\n", 1)[1].split("</app_state>", 1)[0]
            return response

    client = ReturningClient()
    adapter = RelayComputer(APP, expected_title=TITLE, client=client)
    page = adapter.observe()
    field = next(a for a in page["actions"] if a["kind"] == "fill")
    adapter.act(field, page, text="第一步")
    calls = len(client.calls)
    page = adapter.observe()
    assert len(client.calls) == calls
    field = next(a for a in page["actions"] if a["kind"] == "fill")
    assert field["value"] == "第一步"
    client.state = result("外部修改")
    with pytest.raises(StalePage):
        adapter.act(field, page, text="不能覆盖")
    assert client.calls[-1][0] == "get_app_state"
    assert sum(name == "set_value" for name, _ in client.calls) == 1


def test_window_raise_describes_its_scope_without_changing_the_public_tool():
    from jev_ultrafast.model import operation_questions
    from jev_ultrafast.relay import RAISE_HELP

    client = Client()
    adapter = RelayComputer(APP, expected_title=TITLE, client=client, full_tools=True)
    page = adapter.observe()
    action = next(a for a in page['actions'] if a.get('relay_secondary_action') == 'Raise')
    questions = operation_questions({'SECONDARY_ACTION': 'Observed accessibility action'},
                                    {'SECONDARY_ACTION': {'1': action}}, 'show the app')
    assert questions['secondary_action_target']['criteria']['1']['help'] == RAISE_HELP
    adapter.act(action, page)
    assert client.calls[-1] == ('perform_secondary_action', {'app': APP, 'element_index': '0', 'action': 'Raise'})


def test_only_owned_control_origin_is_excluded_and_native_indices_remain_valid():
    raw = (f'App={APP} (pid 42)\nWindow: "{TITLE}", App: 文本编辑.\n'
           '0 标准窗口 TEST.txt\n'
           '\t1 按钮 新标签页\n'
           '\t2 HTML 内容 Debug, URL: 127.0.0.1:8767/\n'
           '\t\t3 文本输入区 (settable) 调试指令, Value: internal-old\n'
           '\t\t4 按钮 关闭麦克风\n'
           '\t5 HTML 内容 Task, URL: https://example.test/task\n'
           '\t\t6 文本输入区 (settable) 调试指令, Value: user-data\n'
           '7 menu bar\n'
           'The focused UI element is 3 文本输入区')
    tools = {**TOOLS, 'press_key': {}, 'type_text': {}}

    def read(text=raw):
        return read_relay_page({'content': [{'type': 'text', 'text': text}]}, APP, TITLE, tools,
                               full_tools=True, excluded_origins=('http://127.0.0.1:8767',))

    page = read()
    assert 'internal-old' not in page['text'] and '关闭麦克风' not in page['text']
    assert 'user-data' in page['text']
    assert {a['element_index'] for a in page['actions']} == {'0', '1', '6'}
    assert not any(a['kind'] in {'key', 'insert'} and not a.get('control_surface_exit') for a in page['actions'])
    from jev_ultrafast.actions import observed_action, parameters_for
    escape = next(a for a in page['actions'] if a.get('control_surface_exit'))
    assert set(parameters_for(escape)['key']) == {'NEW_DOCUMENT'}
    with pytest.raises(ValueError):
        observed_action({**escape, 'parameters': {'key': 'ENTER'}}, page['actions'])
    client = Client({'content': [{'type': 'text', 'text': raw}]})
    client.tools = tools
    adapter = RelayComputer(APP, expected_title=TITLE, client=client, full_tools=True,
                            excluded_origins=('http://127.0.0.1:8767',))
    adapter.act(escape, page)
    assert client.calls[-1] == ('press_key', {'app': APP, 'key': 'super+n'})
    assert read(raw.replace('internal-old', 'internal-new'))['fingerprint'] == page['fingerprint']
    assert read(raw.replace('user-data', 'changed-task'))['fingerprint'] != page['fingerprint']
    focused_task = read(raw.replace('focused UI element is 3', 'focused UI element is 6'))
    assert any(a['kind'] == 'insert' and a['element_index'] == '6' for a in focused_task['actions'])


def test_locked_mac_is_reported_as_execution_environment_error():
    locked = {'content': [{'type': 'text', 'text':
               'The Mac is locked and this Computer Use request cannot be associated with a ChatGPT thread.'}]}
    client = Client(state=locked)
    adapter = RelayComputer(APP, expected_title=TITLE, client=client)
    with pytest.raises(MCPError, match='Mac 已锁屏'):
        adapter.observe()
    assert [tool for tool, _ in client.calls] == ['get_app_state']
    # Document text resembling an error is still app data, not an environment signal.
    page = read_relay_page(result('The Mac is locked'), APP, TITLE, TOOLS)
    assert next(a for a in page['actions'] if a['kind'] == 'fill')['value'] == 'The Mac is locked'


def test_keyboard_fill_is_bridge_owned_and_cancelled_before_primitives():
    from jev_ultrafast.browser import DispatchCancelled
    client = Client()
    client.state['content'][0]['text'] = client.state['content'][0]['text'].replace('文本输入区', '文本栏')
    client.tools = {**TOOLS, **{name: {'inputSchema': {'properties': dict.fromkeys(fields, {})}}
                             for name, fields in {'press_key': ['app', 'key'],
                                                  'type_text': ['app', 'text']}.items()}}
    adapter = RelayComputer(APP, expected_title=TITLE, client=client, keyboard_fill=True)
    page = adapter.observe()
    field = next(a for a in page['actions'] if a['kind'] == 'fill')
    assert field['tool'] == 'field_fill'
    def cancel():
        raise DispatchCancelled('cancelled')
    with pytest.raises(DispatchCancelled):
        adapter.act(field, page, text='new', before_dispatch=cancel)
    assert [name for name, _ in client.calls] == ['get_app_state', 'get_app_state']
    assert adapter.events == []


def test_multiline_editor_keeps_native_unicode_setter():
    page = read_relay_page(result(), APP, TITLE, TOOLS, keyboard_fill=True)
    field = next(a for a in page['actions'] if a['kind'] == 'fill')
    assert field['role'] == 'AXTextArea' and field['tool'] == 'set_value'


def test_document_location_is_not_confused_with_editable_address():
    response = result()
    raw = response['content'][0]['text'].replace('3 按钮 (disabled) 不可用',
                                               '3 HTML 内容 New Tab, URL: chrome://new-tab-page/')
    response['content'][0]['text'] = raw
    page = read_relay_page(response, APP, TITLE, TOOLS)
    assert 'url=chrome://new-tab-page/' in page['text']
    assert 'url=file:///TEST.txt' in page['text']


def test_single_line_unicode_uses_native_setter_without_keyboard_input():
    client = Client()
    client.state['content'][0]['text'] = client.state['content'][0]['text'].replace('文本输入区', '文本栏')
    client.tools = {**TOOLS, **{name: {'inputSchema': {'properties': dict.fromkeys(fields, {})}}
                             for name, fields in {'press_key': ['app', 'key'],
                                                  'type_text': ['app', 'text']}.items()}}
    adapter = RelayComputer(APP, expected_title=TITLE, client=client, keyboard_fill=True)
    page = adapter.observe()
    field = next(a for a in page['actions'] if a['kind'] == 'fill')
    adapter.act(field, page, text='语音联动成功')
    assert client.calls[-1] == ('set_value', {'app': APP, 'element_index': '2', 'value': '语音联动成功'})
    assert [name for name, _ in client.calls] == ['get_app_state', 'get_app_state', 'set_value']


def test_unicode_keyboard_insert_rejected_before_dispatch_but_ascii_supported():
    client = Client()
    client.tools = {**TOOLS, 'type_text': {'inputSchema': {'properties': {'app': {}, 'text': {}}}}}
    adapter = RelayComputer(APP, expected_title=TITLE, client=client, full_tools=True)
    page = adapter.observe()
    action = next(a for a in page['actions'] if a['kind'] == 'insert')
    assert 'ASCII' in action['help'] and 'TYPE_TEXT' in action['help']
    with pytest.raises(ValueError, match='尚未派发输入'):
        adapter.act(action, page, text='继续中文')
    assert adapter.events == []
    assert all(name == 'get_app_state' for name, _ in client.calls)
    adapter.act(action, page, text='12+7=')
    assert client.calls[-1] == ('type_text', {'app': APP, 'text': '12+7='})


def test_cancelled_speech_gate_reuses_read_for_replanning_but_not_next_dispatch():
    from jev_ultrafast.browser import DispatchCancelled
    client = Client()
    adapter = RelayComputer(APP, expected_title=TITLE, client=client)
    page = adapter.observe()
    field = next(a for a in page['actions'] if a['kind'] == 'fill')
    def cancel():
        raise DispatchCancelled('new speech')
    with pytest.raises(DispatchCancelled):
        adapter.act(field, page, text='old', before_dispatch=cancel)
    refreshed = adapter.observe()
    assert [name for name, _ in client.calls] == ['get_app_state', 'get_app_state']
    adapter.act(field, refreshed, text='new')
    assert [name for name, _ in client.calls] == ['get_app_state', 'get_app_state', 'get_app_state', 'set_value']
    assert adapter.rejected_observation is None


def test_observed_path_routes_calls_but_bundle_identity_is_still_verified():
    client = Client()
    path = "/System/Applications/TextEdit.app/"
    relay = RelayComputer(APP, expected_title=TITLE, client=client, app_selector=path)
    page = relay.observe()
    action = next(a for a in page["actions"] if a["kind"] == "fill")
    relay.act(action, page, text="changed")
    assert client.calls
    assert all(args["app"] == path for _, args in client.calls)
    assert relay.events[-1]["arguments"]["app"] == path
    client.state["content"][0]["text"] = client.state["content"][0]["text"].replace(APP, "other.app")
    with pytest.raises((ValueError, StalePage, MCPError)):
        relay.observe()
