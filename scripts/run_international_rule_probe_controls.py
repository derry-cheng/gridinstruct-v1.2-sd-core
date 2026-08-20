#!/usr/bin/env python3
"""Run explicit-input contract-copy controls for the international rule probe.

This is a leakage diagnostic, not a model baseline. The released input carries
rule, standard, jurisdiction, clause, and evidence fields by design. Copying
those fields should therefore reach 1.0; the result documents why those fields
must be excluded from transfer baselines and why structured scores alone do not
establish legal correctness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json


FIELDS = (
    "rule_id",
    "standard_id",
    "jurisdiction",
    "clause_id",
    "evidence_fields",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def field_value(row: dict[str, Any], field: str) -> Any:
    input_data = row.get("input") or {}
    target = row.get("target_contract") or {}
    if field == "jurisdiction":
        return input_data.get(field, target.get(field, row.get("metadata", {}).get(field)))
    if field == "evidence_fields":
        return input_data.get(field, target.get(field, []))
    return input_data.get(field, target.get(field))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/international_rule_probe_v1.jsonl")
    parser.add_argument("--splits", default="metadata/international_rule_probe_splits_v1.json")
    parser.add_argument("--output", default="reports/international_rule_probe_controls_v1.json")
    parser.add_argument("--output-md", default="reports/international_rule_probe_controls_v1.md")
    args = parser.parse_args()

    data_path = ROOT / args.data
    split_path = ROOT / args.splits
    output_path = ROOT / args.output
    output_md = ROOT / args.output_md
    rows = read_jsonl(data_path)
    rows_by_id = {str(row["id"]): row for row in rows}
    split_manifest = read_json(split_path)
    folds: list[dict[str, Any]] = []
    errors: list[str] = []
    for fold in split_manifest.get("folds") or []:
        test_rows = [rows_by_id[row_id] for row_id in fold["test_ids"]]
        field_scores = {
            field: sum(
                float(field_value(row, field) == (row.get("target_contract") or {}).get(field))
                for row in test_rows
            ) / max(len(test_rows), 1)
            for field in FIELDS
        }
        if any(score != 1.0 for score in field_scores.values()):
            errors.append(f"copy_control_not_one:{fold['name']}")
        folds.append(
            {
                "name": fold["name"],
                "fold_type": fold.get("fold_type"),
                "test_records": len(test_rows),
                "explicit_input_contract_copy": field_scores,
            }
        )
    aggregate = {
        field: sum(float(fold["explicit_input_contract_copy"][field]) for fold in folds) / max(len(folds), 1)
        for field in FIELDS
    }
    report = {
        "status": "pass" if not errors else "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": args.data,
        "data_sha256": sha256(data_path),
        "split_manifest": args.splits,
        "split_manifest_sha256": sha256(split_path),
        "record_count": len(rows),
        "folds": folds,
        "aggregate_explicit_input_contract_copy": aggregate,
        "copied_fields": list(FIELDS),
        "interpretation": (
            "This control copies fields already present in the released input; 1.0 is expected by construction. "
            "It is a leakage diagnostic and does not measure legal correctness, physical compliance, or model quality."
        ),
        "errors": errors,
    }
    write_json(output_path, report)
    lines = [
        "# International rule-probe explicit-input controls",
        "",
        f"Status: `{report['status']}`",
        "",
        "The control copies explicit contract fields already present in the input; a score of 1.0 is expected by construction.",
        "",
        "| Fold | N | Rule | Standard | Jurisdiction | Clause | Evidence |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for fold in folds:
        scores = fold["explicit_input_contract_copy"]
        lines.append(
            f"| {fold['name']} | {fold['test_records']} | {scores['rule_id']:.3f} | {scores['standard_id']:.3f} | "
            f"{scores['jurisdiction']:.3f} | {scores['clause_id']:.3f} | {scores['evidence_fields']:.3f} |"
        )
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "record_count": len(rows), "folds": len(folds), "errors": errors}))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
