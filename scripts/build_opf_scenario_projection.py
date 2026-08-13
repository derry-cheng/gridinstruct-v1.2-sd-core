#!/usr/bin/env python3
"""Build the immutable IEEE14/IEEE118 scenario projection consumed by OPF."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json


DEFAULT_SYSTEMS = ("ieee14", "ieee118")
DEFAULT_CONTINGENCIES = (
    "base_power_flow",
    "generator_perturbation",
    "n1_branch_outage",
    "n2_branch_outage",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_system(row: dict[str, Any]) -> str:
    return str(row.get("system") or row.get("network_model") or "").lower().replace(
        " ", ""
    )


def build_projection(
    rows: list[dict[str, Any]],
    systems: set[str],
    contingencies: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [
        row
        for row in rows
        if normalize_system(row) in systems
        and str(row.get("contingency_type") or "") in contingencies
        and row.get("solver_status") == "converged"
    ]
    selected.sort(key=lambda row: (normalize_system(row), str(row["scenario_id"])))
    scenario_ids = [str(row.get("scenario_id") or "") for row in selected]
    hard_gates = {
        "projection_nonempty": bool(selected),
        "scenario_ids_complete_and_unique": all(scenario_ids)
        and len(scenario_ids) == len(set(scenario_ids)),
        "requested_systems_present": {normalize_system(row) for row in selected}
        == systems,
        "all_rows_converged": all(
            row.get("solver_status") == "converged" for row in selected
        ),
        "all_rows_have_physical_source": all(
            bool(row.get("physical_source")) for row in selected
        ),
        "all_rows_have_case_hash": all(
            bool((row.get("physical_source") or {}).get("case_sha256"))
            for row in selected
        ),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "fail",
        "systems": sorted(systems),
        "contingency_types": sorted(contingencies),
        "record_count": len(selected),
        "by_system": dict(Counter(normalize_system(row) for row in selected)),
        "by_contingency_type": dict(
            Counter(str(row.get("contingency_type")) for row in selected)
        ),
        "hard_gates": hard_gates,
    }
    if report["status"] != "pass":
        raise ValueError(f"OPF scenario projection gates failed: {hard_gates}")
    return selected, report


def atomic_write_json(path: Path, value: Any) -> None:
    ensure_dirs(path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    write_json(temporary, value)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="simulation_outputs/contingency/scenarios_converged.json",
    )
    parser.add_argument(
        "--output",
        default=(
            "simulation_outputs/opf_closed_loop/"
            "ieee14_ieee118_source_scenarios_v1.json"
        ),
    )
    parser.add_argument(
        "--report",
        default="reports/opf_scenario_projection_v1.2_sd_core.json",
    )
    parser.add_argument("--systems", nargs="+", default=list(DEFAULT_SYSTEMS))
    parser.add_argument(
        "--contingency-types",
        nargs="+",
        default=list(DEFAULT_CONTINGENCIES),
    )
    args = parser.parse_args()

    input_path = ROOT / args.input
    output_path = ROOT / args.output
    rows = read_json(input_path)
    projection, report = build_projection(
        rows,
        {value.lower().replace(" ", "") for value in args.systems},
        set(args.contingency_types),
    )
    atomic_write_json(output_path, projection)
    report.update(
        {
            "input": args.input,
            "input_sha256": file_sha256(input_path),
            "output": args.output,
            "output_sha256": file_sha256(output_path),
        }
    )
    atomic_write_json(ROOT / args.report, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "record_count": report["record_count"],
                "output_sha256": report["output_sha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
