#!/usr/bin/env python3
"""Summarize strict split and proxy-reduced evidence for SD-core."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from gridinstruct_utils import ROOT, read_json, write_json, ensure_dirs


def classification_scores(report: dict[str, Any], split_key: str = "test_metrics") -> dict[str, float]:
    out = {}
    for task, metrics in (report.get(split_key) or {}).items():
        if "macro_f1" in metrics:
            out[task] = float(metrics["macro_f1"])
    return out


def strict_scores(strict_tfidf: dict[str, Any]) -> dict[str, float]:
    return classification_scores(strict_tfidf, "test_metrics")


def proxy_scores(proxy_report: dict[str, Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in proxy_report.get("rows", []):
        if row.get("profile") != "proxy_reduced":
            continue
        out.setdefault(row["split"], {})[row["task_type"]] = float(row["macro_f1"])
    return out


def plot(summary: dict[str, Any], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    tasks = sorted(
        set(summary["standard_tfidf_classification"]) |
        set(summary["strict_tfidf_classification"]) |
        set(summary["proxy_reduced_standard"]) |
        set(summary["proxy_reduced_strict"])
    )
    series = [
        ("standard TF-IDF", summary["standard_tfidf_classification"], "#4c78a8"),
        ("strict TF-IDF", summary["strict_tfidf_classification"], "#f58518"),
        ("standard proxy-reduced", summary["proxy_reduced_standard"], "#54a24b"),
        ("strict proxy-reduced", summary["proxy_reduced_strict"], "#b279a2"),
    ]
    x = np.arange(len(tasks))
    width = 0.18
    fig, ax = plt.subplots(figsize=(12, 5.8))
    for idx, (name, data, color) in enumerate(series):
        ax.bar(x + (idx - 1.5) * width, [data.get(task, 0.0) for task in tasks], width=width, label=name, color=color)
    ax.set_xticks(x)
    ax.set_xticklabels(tasks, rotation=20, ha="right")
    ax.set_ylim(0, 1.03)
    ax.set_ylabel("Macro-F1")
    ax.set_title("Strict Split and Proxy-Reduced Classification Evidence")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="lower right")
    fig.tight_layout()
    path = figure_dir / "fig_strict_split_scores.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return {"strict_split_scores": str(path.relative_to(ROOT))}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Strict Split Evidence Summary",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This report summarizes the additional source-group-disjoint split and proxy-reduced input audit. These results are interpretation evidence for leakage resistance and do not replace the official release split.",
        "",
        "| task | standard TF-IDF | strict TF-IDF | standard proxy-reduced | strict proxy-reduced |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for task in report["tasks"]:
        lines.append(
            f"| {task} | {report['standard_tfidf_classification'].get(task, 0.0):.4f} | "
            f"{report['strict_tfidf_classification'].get(task, 0.0):.4f} | "
            f"{report['proxy_reduced_standard'].get(task, 0.0):.4f} | "
            f"{report['proxy_reduced_strict'].get(task, 0.0):.4f} |"
        )
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-split", default="reports/strict_source_group_split_v1.2_sd_core.json")
    parser.add_argument("--standard-tfidf", default="benchmark/v1.2_sd_core_tfidf_report.json")
    parser.add_argument("--strict-tfidf", default="benchmark/v1.2_sd_core_strict_tfidf_report.json")
    parser.add_argument("--proxy-reduced", default="reports/proxy_reduced_input_audit_v1.2_sd_core.json")
    parser.add_argument("--output-json", default="reports/strict_split_evidence_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/strict_split_evidence_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()
    strict_split = read_json(ROOT / args.strict_split)
    standard_tfidf = read_json(ROOT / args.standard_tfidf)
    strict_tfidf = read_json(ROOT / args.strict_tfidf)
    proxy_reduced = read_json(ROOT / args.proxy_reduced)
    proxy = proxy_scores(proxy_reduced)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if strict_split.get("status") == "pass" and proxy_reduced.get("status") == "pass" else "warn",
        "strict_split_status": strict_split.get("status"),
        "strict_split_paths": strict_split.get("split_paths"),
        "standard_tfidf_classification": classification_scores(standard_tfidf),
        "strict_tfidf_classification": strict_scores(strict_tfidf),
        "proxy_reduced_standard": proxy.get("standard", {}),
        "proxy_reduced_strict": proxy.get("strict_source_group", {}),
    }
    summary["tasks"] = sorted(
        set(summary["standard_tfidf_classification"]) |
        set(summary["strict_tfidf_classification"]) |
        set(summary["proxy_reduced_standard"]) |
        set(summary["proxy_reduced_strict"])
    )
    summary["figures"] = plot(summary, ROOT / args.figure_dir)
    write_json(ROOT / args.output_json, summary)
    write_markdown(ROOT / args.output_md, summary)
    print({"status": summary["status"], "tasks": len(summary["tasks"])})


if __name__ == "__main__":
    main()
