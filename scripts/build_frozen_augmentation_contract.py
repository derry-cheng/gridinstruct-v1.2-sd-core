#!/usr/bin/env python3
"""Materialize the minimum frozen assets needed to replay the plus15 pool.

The deterministic plus12--plus14 augmenters can be rerun from the reviewed
plus11-full state. The plus15 implicit-ticket rows were produced by an LLM, so
their accepted outputs are preserved as a content-addressed source asset and
replayed verbatim. This keeps stochastic provider output outside the
deterministic transformation chain while making the released pool reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl, write_json, write_jsonl


PLUS12_TYPES = {
    "operation_ticket_rulebook_counterfactual",
    "dispatcher_intent_compound_counterfactual",
}
PLUS13_TYPES = {"operation_monitoring_boundary_counterfactual"}
PLUS14_TYPES = {"operation_plain_ticket_adversarial"}
PLUS15_TYPES = {"operation_implicit_ticket_llm"}
POST_PLUS11_TYPES = PLUS12_TYPES | PLUS13_TYPES | PLUS14_TYPES | PLUS15_TYPES


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def augmentation_type(row: dict[str, Any]) -> str:
    return str((row.get("metadata") or {}).get("augmentation_type") or "")


def materialize(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    plus11_full = [row for row in rows if augmentation_type(row) not in POST_PLUS11_TYPES]
    implicit_rows = [row for row in rows if augmentation_type(row) in PLUS15_TYPES]
    expected_order = (
        [row["id"] for row in plus11_full]
        + [row["id"] for row in rows if augmentation_type(row) in PLUS12_TYPES]
        + [row["id"] for row in rows if augmentation_type(row) in PLUS13_TYPES]
        + [row["id"] for row in rows if augmentation_type(row) in PLUS14_TYPES]
        + [row["id"] for row in implicit_rows]
    )
    actual_order = [row["id"] for row in rows]
    duplicate_ids = [record_id for record_id, count in Counter(actual_order).items() if count > 1]
    summary = {
        "input_records": len(rows),
        "plus11_full_records": len(plus11_full),
        "plus12_added_records": sum(augmentation_type(row) in PLUS12_TYPES for row in rows),
        "plus13_added_records": sum(augmentation_type(row) in PLUS13_TYPES for row in rows),
        "plus14_added_records": sum(augmentation_type(row) in PLUS14_TYPES for row in rows),
        "frozen_implicit_records": len(implicit_rows),
        "duplicate_ids": duplicate_ids,
        "stage_append_order_matches": actual_order == expected_order,
    }
    return plus11_full, implicit_rows, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="data/gridinstruct_v1.2_paper_candidate_actionable_plus15.jsonl",
    )
    parser.add_argument(
        "--plus11-full-output",
        default="data/frozen_sources/gridinstruct_v1.2_plus11_full.jsonl",
    )
    parser.add_argument(
        "--implicit-output",
        default="data/frozen_sources/operation_implicit_ticket_llm_v1.jsonl",
    )
    parser.add_argument(
        "--manifest",
        default="metadata/frozen_augmentation_contract_v1.2.json",
    )
    args = parser.parse_args()

    input_path = ROOT / args.input
    plus11_path = ROOT / args.plus11_full_output
    implicit_path = ROOT / args.implicit_output
    manifest_path = ROOT / args.manifest

    rows = read_jsonl(input_path)
    plus11_full, implicit_rows, summary = materialize(rows)
    if summary["duplicate_ids"]:
        raise SystemExit("input contains duplicate record IDs")
    if not summary["stage_append_order_matches"]:
        raise SystemExit("plus15 record order is not a cumulative stage append order")

    write_jsonl(plus11_path, plus11_full)
    write_jsonl(implicit_path, implicit_rows)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": "frozen_reviewed_plus11_plus_deterministic_plus12_14_plus_frozen_llm_plus15",
        "input": args.input,
        "input_sha256": sha256_file(input_path),
        "plus11_full": args.plus11_full_output,
        "plus11_full_sha256": sha256_file(plus11_path),
        "frozen_implicit_rows": args.implicit_output,
        "frozen_implicit_rows_sha256": sha256_file(implicit_path),
        "replay_scripts": [
            "scripts/augment_operation_dispatcher_counterfactuals.py",
            "scripts/augment_operation_monitoring_counterfactuals.py",
            "scripts/augment_operation_plain_ticket_adversarial.py",
            "scripts/replay_frozen_implicit_ticket_rows.py",
        ],
        "summary": summary,
        "passed": True,
    }
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
