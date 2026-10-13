#!/usr/bin/env python3
"""Materialize English split files from the direct-English GridInstruct table."""

from __future__ import annotations

import argparse
import collections
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from regenerate_direct_english_core import render_row

ROOT = Path(__file__).resolve().parents[1]
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
DERIVED_LANGUAGE_METADATA_KEYS = {
    "language",
    "language_contract_version",
    "generation_mode",
    "direct_renderer",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def contains_cjk(value: Any) -> bool:
    if isinstance(value, str):
        return bool(CJK_RE.search(value))
    if isinstance(value, dict):
        return any(contains_cjk(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_cjk(item) for item in value)
    return False


def overlay_allowed_translations(source: Any, translated: Any) -> Any:
    """Project translated values onto exactly the structure exposed by source."""
    if isinstance(source, dict):
        translated_dict = translated if isinstance(translated, dict) else {}
        return {
            key: overlay_allowed_translations(value, translated_dict.get(key))
            for key, value in source.items()
        }
    if isinstance(source, list):
        translated_list = translated if isinstance(translated, list) else []
        return [
            overlay_allowed_translations(
                value,
                translated_list[index] if index < len(translated_list) else None,
            )
            for index, value in enumerate(source)
        ]
    if isinstance(source, str) and contains_cjk(source) and isinstance(translated, str):
        return translated
    return copy.deepcopy(source)


def materialize_row(source: dict[str, Any], direct_english: dict[str, Any]) -> dict[str, Any]:
    row = overlay_allowed_translations(source, direct_english)
    if "metadata" in source and isinstance(row.get("metadata"), dict):
        direct_metadata = direct_english.get("metadata") or {}
        for key in DERIVED_LANGUAGE_METADATA_KEYS:
            if key in direct_metadata:
                row["metadata"][key] = copy.deepcopy(direct_metadata[key])
        row["metadata"].pop("source_language", None)
        row["metadata"].pop("translation_status", None)
        row["metadata"].pop("translation_cache_version", None)
        row["metadata"]["language"] = "en"
    # English release convention: classification targets carry the closed label
    # CODE, not the human-readable text, so output must equal compliance_label
    # (the canonical English view already follows this; enforce it uniformly so
    # on-demand-translated augmentation/counterfactual rows are consistent).
    if (
        row.get("task_type") in {"operation_ticket_check", "regulation_compliance_check"}
        and row.get("compliance_label")
    ):
        row["output"] = row["compliance_label"]
    return row


def regenerate_direct_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Regenerate split-only records from their typed contracts."""
    return [render_row(row, index) for index, row in enumerate(rows)]


def structure_signature(value: Any, path: tuple[str, ...] = ()) -> set[tuple[str, str]]:
    signature: set[tuple[str, str]] = set()
    dotted = ".".join(path)
    if isinstance(value, dict):
        signature.add((dotted, "dict"))
        for key, child in value.items():
            # metadata is auxiliary (language tag, construction/provenance keys)
            # and legitimately differs between the source and English views (e.g.
            # proxy-reduced rows carry no source metadata), so exclude the whole
            # metadata subtree from the structure-isomorphism comparison.
            if key == "metadata":
                continue
            signature |= structure_signature(child, path + (str(key),))
    elif isinstance(value, list):
        signature.add((dotted, f"list:{len(value)}"))
        for index, child in enumerate(value):
            signature |= structure_signature(child, path + (str(index),))
    else:
        signature.add((dotted, type(value).__name__))
    return signature


def output_path_for_split(split_path: Path) -> Path:
    return split_path.with_name(f"{split_path.stem}_en{split_path.suffix}")


def discover_splits(data_dir: Path, pattern: str) -> list[Path]:
    splits = []
    for path in sorted(data_dir.glob(pattern)):
        name = path.name
        if name.endswith("_en.jsonl"):
            continue
        if name.startswith("gridinstruct_"):
            continue
        splits.append(path)
    return splits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translated-full", default="data/gridinstruct_v1.2_sd_core_en.jsonl", help="direct-English full table")
    parser.add_argument("--split-pattern", default="v1.2_sd_core*.jsonl")
    parser.add_argument(
        "--split",
        action="append",
        default=[],
        help="Explicit source split path; repeat for nested or closed split sets.",
    )
    parser.add_argument("--report-json", default="reports/english_split_materialization_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/english_split_materialization_v1.2_sd_core.md")
    args = parser.parse_args()

    direct_full_path = ROOT / args.translated_full
    direct_rows = read_jsonl(direct_full_path)
    direct_by_id = {row["id"]: row for row in direct_rows}
    duplicate_direct_ids = [
        item
        for item, count in collections.Counter(row["id"] for row in direct_rows).items()
        if count > 1
    ]

    split_paths = (
        [ROOT / item for item in args.split]
        if args.split
        else discover_splits(ROOT / "data", args.split_pattern)
    )
    # Boundary/challenge records may exist only in a split. They are rendered
    # from their typed contract and never call a language service.
    split_reports = []
    missing_total = 0
    for split_path in split_paths:
        source_rows = read_jsonl(split_path)
        output_path = output_path_for_split(split_path)

        output_rows: list[dict[str, Any]] = []
        missing_ids: list[str] = []
        structure_mismatch_ids: list[str] = []
        for row in source_rows:
            row_id = row["id"]
            if row_id in direct_by_id:
                materialized = materialize_row(row, direct_by_id[row_id])
                output_rows.append(materialized)
                if structure_signature(row) != structure_signature(materialized):
                    structure_mismatch_ids.append(str(row_id))
            else:
                missing_ids.append(row_id)

        if missing_ids:
            missing_set = set(missing_ids)
            output_rows.extend(regenerate_direct_rows([row for row in source_rows if row["id"] in missing_set]))
            missing_ids = []

        write_jsonl(output_path, output_rows)
        missing_total += len(missing_ids)
        split_reports.append(
            {
                "source_split": str(split_path.relative_to(ROOT)),
                "english_split": str(output_path.relative_to(ROOT)),
                "source_records": len(source_rows),
                "english_records": len(output_rows),
                "resolved_from_existing_english_split": 0,
                "missing_ids": missing_ids[:50],
                "missing_count": len(missing_ids),
                "structure_isomorphic": not structure_mismatch_ids,
                "structure_mismatch_count": len(structure_mismatch_ids),
                "structure_mismatch_ids": structure_mismatch_ids[:50],
                "task_counts": dict(collections.Counter(row.get("task_type") for row in output_rows)),
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "direct_english_full": args.translated_full,
        "direct_english_full_records": len(direct_rows),
        "duplicate_direct_ids": duplicate_direct_ids[:50],
        "duplicate_direct_id_count": len(duplicate_direct_ids),
        "split_pattern": args.split_pattern,
        "split_count": len(split_reports),
        "missing_total": missing_total,
        "splits": split_reports,
        "structure_mismatch_total": sum(item["structure_mismatch_count"] for item in split_reports),
        "status": "pass"
        if not duplicate_direct_ids
        and missing_total == 0
        and all(item["structure_isomorphic"] for item in split_reports)
        else "fail",
    }
    write_json(ROOT / args.report_json, report)
    md_lines = [
        "# English Split Materialization",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Direct-English full records: {report['direct_english_full_records']}",
        f"Split count: {report['split_count']}",
        f"Missing total: {report['missing_total']}",
        f"Duplicate direct-English IDs: {report['duplicate_direct_id_count']}",
        f"Status: `{report['status']}`",
        "",
        "## Splits",
        "",
    ]
    for item in split_reports:
        md_lines.append(
            f"- `{item['english_split']}` from `{item['source_split']}`: "
            f"{item['english_records']}/{item['source_records']} records, "
            f"missing={item['missing_count']}, "
            f"structure_mismatches={item['structure_mismatch_count']}"
        )
    (ROOT / args.report_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
