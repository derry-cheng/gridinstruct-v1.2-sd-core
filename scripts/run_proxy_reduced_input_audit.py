#!/usr/bin/env python3
"""Audit classification learnability with proxy-reduced input surfaces."""
from __future__ import annotations

import argparse
import json
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
DROP_KEY_TOKENS = (
    "label",
    "intent",
    "source",
    "scenario_id",
    "simulation",
    "template",
    "metadata",
    "validation_status",
    "tool_plan",
    "compliance",
    "expected",
)


def normalize(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").split())


def filtered_input(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in DROP_KEY_TOKENS):
                continue
            out[key] = filtered_input(child)
        return out
    if isinstance(value, list):
        return [filtered_input(item) for item in value]
    return value


def text_for(row: dict[str, Any], profile: str) -> str:
    if profile == "instruction_only":
        return normalize(row.get("instruction"))
    if profile == "proxy_reduced":
        payload = filtered_input(row.get("input") or {})
        return "\n".join(
            [
                f"task_type: {row.get('task_type')}",
                f"instruction: {normalize(row.get('instruction'))}",
                f"input: {normalize(payload)}",
            ]
        )
    raise ValueError(profile)


def group_by_task(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("task_type") in LABEL_FIELDS:
            grouped[str(row["task_type"])].append(row)
    return grouped


def evaluate_task(train_rows: list[dict[str, Any]], eval_rows: list[dict[str, Any]], task: str, profile: str) -> dict[str, Any]:
    label_field = LABEL_FIELDS[task]
    y_train = [str(row.get(label_field)) for row in train_rows]
    y_eval = [str(row.get(label_field)) for row in eval_rows]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1, max_features=60000)
    x_train = vectorizer.fit_transform(text_for(row, profile) for row in train_rows)
    x_eval = vectorizer.transform(text_for(row, profile) for row in eval_rows)
    clf = LinearSVC(dual="auto")
    clf.fit(x_train, y_train)
    pred = clf.predict(x_eval).tolist()
    labels = sorted(set(y_train) | set(y_eval) | set(pred))
    return {
        "task_type": task,
        "profile": profile,
        "n_train": len(train_rows),
        "n_eval": len(eval_rows),
        "accuracy": float(accuracy_score(y_eval, pred)),
        "macro_f1": float(f1_score(y_eval, pred, labels=labels, average="macro", zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y_eval, pred)),
        "label_counts": dict(Counter(y_eval)),
        "labels": labels,
    }


def evaluate_split(name: str, train_rows: list[dict[str, Any]], eval_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    grouped_train = group_by_task(train_rows)
    grouped_eval = group_by_task(eval_rows)
    for profile in ("instruction_only", "proxy_reduced"):
        for task in sorted(grouped_eval):
            if not grouped_train.get(task):
                continue
            result = evaluate_task(grouped_train[task], grouped_eval[task], task, profile)
            result["split"] = name
            rows.append(result)
    return rows


def plot_scores(rows: list[dict[str, Any]], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    order = sorted(rows, key=lambda row: (row["split"], row["task_type"], row["profile"]))
    labels = [f"{row['split']}\n{row['task_type']}\n{row['profile']}" for row in order]
    values = [row["macro_f1"] for row in order]
    colors = ["#1f77b4" if row["profile"] == "proxy_reduced" else "#7f7f7f" for row in order]
    fig, ax = plt.subplots(figsize=(13, max(6, 0.42 * len(labels))))
    ax.barh(np.arange(len(labels)), values, color=colors)
    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.02)
    ax.set_xlabel("Macro-F1")
    ax.set_title("Proxy-Reduced Classification Input Audit")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    path = figure_dir / "fig_proxy_reduced_input_scores.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return {"proxy_reduced_scores": str(path.relative_to(ROOT))}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Proxy-Reduced Input Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This audit uses TF-IDF classifiers over instruction-only and proxy-reduced input text. It excludes metadata, source identifiers, template fields, explicit label fields, and tool-plan fields from the input surface.",
        "",
        "| split | task | profile | n_eval | macro-F1 | balanced accuracy |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['split']} | {row['task_type']} | {row['profile']} | {row['n_eval']} | {row['macro_f1']:.4f} | {row['balanced_accuracy']:.4f} |"
        )
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--standard-train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--standard-test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--strict-train", default="data/v1.2_sd_core_strict_train_en.jsonl")
    parser.add_argument("--strict-test", default="data/v1.2_sd_core_strict_test_en.jsonl")
    parser.add_argument("--output-json", default="reports/proxy_reduced_input_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/proxy_reduced_input_audit_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()
    standard_rows = evaluate_split("standard", read_jsonl(ROOT / args.standard_train), read_jsonl(ROOT / args.standard_test))
    strict_rows = evaluate_split("strict_source_group", read_jsonl(ROOT / args.strict_train), read_jsonl(ROOT / args.strict_test))
    rows = standard_rows + strict_rows
    hard_gates = {
        "standard_tasks_present": sum(row["split"] == "standard" and row["profile"] == "proxy_reduced" for row in rows) == 3,
        "strict_tasks_present": sum(row["split"] == "strict_source_group" and row["profile"] == "proxy_reduced" for row in rows) == 3,
        "no_empty_eval_sets": all(row["n_eval"] > 0 for row in rows),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "warn",
        "input_policy": "instruction plus filtered input; metadata/source/template/explicit label/tool fields excluded",
        "hard_gates": hard_gates,
        "rows": rows,
    }
    report["figures"] = plot_scores(rows, ROOT / args.figure_dir)
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)
    print({"status": report["status"], "rows": len(rows)})


if __name__ == "__main__":
    main()
