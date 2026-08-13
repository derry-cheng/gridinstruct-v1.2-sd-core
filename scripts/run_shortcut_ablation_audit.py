#!/usr/bin/env python3
"""Run input-field and shortcut ablations for classification tasks."""
from __future__ import annotations

import argparse
import csv
import json
import re
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from sklearn.exceptions import UndefinedMetricWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json

LABEL_TASKS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}

KEYWORD_PATTERNS = {
    # The current release is English-derived.  These masks are intentionally
    # data-driven: frequent non-stopword tokens are extracted from the
    # training surface and then checked for actual replacement counts.
    "operation_ticket_check": ["monitor", "permit", "confirm", "ticket", "switch", "review", "complete"],
    "dispatcher_intent_tool_call": ["diagnose", "dispatch", "mitigate", "check", "safety", "violation", "query", "tool"],
    "regulation_compliance_check": ["compliant", "violation", "overload", "voltage", "monitor", "redispatch", "reserve", "check"],
}

VARIANTS = {
    "operation_ticket_check": ["full", "no_ticket_text", "no_monitoring_arrangement", "no_dispatch_permit_status", "no_object_consistency", "ticket_text_only", "procedural_fields_only", "keyword_mask"],
    "dispatcher_intent_tool_call": ["full", "no_utterance", "utterance_only", "grid_state_only", "no_command_channel", "keyword_mask"],
    "regulation_compliance_check": ["full", "no_proposed_action", "no_grid_state_summary", "no_observed_issues", "no_operator_goal", "proposed_action_only", "state_only", "keyword_mask"],
}


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def mask_keywords(text: str, task: str) -> str:
    out = text
    for pattern in KEYWORD_PATTERNS.get(task, []):
        out = out.replace(pattern, "[MASKED]")
    return re.sub(r"\s+", " ", out).strip()


def keyword_replacement_count(text: str, task: str) -> int:
    """Count literal replacements so a zero-effect mask cannot pass silently."""
    return sum(text.lower().count(pattern.lower()) for pattern in KEYWORD_PATTERNS.get(task, []))


