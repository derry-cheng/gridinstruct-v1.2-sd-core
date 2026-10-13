"""Append new generated records to an existing GridInstruct snapshot."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl, summarize_records, write_json, write_jsonl


def merge_new_records(base_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]], base_path: str, candidate_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base_ids = {row["id"] for row in base_rows}
    seen_candidate_ids: set[str] = set()
    duplicate_candidate_ids: list[str] = []
    new_rows: list[dict[str, Any]] = []
    merged_at = datetime.now(timezone.utc).isoformat()

    for row in candidate_rows:
        row_id = row.get("id")
        if row_id in seen_candidate_ids:
            duplicate_candidate_ids.append(str(row_id))
            continue
        seen_candidate_ids.add(row_id)
        if row_id in base_ids:
            continue

        copied = dict(row)
        metadata = dict(copied.get("metadata", {}))
        metadata["snapshot_extension"] = {
            "base_snapshot": base_path,
            "candidate_snapshot": candidate_path,
            "merged_at": merged_at,
        }
        copied["metadata"] = metadata
        new_rows.append(copied)

    if duplicate_candidate_ids:
        preview = ", ".join(duplicate_candidate_ids[:10])
        raise ValueError(f"Candidate dataset contains duplicate ids: {preview}")

    return [*base_rows, *new_rows], new_rows


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Dataset Snapshot Extension Report",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Base snapshot: `{report['base_snapshot']}`",
        f"- Candidate snapshot: `{report['candidate_snapshot']}`",
        f"- Output snapshot: `{report['output_snapshot']}`",
        f"- Base records: {report['base_records']}",
        f"- Candidate records: {report['candidate_records']}",
        f"- New records appended: {report['new_records']}",
        f"- Final records: {report['final_records']}",
        "",
        "## New Records By Task",
        "",
    ]
    for task_type, count in sorted(report["new_records_by_task"].items()):
        lines.append(f"- {task_type}: {count}")
    lines.extend(["", "## Final Snapshot Summary", ""])
    for task_type, count in sorted(report["final_summary"]["by_task"].items()):
        lines.append(f"- {task_type}: {count}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report-json", default="reports/dataset_extension_report.json")
    parser.add_argument("--report-md", default="reports/dataset_extension_report.md")
    args = parser.parse_args()

    base_rows = read_jsonl(ROOT / args.base)
    candidate_rows = read_jsonl(ROOT / args.candidate)
    merged_rows, new_rows = merge_new_records(base_rows, candidate_rows, args.base, args.candidate)
    write_jsonl(ROOT / args.output, merged_rows)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_snapshot": args.base,
        "candidate_snapshot": args.candidate,
        "output_snapshot": args.output,
        "base_records": len(base_rows),
        "candidate_records": len(candidate_rows),
        "new_records": len(new_rows),
        "final_records": len(merged_rows),
        "new_records_by_task": dict(Counter(row.get("task_type") for row in new_rows)),
        "new_records_by_stage": dict(Counter(row.get("task_stage") for row in new_rows)),
        "new_records_by_network": dict(Counter(row.get("network_model") for row in new_rows)),
        "final_summary": summarize_records(merged_rows),
    }
    write_json(ROOT / args.report_json, report)
    write_markdown(ROOT / args.report_md, report)


if __name__ == "__main__":
    main()
