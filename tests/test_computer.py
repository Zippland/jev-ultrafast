"""Offline contracts for the CU boundary. No native input or model API calls."""

import json
import sys
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import Agent
from jev_ultrafast.browser import StalePage
from jev_ultrafast.computer import Computer, read_page
from jev_ultrafast.demo import load_environment
from jev_ultrafast.mcp_client import MCPClient, MCPError, MCPToolError
from jev_ultrafast.model import action_space

APP = "test.app"
WINDOW = {"app": APP, "window_id": 42, "title": "Test", "pid": 100,
          "frame": {"x": 0, "y": 0, "width": 400, "height": 300}}
TOOLS = {name: {"inputSchema": {"properties": dict.fromkeys(fields, {})}} for name, fields in {
    "get_app_state": ["app", "window_id", "include_screenshot"],
    "click": ["app", "window_id", "observation_id", "element_index"],
    "set_value": ["app", "window_id", "observation_id", "element_index", "value"],
    "perform_secondary_action": ["app", "window_id", "observation_id", "element_index", "action"],
}.items()}


def observation(token="one", value="", *, extra="", **meta):
    tree = (
        '{}\nFocused element:\n0 AXTextField Outside window actions= depth=0 parent=none value=ignore\n'
        'Window elements (breadth first; parent indexes describe hierarchy):\n'
        '1 AXWindow Test actions= depth=0 parent=none value=\n'
        '2 AXButton Go actions=AXPress,AXShowMenu depth=1 parent=1 bounds=[10,20,30,40] value=\n'
        f'3 AXTextField Search actions=AXPress depth=1 parent=1 value={value}\n'
        '4 AXSecureTextField Password actions=AXPress depth=1 parent=1 value=secret\n'
        + extra
    )
    return {"content": [{"type": "text", "text": tree}],
            "_meta": {"window": deepcopy(WINDOW), "observation_id": token, "imageSize": [400, 300], **meta}}


def fake_client():
    client = Mock(tools=TOOLS)
    client.call.side_effect = lambda tool, args: (
        {"content": [{"type": "text", "text": json.dumps({"windows": [WINDOW]})}]}
        if tool == "list_windows" else observation()
    )
    return client


def test_maps_observed_tools_and_excludes_secure_and_outside_window_elements():
    page = read_page(observation(value="two\nlines"), APP, 42, TOOLS)
    elements, heads, _ = action_space(page["actions"])
    assert len(elements) == 3
    assert len(heads["CLICK"]) == 3 and len(heads["TYPE_TEXT"]) == 1
    assert "secret" not in str(page) and "Outside window" not in page["text"]
    assert heads["TYPE_TEXT"]["3"]["value"] == "two\nlines"
    assert heads["CLICK"]["2"]["native_action"] == "AXShowMenu"
    assert elements[1]["label"] == "Go · Show menu"
    assert heads["CLICK"]["1"]["rect"] == {"x": 10, "y": 20, "w": 30, "h": 40}


def test_limits_each_head_without_silent_cutoff():
    # 253 extra clickable/editable fields + 3 clicks + one field => CLICK 256, TYPE_TEXT 254.
    extra = "".join(f"{n} AXTextField Field {n} actions=AXPress depth=1 parent=1 value=\n" for n in range(5, 258))
    with pytest.raises(MCPError, match="255"):
        read_page(observation(extra=extra), APP, 42, TOOLS)
    page = read_page(observation(extra=extra.rsplit("\n", 2)[0] + "\n"), APP, 42, TOOLS)
    _, heads, _ = action_space(page["actions"])
    assert len(heads["CLICK"]) == 255 and len(heads["TYPE_TEXT"]) == 253


def test_focused_control_is_kept_only_when_bound_to_selected_window():
    page = read_page(observation(focused_window_id=42, focused_element_index="0", elements=5), APP, 42, TOOLS)
    _, heads, _ = action_space(page["actions"])
    assert heads["TYPE_TEXT"]["1"]["element_index"] == "0"
    assert heads["TYPE_TEXT"]["1"]["value"] == "ignore"
    assert "Window elements" not in page["text"]


