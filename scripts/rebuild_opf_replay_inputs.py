#!/usr/bin/env python3
"""Reconstruct the sealed OPF replay inputs from bound dataset records.

The published OPF action payload is stored inside each of the two auxiliary
records generated for a scenario.  This utility takes one byte-identical
closed-loop payload per scenario, verifies the two variants agree, derives the
scenario projection from the stable scenario identifier, and writes the
immutable inputs consumed by both 7,680-case stress validators.

It deliberately does not regenerate the 35,200-candidate screening ledger or
claim an independent OPF solve.  It only materializes the already published
closed-loop actions and their hash-bound scenario descriptions so that the
registered replay kernels can execute their declared AC checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


SCENARIO_PATTERNS = (
    re.compile(
        r"^(?P<system>ieee\d+)_base_power_flow_load(?P<load>\d+)$"
    ),
    re.compile(
        r"^(?P<system>ieee\d+)_generator_perturbation_load(?P<load>\d+)_gen(?P<gen>\d+)$"
    ),
    re.compile(
        r"^(?P<system>ieee\d+)_n1_branch_outage_load(?P<load>\d+)_"
        r"branchidx_(?P<line>\d+)(?:_(?P<from>\d+)_(?P<to>\d+))?$"
    ),
    re.compile(
        r"^(?P<system>ieee\d+)_secure_candidate_n2_load_milli(?P<load>\d+)"
        r"_gen_centi(?P<gen>\d+)_lines_(?P<line1>\d+)_(?P<line2>\d+)$"
    ),
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_scenario(scenario_id: str, closed: dict[str, Any]) -> dict[str, Any]:
    match = next(
        (
            candidate
            for pattern in SCENARIO_PATTERNS
            if (candidate := pattern.match(scenario_id)) is not None
        ),
        None,
    )
    if match is None:
        raise ValueError(f"unsupported published OPF scenario identifier: {scenario_id}")
    groups = match.groupdict()
    system = str(closed.get("system") or groups["system"])
    load_text = groups["load"]
    if "milli" in scenario_id:
        load_level = int(load_text) / 1000.0
    else:
        load_level = int(load_text) / 100.0
    contingency_type = str(closed.get("contingency_type") or "")
    if not contingency_type:
        raise ValueError(f"{scenario_id} has no contingency_type")
    if "line1" in groups and groups.get("line1") is not None:
        line_indices = [int(groups["line1"]), int(groups["line2"])]
    elif groups.get("line") is not None:
        line_indices = [int(groups["line"])]
    else:
        line_indices = []
    generator_factor = 1.0
    if groups.get("gen") is not None:
        generator_factor = int(groups["gen"]) / 100.0
    physical_source = closed.get("physical_source")
    if not isinstance(physical_source, dict):
        raise ValueError(f"{scenario_id} has no physical_source binding")
    return {
        "scenario_id": scenario_id,
        "network_model": closed.get("network_model"),
        "system": system,
        "contingency_type": contingency_type,
        "base_case_file": physical_source.get("case_file"),
        "load_level": load_level,
        "base_generation_scaling_factor": 1.0,
        "generator_factor": generator_factor,
        "line_indices": line_indices,
        "transformer_index": None,
        "generator_index": None,
        "bus_index": None,
        "physical_source": physical_source,
        "solver": closed.get("opf_solver"),
        "solver_status": closed.get("opf_status"),
    }


def collect_bound_rows(dataset_path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(dataset_path):
        if (row.get("metadata") or {}).get("opf_closed_loop") is True:
            grouped[str(row.get("scenario_id") or "")].append(row)
    return grouped


def rebuild(dataset_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    grouped = collect_bound_rows(dataset_path)
    if len(grouped) != 160 or any(len(rows) != 2 for rows in grouped.values()):
        raise ValueError(
            f"expected 160 scenarios with two bound variants; got "
            f"{len(grouped)} scenarios and counts={sorted({len(v) for v in grouped.values()})}"
        )
    results: list[dict[str, Any]] = []
    scenarios: list[dict[str, Any]] = []
    for scenario_id in sorted(grouped, key=lambda value: (value.split("_")[0], value)):
        variants = sorted(grouped[scenario_id], key=lambda row: int((row.get("metadata") or {}).get("variant_index", 0)))
        closed_payloads = [row.get("closed_loop_validation") for row in variants]
        if any(not isinstance(payload, dict) for payload in closed_payloads):
            raise ValueError(f"{scenario_id} has an incomplete closed-loop payload")
        if len({canonical_hash(payload) for payload in closed_payloads}) != 1:
            raise ValueError(f"the two published variants disagree for {scenario_id}")
        result = closed_payloads[0]
        if str(result.get("scenario_id") or "") != scenario_id:
            raise ValueError(f"closed-loop scenario identity mismatch: {scenario_id}")
        scenario = parse_scenario(scenario_id, result)
        if scenario["physical_source"] != result.get("physical_source"):
            raise ValueError(f"scenario/source binding mismatch: {scenario_id}")
        results.append(result)
        scenarios.append(scenario)
    systems = {str(row.get("system") or "") for row in results}
    counts = {system: sum(row.get("system") == system for row in results) for system in sorted(systems)}
    report = {
        "status": "pass",
        "contract_version": "opf-replay-input-reconstruction-v1",
        "source_dataset": str(dataset_path.relative_to(ROOT)),
        "source_dataset_sha256": file_sha256(dataset_path),
        "published_action_count": len(results),
        "published_variant_count": sum(len(rows) for rows in grouped.values()),
        "variant_agreement": True,
        "by_system": counts,
        "scope_note": (
            "Materializes hash-bound published closed-loop actions and scenario "
            "projections; it does not rerun the OPF optimizer or reconstruct the "
            "35,200-candidate screening ledger."
        ),
    }
    return results, scenarios, report


def source_manifest_with_candidate_overlay(
    scenarios: list[dict[str, Any]],
    filler_path: Path,
    candidate_path: Path,
) -> list[dict[str, Any]]:
    """Keep source and selected-candidate manifests disjoint for the replay contract.

    The 160 published actions contain 80 IEEE118 source actions and 80 selected
    IEEE14 candidates.  The validator accepts an additional candidate manifest,
    so the source projection carries the 80 IEEE118 rows plus 40 independent
    IEEE14 physical rows as registry context; the 120 selected candidates are
    supplied through the additional manifest and are not duplicated here.
    """
    candidate_rows = json.loads(candidate_path.read_text(encoding="utf-8"))
    if not isinstance(candidate_rows, list):
        raise ValueError("candidate manifest must be a JSON list")
    candidate_ids = {str(row.get("scenario_id") or "") for row in candidate_rows}
    filler_rows = json.loads(filler_path.read_text(encoding="utf-8"))
    if not isinstance(filler_rows, list):
        raise ValueError("source filler must be a JSON list")
    ieee118 = [row for row in scenarios if str(row.get("system")) == "ieee118"]
    if len(ieee118) != 80:
        raise ValueError(f"expected 80 IEEE118 source actions, got {len(ieee118)}")
    filler = [
        row
        for row in filler_rows
        if str(row.get("system") or row.get("network_model") or "").lower().replace(" ", "")
        == "ieee14"
        and str(row.get("scenario_id") or "") not in candidate_ids
        and row.get("solver_status") == "converged"
        and row.get("contingency_type") in {"n1_branch_outage", "n2_branch_outage"}
    ]
    filler.sort(key=lambda row: str(row.get("scenario_id") or ""))
    if len(filler) < 40:
        raise ValueError(f"source filler has only {len(filler)} eligible IEEE14 rows")
    return ieee118 + filler[:40]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument(
        "--results",
        default="simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
    )
    parser.add_argument(
        "--scenarios",
        default="simulation_outputs/opf_closed_loop/ieee14_ieee118_source_scenarios_v1.json",
    )
    parser.add_argument(
        "--commit",
        default="simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--source-filler",
        default="simulation_outputs/contingency/scenarios_converged.json",
        help="Physical source registry rows kept outside the selected-candidate overlay.",
    )
    parser.add_argument(
        "--candidate-manifest",
        default="simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
    )
    args = parser.parse_args()
    dataset_path = (ROOT / args.dataset).resolve()
    results_path = (ROOT / args.results).resolve()
    scenarios_path = (ROOT / args.scenarios).resolve()
    commit_path = (ROOT / args.commit).resolve()
    results, scenarios, report = rebuild(dataset_path)
    candidate_path = (ROOT / args.candidate_manifest).resolve()
    filler_path = (ROOT / args.source_filler).resolve()
    scenarios = source_manifest_with_candidate_overlay(scenarios, filler_path, candidate_path)
    report["source_manifest_row_count"] = len(scenarios)
    report["source_manifest_policy"] = "80 IEEE118 source rows plus 40 disjoint IEEE14 registry rows; selected IEEE14 actions are supplied by the additional candidate manifest"
    ensure_dirs(results_path.parent)
    write_json(results_path, {
        "schema_version": "opf-closed-loop-results-v1",
        "status": "pass",
        "results": results,
        "provenance": report,
    })
    write_json(scenarios_path, scenarios)
    report.update({
        "results_path": str(results_path.relative_to(ROOT)),
        "results_sha256": file_sha256(results_path),
        "scenario_manifest_path": str(scenarios_path.relative_to(ROOT)),
        "scenario_manifest_sha256": file_sha256(scenarios_path),
    })
    write_json(commit_path, {
        "schema_version": "1.0",
        "status": "pass",
        "robustness_status": "published_actions_materialized",
        "commit_policy": "written after atomically replacing and hashing the published action and scenario artifacts",
        "provenance": report,
        "artifacts": {
            "results": {
                "path": str(results_path.relative_to(ROOT)),
                "sha256": file_sha256(results_path),
            },
            "scenarios": {
                "path": str(scenarios_path.relative_to(ROOT)),
                "sha256": file_sha256(scenarios_path),
            },
        },
    })
    print(json.dumps({
        "status": "pass",
        "published_action_count": len(results),
        "systems": report["by_system"],
        "results": str(results_path),
        "scenarios": str(scenarios_path),
        "commit": str(commit_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
