#!/usr/bin/env python3
"""Build and score a hard-boundary benchmark from released records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

from gridinstruct_utils import ROOT, ensure_dirs, write_json


TASK_LABEL = {
    "operation_ticket_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
    "regulation_compliance_check": "compliance_label",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def stable_float(key: str) -> float:
    value = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], 16)
    return value / float(16**12)


def normalize_surface(text: str) -> str:
    text = re.sub(r"\b(?:ieee|pegase|rte)\d+[_a-z0-9.-]*", "<scenario>", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "<num>", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def row_text(row: dict[str, Any]) -> str:
    input_obj = row.get("input")
    if isinstance(input_obj, dict):
        compact_input = {
            key: value
            for key, value in input_obj.items()
            if key
            in {
                "ticket_text",
                "equipment_state",
                "operation_context",
                "dispatch_permit_status",
                "monitoring_arrangement",
                "object_consistency",
                "key_checks",
                "utterance",
                "grid_state_summary",
                "proposed_action",
                "operator_goal",
                "observed_issues",
            }
        }
    else:
        compact_input = input_obj
    return normalize_surface(
        json.dumps(
            {
                "instruction": row.get("instruction"),
                "input": compact_input,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def is_boundary_candidate(row: dict[str, Any]) -> bool:
    task = row.get("task_type")
    text = row_text(row)
    meta = row.get("metadata") or {}
    if task == "operation_ticket_check":
        tokens = [
            "monitor",
            "supervisor",
            "permit",
            "not backfilled",
            "empty",
            "single-person",
            "immediate",
            "checked, but",
            "condition",
        ]
        return any(token in text for token in tokens)
    if task == "dispatcher_intent_tool_call":
        tokens = ["confirm", "recalculate", "check", "diagnose", "suggest", "redispatch", "mitigate", "violation"]
        return any(token in text for token in tokens)
    if task == "regulation_compliance_check":
        return str(meta.get("issue_profile") or "").lower() in {"voltage", "overload", "mixed"} or any(
            token in text for token in ["low voltage", "high voltage", "overload", "constrained", "monitor"]
        )
    return False


def select_balanced(rows: list[dict[str, Any]], task: str, max_per_label: int, test_fraction: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    label_field = TASK_LABEL[task]
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("task_type") != task or not row.get(label_field):
            continue
        if is_boundary_candidate(row):
            by_label[str(row[label_field])].append(row)
    label_cap = min(max_per_label, *(len(items) for items in by_label.values()))
    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    for label, items in sorted(by_label.items()):
        ordered = sorted(items, key=lambda item: stable_float(item["id"]))
        selected = ordered[:label_cap]
        test_count = max(1, int(round(len(selected) * test_fraction)))
        test_ids = {item["id"] for item in sorted(selected, key=lambda item: stable_float("test:" + item["id"]))[:test_count]}
        for item in selected:
            output = {
                "id": item["id"],
                "task_type": task,
                "label": item[label_field],
                "instruction": item.get("instruction"),
                "input": item.get("input"),
                "network_model": item.get("network_model"),
                "scenario_id": item.get("scenario_id"),
                "source_regulation_ids": item.get("source_regulation_ids"),
                "metadata": item.get("metadata"),
                "surface_text": row_text(item),
            }
            if item["id"] in test_ids:
                test.append(output)
            else:
                train.append(output)
    return train, test


def label_purity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_surface: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_surface[row["surface_text"]][str(row["label"])] += 1
    total = 0
    pure = 0
    for counter in by_surface.values():
        n = sum(counter.values())
        total += n
        pure += max(counter.values())
    return {
        "surface_groups": len(by_surface),
        "weighted_surface_label_purity": pure / total if total else 0.0,
        "repeated_surface_groups": sum(1 for counter in by_surface.values() if sum(counter.values()) > 1),
    }


def train_and_score(train: list[dict[str, Any]], test: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    x_train = [row["surface_text"] for row in train]
    y_train = [str(row["label"]) for row in train]
    x_test = [row["surface_text"] for row in test]
    y_test = [str(row["label"]) for row in test]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(3, 5), min_df=1, max_features=80000)
    train_matrix = vectorizer.fit_transform(x_train)
    test_matrix = vectorizer.transform(x_test)
    clf = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs", random_state=42)
    clf.fit(train_matrix, y_train)
    pred = clf.predict(test_matrix).tolist()
    predictions = []
    for row, gold, prediction in zip(test, y_test, pred):
        predictions.append(
            {
                "id": row["id"],
                "task_type": row["task_type"],
                "gold": gold,
                "prediction": prediction,
                "correct": gold == prediction,
            }
        )
    labels = sorted(set(y_train) | set(y_test))
    return (
        {
            "train_records": len(train),
            "test_records": len(test),
            "labels": labels,
            "train_label_counts": dict(Counter(y_train)),
            "test_label_counts": dict(Counter(y_test)),
            "accuracy": accuracy_score(y_test, pred),
            "balanced_accuracy": balanced_accuracy_score(y_test, pred),
            "macro_f1": f1_score(y_test, pred, average="macro"),
            "surface_purity": label_purity(train + test),
        },
        predictions,
    )


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Hard Boundary Benchmark",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This benchmark uses masked scenario identifiers and numeric values so that lightweight models cannot rely on exact case ids.",
        "",
        "## Results",
        "",
        "| task | train | test | macro-F1 | balanced accuracy | surface purity |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for task, metrics in report["tasks"].items():
        lines.append(
            f"| {task} | {metrics['train_records']} | {metrics['test_records']} | "
            f"{metrics['macro_f1']:.4f} | {metrics['balanced_accuracy']:.4f} | "
            f"{metrics['surface_purity']['weighted_surface_label_purity']:.4f} |"
        )
    lines.extend(["", "## Interpretation", "", report["interpretation"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_figure(report: dict[str, Any], path: Path) -> None:
    import matplotlib.pyplot as plt

    tasks = list(report["tasks"])
    scores = [report["tasks"][task]["macro_f1"] for task in tasks]
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    colors = ["#3b6ea8", "#5f8f4e", "#b66b3a"]
    ax.bar(range(len(tasks)), scores, color=colors[: len(tasks)], width=0.62)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Macro-F1")
    ax.set_xticks(range(len(tasks)))
    ax.set_xticklabels([task.replace("_", "\n") for task in tasks], fontsize=8)
    ax.set_title("Hard-boundary lightweight baseline")
    ax.grid(axis="y", alpha=0.25)
    for i, score in enumerate(scores):
        ax.text(i, min(score + 0.03, 1.02), f"{score:.3f}", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    ensure_dirs(path.parent)
    fig.savefig(path, dpi=240)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-dir", default="data/hard_boundary_v1.2_sd_core")
    parser.add_argument("--report-json", default="reports/hard_boundary_benchmark_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/hard_boundary_benchmark_v1.2_sd_core.md")
    parser.add_argument("--figure", default="figures/sd_core/fig_hard_boundary_benchmark.png")
    parser.add_argument("--max-per-label", type=int, default=900)
    parser.add_argument("--test-fraction", type=float, default=0.4)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.dataset)
    output_dir = ROOT / args.output_dir
    ensure_dirs(output_dir)
    report_tasks: dict[str, Any] = {}
    all_predictions: list[dict[str, Any]] = []
    for task in TASK_LABEL:
        train, test = select_balanced(rows, task, args.max_per_label, args.test_fraction)
        if len({row["label"] for row in train}) < 2 or len({row["label"] for row in test}) < 2:
            raise RuntimeError(f"Insufficient label coverage for {task}")
        write_jsonl(output_dir / f"{task}_train.jsonl", train)
        write_jsonl(output_dir / f"{task}_test.jsonl", test)
        metrics, predictions = train_and_score(train, test)
        write_jsonl(output_dir / f"{task}_predictions.jsonl", predictions)
        report_tasks[task] = metrics
        all_predictions.extend(predictions)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "dataset": args.dataset,
        "output_dir": args.output_dir,
        "model": "char_tfidf_logistic_regression_with_masked_scenarios_and_numbers",
        "tasks": report_tasks,
        "prediction_count": len(all_predictions),
        "interpretation": (
            "The hard-boundary benchmark is a diagnostic stress set for tasks with high standard-split scores. "
            "It is derived from released records, masks exact scenario identifiers and numeric values, and reports "
            "task-specific lightweight baseline scores beside label-support and surface-purity diagnostics."
        ),
    }
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.report_json, report)
    write_markdown(report, ROOT / args.report_md)
    write_figure(report, ROOT / args.figure)


if __name__ == "__main__":
    main()
