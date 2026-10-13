#!/usr/bin/env python3
"""Append an immutable, hash-pinned record source with strict ID checks."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from gridinstruct_utils import ROOT, read_jsonl, write_json, write_jsonl


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dataset", default=None)
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--checksum-manifest")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    dataset_path = ROOT / args.dataset
    output_path = ROOT / (args.output_dataset or args.dataset)
    source_path = ROOT / args.source
    expected_hash = args.expected_source_sha256
    if args.checksum_manifest:
        manifest = json.loads((ROOT / args.checksum_manifest).read_text(encoding="utf-8"))
        expected_hash = manifest.get(args.source)
    if not expected_hash:
        raise ValueError("an expected source hash or checksum manifest entry is required")
    actual_source_hash = sha256(source_path)
    if actual_source_hash != expected_hash:
        raise ValueError(
            f"frozen source hash mismatch: expected {expected_hash}, "
            f"got {actual_source_hash}"
        )
    rows = read_jsonl(dataset_path)
    frozen = read_jsonl(source_path)
    dataset_ids = [str(row.get("id") or "") for row in rows]
    frozen_ids = [str(row.get("id") or "") for row in frozen]
    if not frozen:
        raise ValueError("frozen source is empty")
    if any(not value for value in dataset_ids + frozen_ids):
        raise ValueError("dataset or frozen source contains a missing ID")
    if len(dataset_ids) != len(set(dataset_ids)):
        raise ValueError("dataset contains duplicate IDs before frozen append")
    if len(frozen_ids) != len(set(frozen_ids)):
        raise ValueError("frozen source contains duplicate IDs")
    existing = {row["id"]: row for row in rows}
    conflicts = [row["id"] for row in frozen if row["id"] in existing and existing[row["id"]] != row]
    if conflicts:
        raise ValueError(f"frozen source ID conflicts: {conflicts[:20]}")
    to_add = [row for row in frozen if row["id"] not in existing]
    rows.extend(to_add)
    if output_path != dataset_path or to_add:
        write_jsonl(output_path, rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "dataset": args.dataset,
        "output_dataset": str(output_path.relative_to(ROOT)),
        "source": args.source,
        "source_sha256": actual_source_hash,
        "source_records": len(frozen),
        "records_appended": len(to_add),
        "records_after": len(rows),
        "dataset_sha256_after": sha256(output_path),
    }
    write_json(ROOT / args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
