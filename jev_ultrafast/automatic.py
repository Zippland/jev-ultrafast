"""Model-selected discovery: offer app/tab identities, then observe only the selected surface."""

import hashlib
import subprocess
import sys
import time

from .actions import OPERATIONS, observed_action
from .browser import StalePage, traced_cdp
from .browser_tools import BrowserTools as AttachedBrowser
from .browser_tools import destination_url
from .mcp_client import MCPClient
from .relay import RelayComputer
from .tracing import TracedMCP


def foreground_app():
    """Read macOS focus immediately after showing a tab; never manipulate UI.

    list_apps is intentionally cached and can predate the action. This small
    local sensor records the postcondition before a user switches away again.
    The script is constant and contains no model/user supplied values.
    """
    if sys.platform != "darwin":
        return None
    try:
        return subprocess.check_output(
            ["osascript", "-l", "JavaScript", "-e", 'ObjC.import("AppKit"); '
             'ObjC.unwrap($.NSWorkspace.sharedWorkspace.frontmostApplication.bundleIdentifier)'],
            text=True, timeout=2, stderr=subprocess.DEVNULL,
        ).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class AutomaticDesktop:
    def __init__(self, inventory, command, trace, *, excluded_origins=()):
        self.inventory, self.command, self.trace = inventory, command, trace
        self.bindings, self.adapters = {}, {}
        self.active = None
        self.focus_changed_at = None
        self.closed = False
        self.excluded_origins = tuple(excluded_origins)

    @staticmethod
    def key(identity):
        return "s" + hashlib.sha256(identity.encode()).hexdigest()[:12]

    def _adapter(self, key):
        if key not in self.adapters:
            binding = self.bindings[key]
            if "tab" in binding:
                adapter = AttachedBrowser(binding["tab"])
            else:
                client = TracedMCP(MCPClient(self.command, timeout=60), self.trace)
                try:
                    adapter = RelayComputer(binding["app"], expected_title=None, client=client, full_tools=True,
                                            keyboard_fill=True, app_selector=binding.get("app_path"),
                                            excluded_origins=self.excluded_origins)
                except Exception:
                    client.close()
                    raise
            self.adapters[key] = adapter
        return self.adapters[key]

    def observe(self):
        catalog = self.inventory()
        bindings = {}
        for app in catalog["apps"]:
            key = self.key("app:" + app["id"])
            if key in bindings:
                raise ValueError(f"Multiple running apps share bundle ID: {app['id']}")
            if key in self.bindings and self.bindings[key].get("app_path") != app.get("path"):
                adapter = self.adapters.pop(key, None)
                if adapter:
                    adapter.close()
            bindings[key] = {"name": app["name"], "app": app["id"], "app_path": app.get("path"), "channel": "CU",
                             "frontmost": app.get("frontmost")}
        for tab in catalog["tabs"]:
            key = self.key("tab:" + tab["id"])
            bindings[key] = {"name": f"Chrome 标签页：{tab['title'] or tab['url']}",
                             "tab": tab["id"], "url": tab["url"], "channel": "Browser Use"}
        # Creating a tab belongs to the connected browser, not to an existing
        # web page. It must remain available when Chrome shows our microphone UI
        # or has no task tab yet.
        browser_key = self.key("browser:chrome")
        if catalog.get("browser_connected"):
            bindings[browser_key] = {"name": "Google Chrome · 新标签页", "browser": True,
                                     "channel": "Browser Use"}
        if len(bindings) > 255:
            raise ValueError("应用与标签页超过 Jev 的 255 个选项上限，未静默截断")
        self.bindings = bindings
        if self.active not in bindings:
            self.active = None
        surfaces, actions = {}, []
        for key, binding in bindings.items():
            surface = f"auto:{key}"
            if "app" in binding:
                actions.append({"id": f"{surface}/activate", "kind": "activate_app",
                                "label": f"Show {binding['name']} in the macOS foreground",
                                "surface": surface, "app_key": key, "app_name": binding["name"],
                                "channel": "CU", "role": "app", "value": "", "native": {}})
            if binding.get("browser"):
                actions.append({"id": f"{surface}/new_tab", "kind": "new_tab",
                                "label": "Open a new background Chrome tab without replacing any existing page",
                                "surface": surface, "app_key": key, "app_name": binding["name"],
                                "channel": binding["channel"], "role": "browser", "value": "",
                                "native": {}, "browser_new_tab": True})
                continue
            if key != self.active:
                actions.append({"id": f"{surface}/inspect", "kind": "inspect", "label": binding["name"],
                                "surface": surface, "app_key": key, "app_name": binding["name"],
                                "channel": binding["channel"], "role": "app" if "app" in binding else "tab",
                                "value": binding.get("url", ""), "native": {}})
                if "tab" in binding:
                    actions.append({"id": f"{surface}/show", "kind": "activate_tab",
                                    "label": f"Show {binding['name']} in foreground Chrome",
                                    "surface": surface, "app_key": key, "app_name": binding["name"],
                                    "channel": binding["channel"], "role": "tab", "value": binding["url"],
                                    "native": {}, "show_observed_tab": binding["tab"]})
                continue
            adapter = self._adapter(key)
            page = adapter.observe(screenshot=False)
            surfaces[surface] = {**page, "channel": binding["channel"]}
            for native in page["actions"]:
                if page.get("agent_control_surface") and not native.get("control_surface_exit"):
                    continue
                if native["kind"] not in OPERATIONS:
                    continue
                if native.get("native_action") == "AXShowMenu" or native["label"] == "AXButton":
                    continue
                actions.append({**native, "id": f"{surface}/{native['id']}", "native": native,
                                "surface": surface, "app_key": key, "app_name": binding["name"],
                                "channel": binding["channel"]})
            if not page.get("agent_control_surface") and not any(a["kind"] == "wait" for a in page["actions"]):
                actions.append({"id": f"{surface}/wait", "kind": "wait", "label": "等待当前界面更新后重新观察",
                                "surface": surface, "app_key": key, "app_name": binding["name"],
                                "channel": binding["channel"], "role": "app", "value": "", "native": {},
                                "bridge_wait": True})
        focus_stale = (self.focus_changed_at is not None and
                       (catalog.get("apps_age_ms") is None or
                        time.monotonic() - catalog["apps_age_ms"] / 1000 < self.focus_changed_at))
        frontmost = foreground_app()
        focus_app = (next((app for app in catalog["apps"] if app["id"] == frontmost), {"id": frontmost})
                     if frontmost else None if focus_stale else next(
                         (app for app in catalog["apps"] if app.get("frontmost")), None))
        if focus_app:
            focus_app = {**focus_app, "frontmost": True}
        return {"surfaces": surfaces, "actions": actions,
                "tool_availability": {
                    "browser_connected": catalog.get("browser_connected"),
                    "execution_mode": catalog.get("execution_mode", "mixed"),
                    "discovery_errors": catalog.get("errors", []),
                    "protected_surfaces": [key for key, page in surfaces.items()
                                           if page.get("agent_control_surface")],
                    "protected_surface_policy": "Agent microphone/control pages cannot be modified. "
                                                "Only currently offered targets can be operated.",
                },
                "desktop_focus": {"frontmost_app": focus_app,
                                  "source": "NSWorkspace" if frontmost else "cached_app_inventory",
                                  "age_ms": 0 if frontmost else catalog.get("apps_age_ms"),
                                  "predates_last_foreground_action": focus_stale and not frontmost,
                                  "tab_visibility": "Reading a browser tab does not show it. "
                                                    "Its foreground visibility is not verified by its page content."},
                "current_context": ({"id": self.active, **bindings[self.active]} if self.active else None)}

    def act(self, action, observation, text=None, before_dispatch=None):
        observed_action(action, observation["actions"])
        key = action["app_key"]
        verification = {}
        if action["kind"] == "inspect":
            if before_dispatch:
                before_dispatch()
            self._adapter(key)
            self.active = key
        elif action["kind"] == "activate_app":
            app = self.bindings[key]["app"]
            if before_dispatch:
                before_dispatch()
            app_path = self.bindings[key].get("app_path")
            command = ["/usr/bin/open", "-a", app_path] if app_path else ["/usr/bin/open", "-b", app]
            subprocess.run(command, check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.active = key
            self.focus_changed_at = time.monotonic()
            verification["foreground_after_action"] = {
                "app": foreground_app(), "requested_app": app,
                "scope": "Immediate observed foreground; returned command alone is not proof of focus.",
            }
        elif action.get("show_observed_tab"):
            target = action["show_observed_tab"]
            info = traced_cdp("Target.getTargetInfo", targetId=target)["targetInfo"]
            if info["url"] != action["value"]:
                raise StalePage("Observed tab navigated before foregrounding; choose from the new inventory")
            if before_dispatch:
                before_dispatch()
            traced_cdp("Target.activateTarget", targetId=target)
            self.focus_changed_at = time.monotonic()
            verification["foreground_after_action"] = {
                "app": foreground_app(), "target": target, "url": info["url"],
                "scope": "Immediate post-action observation; not a promise to retain focus.",
            }
            if key not in self.adapters:
                self.adapters[key] = AttachedBrowser(target)
            self.active = key
        elif action.get("browser_new_tab"):
            url = destination_url(text, blank=True)
            if before_dispatch:
                before_dispatch()
            target = traced_cdp("Target.createTarget", url=url, background=True)["targetId"]
            new_key = self.key("tab:" + target)
            self.adapters[new_key] = AttachedBrowser(target)
            self.active = new_key
        elif action.get("bridge_wait"):
            if before_dispatch:
                before_dispatch()
            time.sleep(0.2)
        else:
            native = {**action["native"], **({"parameters": action["parameters"]} if "parameters" in action else {})}
            self.adapters[key].act(native, observation["surfaces"][action["surface"]],
                                   text=text, before_dispatch=before_dispatch)
            if action["kind"] == "activate_tab":
                # Do not feed a pre-activation inventory sample back as evidence
                # that the foreground action failed and needs repeating.
                self.focus_changed_at = time.monotonic()
                verification["foreground_after_action"] = {
                    "app": foreground_app(), "target": self.adapters[key].target,
                    "url": observation["surfaces"][action["surface"]].get("url"),
                    "scope": "Immediate post-action observation; not a promise to retain focus.",
                }
            adapter = self.adapters[key]
            if getattr(adapter, "closed_by_action", False):
                self.adapters.pop(key)
                self.active = None
            elif getattr(adapter, "created_target", None):
                target, adapter.created_target = adapter.created_target, None
                new_key = self.key("tab:" + target)
                self.adapters[new_key] = AttachedBrowser(target)
                self.active = new_key
        if action["kind"] == "new_tab":
            verification["opened_in_background"] = True
        return {"app": action["app_name"], "channel": action["channel"], "label": action["label"],
                "kind": action["kind"], "parameters": action.get("parameters", {}),
                "text": text, "status": "returned", **verification}

    def close(self):
        self.closed = True
        for adapter in self.adapters.values():
            try:
                adapter.close()
            except Exception:
                pass
