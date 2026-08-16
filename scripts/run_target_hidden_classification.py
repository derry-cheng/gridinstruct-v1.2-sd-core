#!/usr/bin/env python3
"""Evaluate classification tasks after removing target-carrying input fields.

The target-hidden profile keeps dispatcher-facing text and procedural fields,
while removing identifiers, generated state summaries, rule/label fields,
tool plans, and released query/result objects.  It is a diagnostic baseline for
separating task learnability from direct access to the canonical target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.svm import LinearSVC

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


LABEL_FIELDS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}

TASKS = tuple(sorted(LABEL_FIELDS))
SPLITS = {
    "standard": (
        "data/v1.2_sd_core_train_en.jsonl",
        "data/v1.2_sd_core_test_en.jsonl",
    ),
    "strict_source_group": (
        "data/v1.2_sd_core_strict_train_en.jsonl",
        "data/v1.2_sd_core_strict_test_en.jsonl",
    ),
    "template_holdout": (
        "data/v1.2_sd_core_template_holdout_train_en.jsonl",
        "data/v1.2_sd_core_template_holdout_test_en.jsonl",
    ),
}

DROP_KEY_TOKENS = (
    "scenario",
    "source",
    "simulation",
    "metadata",
    "template",
    "validation",
    "tool_plan",
    "structured_query",
    "query_result",
    "rule_summary",
    "rule_id",
    "active_constraints",
    "observed_issues",
    "grid_state_summary",
    "compliance_label",
    "intent",
    "slots",
    "rationale",
    "chosen_response",
    "rejected_response",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").split())


def remove_target_fields(value: Any) -> Any:
    if isinstance(value, dict):
        kept = {}
        for key, child in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in DROP_KEY_TOKENS):
                continue
            kept[key] = remove_target_fields(child)
        return kept
    if isinstance(value, list):
        return [remove_target_fields(item) for item in value]
    return value


def scrub_instruction(text: Any) -> str:
    value = normalize(text)
    value = re.sub(r"\b(?:ieee|pegase|rte)\s*\d+\b", "network", value, flags=re.I)
    value = re.sub(r"\b[a-z]+\d+(?:_[a-z0-9]+)+\b", "scenario", value, flags=re.I)
    value = re.sub(r"\b(?:0\.\d+|1\.\d+|\d+\.\d+)%?\b", "numeric_state", value)
    return value


def text_for(row: dict[str, Any], profile: str) -> str:
    if profile == "full_contract":
        payload = row.get("input") or {}
        return "\n".join(
            [
                f"task_type: {row.get('task_type')}",
                f"instruction: {normalize(row.get('instruction'))}",
                f"input: {normalize(payload)}",
            ]
        )
    if profile == "target_hidden":
        payload = remove_target_fields(row.get("input") or {})
        return "\n".join(
            [
                f"task_type: {row.get('task_type')}",
                f"instruction: {scrub_instruction(row.get('instruction'))}",
                f"input: {normalize(payload)}",
            ]
        )
    if profile == "instruction_only":
        return scrub_instruction(row.get("instruction"))
    raise ValueError(f"unknown input profile: {profile}")


def macro_f1_ci(gold: list[str], pred: list[str], seed: int = 20260805) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(gold)
    if n == 0:
        return 0.0, 0.0
    labels = sorted(set(gold) | set(pred))
    label_to_id = {label: index for index, label in enumerate(labels)}
    gold_ids = np.asarray([label_to_id[value] for value in gold], dtype=np.int16)
    pred_ids = np.asarray([label_to_id[value] for value in pred], dtype=np.int16)
    values = np.empty(400, dtype=np.float64)
    for replicate in range(len(values)):
        indices = rng.integers(0, n, size=n)
        matrix = np.bincount(
            gold_ids[indices] * len(labels) + pred_ids[indices],
            minlength=len(labels) * len(labels),
        ).reshape(len(labels), len(labels))
        true_positive = np.diag(matrix).astype(np.float64)
        precision = true_positive / np.maximum(matrix.sum(axis=0), 1.0)
        recall = true_positive / np.maximum(matrix.sum(axis=1), 1.0)
        values[replicate] = np.mean(
            np.divide(
                2.0 * precision * recall,
                precision + recall,
                out=np.zeros_like(precision),
                where=(precision + recall) > 0,
            )
        )
    return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def evaluate_task(train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]], task: str, profile: str) -> dict[str, Any]:
    label_field = LABEL_FIELDS[task]
    train_text = [text_for(row, profile) for row in train_rows]
    test_text = [text_for(row, profile) for row in test_rows]
    y_train = [str(row.get(label_field)) for row in train_rows]
    y_test = [str(row.get(label_field)) for row in test_rows]
    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(2, 5),
        min_df=2,
        max_features=80000,
        sublinear_tf=True,
    )
    x_train = vectorizer.fit_transform(train_text)
    x_test = vectorizer.transform(test_text)
    model = LinearSVC(class_weight="balanced", C=1.0, dual="auto", random_state=42)
    model.fit(x_train, y_train)
    predictions = model.predict(x_test).tolist()
    labels = sorted(set(y_train) | set(y_test) | set(predictions))
    ci_low, ci_high = macro_f1_ci(y_test, predictions)
    return {
        "task_type": task,
        "input_profile": profile,
        "n_train": len(train_rows),
        "n_test": len(test_rows),
        "vocabulary_size": len(vectorizer.vocabulary_),
        "accuracy": float(accuracy_score(y_test, predictions)),
        "macro_f1": float(f1_score(y_test, predictions, labels=labels, average="macro", zero_division=0)),
        "macro_f1_ci95": [ci_low, ci_high],
        "balanced_accuracy": float(balanced_accuracy_score(y_test, predictions)),
        "gold_label_counts": dict(Counter(y_test)),
        "prediction_label_counts": dict(Counter(predictions)),
        "labels": labels,
    }


def evaluate_split(name: str, train_path: str, test_path: str) -> list[dict[str, Any]]:
    split_started = time.perf_counter()
    train_rows = read_jsonl(ROOT / train_path)
    test_rows = read_jsonl(ROOT / test_path)
    train_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    test_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train_rows:
        if row.get("task_type") in LABEL_FIELDS:
            train_by_task[str(row["task_type"])].append(row)
    for row in test_rows:
        if row.get("task_type") in LABEL_FIELDS:
            test_by_task[str(row["task_type"])].append(row)
    rows = []
    for profile in ("full_contract", "target_hidden", "instruction_only"):
        for task in TASKS:
            if not train_by_task.get(task) or not test_by_task.get(task):
                continue
            item = evaluate_task(train_by_task[task], test_by_task[task], task, profile)
            item.update(
                {
                    "split": name,
                    "train_path": train_path,
                    "test_path": test_path,
                    "train_sha256": sha256(ROOT / train_path),
                    "test_sha256": sha256(ROOT / test_path),
                }
            )
            rows.append(item)
            print(
                f"[target-hidden] split={name} profile={profile} task={task} "
                f"macro_f1={item['macro_f1']:.4f} elapsed={time.perf_counter() - split_started:.1f}s",
                flush=True,
            )
    return rows


def plot(rows: list[dict[str, Any]], path: Path) -> None:
    ensure_dirs(path.parent)
    order = ["full_contract", "target_hidden", "instruction_only"]
    splits = list(SPLITS)
    tasks = list(TASKS)
    task_labels = {
        "dispatcher_intent_tool_call": "Intent routing",
        "operation_ticket_check": "Ticket check",
        "regulation_compliance_check": "Compliance check",
    }
    split_labels = {
        "standard": "Standard",
        "strict_source_group": "Strict source-group",
        "template_holdout": "Template holdout",
    }
    fig, axes = plt.subplots(len(tasks), 1, figsize=(8.2, 6.6), sharex=True)
    for ax, task in zip(np.atleast_1d(axes), tasks):
        for split_index, split in enumerate(splits):
            subset = {row["input_profile"]: row for row in rows if row["task_type"] == task and row["split"] == split}
            values = [subset[profile]["macro_f1"] if profile in subset else np.nan for profile in order]
            x = np.arange(len(order)) + split_index * 0.25
            ax.bar(x, values, width=0.23, label=split_labels.get(split, split.replace("_", " ").title()))
        ax.set_ylim(0, 1.08)
        ax.set_ylabel(f"{task_labels.get(task, task.replace('_', ' '))}\nmacro-F1")
        ax.grid(axis="y", alpha=0.25)
    axes[-1].set_xticks(np.arange(len(order)) + 0.25)
    axes[-1].set_xticklabels(["Full contract", "Target hidden", "Instruction only"])
    axes[0].legend(frameon=False, ncol=3, loc="lower center", bbox_to_anchor=(0.5, 1.01))
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Target-hidden classification baseline",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "The target-hidden profile removes scenario/source identifiers, generated state summaries, rule/label fields, tool plans, structured targets, and released query/result objects. It retains dispatcher-facing text and non-target procedural fields. Scores are diagnostic learnability evidence, not an operational dispatch claim.",
        "",
        "| split | task | input profile | n test | macro-F1 | 95% CI | balanced accuracy |",
        "| --- | --- | --- | ---: | ---: | --- | ---: |",
    ]
    for row in report["rows"]:
        low, high = row["macro_f1_ci95"]
        lines.append(
            f"| {row['split']} | {row['task_type']} | {row['input_profile']} | {row['n_test']} | {row['macro_f1']:.4f} | [{low:.4f}, {high:.4f}] | {row['balanced_accuracy']:.4f} |"
        )
    lines.extend(["", "Input removal tokens", "", ", ".join(f"`{token}`" for token in DROP_KEY_TOKENS), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", default="reports/target_hidden_classification_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/target_hidden_classification_v1.2_sd_core.md")
    parser.add_argument("--output-figure", default="figures/sd_core/fig_target_hidden_classification.png")
    args = parser.parse_args()
    rows = []
    for split, (train_path, test_path) in SPLITS.items():
        rows.extend(evaluate_split(split, train_path, test_path))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if rows else "fail",
        "objective": "target-hidden input-surface ablation for classification learnability",
        "input_policy": {
            "profiles": ["full_contract", "target_hidden", "instruction_only"],
            "removed_key_tokens": list(DROP_KEY_TOKENS),
            "instruction_scrub": "scenario identifiers and numeric state fragments replaced with neutral tokens",
        },
        "splits": SPLITS,
        "rows": rows,
    }
    plot(rows, ROOT / args.output_figure)
    report["figure"] = args.output_figure
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)
    print(json.dumps({"status": report["status"], "rows": len(rows), "figure": args.output_figure}, ensure_ascii=False))


if __name__ == "__main__":
    main()
