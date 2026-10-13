#!/usr/bin/env python3
"""Audit split, label, network, severity, and scenario support for SD-core."""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, write_json

LABEL_FIELDS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def source_group(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    return str(
        meta.get("source_group")
        or meta.get("source_record_id")
        or row.get("source_simulation_case_id")
        or row.get("scenario_id")
        or row.get("id")
        or ""
    )


def severity(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    return str(meta.get("severity_level") or "no_severity")


def network(row: dict[str, Any]) -> str:
    return str(row.get("network_model") or "rule_or_procedure")


def task_label(row: dict[str, Any]) -> str | None:
    task = str(row.get("task_type") or "")
    field = LABEL_FIELDS.get(task)
    if not field:
        return None
    value = row.get(field)
    return None if value is None else str(value)


def distribution(counter: Counter[str], labels: list[str]) -> list[float]:
    total = sum(counter.values())
    if total <= 0:
        return [0.0 for _ in labels]
    return [counter.get(label, 0) / total for label in labels]


def js_divergence(left: Counter[str], right: Counter[str]) -> float | None:
    labels = sorted(set(left) | set(right))
    if not labels or sum(left.values()) == 0 or sum(right.values()) == 0:
        return None
    p = distribution(left, labels)
    q = distribution(right, labels)
    m = [(a + b) / 2.0 for a, b in zip(p, q, strict=True)]

    def kl(a: list[float], b: list[float]) -> float:
        total = 0.0
        for x, y in zip(a, b, strict=True):
            if x > 0 and y > 0:
                total += x * math.log2(x / y)
        return total

    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    label_counts: dict[str, dict[str, int]] = {}
    for task, field in LABEL_FIELDS.items():
        label_counts[task] = dict(Counter(str(row.get(field)) for row in rows if row.get("task_type") == task and row.get(field) is not None))
    scenario_ids = {
        str(row.get("scenario_id") or row.get("source_simulation_case_id"))
        for row in rows
        if row.get("scenario_id") or row.get("source_simulation_case_id")
    }
    return {
        "records": len(rows),
        "task_counts": dict(Counter(str(row.get("task_type") or "unknown") for row in rows)),
        "network_counts": dict(Counter(network(row) for row in rows)),
        "severity_counts": dict(Counter(severity(row) for row in rows)),
        "source_groups": len({source_group(row) for row in rows if source_group(row)}),
        "scenario_ids": len(scenario_ids),
        "label_counts": label_counts,
    }


def load_splits(paths: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    out = {}
    for name, rel in paths.items():
        path = ROOT / rel
        if path.exists():
            out[name] = read_jsonl(path)
    return out


def write_csv_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def table_rows(summaries: dict[str, dict[str, Any]], key: str, value_name: str) -> list[dict[str, Any]]:
    values = sorted({item for summary in summaries.values() for item in summary.get(key, {})})
    rows = []
    for value in values:
        row: dict[str, Any] = {value_name: value}
        for split_name, summary in summaries.items():
            row[split_name] = summary.get(key, {}).get(value, 0)
        rows.append(row)
    return rows


def scenario_summary(metadata: dict[str, Any], dataset_rows: list[dict[str, Any]]) -> dict[str, Any]:
    simulation = metadata.get("simulation", {})
    scenario_rows = [row for row in dataset_rows if row.get("scenario_id") or row.get("source_simulation_case_id")]
    by_network = Counter(network(row) for row in scenario_rows)
    by_contingency = Counter()
    for row in scenario_rows:
        sid = str(row.get("scenario_id") or row.get("source_simulation_case_id") or "")
        if "_n2_" in sid:
            by_contingency["n2_branch_outage"] += 1
        elif "_n1_" in sid:
            by_contingency["n1_branch_outage"] += 1
        elif "gen" in sid:
            by_contingency["generator_perturbation"] += 1
        else:
            by_contingency["base_or_other"] += 1
    return {
        "metadata_total_scenarios": simulation.get("total_scenarios"),
        "metadata_converged_scenarios": simulation.get("converged_scenarios"),
        "systems": simulation.get("systems", []),
        "config": simulation.get("config", {}),
        "record_level_network_counts": dict(by_network),
        "record_level_contingency_surface_counts": dict(by_contingency),
    }


def plot_label_support(report: dict[str, Any], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    label_rows = report["classification_label_support"]
    labels = [f"{row['split']}\n{row['task']}\n{row['label']}" for row in label_rows]
    values = [row["count"] for row in label_rows]
    fig, ax = plt.subplots(figsize=(13, max(6, 0.20 * len(labels))))
    ax.barh(np.arange(len(labels)), values, color="#2563eb")
    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Records")
    ax.set_title("Classification Label Support Across Splits")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    support_path = figure_dir / "fig_label_split_support.png"
    fig.savefig(support_path, dpi=220)
    plt.close(fig)

    split_names = list(report["split_summaries"])
    tasks = sorted({task for summary in report["split_summaries"].values() for task in summary.get("task_counts", {})})
    x = np.arange(len(tasks))
    width = 0.8 / max(len(split_names), 1)
    fig, ax = plt.subplots(figsize=(13, 5.5))
    for idx, split_name in enumerate(split_names):
        counts = [report["split_summaries"][split_name].get("task_counts", {}).get(task, 0) for task in tasks]
        ax.bar(x + (idx - (len(split_names) - 1) / 2) * width, counts, width=width, label=split_name)
    ax.set_xticks(x)
    ax.set_xticklabels(tasks, rotation=20, ha="right")
    ax.set_ylabel("Records")
    ax.set_title("Task Coverage by Split")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    coverage_path = figure_dir / "fig_split_task_network_coverage.png"
    fig.savefig(coverage_path, dpi=220)
    plt.close(fig)
    return {
        "label_support": str(support_path.relative_to(ROOT)),
        "split_task_coverage": str(coverage_path.relative_to(ROOT)),
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Label and Split Support Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This audit summarizes split-level task, label, network, severity, source-group, and scenario support. It is a technical validation artifact for reuse and benchmark reporting.",
        "",
        "## Gates",
        "",
    ]
    for key, value in report["hard_gates"].items():
        lines.append(f"- `{key}`: {'pass' if value else 'fail'}")
    lines.extend(["", "## Split Summary", "", "| split | records | source groups | scenarios | min classification label support |", "| --- | ---: | ---: | ---: | ---: |"])
    for split_name, summary in report["split_summaries"].items():
        lines.append(
            f"| {split_name} | {summary['records']} | {summary['source_groups']} | {summary['scenario_ids']} | {report['min_label_support_by_split'].get(split_name, 0)} |"
        )
    lines.extend(["", "## Label Distribution Shift", "", "| family | task | comparison | Jensen-Shannon divergence |", "| --- | --- | --- | ---: |"])
    for row in report["label_distribution_shift"]:
        value = row["js_divergence"]
        lines.append(f"| {row['family']} | {row['task']} | {row['comparison']} | {'NA' if value is None else f'{value:.4f}'} |")
    lines.extend(["", "## Scenario Construction Summary", ""])
    scenario = report["scenario_construction_summary"]
    lines.append(f"- Candidate scenarios in metadata: {scenario.get('metadata_total_scenarios')}")
    lines.append(f"- Converged scenarios in metadata: {scenario.get('metadata_converged_scenarios')}")
    lines.append(f"- Systems: {', '.join(scenario.get('systems') or [])}")
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--metadata", default="metadata/dataset_metadata.json")
    parser.add_argument("--output-json", default="reports/label_split_support_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/label_split_support_audit_v1.2_sd_core.md")
    parser.add_argument("--task-split-csv", default="reports/label_split_support_task_by_split_v1.2_sd_core.csv")
    parser.add_argument("--network-split-csv", default="reports/label_split_support_network_by_split_v1.2_sd_core.csv")
    parser.add_argument("--severity-split-csv", default="reports/label_split_support_severity_by_split_v1.2_sd_core.csv")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()

    split_paths = {
        "train": "data/v1.2_sd_core_train.jsonl",
        "validation": "data/v1.2_sd_core_validation.jsonl",
        "test": "data/v1.2_sd_core_test.jsonl",
        "ood_test": "data/v1.2_sd_core_ood_test.jsonl",
        "challenge_train": "data/v1.2_sd_core_challenge_train.jsonl",
        "challenge_validation": "data/v1.2_sd_core_challenge_validation.jsonl",
        "challenge_test": "data/v1.2_sd_core_challenge_test.jsonl",
        "strict_train": "data/v1.2_sd_core_strict_train.jsonl",
        "strict_validation": "data/v1.2_sd_core_strict_validation.jsonl",
        "strict_test": "data/v1.2_sd_core_strict_test.jsonl",
        "proxyreduced_train": "data/v1.2_sd_core_proxyreduced_train.jsonl",
        "proxyreduced_validation": "data/v1.2_sd_core_proxyreduced_validation.jsonl",
        "proxyreduced_test": "data/v1.2_sd_core_proxyreduced_test.jsonl",
        "strict_proxyreduced_train": "data/v1.2_sd_core_strict_proxyreduced_train.jsonl",
        "strict_proxyreduced_validation": "data/v1.2_sd_core_strict_proxyreduced_validation.jsonl",
        "strict_proxyreduced_test": "data/v1.2_sd_core_strict_proxyreduced_test.jsonl",
    }
    dataset_rows = read_jsonl(ROOT / args.dataset)
    metadata = read_json(ROOT / args.metadata)
    splits = load_splits(split_paths)
    summaries = {name: summarize_rows(rows) for name, rows in splits.items()}

    label_support_rows = []
    min_by_split = {}
    for split_name, summary in summaries.items():
        counts = []
        for task, label_counts in summary["label_counts"].items():
            for label, count in sorted(label_counts.items()):
                label_support_rows.append({"split": split_name, "task": task, "label": label, "count": count})
                counts.append(count)
        min_by_split[split_name] = min(counts) if counts else 0

    families = {
        "standard": ("train", "validation", "test"),
        "challenge": ("challenge_train", "challenge_validation", "challenge_test"),
        "strict": ("strict_train", "strict_validation", "strict_test"),
        "proxyreduced": ("proxyreduced_train", "proxyreduced_validation", "proxyreduced_test"),
        "strict_proxyreduced": ("strict_proxyreduced_train", "strict_proxyreduced_validation", "strict_proxyreduced_test"),
    }
    shift_rows = []
    for family, names in families.items():
        if not all(name in summaries for name in names):
            continue
        train_name, val_name, test_name = names
        for task in LABEL_FIELDS:
            train_counts = Counter(summaries[train_name]["label_counts"].get(task, {}))
            for other_name in (val_name, test_name):
                other_counts = Counter(summaries[other_name]["label_counts"].get(task, {}))
                shift_rows.append(
                    {
                        "family": family,
                        "task": task,
                        "comparison": f"{train_name}__{other_name}",
                        "js_divergence": js_divergence(train_counts, other_counts),
                    }
                )

    required_splits = ("train", "validation", "test", "ood_test", "strict_train", "strict_validation", "strict_test")
    hard_gates = {
        "required_splits_present": all(name in summaries for name in required_splits),
        "no_empty_required_split": all(summaries.get(name, {}).get("records", 0) > 0 for name in required_splits),
        "classification_test_support_present": all(summaries.get("test", {}).get("label_counts", {}).get(task) for task in LABEL_FIELDS),
        "strict_classification_test_support_present": all(summaries.get("strict_test", {}).get("label_counts", {}).get(task) for task in LABEL_FIELDS),
        "network_coverage_reported": bool(summaries.get("train", {}).get("network_counts")),
        "severity_coverage_reported": bool(summaries.get("train", {}).get("severity_counts")),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "warn",
        "dataset": args.dataset,
        "split_paths": {name: path for name, path in split_paths.items() if name in summaries},
        "split_summaries": summaries,
        "classification_label_support": label_support_rows,
        "min_label_support_by_split": min_by_split,
        "label_distribution_shift": shift_rows,
        "scenario_construction_summary": scenario_summary(metadata, dataset_rows),
        "hard_gates": hard_gates,
    }
    report["figures"] = plot_label_support(report, ROOT / args.figure_dir)
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)
    task_rows = table_rows(summaries, "task_counts", "task_type")
    network_rows = table_rows(summaries, "network_counts", "network_model")
    severity_rows = table_rows(summaries, "severity_counts", "severity_level")
    split_columns = list(summaries)
    write_csv_rows(ROOT / args.task_split_csv, ["task_type", *split_columns], task_rows)
    write_csv_rows(ROOT / args.network_split_csv, ["network_model", *split_columns], network_rows)
    write_csv_rows(ROOT / args.severity_split_csv, ["severity_level", *split_columns], severity_rows)
    print({"status": report["status"], "splits": len(summaries), "label_rows": len(label_support_rows)})


if __name__ == "__main__":
    main()
