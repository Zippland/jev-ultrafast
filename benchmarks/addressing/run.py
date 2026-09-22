"""Paid, no-GUI ablation of an additional Jev addressing head. Never deploys a policy."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.mixed import build_request
from jev_ultrafast.model import post_json, selected_answers, validate_choice
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    load_environment()
    source = Path(__file__).with_name("cases.json")
    cases = json.loads(source.read_text())
    trace = Trace(args.output)
    token = CURRENT_TRACE.set(trace)
    (args.output / "cases.json").write_bytes(source.read_bytes())
    (args.output / "manifest.json").write_text(json.dumps({
        "cases_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "observation": "synthetic browser search form", "actual_computer_use": False,
        "model": os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"),
    }, indent=2))
    surface = "browser:browser"
    base = {"app_key": "browser", "app_name": "浏览器", "channel": "Browser Use", "surface": surface}
    observation = {"surfaces": {surface: {"title": "搜索", "text": "关键词输入框为空，搜索按钮可用"}},
                   "actions": [{**base, "id": "field", "kind": "fill", "label": "关键词", "value": ""},
                               {**base, "id": "button", "kind": "click", "label": "搜索"}]}
    gate = {"type": "choice", "instructions": {
        "question": "Does the chronological speech contain an actionable request directed to this assistant?",
        "focus": "Conversation with other people, narration and quoted commands are not requests to you. "
                 "A clear task remains actionable while the speaker adds unrelated background conversation."},
        "criteria": {"DIRECTED": "An actionable instruction is directed to this assistant.",
                     "BACKGROUND": "No actionable instruction is directed to this assistant."}}
    results = []
    try:
        for index, case in enumerate(cases):
            speech = [{"id": "speech", "text": case["text"], "final": True, "source": "voice", "version": 1}]
            body, _ = build_request(observation, {"browser": {"name": "浏览器"}}, speech, [])
            variants = ["baseline", "gate"] if index % 2 == 0 else ["gate", "baseline"]
            for variant in variants:
                request = copy.deepcopy(body)
                if variant == "gate":
                    request["questions"]["addressing"] = gate
                result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], request)
                operation, _ = selected_answers(result["answers"], request["questions"])
                directed = (variant == "baseline" or validate_choice(
                    result["answers"].get("addressing", {}), gate["criteria"])["choice"] == "DIRECTED")
                acts = operation["choice"] != "LISTEN" and directed
                record = {"case": case["id"], "variant": variant, "operation": operation["choice"],
                          "directed": directed, "acts": acts, "expected": case["act"],
                          "correct": acts == case["act"], "category": case.get("category", "semantic")}
                trace.emit("evaluation", **record)
                results.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
        (args.output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == "__main__":
    main()
