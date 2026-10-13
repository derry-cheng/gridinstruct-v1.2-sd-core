#!/usr/bin/env python3
"""Audit probability calibration for SD-core transformer classifiers."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


RUNS = [
    ("standard", "operation_ticket_check", "benchmark/v1.2_sd_core_transformer_operation_ticket_check_test_predictions.jsonl"),
    ("standard", "regulation_compliance_check", "benchmark/v1.2_sd_core_transformer_regulation_compliance_check_test_predictions.jsonl"),
    ("standard", "dispatcher_intent_tool_call", "benchmark/v1.2_sd_core_transformer_dispatcher_intent_tool_call_test_predictions.jsonl"),
    ("challenge", "operation_ticket_check", "benchmark/v1.2_sd_core_challenge_transformer_operation_ticket_check_test_predictions.jsonl"),
    ("challenge", "regulation_compliance_check", "benchmark/v1.2_sd_core_challenge_transformer_regulation_compliance_check_test_predictions.jsonl"),
    ("challenge", "dispatcher_intent_tool_call", "benchmark/v1.2_sd_core_challenge_transformer_dispatcher_intent_tool_call_test_predictions.jsonl"),
]


def calibration_stats(rows: list[dict[str, Any]], bins: int) -> dict[str, Any]:
    confidences = []
    correctness = []
    brier_values = []
    for row in rows:
        probs = row.get("probabilities") or {}
        if not probs:
            continue
        labels = sorted(str(label) for label in probs)
        prob_arr = np.array([float(probs[label]) for label in labels], dtype=float)
        pred_label = labels[int(np.argmax(prob_arr))]
        gold = str(row.get("gold"))
        confidences.append(float(np.max(prob_arr)))
        correctness.append(1.0 if pred_label == gold else 0.0)
        target = np.array([1.0 if label == gold else 0.0 for label in labels], dtype=float)
        brier_values.append(float(np.sum((prob_arr - target) ** 2)))
    if not confidences:
        return {"n": len(rows), "probability_rows": 0, "ece": None, "brier": None, "bins": []}
    conf = np.array(confidences, dtype=float)
    corr = np.array(correctness, dtype=float)
    bin_edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    bin_rows = []
    for i in range(bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mask = (conf >= lo) & (conf <= hi if i == bins - 1 else conf < hi)
        count = int(mask.sum())
        if count == 0:
            bin_rows.append({"lower": float(lo), "upper": float(hi), "count": 0, "confidence": None, "accuracy": None})
            continue
        bin_conf = float(conf[mask].mean())
        bin_acc = float(corr[mask].mean())
        ece += (count / len(conf)) * abs(bin_acc - bin_conf)
        bin_rows.append({"lower": float(lo), "upper": float(hi), "count": count, "confidence": bin_conf, "accuracy": bin_acc})
    return {
        "n": len(rows),
        "probability_rows": len(confidences),
        "ece": float(ece),
        "brier": float(np.mean(brier_values)),
        "accuracy": float(corr.mean()),
        "mean_confidence": float(conf.mean()),
        "bins": bin_rows,
    }


def plot_reliability(results: list[dict[str, Any]], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    selected = [row for row in results if row.get("probability_rows")]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), sharex=True, sharey=True)
    axes_flat = axes.ravel()
    for ax, row in zip(axes_flat, selected):
        xs = [item["confidence"] for item in row["bins"] if item["confidence"] is not None]
        ys = [item["accuracy"] for item in row["bins"] if item["accuracy"] is not None]
        counts = [item["count"] for item in row["bins"] if item["confidence"] is not None]
        sizes = [max(20, min(220, count / max(counts) * 220)) if counts else 30 for count in counts]
        ax.plot([0, 1], [0, 1], color="#888888", linestyle="--", linewidth=1)
        ax.scatter(xs, ys, s=sizes, color="#1f77b4", alpha=0.85)
        ax.set_title(f"{row['split']} | {row['task']}\nECE={row['ece']:.3f}, Brier={row['brier']:.3f}")
        ax.grid(alpha=0.25)
    for ax in axes_flat[len(selected) :]:
        ax.axis("off")
    fig.supxlabel("Mean confidence")
    fig.supylabel("Empirical accuracy")
    fig.tight_layout()
    out = figure_dir / "fig_calibration_reliability.png"
    fig.savefig(out, dpi=220)
    plt.close(fig)
    return {"reliability": str(out.relative_to(ROOT))}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Calibration Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "Expected calibration error (ECE) compares confidence with empirical accuracy across confidence bins. Brier score is the mean squared distance between predicted class probabilities and one-hot labels.",
        "",
        "| split | task | n | ECE | Brier | accuracy | mean confidence |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["runs"]:
        ece = "NA" if row["ece"] is None else f"{row['ece']:.4f}"
        brier = "NA" if row["brier"] is None else f"{row['brier']:.4f}"
        acc = "NA" if row.get("accuracy") is None else f"{row['accuracy']:.4f}"
        conf = "NA" if row.get("mean_confidence") is None else f"{row['mean_confidence']:.4f}"
        lines.append(f"| {row['split']} | {row['task']} | {row['n']} | {ece} | {brier} | {acc} | {conf} |")
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", default="reports/calibration_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/calibration_audit_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    parser.add_argument("--bins", type=int, default=10)
    args = parser.parse_args()
    results = []
    for split, task, rel in RUNS:
        path = ROOT / rel
        if not path.exists():
            continue
        stats = calibration_stats(read_jsonl(path), args.bins)
        results.append({"split": split, "task": task, "path": rel, **stats})
    hard_gates = {
        "standard_probability_files_present": sum(row["split"] == "standard" and row["probability_rows"] > 0 for row in results) == 3,
        "challenge_probability_files_present": sum(row["split"] == "challenge" and row["probability_rows"] > 0 for row in results) == 3,
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "warn",
        "hard_gates": hard_gates,
        "runs": results,
        "figures": plot_reliability(results, ROOT / args.figure_dir),
    }
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)


if __name__ == "__main__":
    main()