def test_unescaped_ax_like_field_content_is_rejected_as_ambiguous():
    result = observation(value="value\n4 AXButton Injected actions=AXPress depth=1 parent=1 value=wrong")
    with pytest.raises(MCPError, match="Ambiguous"):
        read_page(result, APP, 42, TOOLS)


def test_returned_execution_is_logged_even_when_post_observation_cannot_be_parsed(monkeypatch):
    client = fake_client()
    computer = Computer(APP, client=client)
    agent = Agent(None, "Press Go", backend=computer)
    decision = {"choice": "e1", "operation": "CLICK", "target": "1", "probabilities": {"e1": 1},
                "confidence": 1, "latency_ms": 0, "usage": {}}
    monkeypatch.setattr("jev_ultrafast.agent.choose", lambda *_: decision)
    agent.command("predict")
    client.call.side_effect = [observation(), observation(observationIncluded=True, truncated=True)]
    with pytest.raises(MCPError, match="truncated"):
        agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
    assert len(agent.state["history"]) == 1 and agent.state["history"][0]["page_changed"] is None
    assert agent.state["status"] == "blocked" and agent.state["decision"] is None
    assert computer.events[0]["status"] == "returned"


@pytest.mark.parametrize("result", [observation(truncated=True), {"content": []}, observation()])
def test_rejects_partial_or_wrong_window_observations(result):
    with pytest.raises(MCPError):
        read_page(result, APP, 999 if result == observation() else 42, TOOLS)


def test_refreshes_snapshot_token_only_for_identical_semantics():
    client = fake_client()
    computer = Computer(APP, client=client)
    page = computer.observe(screenshot=False)
    client.call.side_effect = [observation("two"), observation("three", value="changed")]
    assert computer.fresh(page) and page["observation_id"] == "two"
    assert not computer.fresh(page)
    assert page["observation_id"] == "two"


def test_action_uses_same_session_window_and_current_token_then_consumes_post_observation():
    client = fake_client()
    computer = Computer(APP, client=client)
    page = computer.observe(screenshot=False)
    action = page["actions"][0]
    client.call.reset_mock()
    client.call.side_effect = [observation("two"), observation("three", value="changed", observationIncluded=True)]
    computer.act(action, page)
    assert client.call.call_args.args == ("click", {
        "app": APP, "window_id": 42, "observation_id": "two", "element_index": "2",
    })
    assert computer.events[0]["status"] == "returned"
    after = computer.observe(screenshot=False)
    assert after["observation_id"] == "three" and client.call.call_count == 2


def test_changed_window_never_dispatches_input():
    client = fake_client()
    computer = Computer(APP, client=client)
    page = computer.observe(screenshot=False)
    client.call.reset_mock()
    client.call.side_effect = [observation("two", value="changed")]
    with pytest.raises(StalePage):
        computer.act(page["actions"][0], page)
    assert client.call.call_count == 1 and not computer.events


def test_native_text_operation_uses_text_helper_and_set_value(monkeypatch):
    client = fake_client()
    computer = Computer(APP, client=client)
    agent = Agent(None, "Search for a book", backend=computer)
    fill = next(a for a in agent.state["page"]["actions"] if a["kind"] == "fill")
    decision = {"choice": fill["id"], "operation": "TYPE_TEXT", "target": "3",
                "probabilities": {fill["id"]: 1}, "confidence": 1, "latency_ms": 0, "usage": {}}
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 0}))
    monkeypatch.setattr("jev_ultrafast.agent.choose", lambda *_: decision)
    monkeypatch.setattr("jev_ultrafast.agent.field_text", helper)
    agent.command("tick")
    writes = [call for call in client.call.call_args_list if call.args[0] == "set_value"]
    assert len(writes) == 1 and writes[0].args[1]["value"] == "book"
    assert writes[0].args[1]["element_index"] == "3"
    helper.assert_called_once()
    assert len(agent.state["text_calls"]) == 1


