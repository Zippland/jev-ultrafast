"""Codex-CU adapter: public MCP tools only; no imports from the execution repository."""

import hashlib
import json
import re
import time

from .browser import StalePage
from .mcp_client import MCPClient, MCPError, MCPToolError
from .model import action_space

ELEMENT = re.compile(
    r"^(\d+) (AX\w+) (.*?) actions=([^\n]*?) depth=(\d+) parent=(\S+)"
    r"(?: bounds=\[([^\]]+)\])? value=(.*?)(?=^\d+ AX\w+ |\Z)", re.M | re.S,
)
EDITABLE = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}
SECONDARY = {"AXShowMenu": "Show menu", "AXScrollUpByPage": "Scroll up", "AXScrollDownByPage": "Scroll down"}


def json_content(result):
    for block in result.get("content", []):
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except ValueError:
                pass
    raise MCPError("CU returned no structured catalogue; check its MCP version.")


def read_page(result, app, window_id, tools):
    meta = result.get("_meta", {})
    window = meta.get("window", {})
    if window.get("app") != app or window.get("window_id") != window_id or not meta.get("observation_id"):
        raise MCPError("CU observation did not match the selected app/window. Start a fresh run.")
    texts = [b["text"] for b in result.get("content", []) if b.get("type") == "text"]
    tree = next((t for t in reversed(texts) if "Window elements (" in t), None)
    if tree is None:
        raise MCPError("Unsupported CU observation format: no indexed window tree.")
    if meta.get("truncated"):
        raise MCPError("CU truncated this window's element tree. Choose a smaller window before running Jev.")
    # Keep a focused element only when CU also binds it to this window. CU deduplicates it in the tree.
    tree = tree.split("\n", 1)[-1]
    tree_start = tree.index("Window elements (")
    matches = list(ELEMENT.finditer(tree))
    if not matches:
        raise MCPError("CU returned no parseable AX elements; refusing an empty action space.")
    indices = [int(m.group(1)) for m in matches]
    if indices != list(range(len(indices))) or meta.get("elements", len(indices)) != len(indices):
        raise MCPError("Ambiguous CU element tree. No indexed actions will be executed.")
    actions, safe_lines = [], []
    for match in matches:
        index, role, label, exposed, depth, parent, bounds, value = match.groups()
        if match.start() < tree_start and meta.get("focused_window_id") != window_id:
            continue
        if role == "AXSecureTextField":
            continue
        label, value = label.strip(), value.split("\nWindow elements (")[0].strip()
        names = set(exposed.split(","))
        base = {"node": index, "role": role, "label": label or role,
                "value": value, "element_index": index}
        if role in {"AXCheckBox", "AXRadioButton", "AXSwitch"}:
            base["checked"] = "true" if value == "1" else "false" if value == "0" else value
        if bounds:
            try:
                x, y, w, h = map(float, bounds.split(","))
                base["rect"] = {"x": x, "y": y, "w": w, "h": h}
            except ValueError:
                raise MCPError("Invalid CU element bounds.") from None
        safe_lines.append(f"[{index}] {role} {label} parent={parent} value={value} actions={exposed}")
        if "AXPress" in names and "click" in tools:
            actions.append({**base, "kind": "click", "tool": "click"})
        if role in EDITABLE and "set_value" in tools:
            actions.append({**base, "kind": "fill", "tool": "set_value"})
        # Native menus and scroll actions are observed options, never generated key/coordinate programs.
        for native in sorted(names & SECONDARY.keys()):
            if "perform_secondary_action" in tools:
                actions.append({**base, "kind": "click", "tool": "perform_secondary_action",
                                "native_action": native, "label": f"{base['label']} · {SECONDARY[native]}"})
    # Distinct operations on the same AX node need distinct targets within CLICK.
    for n, action in enumerate(actions):
        action["id"] = f"e{n + 1}"
        if action.get("native_action"):
            action["node"] = f"{action['node']}:{action['native_action']}"
    _, targets, _ = action_space(actions)
    if any(len(group) > 255 for group in targets.values()):
        raise MCPError("CU exceeds 255 targets for one operation. Narrow the window; nothing was truncated.")
    actions.append({"id": "wait", "kind": "wait", "label": "Wait for the window to update"})
    w, h = meta.get("imageSize", [window["frame"]["width"], window["frame"]["height"]])
    page = {
        "url": f"cu://{app}/windows/{window_id}", "title": window.get("title", app),
        "text": "\n".join(safe_lines), "w": w, "h": h, "actions": actions,
        "scroll": {"y": 0, "height": h}, "backend": "cu", "window_id": window_id,
        "observation_id": meta["observation_id"], "cu_meta": meta,
    }
    semantic = {k: page[k] for k in ("url", "title", "text", "actions", "w", "h")}
    semantic["window"] = window
    page["fingerprint"] = hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()
    for block in result.get("content", []):
        if block.get("type") == "image":
            page["screenshot"] = block["data"]
            page["screenshot_mime"] = block["mimeType"]
    return page


