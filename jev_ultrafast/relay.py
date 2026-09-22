"""Adapter for cua-relay's public official-runtime MCP contract, independent of Codex-CU."""

import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from .actions import KEYS, PAGES, observed_action, parameter_values
from .browser import StalePage
from .keyboard_fill import replace_field
from .mcp_client import MCPError
from .tracing import CURRENT_TRACE

# These patterns decode the tool's wire format, never user intent or task text.
ROW = re.compile(r"^(\t*)(\d+) ([^\n]*)(?:\n(?!\t*\d+ |The focused UI element)[^\n]*)*", re.M)
ATTRIBUTE = re.compile(r",? (Description|Help|ID|Value|URL|Placeholder|Secondary Actions): ")
ROLES = {
    "标准窗口": "AXWindow", "container": "AXGroup", "分离组": "AXSplitGroup", "分离器": "AXSplitter",
    "滚动区": "AXScrollArea", "滚动条": "AXScrollBar", "文本输入区": "AXTextArea", "文本栏": "AXTextField",
    "文本": "AXStaticText", "text": "AXStaticText", "按钮": "AXButton", "菜单按钮": "AXMenuButton",
    "弹出式按钮": "AXPopUpButton", "复选框": "AXCheckBox", "单选按钮": "AXRadioButton",
    "标题": "AXHeading", "图像": "AXImage", "工具栏": "AXToolbar", "HTML 内容": "AXWebArea",
    "标签组": "AXTabGroup", "标签": "AXRadioButton", "链接": "AXLink", "menu bar": "AXMenuBar",
    "link": "AXLink", "组合框": "AXComboBox",
    "关闭按钮": "AXWindowDecoration", "全屏幕按钮": "AXWindowDecoration", "缩放按钮": "AXWindowDecoration",
    "最小化按钮": "AXWindowDecoration",
}
CLICKABLE = {"AXButton", "AXMenuButton", "AXPopUpButton", "AXCheckBox", "AXRadioButton", "AXLink",
             "AXTextField", "AXTextArea", "AXComboBox"}
SECONDARY = {"Scroll Up": ("AXScrollUpByPage", "Scroll up"),
             "Scroll Down": ("AXScrollDownByPage", "Scroll down")}
RAISE_HELP = ("Raise requests a change in window stacking. It does not open a browser or navigate to a URL. "
              "A returned Raise call is not evidence that the application became foreground; "
              "use the observed desktop focus to check that separately.")


