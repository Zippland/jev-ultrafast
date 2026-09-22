"""Manual paid 100-case real-app evaluation. Not part of the offline pytest suite."""

import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import random
import subprocess
import threading
import time
import traceback
import unicodedata
from pathlib import Path

import httpx
from browser_harness.helpers import cdp

from jev_ultrafast.browser import StalePage
from jev_ultrafast.demo import load_environment
from jev_ultrafast.mcp_client import AppMCPClient, MCPClient
from jev_ultrafast.mixed import MixedDesktop, choose_mixed, mixed_field_text, pending_valid

ROOT = Path(__file__).parent
ARTIFACTS = ROOT.parents[1] / "artifacts" / "cu100"
APP_IDS = {"calculator": "com.apple.calculator", "textedit": "com.apple.TextEdit", "browser": "com.google.Chrome"}
APP_NAMES = {"calculator": "计算器", "textedit": "文本编辑", "browser": "浏览器 Chrome"}


def desktop_locked():
    """Read session state; never change lock, sleep, permission or security settings."""
    root = plistlib.loads(subprocess.check_output(["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"], timeout=5))
    roots = root if isinstance(root, list) else [root]
    for item in roots:
        users = item.get("IOConsoleUsers", [])
        if isinstance(users, dict):
            users = [users]
        for user in users:
            if isinstance(user, dict) and user.get("CGSSessionScreenIsLocked"):
                return True
    return False


def wait_for_desktop(out, case_id):
    announced = False
    while desktop_locked():
        if not announced:
            print(f"PAUSED_LOCKED before {case_id}; no test or model call is running", flush=True)
            (out / "status.json").write_text(json.dumps({"status": "waiting_for_unlock", "next_case": case_id}))
            announced = True
        time.sleep(5)
    (out / "status.json").write_text(json.dumps({"status": "running", "next_case": case_id}))
    if announced:
        print(f"RESUMED_UNLOCKED {case_id}", flush=True)


class RecordingClient:
    def __init__(self, command=None, apps=None):
        self.log_path = None
        self.phase = "setup"
        self.log_lock = threading.Lock()
        self.transport = AppMCPClient(apps, command) if apps else MCPClient(command, timeout=60)
        self.tools = self.transport.tools

    @property
    def closed(self):
        return self.transport.closed

    def close(self):
        self.transport.close()

    def call(self, name, arguments=None):
        started = time.time()
        try:
            result = self.transport.call(name, arguments)
        except Exception as exc:
            self.record(
                {
                    "time": started,
                    "tool": name,
                    "arguments": arguments,
                    "error": str(exc),
                    "result": getattr(exc, "result", None),
                }
            )
            raise
        self.record({"time": started, "tool": name, "arguments": arguments, "result": result})
        return result

    def record(self, item):
        if self.log_path:
            with self.log_lock, self.log_path.open("a") as f:
                f.write(json.dumps({"phase": self.phase, **item}, ensure_ascii=False) + "\n")


class Timeline:
    def __init__(self, events):
        self.events = events
        self.start = time.monotonic()
        self.lock = threading.Lock()
        self.segments = {}
        self.published = []
        self.version = 0
        self.actions = 0
        self.stage = None
        self.stage_start = self.start
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.publish_due()
        self.thread.start()

    def publish_due(self):
        with self.lock:
            elapsed = (time.monotonic() - self.start) * 1000
            for i, event in enumerate(self.events):
                if any(x["index"] == i for x in self.published):
                    continue
                if elapsed < event["at_ms"] or self.actions < event.get("after_actions", 0):
                    continue
                if event.get("during") and (self.stage != event["during"] or time.monotonic() - self.stage_start < 0.1):
                    continue
                segment = event["segment"]
                revision = self.segments.get(segment, {}).get("revision", 0) + 1
                self.segments[segment] = {"segment": segment, "revision": revision, "text": event["text"]}
                self.version += 1
                self.published.append(
                    {"index": i, "version": self.version, "at_ms": round(elapsed), **self.segments[segment]}
                )

    def run(self):
        while not self.stopped.wait(0.02):
            self.publish_due()

    def enter(self, stage):
        with self.lock:
            self.stage, self.stage_start = stage, time.monotonic()

    def snapshot(self):
        self.publish_due()
        with self.lock:
            return self.version, [dict(self.segments[k]) for k in sorted(self.segments)]

    def executed(self):
        with self.lock:
            self.actions += 1
        self.publish_due()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)


def plain(value):
    return "".join(c for c in value if unicodedata.category(c) != "Cf").replace(",", "").strip()