class Computer:
    def __init__(self, app, *, window_id=None, client=None):
        if not isinstance(app, str) or not app.strip():
            raise ValueError("Select an application first.")
        self.app = app
        self.client = client if client is not None else MCPClient()
        self.pending = None
        self.screenshots = False
        self.events = []
        try:
            for name, fields in {
                "get_app_state": {"app", "window_id", "include_screenshot"},
                "click": {"app", "window_id", "observation_id", "element_index"},
            }.items():
                schema = self.client.tools.get(name, {}).get("inputSchema", {}).get("properties", {})
                if not fields <= schema.keys():
                    raise MCPError(f"CU MCP schema for {name} is incompatible. Check its current public tools.")
            windows = json_content(self.client.call("list_windows", {"app": app}))["windows"]
            matches = [w for w in windows if window_id is None or w["window_id"] == window_id]
            if len(matches) != 1:
                raise ValueError("Select one visible window explicitly; open the app first if none are listed.")
            self.window_id = matches[0]["window_id"]
        except Exception:
            self.client.close()
            raise

    def _read(self, screenshot):
        result = self.client.call("get_app_state", {
            "app": self.app, "window_id": self.window_id, "include_screenshot": screenshot,
        })
        return read_page(result, self.app, self.window_id, self.client.tools)

    def observe(self, screenshot=True):
        self.screenshots = screenshot
        if self.pending is not None:
            result, self.pending = self.pending, None
            return read_page(result, self.app, self.window_id, self.client.tools)
        return self._read(screenshot)

    def fresh(self, page, action=None):
        current = self._read(self.screenshots)
        if current["fingerprint"] != page["fingerprint"]:
            return False
        # CU indexes are snapshot-scoped. Refresh the token only after identical full semantics.
        page.update(current)
        return True

    def act(self, action, page, text=None, before_dispatch=None):
        if not self.fresh(page, action):
            raise StalePage("CU window changed before input. Observe and choose again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
            return
        if action not in page["actions"]:
            raise ValueError("CU action was not present in the observed window.")
        tool = action["tool"]
        args = {"app": self.app, "window_id": self.window_id, "observation_id": page["observation_id"],
                "element_index": action["element_index"]}
        if tool == "set_value":
            args["value"] = text
        elif tool == "perform_secondary_action":
            args["action"] = action["native_action"]
        if before_dispatch:
            before_dispatch()
        event = {"tool": tool, "element_index": action["element_index"], "window_id": self.window_id,
                 "observation_id": page["observation_id"], "status": "dispatched"}
        self.events.append(event)
        try:
            result = self.client.call(tool, args)
        except MCPToolError as exc:
            status = exc.result.get("_meta", {}).get("status")
            event["status"] = status or "uncertain"
            # Only the broker's explicit pre-dispatch stale rejection is safe to re-observe.
            if status == "rejected" and "No unambiguous current observation" in str(exc):
                raise StalePage(str(exc)) from exc
            raise
        except MCPError:
            event["status"] = "uncertain"
            raise
        event["status"] = "returned"
        event["trace_id"] = result.get("_meta", {}).get("traceId")
        # CU owns action + post-observation atomically. Parse its observation only after Agent logs execution.
        self.pending = result if result.get("_meta", {}).get("observationIncluded") else None

    def close(self):
        self.client.close()
