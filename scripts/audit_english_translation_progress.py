#!/usr/bin/env python3
"""Audit progress of the English derived-data translation cache."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from translate_sd_core_to_english import contains_cjk, iter_cjk_strings, stable_hash


ROOT = Path(__file__).resolve().parents[1]
PROGRESS_RE = re.compile(r"(\d+)/(\d+) \[([^<\]]+)<([^,\]]+)")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def cache_keys(path: Path) -> tuple[set[str], int, int]:
    keys: set[str] = set()
    valid_lines = 0
    invalid_lines = 0
    if not path.exists():
        return keys, valid_lines, invalid_lines
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                key = row.get("key")
                if isinstance(key, str) and key:
                    keys.add(key)
                    valid_lines += 1
                else:
                    invalid_lines += 1
            except json.JSONDecodeError:
                invalid_lines += 1
    return keys, valid_lines, invalid_lines


def latest_progress(log_path: Path) -> dict[str, Any]:
    if not log_path.exists():
        return {}
    text = log_path.read_text(encoding="utf-8", errors="ignore").replace("\r", "\n")
    matches = list(PROGRESS_RE.finditer(text))
    if not matches:
        return {"log_bytes": log_path.stat().st_size}
    match = matches[-1]
    done = int(match.group(1))
    total = int(match.group(2))
    return {
        "log_bytes": log_path.stat().st_size,
        "batches_done": done,
        "batches_total": total,
        "batch_progress_rate": done / total if total else None,
        "elapsed_display": match.group(3),
        "remaining_display": match.group(4),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# English Translation Progress Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Coverage",
        "",
        f"- Records scanned: {report['records']:,}",
        f"- Source-language fields with CJK characters: {report['total_cjk_fields']:,}",
        f"- Unique source strings requiring translation: {report['unique_source_strings']:,}",
        f"- Cached unique translations: {report['cached_unique_translations']:,}",
        f"- Cache coverage: {report['cache_coverage']:.2%}",
        f"- Remaining unique strings: {report['remaining_unique_strings']:,}",
        f"- Cache valid lines: {report['cache_valid_lines']:,}",
        f"- Cache invalid lines: {report['cache_invalid_lines']:,}",
        "",
        "## Running Log",
        "",
    ]
    progress = report.get("latest_progress") or {}
    if progress:
        for key, value in progress.items():
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- No progress-bar entry found.")
    lines.extend(
        [
            "",
            "## Files",
            "",
            f"- Input: `{report['input']}`",
            f"- Cache: `{report['cache']}`",
            f"- Log: `{report['log']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--cache", default="metadata/translation_cache_v1.jsonl")
    parser.add_argument("--log", default="experiments/sd_core_full_pipeline/10_english_translation/logs/01_translate_full.stderr.log")
    parser.add_argument("--output-json", default="reports/english_translation_progress_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/english_translation_progress_v1.2_sd_core.md")
    args = parser.parse_args()

    input_path = ROOT / args.input
    cache_path = ROOT / args.cache
    log_path = ROOT / args.log
    rows = read_jsonl(input_path)
    unique: dict[str, str] = {}
    total_cjk_fields = 0
    records_with_cjk = 0
    for row in rows:
        row_has_cjk = False
        for _, text in iter_cjk_strings(row):
            if contains_cjk(text):
                row_has_cjk = True
                total_cjk_fields += 1
                unique.setdefault(stable_hash(text), text)
        records_with_cjk += int(row_has_cjk)

    keys, valid_lines, invalid_lines = cache_keys(cache_path)
    cached_unique = len(set(unique) & keys)
    remaining = len(unique) - cached_unique
    now = time.time()
    cache_mtime = cache_path.stat().st_mtime if cache_path.exists() else None
    log_mtime = log_path.stat().st_mtime if log_path.exists() else None
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "cache": args.cache,
        "log": args.log,
        "records": len(rows),
        "records_with_cjk": records_with_cjk,
        "total_cjk_fields": total_cjk_fields,
        "unique_source_strings": len(unique),
        "cached_unique_translations": cached_unique,
        "remaining_unique_strings": remaining,
        "cache_coverage": cached_unique / len(unique) if unique else 1.0,
        "cache_valid_lines": valid_lines,
        "cache_invalid_lines": invalid_lines,
        "cache_size_bytes": cache_path.stat().st_size if cache_path.exists() else 0,
        "cache_age_seconds": None if cache_mtime is None else now - cache_mtime,
        "log_age_seconds": None if log_mtime is None else now - log_mtime,
        "latest_progress": latest_progress(log_path),
    }
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
