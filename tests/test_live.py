"""Offline concurrency tests: no native applications, ASR weights, or paid requests."""

import base64
import json
import threading
import time

import pytest

from jev_ultrafast.browser import DispatchCancelled, browser_operation
from jev_ultrafast.live import LiveSession
from jev_ultrafast.model import MissingTextArgument
from jev_ultrafast.tracing import Trace
from jev_ultrafast.voice.transport import VoiceTransport

ACTION = {"id": "test/1", "kind": "click", "app_name": "App", "label": "control", "channel": "CU"}


class Desktop:
    bindings = {"test": {"name": "App"}}

    def __init__(self):
        self.actions = []
        self.before = lambda: None
        self.fail = False
        self.closed = False

    def observe(self):
        return {"surfaces": {"cu:test": {"title": "App", "text": str(len(self.actions))}}, "actions": [ACTION]}

    def act(self, action, observation, text=None, before_dispatch=None):
        self.before()
        before_dispatch()
        self.actions.append((action, text))
        if self.fail:
            raise RuntimeError("transport disconnected after dispatch")
        return {"app": "App", "channel": "CU", "label": "control", "kind": action["kind"], "text": text}

    def close(self):
        self.closed = True


def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "actor did not reach expected state"
        time.sleep(0.005)


@pytest.fixture
def factory(tmp_path):
    sessions = []

    def create(choose, **kwargs):
        desktop = Desktop()
        session = LiveSession(desktop, Trace(tmp_path / str(len(sessions))), choose=choose, **kwargs)
        sessions.append(session)
        return session, desktop

    yield create
    for session in sessions:
        session.close()
        session.worker.join(timeout=3)
        assert not session.worker.is_alive()


def test_partial_replaces_same_segment_and_listen_does_not_spin(factory):
    seen = []

    def choose(o, b, speech, h):
        seen.append(speech)
        return None, {}

    session, _ = factory(choose)
    session.input("s", "去北")
    until(lambda: len(seen) == 1)
    session.input("s", "去北京", final=True)
    until(lambda: len(seen) == 2)
    assert seen[-1] == [{"id": "s", "text": "去北京", "source": "text", "version": 2, "final": True}]
    assert len(session.snapshot()["segments"]) == 1
    time.sleep(0.05)
    assert len(seen) == 2
    with pytest.raises(ValueError):
        session.input("s", "改写已经定稿的段")


@pytest.mark.parametrize("during_generation", [False, True])
def test_missing_text_waits_without_pausing_or_losing_new_input(factory, during_generation):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def generate(a, o, b, speech, h):
        calls.append(speech[-1]["text"])
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
            raise MissingTextArgument("No argument")
        return "补充的内容", {}

    session, desktop = factory(lambda o, b, s, h: (None if h else {**ACTION, "kind": "fill"}, {}),
                               generate=generate)
    session.input("first", "写入", final=True)
    assert entered.wait(2)
    if during_generation:
        session.input("second", "补充的内容", final=True)
    release.set()
    if not during_generation:
        until(lambda: session.snapshot()["phase"] == "waiting_for_text")
        assert session.snapshot()["enabled"] and not session.snapshot()["error"]
        assert not desktop.actions
        time.sleep(0.03)
        assert len(calls) == 1
        session.input("second", "补充的内容", final=True)
    until(lambda: session.snapshot()["phase"] == "listening")
    assert calls == ["写入", "补充的内容"]
    assert len(desktop.actions) == 1
    assert desktop.actions[0][1] == "补充的内容"


def test_malformed_text_still_pauses_execution(factory):
    def generate(*args):
        raise ValueError("Malformed model response")

    session, desktop = factory(lambda *args: ({**ACTION, "kind": "fill"}, {}), generate=generate)
    session.input("first", "写入")
    until(lambda: session.snapshot()["error"] is not None)
    assert not session.snapshot()["enabled"]
    assert not desktop.actions


@pytest.mark.parametrize("changed", [True, False])
def test_next_choice_receives_observed_effect_not_just_tool_return(factory, changed):
    seen = []

    def choose(o, b, speech, history):
        if history:
            seen.append(history[-1].copy())
            return None, {}
        return ACTION, {}

    session, desktop = factory(choose)
    if not changed:
        fixed = desktop.observe()
        desktop.observe = lambda: fixed
    session.input("s", "perform once", final=True)
    until(lambda: bool(seen))
    assert seen[-1]["observed_change"] is changed
    events = [json.loads(line) for line in session.trace.path.read_text().splitlines()]
    assert [e['type'] for e in events if e['type'] in {'action.returned', 'action.observed'}] == [
        'action.returned', 'action.observed']


