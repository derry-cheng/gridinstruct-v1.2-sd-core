#!/usr/bin/env python3
"""Freeze the retained legacy topology records and their scenario descriptors."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json, write_jsonl


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--augmentation-tag", default="extended_topology_instruction_v1")
    parser.add_argument("--records-output", default="data/frozen_sources/extended_topology_instruction_v1.jsonl")
    parser.add_argument("--scenarios-output", default="simulation_outputs/topology_stress/legacy_topology_scenarios_v1.json")
    parser.add_argument("--checksum-manifest", default="metadata/frozen_source_checksums_v2.json")
    parser.add_argument("--report", default="reports/frozen_legacy_topology_source_v1.json")
    args = parser.parse_args()

    records = [
        row
        for row in read_jsonl(ROOT / args.dataset)
        if (row.get("metadata") or {}).get("augmentation_type") == args.augmentation_tag
    ]
    scenario_ids = sorted({str(row["scenario_id"]) for row in records})
    scenario_map = {str(row["scenario_id"]): row for row in read_json(ROOT / args.scenarios)}
    missing = [scenario_id for scenario_id in scenario_ids if scenario_id not in scenario_map]
    scenarios = [scenario_map[scenario_id] for scenario_id in scenario_ids if scenario_id in scenario_map]
    incomplete = [
        row["scenario_id"]
        for row in scenarios
        if not row.get("query_truth_complete")
        or int(row.get("overloaded_branch_count") or 0) != len(row.get("overloaded_branches") or [])
        or int(row.get("voltage_violation_count") or 0) != len(row.get("voltage_violations") or [])
    ]
    if len(records) != 1136 or len(scenarios) != 71 or missing or incomplete:
        raise ValueError(
            f"legacy source gate failed: records={len(records)}, scenarios={len(scenarios)}, "
            f"missing={len(missing)}, incomplete={len(incomplete)}"
        )

    records_path = ROOT / args.records_output
    scenarios_path = ROOT / args.scenarios_output
    write_jsonl(records_path, records)
    write_json(scenarios_path, scenarios)
    checksums = {
        args.records_output: sha256(records_path),
        args.scenarios_output: sha256(scenarios_path),
    }
    write_json(ROOT / args.checksum_manifest, checksums)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "dataset": args.dataset,
        "augmentation_tag": args.augmentation_tag,
        "records": len(records),
        "unique_scenarios": len(scenarios),
        "missing_scenarios": missing,
        "incomplete_truth_scenarios": incomplete,
        "checksums": checksums,
    }
    write_json(ROOT / args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