def value_state(pages):
    static = [
        line.split(" value=", 1)[1].rsplit(" actions=", 1)[0]
        for line in pages["calculator"]["text"].splitlines()
        if " AXStaticText " in line and " value=" in line
    ]
    fields = [a for a in pages["textedit"]["actions"] if a["kind"] == "fill" and a.get("role") == "AXTextArea"]
    if not static or len(fields) != 1:
        raise ValueError("Independent AX verifier cannot identify calculator result or TextEdit body")
    data = httpx.get("http://127.0.0.1:8770/state", timeout=5).json()
    return {
        "calc": plain(static[-1]),
        "text": fields[0]["value"],
        "browser": data["state"],
        "browser_events": data["events"],
    }


def prepare(desktop, case):
    initial = case["initial"]
    native = desktop.adapters["cu:calculator"]
    page = native.observe(screenshot=False)
    # The native reset control changes between backspace, CE and AC. This is
    # fixture setup only: consume each newly observed control until AC completes.
    for _ in range(64):
        clear = next(
            (a for a in page["actions"] if a["kind"] == "click" and a["label"] in {"全部清除", "清除", "删除"}),
            None,
        )
        if clear is None:
            raise RuntimeError("Calculator reset control is unavailable in the observed AX state")
        native.act(clear, page)
        page = native.observe(screenshot=False)
        if clear["label"] == "全部清除":
            break
    else:
        raise RuntimeError("Calculator fixture reset exceeded 64 observed control actions")
    if initial["calc"] != "0":
        for digit in initial["calc"]:
            action = next(a for a in page["actions"] if a["kind"] == "click" and a["label"] == digit)
            native.act(action, page)
            page = native.observe(screenshot=False)
    native = desktop.adapters["cu:textedit"]
    page = native.observe(screenshot=False)
    field = next(a for a in page["actions"] if a["kind"] == "fill" and a.get("role") == "AXTextArea")
    native.act(field, page, text=initial["text"])
    native.observe(screenshot=False)
    control = {k: initial[k] for k in ("query", "note", "archived", "code")}
    revision = httpx.post("http://127.0.0.1:8770/reset", json={**control, "case_id": case["id"]}, timeout=5).json()[
        "revision"
    ]
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        state = httpx.get("http://127.0.0.1:8770/state", timeout=5).json()["state"]
        if state.get("revision") == revision:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Fixture did not acknowledge reset")
    pages = desktop.independent_observations()
    observed = value_state(pages)
    if observed["calc"] != initial["calc"] or observed["text"] != initial["text"]:
        raise RuntimeError("Fixture reset did not match initial native AX state")
    for key in ("query", "note", "archived"):
        if observed["browser"][key] != initial[key]:
            raise RuntimeError("Fixture reset did not match browser initial state")
    return pages, observed


def verify(case, initial, final, pages, history, timeline, error):
    failures = []
    expected = case["expect"]
    for key in ("calc", "text"):
        wanted = expected.get(key, initial[key])
        if final[key] != wanted:
            failures.append({"check": key, "expected": wanted, "actual": final[key]})
    b = final["browser"]
    wanted_b = expected.get("browser", {})
    for key in ("query", "note", "archived", "saved", "detail"):
        wanted = wanted_b.get(key, initial["browser"][key])
        if b.get(key) != wanted:
            failures.append({"check": "browser." + key, "expected": wanted, "actual": b.get(key)})
    if len(b.get("searches", [])) != wanted_b.get("search_count", 0):
        failures.append(
            {
                "check": "browser.search_count",
                "expected": wanted_b.get("search_count", 0),
                "actual": len(b.get("searches", [])),
            }
        )
    for key, source in [("last_search", "query"), ("last_search_archived", "archived")]:
        if key in wanted_b:
            actual = b.get("searches", [])[-1].get(source) if b.get("searches") else None
            if actual != wanted_b[key]:
                failures.append({"check": "browser." + key, "expected": wanted_b[key], "actual": actual})
    # Independent browser AX field read must agree with the fixture's DOM event oracle.
    ax_fields = {a["label"]: a.get("value", "") for a in pages["browser"]["actions"] if a["kind"] == "fill"}
    for label, key in [("关键词", "query"), ("备注", "note")]:
        if ax_fields.get(label) != b.get(key):
            failures.append({"check": "browser_AX." + key, "expected": b.get(key), "actual": ax_fields.get(label)})
    allowed = set()
    if "calc" in expected:
        allowed.add("calculator")
    if "text" in expected:
        allowed.add("textedit")
    if "browser" in expected:
        allowed.add("browser")
    unrequested = [h for h in history if h["app_key"] not in allowed]
    if unrequested:
        failures.append({"check": "unrequested_app_mutation", "count": len(unrequested)})
    if case["no_actions"] and history:
        failures.append({"check": "no_actions", "actual": len(history)})
    if "not_before_event" in case:
        event = next((p for p in timeline.published if p["index"] == case["not_before_event"]), None)
        if event and any(h["dispatched_ms"] < event["at_ms"] for h in history):
            failures.append({"check": "premature_action"})
    if len(timeline.published) != len(case["events"]):
        failures.append(
            {"check": "input_events_delivered", "expected": len(case["events"]), "actual": len(timeline.published)}
        )
    if error:
        failures.append({"check": "execution_error", "detail": error})
    return failures