def test_pause_during_choice_blocks_late_result(factory):
    entered, release = threading.Event(), threading.Event()

    def choose(*args):
        entered.set()
        assert release.wait(2)
        return ACTION, {}

    session, desktop = factory(choose)
    session.input("s", "操作")
    assert entered.wait(2)
    session.pause()
    release.set()
    until(lambda: session.snapshot()["phase"] == "paused")
    time.sleep(0.04)
    assert desktop.actions == []


def test_new_speech_during_native_freshness_is_semantically_revalidated(factory):
    validations = []

    def validate(a, text, o, b, speech, h):
        validations.append(speech[-1]["text"])
        return True, {}

    session, desktop = factory(lambda o, b, s, h: (None if h else ACTION, {}), validate=validate)
    desktop.before = lambda: session.input("addition", "背景补充")
    session.input("s", "操作")
    until(lambda: len(desktop.actions) == 1)
    assert validations == ["背景补充"]
    assert session.snapshot()["history"][0]["version"] == 2


def test_correction_during_native_freshness_discards_old_action(factory):
    choices = []

    def choose(o, b, speech, h):
        choices.append(speech)
        return (ACTION if len(choices) == 1 else None), {}

    session, desktop = factory(choose, validate=lambda *args: (False, {}))
    desktop.before = lambda: session.input("correction", "不是这个")
    session.input("s", "操作")
    until(lambda: len(choices) >= 2)
    assert not desktop.actions


def test_input_during_text_generation_revalidates_exact_generated_value(factory):
    started, release = threading.Event(), threading.Event()
    generated = {**ACTION, "kind": "fill"}
    gates = []

    def generate(*args):
        started.set()
        assert release.wait(2)
        return "上海", {}

    def choose(o, b, speech, h):
        return (generated if len(speech) == 1 else None), {}

    def validate(action, value, o, b, speech, h):
        gates.append((value, speech[-1]["text"]))
        return False, {}

    session, desktop = factory(choose, generate=generate, validate=validate)
    session.input("first", "上海")
    assert started.wait(2)
    session.input("second", "改成杭州")
    release.set()
    until(lambda: bool(gates))
    assert gates == [("上海", "改成杭州")]
    assert not desktop.actions


def test_uncertain_mutation_is_not_replayed_or_resumed(factory):
    session, desktop = factory(lambda *args: (ACTION, {}))
    desktop.fail = True
    session.input("s", "操作")
    until(lambda: bool(session.snapshot()["error"]))
    assert len(desktop.actions) == 1
    assert session.snapshot()["uncertain"]
    with pytest.raises(ValueError, match="结果不确定"):
        session.resume()
    records = [json.loads(line) for line in session.trace.path.read_text().splitlines()]
    types = [e["type"] for e in records]
    assert types.index("action.dispatch") < types.index("action.uncertain")


@pytest.mark.parametrize('failure_stage', ['freshness', 'validation'])
def test_failure_before_dispatch_does_not_invent_executed_history(factory, failure_stage):
    def fail(*args):
        raise RuntimeError('Read-only preparation failed')

    def choose(observation, bindings, speech, history):
        if failure_stage == 'validation' and len(speech) == 1:
            session.input('revision', '补充要求')
        return (None, {}) if history else (ACTION, {})

    session, desktop = factory(choose, validate=fail)
    if failure_stage == 'freshness':
        desktop.before = fail
    session.input('s', '操作')
    until(lambda: bool(session.snapshot()['error']))
    assert not desktop.actions
    assert not session.snapshot()['history']
    assert not session.snapshot()['uncertain']
    assert not session.snapshot()['enabled']
    records = [json.loads(line) for line in session.trace.path.read_text().splitlines()]
    types = [e['type'] for e in records]
    assert 'action.not_dispatched' in types
    assert 'action.dispatch' not in types and 'action.uncertain' not in types
    # Recovery is explicit, never an automatic retry. It must choose again.
    desktop.before = lambda: None
    session.resume()
    until(lambda: session.snapshot()['phase'] == 'listening')
    assert len(desktop.actions) == 1


def test_disconnected_ui_prevents_dispatch(factory):
    session, desktop = factory(lambda *args: (ACTION, {}), heartbeat_timeout=0.01)
    time.sleep(0.02)
    session.input("s", "操作")
    until(lambda: not session.enabled)
    assert not desktop.actions