def read_relay_page(result, app, expected_title, tools, *, document_url=None, full_tools=False,
                    excluded_origins=(), keyboard_fill=False):
    texts = [block["text"] for block in result.get("content", []) if block.get("type") == "text"]
    raw = next((text for text in texts if "<app_state>" in text or text.startswith("App=")), None)
    if raw is None:
        if any(text.startswith("The Mac is locked") for text in texts):
            raise MCPError("Mac 已锁屏，Computer Use 无法读取界面。请手动解锁后继续；本次请求未执行操作。")
        raise MCPError("cua-relay returned no supported app_state")
    body = raw.split("<app_state>", 1)[1].rsplit("</app_state>", 1)[0].strip() if "<app_state>" in raw else raw.strip()
    identity = re.search(r"^App=.*\(bundleID ([^,]+), pid (\d+)\)$", body, re.M)
    if identity is None:
        identity = re.search(r"^App=([^\n]+) \(pid (\d+)\)$", body, re.M)
    window = re.search(r'^Window: "(.*)", App: .*\.$', body, re.M)
    if not identity or identity.group(1) != app or not window:
        raise MCPError("cua-relay returned an unexpected app/window identity")
    if expected_title is not None and window.group(1) != expected_title:
        raise MCPError("cua-relay key window does not match the bound test app/title; no action permitted")
    expected_title = window.group(1)
    tree = body.split("\n", 2)[2].split("\nThe focused UI element is ", 1)[0]
    tree = tree.split("\nSelected text:", 1)[0].rstrip()
    selection = re.search(r"(?:^|\n)Selected text: ```\n(.*?)\n```(?:\n\nNote:|\s*$)",
                          "\n".join(texts), re.S)
    selected_text = selection.group(1) if selection else None
    rows = list(ROW.finditer(tree))
    if not rows or [int(row.group(2)) for row in rows] != list(range(len(rows))):
        raise MCPError("Ambiguous or truncated cua-relay indexed tree")
    actions, lines, parents, elements = [], [], {}, []
    window_url = None
    focused = re.search(r"The focused UI element is (\d+) ", body)
    focused_index = focused.group(1) if focused else None
    excluded_depth, excluded_indices = None, set()
    for row in rows:
        depth, index = len(row.group(1)), row.group(2)
        if excluded_depth is not None:
            if depth > excluded_depth:
                excluded_indices.add(index)
                continue
            excluded_depth = None
        content = row.group(0)[len(row.group(1)) + len(index) + 1:].rstrip()
        role_name = next((name for name in sorted(ROLES, key=len, reverse=True)
                          if content == name or content.startswith(name + " ")), None)
        role = ROLES.get(role_name, "AXUnknown")
        if index != "0" and depth == 0 and not full_tools:
            break  # Native menu bar is outside the bound window, as in the Codex-CU adapter.
        if role == "AXWindowDecoration" and not full_tools:
            continue
        rest = content[len(role_name):].lstrip() if role_name else content
        flags = ""
        if rest.startswith("("):
            flags, separator, rest = rest[1:].partition(")")
            if not separator:
                raise MCPError("Malformed cua-relay element flags")
            rest = rest.lstrip()
        matches = list(ATTRIBUTE.finditer(" " + rest))
        attrs = {}
        label = rest
        if matches:
            expanded = " " + rest
            label = expanded[:matches[0].start()].strip().rstrip(",")
            for i, match in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(expanded)
                attrs[match.group(1)] = expanded[match.end():end].rstrip()
        if index == '0':
            window_url = attrs.get('URL')
        if role == "AXWebArea" and attrs.get("URL"):
            value = attrs["URL"]
            url = urlsplit(value if "://" in value else "http://" + value)
            if f"{url.scheme}://{url.netloc}" in excluded_origins:
                excluded_depth = depth
                excluded_indices.add(index)
                lines.append(f"[{index}] AXWebArea Agent control interface; contents excluded from task observation")
                continue
        label = attrs.get("Description", label)
        value = attrs.get("Value", "")
        if role == "AXStaticText":
            value, label = value or label, ""
        parent = parents.get(depth - 1, "none")
        parents[depth] = index
        disabled = "disabled" in flags.split(", ")
        exposed = "AXPress" if role in CLICKABLE and not disabled else ""
        hints = {key.lower(): attrs[key] for key in ("Help", "Placeholder", "URL") if attrs.get(key)}
        hint_text = "".join(f" {key}={value}" for key, value in hints.items())
        lines.append(f"[{index}] {role} {label} parent={parent} value={value} actions={exposed}{hint_text}")
        base = {"node": index, "element_index": index, "role": role, "label": label or role, "value": value}
        base.update(hints)
        elements.append(base)
        if role in {"AXCheckBox", "AXRadioButton"}:
            base["checked"] = "true" if value in {"1", "on"} else "false" if value in {"0", "off"} else value
        if disabled:
            continue
        if exposed and "click" in tools:
            actions.append({**base, "kind": "click", "tool": "click"})
        if (
            role in {"AXTextField", "AXTextArea", "AXComboBox"}
            and "settable" in flags.split(", ") and "set_value" in tools
        ):
            # Multiline editors retain the native setter: keyboard text input
            # did not preserve Chinese text in the real TextEdit comparison.
            tool = "field_fill" if keyboard_fill and role != "AXTextArea" else "set_value"
            actions.append({**base, "kind": "fill", "tool": tool})
        if full_tools:
            if role == "AXScrollArea" and "scroll" in tools:
                actions.append({**base, "kind": "scroll", "tool": "scroll"})
            if value and role in {"AXTextField", "AXTextArea", "AXStaticText"} and "select_text" in tools:
                actions.append({**base, "kind": "select_text", "tool": "select_text"})
        for secondary in attrs.get("Secondary Actions", "").split(", "):
            if full_tools and secondary and "perform_secondary_action" in tools:
                action = {**base, "kind": "secondary", "tool": "perform_secondary_action",
                          "relay_secondary_action": secondary, "label": f"{base['label']} · {secondary}"}
                if role == "AXWindow" and secondary == "Raise":
                    action["help"] = " ".join(filter(None, [base.get("help"), RAISE_HELP]))
                actions.append(action)
            elif secondary in SECONDARY and "perform_secondary_action" in tools:
                canonical, label_suffix = SECONDARY[secondary]
                actions.append({**base, "kind": "click", "tool": "perform_secondary_action",
                                "native_action": canonical, "relay_secondary_action": secondary,
                                "node": f"{index}:{canonical}", "label": f"{base['label']} · {label_suffix}"})
    if document_url and window_url != document_url:
        raise MCPError("cua-relay document URL does not match the bound test document")
    if full_tools and "press_key" in tools and focused_index not in excluded_indices:
        actions.append({"kind": "key", "tool": "press_key", "node": "0", "element_index": "0",
                        "role": "AXWindow", "label": f"{expected_title} · current keyboard focus",
                        "value": next((e["value"] for e in elements if e["node"] == focused_index), "")})
    if full_tools and "type_text" in tools and focused_index not in excluded_indices:
        # The public tool types literal keyboard input at app focus; it does not
        # require a settable AX field (e.g. keyboard-driven controls). Preserve
        # observed focus when available, otherwise bind to the observed window.
        focus = next((e for e in elements if e["node"] == focused_index), elements[0])
        actions.append({**focus, "kind": "insert", "tool": "type_text",
                        "label": f"{expected_title} · current keyboard input · {focus['label']}",
                        "help": "This relay keyboard path supports ASCII text only. For Unicode edits, "
                                "use an offered TYPE_TEXT field with its complete desired value, preserving "
                                "existing content outside the requested edit. "
                                "Do not transliterate or omit characters."})
    if full_tools and excluded_indices and "press_key" in tools:
        actions.append({"kind": "key", "tool": "press_key", "node": "0", "element_index": "0",
                        "role": "AXWindow", "label": "Open a new browser window; keep the recording page open",
                        "value": "", "control_surface_exit": True})
    for i, action in enumerate(actions, 1):
        action["id"] = f"e{i}"
    page = {
        "url": f"cu-relay://{app}/{expected_title}", "title": expected_title,
        "text": "\n".join(lines), "actions": actions, "w": 0, "h": 0,
        "scroll": {"y": 0, "height": 0}, "backend": "cua-relay", "relay_text": raw,
        "relay_pid": int(identity.group(2)),
        "focused_index": focused_index,
        "selected_text": selected_text,
        "agent_control_surface": bool(excluded_indices),
    }
    semantic = {k: page[k] for k in ("url", "title", "text", "actions", "relay_pid", "focused_index", "selected_text")}
    page["fingerprint"] = hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()
    return page


