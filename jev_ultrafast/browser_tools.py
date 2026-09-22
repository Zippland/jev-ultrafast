"""Browser interaction tools grounded in the observed tab and code-owned DOM references."""

import base64
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .actions import KEYS, PAGES, observed_action, parameter_values
from .browser import StalePage, traced_cdp
from .mixed import AttachedBrowser
from .tracing import CURRENT_TRACE

EXTRA_STATE = """(() => {
  const c=window.__jevFast, out=[];
  if (!c) return out;
  for (const e of document.querySelectorAll('input[type=file],body *')) {
    const r=e.getBoundingClientRect();
    if (!r.width || !r.height || r.bottom<=0 || r.top>=innerHeight || r.right<=0 || r.left>=innerWidth ||
        !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) ||
        e.closest('[inert],[aria-hidden=true],[aria-disabled=true]') || e.matches(':disabled')) continue;
    const file=e.matches('input[type=file]');
    const css=getComputedStyle(e);
    const scrolling=(e.scrollHeight>e.clientHeight+2 && ['auto','scroll'].includes(css.overflowY)) ||
      (e.scrollWidth>e.clientWidth+2 && ['auto','scroll'].includes(css.overflowX));
    if (!file && !scrolling) continue;
    if (!c.ids.has(e)) c.ids.set(e,c.next++);
    const node=c.ids.get(e); c.nodes.set(node,e);
    const label=e.getAttribute('aria-label') || [...(e.labels||[])].map(l=>l.innerText).join(' ') ||
      e.getAttribute('title') || (file ? 'File upload' : e.innerText.slice(0,120)) || 'Scroll container';
    out.push({id:'extra-'+node, node, kind:file?'upload':'scroll', role:file?'file':'scrollarea', label,
      value:file?[...e.files].map(f=>f.name).join(', '):'', guard:c.guard(e)});
  }
  return out;
})()"""


def destination_url(value, *, blank=False):
    if blank and value == "about:blank":
        return value
    if not isinstance(value, str):
        raise ValueError("Missing URL")
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("Navigation needs a complete HTTP(S) URL without embedded credentials")
    return value