def test_browser_guard_after_coordinate_read_prevents_first_mutation(monkeypatch):
    calls = []

    def cdp(method, **kwargs):
        calls.append(method)
        return {"result": {"value": {"x": 10, "y": 10}}}

    monkeypatch.setattr("jev_ultrafast.browser.cdp", cdp)

    def guard():
        raise DispatchCancelled("paused while reading coordinates")

    with pytest.raises(DispatchCancelled):
        browser_operation({"operation": "act", "session": "s", "action": {"kind": "fill", "node": 1},
                           "text": "late value", "before_dispatch": guard})
    assert calls == ["Runtime.evaluate"]


def test_asr_requires_contiguous_revisions(factory):
    session, _ = factory(lambda *args: (None, {}))
    voice = VoiceTransport(session)
    voice.recording_id = "recording"
    with pytest.raises(ValueError, match="版本不连续"):
        voice._event({"type": "partial", "recording_id": "recording", "base_revision": 2,
                      "revision": 3, "segment_id": "segment", "text": "跳过了版本"})
    assert not session.segments


def test_read_only_routing_is_not_starved_by_continuous_speech(factory):
    inspect = {**ACTION, "kind": "inspect"}
    started, release = threading.Event(), threading.Event()

    def choose(o, b, speech, h):
        if h:
            return None, {}
        started.set()
        assert release.wait(2)
        return inspect, {}

    def no_gate(*args):
        raise AssertionError("read-only observation must not wait for speech to settle")

    session, desktop = factory(choose, validate=no_gate)
    session.input("s", "在文本编辑")
    assert started.wait(2)
    session.input("s", "在文本编辑里写字")
    release.set()
    until(lambda: len(desktop.actions) == 1)
    assert desktop.actions[0][0]["kind"] == "inspect"


def test_empty_new_asr_segment_does_not_wake_model(factory):
    calls = []
    session, _ = factory(lambda *args: (calls.append(1), {}))
    assert session.input("silence", "  ", source="voice") == 0
    assert not session.segments and not calls


def test_text_helper_sends_readable_chinese(monkeypatch):
    from jev_ultrafast import model

    requests = []

    def post(url, key, body):
        requests.append(body)
        return {"choices": [{"message": {"content": '{"text":"语音联动成功"}'}}]}

    monkeypatch.setenv("TEXT_MODEL_API_KEY", "offline")
    monkeypatch.setattr(model, "post_json", post)
    assert model.field_text({"goal": "正文改成“语音联动成功”"})[0] == "语音联动成功"
    assert "语音联动成功" in requests[0]["messages"][1]["content"]


def test_audio_keeps_streaming_past_two_minutes_with_bounded_chunks(factory, monkeypatch):
    session, _ = factory(lambda *args: (None, {}))
    voice = VoiceTransport(session)
    voice.recording_id, voice.status = "continuous", "recording"
    voice.bytes, voice.sequence = 3_840_000, 600
    sent = []
    monkeypatch.setattr(voice, "_send", sent.append)
    voice.audio("continuous", 600, base64.b64encode(bytes(6400)).decode())
    assert voice.bytes == 3_846_400 and voice.sequence == 601
    assert sent[0]["type"] == "audio"


def test_wait_observes_progress_and_continues_without_more_speech(factory):
    wait = {**ACTION, "kind": "wait"}

    def choose(o, b, speech, history):
        return (wait if not history else ACTION if len(history) == 1 else None), {}

    session, desktop = factory(choose)
    session.input("s", "等内容出现后执行", final=True)
    until(lambda: session.phase == "listening" and len(desktop.actions) == 2)
    assert [action[0]["kind"] for action in desktop.actions] == ["wait", "click"]
    assert session.version == 1


def test_long_task_continues_past_eighty_decisions_without_new_speech(factory):
    def choose(o, b, speech, history):
        return (ACTION if len(history) < 120 else None), {}

    session, desktop = factory(choose)
    session.input("goal", "完成整个长任务", final=True)
    until(lambda: session.phase == "listening" and len(desktop.actions) == 120)
    assert session.enabled and session.error is None and session.version == 1


def test_pause_still_cancels_a_late_choice_in_long_task(factory):
    entered, release = threading.Event(), threading.Event()

    def choose(o, b, speech, history):
        if len(history) == 90:
            entered.set()
            assert release.wait(2)
        return ACTION, {}

    session, desktop = factory(choose)
    session.input("goal", "继续执行", final=True)
    try:
        assert entered.wait(2)
        session.pause()
    finally:
        release.set()
    until(lambda: session.phase == "paused")
    time.sleep(.03)
    assert len(desktop.actions) == 90 and not session.error


