import threading
from types import SimpleNamespace

import pytest

from jev_ultrafast.voice_demo import Workbench


def test_voice_discovery_only_exposes_native_apps(tmp_path):
    workbench = Workbench(8767, tmp_path)
    workbench.app_inventory = SimpleNamespace(snapshot=lambda: {
        "apps": [{"id": "com.google.Chrome", "name": "Chrome"}],
        "error": None, "age_ms": 0, "refreshing": False})
    catalog = workbench.discover()
    assert catalog["execution_mode"] == "computer_use"
    assert not catalog["tabs"] and not catalog["errors"]
    assert not hasattr(workbench, "browser_inventory")


def test_replay_cleanup_cannot_pause_replacement_recording(tmp_path):
    workbench = Workbench(8767, tmp_path)
    paused = []
    workbench.session = SimpleNamespace(touch=lambda: None, pause=lambda: paused.append(True))
    workbench.voice = SimpleNamespace(lock=threading.RLock(), recording_id="user-recording")
    with pytest.raises(ValueError, match="未暂停新录音"):
        workbench.command("pause", {"recording_id": "old-test-recording"})
    assert not paused
    workbench.command("pause", {"recording_id": "user-recording"})
    assert paused == [True]
    workbench.command("pause", {})
    assert paused == [True, True]


def test_state_long_poll_wakes_on_new_trace_event_without_missing_existing_event(tmp_path):
    from jev_ultrafast.tracing import Trace
    workbench = Workbench(8767, tmp_path)
    workbench.trace = Trace(tmp_path / 'trace')
    before = workbench.state()['state_cursor']
    entered, finished = threading.Event(), threading.Event()
    result = []
    original = workbench.trace.wait_after
    def wait_after(sequence):
        entered.set()
        original(sequence)
    workbench.trace.wait_after = wait_after
    def read():
        result.append(workbench.state(after=before))
        finished.set()
    worker = threading.Thread(target=read)
    worker.start()
    assert entered.wait(1)
    workbench.trace.emit('test.changed')
    assert finished.wait(.5)
    worker.join()
    assert result[0]['state_cursor'] != before
    # The next update arrived before the client subscribed; return it directly.
    latest = result[0]['state_cursor']
    workbench.trace.emit('test.changed_again')
    workbench.trace.wait_after = lambda _: pytest.fail('must not wait on an obsolete cursor')
    assert workbench.state(after=latest)['state_cursor'] != latest


def test_state_cursor_precedes_snapshot_so_concurrent_update_is_not_lost(tmp_path):
    from jev_ultrafast.tracing import Trace
    workbench = Workbench(8767, tmp_path)
    workbench.trace = Trace(tmp_path / 'trace')
    def snapshot():
        workbench.trace.emit('test.during_snapshot')
        return {}
    workbench.session = SimpleNamespace(touch=lambda: None, snapshot=snapshot)
    first = workbench.state()['state_cursor']
    workbench.trace.wait_after = lambda _: pytest.fail('event during snapshot must remain unread')
    assert workbench.state(after=first)['state_cursor'] != first


def test_preloaded_asr_never_starts_recording_until_explicit_start(tmp_path, monkeypatch):
    from jev_ultrafast.tracing import Trace
    from jev_ultrafast.voice import transport
    monkeypatch.setattr(transport, 'configuration', lambda: {'available': True})
    voice = transport.VoiceTransport(None, trace=Trace(tmp_path / 'warmup'))
    launches, messages = [], []
    def launch(_config):
        launches.append(True)
        voice.process = object()
    monkeypatch.setattr(voice, '_launch', launch)
    monkeypatch.setattr(voice, '_send', messages.append)
    voice.preload()
    voice.preload()
    assert launches == [True] and messages == []
    assert voice.recording_id is None and voice.bytes == 0
    voice._event({'type': 'loaded'})
    assert voice.loaded and voice.status == 'idle'
    session = SimpleNamespace(trace=Trace(tmp_path / 'session'), fail=lambda _: None)
    voice.attach(session)
    assert voice.trace is session.trace
    assert not messages and voice.recording_id is None
    voice.start()
    assert launches == [True]
    assert messages == [{'type': 'start', 'recording_id': voice.recording_id}]
    with pytest.raises(ValueError, match='预热进程'):
        voice.attach(session)


def test_preload_error_is_reported_without_a_desktop_session(tmp_path):
    from jev_ultrafast.tracing import Trace
    from jev_ultrafast.voice.transport import VoiceTransport
    voice = VoiceTransport(None, trace=Trace(tmp_path / 'warmup'))
    voice._event({'type': 'fatal', 'reason': 'model missing'})
    assert voice.snapshot()['error'] == 'model missing' and voice.status == 'error'
    assert voice.session is None and voice.recording_id is None