def run_case(desktop, case, folder, limits):
    folder.mkdir(parents=True, exist_ok=False)
    desktop.client.log_path = folder / "mcp.jsonl"
    desktop.client.phase = "setup"
    initial_pages, initial = prepare(desktop, case)
    (folder / "initial.json").write_text(
        json.dumps({"pages": initial_pages, "oracle": initial}, ensure_ascii=False, indent=2)
    )
    desktop.client.phase = "run"
    desktop.events = []
    timeline = Timeline(case["events"])
    history, calls, text_calls = [], [], []
    error = None
    idle = 0
    surface_order = list(desktop.adapters)
    random.Random(case["id"]).shuffle(surface_order)
    try:
        for _ in range(limits["decisions"]):
            if time.monotonic() - timeline.start > limits["seconds"]:
                raise TimeoutError("Case time budget exhausted")
            if len(history) >= limits["actions"]:
                raise RuntimeError("Case action budget exhausted")
            observation = desktop.observe()
            version, speech = timeline.snapshot()
            timeline.enter("choice")
            action, decision = choose_mixed(observation, desktop.bindings, speech, history, surface_order=surface_order)
            timeline.enter(None)
            calls.append({"kind": "choice", "speech_version": version, **decision})
            if action is None:
                if timeline.snapshot()[0] != version:
                    # LISTEN was chosen before a new utterance arrived. Evaluate the new input first.
                    idle = 0
                    continue
                idle += 1
                pending = [
                    event
                    for i, event in enumerate(case["events"])
                    if not any(p["index"] == i for p in timeline.published)
                ]
                future_timers = any("after_actions" not in event and not event.get("during") for event in pending)
                if idle >= 2 and not future_timers:
                    break
                time.sleep(0.1)
                continue
            idle = 0
            latest, new_speech = timeline.snapshot()
            if latest != version:
                timeline.enter("gate")
                valid, gate = pending_valid(action, None, observation, desktop.bindings, new_speech, history)
                timeline.enter(None)
                calls.append({"kind": "pending", "speech_version": latest, **gate})
                if not valid:
                    continue
                version, speech = latest, new_speech
            text = None
            if action["kind"] == "fill":
                version, speech = timeline.snapshot()
                timeline.enter("text")
                text, details = mixed_field_text(action, observation, desktop.bindings, speech, history)
                timeline.enter(None)
                text_calls.append({"speech_version": version, "value": text, **details})
                latest, new_speech = timeline.snapshot()
                if latest != version:
                    timeline.enter("gate")
                    valid, gate = pending_valid(action, text, observation, desktop.bindings, new_speech, history)
                    timeline.enter(None)
                    calls.append({"kind": "pending", "speech_version": latest, **gate})
                    if not valid:
                        continue
                    version = latest
            dispatched = round((time.monotonic() - timeline.start) * 1000)
            try:
                event = desktop.act(action, observation, text=text)
            except StalePage:
                # Explicit pre-dispatch freshness rejection, never a replay of dispatched input.
                continue
            history.append(
                {
                    **event,
                    "app_key": action["app_key"],
                    "speech_version": version,
                    "dispatched_ms": dispatched,
                    "returned_ms": round((time.monotonic() - timeline.start) * 1000),
                }
            )
            timeline.executed()
        else:
            raise RuntimeError("Case decision budget exhausted")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        timeline.close()
    desktop.client.phase = "verify"
    try:
        final_pages = desktop.independent_observations()
        final = value_state(final_pages)
        failures = verify(case, initial, final, final_pages, history, timeline, error)
    except Exception as exc:
        final_pages, final = {}, {}
        failures = [{"check": "verification_error", "detail": str(exc)}]
        if error:
            failures.append({"check": "execution_error", "detail": error})
    result = {
        "id": case["id"],
        "group": case["group"],
        "category": case["category"],
        "pass": not failures,
        "failures": failures,
        "elapsed_ms": round((time.monotonic() - timeline.start) * 1000),
        "events": timeline.published,
        "history": history,
        "model_calls": calls,
        "text_calls": text_calls,
        "initial": initial,
        "final": final,
        "final_pages": final_pages,
        "execution_events": desktop.events,
        "candidate_surface_order": surface_order,
    }
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--ids", help="Comma-separated case IDs; omitted means all 100")
    parser.add_argument("--backend", choices=["cua-relay", "codex-cu"], default="cua-relay")
    parser.add_argument("--case-seconds", type=int, help="Explicit wall-clock override; actions/decisions stay frozen")
    args = parser.parse_args()
    execution_lock = (ARTIFACTS / ".execution.lock").open("a+")
    try:
        fcntl.flock(execution_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another CU100 runner owns the test desktop; parallel runs are not allowed") from None
    execution_lock.seek(0)
    execution_lock.truncate()
    execution_lock.write(str(os.getpid()))
    execution_lock.flush()
    load_environment()
    os.environ["TYPESAFE_MODEL"] = "jev-1.13.0"
    raw = (ROOT / "suite.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != (ROOT / "suite.sha256").read_text().strip():
        raise SystemExit("Frozen suite hash mismatch")
    suite = json.loads(raw)
    limits = dict(suite["limits"])
    if args.case_seconds is not None:
        if args.case_seconds <= 0:
            raise SystemExit("--case-seconds must be positive")
        limits["seconds"] = args.case_seconds
    cases = [c for c in suite["cases"] if not args.ids or c["id"] in args.ids.split(",")]
    windows = json.loads((ARTIFACTS / "windows.json").read_text())
    bindings = {
        key: {"app": app, "name": APP_NAMES[key], "window_id": windows[app]["window_id"],
              "title": windows[app]["title"]}
        for key, app in APP_IDS.items()
    }
    bindings["textedit"]["document_url"] = (ARTIFACTS / "JEV-CU100-SANDBOX.txt").as_uri()
    command = None
    if args.backend == "cua-relay":
        command = json.loads(os.environ.get("CU_RELAY_COMMAND", '["cua-relay", "serve"]'))
    targets = [t for t in cdp("Target.getTargets")["targetInfos"] if t.get("url") == "http://127.0.0.1:8770/"]
    if len(targets) != 1:
        raise SystemExit("Select exactly one owned browser fixture tab")
    out = ARTIFACTS / args.run_name
    out.mkdir(parents=True, exist_ok=False)
    source_files = [
        Path("jev_ultrafast/mixed.py"),
        Path("jev_ultrafast/computer.py"),
        Path("jev_ultrafast/relay.py"),
        Path("jev_ultrafast/mcp_client.py"),
        Path("jev_ultrafast/browser.py"),
        Path("jev_ultrafast/model.py"),
        Path("jev_ultrafast/questions.py"),
        ROOT / "run.py",
        ROOT / "fixture.html",
    ]
    manifest = {
        "suite_sha256": digest,
        "cu_backend": args.backend,
        "effective_limits": limits,
        "ids": [c["id"] for c in cases],
        "bindings": bindings,
        "browser_target": targets[0]["targetId"],
        "started_unix": time.time(),
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    def connect():
        apps = list(APP_IDS.values()) if args.backend == "cua-relay" else None
        return MixedDesktop(bindings, browser_target=targets[0]["targetId"],
                            client=RecordingClient(command, apps=apps), backend=args.backend)

    desktop = connect()
    summaries = []
    try:
        for index, case in enumerate(cases, 1):
            wait_for_desktop(out, case["id"])
            print(f"START {index}/{len(cases)} {case['id']} {case['category']}", flush=True)
            try:
                result = run_case(desktop, case, out / case["id"], limits)
                summary = {k: result[k] for k in ("id", "group", "category", "pass", "failures", "elapsed_ms")}
                summary.update(
                    actions=len(result["history"]),
                    choice_calls=len(result["model_calls"]),
                    text_calls=len(result["text_calls"]),
                    channels=[h["channel"] for h in result["history"]],
                )
            except Exception as exc:
                summary = {
                    "id": case["id"],
                    "group": case["group"],
                    "category": case["category"],
                    "pass": False,
                    "failures": [{"check": "setup_or_harness_error", "detail": f"{type(exc).__name__}: {exc}"}],
                }
                (out / case["id"]).mkdir(exist_ok=True)
                (out / case["id"] / "error.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
                (out / case["id"] / "traceback.txt").write_text(traceback.format_exc())
            summaries.append(summary)
            (out / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            # A case interrupted by desktop lock remains recorded, but is not a semantic test failure.
            # Stop rather than cascading one unavailable desktop into 99 misleading failed cases.
            if desktop_locked() or any(f["check"] == "setup_or_harness_error" for f in summary["failures"]):
                (out / "status.json").write_text(
                    json.dumps({"status": "interrupted", "case": case["id"], "completed_records": len(summaries)})
                )
                print("INTERRUPTED: restore the environment before starting another recorded run", flush=True)
                return
            if desktop.client.closed:
                desktop.close()
                desktop = connect()
    finally:
        desktop.close()
    print(f"COMPLETE {sum(s['pass'] for s in summaries)}/{len(summaries)}", flush=True)
    (out / "status.json").write_text(json.dumps({"status": "complete", "cases": len(summaries)}))


if __name__ == "__main__":
    main()
