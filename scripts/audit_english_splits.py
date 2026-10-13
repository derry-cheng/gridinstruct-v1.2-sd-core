#!/usr/bin/env python3
"""Audit English split files generated from GridInstruct source splits."""

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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def contains_cjk(obj: Any) -> bool:
    if isinstance(obj, str):
        return bool(CJK_RE.search(obj))
    if isinstance(obj, dict):
        return any(contains_cjk(value) for value in obj.values())
    if isinstance(obj, list):
        return any(contains_cjk(value) for value in obj)
    return False


def output_path_for_split(split_path: Path) -> Path:
    return split_path.with_name(f"{split_path.stem}_en{split_path.suffix}")


def discover_source_splits(data_dir: Path, pattern: str) -> list[Path]:
    # The release also contains ID-only lexical diagnostics and stress views.
    # They have no source-language counterpart and must not be interpreted as
    # missing English materialisations.  The default audit is defined for the
    # four official source splits; callers can provide a different closed set
    # through a narrower pattern when needed.
    official = {
        "v1.2_sd_core_train.jsonl",
        "v1.2_sd_core_validation.jsonl",
        "v1.2_sd_core_test.jsonl",
        "v1.2_sd_core_ood_test.jsonl",
    }
    return [path for path in sorted(data_dir.glob(pattern)) if path.name in official]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-pattern", default="v1.2_sd_core*.jsonl")
    parser.add_argument("--report-json", default="reports/english_split_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/english_split_audit_v1.2_sd_core.md")
    parser.add_argument("--max-examples", type=int, default=30)
    args = parser.parse_args()

    split_reports = []
    failures = []
    for source_path in discover_source_splits(ROOT / "data", args.split_pattern):
        english_path = output_path_for_split(source_path)
        if not english_path.exists():
            failures.append({"split": str(source_path.relative_to(ROOT)), "error": "missing_english_split"})
            continue
        source_rows = read_jsonl(source_path)
        english_rows = read_jsonl(english_path)
        source_ids = [row.get("id") for row in source_rows]
        english_ids = [row.get("id") for row in english_rows]
        cjk_ids = [row.get("id") for row in english_rows if contains_cjk(row)]
        non_en_ids = [
            row.get("id")
            for row in english_rows
            if (row.get("metadata") or {}).get("language") != "en"
        ]
        label_mismatch_ids = [
            row.get("id")
            for row in english_rows
            if row.get("task_type") in {"operation_ticket_check", "regulation_compliance_check"}
            and row.get("output") != row.get("compliance_label")
        ]
        split_ok = (
            len(source_rows) == len(english_rows)
            and source_ids == english_ids
            and not cjk_ids
            and not non_en_ids
            and not label_mismatch_ids
        )
        item = {
            "source_split": str(source_path.relative_to(ROOT)),
            "english_split": str(english_path.relative_to(ROOT)),
            "source_records": len(source_rows),
            "english_records": len(english_rows),
            "id_order_aligned": source_ids == english_ids,
            "records_with_cjk": len(cjk_ids),
            "non_en_metadata": len(non_en_ids),
            "classification_output_mismatches": len(label_mismatch_ids),
            "task_counts": dict(collections.Counter(row.get("task_type") for row in english_rows)),
            "status": "pass" if split_ok else "fail",
            "examples": {
                "cjk_ids": cjk_ids[: args.max_examples],
                "non_en_ids": non_en_ids[: args.max_examples],
                "label_mismatch_ids": label_mismatch_ids[: args.max_examples],
            },
        }
        split_reports.append(item)
        if not split_ok:
            failures.append(item)

    # The promoted release retains the canonical source table separately and
    # publishes English-derived split files only.  When source-language split
    # files are absent, audit the released English files directly and leave
    # source-to-English identity alignment to the full-table alignment report.
    if not split_reports and not failures:
        english_paths = [
            ROOT / "data/v1.2_sd_core_train_en.jsonl",
            ROOT / "data/v1.2_sd_core_validation_en.jsonl",
            ROOT / "data/v1.2_sd_core_test_en.jsonl",
            ROOT / "data/v1.2_sd_core_ood_test_en.jsonl",
        ]
        for english_path in english_paths:
            if not english_path.exists():
                failures.append(
                    {
                        "split": str(english_path.relative_to(ROOT)),
                        "error": "missing_english_split",
                    }
                )
                continue
            english_rows = read_jsonl(english_path)
            cjk_ids = [row.get("id") for row in english_rows if contains_cjk(row)]
            non_en_ids = [
                row.get("id")
                for row in english_rows
                if (row.get("metadata") or {}).get("language") != "en"
            ]
            label_mismatch_ids = [
                row.get("id")
                for row in english_rows
                if row.get("task_type")
                in {"operation_ticket_check", "regulation_compliance_check"}
                and row.get("output") != row.get("compliance_label")
            ]
            split_ok = bool(english_rows) and not cjk_ids and not non_en_ids and not label_mismatch_ids
            item = {
                "source_split": None,
                "english_split": str(english_path.relative_to(ROOT)),
                "source_records": None,
                "english_records": len(english_rows),
                "id_order_aligned": None,
                "source_alignment_scope": "full-table source_english_alignment report",
                "records_with_cjk": len(cjk_ids),
                "non_en_metadata": len(non_en_ids),
                "classification_output_mismatches": len(label_mismatch_ids),
                "task_counts": dict(collections.Counter(row.get("task_type") for row in english_rows)),
                "status": "pass" if split_ok else "fail",
                "examples": {
                    "cjk_ids": cjk_ids[: args.max_examples],
                    "non_en_ids": non_en_ids[: args.max_examples],
                    "label_mismatch_ids": label_mismatch_ids[: args.max_examples],
                },
            }
            split_reports.append(item)
            if not split_ok:
                failures.append(item)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "split_pattern": args.split_pattern,
        "split_count": len(split_reports),
        "failed_split_count": len(failures),
        "splits": split_reports,
        "failures": failures[: args.max_examples],
        "status": "pass" if not failures else "fail",
    }
    write_json(ROOT / args.report_json, report)
    md_lines = [
        "# English Split Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Split count: {report['split_count']}",
        f"Failed split count: {report['failed_split_count']}",
        f"Status: `{report['status']}`",
        "",
        "## Splits",
        "",
    ]
    for item in split_reports:
        md_lines.append(
            f"- `{item['english_split']}`: {item['status']}, "
            f"records={item['english_records']}/{item['source_records']}, "
            f"cjk={item['records_with_cjk']}, "
            f"label_mismatch={item['classification_output_mismatches']}"
        )
    (ROOT / args.report_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
