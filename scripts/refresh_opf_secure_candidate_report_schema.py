#!/usr/bin/env python3
"""Deterministically refresh the secure-candidate report from published rows."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_STATE_TABLES = (
    "bus_results",
    "line_results",
    "transformer_results",
    "ext_grid_results",
    "generator_results",
    "load_results",
    "switch_states",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_counter(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def refresh_report(
    report: dict[str, Any],
    scenarios: list[dict[str, Any]],
    *,
    scenario_sha256: str,
    generator_script_sha256: str,
    append_script_sha256: str | None = None,
    scenario_runner_sha256: str | None = None,
) -> dict[str, Any]:
    if report.get("status") != "pass":
        raise ValueError("candidate report status must be pass")
    target = int(report["target_passed_candidates"])
    if len(scenarios) != target:
        raise ValueError(f"published scenario count {len(scenarios)} != target {target}")

    scenario_ids = [str(row["scenario_id"]) for row in scenarios]
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("published scenario identifiers are not unique")
    if scenario_ids != [str(value) for value in report["output_scenario_ids"]]:
        raise ValueError("published scenario order does not match report output_scenario_ids")

    incomplete: list[str] = []
    for row in scenarios:
        tables_present = all(
            isinstance(row.get(name), list) for name in REQUIRED_STATE_TABLES
        )
        if not (
            tables_present
            and row.get("all_in_service_states_finite") is True
            and row.get("additional_state_tables_complete") is True
        ):
            incomplete.append(str(row["scenario_id"]))
    if incomplete:
        raise ValueError(f"incomplete published scenario state: {incomplete[:5]}")

    report["passed_by_load_level"] = normalized_counter(
        [f"{float(row['load_level']):.2f}" for row in scenarios]
    )
    report["passed_by_generator_factor"] = normalized_counter(
        [f"{float(row['generator_factor']):.2f}" for row in scenarios]
    )
    report["passed_by_line_pair"] = normalized_counter(
        [",".join(str(int(index)) for index in row["line_indices"]) for row in scenarios]
    )
    report["published_output_completeness"] = {
        "additional_state_tables_complete_count": sum(
            row.get("additional_state_tables_complete") is True for row in scenarios
        ),
        "all_in_service_states_finite_count": sum(
            row.get("all_in_service_states_finite") is True for row in scenarios
        ),
        "complete_required_state_table_count": sum(
            all(isinstance(row.get(name), list) for name in REQUIRED_STATE_TABLES)
            for row in scenarios
        ),
        "published_scenario_count": len(scenarios),
        "required_state_tables": list(REQUIRED_STATE_TABLES),
        "status": "pass",
    }
    report["report_schema_refresh"] = {
        "contract": "ieee14-opf-secure-candidate-report-schema-refresh-v1",
        "current_generator_script_sha256": generator_script_sha256,
        "input_output_scenarios_sha256": scenario_sha256,
        "method": "deterministic_counts_and_completeness_from_published_scenarios",
        "published_scenario_count": len(scenarios),
        "status": "pass",
    }
    report["output_scenarios"] = {
        "path": "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
        "sha256": scenario_sha256,
        "record_count": len(scenarios),
    }
    implementation = [
        {
            "path": "scripts/generate_opf_secure_candidate_augmentation.py",
            "sha256": generator_script_sha256,
        }
    ]
    if append_script_sha256 is not None:
        implementation.append(
            {
                "path": "scripts/append_opf_closed_loop_auxiliary_records.py",
                "sha256": append_script_sha256,
            }
        )
    if scenario_runner_sha256 is not None:
        implementation.append(
            {
                "path": "scripts/generate_grid_scenarios.py",
                "sha256": scenario_runner_sha256,
            }
        )
    report["implementation"] = implementation
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        default="reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--scenarios",
        default=(
            "simulation_outputs/opf_closed_loop/"
            "ieee14_secure_candidate_scenarios_v1.json"
        ),
    )
    parser.add_argument(
        "--generator-script",
        default="scripts/generate_opf_secure_candidate_augmentation.py",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_path = ROOT / args.report
    scenario_path = ROOT / args.scenarios
    generator_path = ROOT / args.generator_script
    append_path = ROOT / "scripts/append_opf_closed_loop_auxiliary_records.py"
    scenario_runner_path = ROOT / "scripts/generate_grid_scenarios.py"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    scenarios = json.loads(scenario_path.read_text(encoding="utf-8"))
    refreshed = refresh_report(
        report,
        scenarios,
        scenario_sha256=sha256_file(scenario_path),
        generator_script_sha256=sha256_file(generator_path),
        append_script_sha256=sha256_file(append_path),
        scenario_runner_sha256=sha256_file(scenario_runner_path),
    )
    report_path.write_text(
        json.dumps(refreshed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "report_sha256": sha256_file(report_path),
                "status": "pass",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
