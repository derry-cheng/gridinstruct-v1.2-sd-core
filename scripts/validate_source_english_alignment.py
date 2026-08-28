#!/usr/bin/env python3
"""Fail closed when source and English release views diverge on stable fields."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl, write_json


STABLE_FIELDS = (
    "id",
    "task_stage",
    "task_type",
    "network_model",
    "scenario_id",
    "source_simulation_case_id",
    "source_regulation_ids",
    "compliance_label",
    "intent",
    "structured_query",
    "query_result",
    "rule_id",
    "secondary_rule_ids",
    "split",
    "source_group",
)
STABLE_METADATA_FIELDS = (
    "action_category",
    "augmentation_type",
    "construction_prototype_id",
    "counterfactual_intent",
    "counterfactual_role",
    "equipment_contingency_validation",
    "external_topology_validation",
    "issue_profile",
    "migrated_from_scenario_id",
    "network_family",
    # operation_group is a provenance grouping key built from Chinese
    # action|object surface text; it is legitimately translated in the English
    # view (each release uses its own value consistently), so it is NOT a
    # language-neutral stable field and is excluded from the identity gate.
    "replacement_scenario_id",
    "replacement_scenario_sha256",
    "scenario_link_migrated_by",
    "severity_level",
    "source_group",
    "source_record_id",
    "topology_expansion",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_projection(row: dict[str, Any]) -> dict[str, Any]:
    projection = {field: row.get(field) for field in STABLE_FIELDS if field in row}
    metadata = row.get("metadata") or {}
    projection["metadata"] = {
        field: metadata.get(field)
        for field in STABLE_METADATA_FIELDS
        if field in metadata
    }
    output = row.get("output")
    if isinstance(output, dict):
        projection["structured_output"] = {
            field: output.get(field)
            for field in ("intent", "structured_query", "tool_set", "tools")
            if field in output
        }
    return projection


def stable_projection_on_source_schema(
    source: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    projection = {
        field: row.get(field)
        for field in STABLE_FIELDS
        if field in source
    }
    source_metadata = source.get("metadata") or {}
    row_metadata = row.get("metadata") or {}
    projection["metadata"] = {
        field: row_metadata.get(field)
        for field in STABLE_METADATA_FIELDS
        if field in source_metadata
    }
    source_output = source.get("output")
    row_output = row.get("output")
    if isinstance(source_output, dict):
        row_output = row_output if isinstance(row_output, dict) else {}
        projection["structured_output"] = {
            field: row_output.get(field)
            for field in ("intent", "structured_query", "tool_set", "tools")
            if field in source_output
        }
    return projection


def discover_source_splits(data_dir: Path, pattern: str) -> list[Path]:
    # The default pattern also matches ID-only diagnostic manifests (for
    # example exact-surface and near-neighbour splits) and stress views that
    # deliberately have no source-language projection.  The source/English
    # alignment gate is defined for the four official source splits; callers
    # can still pass any additional closed split explicitly with ``--split``.
    official = {
        "v1.2_sd_core_train.jsonl",
        "v1.2_sd_core_validation.jsonl",
        "v1.2_sd_core_test.jsonl",
        "v1.2_sd_core_ood_test.jsonl",
    }
    return [
        path
        for path in sorted(data_dir.glob(pattern))
        if path.name in official
    ]


def english_path(source_path: Path) -> Path:
    return source_path.with_name(f"{source_path.stem}_en{source_path.suffix}")


def compare_pair(
    source_path: Path,
    translated_path: Path,
    translated_full_by_id: dict[str, dict[str, Any]],
    max_examples: int,
) -> dict[str, Any]:
    source_rows = read_jsonl(source_path)
    translated_rows = read_jsonl(translated_path)
    source_ids = [str(row.get("id") or "") for row in source_rows]
    translated_ids = [str(row.get("id") or "") for row in translated_rows]
    translated_by_id = {str(row.get("id") or ""): row for row in translated_rows}
    stable_mismatches = []
    full_projection_mismatches = []
    stable_mismatch_count = 0
    full_projection_mismatch_count = 0
    classification_output_mismatches = []
    classification_output_mismatch_count = 0
    for source_row in source_rows:
        record_id = str(source_row.get("id") or "")
        translated_row = translated_by_id.get(record_id)
        if translated_row is None:
            continue
        source_projection = stable_projection_on_source_schema(source_row, source_row)
        translated_projection = stable_projection_on_source_schema(source_row, translated_row)
        if source_projection != translated_projection:
            stable_mismatch_count += 1
            if len(stable_mismatches) < max_examples:
                stable_mismatches.append(record_id)
        full_row = translated_full_by_id.get(record_id)
        # The projection check verifies that a split record's English view is
        # consistent with the English FULL table. Augmentation/boundary/
        # counterfactual records legitimately live only in split files and are
        # not present in the canonical full table (full_row is None); they have
        # no full-table counterpart to project from, so they are skipped rather
        # than counted as mismatches.
        if (
            full_row is not None
            and stable_projection_on_source_schema(source_row, full_row)
            != translated_projection
        ):
            full_projection_mismatch_count += 1
            if len(full_projection_mismatches) < max_examples:
                full_projection_mismatches.append(record_id)
        if (
            translated_row.get("task_type") in {"operation_ticket_check", "regulation_compliance_check"}
            and translated_row.get("output") != translated_row.get("compliance_label")
        ):
            classification_output_mismatch_count += 1
            if len(classification_output_mismatches) < max_examples:
                classification_output_mismatches.append(record_id)
    duplicate_source_ids = len(source_ids) - len(set(source_ids))
    duplicate_translated_ids = len(translated_ids) - len(set(translated_ids))
    status = "pass" if (
        source_ids == translated_ids
        and not duplicate_source_ids
        and not duplicate_translated_ids
        and stable_mismatch_count == 0
        and full_projection_mismatch_count == 0
        and classification_output_mismatch_count == 0
    ) else "fail"
    return {
        "source": str(source_path.relative_to(ROOT)),
        "english": str(translated_path.relative_to(ROOT)),
        "source_sha256": sha256(source_path),
        "english_sha256": sha256(translated_path),
        "source_records": len(source_rows),
        "english_records": len(translated_rows),
        "id_order_aligned": source_ids == translated_ids,
        "duplicate_source_ids": duplicate_source_ids,
        "duplicate_english_ids": duplicate_translated_ids,
        "stable_field_mismatch_count": stable_mismatch_count,
        "stable_field_mismatch_examples": stable_mismatches,
        "english_full_projection_mismatch_count": full_projection_mismatch_count,
        "english_full_projection_mismatch_examples": full_projection_mismatches,
        "classification_output_mismatch_count": classification_output_mismatch_count,
        "classification_output_mismatch_examples": classification_output_mismatches,
        "status": status,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-full", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--english-full", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--split-pattern", default="v1.2_sd_core*.jsonl")
    parser.add_argument(
        "--split",
        action="append",
        default=[],
        help="Explicit source split path; repeat to audit a closed split set.",
    )
    parser.add_argument("--report-json", default="reports/source_english_alignment_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/source_english_alignment_v1.2_sd_core.md")
    parser.add_argument("--max-examples", type=int, default=50)
    args = parser.parse_args()

    source_full_path = ROOT / args.source_full
    english_full_path = ROOT / args.english_full
    source_full = read_jsonl(source_full_path)
    english_full = read_jsonl(english_full_path)
    source_full_ids = [str(row.get("id") or "") for row in source_full]
    english_full_ids = [str(row.get("id") or "") for row in english_full]
    english_full_by_id = {str(row.get("id") or ""): row for row in english_full}
    full_stable_mismatches = [
        str(source_row.get("id") or "")
        for source_row in source_full
        if str(source_row.get("id") or "") not in english_full_by_id
        or stable_projection(source_row) != stable_projection(english_full_by_id[str(source_row.get("id") or "")])
    ]
    full_classification_output_mismatches = [
        str(row.get("id") or "")
        for row in english_full
        if row.get("task_type") in {"operation_ticket_check", "regulation_compliance_check"}
        and row.get("output") != row.get("compliance_label")
    ]

    split_reports = []
    missing_english_splits = []
    source_split_paths = (
        [ROOT / item for item in args.split]
        if args.split
        else discover_source_splits(ROOT / "data", args.split_pattern)
    )
    for source_path in source_split_paths:
        if not source_path.is_file():
            missing_english_splits.append(str(source_path.relative_to(ROOT)))
            continue
        translated_path = english_path(source_path)
        if not translated_path.is_file():
            missing_english_splits.append(str(translated_path.relative_to(ROOT)))
            continue
        split_reports.append(
            compare_pair(source_path, translated_path, english_full_by_id, args.max_examples)
        )

    full_ok = (
        source_full_ids == english_full_ids
        and len(source_full_ids) == len(set(source_full_ids))
        and len(english_full_ids) == len(set(english_full_ids))
        and not full_stable_mismatches
        and not full_classification_output_mismatches
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass"
        if full_ok
        and not missing_english_splits
        and split_reports
        and all(item["status"] == "pass" for item in split_reports)
        else "fail",
        "source_full": args.source_full,
        "english_full": args.english_full,
        "source_full_sha256": sha256(source_full_path),
        "english_full_sha256": sha256(english_full_path),
        "source_full_records": len(source_full),
        "english_full_records": len(english_full),
        "full_id_order_aligned": source_full_ids == english_full_ids,
        "full_stable_field_mismatch_count": len(full_stable_mismatches),
        "full_stable_field_mismatch_examples": full_stable_mismatches[: args.max_examples],
        "full_classification_output_mismatch_count": len(full_classification_output_mismatches),
        "full_classification_output_mismatch_examples": full_classification_output_mismatches[: args.max_examples],
        "split_pattern": args.split_pattern,
        "split_count": len(split_reports),
        "missing_english_splits": missing_english_splits,
        "splits": split_reports,
        "stable_fields": list(STABLE_FIELDS),
        "stable_metadata_fields": list(STABLE_METADATA_FIELDS),
    }
    write_json(ROOT / args.report_json, report)
    lines = [
        "# Source/English Alignment Gate",
        "",
        f"- Status: `{report['status']}`",
        f"- Full records: {len(source_full)}/{len(english_full)}",
        f"- Full stable-field mismatches: {len(full_stable_mismatches)}",
        f"- Audited splits: {len(split_reports)}",
        f"- Missing English splits: {len(missing_english_splits)}",
        "",
    ]
    for item in split_reports:
        lines.append(
            f"- `{item['source']}` -> `{item['english']}`: {item['status']}, "
            f"records={item['source_records']}/{item['english_records']}, "
            f"stable_mismatches={item['stable_field_mismatch_count']}"
        )
    (ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
