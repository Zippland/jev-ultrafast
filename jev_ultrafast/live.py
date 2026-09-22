"""One serial actor consumes a revisable input ledger; models decide all user intent."""

import copy
import threading
import time

from .actions import TEXT_ARGUMENTS, observed_action
from .browser import DispatchCancelled, StalePage
from .mixed import choose_mixed, mixed_field_text, pending_valid
from .model import MissingTextArgument
from .tracing import CURRENT_TRACE


class RewritePendingText(DispatchCancelled):
    def __init__(self, version):
        super().__init__("文字已过时，目标仍有效")
        self.version = version


def visible_state(observation):
    """Comparable observed content, excluding transport leases and screenshot paths."""
    return {
        "context": observation.get("current_context"),
        "pages": {key: {k: page.get(k) for k in ("title", "url", "text", "focused_index", "selected_text")}
                  for key, page in observation["surfaces"].items()},
        "targets": [(a.get("id"), a.get("kind"), a.get("label"), a.get("value"))
                    for a in observation["actions"]],
    }


def field_readback(action, text, observation):
    """Literal comparison with an unambiguous observed field, never a success verdict."""
    if action.get("kind") != "fill":
        return None
    matches = [candidate for candidate in observation["actions"]
               if candidate.get("kind") == "fill"
               and all(candidate.get(key) == action.get(key) for key in ("surface", "role", "label"))]
    result = {"requested": text, "comparison": "unavailable",
              "meaning": "Literal field comparison only; not proof of submission or task success"}
    if len(matches) == 1 and "value" in matches[0]:
        result.update(observed=matches[0]["value"],
                      comparison="equal" if matches[0]["value"] == text else "different",
                      matching_basis="unique field on same surface with same role and label",
                      selected_text=observation["surfaces"].get(action.get("surface"), {}).get("selected_text"))
    return result