class RelayComputer:
    def __init__(self, app, *, expected_title, client, document_url=None, title_provider=None, full_tools=False,
                 excluded_origins=(), keyboard_fill=False, app_selector=None):
        self.app, self.expected_title, self.client = app, expected_title, client
        self.app_selector = app_selector or app
        self.document_url = document_url
        self.title_provider = title_provider
        self.full_tools = full_tools
        self.keyboard_fill = keyboard_fill
        self.excluded_origins = tuple(excluded_origins)
        self.pending, self.events = None, []
        self.rejected_observation = None
        for name, required in {"get_app_state": {"app"}, "click": {"app", "element_index"},
                               "set_value": {"app", "element_index", "value"}}.items():
            properties = self.client.tools.get(name, {}).get("inputSchema", {}).get("properties", {})
            if not required <= properties.keys() or "window_id" in properties:
                raise MCPError(f"Unexpected cua-relay public schema for {name}")
        if keyboard_fill:
            for name, required in {"press_key": {"app", "key"}, "type_text": {"app", "text"}}.items():
                properties = self.client.tools.get(name, {}).get("inputSchema", {}).get("properties", {})
                if not required <= properties.keys():
                    raise MCPError(f"Keyboard fill requires public {name} capability")

    def _read(self, screenshot=False):
        result = self.client.call("get_app_state", {"app": self.app_selector})
        title = self.title_provider() if self.title_provider else self.expected_title
        return read_relay_page(result, self.app, title, self.client.tools, document_url=self.document_url,
                               full_tools=self.full_tools, excluded_origins=self.excluded_origins,
                               keyboard_fill=self.keyboard_fill)

    def observe(self, screenshot=False):
        if self.rejected_observation is not None:
            page, self.rejected_observation = self.rejected_observation, None
            return page
        if self.pending is not None:
            result, self.pending = self.pending, None
            return read_relay_page(result, self.app, self.expected_title, self.client.tools,
                                   document_url=self.document_url, full_tools=self.full_tools,
                                   excluded_origins=self.excluded_origins, keyboard_fill=self.keyboard_fill)
        return self._read(screenshot)

    def fresh(self, page, action=None):
        # This read supersedes any earlier returned tree. If it invalidates the
        # decision, reuse the new observation for replanning, never for dispatch.
        self.pending = self.rejected_observation = None
        current = self._read(False)
        if current["fingerprint"] != page["fingerprint"]:
            self.rejected_observation = current
            return False
        page.update(current)
        # A speech gate may cancel before dispatch. Keep this fresh observation
        # for replanning rather than fetching the same state again. It never
        # bypasses the next action's own freshness check.
        self.rejected_observation = current
        return True

    def act(self, action, page, text=None, before_dispatch=None):
        # Relay's lease is app-scoped and consumed by one action. Reacquire it
        # after other-app observations, then use only indices from identical state.
        if not self.fresh(page, action):
            raise StalePage("cua-relay window changed before input; observe and choose again")
        observed_action(action, page["actions"])
        if action["tool"] == "field_fill":
            if not isinstance(text, str) or not text or any(c in text for c in '\r\n\t'):
                raise ValueError("Single-line fill requires a nonempty single-line literal value")
            if before_dispatch:
                before_dispatch()
            trace = CURRENT_TRACE.get()
            if trace:
                trace.emit("fill.strategy", strategy="keyboard" if text.isascii() else "native_unicode",
                           role=action["role"], element_index=action["element_index"])
            # Public keyboard input dropped non-ASCII text in both TextEdit and
            # Chrome probes; the native setter preserved the exact Unicode value.
            if not text.isascii():
                self._dispatch("set_value", {"app": self.app, "element_index": action["element_index"],
                                             "value": text})
                return

            def read():
                self.pending = None
                return self._read()

            final = replace_field(page, action, text, read,
                                  lambda tool, args: self._dispatch(tool, {"app": self.app, **args}))
            self.rejected_observation = final
            return
        params = parameter_values(action)
        args = {"app": self.app, "element_index": action["element_index"]}
        if action["tool"] == "set_value":
            args["value"] = text
        elif action["tool"] == "click" and "parameters" in action:
            style = params["style"]
            args.update(mouse_button="left" if style == "double" else style, click_count=2 if style == "double" else 1)
        elif action["tool"] == "perform_secondary_action":
            args["action"] = action["relay_secondary_action"]
        elif action["tool"] == "scroll":
            args.update(direction=params["direction"], pages=PAGES[params["amount"]])
        elif action["tool"] == "select_text":
            if not isinstance(text, str) or not text or action["value"].count(text) != 1:
                raise ValueError("Text selection must uniquely match the observed element value")
            args.update(text=text, selection=params["selection"])
        elif action["tool"] == "press_key":
            args = {"app": self.app, "key": KEYS[params["key"]][0]}
        elif action["tool"] == "type_text":
            if not isinstance(text, str) or not text.isascii():
                raise ValueError("当前 MCP 键盘输入不能可靠保留非 ASCII 文字；尚未派发输入。"
                                 "请通过可填写字段的 TYPE_TEXT 完成编辑。")
            args = {"app": self.app, "text": text}
        if before_dispatch:
            before_dispatch()
        self._dispatch(action["tool"], args)

    def _dispatch(self, tool, args):
        args = {**args, "app": self.app_selector}
        self.rejected_observation = None
        event = {"tool": tool, "arguments": args, "status": "dispatched", "time": time.time()}
        self.events.append(event)
        try:
            result = self.client.call(tool, args)
        except Exception:
            event["status"] = "uncertain"
            raise
        event["status"] = "returned"
        # Official actions include a post-action tree. Expose it only after the
        # returned execution has been recorded; it does not renew the action lease.
        if any(b.get("type") == "text" and b.get("text", "").startswith("App=")
               for b in result.get("content", [])):
            self.pending = result

    def close(self):
        self.client.close()
