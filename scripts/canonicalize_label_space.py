"""Canonicalize label and intent fields without changing task semantics."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl, write_json, write_jsonl


COMPLIANCE_LABELS = {"compliant", "compliant_with_monitoring", "non_compliant"}
COMPLIANCE_ALIASES = {
    "noncompliant": "non_compliant",
    "non-compliant": "non_compliant",
    "违规": "non_compliant",
    "合规": "compliant",
    "合规但需继续监视": "compliant_with_monitoring",
}
COMPLIANCE_OUTPUT = {
    "non_compliant": "违规",
    "compliant_with_monitoring": "合规但需继续监视",
    "compliant": "合规",
}

INTENT_LABELS = {
    "diagnose_and_dispatch",
    "mitigate_violations",
    "security_check_and_redispatch",
}
INTENT_ALIASES = {
    "diagnose_and_suggest": "diagnose_and_dispatch",
    "assess_security": "security_check_and_redispatch",
}


def sha_record(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def canonical_compliance_label(value: Any) -> str | None:
    if value is None:
        return None
    label = str(value).strip()
    label = COMPLIANCE_ALIASES.get(label, label)
    if label not in COMPLIANCE_LABELS:
        return label
    return label


def canonical_intent(value: Any) -> str | None:
    if value is None:
        return None
    intent = str(value).strip()
    return INTENT_ALIASES.get(intent, intent)


def tool_plan_for(intent: str, scenario_id: str | None) -> list[dict[str, Any]]:
    scenario = scenario_id or "unknown"
    if intent == "security_check_and_redispatch":
        return [
            {"tool": "get_violations", "args": {"scenario_id": scenario}},
            {"tool": "run_power_flow", "args": {"case": "base_and_post_action"}},
            {"tool": "suggest_corrective_actions", "args": {"scenario_id": scenario}},
        ]
    return [
        {"tool": "get_violations", "args": {"scenario_id": scenario}},
        {"tool": "suggest_corrective_actions", "args": {"scenario_id": scenario}},
        {"tool": "run_power_flow", "args": {"case": "post_action"}},
    ]


def canonicalize_row(row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    row = dict(row)
    changes: list[str] = []
    metadata = dict(row.get("metadata") or {})
    previous_signature = {
        "output": row.get("output"),
        "compliance_label": row.get("compliance_label"),
        "intent": row.get("intent"),
        "slots": row.get("slots"),
        "tool_plan": row.get("tool_plan"),
    }

    task_type = row.get("task_type")
    if task_type in {"operation_ticket_check", "regulation_compliance_check"}:
        old_label = row.get("compliance_label")
        new_label = canonical_compliance_label(old_label)
        if new_label != old_label:
            row["compliance_label"] = new_label
            changes.append("compliance_label_alias")
        if new_label in COMPLIANCE_OUTPUT and row.get("output") != COMPLIANCE_OUTPUT[new_label]:
            row["output"] = COMPLIANCE_OUTPUT[new_label]
            changes.append("compliance_output_alignment")

    if task_type == "dispatcher_intent_tool_call":
        old_intent = row.get("intent")
        new_intent = canonical_intent(old_intent)
        if new_intent != old_intent:
            row["intent"] = new_intent
            changes.append("intent_alias")
        output = dict(row.get("output") or {})
        if output.get("intent") != new_intent:
            output["intent"] = new_intent
            row["output"] = output
            changes.append("output_intent_alignment")
        slots = dict(row.get("slots") or output.get("slots") or {})
        if new_intent and slots.get("intent_family") != new_intent:
            slots["intent_family"] = new_intent
            row["slots"] = slots
            output["slots"] = slots
            row["output"] = output
            changes.append("slot_intent_family_alignment")
        if new_intent in INTENT_LABELS and changes:
            row["tool_plan"] = tool_plan_for(new_intent, row.get("scenario_id"))
            changes.append("tool_plan_alignment")

    if changes:
        metadata["label_canonicalized_by"] = "canonicalize_label_space.py"
        metadata["label_canonicalized_at"] = datetime.now(timezone.utc).isoformat()
        metadata["previous_label_signature_sha256"] = sha_record(previous_signature)
        metadata["label_canonicalization_changes"] = sorted(set(changes))
        row["metadata"] = metadata
    return row, changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus8.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus9.jsonl")
    parser.add_argument("--report-json", default="reports/label_space_canonicalization_v1.2_plus9.json")
    parser.add_argument("--report-md", default="reports/label_space_canonicalization_v1.2_plus9.md")
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    out_rows = []
    change_counter: Counter[str] = Counter()
    changed_ids = []
    for row in rows:
        new_row, changes = canonicalize_row(row)
        out_rows.append(new_row)
        if changes:
            changed_ids.append(new_row["id"])
            change_counter.update(changes)

    label_counts = {
        "operation_ticket_check": Counter(
            row.get("compliance_label") for row in out_rows if row.get("task_type") == "operation_ticket_check"
        ),
        "regulation_compliance_check": Counter(
            row.get("compliance_label") for row in out_rows if row.get("task_type") == "regulation_compliance_check"
        ),
        "dispatcher_intent_tool_call": Counter(
            row.get("intent") for row in out_rows if row.get("task_type") == "dispatcher_intent_tool_call"
        ),
    }
    unknowns = {
        "operation_ticket_check": sorted(
            label for label in label_counts["operation_ticket_check"] if label not in COMPLIANCE_LABELS
        ),
        "regulation_compliance_check": sorted(
            label for label in label_counts["regulation_compliance_check"] if label not in COMPLIANCE_LABELS
        ),
        "dispatcher_intent_tool_call": sorted(
            label for label in label_counts["dispatcher_intent_tool_call"] if label not in INTENT_LABELS
        ),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "records": len(out_rows),
        "changed_records": len(changed_ids),
        "changed_ids": changed_ids,
        "change_counts": dict(change_counter),
        "label_counts": {task: dict(counts) for task, counts in label_counts.items()},
        "unknown_labels_after_canonicalization": unknowns,
        "passed": all(not values for values in unknowns.values()),
    }
    write_jsonl(ROOT / args.output, out_rows)
    write_json(ROOT / args.report_json, report)

    lines = [
        "# Label Space Canonicalization Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Input: `{args.input}`",
        f"Output: `{args.output}`",
        "",
        f"- Records: {len(out_rows)}",
        f"- Changed records: {len(changed_ids)}",
        f"- Passed: {report['passed']}",
        "",
        "## Change Counts",
        "",
    ]
    for key, value in sorted(change_counter.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Label Counts", ""])
    for task, counts in label_counts.items():
        formatted = ", ".join(f"{label}={count}" for label, count in sorted(counts.items()))
        lines.append(f"- {task}: {formatted}")
    lines.extend(["", "## Unknown Labels", ""])
    for task, values in unknowns.items():
        lines.append(f"- {task}: {', '.join(values) if values else 'none'}")
    (ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
