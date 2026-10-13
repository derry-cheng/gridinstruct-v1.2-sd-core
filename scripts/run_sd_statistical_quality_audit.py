#!/usr/bin/env python3
"""Compute confidence intervals and support diagnostics for SD-core evidence."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import f1_score, accuracy_score

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json

CLASSIFICATION_RUNS = [
    ("formal_transformer_standard", "operation_ticket_check", "benchmark/v1.2_sd_core_transformer_operation_ticket_check_test_predictions.jsonl"),
    ("formal_transformer_standard", "regulation_compliance_check", "benchmark/v1.2_sd_core_transformer_regulation_compliance_check_test_predictions.jsonl"),
    ("formal_transformer_standard", "dispatcher_intent_tool_call", "benchmark/v1.2_sd_core_transformer_dispatcher_intent_tool_call_test_predictions.jsonl"),
    ("formal_transformer_challenge", "operation_ticket_check", "benchmark/v1.2_sd_core_challenge_transformer_operation_ticket_check_test_predictions.jsonl"),
    ("formal_transformer_challenge", "regulation_compliance_check", "benchmark/v1.2_sd_core_challenge_transformer_regulation_compliance_check_test_predictions.jsonl"),
    ("formal_transformer_challenge", "dispatcher_intent_tool_call", "benchmark/v1.2_sd_core_challenge_transformer_dispatcher_intent_tool_call_test_predictions.jsonl"),
]
GENERATION_RUNS = [
    ("formal_seq2seq", "regulation_qa", "benchmark/v1.2_sd_core_seq2seq_regqa_test_predictions.jsonl"),
    ("formal_seq2seq", "auxiliary_decision", "benchmark/v1.2_sd_core_seq2seq_auxiliary_decision_test_predictions.jsonl"),
    ("formal_seq2seq", "intelligent_data_query", "benchmark/v1.2_sd_core_seq2seq_intelligent_data_query_test_predictions.jsonl"),
]


def percentile_ci(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"lower": None, "upper": None}
    return {"lower": float(np.percentile(values, 2.5)), "upper": float(np.percentile(values, 97.5))}


def bootstrap_classification(rows: list[dict[str, Any]], n_bootstrap: int, seed: int, max_bootstrap_size: int) -> dict[str, Any]:
    gold = [str(row.get("gold")) for row in rows]
    pred = [str(row.get("prediction")) for row in rows]
    labels = sorted(set(gold) | set(pred))
    point_macro = float(f1_score(gold, pred, labels=labels, average="macro", zero_division=0))
    point_acc = float(accuracy_score(gold, pred))
    rng = np.random.default_rng(seed)
    n = len(rows)
    macro_values: list[float] = []
    acc_values: list[float] = []
    if n:
        gold_np = np.array(gold, dtype=object)
        pred_np = np.array(pred, dtype=object)
        bootstrap_size = min(n, max_bootstrap_size)
        for _ in range(n_bootstrap):
            idx = rng.integers(0, n, size=bootstrap_size)
            g = gold_np[idx].tolist()
            p = pred_np[idx].tolist()
            macro_values.append(float(f1_score(g, p, labels=labels, average="macro", zero_division=0)))
            acc_values.append(float(accuracy_score(g, p)))
    per_label = {}
    for label in labels:
        support = sum(1 for item in gold if item == label)
        label_f1 = float(f1_score(gold, pred, labels=[label], average="macro", zero_division=0))
        per_label[label] = {"support": support, "f1": label_f1}
    return {
        "n": n,
        "bootstrap_n": min(n, max_bootstrap_size),
        "metric": "macro_f1",
        "point": point_macro,
        "ci95": percentile_ci(macro_values),
        "accuracy": point_acc,
        "accuracy_ci95": percentile_ci(acc_values),
        "label_counts": dict(Counter(gold)),
        "per_label": per_label,
        "min_label_support": min((item["support"] for item in per_label.values()), default=0),
    }


def bootstrap_mean(values: list[float], n_bootstrap: int, seed: int, max_bootstrap_size: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = len(values)
    point = float(np.mean(values)) if values else 0.0
    boot: list[float] = []
    if n:
        arr = np.array(values, dtype=float)
        bootstrap_size = min(n, max_bootstrap_size)
        for _ in range(n_bootstrap):
            idx = rng.integers(0, n, size=bootstrap_size)
            boot.append(float(np.mean(arr[idx])))
    return {"n": n, "bootstrap_n": min(n, max_bootstrap_size), "metric": "mean_token_f1", "point": point, "ci95": percentile_ci(boot)}


def tfidf_task_rows(prediction_file: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    path = ROOT / prediction_file
    if not path.exists():
        return out
    for row in read_jsonl(path):
        out[str(row.get("task_type") or "unknown")].append(row)
    return out


def collect_rows(n_bootstrap: int, seed: int, max_bootstrap_size: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family, task, rel in CLASSIFICATION_RUNS:
        path = ROOT / rel
        if not path.exists():
            continue
        stats = bootstrap_classification(read_jsonl(path), n_bootstrap, seed + len(rows), max_bootstrap_size)
        rows.append({"family": family, "task": task, "path": rel, **stats})
    for split_name, rel in (("tfidf_test", "benchmark/v1.2_sd_core_tfidf_test_predictions.jsonl"), ("tfidf_ood", "benchmark/v1.2_sd_core_tfidf_ood_predictions.jsonl")):
        for task, items in sorted(tfidf_task_rows(rel).items()):
            if not items:
                continue
            if "correct" in items[0] and "gold" in items[0] and "prediction" in items[0]:
                stats = bootstrap_classification(items, n_bootstrap, seed + len(rows), max_bootstrap_size)
                rows.append({"family": split_name, "task": task, "path": rel, **stats})
            elif "token_f1" in items[0]:
                stats = bootstrap_mean([float(row.get("token_f1") or 0.0) for row in items], n_bootstrap, seed + len(rows), max_bootstrap_size)
                rows.append({"family": split_name, "task": task, "path": rel, **stats})
    for family, task, rel in GENERATION_RUNS:
        path = ROOT / rel
        if not path.exists():
            continue
        values = [float(row.get("token_f1") or 0.0) for row in read_jsonl(path)]
        stats = bootstrap_mean(values, n_bootstrap, seed + len(rows), max_bootstrap_size)
        rows.append({"family": family, "task": task, "path": rel, **stats})
    return rows


def plot_intervals(rows: list[dict[str, Any]], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    selected = [row for row in rows if row.get("family") in {"formal_transformer_standard", "formal_transformer_challenge", "formal_seq2seq", "tfidf_ood"}]
    selected = sorted(selected, key=lambda row: (str(row["family"]), str(row["task"])))
    labels = [f"{row['family']}\n{row['task']}" for row in selected]
    points = [float(row["point"]) for row in selected]
    lower = [float(row["ci95"]["lower"]) if row["ci95"]["lower"] is not None else float(row["point"]) for row in selected]
    upper = [float(row["ci95"]["upper"]) if row["ci95"]["upper"] is not None else float(row["point"]) for row in selected]
    xerr = np.array([[p - lo for p, lo in zip(points, lower)], [hi - p for p, hi in zip(points, upper)]])
    fig, ax = plt.subplots(figsize=(11, max(6, 0.45 * len(selected))))
    y = np.arange(len(selected))
    ax.errorbar(points, y, xerr=xerr, fmt="o", color="#1f4e79", ecolor="#7aa6c2", capsize=3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlim(0, 1.03)
    ax.set_xlabel("Metric value with 95% bootstrap interval")
    ax.set_title("GridInstruct SD-core Statistical Quality Intervals")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    interval_path = figure_dir / "fig_statistical_quality_intervals.png"
    fig.savefig(interval_path, dpi=220)
    plt.close(fig)

    support_rows = [row for row in rows if row.get("metric") == "macro_f1"]
    labels = [f"{row['family']}\n{row['task']}" for row in support_rows]
    supports = [int(row.get("min_label_support") or 0) for row in support_rows]
    fig, ax = plt.subplots(figsize=(11, max(5, 0.42 * len(support_rows))))
    ax.barh(np.arange(len(support_rows)), supports, color="#2f855a")
    ax.set_yticks(np.arange(len(support_rows)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Minimum held-out support per label")
    ax.set_title("Per-label Support in Classification Evaluations")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    support_path = figure_dir / "fig_statistical_label_support.png"
    fig.savefig(support_path, dpi=220)
    plt.close(fig)
    return {"intervals": str(interval_path.relative_to(ROOT)), "label_support": str(support_path.relative_to(ROOT))}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# SD Statistical Quality Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This audit adds bootstrap confidence intervals and support diagnostics for released model evidence. Exact metric values remain in JSON; manuscript-facing text should use these intervals to avoid over-reading near-perfect point estimates.",
        "",
        "## Formal Evidence Intervals",
        "",
    ]
    for row in report["rows"]:
        if row["family"].startswith("formal"):
            ci = row["ci95"]
            lines.append(
                f"- `{row['family']} | {row['task']}`: {row['metric']}={row['point']:.4f}, "
                f"95% CI=[{ci['lower']:.4f}, {ci['upper']:.4f}], n={row['n']}, bootstrap_n={row.get('bootstrap_n')}"
            )
    lines.extend(["", "## Support Diagnostics", ""])
    for row in report["rows"]:
        if row.get("metric") == "macro_f1":
            lines.append(f"- `{row['family']} | {row['task']}`: min_label_support={row.get('min_label_support')}, labels={row.get('label_counts')}")
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", default="reports/sd_statistical_quality_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/sd_statistical_quality_audit_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    parser.add_argument("--bootstrap", type=int, default=300)
    parser.add_argument("--max-bootstrap-size", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=2051)
    args = parser.parse_args()
    rows = collect_rows(args.bootstrap, args.seed, args.max_bootstrap_size)
    figures = plot_intervals(rows, ROOT / args.figure_dir)
    hard_gates = {
        "formal_transformer_intervals_present": sum(row["family"] == "formal_transformer_standard" for row in rows) == 3,
        "challenge_transformer_intervals_present": sum(row["family"] == "formal_transformer_challenge" for row in rows) == 3,
        "formal_seq2seq_intervals_present": sum(row["family"] == "formal_seq2seq" for row in rows) == 3,
        "all_classification_labels_have_support": all((row.get("min_label_support") or 0) > 0 for row in rows if row.get("metric") == "macro_f1"),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "warn",
        "bootstrap_samples": args.bootstrap,
        "bootstrap_max_sample_size": args.max_bootstrap_size,
        "point_estimate_scope": "full prediction files",
        "interval_scope": "fixed-seed bootstrap over each full file capped by max_bootstrap_size for runtime stability",
        "rows": rows,
        "hard_gates": hard_gates,
        "figures": figures,
    }
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)


if __name__ == "__main__":
    main()
