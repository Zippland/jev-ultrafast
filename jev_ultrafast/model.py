"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import atexit
import json
import math
import os
import time
from urllib.parse import urlsplit

import httpx

from .http_client import ModelHTTPClient
from .questions import NEXT_ACTION, TARGET, TEXT_ARGUMENT, TEXT_VALUE
from .tracing import CURRENT_TRACE, traced_call

# Happy Eyeballs avoids waiting for each failed address in sequence. Allow
# temporary VPN/TLS delays without restoring the old per-address 25 s stall.
CLIENT = ModelHTTPClient(http2=True, timeout=httpx.Timeout(25, connect=10),
                         limits=httpx.Limits(keepalive_expiry=60))
atexit.register(CLIENT.close)


def post_json(url, key, body):
    # Only model payloads are recorded. Authorization headers/keys are never passed to the trace.
    return traced_call(CURRENT_TRACE.get(), "model", {"url": url, "body": body},
                       lambda: _post_json(url, key, body))


def _post_json(url, key, body):
    for attempt in range(3):
        trace = CURRENT_TRACE.get()
        started = time.monotonic()
        if trace:
            trace.emit("model.http.start", url=url, attempt=attempt + 1)
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            if trace:
                trace.emit("model.http.error", url=url, attempt=attempt + 1, error="connection failed",
                           error_type=type(exc).__name__,
                           latency_ms=round((time.monotonic() - started) * 1000))
            if attempt < 2:
                time.sleep(0.5 * 2**attempt)
                continue
            host = urlsplit(url).hostname or "模型服务"
            provider = {"api.typesafe.ai": "Jev", "openrouter.ai": "OpenRouter 文本模型"}.get(host, host)
            reason = ("连接超时" if isinstance(exc, httpx.ConnectTimeout) else
                      "等待响应超时" if isinstance(exc, httpx.ReadTimeout) else "网络请求失败")
            raise RuntimeError(f"{provider} {reason}（{type(exc).__name__}，已尝试 {attempt + 1} 次）。"
                               "后续操作已暂停；此前已执行的操作不会撤销。") from None
        if trace:
            trace.emit("model.http.end", url=url, attempt=attempt + 1, status=response.status_code,
                       latency_ms=round((time.monotonic() - started) * 1000))
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


class InvalidChoice(ValueError):
    """A consumed model answer violates the finite-choice response contract."""


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise InvalidChoice("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


OPERATION_LABELS = {
    "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
    "TYPE_TEXT": "Enter or replace text in an editable field. A text LLM will supply the value from the goal.",
    "SELECT": "Select an observed dropdown value.",
}


def operation_questions(operations, targets, goal, *, rules=NEXT_ACTION, grouped=False):
    """The same operation + compatible-target fan-out for fixed goals and live speech."""
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": rules}}
    }
    for operation, candidates in targets.items():
        if len(candidates) > 255 and (not grouped or len(candidates) > 255 * 255):
            raise ValueError(f"{operation} has more than 255 observed targets; no silent truncation")
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded", "help", "placeholder") if k in a},
                    **({"app": a["app_name"], "channel": a["channel"]} if "app_name" in a else {}),
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [rules, TARGET]},
        }
    if grouped:
        for operation, candidates in targets.items():
            if len(candidates) <= 255:
                continue
            head = operation.lower() + "_target"
            original = questions[head]
            entries = list(original["criteria"].items())
            groups = {}
            for start in range(0, len(entries), 255):
                group = str(start // 255 + 1)
                criteria = dict(entries[start:start + 255])
                groups[group] = criteria
                questions[f"{head}_group_{group}"] = {**original, "criteria": criteria}
            questions[head] = {"type": "choice", "criteria": groups, "instructions": {
                "goal": goal, "operation": operation,
                "rules": rules,
                "question": "Choose the group containing the best next target for this operation. "
                            "Each group lists its observed targets. Group membership is only an index; "
                            "it is not a preference or a sequence of actions."}}
    return questions


def selected_target_head(answers, questions, operation):
    head = operation.lower() + "_target"
    if head not in questions:
        return None
    if f"{head}_group_1" in questions:
        group = validate_choice(answers.get(head, {}), questions[head]["criteria"])
        return f"{head}_group_{group['choice']}"
    return head


def selected_answers(answers, questions):
    """Only the chosen operation's target can affect execution."""
    operation = validate_choice(answers.get("operation", {}), questions["operation"]["criteria"])
    head = selected_target_head(answers, questions, operation["choice"])
    target = validate_choice(answers.get(head, {}), questions[head]["criteria"]) if head in questions else None
    return operation, target


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    operations = {key: OPERATION_LABELS[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = operation_questions(operations, targets, goal)
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    operation_answer, target_answer = selected_answers(result["answers"], questions)
    operation = operation_answer["choice"]
    target = None
    probabilities = {}
    if operation in targets:
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


class MissingTextArgument(ValueError):
    """The helper explicitly abstained; no literal argument is available."""


def parse_text_argument(content):
    """Accept a JSON value with optional Markdown framing, never extract from prose."""
    if not isinstance(content, str):
        raise ValueError("Expected text model content")
    content = content.strip()
    for opening in ("```json\n", "```\n"):
        if content.startswith(opening):
            content = content[len(opening):]
            break
    # Some providers append only the closing fence despite strict JSON mode.
    if content.endswith("```"):
        content = content[:-3].rstrip()
    output = json.loads(content)
    value = output["text"]
    if set(output) == {"text"} and value is None:
        raise MissingTextArgument("Text helper supplied no argument; nothing typed.")
    if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("Expected one nonempty literal argument")
    return value


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    formatting = {"response_format": {"type": "json_object"}}
    if urlsplit(base).hostname == "openrouter.ai":
        formatting = {
            "provider": {"require_parameters": True},
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "literal_text", "strict": True,
                "schema": {"type": "object", "properties": {"text": {"type": ["string", "null"]}},
                           "required": ["text"], "additionalProperties": False},
            }},
        }
    started = time.perf_counter()
    request = {
        "model": model,
        "max_tokens": 1024,
        **formatting,
        **reasoning,
        "messages": [
            {"role": "system", "content": TEXT_ARGUMENT if "argument" in context else TEXT_VALUE},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    }
    attempt_usage = []
    for attempt in range(2):
        result = post_json(base + "/chat/completions", key, request)
        attempt_usage.append(result.get("usage", {}))
        try:
            choice = result["choices"][0]
            truncated = choice.get("finish_reason") == "length" or choice.get("native_finish_reason") == "length"
            if truncated:
                trace = CURRENT_TRACE.get()
                if trace:
                    trace.emit("text.truncated", attempt=attempt + 1, max_tokens=request["max_tokens"],
                               retrying=attempt == 0)
                if attempt == 0:
                    # No tool has executed. Regenerate from identical source input,
                    # never complete or execute a partially returned argument.
                    request = {**request, "max_tokens": 2048}
                    continue
                raise ValueError("Text model output was truncated twice; nothing typed.")
            value = parse_text_argument(choice["message"]["content"])
        except MissingTextArgument:
            raise
        except (ValueError, KeyError, TypeError, IndexError):
            raise ValueError("Text helper returned no valid complete field value; nothing typed.") from None
        break
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
        "attempts": len(attempt_usage),
        "attempt_usage": attempt_usage,
    }
