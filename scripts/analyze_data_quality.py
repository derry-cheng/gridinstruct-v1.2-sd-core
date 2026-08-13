"""Analyze GridInstruct release data quality beyond schema validation."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


REQUIRED_BY_TASK: dict[str, tuple[str, ...]] = {
    "operation_ticket_check": ("compliance_label", "source_regulation_ids"),
    "regulation_qa": ("output", "source_regulation_ids"),
    "regulation_compliance_check": ("compliance_label", "rationale", "scenario_id", "source_regulation_ids"),
    "auxiliary_decision": ("output", "chosen_response", "rejected_response", "scenario_id"),
    "dispatcher_intent_tool_call": ("intent", "slots", "tool_plan", "scenario_id"),
    "intelligent_data_query": ("structured_query", "query_result", "scenario_id"),
}


ALLOW_EMPTY_VALUES = {
    ("intelligent_data_query", "query_result"),
}


def safe_percent(num: int | float, den: int | float) -> float:
    return float(num) / float(den) if den else 0.0


def short_hashable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def field_missing(row: dict[str, Any], field: str) -> bool:
    task = str(row.get("task_type"))
    if (task, field) in ALLOW_EMPTY_VALUES:
        return field not in row
    value = row.get(field)
    return value in (None, "", [], {})


def load_split(path: str) -> list[dict[str, Any]]:
    p = ROOT / path
    return read_jsonl(p) if p.exists() else []


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    task_counts = Counter(row.get("task_type") for row in rows)
    network_counts = Counter(str(row.get("network_model") or "none") for row in rows)
    rule_counts = Counter(rule for row in rows for rule in row.get("source_regulation_ids", []))
    scenario_counts = Counter(row.get("scenario_id") for row in rows if row.get("scenario_id"))
    validation_status_counts = Counter(
        str(row.get("metadata", {}).get("validation_status") or "missing") for row in rows
    )
    missing_required: dict[str, Counter[str]] = defaultdict(Counter)
    label_distribution: dict[str, Counter[str]] = defaultdict(Counter)
    response_lengths: dict[str, list[int]] = defaultdict(list)
    duplicate_keys = Counter()
    for row in rows:
        task = str(row.get("task_type"))
        for field in REQUIRED_BY_TASK.get(task, ()):
            if field_missing(row, field):
                missing_required[task][field] += 1
        if row.get("compliance_label"):
            label_distribution[task][str(row["compliance_label"])] += 1
        target = row.get("output") or row.get("chosen_response") or row.get("compliance_label") or row.get("intent")
        response_lengths[task].append(len(short_hashable(target)))
        duplicate_keys[(task, row.get("instruction"), short_hashable(row.get("input")))] += 1
    duplicate_prompt_count = sum(count - 1 for count in duplicate_keys.values() if count > 1)
    length_summary = {}
    for task, lengths in response_lengths.items():
        if not lengths:
            continue
        sorted_lengths = sorted(lengths)
        length_summary[task] = {
            "min": sorted_lengths[0],
            "median": sorted_lengths[len(sorted_lengths) // 2],
            "p95": sorted_lengths[int(0.95 * (len(sorted_lengths) - 1))],
            "max": sorted_lengths[-1],
        }
    return {
        "records": len(rows),
        "task_counts": dict(task_counts),
        "network_counts": dict(network_counts),
        "rule_counts": dict(rule_counts),
        "scenario_count": len(scenario_counts),
        "scenario_reuse_top10": scenario_counts.most_common(10),
        "validation_status_counts": dict(validation_status_counts),
        "missing_required": {task: dict(counter) for task, counter in missing_required.items()},
        "label_distribution": {task: dict(counter) for task, counter in label_distribution.items()},
        "response_length_chars": length_summary,
        "duplicate_prompt_input_count": duplicate_prompt_count,
    }


def split_overlap(splits: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    ids = {name: {row.get("id") for row in rows} for name, rows in splits.items()}
    scenarios = {
        name: {row.get("scenario_id") for row in rows if row.get("scenario_id")}
        for name, rows in splits.items()
    }
    prompts = {
        name: {
            (row.get("task_type"), row.get("instruction"), short_hashable(row.get("input")))
            for row in rows
        }
        for name, rows in splits.items()
    }
    overlaps = {}
    names = sorted(splits)
    for idx, left in enumerate(names):
        for right in names[idx + 1 :]:
            overlaps[f"{left}__{right}"] = {
                "id_overlap": len(ids[left] & ids[right]),
                "scenario_overlap": len(scenarios[left] & scenarios[right]),
                "prompt_input_overlap": len(prompts[left] & prompts[right]),
            }
    return overlaps


def gate_report(dataset_summary: dict[str, Any], split_summaries: dict[str, Any], overlaps: dict[str, Any]) -> dict[str, Any]:
    required_missing = sum(
        count
        for task_counts in dataset_summary["missing_required"].values()
        for count in task_counts.values()
    )
    id_overlap = sum(item["id_overlap"] for item in overlaps.values())
    prompt_overlap = sum(item["prompt_input_overlap"] for item in overlaps.values())
    total = dataset_summary["records"]
    min_task = min(dataset_summary["task_counts"].values())
    gates = {
        "min_records_60000": total >= 60000,
        "all_tasks_present": set(dataset_summary["task_counts"]) >= set(REQUIRED_BY_TASK),
        "min_task_records_4000": min_task >= 4000,
        "required_fields_complete": required_missing == 0,
        "no_split_id_overlap": id_overlap == 0,
        "no_split_prompt_input_overlap": prompt_overlap == 0,
        "official_splits_present": set(split_summaries) >= {"train", "validation", "test", "ood_test"},
    }
    return {
        "status": "pass" if all(gates.values()) else "fail",
        "gates": gates,
        "required_missing_total": required_missing,
        "split_id_overlap_total": id_overlap,
        "split_prompt_input_overlap_total": prompt_overlap,
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Data Quality Optimization Report",
        "",
        f"Generated: {payload['generated_at']}",
        f"Dataset: `{payload['dataset']}`",
        "",
        "## Verdict",
        "",
        f"- Status: `{payload['gates']['status']}`",
        f"- Records: {payload['dataset_summary']['records']:,}",
        f"- Required missing fields: {payload['gates']['required_missing_total']}",
        f"- Split ID overlap: {payload['gates']['split_id_overlap_total']}",
        f"- Split prompt/input overlap: {payload['gates']['split_prompt_input_overlap_total']}",
        "",
        "## Task Counts",
        "",
    ]
    for task, count in sorted(payload["dataset_summary"]["task_counts"].items()):
        lines.append(f"- {task}: {count:,}")
    lines.extend(["", "## Network Coverage", ""])
    for network, count in sorted(payload["dataset_summary"]["network_counts"].items()):
        lines.append(f"- {network}: {count:,}")
    lines.extend(["", "## Split Overlap", ""])
    for pair, row in sorted(payload["split_overlap"].items()):
        lines.append(
            f"- {pair}: ids={row['id_overlap']}, scenarios={row['scenario_overlap']}, prompt_inputs={row['prompt_input_overlap']}"
        )
    lines.extend(["", "## Label Distribution", ""])
    for task, labels in sorted(payload["dataset_summary"]["label_distribution"].items()):
        formatted = ", ".join(f"{label}={count}" for label, count in sorted(labels.items()))
        lines.append(f"- {task}: {formatted}")
    lines.extend(["", "## Gate Details", ""])
    for gate, ok in sorted(payload["gates"]["gates"].items()):
        lines.append(f"- {gate}: {'PASS' if ok else 'FAIL'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus9.jsonl")
    parser.add_argument("--train", default="data/v1.2_paper_plus9_train.jsonl")
    parser.add_argument("--validation", default="data/v1.2_paper_plus9_validation.jsonl")
    parser.add_argument("--test", default="data/v1.2_paper_plus9_test.jsonl")
    parser.add_argument("--ood", default="data/v1.2_paper_plus9_ood_test.jsonl")
    parser.add_argument("--output-json", default="reports/data_quality_optimization_v1.2_plus9.json")
    parser.add_argument("--output-md", default="reports/data_quality_optimization_v1.2_plus9.md")
    args = parser.parse_args()

    dataset_rows = read_jsonl(ROOT / args.dataset)
    splits = {
        "train": load_split(args.train),
        "validation": load_split(args.validation),
        "test": load_split(args.test),
        "ood_test": load_split(args.ood),
    }
    split_summaries = {name: summarize_rows(rows) for name, rows in splits.items()}
    overlaps = split_overlap(splits)
    dataset_summary = summarize_rows(dataset_rows)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "split_paths": {
            "train": args.train,
            "validation": args.validation,
            "test": args.test,
            "ood_test": args.ood,
        },
        "dataset_summary": dataset_summary,
        "split_summaries": split_summaries,
        "split_overlap": overlaps,
        "gates": gate_report(dataset_summary, split_summaries, overlaps),
    }
    write_json(ROOT / args.output_json, payload)
    ensure_dirs((ROOT / args.output_md).parent)
    write_markdown(ROOT / args.output_md, payload)


if __name__ == "__main__":
    main()