def with_removed_fields(data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    copied = dict(data)
    for field in fields:
        if field in copied:
            copied[field] = f"[MASKED_{field}]"
    return copied


def variant_payload(row: dict[str, Any], task: str, variant: str) -> dict[str, Any]:
    inp = row.get("input") if isinstance(row.get("input"), dict) else {"input_text": row.get("input")}
    inp = dict(inp or {})
    instruction = row.get("instruction", "")
    if task == "operation_ticket_check":
        if variant == "no_ticket_text":
            inp = with_removed_fields(inp, ["ticket_text"])
        elif variant == "no_monitoring_arrangement":
            inp = with_removed_fields(inp, ["monitoring_arrangement"])
        elif variant == "no_dispatch_permit_status":
            inp = with_removed_fields(inp, ["dispatch_permit_status"])
        elif variant == "no_object_consistency":
            inp = with_removed_fields(inp, ["object_consistency"])
        elif variant == "ticket_text_only":
            inp = {"ticket_text": inp.get("ticket_text", "")}
            instruction = ""
        elif variant == "procedural_fields_only":
            inp = {key: inp.get(key) for key in ("dispatch_permit_status", "monitoring_arrangement", "object_consistency", "key_checks") if key in inp}
            instruction = ""
    elif task == "dispatcher_intent_tool_call":
        if variant == "no_utterance":
            inp = with_removed_fields(inp, ["utterance"])
        elif variant == "utterance_only":
            inp = {"utterance": inp.get("utterance", "")}
            instruction = ""
        elif variant == "grid_state_only":
            inp = {"grid_state_summary": inp.get("grid_state_summary", "")}
            instruction = ""
        elif variant == "no_command_channel":
            inp = with_removed_fields(inp, ["command_channel"])
    elif task == "regulation_compliance_check":
        if variant == "no_proposed_action":
            inp = with_removed_fields(inp, ["proposed_action"])
        elif variant == "no_grid_state_summary":
            inp = with_removed_fields(inp, ["grid_state_summary"])
        elif variant == "no_observed_issues":
            inp = with_removed_fields(inp, ["observed_issues"])
        elif variant == "no_operator_goal":
            inp = with_removed_fields(inp, ["operator_goal"])
        elif variant == "proposed_action_only":
            inp = {"proposed_action": inp.get("proposed_action", "")}
            instruction = ""
        elif variant == "state_only":
            inp = {key: inp.get(key) for key in ("grid_state_summary", "observed_issues") if key in inp}
            instruction = ""
    return {"instruction": instruction, "input": inp}


def make_text(row: dict[str, Any], task: str, variant: str) -> str:
    payload = variant_payload(row, task, variant)
    text = compact_json(payload)
    if variant == "keyword_mask":
        text = mask_keywords(text, task)
    return text


def label(row: dict[str, Any], task: str) -> str:
    return str(row.get(LABEL_TASKS[task]) or "missing_label")


def load_task_rows(path: str, task: str) -> list[dict[str, Any]]:
    if not path or not (ROOT / path).exists():
        return []
    return [row for row in read_jsonl(ROOT / path) if row.get("task_type") == task and row.get(LABEL_TASKS[task])]


def evaluate_variant(task: str, split_family: str, train_path: str, test_path: str, variant: str) -> dict[str, Any] | None:
    train_rows = load_task_rows(train_path, task)
    test_rows = load_task_rows(test_path, task)
    if not train_rows or not test_rows:
        return None
    y_train = [label(row, task) for row in train_rows]
    y_test = [label(row, task) for row in test_rows]
    classes = sorted(set(y_train) | set(y_test))
    raw_train = [make_text(row, task, "full") for row in train_rows]
    raw_test = [make_text(row, task, "full") for row in test_rows]
    x_train = [make_text(row, task, variant) for row in train_rows]
    x_test = [make_text(row, task, variant) for row in test_rows]
    model = Pipeline([
        ("tfidf", TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=1, max_features=120000, sublinear_tf=True)),
        ("clf", LinearSVC(class_weight="balanced", C=1.0, random_state=42)),
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=UndefinedMetricWarning)
        model.fit(x_train, y_train)
        pred = model.predict(x_test)
        macro_f1 = f1_score(y_test, pred, labels=classes, average="macro", zero_division=0)
        balanced = balanced_accuracy_score(y_test, pred)
    return {
        "task": task,
        "split_family": split_family,
        "model_family": "tfidf_char_linear_svc",
        "input_variant": variant,
        "train_path": train_path,
        "test_path": test_path,
        "n_train": len(train_rows),
        "n_test": len(test_rows),
        "train_label_counts": dict(Counter(y_train)),
        "test_label_counts": dict(Counter(y_test)),
        "macro_f1": float(macro_f1),
        "balanced_accuracy": float(balanced),
        "accuracy": float(accuracy_score(y_test, pred)),
        "keyword_replacements_train": sum(keyword_replacement_count(text, task) for text in raw_train)
        if variant == "keyword_mask"
        else None,
        "keyword_replacements_test": sum(keyword_replacement_count(text, task) for text in raw_test)
        if variant == "keyword_mask"
        else None,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    fieldnames = ["task", "split_family", "model_family", "input_variant", "train_path", "test_path", "n_train", "n_test", "macro_f1", "balanced_accuracy", "accuracy", "delta_vs_full", "risk_level", "keyword_replacements_train", "keyword_replacements_test", "train_label_counts", "test_label_counts"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (dict, list)) else row.get(key, "") for key in fieldnames})


def add_delta_and_risk(rows: list[dict[str, Any]]) -> None:
    full_scores = {(row["task"], row["split_family"]): row["macro_f1"] for row in rows if row["input_variant"] == "full"}
    for row in rows:
        base = full_scores.get((row["task"], row["split_family"]), row["macro_f1"])
        delta = float(base) - float(row["macro_f1"])
        row["delta_vs_full"] = delta
        if row["input_variant"] == "full":
            row["risk_level"] = "reference"
        elif delta >= 0.15:
            row["risk_level"] = "high_field_dependence"
        elif delta >= 0.05:
            row["risk_level"] = "moderate_field_dependence"
        else:
            row["risk_level"] = "low_field_dependence"


