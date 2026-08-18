#!/usr/bin/env python3
"""Regenerate the four public GridInstruct splits with the direct-English renderer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gridinstruct_utils import ROOT
from regenerate_direct_english_core import read_jsonl, render_row, write_jsonl_atomic


DEFAULT_SPLITS = ("train", "validation", "test", "ood_test")
DEFAULT_ADDITIONAL_ENGLISH = (
    "data/v1.2_sd_core_boundary_challenge_test_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_train_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_validation_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_test_en.jsonl",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--splits", nargs="+", default=list(DEFAULT_SPLITS))
    parser.add_argument("--additional-english", nargs="*", default=list(DEFAULT_ADDITIONAL_ENGLISH))
    parser.add_argument("--skip-core", action="store_true")
    parser.add_argument("--report-json", default="reports/direct_english_split_materialization_v1.2_sd_core.json")
    args = parser.parse_args()

    split_reports = []
    for split in ([] if args.skip_core else args.splits):
        source_path = ROOT / f"data/v1.2_sd_core_{split}.jsonl"
        english_path = ROOT / f"data/v1.2_sd_core_{split}_en.jsonl"
        source_rows = read_jsonl(source_path)
        rendered = [render_row(row, i) for i, row in enumerate(source_rows)]
        write_jsonl_atomic(source_path, rendered)
        write_jsonl_atomic(english_path, rendered)
        split_reports.append(
            {
                "split": split,
                "records": len(rendered),
                "source": str(source_path.relative_to(ROOT)),
                "english": str(english_path.relative_to(ROOT)),
                "generation_mode": "direct_english_from_typed_contract",
                "language_contract_version": "direct-en-v2",
                "source_language_fields_present": any(
                    key in (row.get("metadata") or {})
                    for row in rendered
                    for key in ("source_language", "translation_status", "translation_cache_version")
                ),
            }
        )
    for relative_path in args.additional_english:
        path = ROOT / relative_path
        if not path.exists():
            continue
        rows = read_jsonl(path)
        rendered = [render_row(row, i) for i, row in enumerate(rows)]
        write_jsonl_atomic(path, rendered)
        split_reports.append(
            {
                "split": path.name,
                "records": len(rendered),
                "source": None,
                "english": str(path.relative_to(ROOT)),
                "generation_mode": "direct_english_from_typed_contract",
                "language_contract_version": "direct-en-v2",
                "source_language_fields_present": any(
                    key in (row.get("metadata") or {})
                    for row in rendered
                    for key in ("source_language", "translation_status", "translation_cache_version")
                ),
            }
        )
    report = {
        "status": "pass" if all(not item["source_language_fields_present"] for item in split_reports) else "fail",
        "renderer": "regenerate_direct_english_core.py",
        "splits": split_reports,
        "records": sum(item["records"] for item in split_reports),
    }
    output = ROOT / args.report_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
