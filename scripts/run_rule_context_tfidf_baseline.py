"""Run TF-IDF classification baselines with and without rule-card context."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.svm import LinearSVC

from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, write_json, write_jsonl


CLASSIFICATION_TASKS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def compact(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").strip().split())


def natural_input(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("input") or {}
    task = row["task_type"]
    if task == "operation_ticket_check":
        return {
            "ticket_text": payload.get("ticket_text"),
            "equipment_state": payload.get("equipment_state"),
            "operation_context": payload.get("operation_context"),
            "dispatch_permit_status": payload.get("dispatch_permit_status"),
            "monitoring_arrangement": payload.get("monitoring_arrangement"),
            "object_consistency": payload.get("object_consistency"),
            "key_checks": payload.get("key_checks"),
        }
    if task == "regulation_compliance_check":
        return {
            "grid_state_summary": payload.get("grid_state_summary"),
            "proposed_action": payload.get("proposed_action"),
            "operator_goal": payload.get("operator_goal"),
            "observed_issues": payload.get("observed_issues"),
            "rule_summary": payload.get("rule_summary"),
            "active_constraints": payload.get("active_constraints") or payload.get("observed_issues"),
        }
    if task == "dispatcher_intent_tool_call":
        return {
            "utterance": payload.get("utterance") or payload.get("user_utterance"),
            "grid_state_summary": payload.get("grid_state_summary") or payload.get("scenario_summary"),
            "command_channel": payload.get("command_channel"),
            "operator_request": payload.get("operator_request"),
        }
    return payload


def load_rule_context(path: Path) -> dict[str, str]:
    rules = read_json(path)
    context: dict[str, str] = {}
    for rule in rules:
        context[str(rule["rule_id"])] = " ".join(
            compact(value)
            for value in [
                rule.get("summary"),
                rule.get("source_title"),
                rule.get("rule_type"),
                rule.get("evidence_fields"),
            ]
            if value
        )
    return context


def text_for(row: dict[str, Any], rule_context: dict[str, str], include_rule_context: bool) -> str:
    parts = [
        f"task: {row['task_type']}",
        f"instruction: {compact(row.get('instruction'))}",
        f"input: {compact(natural_input(row))}",
    ]
    if include_rule_context:
        rules = [rule_context.get(str(rule_id), str(rule_id)) for rule_id in row.get("source_regulation_ids", [])]
        parts.append(f"rule_context: {compact(rules)}")
    return "\n".join(parts)


def label_for(row: dict[str, Any]) -> str:
    return compact(row.get(CLASSIFICATION_TASKS[row["task_type"]]))


def grouped(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("task_type") in CLASSIFICATION_TASKS:
            out[row["task_type"]].append(row)
    return out


def evaluate_profile(
    train_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    rule_context: dict[str, str],
    include_rule_context: bool,
    profile: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task = eval_rows[0]["task_type"]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1, max_features=80000)
    x_train = vectorizer.fit_transform(text_for(row, rule_context, include_rule_context) for row in train_rows)
    x_eval = vectorizer.transform(text_for(row, rule_context, include_rule_context) for row in eval_rows)
    y_train = [label_for(row) for row in train_rows]
    y_eval = [label_for(row) for row in eval_rows]
    clf = LinearSVC(dual="auto")
    clf.fit(x_train, y_train)
    y_pred = clf.predict(x_eval).tolist()
    predictions = [
        {
            "id": row["id"],
            "task_type": task,
            "profile": profile,
            "gold": gold,
            "prediction": pred,
            "correct": gold == pred,
        }
        for row, gold, pred in zip(eval_rows, y_eval, y_pred)
    ]
    label_counts = Counter(y_eval)
    metrics = {
        "task_type": task,
        "profile": profile,
        "model_family": "tfidf_linear_svc",
        "n": len(eval_rows),
        "accuracy": accuracy_score(y_eval, y_pred),
        "macro_f1": f1_score(y_eval, y_pred, average="macro"),
        "balanced_accuracy": balanced_accuracy_score(y_eval, y_pred),
        "label_counts": dict(label_counts),
        "label_space": sorted(set(y_train) | set(y_eval)),
    }
    return metrics, predictions


def run_split(
    train_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    rule_context: dict[str, str],
    include_rule_context: bool,
    profile: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    train_by_task = grouped(train_rows)
    eval_by_task = grouped(eval_rows)
    metrics: dict[str, Any] = {}
    predictions: list[dict[str, Any]] = []
    for task in sorted(eval_by_task):
        if not train_by_task.get(task):
            continue
        task_metrics, task_predictions = evaluate_profile(
            train_by_task[task], eval_by_task[task], rule_context, include_rule_context, profile
        )
        metrics[task] = task_metrics
        predictions.extend(task_predictions)
    return metrics, predictions


def aggregate(metrics: dict[str, Any]) -> float:
    values = [float(row["macro_f1"]) for row in metrics.values()]
    return sum(values) / len(values) if values else 0.0


def plot_report(report: dict[str, Any], figure_dir: Path) -> list[str]:
    ensure_dirs(figure_dir)
    tasks = sorted({task for profile in report["profiles"].values() for task in profile["test_metrics"]})
    profiles = list(report["profiles"])
    fig, ax = plt.subplots(figsize=(7.6, 3.4))
    width = 0.8 / max(len(profiles), 1)
    xs = list(range(len(tasks)))
    colors = ["#4C78A8", "#F58518", "#54A24B", "#B279A2"]
    for idx, profile in enumerate(profiles):
        values = [report["profiles"][profile]["test_metrics"].get(task, {}).get("macro_f1", 0.0) for task in tasks]
        offsets = [x - 0.4 + width / 2 + idx * width for x in xs]
        bars = ax.bar(offsets, values, width=width, label=profile, color=colors[idx % len(colors)])
        ax.bar_label(bars, labels=[f"{value:.3f}" for value in values], fontsize=7, padding=1)
    ax.set_xticks(xs)
    ax.set_xticklabels(tasks, rotation=25, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Macro-F1")
    ax.set_title("Rule-context TF-IDF classification baselines")
    ax.legend(frameon=False)
    path = figure_dir / "fig_rule_context_tfidf_macro_f1.png"
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return [str(path.relative_to(ROOT))]


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Rule-context TF-IDF Baseline",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "This baseline compares the same character TF-IDF classifier with and without record-linked rule-card summaries. The rule context is released provenance text, not a hand-written decision rule.",
        "",
        "## Test macro-F1",
        "",
    ]
    for profile, payload in report["profiles"].items():
        lines.append(f"### {profile}")
        lines.append(f"- Test aggregate macro-F1: {payload['test_macro_score']:.4f}")
        for task, metrics in payload["test_metrics"].items():
            lines.append(f"- {task}: macro-F1={metrics['macro_f1']:.4f}, accuracy={metrics['accuracy']:.4f}, n={metrics['n']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--rules", default="rules/regulation_rules.json")
    parser.add_argument("--output-prefix", default="benchmark/v1.2_sd_core_rule_context_tfidf")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()

    train_rows = read_jsonl(ROOT / args.train)
    validation_rows = read_jsonl(ROOT / args.validation)
    test_rows = read_jsonl(ROOT / args.test)
    rule_context = load_rule_context(ROOT / args.rules)

    profiles = {}
    all_predictions: list[dict[str, Any]] = []
    for profile, include_context in (("natural_no_rule", False), ("natural_with_rule_context", True)):
        validation_metrics, validation_predictions = run_split(
            train_rows, validation_rows, rule_context, include_context, profile
        )
        test_metrics, test_predictions = run_split(train_rows, test_rows, rule_context, include_context, profile)
        profiles[profile] = {
            "include_rule_context": include_context,
            "validation_metrics": validation_metrics,
            "test_metrics": test_metrics,
            "validation_macro_score": aggregate(validation_metrics),
            "test_macro_score": aggregate(test_metrics),
        }
        all_predictions.extend(validation_predictions)
        all_predictions.extend(test_predictions)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train": args.train,
        "validation": args.validation,
        "test": args.test,
        "rules": args.rules,
        "objective": "rule_context_tfidf_classification_baseline",
        "train_records": len(train_rows),
        "validation_records": len(validation_rows),
        "test_records": len(test_rows),
        "profiles": profiles,
        "figures": [],
        "interpretation": "Rule-card summaries are used as released provenance context, not as handcrafted label heuristics.",
    }
    report["figures"] = plot_report(report, ROOT / args.figure_dir)
    prefix = ROOT / args.output_prefix
    write_json(Path(f"{prefix}_report.json"), report)
    write_jsonl(Path(f"{prefix}_predictions.jsonl"), all_predictions)
    write_markdown(Path(f"{prefix}_report.md"), report)


if __name__ == "__main__":
    main()