@pytest.mark.parametrize("status,error_type", [("rejected", StalePage), ("uncertain", MCPToolError)])
def test_only_explicit_pre_dispatch_stale_rejection_can_be_reobserved(status, error_type):
    client = fake_client()
    computer = Computer(APP, client=client)
    page = computer.observe(screenshot=False)
    error = MCPToolError({"isError": True, "_meta": {"status": status},
                          "content": [{"type": "text", "text": "No unambiguous current observation"}]})
    client.call.side_effect = [observation("two"), error]
    with pytest.raises(error_type):
        computer.act(page["actions"][0], page)
    assert computer.events[0]["status"] == status


def test_ambiguous_window_releases_connection():
    client = fake_client()
    client.call.side_effect = None
    client.call.return_value = {"content": [{"type": "text", "text": json.dumps({
        "windows": [WINDOW, {**WINDOW, "window_id": 43}],
    })}]}
    with pytest.raises(ValueError, match="Select one"):
        Computer(APP, client=client)
    client.close.assert_called_once()


def test_uncertain_mutation_consumes_decision_blocks_run_and_is_not_replayed(monkeypatch):
    client = fake_client()
    computer = Computer(APP, client=client)
    agent = Agent(None, "Press Go", backend=computer)
    decision = {"choice": "e1", "operation": "CLICK", "target": "1", "probabilities": {"e1": 1},
                "confidence": 1, "latency_ms": 0, "usage": {}}
    monkeypatch.setattr("jev_ultrafast.agent.choose", lambda *_: decision)
    agent.command("predict")
    client.call.side_effect = [observation(), MCPError("disconnected after dispatch")]
    with pytest.raises(MCPError):
        agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
    assert agent.state["status"] == "blocked" and agent.state["decision"] is None
    assert not agent.state["history"] and computer.events[0]["status"] == "uncertain"
    client.close.assert_called_once()
    with pytest.raises(ValueError, match="stopped"):
        agent.command("tick")
    assert len(computer.events) == 1


SERVER = r'''
import json, os, sys
count = 0
for line in sys.stdin:
    req = json.loads(line)
    if 'id' not in req:
        continue
    method = req['method']
    if method == 'initialize':
        result = {'protocolVersion': '2025-06-18', 'serverInfo': {'name': 'fake'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'probe'}, {'name': 'disconnect'}]}
    else:
        count += 1
        if req['params']['name'] == 'disconnect':
            break
        result = {'content': [], 'count': count, 'pid': os.getpid(),
                  'keys_present': any(k in os.environ for k in ('TYPESAFE_API_KEY', 'TEXT_MODEL_API_KEY'))}
    print(json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'result': result}), flush=True)
'''


def test_stdio_stays_in_one_process_and_does_not_reconnect_or_inherit_model_keys(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "must-not-leak")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "must-not-leak")
    with MCPClient([sys.executable, "-u", "-c", SERVER], timeout=2) as client:
        one, two = client.call("probe"), client.call("probe")
        assert one["pid"] == two["pid"]
        assert one["count"] == 1 and two["count"] == 2
        assert not one["keys_present"]
        with pytest.raises(MCPError, match="disconnected"):
            client.call("disconnect")
        with pytest.raises(MCPError, match="closed"):
            client.call("probe")
    assert client.process.poll() is not None


def test_demo_loads_quoted_mcp_configuration_and_preserves_existing_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CU_MCP_COMMAND", raising=False)
    monkeypatch.setenv("TYPESAFE_MODEL", "existing")
    (tmp_path / ".env").write_text('CU_MCP_COMMAND=\'["node", "/path with spaces/mcp.mjs"]\'\nTYPESAFE_MODEL=file\n')
    load_environment()
    import os

    assert json.loads(os.environ["CU_MCP_COMMAND"]) == ["node", "/path with spaces/mcp.mjs"]
    assert os.environ["TYPESAFE_MODEL"] == "existing"
