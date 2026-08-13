#!/usr/bin/env python3
"""Materialize English split files from the translated full GridInstruct table."""

from __future__ import annotations

import argparse
import collections
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
DERIVED_TRANSLATION_METADATA_KEYS = {
    "language",
    "source_language",
    "translation_status",
    "translation_cache_version",
    "english_rematerialized_from_promoted",
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


def materialize_row(source: dict[str, Any], translated: dict[str, Any]) -> dict[str, Any]:
    row = overlay_allowed_translations(source, translated)
    if "metadata" in source and isinstance(row.get("metadata"), dict):
        translated_metadata = translated.get("metadata") or {}
        for key in DERIVED_TRANSLATION_METADATA_KEYS:
            if key in translated_metadata:
                row["metadata"][key] = copy.deepcopy(translated_metadata[key])
        # Preserve the source schema while adding the English-release language
        # marker only to views that already expose a metadata object. A reduced
        # projection must not regain fields removed by its source contract.
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


def translate_rows_via_pipeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate any residual CJK string values inside `rows` by delegating to
    the translation pipeline (cache + provider). Structure and non-CJK values
    are preserved; only CJK strings are translated. Used for split-specific
    signature fields (e.g. challenge_group_key) and any input fields not
    overlaid from the canonical English table."""
    if not rows:
        return rows
    import subprocess
    import sys
    temp_src = ROOT / "data" / "_materialize_residual_source.jsonl"
    temp_en = ROOT / "data" / "_materialize_residual_en.jsonl"
    write_jsonl(temp_src, rows)
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "translate_sd_core_to_english.py"),
        "--input",
        str(temp_src),
        "--output",
        str(temp_en),
        "--cache",
        "metadata/translation_cache_v1.jsonl",
        "--report-json",
        "reports/_materialize_residual_translation.json",
        "--report-md",
        "reports/_materialize_residual_translation.md",
        "--shuffle-missing",
    ]
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    out = read_jsonl(temp_en)
    temp_src.unlink(missing_ok=True)
    temp_en.unlink(missing_ok=True)
    return out


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
    parser.add_argument("--translated-full", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
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

    translated_full_path = ROOT / args.translated_full
    translated_rows = read_jsonl(translated_full_path)
    translated_by_id = {row["id"]: row for row in translated_rows}
    duplicate_translated_ids = [
        item
        for item, count in collections.Counter(row["id"] for row in translated_rows).items()
        if count > 1
    ]

    split_paths = (
        [ROOT / item for item in args.split]
        if args.split
        else discover_splits(ROOT / "data", args.split_pattern)
    )
    # Augmentation records (boundary / challenge / counterfactual variants) are
    # derived records that live only in split files and therefore have no
    # counterpart in the translated canonical full table. Translate any such
    # missing records on demand via the same translation pipeline (cache +
    # provider) so every released split has a complete English view. Each missing
    # id is translated exactly once and the cache is reused across runs.
    missing_source_rows: list[dict[str, Any]] = []
    seen_missing: set[str] = set()
    for split_path in split_paths:
        for row in read_jsonl(split_path):
            rid = str(row.get("id") or "")
            if rid and rid not in translated_by_id and rid not in seen_missing:
                seen_missing.add(rid)
                missing_source_rows.append(row)
    if missing_source_rows:
        import subprocess
        import sys
        temp_src = ROOT / "data" / "_materialize_missing_source.jsonl"
        temp_en = ROOT / "data" / "_materialize_missing_en.jsonl"
        write_jsonl(temp_src, missing_source_rows)
        translate_cmd = [
            sys.executable,
            str(ROOT / "scripts" / "translate_sd_core_to_english.py"),
            "--input",
            str(temp_src),
            "--output",
            str(temp_en),
            "--cache",
            "metadata/translation_cache_v1.jsonl",
            "--report-json",
            "reports/_materialize_missing_translation.json",
            "--report-md",
            "reports/_materialize_missing_translation.md",
            "--shuffle-missing",
        ]
        subprocess.run(translate_cmd, cwd=str(ROOT), check=True)
        translated_rows.extend(read_jsonl(temp_en))
        translated_by_id = {row["id"]: row for row in translated_rows}
        temp_src.unlink(missing_ok=True)
        temp_en.unlink(missing_ok=True)
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
            if row_id in translated_by_id:
                materialized = materialize_row(row, translated_by_id[row_id])
                output_rows.append(materialized)
                if structure_signature(row) != structure_signature(materialized):
                    structure_mismatch_ids.append(str(row_id))
            else:
                missing_ids.append(row_id)

        # Translate residual CJK in split/augmentation-specific fields (e.g.
        # operation_group, plain_ticket_boundary_key) that the canonical
        # projection could not overlay. Preserves structure; only CJK string
        # values are translated via the cache-backed translation pipeline.
        cjk_rows = [r for r in output_rows if contains_cjk(r)]
        if cjk_rows:
            translated_cjk = {r["id"]: r for r in translate_rows_via_pipeline(cjk_rows)}
            output_rows = [
                translated_cjk.get(r["id"], r) if contains_cjk(r) else r
                for r in output_rows
            ]

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
        "translated_full": args.translated_full,
        "translated_full_records": len(translated_rows),
        "duplicate_translated_ids": duplicate_translated_ids[:50],
        "duplicate_translated_id_count": len(duplicate_translated_ids),
        "split_pattern": args.split_pattern,
        "split_count": len(split_reports),
        "missing_total": missing_total,
        "splits": split_reports,
        "structure_mismatch_total": sum(item["structure_mismatch_count"] for item in split_reports),
        "status": "pass"
        if not duplicate_translated_ids
        and missing_total == 0
        and all(item["structure_isomorphic"] for item in split_reports)
        else "fail",
    }
    write_json(ROOT / args.report_json, report)
    md_lines = [
        "# English Split Materialization",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Translated full records: {report['translated_full_records']}",
        f"Split count: {report['split_count']}",
        f"Missing total: {report['missing_total']}",
        f"Duplicate translated IDs: {report['duplicate_translated_id_count']}",
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
