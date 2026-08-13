#!/usr/bin/env python3
"""Create a global source-group-disjoint strict split for SD-core audits."""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, stable_shuffle, write_json, write_jsonl

SPLITS = ("train", "validation", "test")
RATIOS = {"train": 0.8, "validation": 0.1, "test": 0.1}
CLASSIFICATION_LABEL_FIELDS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root


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


def provenance_keys(row: dict[str, Any]) -> list[str]:
    meta = row.get("metadata") or {}
    raw = [
        ("source_group", source_group(row)),
        ("source_record_id", meta.get("source_record_id")),
        ("scenario_id", row.get("scenario_id")),
        ("source_simulation_case_id", row.get("source_simulation_case_id")),
        ("operation_group", meta.get("operation_group")),
    ]
    keys = [f"{name}::{str(value).strip()}" for name, value in raw if str(value or "").strip()]
    if not keys:
        keys = [f"record::{row.get('id')}"]
    return keys


def target_key(row: dict[str, Any]) -> str:
    task = str(row.get("task_type") or "unknown")
    field = CLASSIFICATION_LABEL_FIELDS.get(task)
    if field:
        return f"{task}::{row.get(field)}"
    if task == "intelligent_data_query":
        query = row.get("structured_query") or {}
        return f"{task}::{query.get('filter') or query.get('select') or 'query'}"
    return f"{task}::__task__"


def build_components(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uf = UnionFind()
    row_keys: list[list[str]] = []
    for row in rows:
        keys = provenance_keys(row)
        row_keys.append(keys)
        uf.find(keys[0])
        for key in keys[1:]:
            uf.union(keys[0], key)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row, keys in zip(rows, row_keys, strict=True):
        grouped[uf.find(keys[0])].append(row)
    components = []
    for key, comp_rows in grouped.items():
        components.append(
            {
                "group_key": key,
                "rows": comp_rows,
                "size": len(comp_rows),
                "task_counts": Counter(str(row.get("task_type") or "unknown") for row in comp_rows),
                "target_counts": Counter(target_key(row) for row in comp_rows),
                "network_counts": Counter(str(row.get("network_model") or "no_network") for row in comp_rows),
            }
        )
    return components


def assign_components(components: list[dict[str, Any]], seed: int) -> dict[str, list[dict[str, Any]]]:
    total = sum(item["size"] for item in components)
    task_totals = Counter()
    target_totals = Counter()
    for item in components:
        task_totals.update(item["task_counts"])
        target_totals.update(item["target_counts"])
    desired_total = {name: total * RATIOS[name] for name in SPLITS}
    desired_task = {
        name: {task: count * RATIOS[name] for task, count in task_totals.items()}
        for name in SPLITS
    }
    desired_target = {
        name: {target: count * RATIOS[name] for target, count in target_totals.items()}
        for name in SPLITS
    }
    ordered = stable_shuffle(components, seed)
    ordered.sort(key=lambda item: (min((target_totals[t] for t in item["target_counts"]), default=total), -item["size"]))
    out: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLITS}
    current_total = {name: 0 for name in SPLITS}
    current_task = {name: Counter() for name in SPLITS}
    current_target = {name: Counter() for name in SPLITS}
    for item in ordered:
        best_name = "train"
        best_score: float | None = None
        for name in SPLITS:
            score = (desired_total[name] - current_total[name]) / max(desired_total[name], 1.0)
            for task, count in item["task_counts"].items():
                score += 0.35 * count / item["size"] * (desired_task[name][task] - current_task[name][task]) / max(desired_task[name][task], 1.0)
            for target, count in item["target_counts"].items():
                score += 1.10 * count / item["size"] * (desired_target[name][target] - current_target[name][target]) / max(desired_target[name][target], 1.0)
            projected = current_total[name] + item["size"]
            if name != "train" and projected > desired_total[name] * 1.12:
                score -= 2.0 * (projected - desired_total[name] * 1.12) / max(desired_total[name], 1.0)
            if best_score is None or score > best_score:
                best_score = score
                best_name = name
        out[best_name].append(item)
        current_total[best_name] += item["size"]
        current_task[best_name].update(item["task_counts"])
        current_target[best_name].update(item["target_counts"])
    return out


def flatten(split_components: dict[str, list[dict[str, Any]]], seed: int) -> dict[str, list[dict[str, Any]]]:
    return {
        name: stable_shuffle([row for item in items for row in item["rows"]], seed + idx * 1009)
        for idx, (name, items) in enumerate(split_components.items())
    }


def value_sets(rows: list[dict[str, Any]], kind: str) -> set[str]:
    if kind == "record_id":
        return {str(row.get("id")) for row in rows if row.get("id")}
    if kind == "source_group":
        return {source_group(row) for row in rows if source_group(row)}
    if kind == "scenario_id":
        return {str(row.get("scenario_id") or row.get("source_simulation_case_id")) for row in rows if row.get("scenario_id") or row.get("source_simulation_case_id")}
    if kind == "source_record_id":
        return {str((row.get("metadata") or {}).get("source_record_id")) for row in rows if (row.get("metadata") or {}).get("source_record_id")}
    if kind == "operation_group":
        return {str((row.get("metadata") or {}).get("operation_group")) for row in rows if (row.get("metadata") or {}).get("operation_group")}
    if kind == "source_simulation_case_id":
        return {str(row.get("source_simulation_case_id")) for row in rows if row.get("source_simulation_case_id")}
    if kind == "target":
        return {target_key(row) for row in rows}
    raise ValueError(kind)


