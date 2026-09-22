import hashlib

from jev_ultrafast.runtime import capture_runtime


def test_disk_edits_do_not_change_running_source_evidence_or_served_frontend(tmp_path):
    (tmp_path / "static").mkdir()
    source = tmp_path / "live.py"
    frontend = tmp_path / "static" / "voice.js"
    source.write_text("old_backend")
    frontend.write_text("old_frontend")
    running = capture_runtime(tmp_path)
    source.write_text("new_backend")
    frontend.write_text("new_frontend")
    assert running["assets"]["voice.js"] == "old_frontend"
    assert running["manifest"]["live.py"] == hashlib.sha256(b"old_backend").hexdigest()
    restarted = capture_runtime(tmp_path)
    assert restarted["id"] != running["id"]
    assert restarted["assets"]["voice.js"] == "new_frontend"
