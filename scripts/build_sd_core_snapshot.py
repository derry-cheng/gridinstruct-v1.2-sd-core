"""Build a lower-duplication Scientific Data core snapshot from plus15.

The plus15 file is kept as the full augmented pool. This script creates an
SD-facing core release by retaining all original records, always retaining
reviewed/accepted records, and selecting a capped, deterministic subset of
augmentation records. The goal is a manuscript-friendly release surface with
lower source-group expansion and less template/proxy risk.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_AUGMENTATION_QUOTAS = {
    "operation_implicit_ticket_llm": 681,
    "rulebook_counterfactual_action": 3500,
    "rulebook_action_surface_paraphrase": 3500,
    "dispatcher_intent_compound_counterfactual": 2000,
    "operation_ticket_rulebook_counterfactual": 2000,
    "operation_monitoring_boundary_counterfactual": 2000,
    "operation_plain_ticket_adversarial": 2000,
}

REVIEW_STATUS_PRIORITY = {
    "llm_dual_review_accepted": 0,
    "llm_adjudicated_revised": 1,
    "llm_augmented_pending_expert_review": 2,
    "generated_pending_expert_review": 5,
}

LABEL_PRIORITY = {
    "regulation_compliance_check": {
        "compliant": 0,
        "non_compliant": 1,
        "compliant_with_monitoring": 2,
    },
    "operation_ticket_check": {
        "compliant_with_monitoring": 0,
        "non_compliant": 1,
        "compliant": 2,
    },
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def augmentation_type(row: dict[str, Any]) -> str:
    return str((row.get("metadata") or {}).get("augmentation_type") or "original")


def source_key(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("source_record_id") or row.get("id"))


def label_of(row: dict[str, Any]) -> str:
    output = row.get("output")
    if isinstance(output, dict):
        output = output.get("intent") or output.get("structured_query") or output
    return str(row.get("compliance_label") or row.get("intent") or output or "NA")


def network_of(row: dict[str, Any]) -> str:
    return str(row.get("network_model") or "null")


def group_size_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(source_key(row) for row in rows if augmentation_type(row) != "original")
    values = sorted(counts.values())
    if not values:
        return {"groups": 0, "max": 0, "p95": 0, "histogram": {}}
    p95_index = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
    return {
        "groups": len(values),
        "max": max(values),
        "p95": values[p95_index],
        "histogram": dict(Counter(values)),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_task = Counter(str(row.get("task_type")) for row in rows)
    by_stage = Counter(str(row.get("task_stage")) for row in rows)
    by_network = Counter(network_of(row) for row in rows)
    by_aug = Counter(augmentation_type(row) for row in rows)
    label_by_task: dict[str, dict[str, int]] = {}
    for task in sorted(by_task):
        label_by_task[task] = dict(Counter(label_of(row) for row in rows if str(row.get("task_type")) == task))
    return {
        "total_records": len(rows),
        "by_task": dict(by_task),
        "by_stage": dict(by_stage),
        "by_network": dict(by_network),
        "by_augmentation_type": dict(by_aug),
        "label_by_task": label_by_task,
        "source_group_size": group_size_summary(rows),
    }


def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    metadata = row.get("metadata") or {}
    task = str(row.get("task_type"))
    label = str(row.get("compliance_label") or "")
    return (
        REVIEW_STATUS_PRIORITY.get(str(metadata.get("validation_status")), 9),
        LABEL_PRIORITY.get(task, {}).get(label, 5),
        str(metadata.get("action_category") or ""),
        source_key(row),
        str(row.get("id")),
    )


def select_core_rows(
    rows: list[dict[str, Any]],
    quotas: dict[str, int],
    max_augmented_per_source: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    augmented_source_counts: Counter[str] = Counter()
    forced_reasons: Counter[str] = Counter()

    for row in rows:
        if augmentation_type(row) == "original":
            selected.append(row)
            selected_ids.add(str(row["id"]))

    by_aug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        aug = augmentation_type(row)
        if aug != "original":
            by_aug[aug].append(row)

    for aug in quotas:
        aug_rows = by_aug.get(aug, [])
        quota = quotas.get(aug, 0)
        if quota <= 0:
            continue
        kept = 0
        for row in sorted(aug_rows, key=sort_key):
            rid = str(row["id"])
            if rid in selected_ids:
                continue
            src = source_key(row)
            status = str((row.get("metadata") or {}).get("validation_status") or "")
            force = status in {"llm_dual_review_accepted", "llm_adjudicated_revised"}
            if augmented_source_counts[src] >= max_augmented_per_source:
                continue
            if not force and kept >= quota:
                continue
            selected.append(row)
            selected_ids.add(rid)
            augmented_source_counts[src] += 1
            kept += 1
            if force:
                forced_reasons[status] += 1

    selected.sort(key=lambda row: str(row["id"]))
    report = {
        "quotas": quotas,
        "max_augmented_per_source": max_augmented_per_source,
        "selected_ids": len(selected_ids),
        "forced_review_status_counts": dict(forced_reasons),
        "input_summary": summarize(rows),
        "core_summary": summarize(selected),
        "retention_by_augmentation_type": {},
    }
    input_aug = Counter(augmentation_type(row) for row in rows)
    core_aug = Counter(augmentation_type(row) for row in selected)
    for aug in sorted(input_aug):
        before = input_aug[aug]
        after = core_aug.get(aug, 0)
        report["retention_by_augmentation_type"][aug] = {
            "input": before,
            "core": after,
            "retention_rate": after / before if before else 0.0,
        }
    return selected, report


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    core = report["core_summary"]
    lines = [
        "# SD Core Selection Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Input: `{report['input']}`",
        f"Output: `{report['output']}`",
        "",
        "## Summary",
        "",
        f"- Input records: {report['input_summary']['total_records']}",
        f"- Core records: {core['total_records']}",
        f"- Augmented source group max: {core['source_group_size']['max']}",
        f"- Augmented source group p95: {core['source_group_size']['p95']}",
        "",
        "## Task Distribution",
        "",
        "| Task | Records |",
        "| --- | ---: |",
    ]
    for task, count in sorted(core["by_task"].items()):
        lines.append(f"| `{task}` | {count} |")
    lines += ["", "## Augmentation Retention", "", "| Augmentation type | Input | Core | Retention |", "| --- | ---: | ---: | ---: |"]
    for aug, item in sorted(report["retention_by_augmentation_type"].items()):
        lines.append(f"| `{aug}` | {item['input']} | {item['core']} | {item['retention_rate']:.3f} |")
    lines += ["", "## Label Distribution By Task", ""]
    for task, labels in sorted(core["label_by_task"].items()):
        lines.append(f"### {task}")
        lines.append("")
        lines.append("| Label | Records |")
        lines.append("| --- | ---: |")
        for label, count in sorted(labels.items()):
            lines.append(f"| `{label}` | {count} |")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_quotas(value: str | None) -> dict[str, int]:
    quotas = dict(DEFAULT_AUGMENTATION_QUOTAS)
    if not value:
        return quotas
    for part in value.split(","):
        if not part.strip():
            continue
        key, raw = part.split("=", 1)
        quotas[key.strip()] = int(raw)
    return quotas


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus15.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--report-json", default="reports/sd_core_selection_report.json")
    parser.add_argument("--report-md", default="reports/sd_core_selection_report.md")
    parser.add_argument("--max-augmented-per-source", type=int, default=3)
    parser.add_argument("--augmentation-quotas", default=None)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    quotas = parse_quotas(args.augmentation_quotas)
    selected, report = select_core_rows(rows, quotas, args.max_augmented_per_source)
    report.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input": args.input,
            "output": args.output,
            "passed": 60000 <= len(selected) <= 90000
            and report["core_summary"]["source_group_size"]["max"] <= args.max_augmented_per_source
            and report["core_summary"]["source_group_size"]["p95"] <= args.max_augmented_per_source,
        }
    )
    write_jsonl(ROOT / args.output, selected)
    write_json(ROOT / args.report_json, report)
    write_markdown(ROOT / args.report_md, report)
    print(json.dumps({"output": args.output, "records": len(selected), "passed": report["passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