def overlap_counts(splits: dict[str, list[dict[str, Any]]], kind: str) -> dict[str, int]:
    sets = {name: value_sets(rows, kind) for name, rows in splits.items()}
    out: dict[str, int] = {}
    names = list(sets)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            out[f"{left}__{right}"] = len(sets[left] & sets[right])
    return out


def split_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "records": len(rows),
        "source_groups": len(value_sets(rows, "source_group")),
        "scenarios": len(value_sets(rows, "scenario_id")),
        "task_counts": dict(Counter(str(row.get("task_type") or "unknown") for row in rows)),
        "target_counts": dict(Counter(target_key(row) for row in rows)),
        "network_counts": dict(Counter(str(row.get("network_model") or "no_network") for row in rows)),
    }


def plot_overlap(report: dict[str, Any], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    labels, values = [], []
    for kind, overlaps in report["overlaps"].items():
        for pair, count in sorted(overlaps.items()):
            labels.append(f"{kind}\n{pair.replace('__', ' / ')}")
            values.append(count)
    fig, ax = plt.subplots(figsize=(10, max(5, 0.42 * len(labels))))
    ax.barh(np.arange(len(labels)), values, color="#2563eb")
    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Overlap count")
    ax.set_title("Strict Source-Group Split Overlap Checks")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    path = figure_dir / "fig_strict_split_group_overlap.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return {"strict_group_overlap": str(path.relative_to(ROOT))}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Strict Source-Group Split",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This split is an additional stress split. It assigns global provenance-connected components to train, validation, or test so source groups and scenario identifiers do not cross strict split boundaries.",
        "",
        "## Split Summary",
        "",
        "| split | records | source groups | scenarios |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, summary in report["splits"].items():
        lines.append(f"| {name} | {summary['records']} | {summary['source_groups']} | {summary['scenarios']} |")
    lines.extend(["", "## Hard Gates", ""])
    for key, value in report["hard_gates"].items():
        lines.append(f"- `{key}`: {'pass' if value else 'fail'}")
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--seed", type=int, default=2061)
    parser.add_argument("--train-output", default="data/v1.2_sd_core_strict_train.jsonl")
    parser.add_argument("--validation-output", default="data/v1.2_sd_core_strict_validation.jsonl")
    parser.add_argument("--test-output", default="data/v1.2_sd_core_strict_test.jsonl")
    parser.add_argument("--report-json", default="reports/strict_source_group_split_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/strict_source_group_split_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()
    rows = read_jsonl(ROOT / args.input)
    components = build_components(rows)
    split_components = assign_components(components, args.seed)
    splits = flatten(split_components, args.seed)
    output_paths = {
        "train": args.train_output,
        "validation": args.validation_output,
        "test": args.test_output,
    }
    overlaps = {
        "record_id": overlap_counts(splits, "record_id"),
        "source_group": overlap_counts(splits, "source_group"),
        "scenario_id": overlap_counts(splits, "scenario_id"),
        "source_record_id": overlap_counts(splits, "source_record_id"),
        "source_simulation_case_id": overlap_counts(splits, "source_simulation_case_id"),
        "operation_group": overlap_counts(splits, "operation_group"),
    }
    hard_gates = {
        "all_splits_nonempty": all(len(splits[name]) > 0 for name in SPLITS),
        "record_id_disjoint": all(count == 0 for count in overlaps["record_id"].values()),
        "source_group_disjoint": all(count == 0 for count in overlaps["source_group"].values()),
        "scenario_id_disjoint": all(count == 0 for count in overlaps["scenario_id"].values()),
        "source_record_id_disjoint": all(count == 0 for count in overlaps["source_record_id"].values()),
        "source_simulation_case_id_disjoint": all(count == 0 for count in overlaps["source_simulation_case_id"].values()),
        "operation_group_disjoint": all(count == 0 for count in overlaps["operation_group"].values()),
        "all_tasks_in_train": len(set(split_summary(splits["train"])["task_counts"])) == 6,
        "classification_targets_in_test": all(
            any(key.startswith(f"{task}::") for key in split_summary(splits["test"])["target_counts"])
            for task in CLASSIFICATION_LABEL_FIELDS
        ),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "fail",
        "input": args.input,
        "input_sha256": sha256(ROOT / args.input),
        "split_paths": output_paths,
        "component_count": len(components),
        "split_policy": "global provenance connected components over source_group, source_record_id, scenario_id, source_simulation_case_id, and operation_group",
        "splits": {name: split_summary(rows) for name, rows in splits.items()},
        "overlaps": overlaps,
        "hard_gates": hard_gates,
    }
    if report["status"] == "pass":
        for name, rel in output_paths.items():
            write_jsonl(ROOT / rel, splits[name])
        report["output_sha256"] = {name: sha256(ROOT / rel) for name, rel in output_paths.items()}
    else:
        report["output_sha256"] = {}
    report["figures"] = plot_overlap(report, ROOT / args.figure_dir)
    write_json(ROOT / args.report_json, report)
    write_markdown(ROOT / args.report_md, report)
    print({"status": report["status"], "splits": {name: len(rows) for name, rows in splits.items()}, "components": len(components)})
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
