#!/usr/bin/env python3
"""Run lightweight multi-seed stability audits for SD-core classification tasks."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json

LABEL_FIELDS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def normalize(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").split())


def text_for(row: dict[str, Any]) -> str:
    parts = [
        f"task_type: {row.get('task_type')}",
        f"instruction: {normalize(row.get('instruction'))}",
        f"input: {normalize(row.get('input'))}",
    ]
    return "\n".join(parts)


def group_by_task(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        task = str(row.get("task_type") or "")
        if task in LABEL_FIELDS:
            grouped[task].append(row)
    return grouped


def evaluate_matrix(x_train: Any, x_test: Any, y_train: list[str], y_test: list[str], task: str, seed: int) -> dict[str, Any]:
    labels = sorted(set(y_train) | set(y_test))
    clf = SGDClassifier(
        loss="modified_huber",
        alpha=1e-5,
        penalty="l2",
        max_iter=1200,
        tol=1e-4,
        random_state=seed,
        class_weight="balanced",
        average=True,
    )
    clf.fit(x_train, y_train)
    pred = clf.predict(x_test).tolist()
    return {
        "seed": seed,
        "task_type": task,
        "n_train": len(y_train),
        "n_test": len(y_test),
        "label_counts": dict(Counter(y_test)),
        "accuracy": float(accuracy_score(y_test, pred)),
        "macro_f1": float(f1_score(y_test, pred, labels=labels, average="macro", zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y_test, pred)),
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["split_family"], row["task_type"])].append(row)
    summary = []
    for (family, task), items in sorted(grouped.items()):
        values = [float(item["macro_f1"]) for item in items]
        balanced = [float(item["balanced_accuracy"]) for item in items]
        summary.append(
            {
                "split_family": family,
                "task_type": task,
                "runs": len(items),
                "macro_f1_mean": mean(values),
                "macro_f1_std": pstdev(values) if len(values) > 1 else 0.0,
                "macro_f1_min": min(values),
                "macro_f1_max": max(values),
                "balanced_accuracy_mean": mean(balanced),
                "balanced_accuracy_std": pstdev(balanced) if len(balanced) > 1 else 0.0,
                "n_train": items[0]["n_train"],
                "n_test": items[0]["n_test"],
            }
        )
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    fieldnames = [
        "split_family",
        "task_type",
        "seed",
        "n_train",
        "n_test",
        "accuracy",
        "macro_f1",
        "balanced_accuracy",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def plot(summary: list[dict[str, Any]], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    labels = [f"{row['split_family']}\n{row['task_type']}" for row in summary]
    means = [row["macro_f1_mean"] for row in summary]
    stds = [row["macro_f1_std"] for row in summary]
    fig, ax = plt.subplots(figsize=(14, max(6, 0.34 * len(labels))))
    ax.barh(np.arange(len(labels)), means, xerr=stds, color="#2f855a", alpha=0.88, ecolor="#111827")
    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.03)
    ax.set_xlabel("Macro-F1 mean across seeds")
    ax.set_title("Lightweight Multi-Seed Classification Stability")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    path = figure_dir / "fig_linear_seed_stability.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return {"linear_seed_stability": str(path.relative_to(ROOT))}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Linear Multi-Seed Stability Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This audit trains stochastic linear text classifiers over released classification labels across multiple random seeds. It is a lightweight stability check; formal neural baselines remain in the main benchmark reports.",
        "",
        "## Gates",
        "",
    ]
    for key, value in report["hard_gates"].items():
        lines.append(f"- `{key}`: {'pass' if value else 'fail'}")
    lines.extend(["", "## Summary", "", "| split family | task | runs | mean macro-F1 | std | min | max |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in report["summary"]:
        lines.append(
            f"| {row['split_family']} | {row['task_type']} | {row['runs']} | {row['macro_f1_mean']:.4f} | {row['macro_f1_std']:.4f} | {row['macro_f1_min']:.4f} | {row['macro_f1_max']:.4f} |"
        )
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 17, 23, 31, 43])
    parser.add_argument("--output-json", default="reports/linear_seed_stability_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/linear_seed_stability_audit_v1.2_sd_core.md")
    parser.add_argument("--output-csv", default="benchmark/v1.2_sd_core_linear_seed_stability_results.csv")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()

    split_families = {
        "standard": ("data/v1.2_sd_core_train_en.jsonl", "data/v1.2_sd_core_test_en.jsonl"),
        "challenge": ("data/v1.2_sd_core_challenge_train_en.jsonl", "data/v1.2_sd_core_challenge_test_en.jsonl"),
        "strict": ("data/v1.2_sd_core_strict_train_en.jsonl", "data/v1.2_sd_core_strict_test_en.jsonl"),
        "proxyreduced": ("data/v1.2_sd_core_proxyreduced_train_en.jsonl", "data/v1.2_sd_core_proxyreduced_test_en.jsonl"),
        "strict_proxyreduced": ("data/v1.2_sd_core_strict_proxyreduced_train_en.jsonl", "data/v1.2_sd_core_strict_proxyreduced_test_en.jsonl"),
    }
    rows = []
    missing = []
    for family, (train_path, test_path) in split_families.items():
        if not (ROOT / train_path).exists() or not (ROOT / test_path).exists():
            missing.append({"split_family": family, "train": train_path, "test": test_path})
            continue
        train_by_task = group_by_task(read_jsonl(ROOT / train_path))
        test_by_task = group_by_task(read_jsonl(ROOT / test_path))
        for task in sorted(LABEL_FIELDS):
            if not train_by_task.get(task) or not test_by_task.get(task):
                missing.append({"split_family": family, "task_type": task, "reason": "missing_train_or_test_rows"})
                continue
            field = LABEL_FIELDS[task]
            y_train = [str(row.get(field)) for row in train_by_task[task]]
            y_test = [str(row.get(field)) for row in test_by_task[task]]
            vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1, max_features=50000, sublinear_tf=True)
            x_train = vectorizer.fit_transform(text_for(row) for row in train_by_task[task])
            x_test = vectorizer.transform(text_for(row) for row in test_by_task[task])
            for seed in args.seeds:
                result = evaluate_matrix(x_train, x_test, y_train, y_test, task, seed)
                result["split_family"] = family
                rows.append(result)
    summary = summarize(rows)
    max_std = max((row["macro_f1_std"] for row in summary), default=0.0)
    hard_gates = {
        "all_expected_split_families_present": len(missing) == 0,
        "all_runs_completed": len(rows) == len(split_families) * len(LABEL_FIELDS) * len(args.seeds),
        "seed_count_at_least_five": len(args.seeds) >= 5,
        "macro_f1_std_bounded": max_std <= 0.050,
        "no_empty_test_sets": all(row["n_test"] > 0 for row in rows),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "warn",
        "seeds": args.seeds,
        "split_families": split_families,
        "rows": rows,
        "summary": summary,
        "missing": missing,
        "hard_gates": hard_gates,
        "interpretation": "High stable scores on procedural tasks are trainability evidence under released surfaces; they are reported with strict and proxy-reduced audits.",
    }
    report["figures"] = plot(summary, ROOT / args.figure_dir)
    write_csv(ROOT / args.output_csv, rows)
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)
    print({"status": report["status"], "runs": len(rows), "max_std": max_std})


if __name__ == "__main__":
    main()
