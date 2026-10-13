#!/usr/bin/env python3
"""Account for typed configurations without using user-facing wording.

The audit deliberately excludes instruction, rationale, free-text output, and
record identifiers.  It reports a reproducible accounting view of structured
configuration multiplicity; it is not a human semantic-quality assessment.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, write_json


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def freeze(value: Any) -> Any:
    """Convert JSON values into a deterministic, hashable representation."""
    if value is None:
        return None
    if isinstance(value, dict):
        return tuple(sorted((str(k), freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(sorted((freeze(v) for v in value), key=repr))
    if isinstance(value, str):
        return value.strip() or None
    return value


def scenario_class(scenario_id: object) -> str:
    text = str(scenario_id or "")
    if not text:
        return "rule_only"
    if "n2_" in text:
        return "n2_branch_outage"
    if "n1_branch_outage" in text:
        return "n1_branch_outage"
    if "generator_perturbation" in text:
        return "generator_perturbation"
    if "equipment_contingency" in text or "equipment" in text:
        return "equipment_contingency"
    if "base_power_flow" in text:
        return "base_power_flow"
    return "other_simulation_case"


def network_family(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("network_family") or row.get("network_model") or ("networked" if row.get("scenario_id") else "rule_only"))


def severity(row: dict[str, Any]) -> str | None:
    metadata = row.get("metadata") or {}
    return metadata.get("severity_level")


def issue_profile(row: dict[str, Any], input_data: dict[str, Any]) -> str | None:
    metadata = row.get("metadata") or {}
    return metadata.get("issue_profile") or input_data.get("issue_profile") or input_data.get("analysis_focus")


def response_mode_class(value: object) -> str | None:
    text = str(value or "").lower()
    if not text:
        return None
    if "voltage" in text:
        return "voltage_first"
    if "constraint" in text:
        return "constraint_first"
    if "conservative" in text:
        return "conservative_correction"
    if "reversible" in text:
        return "reversible_first"
    if "locate" in text:
        return "locate_then_execute"
    if "handle" in text:
        return "handle_then_review"
    if "execute" in text:
        return "execute"
    return "other"


def permit_class(value: object) -> str | None:
    text = str(value or "").lower()
    if not text:
        return None
    if "not been backfilled" in text:
        return "applied_unbackfilled"
    if "missing" in text:
        return "missing"
    if "verbal" in text:
        return "verbal"
    if "confirmed" in text:
        return "confirmed"
    return "other"


def monitoring_class(value: object) -> str | None:
    text = str(value or "").lower()
    if not text:
        return None
    if "repeat" in text:
        return "repeat_process"
    if "after execution" in text or "post" in text:
        return "post_execution"
    if "supervisor" in text:
        return "supervised"
    return "other"


def query_result_shape(row: dict[str, Any]) -> str:
    output = row.get("output")
    if not isinstance(output, dict):
        return type(output).__name__
    result = output.get("query_result")
    if isinstance(result, dict):
        return "object"
    if isinstance(result, list):
        return "list"
    return type(result).__name__


def typed_signature(row: dict[str, Any], abstract: bool = False) -> tuple[Any, ...]:
    task = row.get("task_type")
    metadata = row.get("metadata") or {}
    input_data = row.get("input") if isinstance(row.get("input"), dict) else {}
    output = row.get("output") if isinstance(row.get("output"), dict) else {}
    scenario = scenario_class(row.get("scenario_id"))
    network = network_family(row)
    common = [task, scenario, network, severity(row), issue_profile(row, input_data)]

    if task == "auxiliary_decision":
        values = [
            freeze(input_data.get("available_actions") or []),
            response_mode_class(input_data.get("response_mode")),
            freeze(input_data.get("decision_window")),
            bool(input_data.get("opf_validation_summary")),
        ]
    elif task == "regulation_compliance_check":
        values = [
            freeze(input_data.get("observed_issues") or []),
            metadata.get("action_category"),
            row.get("compliance_label"),
        ]
    elif task == "dispatcher_intent_tool_call":
        slots = output.get("slots") or {}
        values = [
            row.get("intent"),
            slots.get("target_issue"),
            slots.get("priority"),
            freeze(input_data.get("command_channel")),
        ]
    elif task == "intelligent_data_query":
        query = row.get("structured_query") or output.get("structured_query") or {}
        values = [
            freeze(input_data.get("analysis_focus")),
            freeze(input_data.get("request_purpose")),
            freeze(query.get("filter")),
            freeze(query.get("source")),
            query_result_shape(row),
        ]
    elif task == "operation_ticket_check":
        values = [
            row.get("compliance_label"),
            freeze(input_data.get("operation_context")),
            permit_class(input_data.get("dispatch_permit_status")),
            freeze(input_data.get("object_consistency")),
            freeze(input_data.get("key_checks") or []),
            monitoring_class(input_data.get("monitoring_arrangement")),
        ]
    elif task == "regulation_qa":
        values = [
            input_data.get("rule_id"),
            input_data.get("secondary_rule_id"),
            freeze(input_data.get("audience")),
            freeze(input_data.get("deliverable")),
            freeze(input_data.get("focus_area")),
            freeze(input_data.get("usage_context")),
            freeze(input_data.get("evidence_fields") or []),
        ]
    else:
        values = []

    if abstract:
        # Remove network/state identity while preserving task, operating
        # condition, and typed target structure.  This is an accounting view,
        # not a claim that these categories are semantically complete.
        common[1] = "networked_scenario" if scenario != "rule_only" else "rule_only"
        common[2] = "networked" if scenario != "rule_only" else "rule_only"
    return tuple(freeze(value) for value in common + values)


def summarise(counter: Counter[tuple[Any, ...]], records: int) -> dict[str, Any]:
    multiplicities = list(counter.values())
    return {
        "records": records,
        "unique_signatures": len(counter),
        "unique_rate": round(len(counter) / records, 8) if records else 0.0,
        "singleton_signatures": sum(value == 1 for value in multiplicities),
        "median_records_per_signature": statistics.median(multiplicities) if multiplicities else 0,
        "p95_records_per_signature": sorted(multiplicities)[max(0, int(0.95 * len(multiplicities)) - 1)] if multiplicities else 0,
        "max_records_per_signature": max(multiplicities) if multiplicities else 0,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Typed Configuration and Semantic-Pattern Audit",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        f"- Records: {report['total_records']}",
        "",
        report["scope_statement"],
        "",
        "## Aggregate accounting",
        "",
        "| View | Records | Unique signatures | Unique rate | Median multiplicity | P95 multiplicity | Maximum multiplicity |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, summary in report["aggregate"].items():
        lines.append(
            f"| {name} | {summary['records']} | {summary['unique_signatures']} | "
            f"{summary['unique_rate']:.4f} | {summary['median_records_per_signature']} | "
            f"{summary['p95_records_per_signature']} | {summary['max_records_per_signature']} |"
        )
    lines.extend(
        [
            "",
            "## Task-stratified accounting",
            "",
            "| Task | Records | Typed configurations | Abstract patterns | Typed unique rate | Abstract unique rate |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for task, row in report["task_summary"].items():
        lines.append(
            f"| {task} | {row['records']} | {row['typed_configurations']} | {row['abstract_patterns']} | "
            f"{row['typed_unique_rate']:.4f} | {row['abstract_unique_rate']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The typed view preserves network and scenario classes plus structured task fields while excluding user-facing wording and free-text targets. The abstract view additionally collapses network and scenario identity to distinguish operating/task patterns from physical-state identity. Both views are deterministic accounting diagnostics; neither establishes independent semantic correctness, linguistic naturalness, or expert agreement.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--report-json", default="reports/typed_semantic_pattern_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/typed_semantic_pattern_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    rows = list(read_jsonl(ROOT / args.dataset))
    typed = Counter(typed_signature(row) for row in rows)
    abstract = Counter(typed_signature(row, abstract=True) for row in rows)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row.get("task_type"))].append(row)

    task_summary: dict[str, dict[str, Any]] = {}
    for task, task_rows in sorted(by_task.items()):
        typed_task = Counter(typed_signature(row) for row in task_rows)
        abstract_task = Counter(typed_signature(row, abstract=True) for row in task_rows)
        task_summary[task] = {
            "records": len(task_rows),
            "typed_configurations": len(typed_task),
            "abstract_patterns": len(abstract_task),
            "typed_unique_rate": round(len(typed_task) / len(task_rows), 8),
            "abstract_unique_rate": round(len(abstract_task) / len(task_rows), 8),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "dataset": args.dataset,
        "total_records": len(rows),
        "aggregate": {
            "typed_configuration": summarise(typed, len(rows)),
            "abstract_operating_task_pattern": summarise(abstract, len(rows)),
        },
        "task_summary": task_summary,
        "scope_statement": (
            "This audit counts structured configuration signatures from the released table. "
            "Instruction, rationale, free-text output, and record identifiers are excluded. "
            "The result measures configuration multiplicity and template fan-out; it is not a human semantic or linguistic validation."
        ),
    }
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.report_json, report)
    write_markdown(report, ROOT / args.report_md)


if __name__ == "__main__":
    main()
