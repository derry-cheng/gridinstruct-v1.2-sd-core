#!/usr/bin/env python3
"""Reconcile screened IEEE14 candidates with released OPF scenarios."""

from __future__ import annotations

import argparse
import io
import json
import tarfile
from collections import Counter
from contextlib import ExitStack
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative: str, archive: tarfile.TarFile | None = None):
    if archive is not None:
        with archive.extractfile(relative) as handle:
            return json.load(handle)
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def build_report(archive_path: Path | None = None) -> dict:
    with ExitStack() as stack:
        archive = stack.enter_context(tarfile.open(archive_path, "r:gz")) if archive_path else None
        return _build_report(archive)


def _build_report(archive: tarfile.TarFile | None) -> dict:
    register = load_json("reports/opf_candidate_register_v1.2_sd_core.json", archive)
    released = load_json("simulation_outputs/opf_closed_loop/auxiliary_opf_results.json", archive)["results"]
    selected = load_json("simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json", archive)
    source = load_json("simulation_outputs/opf_closed_loop/ieee14_ieee118_source_scenarios_v1.json", archive)

    released_ids = [row["scenario_id"] for row in released]
    selected_ids = {row["scenario_id"] for row in selected}
    source_ids = {row["scenario_id"] for row in source}
    released_set = set(released_ids)
    by_system = dict(sorted(Counter(row["system"] for row in released).items()))
    denominator = register["denominator"]
    opf_rows_by_scenario: Counter[str] = Counter()
    relative = "data/gridinstruct_v1.2_sd_core_en.jsonl"
    handle = (
        io.TextIOWrapper(archive.extractfile(relative), encoding="utf-8")
        if archive is not None else (ROOT / relative).open(encoding="utf-8")
    )
    with handle:
        for line in handle:
            row = json.loads(line)
            if row.get("task_type") == "auxiliary_decision" and isinstance(
                row.get("executable_control_target"), dict
            ):
                opf_rows_by_scenario[row["scenario_id"]] += 1
    checks = {
        "released_scenarios_unique": len(released_ids) == len(released_set) == 160,
        "released_sources_complete": released_set <= selected_ids | source_ids,
        "source_groups_disjoint": not selected_ids & source_ids,
        "released_system_balance": by_system == {"ieee118": 80, "ieee14": 80},
        "screening_denominator_consistent": (
            denominator["screened_candidates"]
            == denominator["screened_passes"] + denominator["screened_rejections"]
        ),
        "selected_pool_consistent": len(selected_ids) == denominator["selected_target"] == 120,
        "released_ieee14_drawn_from_selected_pool": len(released_set & selected_ids) == 80,
        "released_ieee118_drawn_from_source_pool": len(released_set & source_ids) == 80,
        "opf_record_scenarios_match_released_scenarios": set(opf_rows_by_scenario) == released_set,
        "two_opf_records_per_released_scenario": all(
            opf_rows_by_scenario[scenario_id] == 2 for scenario_id in released_set
        ),
    }
    report = {
        "status": "pass" if all(checks.values()) else "fail",
        "registered_candidates": denominator["registered_candidates"],
        "power_flow_converged": denominator["power_flow_converged"],
        "eligible_violation_candidates": denominator["eligible_violation_candidates"],
        "robust_screened_candidates": denominator["screened_candidates"],
        "robust_passed_candidates": denominator["screened_passes"],
        "eligible_not_screened_after_target": denominator["eligible_not_screened_after_target"],
        "selected_ieee14_pool": len(selected_ids),
        "released_scenarios": len(released_set),
        "released_scenarios_by_system": by_system,
        "released_from_selected_ieee14_pool": len(released_set & selected_ids),
        "selected_ieee14_not_released": len(selected_ids - released_set),
        "released_from_ieee118_source_pool": len(released_set & source_ids),
        "released_instruction_variants": sum(opf_rows_by_scenario.values()),
        "checks": checks,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, help="Read inputs directly from the compact archive")
    parser.add_argument(
        "--output", default="reports/opf_selection_mapping_v1.2_sd_core.json"
    )
    args = parser.parse_args()
    report = build_report(args.archive)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