@pytest.mark.parametrize('still_valid', [True, False])
def test_speech_updated_during_choice_reaches_text_model_without_skipping_target_gate(factory, still_valid):
    entered, release = threading.Event(), threading.Event()
    choices, generated, gates = [], [], []
    action = {**ACTION, 'kind': 'fill'}

    def choose(o, b, speech, history):
        choices.append(speech)
        if len(choices) > 1:
            return None, {}
        entered.set()
        assert release.wait(2)
        return action, {}

    def generate(a, o, b, speech, history):
        generated.append(speech)
        return '杭州', {}

    def validate(a, text, o, b, speech, history):
        gates.append((a, text, speech))
        return still_valid, {}

    session, desktop = factory(choose, generate=generate, validate=validate)
    session.input('s', '填写上')
    assert entered.wait(2)
    session.input('s', '填写杭州', final=True)
    release.set()
    until(lambda: session.snapshot()['phase'] == 'listening')
    assert len(generated) == 1
    assert generated[0][0]['text'] == '填写杭州'
    assert len(gates) == 1 and gates[0][0] == action and gates[0][1] == '杭州'
    assert gates[0][2] == generated[0]
    assert desktop.actions == ([(action, '杭州')] if still_valid else [])
    events = [json.loads(line) for line in session.trace.path.read_text().splitlines()]
    text_event = next(e for e in events if e['type'] == 'text')
    assert text_event['version'] == 2 and text_event['choice_version'] == 1


def test_recovered_tool_wakes_listening_task_without_changing_speech(factory):
    seen = []

    def choose(o, b, speech, history):
        seen.append(speech)
        return None, {}

    session, desktop = factory(choose)
    session.input('s', '继续任务', final=True)
    until(lambda: session.snapshot()['phase'] == 'listening' and len(seen) == 1)
    session.refresh()
    until(lambda: len(seen) == 2)
    assert seen[0] == seen[1] and session.version == 1 and not desktop.actions
    session.pause()
    session.refresh()
    time.sleep(.03)
    assert len(seen) == 2 and not session.enabled


def test_field_readback_uses_semantics_not_stale_indices():
    from jev_ultrafast.live import field_readback
    action = {'kind': 'fill', 'surface': 'cu:chrome', 'role': 'AXTextField',
              'label': 'address', 'element_index': '9'}
    current = {**action, 'element_index': '12', 'value': 'https://example.org/search?q=old'}
    observation = {'actions': [current], 'surfaces': {'cu:chrome': {'selected_text': '/search?q=old'}}}
    result = field_readback(action, 'https://example.org', observation)
    assert result['comparison'] == 'different'
    assert result['observed'] == current['value']
    assert result['selected_text'] == '/search?q=old'
    assert field_readback(action, current['value'], observation)['comparison'] == 'equal'
    observation['actions'].append({**current, 'element_index': '25'})
    assert field_readback(action, 'https://example.org', observation)['comparison'] == 'unavailable'
    observation['actions'] = [{**current, 'surface': 'cu:other'}]
    assert field_readback(action, 'https://example.org', observation)['comparison'] == 'unavailable'
    assert field_readback({**action, 'kind': 'click'}, None, observation) is None


@pytest.mark.parametrize('change', ['none', 'target', 'speech', 'context'])
def test_rewrite_reuses_only_a_still_observed_target_for_the_validated_speech(factory, change):
    started, release = threading.Event(), threading.Event()
    choices, generated = [], []
    gate_done = False
    refreshed = False
    action = {**ACTION, 'kind': 'fill'}
    def choose(o, b, speech, history):
        choices.append(speech)
        return (None if history else o['actions'][0]), {}
    def generate(a, o, b, speech, history):
        generated.append(speech[-1]['text'])
        if len(generated) == 1:
            started.set()
            assert release.wait(2)
        return speech[-1]['text'], {}
    def validate(*args):
        nonlocal gate_done
        gate_done = True
        return False, {'text_review': 'REWRITE'}
    session, desktop = factory(choose, generate=generate, validate=validate)
    def observe():
        nonlocal action, refreshed
        if gate_done and not refreshed:
            refreshed = True
            if change == 'target':
                action = {**action, 'label': 'different observed target'}
            elif change == 'speech':
                session.input('s', 'newest')
        return {'surfaces': {'cu:test': {'title': 'App',
                'text': 'changed' if refreshed and change == 'context' else 'baseline'}}, 'actions': [action]}
    desktop.observe = observe
    session.input('s', 'old')
    assert started.wait(2)
    session.input('s', 'new')
    release.set()
    until(lambda: len(desktop.actions) == 1 and session.phase == 'listening')
    assert desktop.actions[0][1] == ('newest' if change == 'speech' else 'new')
    assert len(choices) == (2 if change == 'none' else 3)
    assert len(generated) == 2
