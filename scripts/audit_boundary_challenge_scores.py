#!/usr/bin/env python3
"""Audit high-score challenge results by boundary-sample family."""

from __future__ import annotations

import argparse
import collections
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_family(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("augmentation_type") or "base")


def label_for(row: dict[str, Any]) -> str:
    if row.get("task_type") == "dispatcher_intent_tool_call":
        return str(row.get("intent"))
    if row.get("task_type") == "operation_ticket_check":
        return str(row.get("compliance_label"))
    return str(row.get("output"))


def macro_f1(gold: list[str], pred: list[str]) -> float:
    labels = sorted(set(gold) | set(pred))
    values = []
    for label in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, pred) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, pred) if g == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        values.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(values) / len(values) if values else 0.0


def summarize_subset(rows: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    matched = [row for row in rows if row.get("id") in predictions]
    gold = [label_for(row) for row in matched]
    pred = [str(predictions[row["id"]].get("prediction")) for row in matched]
    correct = [g == p for g, p in zip(gold, pred)]
    confusion = collections.Counter(f"{g} -> {p}" for g, p in zip(gold, pred))
    return {
        "records": len(rows),
        "matched_predictions": len(matched),
        "accuracy": sum(correct) / len(correct) if correct else None,
        "macro_f1": macro_f1(gold, pred) if matched else None,
        "label_counts": dict(collections.Counter(gold)),
        "prediction_counts": dict(collections.Counter(pred)),
        "confusion_top": confusion.most_common(20),
    }


def load_prediction_map(path: Path) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in read_jsonl(path)}


def plot(rows: list[dict[str, Any]], figure_path: Path) -> None:
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    plot_rows = [row for row in rows if row["macro_f1"] is not None and row["records"] >= 20]
    plot_rows = sorted(plot_rows, key=lambda item: (item["task_type"], item["family"]))
    labels = [f"{row['task_type']}\n{row['family']}" for row in plot_rows]
    values = [row["macro_f1"] for row in plot_rows]
    colors = ["#3b6fb6" if row["family"] == "base" else "#d07c2c" for row in plot_rows]
    fig_width = max(8.0, min(18.0, 0.58 * len(plot_rows)))
    fig, ax = plt.subplots(figsize=(fig_width, 4.8))
    ax.bar(range(len(values)), values, color=colors, edgecolor="#222222", linewidth=0.5)
    ax.set_ylim(0.0, 1.04)
    ax.set_ylabel("Macro-F1")
    ax.set_title("Boundary challenge subset scores")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=55, ha="right", fontsize=7)
    ax.grid(axis="y", color="#dddddd", linewidth=0.7)
    for idx, value in enumerate(values):
        ax.text(idx, min(1.02, value + 0.015), f"{value:.3f}", ha="center", va="bottom", fontsize=7)
    fig.tight_layout()
    fig.savefig(figure_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--challenge-test", default="data/v1.2_sd_core_challenge_test_en.jsonl")
    parser.add_argument(
        "--operation-predictions",
        default="benchmark/v1.2_sd_core_challenge_transformer_operation_ticket_check_test_predictions.jsonl",
    )
    parser.add_argument(
        "--dispatcher-predictions",
        default="benchmark/v1.2_sd_core_challenge_transformer_dispatcher_intent_tool_call_test_predictions.jsonl",
    )
    parser.add_argument("--output-json", default="reports/boundary_challenge_score_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/boundary_challenge_score_audit_v1.2_sd_core.md")
    parser.add_argument("--figure", default="figures/sd_core/fig_boundary_challenge_subset_scores.png")
    args = parser.parse_args()

    challenge_rows = read_jsonl(ROOT / args.challenge_test)
    prediction_maps = {
        "operation_ticket_check": load_prediction_map(ROOT / args.operation_predictions),
        "dispatcher_intent_tool_call": load_prediction_map(ROOT / args.dispatcher_predictions),
    }

    rows = []
    for task_type, predictions in prediction_maps.items():
        task_rows = [row for row in challenge_rows if row.get("task_type") == task_type]
        by_family: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for row in task_rows:
            by_family[safe_family(row)].append(row)
        for family, family_rows in sorted(by_family.items()):
            summary = summarize_subset(family_rows, predictions)
            rows.append({"task_type": task_type, "family": family, **summary})
        boundary_rows = [row for row in task_rows if safe_family(row) != "base"]
        rows.append({"task_type": task_type, "family": "all_boundary_families", **summarize_subset(boundary_rows, predictions)})
        rows.append({"task_type": task_type, "family": "all_challenge_records", **summarize_subset(task_rows, predictions)})

    plot(rows, ROOT / args.figure)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "challenge_test": args.challenge_test,
        "prediction_files": {
            "operation_ticket_check": args.operation_predictions,
            "dispatcher_intent_tool_call": args.dispatcher_predictions,
        },
        "rows": rows,
        "figure": args.figure,
        "status": "pass",
        "claim_guidance": (
            "Use these subset results to interpret near-perfect aggregate F1. "
            "Boundary-family scores remain validation evidence for constrained labels, "
            "not evidence of autonomous dispatch reasoning."
        ),
    }
    write_json(ROOT / args.output_json, report)
    md_lines = [
        "# Boundary Challenge Score Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Figure: `{args.figure}`",
        "",
        "## Subset Results",
        "",
        "| Task | Family | Records | Macro-F1 | Accuracy |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        macro = "NA" if row["macro_f1"] is None else f"{row['macro_f1']:.4f}"
        acc = "NA" if row["accuracy"] is None else f"{row['accuracy']:.4f}"
        md_lines.append(f"| {row['task_type']} | {row['family']} | {row['records']} | {macro} | {acc} |")
    md_lines.extend(["", "## Claim Guidance", "", report["claim_guidance"]])
    (ROOT / args.output_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
