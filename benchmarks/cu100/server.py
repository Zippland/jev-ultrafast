"""Loopback browser fixture; reset/control and oracle are never exposed to the policy."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOCK = threading.Lock()
CONTROL = {"revision": 0, "case_id": "preflight", "code": "试验码-417"}
STATE = {}
EVENTS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, data, mime="application/json"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/":
            return self.reply(Path(__file__).with_name("fixture.html").read_bytes(), "text/html")
        with LOCK:
            self.reply(CONTROL if self.path == "/control" else {"state": STATE, "events": EVENTS})

    def do_POST(self):
        global CONTROL, STATE, EVENTS
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with LOCK:
            if self.path == "/reset":
                CONTROL = {**body, "revision": CONTROL["revision"] + 1}
                STATE, EVENTS = {}, []
            elif self.path == "/observe" and body.get("revision") == CONTROL["revision"]:
                STATE = body
                EVENTS.append(body)
            self.reply({"ok": True, "revision": CONTROL["revision"]})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8770), Handler).serve_forever()
