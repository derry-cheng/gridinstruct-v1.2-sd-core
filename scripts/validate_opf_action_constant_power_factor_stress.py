#!/usr/bin/env python3
"""Replay every published OPF action under constant-power-factor load stress."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any

from pandapower.topology import unsupplied_buses

import validate_opf_action_uncertainty_stress as active_stress
from append_opf_closed_loop_auxiliary_records import (
    ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU,
    THERMAL_BOUNDARY_EPSILON_PERCENT,
)


CONTRACT_VERSION = "opf-action-constant-power-factor-stress-v1"


def preregistered_case_specs(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reuse the sealed physical grid while assigning model-specific identities."""
    return [
        {
            **spec,
            "case_id": f"constant_power_factor::{spec['case_id']}",
        }
        for spec in active_stress.preregistered_case_specs(results)
    ]


def apply_constant_power_factor_stress(
    net: Any,
    stress_fraction: float,
) -> dict[str, float]:
    """Scale P and Q by one factor for every in-service load."""
    active_indices = [
        index
        for index in net.load.index
        if "in_service" not in net.load.columns
        or bool(net.load.at[index, "in_service"])
    ]
    before_p_mw = (
        float(net.load.loc[active_indices, "p_mw"].sum()) if active_indices else 0.0
    )
    before_q_mvar = (
        float(net.load.loc[active_indices, "q_mvar"].sum()) if active_indices else 0.0
    )
    if active_indices:
        multiplier = 1.0 + stress_fraction
        net.load.loc[active_indices, "p_mw"] *= multiplier
        net.load.loc[active_indices, "q_mvar"] *= multiplier
    after_p_mw = (
        float(net.load.loc[active_indices, "p_mw"].sum()) if active_indices else 0.0
    )
    after_q_mvar = (
        float(net.load.loc[active_indices, "q_mvar"].sum()) if active_indices else 0.0
    )
    expected_q = before_q_mvar * (1.0 + stress_fraction)
    if not math.isclose(after_q_mvar, expected_q, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError("constant-power-factor reactive-load scaling drifted")
    return {
        "active_load_before_mw": before_p_mw,
        "active_load_after_mw": after_p_mw,
        "active_load_perturbation_mw": after_p_mw - before_p_mw,
        "reactive_load_before_mvar": before_q_mvar,
        "reactive_load_after_mvar": after_q_mvar,
        "reactive_load_perturbation_mvar": after_q_mvar - before_q_mvar,
    }


def execute_case(
    action_net: Any,
    result: dict[str, Any],
    spec: dict[str, Any],
    *,
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    net = copy.deepcopy(action_net)
    contingency = spec["contingency"]
    outaged_generator = None
    reference_controls = result["post_action_controls"]
    control_replay_policy = "published_base_post_action_controls"
    if contingency is not None:
        element = str(contingency["element"])
        index = int(contingency["index"])
        if index not in getattr(net, element).index:
            raise ValueError(
                f"registered contingency is unavailable: {element}:{index}"
            )
        getattr(net, element).at[index, "in_service"] = False
        if element == "gen":
            outaged_generator = index
        check = active_stress.registered_n1_check_lookup(result)[(element, index)]
        reference_controls = check["control_feasibility"][
            "post_contingency_controls"
        ]
        reference_metrics = check.get("metrics") or {}
        active_stress.apply_control_vector(net, reference_controls)
        control_replay_policy = (
            "stored_bounded_corrective_post_contingency_controls"
        )
    else:
        reference_metrics = result.get("post_action") or {}
    disconnected = sorted(int(value) for value in unsupplied_buses(net))
    load_stress = apply_constant_power_factor_stress(
        net,
        float(spec["stress_fraction"]),
    )
    common = {
        **spec,
        "stress_percent": 100.0 * float(spec["stress_fraction"]),
        "load_model": "uniform_constant_power_factor",
        "load_model_description": (
            "uniform multiplicative perturbation of every in-service load.p_mw "
            "and load.q_mvar by the same factor"
        ),
        "control_replay_policy": control_replay_policy,
        "load_stress": load_stress,
        "unsupplied_buses": disconnected,
    }
    if disconnected:
        return {
            **common,
            "solver_status": "not_run_unsupplied_island",
            "safe": False,
            "error_type": "UnsuppliedIsland",
            "error": f"unsupplied buses: {disconnected}",
        }
    try:
        active_stress.run_power_flow(net)
        measured = active_stress.case_metrics(
            net,
            result,
            balance_tolerance_mw=balance_tolerance_mw,
            outaged_generator=outaged_generator,
        )
        balancing = active_stress.external_balance(
            net,
            reference_controls,
            load_stress,
            reference_losses_mw=float(
                reference_metrics.get("network_losses_mw") or 0.0
            ),
            realized_losses_mw=float(measured["network_losses_mw"]),
        )
        if (
            float(balancing["incremental_active_balance_residual_mw"])
            > balance_tolerance_mw
        ):
            measured["safe"] = False
            measured["incremental_active_balance_residual_mw"] = balancing[
                "incremental_active_balance_residual_mw"
            ]
        selected_margin = float(
            result.get("selected_loading_margin_percent") or 100.0
        )
        return {
            **common,
            "solver_status": "converged",
            "metrics": measured,
            "external_balance": balancing,
            "selected_precontingency_loading_margin_percent": selected_margin,
            "precontingency_margin_preserved": bool(
                float(measured["max_branch_loading_percent"])
                <= selected_margin + THERMAL_BOUNDARY_EPSILON_PERCENT
            )
            if spec["case_type"] == "base_stress"
            else None,
            "safe": measured["safe"],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            **common,
            "solver_status": "failed",
            "safe": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


def build_report(
    results: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    validation: list[dict[str, Any]],
    input_artifacts: dict[str, Any],
    *,
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    stress_points = active_stress.PREREGISTERED_ACTIVE_LOAD_STRESS
    expected_base = len(results) * len(stress_points)
    expected_n1 = sum(
        len(active_stress.registered_n1_identities(result)) for result in results
    ) * len(stress_points)
    base_rows = [row for row in cases if row["case_type"] == "base_stress"]
    n1_rows = [
        row for row in cases if row["case_type"] == "registered_n1_stress"
    ]
    denominator_complete = (
        len(cases) == expected_base + expected_n1
        and len(base_rows) == expected_base
        and len(n1_rows) == expected_n1
        and len({row["case_id"] for row in cases}) == len(cases)
    )
    if not denominator_complete:
        raise RuntimeError("registered constant-power-factor denominator is incomplete")

    base_summary = active_stress.summarize_rows(base_rows)
    n1_summary = active_stress.summarize_rows(n1_rows)
    gates = {
        "input_contract_complete": len(validation) == len(results),
        "registered_denominator_complete": denominator_complete,
        "base_stress_convergence_rate_is_one": (
            base_summary["convergence_rate"] == 1.0
        ),
        "base_stress_safety_rate_is_one": (
            base_summary["safety_rate_over_registered_denominator"] == 1.0
        ),
        "registered_n1_convergence_rate_is_one": (
            n1_summary["convergence_rate"] == 1.0
        ),
        "registered_n1_safety_rate_is_one": (
            n1_summary["safety_rate_over_registered_denominator"] == 1.0
        ),
        "worst_voltage_margin_meets_published_minimum": all(
            value is not None
            and float(value) >= ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
            for value in (
                base_summary["minimum_worst_voltage_margin_pu"],
                n1_summary["minimum_worst_voltage_margin_pu"],
            )
        ),
        "worst_thermal_margin_nonnegative": all(
            value is not None
            and float(value) >= -THERMAL_BOUNDARY_EPSILON_PERCENT
            for value in (
                base_summary["minimum_thermal_margin_percent"],
                n1_summary["minimum_thermal_margin_percent"],
            )
        ),
        "all_registered_device_limits_pass": all(
            not (row.get("metrics") or {}).get(
                "registered_network_limit_violations"
            )
            for row in cases
            if row.get("solver_status") == "converged"
        ),
        "all_incremental_active_balances_close": all(
            row.get("solver_status") == "converged"
            and float(
                (row.get("external_balance") or {}).get(
                    "incremental_active_balance_residual_mw",
                    float("inf"),
                )
            )
            <= balance_tolerance_mw
            for row in cases
        ),
    }
    robustness_pass = bool(all(gates.values()))
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "pass" if robustness_pass else "diagnostic",
        "integrity_status": (
            "pass"
            if gates["input_contract_complete"]
            and gates["registered_denominator_complete"]
            else "fail"
        ),
        "robustness_status": "pass" if robustness_pass else "needs_optimization",
        "claim_tested": (
            "published base and stored bounded corrective OPF commands remain "
            "AC-feasible and secure when active and reactive demand are jointly "
            "perturbed at constant power factor over the complete registered grid"
        ),
        "stress_contract": {
            "stress_fractions": list(stress_points),
            "stress_percent": [100.0 * value for value in stress_points],
            "active_load_policy": "uniform multiplicative perturbation",
            "reactive_load_policy": (
                "scaled by the same multiplier as active load, preserving each "
                "in-service load's source-scenario power factor"
            ),
            "selection_policy": (
                "sealed full Cartesian replay over every published action, all "
                "six stress points, the base topology, and all seven registered "
                "N-1 identities; no action is excluded or re-optimized"
            ),
        },
        "hard_thresholds": {
            "voltage_bounds_pu": [0.95, 1.05],
            "thermal_loading_limit_percent": 100.0,
            "minimum_worst_voltage_margin_pu": (
                ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
            ),
            "power_balance_tolerance_mw": balance_tolerance_mw,
            "required_convergence_rate": 1.0,
            "required_safety_rate": 1.0,
        },
        "registered_denominator": {
            "published_action_count": len(results),
            "stress_point_count_per_action": len(stress_points),
            "expected_base_case_count": expected_base,
            "expected_registered_n1_case_count": expected_n1,
            "expected_total_case_count": expected_base + expected_n1,
            "observed_total_case_count": len(cases),
            "complete": denominator_complete,
        },
        "base_stress_summary": base_summary,
        "registered_n1_stress_summary": n1_summary,
        "system_statistics": active_stress.grouped_statistics(
            cases,
            lambda row: row.get("system"),
        ),
        "perturbation_statistics": active_stress.grouped_statistics(
            cases,
            lambda row: f"{float(row['stress_percent']):+.0f}%",
        ),
        "case_type_statistics": active_stress.grouped_statistics(
            cases,
            lambda row: row.get("case_type"),
        ),
        "optimization_diagnostics": active_stress.optimization_diagnostics(
            cases,
            balance_tolerance_mw=balance_tolerance_mw,
        ),
        "hard_gates": gates,
        "input_validation": validation,
        "input_artifacts": input_artifacts,
        "implementation": [
            {
                "path": "scripts/validate_opf_action_constant_power_factor_stress.py",
                "sha256": active_stress.file_sha256(Path(__file__)),
            },
            {
                "path": "scripts/validate_opf_action_uncertainty_stress.py",
                "sha256": active_stress.file_sha256(Path(active_stress.__file__)),
                "role": "shared AC replay and safety kernel",
            },
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--opf-results",
        default="simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
    )
    parser.add_argument(
        "--opf-commit",
        default=(
            "simulation_outputs/opf_closed_loop/"
            "opf_closed_loop_commit_v1.2_sd_core.json"
        ),
    )
    parser.add_argument(
        "--scenarios",
        default=(
            "simulation_outputs/opf_closed_loop/"
            "ieee14_ieee118_source_scenarios_v1.json"
        ),
    )
    parser.add_argument("--additional-scenarios", nargs="*", default=[])
    parser.add_argument("--power-balance-tolerance-mw", type=float, default=1e-3)
    parser.add_argument("--active-power-replay-tolerance-mw", type=float, default=1e-4)
    parser.add_argument(
        "--reactive-power-replay-tolerance-mvar",
        type=float,
        default=1e-3,
    )
    parser.add_argument("--voltage-replay-tolerance-pu", type=float, default=1e-6)
    parser.add_argument(
        "--allow-diagnostic",
        action="store_true",
        help="Return zero for a complete diagnostic artifact that fails robustness.",
    )
    parser.add_argument(
        "--case-results-json",
        default=(
            "simulation_outputs/opf_closed_loop/"
            "opf_action_constant_power_factor_stress_cases_v1.json"
        ),
    )
    parser.add_argument(
        "--report-json",
        default=(
            "reports/"
            "opf_action_constant_power_factor_stress_v1.2_sd_core.json"
        ),
    )
    parser.add_argument(
        "--commit-json",
        default=(
            "simulation_outputs/opf_closed_loop/"
            "opf_action_constant_power_factor_stress_commit_v1.json"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results_path = active_stress.resolve_input_path(Path(args.opf_results))
    opf_commit_path = active_stress.resolve_input_path(Path(args.opf_commit))
    scenario_paths = [
        active_stress.resolve_input_path(Path(args.scenarios)),
        *[
            active_stress.resolve_input_path(Path(value))
            for value in args.additional_scenarios
        ],
    ]
    scenario_arguments = [args.scenarios, *args.additional_scenarios]
    case_path = active_stress.resolve_input_path(Path(args.case_results_json))
    report_path = active_stress.resolve_input_path(Path(args.report_json))
    output_commit_path = active_stress.resolve_input_path(Path(args.commit_json))
    active_stress.validate_output_locations(
        [results_path, opf_commit_path, *scenario_paths],
        [case_path, report_path, output_commit_path],
    )
    output_commit_path.unlink(missing_ok=True)
    commit_validation = active_stress.validate_opf_commit(
        results_path,
        opf_commit_path,
    )
    results = active_stress.load_published_results(results_path)
    scenarios = active_stress.load_scenario_registry(scenario_paths)
    result_by_scenario = {str(row["scenario_id"]): row for row in results}
    missing = sorted(set(result_by_scenario) - set(scenarios))
    if missing:
        raise ValueError(
            f"published OPF results have missing scenarios: {missing[:20]}"
        )

    validation = []
    action_nets = {}
    for result in results:
        scenario_id = str(result["scenario_id"])
        scenario = scenarios[scenario_id]
        net = active_stress.build_net(scenario)
        contract = active_stress.validate_result_contract(result, scenario, net)
        active_stress.apply_published_action(net, result)
        replay = active_stress.validate_zero_stress_replay(
            net,
            result,
            active_power_tolerance_mw=args.active_power_replay_tolerance_mw,
            reactive_power_tolerance_mvar=args.reactive_power_replay_tolerance_mvar,
            voltage_tolerance_pu=args.voltage_replay_tolerance_pu,
        )
        n1_replays = active_stress.validate_zero_stress_registered_n1_replays(
            net,
            result,
            active_power_tolerance_mw=args.active_power_replay_tolerance_mw,
            reactive_power_tolerance_mvar=args.reactive_power_replay_tolerance_mvar,
            voltage_tolerance_pu=args.voltage_replay_tolerance_pu,
            balance_tolerance_mw=args.power_balance_tolerance_mw,
        )
        validation.append(
            {
                "scenario_id": scenario_id,
                "system": result.get("system"),
                "contract": contract,
                "zero_stress_replay": replay,
                "zero_stress_registered_n1_replays": n1_replays,
                "status": "pass",
            }
        )
        action_nets[scenario_id] = net

    specs = preregistered_case_specs(results)
    cases = [
        execute_case(
            action_nets[str(spec["scenario_id"])],
            result_by_scenario[str(spec["scenario_id"])],
            spec,
            balance_tolerance_mw=args.power_balance_tolerance_mw,
        )
        for spec in specs
    ]
    input_artifacts = {
        "opf_commit": {
            "path": args.opf_commit,
            "sha256": active_stress.file_sha256(opf_commit_path),
        },
        "opf_results": {
            "path": args.opf_results,
            "sha256": active_stress.file_sha256(results_path),
        },
        "scenario_manifests": [
            {
                "path": argument,
                "sha256": active_stress.file_sha256(path),
            }
            for argument, path in zip(
                scenario_arguments,
                scenario_paths,
                strict=True,
            )
        ],
        "opf_commit_validation": commit_validation,
        "zero_stress_replay_tolerances": {
            "active_power_mw": args.active_power_replay_tolerance_mw,
            "reactive_power_mvar": args.reactive_power_replay_tolerance_mvar,
            "voltage_pu": args.voltage_replay_tolerance_pu,
        },
    }
    report = build_report(
        results,
        cases,
        validation,
        input_artifacts,
        balance_tolerance_mw=args.power_balance_tolerance_mw,
    )
    case_payload = {
        "contract_version": CONTRACT_VERSION,
        "input_artifacts": input_artifacts,
        "registered_case_count": len(cases),
        "cases": cases,
    }
    active_stress.atomic_json(case_path, case_payload)
    active_stress.atomic_json(report_path, report)
    active_stress.atomic_json(
        output_commit_path,
        {
            "schema_version": "1.0",
            "status": active_stress.output_commit_status(report),
            "robustness_status": report["robustness_status"],
            "commit_policy": (
                "written last after atomically replacing and hashing every "
                "constant-power-factor stress artifact"
            ),
            "inputs": input_artifacts,
            "artifacts": {
                "cases": {
                    "path": args.case_results_json,
                    "sha256": active_stress.file_sha256(case_path),
                },
                "report": {
                    "path": args.report_json,
                    "sha256": active_stress.file_sha256(report_path),
                },
            },
        },
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "integrity_status": report["integrity_status"],
                "robustness_status": report["robustness_status"],
                "registered_case_count": len(cases),
                "report": str(report_path),
                "commit": str(output_commit_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    exit_code = active_stress.completion_exit_code(
        report,
        allow_diagnostic=args.allow_diagnostic,
    )
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