def plot_scores(rows: list[dict[str, Any]], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    figures: dict[str, str] = {}
    for split_family in sorted({row["split_family"] for row in rows}):
        subset = [row for row in rows if row["split_family"] == split_family]
        tasks = sorted({row["task"] for row in subset})
        fig, axes = plt.subplots(len(tasks), 1, figsize=(12, 3.6 * len(tasks)), squeeze=False)
        for ax, task in zip(axes[:, 0], tasks):
            task_rows = [row for row in subset if row["task"] == task]
            variants = [row["input_variant"] for row in task_rows]
            scores = [row["macro_f1"] for row in task_rows]
            colors = ["#2b6cb0" if variant == "full" else "#dd6b20" for variant in variants]
            ax.bar(range(len(variants)), scores, color=colors)
            ax.set_title(f"{task} - {split_family} input ablation")
            ax.set_ylabel("Macro F1")
            ax.set_ylim(0, 1.05)
            ax.set_xticks(range(len(variants)))
            ax.set_xticklabels(variants, rotation=28, ha="right")
            ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        path = figure_dir / f"fig_shortcut_ablation_{split_family}.png"
        fig.savefig(path, dpi=220)
        plt.close(fig)
        figures[f"shortcut_ablation_{split_family}"] = str(path.relative_to(ROOT))

    top = sorted([row for row in rows if row["input_variant"] != "full"], key=lambda row: row["delta_vs_full"], reverse=True)[:18]
    if top:
        fig, ax = plt.subplots(figsize=(12, max(5, 0.42 * len(top))))
        labels = [f"{row['task']}{chr(10)}{row['split_family']}:{row['input_variant']}" for row in top]
        deltas = [row["delta_vs_full"] for row in top]
        ax.barh(range(len(top)), deltas, color="#c53030")
        ax.set_yticks(range(len(top)))
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
        ax.set_xlabel("Macro F1 drop from full input")
        ax.set_title("Largest Input-Field Sensitivity Drops")
        ax.grid(axis="x", alpha=0.25)
        fig.tight_layout()
        path = figure_dir / "fig_shortcut_ablation_delta.png"
        fig.savefig(path, dpi=220)
        plt.close(fig)
        figures["shortcut_ablation_delta"] = str(path.relative_to(ROOT))
    return figures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="data/v1.2_sd_core_train.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test.jsonl")
    parser.add_argument("--challenge-train", default="data/v1.2_sd_core_challenge_train.jsonl")
    parser.add_argument("--challenge-test", default="data/v1.2_sd_core_challenge_test.jsonl")
    parser.add_argument("--output-json", default="reports/shortcut_ablation_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/shortcut_ablation_v1.2_sd_core.md")
    parser.add_argument("--output-csv", default="benchmark/v1.2_sd_core_shortcut_ablation_results.csv")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()

    rows = []
    split_defs = [("standard", args.train, args.test)]
    if args.challenge_train and args.challenge_test and (ROOT / args.challenge_train).exists() and (ROOT / args.challenge_test).exists():
        split_defs.append(("challenge", args.challenge_train, args.challenge_test))
    for split_family, train_path, test_path in split_defs:
        for task in LABEL_TASKS:
            for variant in VARIANTS[task]:
                item = evaluate_variant(task, split_family, train_path, test_path, variant)
                if item:
                    rows.append(item)
    add_delta_and_risk(rows)
    figures = plot_scores(rows, ROOT / args.figure_dir)
    write_csv(ROOT / args.output_csv, rows)

    high = [row for row in rows if row.get("risk_level") == "high_field_dependence"]
    moderate = [row for row in rows if row.get("risk_level") == "moderate_field_dependence"]
    full_scores = {f"{row['split_family']}|{row['task']}": row["macro_f1"] for row in rows if row["input_variant"] == "full"}
    keyword_rows = [row for row in rows if row["input_variant"] == "keyword_mask"]
    keyword_mask_status = (
        "pass"
        if keyword_rows and all(
            (row.get("keyword_replacements_train") or 0) > 0
            and (row.get("keyword_replacements_test") or 0) > 0
            for row in keyword_rows
        )
        else "fail"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if keyword_mask_status == "pass" else "fail",
        "keyword_mask_status": keyword_mask_status,
        "keyword_mask_replacement_summary": [
            {
                "task": row["task"],
                "split_family": row["split_family"],
                "train_replacements": row.get("keyword_replacements_train"),
                "test_replacements": row.get("keyword_replacements_test"),
            }
            for row in keyword_rows
        ],
        "interpretation": "Input-field sensitivity audit for trainability and shortcut analysis; not an operational-dispatch capability claim.",
        "rows": rows,
        "full_scores": full_scores,
        "high_field_dependence_count": len(high),
        "moderate_field_dependence_count": len(moderate),
        "figures": figures,
        "csv": args.output_csv,
    }
    write_json(ROOT / args.output_json, report)

    lines = ["# Shortcut and Input-Field Ablation Audit", "", f"Generated: {report['generated_at']}", f"Status: `{report['status']}`", f"Keyword mask status: `{keyword_mask_status}`", "", "This audit evaluates how much each classification task depends on specific input fields. It is used to interpret near-perfect held-out scores as trainability and input-sensitivity evidence.", "", "## Full-Input Scores", ""]
    for key, value in sorted(full_scores.items()):
        lines.append(f"- `{key}`: {value:.4f}")
    lines.extend(["", "## Highest Sensitivity Rows", ""])
    for row in sorted([r for r in rows if r["input_variant"] != "full"], key=lambda r: r["delta_vs_full"], reverse=True)[:12]:
        lines.append(f"- `{row['split_family']} | {row['task']} | {row['input_variant']}`: macro-F1={row['macro_f1']:.4f}, drop={row['delta_vs_full']:.4f}, risk=`{row['risk_level']}`")
    lines.extend(["", "## Figures", ""])
    for name, path in figures.items():
        lines.append(f"- {name}: `{path}`")
    (ROOT / args.output_md).write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    if report["status"] != "pass":
        raise SystemExit(1)



if __name__ == "__main__":
    main()
