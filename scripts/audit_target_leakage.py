#!/usr/bin/env python3
"""Audit whether released inputs expose their supervised targets.

The audit is intentionally small and deterministic.  It checks the natural
instruction and the exposed structured input, while allowing provenance fields
such as scenario and rule identifiers to remain visible.  A release cannot be
called leakage-controlled when a closed label or a rule answer is copied into
the user-facing input.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT


CLASSIFICATION_TASKS = {
    "regulation_compliance_check": "compliance_label",
    "operation_ticket_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {flatten_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(flatten_text(item) for item in value)
    return str(value or "")


def normalise(value: Any) -> str:
    return " ".join(flatten_text(value).lower().replace("_", " ").split())


def audit(path: Path) -> dict[str, Any]:
    total = 0
    task_counts: Counter[str] = Counter()
    leaks: Counter[str] = Counter()
    examples: dict[str, dict[str, Any]] = {}

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            total += 1
            task = str(row.get("task_type") or "")
            task_counts[task] += 1
            instruction = normalise(row.get("instruction"))
            exposed_input = normalise(row.get("input"))

            if task in CLASSIFICATION_TASKS:
                field = CLASSIFICATION_TASKS[task]
                if field == "compliance_label":
                    target = normalise(row.get(field) or row.get("output"))
                else:
                    output = row.get("output") if isinstance(row.get("output"), dict) else {}
                    target = normalise(output.get(field))
                if target and (target in instruction or target in exposed_input):
                    key = f"{task}:target_in_exposed_input"
                    leaks[key] += 1
                    examples.setdefault(key, {"id": row.get("id"), "target": target, "instruction": row.get("instruction")})

            if task == "regulation_qa":
                summary = normalise((row.get("input") or {}).get("rule_summary"))
                if summary and (summary in instruction or summary in exposed_input):
                    key = "regulation_qa:rule_summary_in_exposed_input"
                    leaks[key] += 1
                    examples.setdefault(key, {"id": row.get("id"), "instruction": row.get("instruction")})

    return {
        "status": "pass" if not leaks else "fail",
        "input": str(path.relative_to(ROOT)),
        "records": total,
        "task_counts": dict(sorted(task_counts.items())),
        "leak_counts": dict(sorted(leaks.items())),
        "leak_total": sum(leaks.values()),
        "examples": examples,
        "policy": "Closed labels and rule summaries must not appear in the natural instruction or exposed structured input; provenance identifiers remain allowed.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output-json", default="reports/target_leakage_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/target_leakage_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    report = audit(ROOT / args.input)
    (ROOT / args.output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Target leakage audit",
        "",
        f"- Status: **{report['status']}**",
        f"- Records: **{report['records']:,}**",
        f"- Exposed-target records: **{report['leak_total']:,}**",
        "",
        "The audit scans the user-facing instruction and exposed structured input. "
        "Provenance identifiers are allowed; supervised labels and rule summaries are not.",
    ]
    (ROOT / args.output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "records": report["records"], "leak_total": report["leak_total"]}, ensure_ascii=False))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
