"""Read-only diagnostics derived from trace events, never an execution policy."""

import copy
from collections import Counter, defaultdict, deque
from statistics import median


class Telemetry:
    def __init__(self):
        self.counts = Counter()
        self.latencies = defaultdict(lambda: deque(maxlen=1024))
        self.spans = {}
        self.native_reads = set()
        self.decisions = deque(maxlen=12)
        self.last_text = self.last_pending = self.last_asr = None
        self.last_execution = None
        self.input_at = {}
        self.input_tokens = self.output_tokens = self.cost_samples = self.usage_samples = 0
        self.reported_cost = 0
        self.observation_at = self.dispatch_at = None

    def add(self, event):
        kind, now = event["type"], event["ms"]
        self.counts[kind] += 1
        latency = event.get("latency_ms")
        if kind in {"action.dispatch", "action.returned", "action.uncertain", "action.not_dispatched"}:
            action = event.get("action") or event
            self.last_execution = {"status": kind.removeprefix("action."), "ms": now,
                                   "version": event.get("version"), "label": action.get("label"),
                                   "error": event.get("error")}
        if latency is not None:
            self.latencies[kind].append(latency)
        if kind == "input":
            self.input_at[event["version"]] = now
        elif kind == "observation.start":
            self.observation_at = now
        elif kind == "observation.end" and self.observation_at is not None:
            self.latencies["observe"].append(now - self.observation_at)
        elif kind == "action.dispatch":
            self.dispatch_at = now
            if event["version"] in self.input_at:
                self.latencies["input_to_dispatch"].append(now - self.input_at[event["version"]])
        elif kind in {"action.returned", "action.uncertain"} and self.dispatch_at is not None:
            self.latencies["execute"].append(now - self.dispatch_at)
            if event["version"] in self.input_at:
                self.latencies["input_to_return"].append(now - self.input_at[event["version"]])
            self.dispatch_at = None
        elif kind == "mcp.start" and event.get("request", {}).get("tool") == "get_app_state":
            self.native_reads.add(event["span"])
        elif kind in {"mcp.end", "mcp.error"}:
            if event["span"] in self.native_reads:
                self.native_reads.remove(event["span"])
                if latency is not None:
                    self.latencies["native_state"].append(latency)
        elif kind == "model.start":
            self.spans[event["span"]] = event["request"]["url"]
        elif kind in {"model.end", "model.error"}:
            url = self.spans.pop(event["span"], "")
            self.latencies["jev" if "/systemone" in url else "text_model"].append(latency)
            if kind == "model.end":
                usage = event["result"].get("usage", {})
                self.input_tokens += usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
                self.output_tokens += usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
                self.usage_samples += 1
                if isinstance(usage.get("cost"), (int, float)):
                    self.reported_cost += usage["cost"]
                    self.cost_samples += 1
        elif kind == "choice" and "response" in event:
            answers, questions = event["response"]["answers"], event["request"]["questions"]
            heads = event["selected_heads"]
            charts = []
            for index, head in enumerate(heads):
                title = "操作" if index == 0 else "目标 / 应用 / 工具" if index == 1 else "参数 · " + head
                answer = answers[head]
                options = questions[head]["criteria"]
                entries = []
                for key, probability in sorted(answer["probabilities"].items(), key=lambda x: -x[1])[:6]:
                    label = options[key]
                    if head == "operation":
                        label = "继续聆听" if key == "LISTEN" else key
                    elif isinstance(label, dict):
                        label = f"{label.get('app', '')} · {label.get('channel', '')} · {label.get('element', key)}"
                    entries.append({"id": key, "label": label, "probability": probability,
                                    "selected": key == answer["choice"]})
                charts.append({"title": title, "confidence": answer["confidence"], "entries": entries})
            self.decisions.append({"seq": event["seq"], "version": event["version"], "ms": now,
                                   "operation": event["operation"], "latency_ms": latency,
                                   "charts": charts, "action": event.get("action"),
                                   "outcome_status": (event.get("outcome_review") or {}).get("status"),
                                   "input_preview": [
                                       {"version": s.get("version"), "final": s.get("final"),
                                        "text": s.get("text", "")[-1000:], "truncated": len(s.get("text", "")) > 1000}
                                       for s in event["request"].get("state", {}).get("speech", [])[-3:]]})
        elif kind == "text":
            self.last_text = {"value": event["value"], "version": event["version"], "latency_ms": latency}
        elif kind.startswith("pending") and "valid" in event:
            answer = event.get("response", {}).get("answers", {}).get("pending", {})
            self.last_pending = {"valid": event["valid"], "version": event["version"],
                                 "latency_ms": latency, "probabilities": answer.get("probabilities", {}),
                                 "text_review": event.get("text_review")}
        elif kind in {"asr.partial", "asr.segment_final"}:
            self.last_asr = {k: event.get(k) for k in ("text", "stable_text", "volatile_text", "rollback_chars",
                                                     "revision", "base_revision", "segment_id", "latency_ms")}

    def snapshot(self):
        latency = {key: {"last": values[-1], "p50": round(median(values)), "samples": len(values)}
                   for key, values in self.latencies.items() if values and all(v is not None for v in values)}
        return {"counts": dict(self.counts), "latency": latency, "decisions": copy.deepcopy(list(self.decisions)),
                "last_text": self.last_text, "last_pending": self.last_pending, "last_asr": self.last_asr,
                "last_execution": copy.deepcopy(self.last_execution),
                "tokens": {"input": self.input_tokens, "output": self.output_tokens},
                "cost": {"reported_usd": self.reported_cost, "reported_calls": self.cost_samples,
                         "completed_calls": self.usage_samples}}
