"""Append-only local trace, including starts and failures before a session can be interrupted."""

import json
import threading
import time
import uuid
from contextvars import ContextVar
from pathlib import Path

from .runtime import RUNTIME
from .telemetry import Telemetry

CURRENT_TRACE = ContextVar("jev_trace", default=None)


class Trace:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.path = self.folder / "events.jsonl"
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.sequence = 0
        self.recent = []
        self.telemetry = Telemetry()
        (self.folder / "source-sha256.json").write_text(json.dumps(RUNTIME["manifest"], indent=2) + "\n")
        (self.folder / "runtime.json").write_text(json.dumps({"id": RUNTIME["id"],
                                                           "source_snapshot": "process_start"}) + "\n")

    def emit(self, event_type, **data):
        with self.lock:
            self.sequence += 1
            event = {"seq": self.sequence, "time": time.time(),
                     "ms": round((time.monotonic() - self.started) * 1000), "type": event_type, **data}
            with self.path.open("a") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.telemetry.add(event)
            # The browser sees small summaries; full requests/results stay in the downloadable trace.
            self.recent.append({k: v for k, v in event.items() if k not in {"request", "response", "result", "pages"}})
            self.recent = self.recent[-100:]
            self.changed.notify_all()
            return event

    def wait_after(self, sequence, timeout=1):
        with self.changed:
            self.changed.wait_for(lambda: self.sequence != sequence, timeout=timeout)

    def snapshot(self):
        with self.lock:
            return list(self.recent)

    def dashboard(self):
        with self.lock:
            return self.telemetry.snapshot()


def traced_call(trace, kind, request, call):
    if trace is None:
        return call()
    span = uuid.uuid4().hex[:12]
    start = time.monotonic()
    trace.emit(kind + ".start", span=span, request=request)
    try:
        result = call()
    except Exception as exc:
        trace.emit(kind + ".error", span=span, error=f"{type(exc).__name__}: {exc}",
                   latency_ms=round((time.monotonic() - start) * 1000))
        raise
    trace.emit(kind + ".end", span=span, result=result, latency_ms=round((time.monotonic() - start) * 1000))
    return result


class TracedMCP:
    def __init__(self, transport, trace):
        self.transport, self.trace = transport, trace
        self.tools = transport.tools

    @property
    def closed(self):
        return self.transport.closed

    def call(self, name, arguments=None):
        return traced_call(self.trace, "mcp", {"tool": name, "arguments": arguments},
                           lambda: self.transport.call(name, arguments))

    def close(self):
        self.transport.close()