class LiveSession:
    def __init__(self, desktop, trace, *, choose=choose_mixed, generate=mixed_field_text,
                 validate=pending_valid, heartbeat_timeout=20):
        self.desktop, self.trace = desktop, trace
        self.choose, self.generate, self.validate = choose, generate, validate
        self.condition = threading.Condition(threading.RLock())
        self.segments = {}
        self.version = self.epoch = self.wakeup = 0
        self.enabled = True
        self.closed = False
        self.uncertain = False
        self.phase, self.error = "observing", None
        self.history, self.observation = [], None
        self.heartbeat_timeout = heartbeat_timeout
        self.last_seen = time.monotonic()
        self.worker = threading.Thread(target=self._run, name="jev-live", daemon=True)
        self.worker.start()

    def touch(self):
        with self.condition:
            self.last_seen = time.monotonic()

    def refresh(self):
        """A recovered tool can unblock existing speech without fabricating new input."""
        with self.condition:
            if self.enabled and not self.closed:
                self.wakeup += 1
                self.condition.notify_all()

    def input(self, key, text, *, final=False, source="text"):
        if not isinstance(text, str) or len(text) > 16000:
            raise ValueError("单段输入超过 16000 字，请开始新会话")
        with self.condition:
            if self.closed:
                raise ValueError("会话已关闭")
            previous = self.segments.get(key)
            if previous is None and not text.strip():
                return self.version
            if previous and previous["text"] == text and previous["final"] == final:
                return self.version
            if previous and previous["final"]:
                raise ValueError("已定稿的语音段不能回写；请追加新指令")
            size = sum(len(s["text"]) for k, s in self.segments.items() if k != key) + len(text)
            if size > 48000 or (key not in self.segments and len(self.segments) >= 256):
                raise ValueError("当前会话输入已满，请断开并开始新会话")
            self.version += 1
            self.segments[key] = {"id": key, "text": text, "final": final, "source": source,
                                  "version": self.version}
            self.trace.emit("input", segment=copy.deepcopy(self.segments[key]), replaced=previous,
                            version=self.version)
            self.wakeup += 1
            self.condition.notify_all()
            return self.version

    def pause(self, reason="用户暂停"):
        with self.condition:
            if self.closed:
                return
            self.enabled = False
            self.epoch += 1
            self.phase = "paused"
            self.trace.emit("pause", reason=reason)
            self.condition.notify_all()

    def fail(self, error):
        with self.condition:
            self.pause(str(error))
            self.error = str(error)
            self.trace.emit("session.error", error=str(error))

    def resume(self):
        with self.condition:
            if self.closed:
                raise ValueError("会话已关闭")
            if self.uncertain:
                raise ValueError("上次操作结果不确定，请检查真实应用后断开并开始新会话")
            self.last_seen = time.monotonic()
            self.enabled, self.error = True, None
            self.epoch += 1
            self.wakeup += 1
            self.phase = "observing"
            self.trace.emit("resume")
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            pages = self.observation["surfaces"] if self.observation else {}
            return {"phase": self.phase, "enabled": self.enabled, "closed": self.closed,
                    "error": self.error, "version": self.version, "uncertain": self.uncertain,
                    "tool_availability": copy.deepcopy((self.observation or {}).get("tool_availability")),
                    "segments": copy.deepcopy(list(self.segments.values())),
                    "history": copy.deepcopy(self.history[-30:]),
                    "observations": {key: {"title": page["title"], "text": page["text"]}
                                     for key, page in pages.items()},
                    "trace_path": str(self.trace.path), "events": self.trace.snapshot(),
                    "dashboard": self.trace.dashboard()}

    def close(self):
        self.pause("断开会话")
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        # The actor releases its own transports after any in-flight RPC returns.
        # Closing a transport underneath a mutation would hide its outcome.

    def _check(self, epoch, version=None):
        if time.monotonic() - self.last_seen > self.heartbeat_timeout:
            self.pause("工作台连接中断，执行已暂停")
        if self.closed or not self.enabled or epoch != self.epoch:
            raise DispatchCancelled("会话已暂停或结束，未派发操作")
        if version is not None and version != self.version:
            raise DispatchCancelled("输入已更新，未派发旧操作")

    def _input_state(self, epoch):
        with self.condition:
            self._check(epoch)
            return self.version, copy.deepcopy(list(self.segments.values()))

    def _phase(self, value):
        with self.condition:
            if self.enabled and not self.closed:
                self.phase = value

    def _observe(self):
        self._phase("observing")
        self.trace.emit("observation.start")
        observation = self.desktop.observe()
        self.trace.emit("observation.end", pages=observation["surfaces"],
                        tool_availability=observation.get("tool_availability"),
                        targets=len(observation["actions"]))
        with self.condition:
            self.observation = observation
        return observation

    def _burst(self, epoch):
        observation = self._observe()
        rewrite = None
        # Each iteration checks the latest input and cancellation epoch. A task
        # ends at LISTEN, pause or failure, not an arbitrary number of decisions.
        while True:
            version, speech = self._input_state(epoch)
            if not any(s["text"].strip() for s in speech):
                with self.condition:
                    return self.wakeup if self.version == version else -1
            action = None
            if rewrite is not None and rewrite[1] == version:
                try:
                    observed_action(rewrite[0], observation["actions"])
                    action = rewrite[0]
                except ValueError:
                    pass  # The target changed; select again from the new observation.
            rewrite = None
            if action is None:
                self._phase("choosing")
                action, details = self.choose(observation, self.desktop.bindings, speech, self.history)
                self.trace.emit("choice", version=version, action=action, **details)
            else:
                self.trace.emit("text.rewrite_target_reused", version=version, action=action)
            self._input_state(epoch)
            if action is None:
                with self.condition:
                    if self.version != version:
                        continue
                    blocked = details.get("operation") == "BLOCKED"
                    self._phase("blocked" if blocked else "listening")
                    self.trace.emit("blocked" if blocked else "listen", version=version)
                    return self.wakeup
            if action["kind"] == "inspect":
                # Reading a previously selected surface is useful speculation, not an app mutation.
                # New speech can choose another surface next; it must not starve all observations.
                def before_read():
                    with self.condition:
                        self._check(epoch)
                        self.trace.emit("route.observe", version=version, action=action)

                self._phase("observing")
                event = self.desktop.act(action, observation, before_dispatch=before_read)
                with self.condition:
                    self.history.append({**event, "version": version})
                self.trace.emit("route.returned", **event, version=version)
                observation = self._observe()
                continue
            text = None
            # Generate for the selected field using speech available now. Keep
            # the choice's version: updated wording must still validate this
            # exact target/value before dispatch, even if generation used it.
            if action["kind"] in TEXT_ARGUMENTS:
                text_version, text_speech = self._input_state(epoch)
                self._phase("writing")
                try:
                    text, details = self.generate(action, observation, self.desktop.bindings, text_speech, self.history)
                except MissingTextArgument:
                    with self.condition:
                        self._check(epoch)
                        revised = self.version != text_version
                        self.trace.emit("text.unavailable", version=text_version, action=action,
                                        revised=revised, reason="文本模型未提供可用参数，本次动作未执行")
                        if not revised:
                            self._phase("waiting_for_text")
                            return self.wakeup
                    observation = self._observe()
                    continue
                self.trace.emit("text", version=text_version, choice_version=version, value=text, **details)
            self._input_state(epoch)
            dispatched = False

            def before_dispatch():
                nonlocal version, dispatched
                # Validate once at the last boundary after all read-only preparation.
                # An earlier gate would be obsolete after the native freshness read.
                for _ in range(3):
                    newest, newest_speech = self._input_state(epoch)
                    if newest != version:
                        self._phase("validating")
                        valid, gate = self.validate(action, text, observation, self.desktop.bindings,
                                                    newest_speech, self.history)
                        self.trace.emit("pending.dispatch", version=newest, valid=valid, **gate)
                        if not valid:
                            if gate.get("text_review") == "REWRITE":
                                raise RewritePendingText(newest)
                            raise DispatchCancelled("新输入使待执行动作失效")
                        version = newest
                    with self.condition:
                        self._check(epoch)
                        if version == self.version:
                            self.trace.emit("action.dispatch", version=version, action=action, text=text)
                            dispatched = True
                            return
                raise DispatchCancelled("输入仍在更新，请重新选择")

            self._phase("executing")
            try:
                event = self.desktop.act(action, observation, text=text, before_dispatch=before_dispatch)
            except DispatchCancelled as exc:
                self.trace.emit("action.cancelled", version=version)
                self._input_state(epoch)
                previous_state = visible_state(observation)
                observation = self._observe()
                if isinstance(exc, RewritePendingText) and visible_state(observation) == previous_state:
                    rewrite = (action, exc.version)
                continue
            except StalePage as exc:
                self.trace.emit("action.stale", reason=str(exc))
                observation = self._observe()
                continue
            except Exception as exc:
                if not dispatched:
                    # Freshness reads, parameter checks and speech validation
                    # can fail before any mutation. Stop, but do not claim the
                    # attempted action executed or poison subsequent history.
                    self.trace.emit("action.not_dispatched", version=version, action=action,
                                    error=f"{type(exc).__name__}: {exc}")
                    raise
                # Never replay a mutation whose result is uncertain, even on explicit resume.
                uncertain = {"app": action["app_name"], "channel": action["channel"],
                             "label": action["label"], "kind": action["kind"], "text": text,
                             "parameters": action.get("parameters", {}),
                             "status": "uncertain", "version": version}
                with self.condition:
                    self.history.append(uncertain)
                    self.uncertain = action["kind"] != "inspect"
                self.trace.emit("action.uncertain", **uncertain)
                raise
            history_entry = {**event, "version": version}
            with self.condition:
                self.history.append(history_entry)
            self.trace.emit("action.returned", **event, version=version)
            # Observe the returned native tree or read the live page. Final success
            # still needs an independent outcome check, not just an RPC return/LISTEN.
            previous_state = visible_state(observation)
            observation = self._observe()
            changed = previous_state != visible_state(observation)
            readback = field_readback(action, text, observation)
            with self.condition:
                history_entry["observed_change"] = changed
                if readback is not None:
                    history_entry["field_readback"] = readback
            self.trace.emit("action.observed", version=version, observed_change=changed,
                            field_readback=readback, action=action,
                            meaning="Visible change only; not a task-success verdict")

    def _run(self):
        token = CURRENT_TRACE.set(self.trace)
        consumed = -1
        try:
            while True:
                with self.condition:
                    while not self.closed and (not self.enabled or consumed == self.wakeup):
                        self.condition.wait(timeout=1)
                        if self.enabled and time.monotonic() - self.last_seen > self.heartbeat_timeout:
                            self.pause("工作台连接中断，执行已暂停")
                    if self.closed:
                        break
                    consumed, epoch = self.wakeup, self.epoch
                try:
                    acknowledged = self._burst(epoch)
                    with self.condition:
                        if acknowledged is not None:
                            consumed = max(consumed, acknowledged)
                        # New input arriving after LISTEN is still a wakeup, not silently consumed.
                        if self.observation and self.enabled and self.phase == "observing":
                            self.phase = "listening"
                except DispatchCancelled:
                    pass
                except Exception as exc:
                    self.fail(f"{type(exc).__name__}: {exc}")
        finally:
            try:
                self.desktop.close()
            finally:
                self.trace.emit("session.closed")
                CURRENT_TRACE.reset(token)
