"""Real Chrome tools against an owned local fixture; no ASR or paid model calls."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jev_ultrafast.browser import traced_cdp
from jev_ultrafast.browser_tools import BrowserTools
from jev_ultrafast.tracing import CURRENT_TRACE, Trace

HTML = """<!doctype html><meta charset=utf-8><title>Jev Tool Smoke</title>
<textarea aria-label="正文">旧内容</textarea><button onclick="this.textContent='已点击'">测试按钮</button>
<label>上传<input type=file></label>
<div aria-label="滚动测试" style="height:80px;width:200px;overflow:auto"><p style="height:500px">滚动内容</p></div>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        data = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    folder = Path("artifacts/voice-tools") / time.strftime("%Y%m%d-%H%M%S")
    trace = Trace(folder)
    token = CURRENT_TRACE.set(trace)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    target = traced_cdp("Target.createTarget", url=url, background=True)["targetId"]
    browser = BrowserTools(target)
    results, extra_targets = [], []

    def run(kind, text=None, parameters=None):
        page = browser.observe()
        action = next(a for a in page["actions"] if a["kind"] == kind)
        if parameters:
            action = {**action, "parameters": parameters}
        browser.act(action, page, text=text, before_dispatch=lambda: trace.emit(
            "action.dispatch", version=0, action=action, text=text))
        trace.emit("action.returned", version=0, kind=kind, text=text)

    def check(name, expression, expected):
        actual = browser.evaluate(expression)
        results.append({"name": name, "actual": actual, "expected": expected, "pass": actual == expected})
        assert actual == expected, (name, actual, expected)

    try:
        run("fill", "甲乙丙")
        check("replace", "document.querySelector('textarea').value", "甲乙丙")
        run("select_text", "乙", {"selection": "text"})
        check("selection", "(()=>{const e=document.querySelector('textarea');"
              "return e.value.slice(e.selectionStart,e.selectionEnd)})()", "乙")
        run("insert", "新")
        check("insert over selection", "document.querySelector('textarea').value", "甲新丙")
        run("key", parameters={"key": "SELECT_ALL"})
        run("insert", "快捷键成功")
        check("keyboard", "document.querySelector('textarea').value", "快捷键成功")
        upload = trace.folder / "upload.txt"
        upload.write_text("owned smoke fixture")
        run("upload", str(upload.resolve()))
        check("upload", "document.querySelector('input[type=file]').files[0].name", "upload.txt")
        run("scroll", parameters={"direction": "down", "amount": "page"})
        check("nested scroll", "document.querySelector('[aria-label=滚动测试]').scrollTop>0", True)
        run("navigate", url + "second")
        run("back")
        check("back", "location.pathname", "/")
        run("forward")
        check("forward", "location.pathname", "/second")
        run("screenshot")
        run("new_tab", url)
        extra_targets.append(browser.created_target)
        child = BrowserTools(browser.created_target)
        try:
            page = child.observe()
            close = next(a for a in page["actions"] if a["kind"] == "close_tab")
            child.act(close, page)
            results.append({"name": "new/close tab", "pass": child.closed_by_action})
        finally:
            child.close()
        extra_targets.remove(browser.created_target)
        print(json.dumps({"results": results, "trace": str(trace.path)}, ensure_ascii=False), flush=True)
    finally:
        (trace.folder / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        browser.close()
        for target_id in [target, *extra_targets]:
            try:
                traced_cdp("Target.closeTarget", targetId=target_id)
            except Exception:
                pass
        server.shutdown()
        server.server_close()
        CURRENT_TRACE.reset(token)


if __name__ == "__main__":
    main()
