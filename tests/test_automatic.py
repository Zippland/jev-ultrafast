"""Discovery identities become choices; no caller chooses an app or a tab."""

import pytest

from jev_ultrafast import automatic
from jev_ultrafast.mixed import build_request, observation_context
from jev_ultrafast.tracing import Trace
from jev_ultrafast.voice_demo import running_apps


@pytest.fixture(autouse=True)
def no_real_desktop_reads(monkeypatch):
    monkeypatch.setattr(automatic, "foreground_app", lambda: None)


def test_list_apps_wire_format():
    assert running_apps({"content": [{"type": "text", "text":
        "计算器 — /System/Applications/Calculator.app/ — com.apple.calculator [running, last-used=2026]\n"
        "Other — /Applications/Other.app/ — other.app [installed]"}]}) == [
            {"id": "com.apple.calculator", "name": "计算器",
             "path": "/System/Applications/Calculator.app/", "frontmost": False}]


def test_activate_app_uses_observed_identity_and_checks_actual_focus(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(automatic.subprocess, "run", lambda command, **kw: calls.append(command))
    monkeypatch.setattr(automatic, "foreground_app", lambda: "other.app")
    desktop = automatic.AutomaticDesktop(lambda: {"apps": [{"id": "native.app", "name": "Native"}],
                                                   "tabs": []}, [], Trace(tmp_path / "trace"))
    page = desktop.observe()
    action = next(a for a in page["actions"] if a["kind"] == "activate_app")
    returned = desktop.act(action, page, before_dispatch=lambda: calls.append("dispatch"))
    assert calls == ["dispatch", ["/usr/bin/open", "-b", "native.app"]]
    assert returned["foreground_after_action"]["app"] == "other.app"
    assert returned["foreground_after_action"]["requested_app"] == "native.app"
    assert desktop.active == action["app_key"]
    class Native:
        def observe(self, screenshot=False):
            return {"title": "Native window", "text": "Observed content", "actions": []}

        def close(self):
            pass

    desktop.adapters[desktop.active] = Native()
    assert desktop.observe()["surfaces"][action["surface"]]["text"] == "Observed content"
    with pytest.raises(ValueError):
        desktop.act({**action, "app_key": "invented"}, page)
    assert len(calls) == 2
    desktop.close()


def test_disconnected_browser_is_explicit_model_context_and_recovers(tmp_path):
    catalog = {"apps": [{"id": "native.app", "name": "Native"}], "tabs": [],
               "browser_connected": False, "errors": ["Browser Use：connection unavailable"]}
    desktop = automatic.AutomaticDesktop(lambda: catalog, [], Trace(tmp_path / "trace"))
    observation = desktop.observe()
    request, _ = build_request(observation, desktop.bindings, [], [])
    assert request["state"]["tool_availability"]["browser_connected"] is False
    assert request["state"]["tool_availability"]["discovery_errors"] == catalog["errors"]
    assert "NEW_TAB" not in request["state"]["available_targets"]
    catalog.update(browser_connected=True, errors=[])
    request, _ = build_request(desktop.observe(), desktop.bindings, [], [])
    assert request["state"]["tool_availability"]["browser_connected"] is True
    assert request["state"]["tool_availability"]["discovery_errors"] == []
    assert "NEW_TAB" in request["state"]["available_targets"]
    desktop.close()


def test_model_discovers_app_and_exact_tab_then_only_observed_controls(monkeypatch, tmp_path):
    def inventory():
        return {"apps": [{"id": "native.app", "name": "Native"}],
                "tabs": [{"id": "observed-tab", "title": "Form", "url": "http://fixture/"}]}
    opened = []

    class Browser:
        def __init__(self, target):
            opened.append(target)

        def observe(self, screenshot=False):
            return {"title": "Form", "text": "Actual field", "actions": [
                {"id": "1", "kind": "fill", "label": "Actual field", "node": 1}]}

        def close(self):
            pass

    monkeypatch.setattr(automatic, "AttachedBrowser", Browser)
    desktop = automatic.AutomaticDesktop(inventory, ["unused"], Trace(tmp_path / "trace"))
    first = desktop.observe()
    assert first["surfaces"] == {}
    assert {a["kind"] for a in first["actions"]} == {"inspect", "activate_tab", "activate_app"}
    body, _ = build_request(first, desktop.bindings, [{"text": "fill the form"}], [])
    assert set(body["questions"]) == {"operation", "switch_app_target", "show_tab_target", "show_app_target", "outcome"}
    assert set(body["questions"]["operation"]["criteria"]) == {"SWITCH_APP", "SHOW_TAB", "SHOW_APP", "LISTEN"}
    assert len(body["questions"]["switch_app_target"]["criteria"]) == 2  # native app, exact Chrome tab
    selected = next(a for a in first["actions"] if a["channel"] == "Browser Use")
    desktop.act(selected, first)
    assert opened == ["observed-tab"]
    second = desktop.observe()
    assert any(a["kind"] == "fill" and a["label"] == "Actual field" for a in second["actions"])
    assert observation_context(second, desktop.bindings)[0]["channel"] == "Browser Use"
    assert second["current_context"]["tab"] == "observed-tab"
    assert all(a["kind"] in {"inspect", "activate_app"} or a["app_key"] == desktop.active for a in second["actions"])
    body, _ = build_request(second, desktop.bindings, [{"text": "fill the form"}], [])
    assert set(body["questions"]) == {"operation", "switch_app_target", "type_text_target",
                                      "wait_target", "show_app_target", "outcome"}
    assert body["state"]["current_context"]["channel"] == "Browser Use"
    wait = next(a for a in second["actions"] if a["kind"] == "wait")
    dispatched = []
    desktop.act(wait, second, before_dispatch=lambda: dispatched.append(True))
    assert dispatched == [True]  # No invented native tool or element index is dispatched.
    desktop.close()


def test_connected_browser_can_create_first_tab_without_observing_a_page(monkeypatch, tmp_path):
    catalog = {"apps": [], "tabs": [], "browser_connected": True}
    desktop = automatic.AutomaticDesktop(lambda: catalog, [], Trace(tmp_path / "trace"))
    calls = []

    class Browser:
        def __init__(self, target):
            self.target = target

        def observe(self, screenshot=False):
            return {"title": "", "text": "", "actions": []}

        def close(self):
            pass

    def create(method, **params):
        calls.append((method, params))
        return {"targetId": "new-observed-tab"}

    monkeypatch.setattr(automatic, "AttachedBrowser", Browser)
    monkeypatch.setattr(automatic, "traced_cdp", create)
    observation = desktop.observe()
    action, = observation["actions"]
    assert action["kind"] == "new_tab"
    body, _ = build_request(observation, desktop.bindings, [{"text": "open a browser"}], [])
    assert "NEW_TAB" in body["state"]["available_targets"]
    dispatch = []
    desktop.act(action, observation, text="about:blank", before_dispatch=lambda: dispatch.append(True))
    assert dispatch == [True]
    assert calls == [("Target.createTarget", {"url": "about:blank", "background": True})]
    catalog["tabs"] = [{"id": "new-observed-tab", "title": "", "url": "about:blank"}]
    assert desktop.observe()["current_context"]["tab"] == "new-observed-tab"
    catalog["browser_connected"] = False
    assert not any(a.get("browser_new_tab") for a in desktop.observe()["actions"])
    desktop.close()


def test_foreground_observation_from_before_show_tab_is_not_reused(monkeypatch, tmp_path):
    catalog = {"apps": [{"id": "other.app", "name": "Other", "frontmost": True}],
               "tabs": [{"id": "tab", "title": "Page", "url": "https://example.test"}],
               "apps_age_ms": 5000}

    class Browser:
        def __init__(self, _target):
            self.target = _target

        def observe(self, screenshot=False):
            return {"title": "Page", "text": "", "actions": [
                {"id": "show", "kind": "activate_tab", "label": "Show tab"}]}

        def act(self, *args, **kwargs):
            pass

        def close(self):
            pass

    now = [10.0]
    monkeypatch.setattr(automatic.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(automatic, "AttachedBrowser", Browser)
    desktop = automatic.AutomaticDesktop(lambda: catalog, [], Trace(tmp_path / "trace"))
    first = desktop.observe()
    desktop.act(next(a for a in first["actions"] if a["channel"] == "Browser Use"), first)
    page = desktop.observe()
    assert page["desktop_focus"]["frontmost_app"]["id"] == "other.app"
    monkeypatch.setattr(automatic, "foreground_app", lambda: "com.google.Chrome")
    returned = desktop.act(next(a for a in page["actions"] if a["kind"] == "activate_tab"), page)
    assert returned["foreground_after_action"]["app"] == "com.google.Chrome"
    fresh = desktop.observe()["desktop_focus"]
    assert fresh["frontmost_app"]["id"] == "com.google.Chrome" and fresh["source"] == "NSWorkspace"
    assert fresh["age_ms"] == 0
    monkeypatch.setattr(automatic, "foreground_app", lambda: None)
    assert desktop.observe()["desktop_focus"]["frontmost_app"] is None
    now[0] = 11.0
    catalog["apps_age_ms"] = 0
    assert desktop.observe()["desktop_focus"]["frontmost_app"]["id"] == "other.app"
    desktop.close()


def test_show_discovered_tab_without_first_reading_or_mutating_its_page(monkeypatch, tmp_path):
    from jev_ultrafast.browser import StalePage

    catalog = {'apps': [], 'tabs': [{'id': 'observed', 'title': 'Known page', 'url': 'https://example.test/'}]}
    calls = []
    url = [catalog['tabs'][0]['url']]

    def call(method, **params):
        calls.append(method)
        assert params == {'targetId': 'observed'}
        return {'targetInfo': {'targetId': 'observed', 'url': url[0]}} if method == 'Target.getTargetInfo' else {}

    class Browser:
        def __init__(self, target):
            self.target = target

        def close(self):
            pass

    monkeypatch.setattr(automatic, 'traced_cdp', call)
    monkeypatch.setattr(automatic, 'AttachedBrowser', Browser)
    monkeypatch.setattr(automatic, 'foreground_app', lambda: 'com.google.Chrome')
    desktop = automatic.AutomaticDesktop(lambda: catalog, [], Trace(tmp_path / 'trace'))
    observation = desktop.observe()
    assert not observation['surfaces']
    action = next(a for a in observation['actions'] if a['kind'] == 'activate_tab')
    url[0] = 'https://example.test/changed'
    with pytest.raises(StalePage):
        desktop.act(action, observation)
    assert calls == ['Target.getTargetInfo']
    url[0] = action['value']
    event = desktop.act(action, observation, before_dispatch=lambda: calls.append('dispatch'))
    assert calls[-3:] == ['Target.getTargetInfo', 'dispatch', 'Target.activateTarget']
    assert event['foreground_after_action']['app'] == 'com.google.Chrome'
    assert desktop.active == action['app_key']
    desktop.close()


def test_native_workbench_cannot_be_overwritten_but_browser_tools_remain(tmp_path):
    catalog = {"apps": [{"id": "chrome", "name": "Chrome"}], "tabs": [
        {"id": "task", "title": "Task", "url": "https://example.org/"}],
        "browser_connected": True}
    desktop = automatic.AutomaticDesktop(lambda: catalog, [], Trace(tmp_path / "trace"))
    key = desktop.key("app:chrome")

    class Workbench:
        def observe(self, screenshot=False):
            return {"agent_control_surface": True, "actions": [
                {"id": "address", "kind": "fill", "label": "Address"},
                {"id": "raise", "kind": "secondary", "label": "Raise"}]}

    desktop.active = key
    desktop.adapters[key] = Workbench()
    observation = desktop.observe()
    assert {a["kind"] for a in observation["actions"]} == {"inspect", "activate_tab", "new_tab", "activate_app"}
    assert all(a["app_key"] != key or a["kind"] == "activate_app" for a in observation["actions"])
    assert observation["surfaces"][f"auto:{key}"]["agent_control_surface"]
    assert observation["tool_availability"]["protected_surfaces"] == [f"auto:{key}"]


def test_activate_app_uses_observed_path(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(automatic.subprocess, "run", lambda command, **kw: calls.append(command))
    catalog = {"apps": [{"id": "native.app", "name": "Native", "path": "/Applications/Native.app/"}],
               "tabs": []}
    desktop = automatic.AutomaticDesktop(lambda: catalog, [], Trace(tmp_path / "trace"))
    page = desktop.observe()
    desktop.act(next(a for a in page["actions"] if a["kind"] == "activate_app"), page)
    assert calls == [["/usr/bin/open", "-a", "/Applications/Native.app/"]]
    catalog["apps"].append(dict(catalog["apps"][0], path="/Volumes/Native.app/"))
    with pytest.raises(ValueError, match="Multiple running apps"):
        desktop.observe()
