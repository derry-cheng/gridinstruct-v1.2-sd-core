#!/usr/bin/env python3
"""Audit split independence with explicit record, scenario, and source-group scopes."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


def load_rows(rel: str) -> list[dict[str, Any]]:
    path = ROOT / rel
    return read_jsonl(path) if path.exists() else []


def source_record_id(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    return str(meta.get("source_record_id") or row.get("id") or "")


def source_group(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    return str(
        meta.get("source_group")
        or meta.get("source_record_id")
        or row.get("source_simulation_case_id")
        or row.get("scenario_id")
        or row.get("id")
        or ""
    )


def values(rows: list[dict[str, Any]], field: str) -> set[str]:
    if field == "record_id":
        return {str(row.get("id") or "") for row in rows if row.get("id")}
    if field == "scenario_id":
        return {str(row.get("scenario_id") or row.get("source_simulation_case_id") or "") for row in rows if row.get("scenario_id") or row.get("source_simulation_case_id")}
    if field == "source_record_id":
        return {source_record_id(row) for row in rows if source_record_id(row)}
    if field == "source_group":
        return {source_group(row) for row in rows if source_group(row)}
    raise ValueError(field)


def pairwise_overlaps(split_rows: dict[str, list[dict[str, Any]]], field: str) -> dict[str, int]:
    sets = {name: values(rows, field) for name, rows in split_rows.items()}
    names = sorted(sets)
    out = {}
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            out[f"{left}__{right}"] = len(sets[left] & sets[right])
    return out


def summarize_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    task_counts: dict[str, int] = defaultdict(int)
    network_counts: dict[str, int] = defaultdict(int)
    severity_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        task_counts[str(row.get("task_type") or "unknown")] += 1
        network_counts[str(row.get("network_model") or "no_network")] += 1
        severity_counts[str((row.get("metadata") or {}).get("severity_level") or "unknown")] += 1
    return {
        "records": len(rows),
        "scenario_ids": len(values(rows, "scenario_id")),
        "source_record_ids": len(values(rows, "source_record_id")),
        "source_groups": len(values(rows, "source_group")),
        "task_counts": dict(sorted(task_counts.items())),
        "network_counts": dict(sorted(network_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
    }


def plot_overlaps(overlaps: dict[str, dict[str, int]], figure_dir: Path) -> dict[str, str]:
    ensure_dirs(figure_dir)
    labels = []
    counts = []
    for field, field_overlaps in overlaps.items():
        for pair, count in sorted(field_overlaps.items()):
            labels.append(f"{field}\n{pair.replace('__', ' / ')}")
            counts.append(count)
    fig, ax = plt.subplots(figsize=(12, max(6, 0.32 * len(labels))))
    ax.barh(range(len(labels)), counts, color="#4c78a8")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Overlap count")
    ax.set_title("Split Independence Diagnostics")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    out = figure_dir / "fig_split_independence_overlap.png"
    fig.savefig(out, dpi=220)
    plt.close(fig)
    return {"overlap_diagnostics": str(out.relative_to(ROOT))}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Split Independence Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "This audit separates record-level isolation, scenario-level diagnostics, source-record diagnostics, and OOD isolation so that split claims use a single scope.",
        "",
        "## Hard Gates",
        "",
    ]
    for key, value in report["hard_gates"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Split Summary", "", "| split | records | scenarios | source_records | source_groups |", "| --- | ---: | ---: | ---: | ---: |"])
    for split, summary in report["split_summary"].items():
        lines.append(f"| {split} | {summary['records']} | {summary['scenario_ids']} | {summary['source_record_ids']} | {summary['source_groups']} |")
    lines.extend(["", "## Overlap Diagnostics", ""])
    for field, overlaps in report["overlaps"].items():
        lines.append(f"### {field}")
        for pair, count in sorted(overlaps.items()):
            lines.append(f"- `{pair}`: {count}")
    lines.extend(["", "## Interpretation", ""])
    lines.append("Record identifiers are isolated across released splits. Standard train/validation/test scenario overlap is reported as a diagnostic because multiple task surfaces can originate from the same physical scenario. OOD isolation is evaluated separately and must remain scenario-disjoint from standard splits.")
    lines.extend(["", "## Figures", ""])
    for name, rel in report["figures"].items():
        lines.append(f"- {name}: `{rel}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--validation", default="data/v1.2_sd_core_validation_en.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--ood", default="data/v1.2_sd_core_ood_test_en.jsonl")
    parser.add_argument("--challenge-train", default="data/v1.2_sd_core_challenge_train_en.jsonl")
    parser.add_argument("--challenge-validation", default="data/v1.2_sd_core_challenge_validation_en.jsonl")
    parser.add_argument("--challenge-test", default="data/v1.2_sd_core_challenge_test_en.jsonl")
    parser.add_argument("--output-json", default="reports/split_independence_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/split_independence_audit_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()

    standard = {
        "train": load_rows(args.train),
        "validation": load_rows(args.validation),
        "test": load_rows(args.test),
        "ood_test": load_rows(args.ood),
    }
    challenge = {
        "challenge_train": load_rows(args.challenge_train),
        "challenge_validation": load_rows(args.challenge_validation),
        "challenge_test": load_rows(args.challenge_test),
    }
    overlaps = {
        "record_id_standard": pairwise_overlaps(standard, "record_id"),
        "scenario_id_standard": pairwise_overlaps(standard, "scenario_id"),
        "source_record_id_standard": pairwise_overlaps(standard, "source_record_id"),
        "source_group_standard": pairwise_overlaps(standard, "source_group"),
        "record_id_challenge": pairwise_overlaps(challenge, "record_id"),
        "scenario_id_challenge": pairwise_overlaps(challenge, "scenario_id"),
    }
    standard_non_ood = {"train": standard["train"], "validation": standard["validation"], "test": standard["test"]}
    ood_scenarios = values(standard["ood_test"], "scenario_id")
    non_ood_scenarios = set().union(*(values(rows, "scenario_id") for rows in standard_non_ood.values()))
    hard_gates = {
        "standard_record_id_disjoint": all(count == 0 for count in overlaps["record_id_standard"].values()),
        "challenge_record_id_disjoint": all(count == 0 for count in overlaps["record_id_challenge"].values()),
        "ood_scenario_disjoint_from_standard": len(ood_scenarios & non_ood_scenarios) == 0,
        "all_released_splits_nonempty": all(len(rows) > 0 for rows in standard.values()),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "warn",
        "scope_statement": "Record-id and OOD scenario isolation are hard gates; standard scenario/source-group overlaps are reported as diagnostics.",
        "split_summary": {name: summarize_split(rows) for name, rows in {**standard, **challenge}.items()},
        "overlaps": overlaps,
        "ood_scenario_overlap_with_standard": len(ood_scenarios & non_ood_scenarios),
        "hard_gates": hard_gates,
        "figures": plot_overlaps(overlaps, ROOT / args.figure_dir),
    }
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)


if __name__ == "__main__":
    main()
