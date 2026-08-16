#!/usr/bin/env python3
"""Run the target-hidden classification diagnostic on the exact-surface split.

The split is defined by ID-only manifests produced by
``create_exact_surface_components.py``.  This script materializes no second
copy of the canonical table: it joins the manifests to the hashed canonical
JSONL table in memory and reuses the deterministic linear TF--IDF baseline.
The result is a lexical-stress diagnostic, not a semantic or physical
independence claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json
from run_target_hidden_classification import LABEL_FIELDS, TASKS, evaluate_task


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ids(path: Path) -> list[str]:
    return [str(row["id"]) for row in read_jsonl(path)]


def materialize(rows_by_id: dict[str, dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    missing = [value for value in ids if value not in rows_by_id]
    if missing:
        raise RuntimeError(f"manifest IDs missing from canonical table: {missing[:3]}")
    return [rows_by_id[value] for value in ids]


def plot(rows: list[dict[str, Any]], path: Path) -> None:
    ensure_dirs(path.parent)
    profiles = ["full_contract", "target_hidden", "instruction_only"]
    tasks = list(TASKS)
    task_labels = {
        "dispatcher_intent_tool_call": "Intent routing",
        "operation_ticket_check": "Ticket check",
        "regulation_compliance_check": "Compliance check",
    }
    fig, axes = plt.subplots(len(tasks), 1, figsize=(8.2, 6.5), sharex=True)
    x = np.arange(len(profiles))
    for ax, task in zip(np.atleast_1d(axes), tasks):
        subset = {row["input_profile"]: row for row in rows if row["task_type"] == task}
        values = [subset[profile]["macro_f1"] for profile in profiles]
        ax.bar(x, values, width=0.62, color=["#1b4965", "#5fa8d3", "#cae9ff"])
        ax.set_ylim(0, 1.12)
        ax.set_ylabel(task_labels.get(task, task.replace("_", " ")) + "\nmacro-F1")
        ax.grid(axis="y", alpha=0.25)
        for index, value in enumerate(values):
            ax.text(index, value + 0.025, f"{value:.3f}", ha="center", va="bottom", fontsize=8)
    axes[-1].set_xticks(x)
    axes[-1].set_xticklabels(["Full contract", "Target hidden", "Instruction only"])
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Exact-surface target-hidden classification diagnostic",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "This CPU diagnostic evaluates three closed-label tasks after assigning every character-5-gram component to one split. The target-hidden profile removes target-carrying fields before fitting a deterministic character TF--IDF linear SVC. The scores quantify learnability under a lexical near-duplicate stress split; they do not establish semantic independence, physical-label validity, or dispatch competence.",
        "",
        "| task | input profile | train | test | macro-F1 | 95% CI | balanced accuracy |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: |",
    ]
    for row in report["rows"]:
        low, high = row["macro_f1_ci95"]
        lines.append(
            f"| {row['task_type']} | {row['input_profile']} | {row['n_train']} | {row['n_test']} | {row['macro_f1']:.4f} | [{low:.4f}, {high:.4f}] | {row['balanced_accuracy']:.4f} |"
        )
    lines.extend(["", "The exact-surface component report and the canonical input hash are recorded in the JSON receipt.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--split-report", default="reports/exact_surface_component_split_v1.2_sd_core.json")
    parser.add_argument("--output-json", default="reports/exact_surface_target_hidden_classification_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/exact_surface_target_hidden_classification_v1.2_sd_core.md")
    parser.add_argument("--output-figure", default="figures/sd_core_quality/fig_exact_surface_target_hidden.png")
    args = parser.parse_args()

    canonical_path = ROOT / args.canonical
    canonical_rows = read_jsonl(canonical_path)
    rows_by_id = {str(row["id"]): row for row in canonical_rows}
    split_report_path = ROOT / args.split_report
    split_report = json.loads(split_report_path.read_text(encoding="utf-8"))
    split_paths = split_report["output_paths"]
    split_rows = {
        name: materialize(rows_by_id, load_ids(ROOT / path))
        for name, path in split_paths.items()
    }
    train_rows = split_rows["train"]
    test_rows = split_rows["test"]
    train_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    test_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train_rows:
        if row.get("task_type") in LABEL_FIELDS:
            train_by_task[str(row["task_type"])].append(row)
    for row in test_rows:
        if row.get("task_type") in LABEL_FIELDS:
            test_by_task[str(row["task_type"])].append(row)

    result_rows = []
    for profile in ("full_contract", "target_hidden", "instruction_only"):
        for task in TASKS:
            if not train_by_task.get(task) or not test_by_task.get(task):
                continue
            item = evaluate_task(train_by_task[task], test_by_task[task], task, profile)
            item.update({"split": "exact_surface_component", "train_manifest": split_paths["train"], "test_manifest": split_paths["test"]})
            result_rows.append(item)
            print(f"[exact-surface-baseline] profile={profile} task={task} macro_f1={item['macro_f1']:.4f}", flush=True)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if len(result_rows) == 9 else "fail",
        "objective": "target-hidden classification on an exact character-5-gram component split",
        "canonical": {"path": args.canonical, "sha256": sha256(canonical_path), "records": len(canonical_rows)},
        "split_report": {"path": args.split_report, "sha256": sha256(split_report_path)},
        "split_counts": {name: len(values) for name, values in split_rows.items()},
        "rows": result_rows,
        "figure": args.output_figure,
        "interpretation": "Lexical near-duplicate stress diagnostic only; no semantic independence, physical-label validity, or operational dispatch claim.",
    }
    plot(result_rows, ROOT / args.output_figure)
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)
    print(json.dumps({"status": report["status"], "rows": len(result_rows), "split_counts": report["split_counts"]}, ensure_ascii=False), flush=True)
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