class BrowserTools(AttachedBrowser):
    def __init__(self, target_id):
        super().__init__(target_id)
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.closed_by_action = False
        self.created_target = None

    def observe(self, screenshot=False):
        page = super().observe(screenshot=screenshot)
        if page.get("omitted_actions", 0):
            raise ValueError("Page observation omitted controls; refusing a silently truncated tool menu")
        extra = self.evaluate(EXTRA_STATE)
        for action in extra:
            page["guards"][str(action["node"])] = action.pop("guard")
        for action in list(page["actions"]):
            if action["kind"] == "fill":
                extra.append({**action, "id": "insert-" + action["id"], "kind": "insert"})
                if action.get("value"):
                    extra.append({**action, "id": "text-" + action["id"], "kind": "select_text"})
        for kind, label in {
            "key": "Current keyboard focus", "navigate": "Open URL in this tab", "new_tab": "Open a new tab",
            "close_tab": "Close this tab", "activate_tab": "Show this tab in Chrome", "reload": "Reload this tab",
        }.items():
            extra.append({"id": "tab-" + kind, "kind": kind, "role": "tab", "label": label, "value": page["url"]})
        history = self.call("Page.getNavigationHistory")
        entries, index = history["entries"], history["currentIndex"]
        for kind, offset in (("back", -1), ("forward", 1)):
            if 0 <= index + offset < len(entries):
                entry = entries[index + offset]
                extra.append({"id": "tab-" + kind, "kind": kind, "role": "history", "entry_id": entry["id"],
                              "label": entry.get("title") or entry["url"], "value": entry["url"]})
        if CURRENT_TRACE.get():
            extra.append({"id": "tab-screenshot", "kind": "screenshot", "role": "tab", "label": "Save screenshot"})
        page["actions"].extend(extra)
        return page

    def _node(self, action, page, *, object_id=False):
        node = action["node"]
        if type(node) is not int:
            raise ValueError("Target must be an observed node")
        current = self.evaluate(f"window.__jevFast.guard(window.__jevFast.nodes.get({node}))")
        if current != page["guards"].get(str(node)):
            raise StalePage("Observed node changed before tool execution")
        expression = f"window.__jevFast.nodes.get({node})"
        if object_id:
            result = self.call("Runtime.evaluate", expression=expression, returnByValue=False)
            return result["result"]["objectId"]
        return expression

    def act(self, action, page, text=None, before_dispatch=None):
        observed_action(action, page["actions"])
        kind, params = action["kind"], parameter_values(action)
        if kind in {"click", "fill", "select"} or (kind == "scroll" and "delta" in action):
            return super().act(action, page, text=text, before_dispatch=before_dispatch)
        if not self.fresh(page):
            raise StalePage("Tab changed before tool execution")
        guard = before_dispatch or (lambda: None)
        node = self._node(action, page) if "node" in action else None
        if kind in {"navigate", "new_tab"}:
            text = destination_url(text, blank=kind == "new_tab")
        if kind == "upload":
            path = Path(text or "")
            if not path.is_absolute() or not path.is_file():
                raise ValueError("Upload requires an explicit existing absolute file path")
            obj = self._node(action, page, object_id=True)
            try:
                node_id = self.call("DOM.describeNode", objectId=obj)["node"]["backendNodeId"]
            finally:
                self.call("Runtime.releaseObject", objectId=obj)
        if kind == "select_text" and (not text or action["value"].count(text) != 1):
            raise ValueError("Text must uniquely match the observed field value")
        guard()
        if kind == "navigate":
            result = self.call("Page.navigate", url=text)
            if result.get("errorText"):
                raise RuntimeError(result["errorText"])
        elif kind == "new_tab":
            result = traced_cdp("Target.createTarget", url=text, background=True)
            self.created_target = result["targetId"]
        elif kind == "close_tab":
            if not traced_cdp("Target.closeTarget", targetId=self.target).get("success"):
                raise RuntimeError("Tab close was not confirmed")
            self.closed_by_action, self.session = True, None
        elif kind == "activate_tab":
            traced_cdp("Target.activateTarget", targetId=self.target)
        elif kind == "reload":
            self.call("Page.reload")
        elif kind in {"back", "forward"}:
            self.call("Page.navigateToHistoryEntry", entryId=action["entry_id"])
        elif kind == "insert":
            self.evaluate(f"{node}.focus()")
            self.call("Input.insertText", text=text)
        elif kind == "select_text":
            result = self.evaluate("""((e, text, mode) => {
              const value='value' in e ? e.value : e.textContent;
              const start=value.indexOf(text), end=start+text.length;
              if (start<0 || value.indexOf(text,start+1)>=0) return false;
              const a=mode==='cursor_after'?end:start, b=mode==='cursor_before'?start:end;
              e.focus();
              if (typeof e.setSelectionRange==='function') e.setSelectionRange(a,b);
              else {
                const walker=document.createTreeWalker(e,NodeFilter.SHOW_TEXT), range=document.createRange();
                let n, offset=0, found=false;
                while ((n=walker.nextNode())) {
                  const next=offset+n.textContent.length;
                  if (!found && a<=next) {range.setStart(n,a-offset); found=true;}
                  if (found && b<=next) {range.setEnd(n,b-offset); break;}
                  offset=next;
                }
                if (!found || !n) return false;
                const selection=getSelection(); selection.removeAllRanges(); selection.addRange(range);
              }
              return true;
            })(""" + node + "," + json.dumps(text) + "," + json.dumps(params["selection"]) + ")")
            if not result:
                raise RuntimeError("Selection was not confirmed; inspect before continuing")
        elif kind == "key":
            _, key, modifiers = KEYS[params["key"]]
            code = {"Enter": 13, "Tab": 9, "Escape": 27, "Backspace": 8, "Delete": 46,
                    "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
                    "Home": 36, "End": 35, "PageUp": 33, "PageDown": 34}.get(key)
            code = code if code is not None else ord(key.upper())
            args = {"key": key, "modifiers": modifiers, "windowsVirtualKeyCode": code}
            if len(key) == 1 and key.isalpha():
                args["code"] = "Key" + key.upper()
            command = {"SELECT_ALL": "selectAll", "COPY": "copy", "CUT": "cut", "PASTE": "paste",
                       "UNDO": "undo", "REDO": "redo"}.get(params["key"])
            self.call("Input.dispatchKeyEvent", type="rawKeyDown", **args,
                      **({"commands": [command]} if command else {}))
            if not modifiers and key in {"Enter", " "}:
                self.call("Input.dispatchKeyEvent", type="char", text="\r" if key == "Enter" else " ", **args)
            self.call("Input.dispatchKeyEvent", type="keyUp", **args)
        elif kind == "scroll":
            axis = "Top" if params["direction"] in {"up", "down"} else "Left"
            dimension = "Height" if axis == "Top" else "Width"
            scale = PAGES[params["amount"]] * (-1 if params["direction"] in {"up", "left"} else 1)
            self.evaluate(f"((e)=>{{e.scroll{axis}+=e.client{dimension}*{scale}}})({node})")
        elif kind == "upload":
            self.call("DOM.setFileInputFiles", files=[str(path)], backendNodeId=node_id)
        elif kind == "screenshot":
            trace = CURRENT_TRACE.get()
            if trace is None:
                raise ValueError("Screenshot needs a session output folder")
            result = self.call("Page.captureScreenshot", format="png")
            output = trace.folder / ("screenshot-" + uuid.uuid4().hex[:8] + ".png")
            output.write_bytes(base64.b64decode(result["data"]))
            trace.emit("screenshot.saved", path=str(output))
        elif kind == "wait":
            time.sleep(0.15)
        else:
            raise ValueError("Unsupported browser tool")
        return {"executed": action["id"]}
