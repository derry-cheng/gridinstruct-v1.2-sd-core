#!/usr/bin/env python3
"""Recompute complete AC power-flow truth for every released scenario.

This migration is intentionally independent of instruction generation.  It
reads the frozen scenario manifest, reconstructs each network/contingency, and
replaces every query-facing result with a complete list.  Counts and lists are
checked before the output is accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandapower as pp

from generate_extended_topology_stress import SYSTEM_LOADERS as EXTERNAL_LOADERS
from generate_grid_scenarios import SYSTEM_LOADERS as CORE_LOADERS
from generate_grid_scenarios import scale_generation, scale_loads, summarize_results
from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json
from pglib_network_loader import (
    FORMAL_SYSTEMS,
    load_pglib_network,
    network_provenance,
)


SUMMARY_KEYS = {
    "max_branch_loading_percent",
    "max_line_loading_percent",
    "max_transformer_loading_percent",
    "min_bus_voltage_pu",
    "max_bus_voltage_pu",
    "overloaded_branch_count",
    "overloaded_transformer_count",
    "voltage_violation_count",
    "overloaded_branches",
    "overloaded_transformers",
    "voltage_violations",
    "violation_type",
    "severity_level",
    "threshold_policy",
    "bus_results",
    "line_results",
    "transformer_results",
    "generator_results",
    "ext_grid_results",
    "load_results",
    "additional_element_results",
    "switch_states",
    "state_counts",
    "additional_state_tables_complete",
    "all_in_service_states_finite",
    "generation_mw",
    "demand_mw",
    "network_losses_mw",
    "power_balance_residual_mw",
}
ALLOWED_CONTINGENCIES = {
    "base_power_flow",
    "generator_perturbation",
    "n1_branch_outage",
    "n2_branch_outage",
    "n1_transformer_outage",
    "n1_generator_outage",
    "n1_bus_outage",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_system(row: dict[str, Any]) -> str:
    physical_source = row.get("physical_source")
    source_system = (
        physical_source.get("system")
        if isinstance(physical_source, dict)
        else None
    )
    raw = str(row.get("system") or source_system or row.get("network_model") or "")
    return raw.lower().replace(" ", "")


def line_indices(row: dict[str, Any]) -> list[int]:
    if row.get("line_indices"):
        return [int(value) for value in row["line_indices"]]
    if row.get("line_index") is not None:
        return [int(row["line_index"])]
    scenario_id = str(row.get("scenario_id") or "")
    match = re.search(r"branchidx_(\d+)_", scenario_id)
    if match:
        return [int(match.group(1))]
    match = re.search(r"branches_(\d+)_\d+_\d+_(\d+)_\d+_\d+$", scenario_id)
    if match:
        return [int(match.group(1)), int(match.group(2))]
    match = re.search(r"_line(\d+)$", scenario_id)
    return [int(match.group(1))] if match else []


def generator_factor(row: dict[str, Any]) -> float:
    if row.get("generator_factor") is not None:
        return float(row["generator_factor"])
    match = re.search(r"_gen(\d+)", str(row.get("scenario_id") or ""))
    return int(match.group(1)) / 100.0 if match else 1.0


def validate_physical_source(
    row: dict[str, Any],
    actual_source: dict[str, Any],
) -> None:
    declared = row.get("physical_source")
    if not isinstance(declared, dict):
        return
    fields = (
        "contract_version",
        "system",
        "repository",
        "repository_commit",
        "license_sha256",
        "case_file",
        "case_sha256",
    )
    mismatches = {
        field: {
            "declared": declared.get(field),
            "actual": actual_source.get(field),
        }
        for field in fields
        if declared.get(field) != actual_source.get(field)
    }
    if mismatches:
        raise ValueError(
            f"{row.get('scenario_id')} physical_source mismatch: {mismatches}"
        )


def build_net(row: dict[str, Any], *, pglib_root: Path | None = None):
    system = normalize_system(row)
    loaders = {**CORE_LOADERS, **EXTERNAL_LOADERS}
    if system not in loaders:
        raise ValueError(f"unsupported network model: {system}")
    physical_source = row.get("physical_source")
    if isinstance(physical_source, dict):
        if system not in FORMAL_SYSTEMS:
            raise ValueError(
                f"{row.get('scenario_id')} declares an unsupported PGLib system: {system}"
            )
        if pglib_root is None:
            raise ValueError(
                f"{row.get('scenario_id')} has hash-bound physical_source but "
                "--pglib-root was not provided"
            )
        net = load_pglib_network(system, pglib_root)
        validate_physical_source(row, network_provenance(net))
    else:
        net = loaders[system]()
    scale_loads(net, float(row.get("load_level") or 1.0))
    generation_scaling_factor = float(
        row.get("base_generation_scaling_factor") or 1.0
    )
    if generation_scaling_factor != 1.0:
        scale_generation(net, generation_scaling_factor)
    perturbation_factor = generator_factor(row)
    if len(net.gen) and (
        str(row.get("contingency_type") or "") == "generator_perturbation"
        or not math.isclose(
            perturbation_factor, 1.0, rel_tol=0.0, abs_tol=1e-12
        )
    ):
        net.gen["p_mw"] *= perturbation_factor
    contingency = str(row.get("contingency_type") or "")
    if contingency not in ALLOWED_CONTINGENCIES:
        raise ValueError(f"unsupported contingency_type: {contingency!r}")
    if contingency in {"n1_branch_outage", "n2_branch_outage"}:
        indices = line_indices(row)
        required = 1 if contingency == "n1_branch_outage" else 2
        if len(indices) != required or len(set(indices)) != required:
            raise ValueError(f"{contingency} requires {required} unique line indices; got {indices}")
        affected = ((row.get("affected_components") or {}).get("branches") or [])
        if len(affected) != required:
            raise ValueError(f"{contingency} affected_components.branches must contain {required} entries")
        for idx in indices:
            if idx < 0 or idx >= len(net.line):
                raise IndexError(f"line index {idx} outside {system} line table")
            net.line.at[idx, "in_service"] = False
    elif contingency == "generator_perturbation":
        pass
    elif contingency == "n1_transformer_outage":
        idx = row.get("transformer_index")
        if idx is None:
            match = re.search(r"_trafoidx_(\d+)$", str(row.get("scenario_id") or ""))
            idx = int(match.group(1)) if match else None
        if idx is None or int(idx) not in net.trafo.index:
            raise IndexError(f"transformer index {idx} outside {system} transformer table")
        if len(((row.get("affected_components") or {}).get("transformers") or [])) != 1:
            raise ValueError("n1_transformer_outage requires one affected transformer")
        net.trafo.at[int(idx), "in_service"] = False
    elif contingency == "n1_generator_outage":
        idx = row.get("generator_index")
        if idx is None:
            match = re.search(r"_genidx_(\d+)$", str(row.get("scenario_id") or ""))
            idx = int(match.group(1)) if match else None
        if idx is None or int(idx) not in net.gen.index:
            raise IndexError(f"generator index {idx} outside {system} generator table")
        if len(((row.get("affected_components") or {}).get("generators") or [])) != 1:
            raise ValueError("n1_generator_outage requires one affected generator")
        net.gen.at[int(idx), "in_service"] = False
    elif contingency == "n1_bus_outage":
        idx = row.get("bus_index")
        if idx is None:
            match = re.search(r"_busidx_(\d+)$", str(row.get("scenario_id") or ""))
            idx = int(match.group(1)) if match else None
        if idx is None or int(idx) not in net.bus.index:
            raise IndexError(f"bus index {idx} outside {system} bus table")
        if ((row.get("affected_components") or {}).get("buses") or []) != [int(idx)]:
            raise ValueError("n1_bus_outage requires one matching affected bus")
        net.bus.at[int(idx), "in_service"] = False
    return net


def validate_complete(row: dict[str, Any]) -> None:
    checks = (
        ("overloaded_branch_count", "overloaded_branches"),
        ("overloaded_transformer_count", "overloaded_transformers"),
        ("voltage_violation_count", "voltage_violations"),
    )
    for count_key, list_key in checks:
        if int(row.get(count_key) or 0) != len(row.get(list_key) or []):
            raise ValueError(
                f"{row.get('scenario_id')}: {count_key}={row.get(count_key)} "
                f"but len({list_key})={len(row.get(list_key) or [])}"
            )
    state_lists = {
        "buses": "bus_results",
        "lines": "line_results",
        "transformers": "transformer_results",
        "generators": "generator_results",
        "ext_grids": "ext_grid_results",
        "loads": "load_results",
    }
    counts = row.get("state_counts") or {}
    for count_key, list_key in state_lists.items():
        values = row.get(list_key)
        if not isinstance(values, list) or int(counts.get(count_key, -1)) != len(values):
            raise ValueError(f"{row.get('scenario_id')}: incomplete {list_key} state table")
    additional = row.get("additional_element_results")
    if not isinstance(additional, dict):
        raise ValueError(f"{row.get('scenario_id')}: missing additional element state tables")
    for element, values in additional.items():
        if not isinstance(values, list) or int(counts.get(element, -1)) != len(values):
            raise ValueError(f"{row.get('scenario_id')}: incomplete additional {element} state table")
    switches = row.get("switch_states")
    if not isinstance(switches, list) or int(counts.get("switches", -1)) != len(switches):
        raise ValueError(f"{row.get('scenario_id')}: incomplete switch state table")
    if row.get("additional_state_tables_complete") is not True:
        raise ValueError(f"{row.get('scenario_id')}: additional element state count mismatch")
    if row.get("all_in_service_states_finite") is not True:
        raise ValueError(f"{row.get('scenario_id')}: non-finite in-service electrical state")
    if float(row.get("power_balance_residual_mw", float("inf"))) > 1e-3:
        raise ValueError(f"{row.get('scenario_id')}: power-balance residual exceeds 1e-3 MW")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Complete Scenario Truth Rebuild",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        f"- Input scenarios: {report['input_scenarios']}",
        f"- Recomputed scenarios: {report['recomputed_scenarios']}",
        f"- Errors: {report['error_count']}",
        f"- Previously truncated scenarios: {report['previously_truncated_scenarios']}",
        f"- Complete output scenarios: {report['complete_output_scenarios']}",
        f"- Input SHA-256: `{report['input_sha256']}`",
        f"- Output SHA-256: `{report['output_sha256']}`",
        "",
        "## Network coverage",
        "",
    ]
    for system, count in sorted(report["by_system"].items()):
        lines.append(f"- {system}: {count}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--output", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--report-json", default="reports/scenario_truth_rebuild_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/scenario_truth_rebuild_v1.2_sd_core.md")
    parser.add_argument(
        "--attempts-json",
        default="reports/scenario_truth_rebuild_attempts_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--pglib-root",
        type=Path,
        default=None,
        help="Pinned PGLib checkout required by hash-bound physical_source rows.",
    )
    args = parser.parse_args()

    input_path = ROOT / args.input
    output_path = ROOT / args.output
    rows = read_json(input_path)
    if not isinstance(rows, list) or not rows:
        raise ValueError("scenario input must be a non-empty list")
    scenario_ids = [str(row.get("scenario_id") or "") for row in rows]
    if any(not value for value in scenario_ids):
        raise ValueError("scenario input contains a missing scenario_id")
    if len(scenario_ids) != len(set(scenario_ids)):
        duplicates = sorted({value for value in scenario_ids if scenario_ids.count(value) > 1})
        raise ValueError(f"scenario input contains duplicate IDs: {duplicates[:20]}")
    input_hash = sha256(input_path)
    rebuilt: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    attempts: list[dict[str, Any]] = []
    previously_truncated = 0

    for row in rows:
        if any(
            int(row.get(count_key) or 0) != len(row.get(list_key) or [])
            for count_key, list_key in (
                ("overloaded_branch_count", "overloaded_branches"),
                ("voltage_violation_count", "voltage_violations"),
            )
        ):
            previously_truncated += 1
        try:
            net = build_net(row, pglib_root=args.pglib_root)
            pp.runpp(
                net,
                algorithm="nr",
                init="auto",
                tolerance_mva=1e-6,
                max_iteration=50,
                enforce_q_lims=True,
                numba=False,
            )
            summary = summarize_results(net)
            updated = {key: value for key, value in row.items() if key not in SUMMARY_KEYS}
            updated.update(summary)
            updated.update(
                {
                    "solver": "pandapower.runpp",
                    "solver_status": "converged",
                    "error": None,
                    "scenario_truth_schema_version": "2.0",
                    "query_truth_complete": True,
                }
            )
            validate_complete(updated)
            rebuilt.append(updated)
            attempts.append(
                {
                    "scenario_id": str(row.get("scenario_id")),
                    "input_row_sha256": hashlib.sha256(
                        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    "status": "pass",
                    "error_type": None,
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            error = {
                "scenario_id": str(row.get("scenario_id")),
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
            errors.append(error)
            attempts.append(
                {
                    **error,
                    "input_row_sha256": hashlib.sha256(
                        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    "status": "fail",
                }
            )

    status = "pass" if len(rebuilt) == len(rows) and not errors else "fail"
    if status == "pass":
        ensure_dirs(output_path.parent, (ROOT / args.report_json).parent)
        write_json(output_path, rebuilt)
        output_hash = sha256(output_path)
    else:
        output_hash = "not_written"

    invariants = {
        "overloaded_branch_count_equals_list_length": all(
            int(row.get("overloaded_branch_count") or 0) == len(row.get("overloaded_branches") or []) for row in rebuilt
        ),
        "overloaded_transformer_count_equals_list_length": all(
            int(row.get("overloaded_transformer_count") or 0) == len(row.get("overloaded_transformers") or []) for row in rebuilt
        ),
        "voltage_violation_count_equals_list_length": all(
            int(row.get("voltage_violation_count") or 0) == len(row.get("voltage_violations") or []) for row in rebuilt
        ),
        "complete_state_tables_present": all(
            all(
                isinstance(row.get(key), list)
                for key in (
                    "bus_results",
                    "line_results",
                    "transformer_results",
                    "generator_results",
                    "ext_grid_results",
                    "load_results",
                    "switch_states",
                )
            )
            and isinstance(row.get("additional_element_results"), dict)
            and row.get("additional_state_tables_complete") is True
            for row in rebuilt
        ),
        "all_in_service_states_finite": all(
            row.get("all_in_service_states_finite") is True for row in rebuilt
        ),
        "all_power_balance_residuals_within_1e_minus_3_mw": all(
            float(row.get("power_balance_residual_mw", float("inf"))) <= 1e-3 for row in rebuilt
        ),
    }
    if not all(invariants.values()):
        status = "fail"
    write_json(ROOT / args.attempts_json, attempts)
    attempts_hash = sha256(ROOT / args.attempts_json)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": args.input,
        "output": args.output,
        "input_sha256": input_hash,
        "output_sha256": output_hash,
        "attempts": args.attempts_json,
        "attempts_sha256": attempts_hash,
        "input_scenarios": len(rows),
        "recomputed_scenarios": len(rebuilt),
        "error_count": len(errors),
        "errors": errors,
        "previously_truncated_scenarios": previously_truncated,
        "complete_output_scenarios": sum(1 for row in rebuilt if row.get("query_truth_complete")),
        "by_system": dict(Counter(normalize_system(row) for row in rebuilt)),
        "truth_invariants": invariants,
    }
    write_json(ROOT / args.report_json, report)
    write_markdown(ROOT / args.report_md, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
