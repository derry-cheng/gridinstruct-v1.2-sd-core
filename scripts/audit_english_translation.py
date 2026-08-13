#!/usr/bin/env python3
"""Audit an English derived GridInstruct JSONL file."""

from __future__ import annotations

import argparse
import collections
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def contains_cjk(value: Any) -> bool:
    return isinstance(value, str) and bool(CJK_RE.search(value))


def walk_strings(obj: Any, path: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            rows.extend(walk_strings(value, path + (str(key),)))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            rows.extend(walk_strings(value, path + (str(idx),)))
    elif isinstance(obj, str):
        rows.append((".".join(path), obj))
    return rows


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--translated", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--report-json", default="reports/english_translation_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/english_translation_audit_v1.2_sd_core.md")
    parser.add_argument("--max-examples", type=int, default=30)
    args = parser.parse_args()

    source = read_jsonl(ROOT / args.source)
    translated = read_jsonl(ROOT / args.translated)

    source_ids = [row.get("id") for row in source]
    translated_ids = [row.get("id") for row in translated]
    id_aligned = source_ids == translated_ids
    source_id_set = set(source_ids)
    translated_id_set = set(translated_ids)

    cjk_by_field: collections.Counter[str] = collections.Counter()
    cjk_examples: list[dict[str, str]] = []
    records_with_cjk = 0
    language_metadata = collections.Counter()
    task_counts = collections.Counter()
    classification_output_mismatches: list[dict[str, str]] = []

    for row in translated:
        task_counts[row.get("task_type", "NA")] += 1
        if row.get("task_type") in {"operation_ticket_check", "regulation_compliance_check"}:
            if row.get("output") != row.get("compliance_label"):
                classification_output_mismatches.append(
                    {
                        "id": str(row.get("id")),
                        "task_type": str(row.get("task_type")),
                        "output": str(row.get("output")),
                        "compliance_label": str(row.get("compliance_label")),
                    }
                )
        metadata = row.get("metadata") or {}
        language_metadata[str(metadata.get("language", "NA"))] += 1
        row_has_cjk = False
        for path, value in walk_strings(row):
            if contains_cjk(value):
                row_has_cjk = True
                cjk_by_field[path] += 1
                if len(cjk_examples) < args.max_examples:
                    cjk_examples.append(
                        {
                            "id": str(row.get("id")),
                            "task_type": str(row.get("task_type")),
                            "field": path,
                            "text": value[:240],
                        }
                    )
        records_with_cjk += int(row_has_cjk)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "translated": args.translated,
        "source_records": len(source),
        "translated_records": len(translated),
        "record_count_match": len(source) == len(translated),
        "id_order_aligned": id_aligned,
        "missing_ids": sorted(source_id_set - translated_id_set)[: args.max_examples],
        "extra_ids": sorted(translated_id_set - source_id_set)[: args.max_examples],
        "records_with_cjk": records_with_cjk,
        "records_with_cjk_rate": records_with_cjk / len(translated) if translated else 0.0,
        "cjk_by_field_top": cjk_by_field.most_common(50),
        "cjk_examples": cjk_examples,
        "classification_output_mismatch_count": len(classification_output_mismatches),
        "classification_output_mismatch_examples": classification_output_mismatches[: args.max_examples],
        "language_metadata": dict(language_metadata),
        "task_counts": dict(task_counts),
        "status": "pass"
        if len(source) == len(translated)
        and id_aligned
        and records_with_cjk == 0
        and not classification_output_mismatches
        else "warn",
    }

    write_json(ROOT / args.report_json, report)
    md_lines = [
        "# English Translation Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Source records: {report['source_records']}",
        f"Translated records: {report['translated_records']}",
        f"ID order aligned: {report['id_order_aligned']}",
        f"Records with CJK remaining: {report['records_with_cjk']} ({report['records_with_cjk_rate']:.4%})",
        f"Classification output mismatches: {report['classification_output_mismatch_count']}",
        f"Status: `{report['status']}`",
        "",
        "## Top CJK Fields",
        "",
    ]
    for field, count in report["cjk_by_field_top"][:20]:
        md_lines.append(f"- `{field}`: {count}")
    md_lines.extend(["", "## Examples", ""])
    for item in cjk_examples[:10]:
        md_lines.append(f"- `{item['id']}` `{item['field']}`: {item['text']}")
    (ROOT / args.report_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
