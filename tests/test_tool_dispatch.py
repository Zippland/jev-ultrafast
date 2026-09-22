"""Offline dispatch boundaries: model values cannot turn into arbitrary tool arguments."""

import copy
import json

import httpx
import pytest
from test_relay import APP, TITLE, TOOLS, Client, result

from jev_ultrafast import mixed, model
from jev_ultrafast.actions import PARAMETERS, observed_action
from jev_ultrafast.browser_tools import destination_url
from jev_ultrafast.relay import RelayComputer, read_relay_page
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def answer(choice, choices):
    return {"choice": choice, "confidence": 1, "probabilities": {key: int(key == choice) for key in choices}}


def test_only_selected_tools_parameters_are_consumed(monkeypatch):
    action = {"id": "key", "kind": "key", "app_key": "app", "app_name": "App", "channel": "CU",
              "label": "Window", "surface": "cu:app"}
    observation = {"actions": [action, {**action, "id": "click", "kind": "click"}],
                   "surfaces": {"cu:app": {"title": "App", "text": "Observed"}}}
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline")

    def post(url, key, body):
        questions = body["questions"]
        return {"answers": {"operation": answer("PRESS_KEY", questions["operation"]["criteria"]),
                            "press_key_target": answer("1", ["1"]),
                            "press_key_key": answer("UNDO", PARAMETERS["key"]["key"]),
                            "click_style": {"choice": "not a supported parameter"}}}

    monkeypatch.setattr(mixed, "post_json", post)
    selected, details = mixed.choose_mixed(observation, {"app": {"name": "App"}}, [], [])
    assert selected["parameters"] == {"key": "UNDO"}
    assert observed_action(selected, observation["actions"]) == action
    assert details["selected_heads"] == ["operation", "press_key_target", "press_key_key"]
    selected["parameters"]["key"] = "arbitrary shell command"
    with pytest.raises(ValueError, match="parameters"):
        observed_action(selected, observation["actions"])


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi", "https://u:p@x/"])
def test_navigation_text_cannot_become_code_or_local_file(url):
    with pytest.raises(ValueError):
        destination_url(url)


class FullClient(Client):
    tools = {**TOOLS, **{name: {} for name in ("press_key", "type_text", "select_text", "scroll", "drag")}}


def test_native_insert_and_replace_use_different_tools():
    client = FullClient()
    adapter = RelayComputer(APP, expected_title=TITLE, client=client, full_tools=True)
    for kind, expected_tool, parameter in (("fill", "set_value", "value"), ("insert", "type_text", "text")):
        page = adapter.observe()
        action = next(a for a in page["actions"] if a["kind"] == kind)
        text = "保持原文" if kind == "fill" else "literal text"
        adapter.act(action, page, text=text)
        tool, args = client.calls[-1]
        assert tool == expected_tool and args[parameter] == text
        assert ("element_index" in args) == (kind == "fill")


def test_literal_keyboard_input_can_target_observed_window_without_editable_ax_field():
    state = {"content": [{"type": "text", "text": (
        f'App={APP} (pid 42)\nWindow: "{TITLE}", App: Test.\n'
        f'0 标准窗口 {TITLE}\n\t1 按钮 2\n'
    )}]}
    client = FullClient(state)
    adapter = RelayComputer(APP, expected_title=TITLE, client=client, full_tools=True)
    page = adapter.observe()
    action = next(a for a in page["actions"] if a["kind"] == "insert")
    assert action["role"] == "AXWindow" and action["node"] == "0"
    assert not any(a["kind"] == "fill" for a in page["actions"])
    adapter.act(action, page, text="2+3=")
    assert client.calls[-1] == ("type_text", {"app": APP, "text": "2+3="})


def test_native_text_selection_checks_exact_observed_text_before_dispatch():
    client = FullClient()
    adapter = RelayComputer(APP, expected_title=TITLE, client=client, full_tools=True)
    page = adapter.observe()
    action = next(a for a in page["actions"] if a["kind"] == "select_text")
    action = {**action, "parameters": {"selection": "cursor_after"}}
    with pytest.raises(ValueError, match="uniquely"):
        adapter.act(action, page, text="并不存在的文字")
    assert all(name == "get_app_state" for name, _ in client.calls)
    adapter.act(action, page, text="第一行")
    assert client.calls[-1] == ("select_text", {"app": APP, "element_index": "2",
                                              "text": "第一行", "selection": "cursor_after"})


def test_missing_geometry_does_not_offer_a_fabricated_drag_target():
    page = read_relay_page(result(), APP, TITLE, FullClient.tools, full_tools=True)
    assert not any(action["kind"] == "drag" for action in page["actions"])
    assert {a["tool"] for a in page["actions"]} >= {
        "click", "set_value", "select_text", "scroll", "press_key", "type_text", "perform_secondary_action"}


def test_parameters_are_retained_in_pending_intent_check(monkeypatch):
    captured = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline")

    def post(url, key, body):
        captured.append(copy.deepcopy(body))
        return {"answers": {"pending": answer("DISCARD", ["EXECUTE", "DISCARD"])}}

    monkeypatch.setattr(mixed, "post_json", post)
    action = {"kind": "key", "app_name": "App", "channel": "CU", "label": "Window",
              "parameters": {"key": "PASTE"}}
    valid, _ = mixed.pending_valid(action, None, {"surfaces": {}}, {}, [{"text": "不要粘贴"}], [])
    assert not valid and captured[0]["state"]["pending_action"]["parameters"] == {"key": "PASTE"}


def test_model_transport_retry_is_bounded_traced_and_redacted(monkeypatch, tmp_path):
    attempts = []

    def transport(request):
        attempts.append(request)
        if len(attempts) < 3:
            raise httpx.RemoteProtocolError("connection closed")
        return httpx.Response(200, json={"answers": {}, "usage": {}})

    trace = Trace(tmp_path / "trace")
    token = CURRENT_TRACE.set(trace)
    monkeypatch.setattr(model.time, "sleep", lambda _: None)
    try:
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            monkeypatch.setattr(model, "CLIENT", client)
            assert model.post_json("https://api.example/systemone", "private-key", {"questions": {}})["answers"] == {}
        records = [json.loads(line) for line in trace.path.read_text().splitlines()]
        assert len(attempts) == 3
        errors = [r for r in records if r["type"] == "model.http.error"]
        assert [r["error_type"] for r in errors] == ["RemoteProtocolError", "RemoteProtocolError"]
        assert "private-key" not in trace.path.read_text()
    finally:
        CURRENT_TRACE.reset(token)


def test_network_failure_names_provider_without_claiming_prior_actions_did_not_happen(monkeypatch):
    def transport(request):
        raise httpx.ConnectTimeout('private transport detail', request=request)

    monkeypatch.setattr(model.time, 'sleep', lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        monkeypatch.setattr(model, 'CLIENT', client)
        with pytest.raises(RuntimeError, match='Jev 连接超时') as error:
            model.post_json('https://api.typesafe.ai/v1/systemone', 'private-key', {})
    message = str(error.value)
    assert '已尝试 3 次' in message and '此前已执行的操作不会撤销' in message
    assert 'no action executed' not in message and 'private' not in message
