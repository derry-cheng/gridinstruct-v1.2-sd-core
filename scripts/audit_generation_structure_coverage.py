#!/usr/bin/env python3
"""Audit aggregate structure coverage for formal SD-core generation tasks."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def safe_json(value: Any) -> Any | None:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return None


def summarize_predictions(path: Path) -> dict[str, Any]:
    rows = read_jsonl(path)
    task_counts = Counter(str(row.get("task_type") or "unknown") for row in rows)
    token_f1_values = [float(row.get("token_f1")) for row in rows if row.get("token_f1") is not None]
    char_f1_values = [float(row.get("char_f1")) for row in rows if row.get("char_f1") is not None]
    exact_values = [bool(row.get("exact_match")) for row in rows if "exact_match" in row]
    prediction_lengths = [len(str(row.get("prediction") or "")) for row in rows]
    gold_lengths = [len(str(row.get("gold") or row.get("target") or "")) for row in rows]
    json_rows = [row for row in rows if "prediction_is_json" in row]
    query_rows = [row for row in rows if "structured_query_field_accuracy" in row]
    tool_rows = [row for row in rows if "tool_set_jaccard" in row]

    gold_key_counts: Counter[str] = Counter()
    filter_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    for row in rows:
        parsed = safe_json(row.get("gold") or row.get("target"))
        if isinstance(parsed, dict):
            for key in parsed:
                gold_key_counts[str(key)] += 1
            query = parsed.get("structured_query") if isinstance(parsed.get("structured_query"), dict) else None
            if query:
                for key in query:
                    gold_key_counts[f"structured_query.{key}"] += 1
                if query.get("filter") is not None:
                    filter_counts[str(query.get("filter"))] += 1
            plan = parsed.get("tool_plan") if isinstance(parsed.get("tool_plan"), list) else None
            if plan:
                for item in plan:
                    if isinstance(item, dict) and item.get("tool"):
                        tool_counts[str(item["tool"])] += 1

    return {
        "path": str(path.relative_to(ROOT)),
        "records": len(rows),
        "task_counts": dict(task_counts),
        "token_f1_mean": mean(token_f1_values) if token_f1_values else None,
        "char_f1_mean": mean(char_f1_values) if char_f1_values else None,
        "exact_match_rate": sum(exact_values) / len(exact_values) if exact_values else None,
        "prediction_length_mean": mean(prediction_lengths) if prediction_lengths else None,
        "gold_length_mean": mean(gold_lengths) if gold_lengths else None,
        "prediction_is_json_rate": (
            sum(1 for row in json_rows if row.get("prediction_is_json")) / len(json_rows) if json_rows else None
        ),
        "structured_query_field_accuracy_mean": (
            mean(float(row.get("structured_query_field_accuracy") or 0.0) for row in query_rows) if query_rows else None
        ),
        "tool_set_jaccard_mean": mean(float(row.get("tool_set_jaccard") or 0.0) for row in tool_rows) if tool_rows else None,
        "gold_key_counts": dict(gold_key_counts),
        "filter_counts": dict(filter_counts),
        "tool_counts": dict(tool_counts),
    }


def report_summary(report_path: Path, prediction_path: Path) -> dict[str, Any]:
    report = read_json(report_path)
    predictions = summarize_predictions(prediction_path)
    return {
        "report": str(report_path.relative_to(ROOT)),
        "predictions": predictions,
        "task_types": report.get("task_types", []),
        "target_mode": (report.get("config") or {}).get("target_mode"),
        "prompt_mode": (report.get("config") or {}).get("prompt_mode"),
        "test_evaluation": report.get("test_evaluation", {}),
        "metric_definition": report.get("metric_definition"),
    }


def metric_applicability(normalization: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {
            "task_type": "regulation_qa",
            "metric": "token_f1",
            "applicability": "primary text-overlap metric",
            "value": None,
        },
        {
            "task_type": "auxiliary_decision",
            "metric": "char_f1/canonical_json_exact/schema_valid/tool_set_exact/tool_set_jaccard",
            "applicability": "applies to tool-plan style auxiliary targets",
            "value": None,
        },
        {
            "task_type": "intelligent_data_query",
            "metric": "char_f1/canonical_json_exact/schema_valid/structured_query_exact/field_accuracy",
            "applicability": "applies to structured query targets",
            "value": None,
        },
    ]
    for task, item in (normalization.get("tasks") or {}).items():
        if task == "intelligent_data_query":
            rows.append(
                {
                    "task_type": task,
                    "metric": "tool_set_exact/tool_set_jaccard",
                    "applicability": "not a tool-plan target; reported as not applicable for query claims",
                    "value": item.get("tool_set_exact"),
                }
            )
        if task == "auxiliary_decision":
            rows.append(
                {
                    "task_type": task,
                    "metric": "structured_query_exact/field_accuracy",
                    "applicability": "not a structured-query target; reported as not applicable for auxiliary claims",
                    "value": item.get("structured_query_field_accuracy"),
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    fieldnames = ["task_type", "metric", "applicability", "value"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def plot(report: dict[str, Any], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    labels, values = [], []
    for item in report["runs"]:
        task = "+".join(item.get("task_types") or ["unknown"])
        evals = item.get("test_evaluation") or {}
        for metric in ("token_f1", "char_f1", "prediction_is_json", "prediction_schema_valid", "canonical_json_exact", "structured_query_field_accuracy", "tool_set_jaccard"):
            if metric in evals:
                labels.append(f"{task}\n{metric}")
                values.append(float(evals[metric]))
    fig, ax = plt.subplots(figsize=(11, max(5, 0.42 * len(labels))))
    ax.barh(np.arange(len(labels)), values, color="#7c3aed")
    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.03)
    ax.set_xlabel("Metric value")
    ax.set_title("Generation Structure Coverage and Applicability")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    path = figure_dir / "fig_generation_structure_coverage.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return {"generation_structure_coverage": str(path.relative_to(ROOT))}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Generation Structure Coverage Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This audit reports aggregate structure coverage for formal generation tasks. It distinguishes text-generation, structured-query, and tool-plan metrics so non-applicable metric fields are not interpreted as failures.",
        "",
        "## Gates",
        "",
    ]
    for key, value in report["hard_gates"].items():
        lines.append(f"- `{key}`: {'pass' if value else 'fail'}")
    lines.extend(["", "## Formal Runs", "", "| task | n | named overlap F1 | JSON validity | structured field accuracy | tool Jaccard |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for item in report["runs"]:
        task = "+".join(item.get("task_types") or ["unknown"])
        evals = item.get("test_evaluation") or {}
        pred = item["predictions"]
        lines.append(
            f"| {task} | {pred['records']} | {evals.get('token_f1', evals.get('char_f1', 'NA'))} | {evals.get('prediction_is_json', 'NA')} | {evals.get('structured_query_field_accuracy', 'NA')} | {evals.get('tool_set_jaccard', 'NA')} |"
        )
    lines.extend(["", "## Metric Applicability", "", "| task | metric | applicability | reported value |", "| --- | --- | --- | ---: |"])
    for row in report["metric_applicability"]:
        lines.append(f"| {row['task_type']} | {row['metric']} | {row['applicability']} | {row.get('value', 'NA')} |")
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--normalization", default="reports/structured_prediction_normalization_v1.2_sd_core.json")
    parser.add_argument("--output-json", default="reports/generation_structure_coverage_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/generation_structure_coverage_audit_v1.2_sd_core.md")
    parser.add_argument("--applicability-csv", default="benchmark/v1.2_sd_core_generation_metric_applicability.csv")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()

    prefixes = [
        "benchmark/v1.2_sd_core_seq2seq_regqa",
        "benchmark/v1.2_sd_core_seq2seq_auxiliary_decision",
        "benchmark/v1.2_sd_core_seq2seq_intelligent_data_query",
    ]
    runs = []
    missing = []
    for prefix in prefixes:
        report_path = ROOT / f"{prefix}_report.json"
        pred_path = ROOT / f"{prefix}_test_predictions.jsonl"
        if not report_path.exists() or not pred_path.exists():
            missing.append({"report": str(report_path.relative_to(ROOT)), "predictions": str(pred_path.relative_to(ROOT))})
            continue
        runs.append(report_summary(report_path, pred_path))
    normalization = read_json(ROOT / args.normalization) if (ROOT / args.normalization).exists() else {}
    applicability = metric_applicability(normalization)
    hard_gates = {
        "all_formal_generation_reports_present": len(missing) == 0 and len(runs) == len(prefixes),
        "prediction_counts_match_reports": all(
            int((item.get("test_evaluation") or {}).get("n", item["predictions"]["records"])) == item["predictions"]["records"]
            for item in runs
        ),
        "normalization_report_passed": normalization.get("overall_status") == "pass",
        "metric_applicability_documented": len(applicability) >= 3,
        "no_empty_prediction_files": all(item["predictions"]["records"] > 0 for item in runs),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "warn",
        "runs": runs,
        "normalization": normalization,
        "metric_applicability": applicability,
        "missing": missing,
        "hard_gates": hard_gates,
        "interpretation": "Token-F1, structured-query metrics, and tool-plan metrics have task-specific applicability; non-applicable zero fields are excluded from task claims.",
    }
    report["figures"] = plot(report, ROOT / args.figure_dir)
    write_csv(ROOT / args.applicability_csv, applicability)
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)
    print({"status": report["status"], "runs": len(runs)})


if __name__ == "__main__":
    main()
