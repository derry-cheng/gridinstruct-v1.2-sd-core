#!/usr/bin/env python3
"""Merge deterministic scenario sources into a single rebuild manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_json, write_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument(
        "--exclude-scenario-ids-from-report",
        default=None,
        help="Passing migration report whose invalid legacy scenario IDs are replaced by a current source.",
    )
    parser.add_argument("--output", default="simulation_outputs/contingency/scenario_rebuild_manifest.json")
    parser.add_argument("--report", default="reports/scenario_source_merge_v1.2_sd_core.json")
    args = parser.parse_args()

    excluded_ids: set[str] = set()
    replacement_ids: set[str] = set()
    exclusion_source: str | None = None
    exclusion_report = None
    if args.exclude_scenario_ids_from_report:
        exclusion_path = ROOT / args.exclude_scenario_ids_from_report
        exclusion_report = read_json(exclusion_path)
        excluded_ids = set(exclusion_report.get("invalid_legacy_scenario_ids") or [])
        replacement_ids = {
            str(item.get("replacement_scenario_id") or "")
            for item in (exclusion_report.get("scenario_migration") or {}).values()
        }
        exclusion_source = str(exclusion_report.get("legacy_scenarios") or "")
        if (
            exclusion_report.get("status") != "pass"
            or not excluded_ids
            or len(replacement_ids) != len(excluded_ids)
            or "" in replacement_ids
            or exclusion_source not in args.source
            or exclusion_report.get("cardinality_preserved") is not True
            or exclusion_report.get("ids_preserved") is not True
        ):
            raise ValueError("scenario exclusion report is not a passing cardinality-preserving migration")

    merged: dict[str, dict[str, Any]] = {}
    source_rows = []
    exact_duplicate_ids: list[str] = []
    for source in args.source:
        path = ROOT / source
        rows = read_json(path)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"scenario source is empty or not a list: {source}")
        converged_before_exclusion = [row for row in rows if row.get("solver_status") == "converged"]
        converged = [
            row for row in converged_before_exclusion
            if not (
                source == exclusion_source
                and str(row.get("scenario_id") or "") in excluded_ids
            )
        ]
        source_ids = [str(row.get("scenario_id") or "") for row in converged]
        if any(not value for value in source_ids):
            raise ValueError(f"missing scenario_id in {source}")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"duplicate scenario_id inside {source}")
        for row in converged:
            scenario_id = str(row["scenario_id"])
            if scenario_id in merged:
                if merged[scenario_id] != row:
                    raise ValueError(f"conflicting duplicate scenario_id across sources: {scenario_id}")
                exact_duplicate_ids.append(scenario_id)
                continue
            merged[scenario_id] = row
        source_rows.append(
            {
                "path": source,
                "sha256": sha256(path),
                "records": len(rows),
                "converged_records_before_exclusion": len(converged_before_exclusion),
                "excluded_migrated_scenarios": len(converged_before_exclusion) - len(converged),
                "converged_records": len(converged),
            }
        )

    if replacement_ids - set(merged):
        raise ValueError(
            "migration replacements missing from merged current sources: "
            f"{sorted(replacement_ids - set(merged))[:20]}"
        )
    output_rows = [merged[key] for key in sorted(merged)]
    if not output_rows:
        raise ValueError("no converged scenarios were available to merge")
    output_path = ROOT / args.output
    write_json(output_path, output_rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "sources": source_rows,
        "source_records_total": sum(row["records"] for row in source_rows),
        "unique_converged_scenarios": len(output_rows),
        "exact_duplicate_scenario_ids_deduplicated": sorted(set(exact_duplicate_ids)),
        "exclusion_report": args.exclude_scenario_ids_from_report,
        "excluded_migrated_scenario_ids": sorted(excluded_ids),
        "excluded_migrated_scenario_count": len(excluded_ids),
        "migration_replacement_scenario_ids": sorted(replacement_ids),
        "migration_replacements_present": replacement_ids.issubset(merged),
        "output": args.output,
        "output_sha256": sha256(output_path),
        "duplicate_policy": "exact duplicates are deduplicated; conflicting duplicate IDs fail before writing",
    }
    write_json(ROOT / args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
