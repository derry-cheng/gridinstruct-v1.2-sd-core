#!/usr/bin/env python3
"""Deterministically rematerialize the English release onto the promoted source hash.

For record IDs present in the previous English view, reuse validated English
natural-language fields and apply the exact scenario-id substitutions recorded by
migrate_dataset_scenario_links. Structured anchors (IDs, labels, queries, network
names, rule links, splits) are taken from the promoted source. Only genuinely new
record IDs are submitted to the translation endpoint pool.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import copy
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tqdm.auto import tqdm

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json, write_jsonl
from translate_sd_core_to_english import (
    append_cache,
    build_endpoint_pool,
    contains_cjk,
    iter_cjk_strings,
    load_cache,
    load_env,
    materialize_records,
    stable_hash,
    translate_batch,
)


STRUCTURED_KEYS = {
    "id",
    "task_type",
    "scenario_id",
    "source_simulation_case_id",
    "network_model",
    "compliance_label",
    "intent",
    "structured_query",
    "query_result",
    "rule_id",
    "secondary_rule_ids",
    "split",
    "source_group",
}

# Natural-language-bearing structured fields: only overlay when the source value
# is already non-CJK. Otherwise keep the validated English text.
CJK_SENSITIVE_STRUCTURED_KEYS = {"slots"}


def value_contains_cjk(value: Any) -> bool:
    if isinstance(value, str):
        return contains_cjk(value)
    if isinstance(value, list):
        return any(value_contains_cjk(item) for item in value)
    if isinstance(value, dict):
        return any(value_contains_cjk(item) for item in value.values())
    return False


def replace_text(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace_text(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: replace_text(item, old, new) for key, item in value.items()}
    return value


def overlay_structured(english: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(english)
    for key in STRUCTURED_KEYS:
        if key not in source:
            continue
        value = source[key]
        if key in CJK_SENSITIVE_STRUCTURED_KEYS and value_contains_cjk(value):
            # Keep validated English slots / NL-bearing structured fields.
            continue
        row[key] = copy.deepcopy(value)

    task_type = source.get("task_type")
    # Closed-label tasks must use the authoritative English enum label.
    if task_type in {"operation_ticket_check", "regulation_compliance_check"}:
        label = source.get("compliance_label")
        if isinstance(label, str) and label:
            row["output"] = label
    else:
        source_output = source.get("output")
        if isinstance(source_output, dict):
            output = dict(row.get("output") or {})
            for key in ("intent", "structured_query", "tool_set", "tools"):
                if key in source_output:
                    output[key] = copy.deepcopy(source_output[key])
            if "slots" in source_output and not value_contains_cjk(source_output["slots"]):
                output["slots"] = copy.deepcopy(source_output["slots"])
            row["output"] = output
        elif isinstance(source_output, str) and not contains_cjk(source_output):
            row["output"] = source_output
        # If source output is Chinese NL, keep the previous English output.

    source_meta = dict(source.get("metadata") or {})
    meta = dict(row.get("metadata") or {})
    for key in (
        "severity_level",
        "issue_profile",
        "source_record_id",
        "construction_prototype_id",
        "source_group",
        "augmentation_type",
        "network_family",
        "scenario_link_migrated_by",
        "migrated_from_scenario_id",
        "replacement_scenario_id",
        "replacement_scenario_sha256",
        "topology_expansion",
        "external_topology_validation",
        "equipment_contingency_validation",
    ):
        if key in source_meta:
            meta[key] = source_meta[key]
        elif key in meta and key not in source_meta:
            # Drop stale parent links that the promoted source no longer carries.
            if key in {"source_record_id"}:
                meta.pop(key, None)
    meta["language"] = "en"
    meta["source_language"] = "zh"
    meta["translation_status"] = meta.get("translation_status") or "machine_translated"
    meta["translation_cache_version"] = "v1"
    meta["english_rematerialized_from_promoted"] = True
    row["metadata"] = meta
    if row.get("network_model") not in (None, ""):
        row["network_model"] = str(row["network_model"]).upper().replace(" ", "")
    return row


def apply_scenario_mapping(row: dict[str, Any], mapping: dict[str, dict[str, Any]]) -> dict[str, Any]:
    meta = row.get("metadata") or {}
    old_id = str(meta.get("migrated_from_scenario_id") or "")
    new_id = str(row.get("scenario_id") or meta.get("replacement_scenario_id") or "")
    if old_id and new_id and old_id != new_id:
        row = replace_text(row, old_id, new_id)
        return row
    # If the row still carries a legacy scenario id that appears in the mapping keys,
    # apply only that one replacement.
    current = str(row.get("scenario_id") or "")
    if current in mapping:
        replacement = str(mapping[current].get("replacement_scenario_id") or "")
        if replacement and replacement != current:
            row = replace_text(row, current, replacement)
    return row


def read_jsonl_by_id(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            rows[str(row["id"])] = row
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--previous-english", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--migration-report", default="reports/dataset_scenario_link_migration_v1.2_sd_core.json")
    parser.add_argument("--cache", default="metadata/translation_cache_v1.jsonl")
    parser.add_argument("--report-json", default="reports/english_rematerialization_v1.2_sd_core.json")
    parser.add_argument("--progress-json", default="reports/english_rematerialization_progress_v1.2_sd_core.json")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--future-timeout", type=float, default=180)
    parser.add_argument("--translate-new", action="store_true", default=True)
    parser.add_argument("--skip-translate-new", action="store_true")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    source_rows = []
    with (ROOT / args.source).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                source_rows.append(json.loads(line))
    previous = read_jsonl_by_id(ROOT / args.previous_english)
    migration = read_json(ROOT / args.migration_report)
    mapping = migration.get("scenario_mapping") or {}

    rematerialized: list[dict[str, Any]] = []
    new_rows: list[dict[str, Any]] = []
    reused = 0
    for source in source_rows:
        record_id = str(source["id"])
        if record_id in previous:
            row = overlay_structured(previous[record_id], source)
            row = apply_scenario_mapping(row, mapping)
            rematerialized.append(row)
            reused += 1
        else:
            new_rows.append(source)

    cache = load_cache(ROOT / args.cache)
    translated_count = 0
    errors: list[str] = []
    failed_batches = 0
    if new_rows and not args.skip_translate_new:
        endpoints = build_endpoint_pool()
        unique: dict[str, str] = {}
        for row in new_rows:
            for _, text in iter_cjk_strings(row):
                unique.setdefault(stable_hash(text), text)
        missing = [(key, text) for key, text in unique.items() if key not in cache]
        batches = [missing[i : i + args.batch_size] for i in range(0, len(missing), args.batch_size)]
        progress_path = ROOT / args.progress_json
        ensure_dirs(progress_path.parent)
        completed = 0
        if batches:
            # Sliding window: never enqueue the entire queue at once.
            window = max(args.workers * 2, args.workers)
            with futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                in_flight: dict[futures.Future, list[tuple[str, str]]] = {}
                batch_iter = iter(batches)
                for _ in range(min(window, len(batches))):
                    batch = next(batch_iter)
                    in_flight[executor.submit(translate_batch, batch, endpoints, args.timeout, args.max_retries)] = batch
                with tqdm(total=len(batches), desc="translate-new") as bar:
                    while in_flight:
                        done, _pending = futures.wait(
                            in_flight.keys(),
                            timeout=args.future_timeout,
                            return_when=futures.FIRST_COMPLETED,
                        )
                        if not done:
                            # No completion within window: keep waiting, but record stall.
                            errors.append(
                                f"stall: no batch completed within {args.future_timeout}s; "
                                f"in_flight={len(in_flight)} completed={completed}/{len(batches)}"
                            )
                            write_json(
                                progress_path,
                                {
                                    "updated_at": datetime.now(timezone.utc).isoformat(),
                                    "completed_batches": completed,
                                    "total_batches": len(batches),
                                    "translated_unique": translated_count,
                                    "failed_batches": failed_batches,
                                    "cache_size": len(cache),
                                    "missing_at_start": len(missing),
                                    "stall": True,
                                    "in_flight": len(in_flight),
                                },
                            )
                            continue
                        for future in done:
                            batch = in_flight.pop(future)
                            try:
                                new_cache_rows, batch_errors = future.result(timeout=1)
                                cache.update(new_cache_rows)
                                append_cache(ROOT / args.cache, new_cache_rows)
                                translated_count += len(new_cache_rows)
                                errors.extend(batch_errors[:20])
                                if not new_cache_rows:
                                    failed_batches += 1
                            except Exception as exc:  # noqa: BLE001
                                failed_batches += 1
                                errors.append(
                                    f"batch failed size={len(batch)} first={batch[0][0]}: {type(exc).__name__}: {exc}"
                                )
                            completed += 1
                            bar.update(1)
                            if completed % 10 == 0 or completed == len(batches):
                                write_json(
                                    progress_path,
                                    {
                                        "updated_at": datetime.now(timezone.utc).isoformat(),
                                        "completed_batches": completed,
                                        "total_batches": len(batches),
                                        "translated_unique": translated_count,
                                        "failed_batches": failed_batches,
                                        "cache_size": len(cache),
                                        "missing_at_start": len(missing),
                                    },
                                )
                            try:
                                nxt = next(batch_iter)
                            except StopIteration:
                                continue
                            in_flight[
                                executor.submit(
                                    translate_batch, nxt, endpoints, args.timeout, args.max_retries
                                )
                            ] = nxt
        # Reload cache in case another process appended concurrently.
        cache = load_cache(ROOT / args.cache)
        translated_new, materialize_stats = materialize_records(new_rows, cache)
    else:
        translated_new, materialize_stats = materialize_records(new_rows, cache) if new_rows else ([], {
            "translated_fields": 0,
            "missing_translations": 0,
            "records_with_cjk_remaining": 0,
        })

    # Preserve promoted source order.
    by_id = {str(row["id"]): row for row in rematerialized}
    by_id.update({str(row["id"]): row for row in translated_new})
    output_rows = [by_id[str(row["id"])] for row in source_rows]

    # Safety net: apply cache to any residual CJK on the full release view
    # (e.g. migrated free-text that still matches a known translation).
    cache = load_cache(ROOT / args.cache)
    output_rows, final_materialize_stats = materialize_records(output_rows, cache)

    # Final CJK gate
    remaining = sum(1 for row in output_rows if any(contains_cjk(text) for _, text in iter_cjk_strings(row)))
    ensure_dirs((ROOT / args.output).parent)
    write_jsonl(ROOT / args.output, output_rows)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if remaining == 0 and len(output_rows) == len(source_rows) else "fail",
        "source": args.source,
        "previous_english": args.previous_english,
        "output": args.output,
        "source_records": len(source_rows),
        "output_records": len(output_rows),
        "reused_english_records": reused,
        "new_records_translated": len(new_rows),
        "new_unique_translations": translated_count,
        "failed_batches": failed_batches,
        "records_with_cjk_remaining": remaining,
        "materialize_new": materialize_stats,
        "materialize_final": final_materialize_stats,
        "task_counts": dict(Counter(row.get("task_type") for row in output_rows)),
        "network_counts": dict(
            Counter(str(row.get("network_model")) for row in output_rows if row.get("network_model"))
        ),
        "errors_sample": errors[:100],
    }
    write_json(ROOT / args.report_json, report)
    print(json.dumps({k: report[k] for k in report if k != "errors_sample"}, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
