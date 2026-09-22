"""Loopback workbench: streaming local speech -> Jev choice/text -> real AX execution."""

import argparse
import json
import os
import secrets
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .automatic import AutomaticDesktop
from .demo import load_environment
from .inventory import AppInventory
from .live import LiveSession
from .mcp_client import MCPClient
from .runtime import RUNTIME
from .tracing import Trace, TracedMCP
from .voice.transport import VoiceTransport, configuration


def relay_command():
    return json.loads(os.environ.get("CU_RELAY_COMMAND", '["cua-relay", "serve"]'))


def running_apps(result):
    """Decode list_apps' documented line format; this never reads natural-language goals."""
    apps = []
    for block in result.get("content", []):
        if block.get("type") != "text":
            continue
        for line in block["text"].splitlines():
            parts = line.split(" — ")
            if len(parts) != 3 or " [" not in parts[2]:
                continue
            app, flags = parts[2].split(" [", 1)
            if "running" in flags.rstrip("]").split(", "):
                apps.append({"id": app, "name": parts[0], "path": parts[1],
                             "frontmost": "frontmost" in flags.rstrip("]").split(", ")})
    return apps


class Workbench:
    def __init__(self, port, artifacts):
        self.port, self.artifacts = port, Path(artifacts)
        self.token = secrets.token_urlsafe(32)
        self.session = self.voice = self.trace = None
        self.catalog = {"apps": [], "tabs": []}
        self.operation = threading.Lock()
        self.closing = False
        self.inventory_client = None
        self.app_inventory = AppInventory(self._list_apps)

    def _list_apps(self):
        if self.inventory_client is None:
            self.inventory_client = TracedMCP(MCPClient(relay_command()), self.trace)
        return running_apps(self.inventory_client.call("list_apps", {}))

    def preload_voice(self):
        if self.voice or self.session or not configuration()["available"]:
            return
        folder = self.artifacts / ("warmup-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
        self.trace = Trace(folder)
        self.voice = VoiceTransport(None, trace=self.trace)
        try:
            self.voice.preload()
        except Exception as exc:
            self.trace.emit("voice.preload_failed", error=str(exc))
            self.voice.close()
            self.voice = None

    def state(self, after=None):
        trace = self.trace
        sequence = trace.sequence if trace else 0
        cursor = f"{trace.path if trace else ''}:{sequence}:{self.session is not None}:{self.closing}"
        if after == cursor:
            if trace:
                trace.wait_after(sequence)
            else:
                threading.Event().wait(.5)
        # Capture the cursor BEFORE snapshots: an event arriving while we read
        # state must remain eligible for the client's next request.
        trace = self.trace
        cursor = f"{trace.path if trace else ''}:{trace.sequence if trace else 0}:" \
                 f"{self.session is not None}:{self.closing}"
        session, voice = self.session, self.voice
        if session:
            session.touch()
        return {"connected": session is not None, "closing": self.closing,
                "state_cursor": cursor,
                "runtime_id": RUNTIME["id"],
                "session": session.snapshot() if session else None,
                "voice": voice.snapshot() if voice else {"status": "idle"},
                "asr": configuration(), "catalog": self.catalog,
                "choice_model": os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"),
                "text_model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
                "trace_path": str(self.trace.path) if self.trace else None}

    def discover(self):
        errors = []
        native = self.app_inventory.snapshot()
        apps = native["apps"]
        if native["error"]:
            errors.append(f"Computer Use：{native['error']}")
        self.catalog = {"apps": apps, "tabs": [], "errors": errors, "execution_mode": "computer_use",
                        "apps_age_ms": native["age_ms"], "apps_refreshing": native["refreshing"]}
        if self.trace:
            self.trace.emit("inventory", **self.catalog)
        if not apps:
            raise ValueError("未发现可用应用。" + "；".join(errors))
        return self.catalog

    def connect(self, body=None):
        if self.session or self.closing:
            raise ValueError("当前会话尚未断开")
        for key in ("TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY"):
            if not os.environ.get(key):
                raise ValueError(f"请先在服务端 .env 配置 {key}")
        folder = self.artifacts / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
        self.trace = Trace(folder)
        self.app_inventory.close()
        if self.inventory_client:
            self.inventory_client.close()
            self.inventory_client = None
        self.app_inventory = AppInventory(self._list_apps)
        self.trace.emit("session.start", backend="cua-relay", routing="automatic", channels=["CU"],
                        runtime_id=RUNTIME["id"],
                        choice_model=os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"),
                        text_model=os.environ.get("TEXT_MODEL", "deepseek-chat"))
        desktop = AutomaticDesktop(self.discover, relay_command(), self.trace,
                                   excluded_origins=(f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"))
        self.session = LiveSession(desktop, self.trace)
        if self.voice:
            self.voice.attach(self.session)
        else:
            self.voice = VoiceTransport(self.session)
        return self.state()

    def disconnect(self):
        if self.closing:
            return
        if not self.session:
            if self.voice:
                self.voice.close()
                self.voice = None
            return
        self.closing = True
        session, voice = self.session, self.voice
        session.close()

        def release():
            try:
                if voice:
                    voice.close()
                session.worker.join()
                self.app_inventory.close()
                if self.inventory_client:
                    self.inventory_client.close()
                    self.inventory_client = None
            finally:
                self.session = self.voice = None
                self.closing = False

        threading.Thread(target=release, name="jev-disconnect", daemon=True).start()

    def command(self, name, body):
        if name in {"discover", "connect"}:
            if not self.operation.acquire(blocking=False):
                raise ValueError("正在连接或刷新，请稍等")
            try:
                return self.discover() if name == "discover" else self.connect(body)
            finally:
                self.operation.release()
        if name == "disconnect":
            self.disconnect()
            return {"ok": True}
        if name == "voice/start" and not self.session:
            if not self.operation.acquire(blocking=False):
                raise ValueError("正在准备会话")
            try:
                self.connect()
            finally:
                self.operation.release()
        session, voice = self.session, self.voice
        if not session or self.closing:
            raise ValueError("请先连接应用")
        session.touch()
        if name == "pause":
            if "recording_id" in body:
                with voice.lock:
                    if body["recording_id"] != voice.recording_id:
                        raise ValueError("录音已更换，未暂停新录音")
                    session.pause()
            else:
                session.pause()
        elif name == "resume":
            session.resume()
        elif name == "input":
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("请输入指令")
            return {"version": session.input(uuid.uuid4().hex, text, final=True)}
        elif name == "voice/start":
            if voice.status == "error":
                voice.close()
                self.voice = voice = VoiceTransport(session)
            if not session.enabled:
                session.resume()
            return voice.start(body.get("capture_settings"))
        elif name == "voice/audio":
            voice.audio(body.get("recording_id"), body.get("sequence"), body.get("pcm_s16le"))
        elif name == "voice/finish":
            session.pause("关闭麦克风")
            voice.finish(body.get("recording_id"))
        elif name == "voice/cancel":
            session.pause("取消录音")
            voice.cancel()
        else:
            raise ValueError("未知操作")
        return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    @property
    def app(self):
        return self.server.workbench

    def allowed(self, token=False):
        origin = f"http://127.0.0.1:{self.app.port}"
        return (self.headers.get("Host") == f"127.0.0.1:{self.app.port}"
                and self.headers.get("Origin") in (None, origin)
                and self.headers.get("Sec-Fetch-Site") not in {"cross-site"}
                and (not token or self.headers.get("X-Voice-Token") == self.app.token))

    def send(self, status, value, mime="application/json; charset=utf-8"):
        data = value if isinstance(value, bytes) else (
            json.dumps(value, ensure_ascii=False).encode() if mime.startswith("application/json")
            else value.encode())
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        if not self.allowed(token=path.startswith("/api/")):
            return self.send(403, {"error": "仅限本机工作台请求"})
        if path == "/api/state":
            after = parse_qs(urlparse(self.path).query).get("after", [None])[0]
            return self.send(200, self.app.state(after=after))
        if path == "/api/trace":
            if not self.app.trace or not self.app.trace.path.exists():
                return self.send(404, {"error": "尚无 trace"})
            return self.send(200, self.app.trace.path.read_bytes(), "application/x-ndjson; charset=utf-8")
        files = {"/": ("voice.html", "text/html"), "/voice.js": ("voice.js", "text/javascript"),
                 "/voice.css": ("voice.css", "text/css"),
                 "/pcm-worklet.js": ("pcm-worklet.js", "text/javascript")}
        if path not in files:
            return self.send(404, {"error": "Not found"})
        name, mime = files[path]
        content = RUNTIME["assets"][name].replace("__VOICE_TOKEN__", self.app.token)
        return self.send(200, content, mime + "; charset=utf-8")

    def do_POST(self):
        if not self.allowed(token=True):
            return self.send(403, {"error": "仅限本机工作台请求"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("请求大小无效")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求格式无效")
            result = self.app.command(urlparse(self.path).path.removeprefix("/api/"), body)
            self.send(200, result)
        except Exception as exc:
            self.send(400, {"error": f"{type(exc).__name__}: {exc}"})


def main():
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--artifacts", default="artifacts/voice")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.workbench = Workbench(args.port, Path(args.artifacts).resolve())
    if os.environ.get("VOICE_PRELOAD", "1") != "0":
        server.workbench.preload_voice()
    print(f"Jev 语音工作台 http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.workbench.disconnect()
        server.server_close()


if __name__ == "__main__":
    main()
