"""Explicit standalone CU diagnostic; no model calls. Creates a browser test window."""

import json

from jev_ultrafast.mcp_client import MCPClient
from jev_ultrafast.relay import read_relay_page
from jev_ultrafast.tracing import CURRENT_TRACE, Trace, TracedMCP
from jev_ultrafast.voice_demo import relay_command


def main():
    t = Trace("artifacts/addressing/cu-exclusive-write-v1")
    tok = CURRENT_TRACE.set(t)
    c = TracedMCP(MCPClient(relay_command(), timeout=60), t)
    app = "com.google.Chrome"

    def read():
        r = c.call("get_app_state", {"app": app})
        p = read_relay_page(r, app, None, c.tools, full_tools=True)
        print(
            json.dumps(
                {
                    "title": p["title"],
                    "focus": p["focused_index"],
                    "fields": [
                        (a["element_index"], a["label"], a["value"]) for a in p["actions"] if a["kind"] == "fill"
                    ],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return p

    def key(k):
        read()
        c.call("press_key", {"app": app, "key": k})

    try:
        key("super+n")
        p = read()
        a = next(a for a in p["actions"] if a["kind"] == "fill" and a["label"] == "地址和搜索栏")
        c.call("set_value", {"app": app, "element_index": a["element_index"], "value": "https://www.google.com"})
        read()
        key("super+l")
        read()
        c.call("type_text", {"app": app, "text": "https://www.google.com"})
        read()
        key("Return")
        read()
    finally:
        c.close()
        CURRENT_TRACE.reset(tok)


if __name__ == "__main__":
    main()
