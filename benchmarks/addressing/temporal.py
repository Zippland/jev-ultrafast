"""Explicit paid probe of pending-action and literal-text handling; no GUI mutations."""

import argparse
import hashlib
import json
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.mixed import mixed_field_text, pending_valid
from jev_ultrafast.tracing import CURRENT_TRACE, Trace

CASES = [
    ("background", ["关键词填写北京天气。", "小王，今天中午吃什么？"], True, "北京天气"),
    ("other-person-command", ["关键词填写北京天气。", "小王，你在你的电脑上搜索上海天气。"], True, "北京天气"),
    ("correction", ["关键词填写北京天气。", "不对，改成上海天气。"], False, "上海天气"),
    ("cancel", ["关键词填写北京天气。", "先别填，保持空白。"], False, None),
    ("quoted-background", ["关键词填写北京天气。", "小王刚才说的是搜索上海天气，这是转述。"], True, "北京天气"),
    ("literal-quote", ["关键词填写北京天气。", "改成这句话：小王，你在你的电脑上搜索上海天气。"], False,
     "小王，你在你的电脑上搜索上海天气。"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a fresh output directory to preserve prior evidence")
    load_environment()
    trace = Trace(args.output)
    token = CURRENT_TRACE.set(trace)
    source = Path(__file__).read_bytes()
    (args.output / "probe.py").write_bytes(source)
    (args.output / "manifest.json").write_text(json.dumps({
        "source_sha256": hashlib.sha256(source).hexdigest(), "actual_computer_use": False,
        "scope": "synthetic observed form, real pending JEV and text model calls",
        "cases": CASES,
    }, ensure_ascii=False, indent=2))
    action = {"app_key": "browser", "app_name": "浏览器", "channel": "Browser Use",
              "surface": "browser:browser", "id": "field", "kind": "fill", "label": "关键词", "value": ""}
    observation = {"surfaces": {"browser:browser": {"title": "搜索", "text": "关键词输入框为空"}},
                   "actions": [action]}
    bindings = {"browser": {"name": "浏览器"}}
    results = []
    try:
        for name, segments, expected_valid, expected_text in CASES:
            speech = [{"id": str(i), "text": text, "final": True, "source": "voice", "version": i + 1}
                      for i, text in enumerate(segments)]
            valid, _ = pending_valid(action, "北京天气", observation, bindings, speech, [])
            value = None
            error = None
            if expected_text is not None:
                try:
                    value, _ = mixed_field_text(action, observation, bindings, speech, [])
                except ValueError as exc:
                    error = str(exc)
            record = {"case": name, "pending_valid": valid, "expected_valid": expected_valid,
                      "text": value, "expected_text": expected_text,
                      "pending_correct": valid == expected_valid,
                      "text_exact": value == expected_text, "error": error}
            trace.emit("evaluation", **record)
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        (args.output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == "__main__":
    main()
