"""Summarize complete, independently verified cases without counting blocked setup as model failure."""

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def timing(values):
    if not values:
        return None
    values = sorted(values)
    return {
        "count": len(values),
        "median_ms": statistics.median(values),
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1],
        "max_ms": max(values),
    }


def summarize(folder):
    suite = json.loads((Path(__file__).parent / "suite.json").read_text())
    cases = {case["id"]: case for case in suite["cases"]}
    records = [json.loads(p.read_text()) for p in sorted(folder.glob("*/result.json"))]
    completed = [r for r in records if r.get("final_pages") and r.get("final")]
    interrupted = [r["id"] for r in records if r not in completed]
    setup_errors = [p.parent.name for p in folder.glob("*/error.json")]
    recorded = {r["id"] for r in records} | set(setup_errors)
    incomplete = [p.name for p in folder.iterdir() if p.is_dir() and p.name in cases and p.name not in recorded]
    interrupted.extend(sorted(incomplete))
    groups = {
        g: {
            "completed": sum(r["group"] == g for r in completed),
            "passed": sum(r["group"] == g and r["pass"] for r in completed),
        }
        for g in "CTBX"
    }
    history = [h for r in completed for h in r["history"]]
    models = [c for r in completed for c in r["model_calls"]]
    texts = [c for r in completed for c in r["text_calls"]]
    cohort_checks = {
        "single_input": lambda case: len(case["events"]) == 1,
        "multiple_inputs": lambda case: len(case["events"]) > 1,
        "no_actions": lambda case: case["no_actions"],
        "during_choice": lambda case: any(e.get("during") == "choice" for e in case["events"]),
        "during_text": lambda case: any(e.get("during") == "text" for e in case["events"]),
        "after_actions": lambda case: any("after_actions" in e for e in case["events"]),
        "asr_revisions": lambda case: len({e["segment"] for e in case["events"]}) < len(case["events"]),
    }
    cohorts = {}
    for name, check in cohort_checks.items():
        selected = [r for r in completed if check(cases[r["id"]])]
        cohorts[name] = {
            "completed": len(selected),
            "passed": sum(r["pass"] for r in selected),
            "failed_ids": [r["id"] for r in selected if not r["pass"]],
        }
    stale = []
    for r in completed:
        for h in r["history"]:
            arrived = max((e["version"] for e in r["events"] if e["at_ms"] <= h["dispatched_ms"]), default=0)
            if h["speech_version"] < arrived:
                stale.append(
                    {
                        "case": r["id"],
                        "used": h["speech_version"],
                        "arrived": arrived,
                        "app": h["app"],
                        "dispatched_ms": h["dispatched_ms"],
                    }
                )
    report = {
        "completed": len(completed),
        "passed": sum(r["pass"] for r in completed),
        "target": 100,
        "interrupted": interrupted,
        "setup_errors": setup_errors,
        "groups": groups,
        "cohorts": cohorts,
        "returned_actions_by_channel": dict(Counter(h["channel"] for h in history)),
        "browser_actions_by_channel": dict(Counter(h["channel"] for h in history if h["app_key"] == "browser")),
        "choice_and_gate_calls": len(models),
        "calls_by_kind": dict(Counter(c["kind"] for c in models)),
        "pending_decisions": dict(Counter(
            c["response"]["answers"]["pending"]["choice"] for c in models if c["kind"] == "pending"
        )),
        "text_calls": len(texts),
        "choice_models": dict(Counter(c["response"].get("model") for c in models)),
        "text_models": dict(Counter(c.get("model") for c in texts)),
        "decision_latency": timing([c["latency_ms"] for c in models]),
        "text_latency": timing([c["latency_ms"] for c in texts]),
        "case_latency": timing([r["elapsed_ms"] for r in completed]),
        "first_action_latency": timing([r["history"][0]["dispatched_ms"] for r in completed if r["history"]]),
        "failure_checks": dict(Counter(f["check"] for r in completed for f in r["failures"])),
        "actions_with_newer_unevaluated_input": stale,
        "failures": {r["id"]: r["failures"] for r in completed if not r["pass"]},
    }
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    lines = [
        "# CU100 混合执行结果",
        "",
        f"完成独立验证：{report['completed']} / 100；通过：{report['passed']}。",
        "锁屏中断与初始化失败不混入模型通过率；未运行题目不计分。",
        "",
        "| 组别 | 完成 | 通过 |",
        "| --- | ---: | ---: |",
    ]
    for g, name in [("C", "计算器"), ("T", "文本编辑"), ("B", "浏览器"), ("X", "跨 App")]:
        lines.append(f"| {name} | {groups[g]['completed']} / 25 | {groups[g]['passed']} |")
    lines += [
        "",
        "浏览器动作通道：" + json.dumps(report["browser_actions_by_channel"], ensure_ascii=False),
        f"选择/复核模型调用：{len(models)}；文本模型调用：{len(texts)}。",
        "选择/复核延迟：" + json.dumps(report["decision_latency"], ensure_ascii=False),
        "文本生成延迟：" + json.dumps(report["text_latency"], ensure_ascii=False),
        "",
        "## 时序分组",
        "",
        "以下分组存在重叠，不能相加为总数。",
        "",
        "| 场景 | 完成 | 通过 | 失败题号 |",
        "| --- | ---: | ---: | --- |",
    ]
    for key, name in [
        ("single_input", "一次输入"), ("multiple_inputs", "多次输入"), ("no_actions", "要求不操作"),
        ("during_choice", "选择推理期间更新"), ("during_text", "文本生成期间更新"),
        ("after_actions", "执行后追加输入"), ("asr_revisions", "同段 ASR 修订"),
    ]:
        cohort = cohorts[key]
        lines.append(f"| {name} | {cohort['completed']} | {cohort['passed']} | {', '.join(cohort['failed_ids'])} |")
    lines += [
        "",
        "## 逐题结果",
        "",
        "| 题号 | 结果 | 原因 |",
        "| --- | --- | --- |",
    ]
    for r in completed:
        why = json.dumps(r["failures"], ensure_ascii=False) if r["failures"] else "独立最终状态验证通过"
        lines.append(f"| {r['id']} | {'通过' if r['pass'] else '失败'} | {why.replace('|', '/')} |")
    if interrupted or setup_errors:
        lines += ["", "中断：" + ", ".join(interrupted), "初始化失败：" + ", ".join(setup_errors)]
    lines += [
        "",
        "边界：转写文本按时序回放，真实 App 与工具执行；不包含麦克风/ASR 准确率测试。",
        "只运行冻结的一版题集，不能据此推导任意 App、任意口语的成功率。"
        "原始请求、实际动作与独立 AX 结果保存在各题目录。",
    ]
    (folder / "report.md").write_text("\n".join(lines) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.folder), ensure_ascii=False, indent=2))
