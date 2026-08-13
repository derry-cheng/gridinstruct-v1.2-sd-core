"""Create publication-style visual diagnostics for classification runs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


LABEL_NAMES = {
    "compliant": "Compliant",
    "compliant_with_monitoring": "Compliant + Monitoring",
    "non_compliant": "Non-compliant",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def class_metrics(rows: list[dict[str, Any]]) -> tuple[list[str], dict[str, dict[str, float]]]:
    labels = sorted({row["gold"] for row in rows} | {row["prediction"] for row in rows})
    metrics: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = sum(row["gold"] == label and row["prediction"] == label for row in rows)
        fp = sum(row["gold"] != label and row["prediction"] == label for row in rows)
        fn = sum(row["gold"] == label and row["prediction"] != label for row in rows)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        metrics[label] = {
            "support": float(sum(row["gold"] == label for row in rows)),
            "predicted": float(sum(row["prediction"] == label for row in rows)),
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return labels, metrics


def confusion(labels: list[str], rows: list[dict[str, Any]]) -> list[list[int]]:
    return [
        [sum(row["gold"] == gold and row["prediction"] == pred for row in rows) for pred in labels]
        for gold in labels
    ]


def display(label: str) -> str:
    return LABEL_NAMES.get(label, label.replace("_", " ").title())


def save_training_curve(report: dict[str, Any], output: Path, title: str) -> None:
    history = report.get("history") or []
    if not history:
        return
    epochs = [item["epoch"] for item in history]
    loss = [item["train_loss"] for item in history]
    val_f1 = [item["validation_macro_f1"] for item in history]
    fig, ax1 = plt.subplots(figsize=(7.0, 4.2), dpi=180)
    ax1.plot(epochs, loss, marker="o", color="#355C7D", linewidth=2.0, label="Training loss")
    ax1.set_xlabel("Epoch")
    ax1.set_ylabel("Training loss")
    ax1.grid(axis="y", color="#D8DEE9", linewidth=0.8)
    ax2 = ax1.twinx()
    ax2.plot(epochs, val_f1, marker="s", color="#C06C84", linewidth=2.0, label="Validation macro-F1")
    ax2.set_ylabel("Validation macro-F1")
    ax2.set_ylim(0, 1.02)
    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines + lines2, labels + labels2, loc="center right", frameon=False)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_confusion_matrix(labels: list[str], matrix: list[list[int]], output: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(6.2, 5.2), dpi=180)
    im = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(labels)), [display(label) for label in labels], rotation=25, ha="right")
    ax.set_yticks(range(len(labels)), [display(label) for label in labels])
    ax.set_xlabel("Predicted label")
    ax.set_ylabel("Gold label")
    ax.set_title(title)
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            color = "white" if value > max(max(values) for values in matrix) * 0.55 else "#1B1B1B"
            ax.text(j, i, str(value), ha="center", va="center", color=color, fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_metric_bars(labels: list[str], metrics: dict[str, dict[str, float]], output: Path, title: str) -> None:
    x = list(range(len(labels)))
    width = 0.25
    fig, ax = plt.subplots(figsize=(7.4, 4.5), dpi=180)
    for offset, metric, color in [
        (-width, "precision", "#355C7D"),
        (0.0, "recall", "#6C5B7B"),
        (width, "f1", "#C06C84"),
    ]:
        values = [metrics[label][metric] for label in labels]
        ax.bar([item + offset for item in x], values, width=width, label=metric.title(), color=color)
    ax.set_xticks(x, [display(label) for label in labels], rotation=20, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Score")
    ax.set_title(title)
    ax.grid(axis="y", color="#D8DEE9", linewidth=0.8)
    ax.legend(frameon=False, ncol=3, loc="lower right")
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--title", default="Classification Evaluation")
    args = parser.parse_args()

    report_path = Path(args.report)
    predictions_path = Path(args.predictions)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = read_jsonl(predictions_path)
    labels, metrics = class_metrics(rows)
    matrix = confusion(labels, rows)

    save_confusion_matrix(labels, matrix, output_dir / "confusion_matrix.png", f"{args.title}: Confusion Matrix")
    save_metric_bars(labels, metrics, output_dir / "per_class_metrics.png", f"{args.title}: Per-Class Metrics")
    save_training_curve(report, output_dir / "training_curve.png", f"{args.title}: Training Curve")

    with (output_dir / "per_class_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["label", "support", "predicted", "precision", "recall", "f1"])
        writer.writeheader()
        for label in labels:
            writer.writerow({"label": display(label), **metrics[label]})

    summary = {
        "report": str(report_path),
        "predictions": str(predictions_path),
        "n": len(rows),
        "errors": sum(not row["correct"] for row in rows),
        "gold_counts": dict(Counter(row["gold"] for row in rows)),
        "prediction_counts": dict(Counter(row["prediction"] for row in rows)),
        "test_evaluation": report.get("test_evaluation"),
        "figures": [
            "confusion_matrix.png",
            "per_class_metrics.png",
            "training_curve.png",
            "per_class_metrics.csv",
        ],
    }
    (output_dir / "figure_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
