"""Append rulebook counterfactual compliance actions for plus11.

The plus10 data fixes action-surface collapse, but most compliance examples
still have one label per grid-state summary. This script adds same-scenario
counterfactual actions with labels computed by the validator rulebook, so a
model must consider both the grid state and the proposed action.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from augment_compliance_action_paraphrases import ACTION_SURFACES, INSTRUCTION_TEMPLATES
from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, write_json
from validate_dataset import expected_compliance_label


OUTPUT_BY_LABEL = {
    "non_compliant": "违规",
    "compliant_with_monitoring": "合规但需继续监视",
    "compliant": "合规",
}

ISSUE_TEXT = {
    "overload": "支路过载",
    "low_voltage": "低电压",
    "high_voltage": "高电压",
}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def active_issue_text(row: dict[str, Any]) -> str:
    issues = (row.get("input") or {}).get("observed_issues") or []
    mapped = [ISSUE_TEXT.get(str(item), str(item)) for item in issues]
    return "、".join(mapped) if mapped else "未见热稳定或电压越限"


def rationale_for(row: dict[str, Any], action: str, label: str) -> str:
    issue_text = active_issue_text(row)
    has_issue = issue_text != "未见热稳定或电压越限"
    if label == "non_compliant":
        if has_issue:
            return (
                f"当前场景存在{issue_text}，动作“{action}”会放大约束压力或跳过必要校核，"
                "不能作为当前可下发调度动作。"
            )
        return (
            f"当前虽未出现显式越限，但安全裕度已接近边界，动作“{action}”会进一步压缩可用裕度，"
            "因此不满足规程要求。"
        )
    if label == "compliant_with_monitoring":
        if has_issue:
            return (
                f"当前场景存在{issue_text}，动作“{action}”属于受控或纠正性操作，"
                "但执行后仍需复核潮流和电压并持续监视。"
            )
        return (
            f"当前未见热稳定或电压越限，动作“{action}”可作为受控操作，"
            "但仍需完成常规安全校核并继续监视。"
        )
    if has_issue:
        return (
            f"当前场景存在{issue_text}，动作“{action}”针对主要约束进行低风险纠正，"
            "满足当前安全边界下的执行条件。"
        )
    return f"当前未见热稳定或电压越限，动作“{action}”满足常规执行条件。"


def labels_by_grid_summary(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("task_type") != "regulation_compliance_check":
            continue
        summary = str((row.get("input") or {}).get("grid_state_summary") or "")
        labels[summary].add(str(row.get("compliance_label")))
    return labels


def possible_counterfactuals(
    row: dict[str, Any],
    scenario: dict[str, Any],
    scenario_index: int,
) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    current_action = str((row.get("input") or {}).get("proposed_action") or "")
    for category_index, (category, surfaces) in enumerate(ACTION_SURFACES.items()):
        label = expected_compliance_label(scenario, surfaces[0])
        if label is None:
            continue
        surface = surfaces[(scenario_index + category_index) % len(surfaces)]
        if surface == current_action and len(surfaces) > 1:
            surface = surfaces[(scenario_index + category_index + 1) % len(surfaces)]
        candidates.append((category, surface, label))
    return candidates


def make_counterfactual(
    row: dict[str, Any],
    category: str,
    action: str,
    label: str,
    variant: int,
) -> dict[str, Any]:
    out = copy.deepcopy(row)
    out["id"] = f"{row['id']}_counterfactual_{variant:02d}"
    out["instruction"] = INSTRUCTION_TEMPLATES[variant % len(INSTRUCTION_TEMPLATES)].format(action=action)
    out["input"] = dict(out.get("input") or {})
    out["input"]["proposed_action"] = action
    out["output"] = OUTPUT_BY_LABEL[label]
    out["compliance_label"] = label
    out["rationale"] = rationale_for(out, action, label)
    metadata = dict(out.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_compliance_counterfactual_actions.py",
            "augmentation_type": "rulebook_counterfactual_action",
            "source_record_id": row["id"],
            "action_category": category,
            "counterfactual_variant": variant,
        }
    )
    out["metadata"] = metadata
    return out


def build_augmented_rows(
    rows: list[dict[str, Any]],
    scenarios: dict[str, dict[str, Any]],
    max_new_per_summary: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out = list(rows)
    seen_ids = {str(row.get("id")) for row in rows}
    representative: dict[str, dict[str, Any]] = {}
    scenario_order: dict[str, int] = {}
    for row in rows:
        if row.get("task_type") != "regulation_compliance_check":
            continue
        summary = str((row.get("input") or {}).get("grid_state_summary") or "")
        if summary and summary not in representative:
            representative[summary] = row
            scenario_order[summary] = len(scenario_order)

    before_labels = labels_by_grid_summary(rows)
    stats: Counter[str] = Counter()
    after_labels = {summary: set(labels) for summary, labels in before_labels.items()}

    for summary, row in representative.items():
        scenario = scenarios.get(str(row.get("scenario_id") or ""))
        if not scenario:
            stats["missing_scenario"] += 1
            continue
        existing = after_labels.get(summary, set())
        candidates = possible_counterfactuals(row, scenario, scenario_order[summary])
        by_label: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for category, action, label in candidates:
            if label not in existing:
                by_label[label].append((category, action, label))
        additions: list[tuple[str, str, str]] = []
        for label in ("non_compliant", "compliant", "compliant_with_monitoring"):
            if label in by_label and len(additions) < max_new_per_summary:
                additions.append(by_label[label][0])
        for variant, (category, action, label) in enumerate(additions, start=1):
            new_row = make_counterfactual(row, category, action, label, variant)
            if new_row["id"] in seen_ids:
                raise ValueError(f"Duplicate generated id: {new_row['id']}")
            seen_ids.add(new_row["id"])
            out.append(new_row)
            after_labels.setdefault(summary, set()).add(label)
            stats["added_records"] += 1
            stats[f"added_label_{label}"] += 1
            stats[f"added_category_{category}"] += 1

    pure_before = sum(1 for labels in before_labels.values() if len(labels) == 1)
    pure_after = sum(1 for labels in after_labels.values() if len(labels) == 1)
    report = {
        "input_grid_summaries": len(before_labels),
        "pure_grid_summary_labels_before": pure_before,
        "pure_grid_summary_label_rate_before": pure_before / max(len(before_labels), 1),
        "pure_grid_summary_labels_after": pure_after,
        "pure_grid_summary_label_rate_after": pure_after / max(len(after_labels), 1),
        "stats": dict(stats),
        "added_label_counts": {
            key.removeprefix("added_label_"): value
            for key, value in stats.items()
            if key.startswith("added_label_")
        },
        "added_category_counts": {
            key.removeprefix("added_category_"): value
            for key, value in stats.items()
            if key.startswith("added_category_")
        },
    }
    return out, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus10.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus11.jsonl")
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--report-json", default="reports/compliance_counterfactual_augmentation_v1.2_plus11.json")
    parser.add_argument("--report-md", default="reports/compliance_counterfactual_augmentation_v1.2_plus11.md")
    parser.add_argument("--max-new-per-summary", type=int, default=2)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    scenario_rows = read_json(ROOT / args.scenarios)
    scenarios = {row["scenario_id"]: row for row in scenario_rows}
    out_rows, counterfactual_report = build_augmented_rows(rows, scenarios, args.max_new_per_summary)
    write_jsonl(ROOT / args.output, out_rows)

    before_counts = Counter(
        row.get("compliance_label")
        for row in rows
        if row.get("task_type") == "regulation_compliance_check"
    )
    after_counts = Counter(
        row.get("compliance_label")
        for row in out_rows
        if row.get("task_type") == "regulation_compliance_check"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "input_records": len(rows),
        "output_records": len(out_rows),
        "added_records": len(out_rows) - len(rows),
        "max_new_per_summary": args.max_new_per_summary,
        "before_regulation_compliance_labels": dict(before_counts),
        "after_regulation_compliance_labels": dict(after_counts),
        "counterfactual_grid_summary_report": counterfactual_report,
        "passed": counterfactual_report["stats"].get("missing_scenario", 0) == 0
        and counterfactual_report["pure_grid_summary_label_rate_after"] <= 0.05,
    }
    write_json(ROOT / args.report_json, report)

    lines = [
        "# Compliance Counterfactual Action Augmentation",
        "",
        f"Generated: {report['generated_at']}",
        f"Input: `{args.input}`",
        f"Output: `{args.output}`",
        f"Input records: {report['input_records']}",
        f"Output records: {report['output_records']}",
        f"Added records: {report['added_records']}",
        "",
        "## Grid-State Label Diversity",
        "",
        f"- Pure grid-state label rate before: {counterfactual_report['pure_grid_summary_label_rate_before']:.6f}",
        f"- Pure grid-state label rate after: {counterfactual_report['pure_grid_summary_label_rate_after']:.6f}",
        "",
        "## Added Labels",
        "",
        *[f"- {label}: {count}" for label, count in sorted(counterfactual_report["added_label_counts"].items())],
        "",
        "## Added Action Categories",
        "",
        *[
            f"- {category}: {count}"
            for category, count in sorted(counterfactual_report["added_category_counts"].items())
        ],
    ]
    (ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
