#!/usr/bin/env python3
"""Replay accepted implicit-ticket LLM rows onto a deterministic plus14 pool."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl, write_json, write_jsonl


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replay(base_rows: list[dict[str, Any]], frozen_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base_ids = {str(row.get("id")) for row in base_rows}
    frozen_ids = [str(row.get("id")) for row in frozen_rows]
    if len(frozen_ids) != len(set(frozen_ids)):
        raise ValueError("frozen implicit rows contain duplicate IDs")
    overlap = sorted(base_ids.intersection(frozen_ids))
    if overlap:
        raise ValueError(f"frozen implicit IDs already exist in base: {overlap[:3]}")
    for row in frozen_rows:
        if (row.get("metadata") or {}).get("augmentation_type") != "operation_implicit_ticket_llm":
            raise ValueError(f"unexpected frozen row type for {row.get('id')}")
    return list(base_rows) + list(frozen_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="data/gridinstruct_v1.2_paper_candidate_actionable_plus14.jsonl",
    )
    parser.add_argument(
        "--frozen-rows",
        default="data/frozen_sources/operation_implicit_ticket_llm_v1.jsonl",
    )
    parser.add_argument(
        "--output",
        default="data/gridinstruct_v1.2_paper_candidate_actionable_plus15_replayed.jsonl",
    )
    parser.add_argument(
        "--expected-output",
        default="",
        help="Optional prior output for exact byte comparison. Omit when creating a new release.",
    )
    parser.add_argument(
        "--report",
        default="reports/frozen_implicit_ticket_replay_v1.2.json",
    )
    args = parser.parse_args()

    input_path = ROOT / args.input
    frozen_path = ROOT / args.frozen_rows
    output_path = ROOT / args.output
    expected_path = ROOT / args.expected_output if args.expected_output else None
    report_path = ROOT / args.report

    base_rows = read_jsonl(input_path)
    frozen_rows = read_jsonl(frozen_path)
    output = replay(base_rows, frozen_rows)
    write_jsonl(output_path, output)

    output_sha256 = sha256_file(output_path)
    expected_sha256 = sha256_file(expected_path) if expected_path and expected_path.exists() else None
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "input_records": len(base_rows),
        "input_sha256": sha256_file(input_path),
        "frozen_rows": args.frozen_rows,
        "frozen_records": len(frozen_rows),
        "frozen_sha256": sha256_file(frozen_path),
        "output": args.output,
        "output_records": len(output),
        "output_sha256": output_sha256,
        "expected_output": args.expected_output or None,
        "expected_output_sha256": expected_sha256,
        "exact_byte_replay": None if expected_sha256 is None else output_sha256 == expected_sha256,
    }
    report["passed"] = expected_sha256 is None or bool(report["exact_byte_replay"])
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit("replayed plus15 does not match expected output")


if __name__ == "__main__":
    main()
