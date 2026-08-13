#!/usr/bin/env python3
"""Validate and atomically promote an immutable dataset stage."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from gridinstruct_utils import ROOT, atomic_write_text, read_jsonl, write_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_promoted_dataset(output_path: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_hash = sha256(output_path)
    errors = []
    if manifest.get("status") != "pass":
        errors.append("promotion_manifest_not_passing")
    if manifest.get("atomic_promotion") is not True:
        errors.append("promotion_not_recorded_as_atomic")
    if manifest.get("output_sha256") != actual_hash:
        errors.append("canonical_dataset_mutated_after_promotion")
    if manifest.get("input_sha256") != actual_hash:
        errors.append("canonical_dataset_differs_from_final_immutable_stage")
    if errors:
        raise ValueError(";".join(errors))
    rows = read_jsonl(output_path)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "output": str(output_path.relative_to(ROOT)),
        "manifest": str(manifest_path.relative_to(ROOT)),
        "records": len(rows),
        "output_sha256": actual_hash,
        "manifest_sha256": sha256(manifest_path),
        "canonical_dataset_immutable_since_promotion": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--upstream-gate",
        help="Passing transactional stage report that binds the exact promotion input hash.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify that the promoted output still matches its immutable promotion manifest.",
    )
    parser.add_argument("--verification-report")
    args = parser.parse_args()

    output_path = ROOT / args.output
    manifest_path = ROOT / args.manifest
    if args.verify_only:
        if not output_path.is_file() or not manifest_path.is_file():
            raise ValueError("verification requires an existing promoted dataset and manifest")
        report = verify_promoted_dataset(output_path, manifest_path)
        if args.verification_report:
            write_json(ROOT / args.verification_report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if not args.input or not args.upstream_gate:
        raise ValueError("promotion requires --input and --upstream-gate")

    input_path = ROOT / args.input
    gate_path = ROOT / args.upstream_gate
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    transaction = gate.get("transaction") or {}
    bound_artifacts = transaction.get("artifacts") or {}
    input_hash = sha256(input_path)
    if (
        gate.get("status") != "pass"
        or transaction.get("committed") is not True
        or bound_artifacts.get(args.input) != input_hash
    ):
        raise ValueError("promotion input is not bound to a passing committed upstream transaction")

    rows = read_jsonl(input_path)
    ids = [str(row.get("id") or "") for row in rows]
    if not rows or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("promotion input must be non-empty with unique non-empty IDs")
    input_hash = sha256(input_path)
    atomic_write_text(output_path, input_path.read_text(encoding="utf-8"))
    output_hash = sha256(output_path)
    if input_hash != output_hash:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("promoted dataset hash differs from immutable input stage")
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "input": args.input,
        "output": args.output,
        "records": len(rows),
        "input_sha256": input_hash,
        "output_sha256": output_hash,
        "upstream_gate": args.upstream_gate,
        "upstream_gate_sha256": sha256(gate_path),
        "atomic_promotion": True,
    }
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
