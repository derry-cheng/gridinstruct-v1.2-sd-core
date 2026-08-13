#!/usr/bin/env python3
"""Build a unified source-group map for released GridInstruct records."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


def row_hash(row: dict[str, Any]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output-csv", default="metadata/source_group_map.csv")
    parser.add_argument("--output-json", default="reports/source_group_map_audit_v1.2_sd_core.json")
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.dataset)
    output = ROOT / args.output_csv
    ensure_dirs(output.parent, ROOT / "reports")
    fields = [
        "record_id",
        "task_type",
        "split_scope",
        "source_group",
        "source_record_id",
        "scenario_id",
        "template_family",
        "augmentation_type",
        "created_by",
        "row_sha256",
    ]
    task_counts: Counter[str] = Counter()
    source_group_counts: Counter[str] = Counter()
    missing_source_group = 0
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            meta = row.get("metadata") or {}
            sg = source_group(row)
            if not sg:
                missing_source_group += 1
            task = str(row.get("task_type") or "unknown")
            task_counts[task] += 1
            source_group_counts[sg] += 1
            writer.writerow(
                {
                    "record_id": row.get("id", ""),
                    "task_type": task,
                    "split_scope": "release_dataset",
                    "source_group": sg,
                    "source_record_id": source_record_id(row),
                    "scenario_id": row.get("scenario_id") or row.get("source_simulation_case_id") or "",
                    "template_family": meta.get("template_family", ""),
                    "augmentation_type": meta.get("augmentation_type", ""),
                    "created_by": meta.get("created_by", ""),
                    "row_sha256": row_hash(row),
                }
            )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "output_csv": args.output_csv,
        "records": len(rows),
        "unique_source_groups": len(source_group_counts),
        "missing_source_group": missing_source_group,
        "task_counts": dict(sorted(task_counts.items())),
        "status": "pass" if len(rows) > 0 and missing_source_group == 0 else "warn",
    }
    write_json(ROOT / args.output_json, report)


if __name__ == "__main__":
    main()
