#!/usr/bin/env python3
"""Generate and preflight a deterministic IEEE14 OPF security augmentation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from append_opf_closed_loop_auxiliary_records import (
    CandidateInfeasible,
    ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU,
    run_closed_loop,
)
from generate_grid_scenarios import (
    clone_net,
    factor_token,
    run_scenario,
)
from gridinstruct_utils import ROOT, ensure_dirs, write_json


def decimal_grid(
    start: str,
    stop: str,
    step: str,
    *,
    token_function=factor_token,
) -> list[float]:
    first = Decimal(start)
    last = Decimal(stop)
    increment = Decimal(step)
    if increment <= 0 or first > last:
        raise ValueError("invalid closed decimal grid")
    values = []
    current = first
    while current <= last:
        values.append(float(current))
        current += increment
    tokens = [token_function(value) for value in values]
    if len(tokens) != len(set(tokens)):
        raise ValueError("load grid collides under scenario ID factor tokens")
    return values


def candidate_scenario_id(
    load_level: float,
    generator_factor: float,
    first_line: int,
    second_line: int,
) -> str:
    """Use milliper-unit load tokens so boundary-refinement states stay unique."""
    load_token = f"{round(1000 * load_level):04d}"
    generator_token = f"{round(100 * generator_factor):03d}"
    return (
        "ieee14_secure_candidate_n2_"
        f"load_milli{load_token}_gen_centi{generator_token}_"
        f"lines_{first_line}_{second_line}"
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slack_adjacent_line_pairs(
    net,
    *,
    bottleneck_anchor_only: bool = False,
) -> list[tuple[int, int]]:
    slack_buses = {int(bus) for bus in net.ext_grid.bus.tolist()}
    anchors = sorted(
        int(idx)
        for idx, row in net.line.iterrows()
        if int(row["from_bus"]) in slack_buses or int(row["to_bus"]) in slack_buses
    )
    if bottleneck_anchor_only and anchors:
        anchors = [
            min(
                anchors,
                key=lambda idx: (
                    float(net.line.at[idx, "max_i_ka"])
                    if "max_i_ka" in net.line
                    else float("inf"),
                    idx,
                ),
            )
        ]
    pairs = {
        tuple(sorted((anchor, int(partner))))
        for anchor in anchors
        for partner in net.line.index
        if int(partner) != anchor
    }
    return sorted(pairs)


def generation_order(
    load_levels: list[float],
    generator_factors: list[float],
    line_pairs: list[tuple[int, int]],
) -> list[tuple[float, float, int, int]]:
    """Stable hash-interleaving distributes physical strata without outcomes."""
    registered = [
        (load_level, generator_factor, first, second)
        for load_level in load_levels
        for generator_factor in generator_factors
        for first, second in line_pairs
    ]
    return sorted(
        registered,
        key=lambda row: hashlib.sha256(
            json.dumps(row, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )


def source_binding(scenario: dict[str, Any]) -> tuple[str, str, str]:
    source = scenario.get("physical_source") or {}
    return (
        str(source.get("repository_commit") or ""),
        str(source.get("case_file") or ""),
        str(source.get("case_sha256") or ""),
    )


def passed_diversity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_load = Counter(f"{float(row['load_level']):.3f}" for row in rows)
    by_factor = Counter(f"{float(row.get('generator_factor') or 1.0):.2f}" for row in rows)
    by_pair = Counter(
        ",".join(str(int(value)) for value in row.get("line_indices") or [])
        for row in rows
    )
    return {
        "load_levels": sorted(float(value) for value in by_load),
        "generator_factors": sorted(float(value) for value in by_factor),
        "line_pairs": sorted(
            tuple(int(value) for value in key.split(","))
            for key in by_pair
            if key
        ),
        "passed_by_load_level": dict(sorted(by_load.items())),
        "passed_by_generator_factor": dict(sorted(by_factor.items())),
        "passed_by_line_pair": dict(sorted(by_pair.items())),
    }


def screening_order_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Prioritize recoverable headroom using only the pre-action AC state."""
    min_voltage = float(row.get("min_bus_voltage_pu") or 1.0)
    max_voltage = float(row.get("max_bus_voltage_pu") or 1.0)
    voltage_violation = max(0.95 - min_voltage, max_voltage - 1.05, 0.0)
    return (
        float(row.get("max_branch_loading_percent") or float("inf")),
        voltage_violation,
        float(row.get("load_level") or 1.0),
        abs(float(row.get("generator_factor") or 1.0) - 1.0),
        tuple(int(value) for value in (row.get("line_indices") or [])),
        str(row.get("scenario_id") or ""),
    )


def coverage_first_screening_order(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepend one deterministic representative of every physical stratum."""
    ordered = sorted(rows, key=screening_order_key)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add_representatives(key_function) -> None:
        strata = sorted({key_function(row) for row in ordered})
        for stratum in strata:
            representative = next(
                row for row in ordered if key_function(row) == stratum
            )
            scenario_id = str(representative.get("scenario_id") or "")
            if scenario_id not in selected_ids:
                selected.append(representative)
                selected_ids.add(scenario_id)

    add_representatives(
        lambda row: tuple(int(value) for value in row.get("line_indices") or [])
    )
    add_representatives(lambda row: round(float(row["load_level"]), 8))
    add_representatives(
        lambda row: round(float(row.get("generator_factor") or 1.0), 8)
    )
    selected.extend(
        row
        for row in ordered
        if str(row.get("scenario_id") or "") not in selected_ids
    )
    if len(selected) != len(ordered):
        raise RuntimeError("coverage-first screening order lost registered candidates")
    return selected


def select_diverse_target(
    rows: list[dict[str, Any]],
    target: int,
    minimum_load_levels: int,
    minimum_generator_factors: int,
    minimum_line_pairs: int,
) -> list[dict[str, Any]]:
    """Select an exact target while preserving the registered screening order."""
    if target <= 0:
        raise ValueError("target must be positive")
    if len(rows) < target:
        raise ValueError(
            f"robust-pass pool is smaller than the exact target: {len(rows)}/{target}"
        )

    selected_indices: set[int] = set()

    def add_representatives(
        key_function,
        minimum_count: int,
        label: str,
    ) -> None:
        observed = {
            key_function(rows[index])
            for index in selected_indices
        }
        for index, row in enumerate(rows):
            if len(observed) >= minimum_count:
                break
            value = key_function(row)
            if value in observed:
                continue
            selected_indices.add(index)
            observed.add(value)
        if len(observed) < minimum_count:
            raise ValueError(
                f"robust-pass pool has insufficient {label} diversity: "
                f"{len(observed)}/{minimum_count}"
            )

    add_representatives(
        lambda row: round(float(row["load_level"]), 8),
        minimum_load_levels,
        "load-level",
    )
    add_representatives(
        lambda row: round(float(row.get("generator_factor") or 1.0), 8),
        minimum_generator_factors,
        "generator-factor",
    )
    add_representatives(
        lambda row: tuple(int(value) for value in row.get("line_indices") or []),
        minimum_line_pairs,
        "line-pair",
    )
    if len(selected_indices) > target:
        raise ValueError(
            "minimum diversity representatives exceed the exact target"
        )
    for index in range(len(rows)):
        if len(selected_indices) >= target:
            break
        selected_indices.add(index)

    selected = [rows[index] for index in sorted(selected_indices)]
    diversity = passed_diversity(selected)
    if len(selected) != target:
        raise ValueError(
            f"deterministic selector did not fill the exact target: "
            f"{len(selected)}/{target}"
        )
    if (
        len(diversity["load_levels"]) < minimum_load_levels
        or len(diversity["generator_factors"]) < minimum_generator_factors
        or len(diversity["line_pairs"]) < minimum_line_pairs
    ):
        raise ValueError("deterministic selector did not preserve required diversity")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pglib-root",
        type=Path,
        default=Path("third_party/pglib-opf-v23.07"),
    )
    parser.add_argument("--load-start", default="0.600")
    parser.add_argument("--load-stop", default="0.720")
    parser.add_argument("--load-step", default="0.010")
    parser.add_argument("--boundary-load-start", default="0.620")
    parser.add_argument("--boundary-load-stop", default="0.630")
    parser.add_argument("--boundary-load-step", default="0.001")
    parser.add_argument(
        "--generator-factors",
        nargs="+",
        type=float,
        default=[
            round(0.50 + 0.01 * index, 2)
            for index in range(101)
            if round(0.50 + 0.01 * index, 2) != 1.0
        ],
    )
    parser.add_argument("--target-passed-candidates", type=int, default=120)
    parser.add_argument("--min-passed-load-levels", type=int, default=3)
    parser.add_argument("--min-passed-generator-factors", type=int, default=5)
    parser.add_argument("--min-passed-line-pairs", type=int, default=1)
    parser.add_argument("--post-action-n1-checks", type=int, default=5)
    parser.add_argument("--post-action-generator-n1-checks", type=int, default=2)
    parser.add_argument(
        "--loading-margins",
        nargs="+",
        type=float,
        default=[90.0],
    )
    parser.add_argument("--power-balance-tolerance-mw", type=float, default=1e-3)
    parser.add_argument(
        "--output-scenarios",
        default="simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
    )
    parser.add_argument(
        "--output-report",
        default="reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--progress-json",
        default="reports/ieee14_opf_secure_candidate_augmentation_progress_v1.2_sd_core.json",
    )
    args = parser.parse_args()

    if args.target_passed_candidates <= 100:
        raise ValueError("formal augmentation requires more than 100 passed candidates")
    pglib_root = (ROOT / args.pglib_root).resolve()
    load_levels = sorted(
        set(
            decimal_grid(args.load_start, args.load_stop, args.load_step)
            + decimal_grid(
                args.boundary_load_start,
                args.boundary_load_stop,
                args.boundary_load_step,
                token_function=lambda value: f"{value:.3f}",
            )
        )
    )
    generator_factors = [float(value) for value in args.generator_factors]
    if len(generator_factors) != len(set(generator_factors)):
        raise ValueError("generator factors must be unique")
    net = clone_net("ieee14", pglib_root)
    line_pairs = slack_adjacent_line_pairs(net, bottleneck_anchor_only=True)
    registered = generation_order(load_levels, generator_factors, line_pairs)
    progress_path = ROOT / args.progress_json
    ensure_dirs(progress_path.parent)
    write_json(
        progress_path,
        {
            "status": "generating_power_flow_candidates",
            "registered_candidate_count": len(registered),
            "generated_candidate_count": 0,
            "screened_candidate_count": 0,
            "screened_pass_count": 0,
        },
    )

    generated = []
    for ordinal, (load_level, generator_factor, first, second) in enumerate(
        registered,
        start=1,
    ):
        scenario = run_scenario(
                "ieee14",
                load_level,
                "n2_branch_outage",
                first,
                generator_factor,
                second_branch_idx=second,
                pglib_root=pglib_root,
                scale_generation_with_load=True,
            )
        scenario["scenario_id"] = candidate_scenario_id(
            load_level,
            generator_factor,
            first,
            second,
        )
        generated.append(scenario)
        if ordinal % 100 == 0 or ordinal == len(registered):
            print(
                f"generated_pf={ordinal}/{len(registered)}",
                flush=True,
            )
            write_json(
                progress_path,
                {
                    "status": "generating_power_flow_candidates",
                    "registered_candidate_count": len(registered),
                    "generated_candidate_count": ordinal,
                    "screened_candidate_count": 0,
                    "screened_pass_count": 0,
                },
            )
    scenario_ids = [str(row.get("scenario_id") or "") for row in generated]
    if any(not value for value in scenario_ids) or len(scenario_ids) != len(
        set(scenario_ids)
    ):
        raise ValueError("generated OPF security candidates have missing or duplicate IDs")
    bindings = {source_binding(row) for row in generated}
    if len(bindings) != 1 or any(not value for value in next(iter(bindings))):
        raise ValueError("generated OPF candidates do not share one complete PGLib binding")

    eligible = [
        row
        for row in generated
        if row.get("solver_status") == "converged"
        and (row.get("violation_type") or ["none"]) != ["none"]
    ]
    eligible = coverage_first_screening_order(eligible)
    screened = []
    passed = []
    for scenario in eligible:
        diversity = passed_diversity(passed)
        if (
            len(passed) >= args.target_passed_candidates
            and len(diversity["load_levels"]) >= args.min_passed_load_levels
            and len(diversity["generator_factors"])
            >= args.min_passed_generator_factors
            and len(diversity["line_pairs"]) >= args.min_passed_line_pairs
        ):
            break
        try:
            result = run_closed_loop(
                scenario,
                args.post_action_n1_checks,
                args.post_action_generator_n1_checks,
                tuple(args.loading_margins),
                args.power_balance_tolerance_mw,
            )
            screened.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "status": "pass",
                    "selected_loading_margin_percent": result[
                        "selected_loading_margin_percent"
                    ],
                    "post_action_n1_summary": {
                        key: result["post_action_n1"][key]
                        for key in (
                            "requested_checks",
                            "completed_checks",
                            "secure_checks",
                            "security_rate",
                            "all_control_feasibility_checks_pass",
                        )
                    },
                    "active_load_uncertainty_construction_gate": result[
                        "active_load_uncertainty_construction_gate"
                    ],
                    "constant_power_factor_uncertainty_construction_gate": result[
                        "constant_power_factor_uncertainty_construction_gate"
                    ],
                    "load_uncertainty_construction_gate": result[
                        "load_uncertainty_construction_gate"
                    ],
                    "relative_violation_reduction": result[
                        "relative_violation_reduction"
                    ],
                }
            )
            passed.append(scenario)
        except CandidateInfeasible as exc:
            screened.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "status": "registered_ac_security_infeasible",
                    "attempts": exc.attempts,
                }
            )
        except Exception as exc:  # noqa: BLE001
            screened.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "status": "screening_error",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
        print(
            f"screened={len(screened)} pass={len(passed)} "
            f"scenario={scenario['scenario_id']}",
            flush=True,
        )
        write_json(
            progress_path,
            {
                "status": "screening_ac_opf_security",
                "registered_candidate_count": len(generated),
                "eligible_violation_candidate_count": len(eligible),
                "screened_candidate_count": len(screened),
                "screened_pass_count": len(passed),
                "last_scenario_id": scenario["scenario_id"],
            },
        )

    robust_passed = list(passed)
    selection_error = None
    try:
        passed = select_diverse_target(
            robust_passed,
            args.target_passed_candidates,
            args.min_passed_load_levels,
            args.min_passed_generator_factors,
            args.min_passed_line_pairs,
        )
    except ValueError as exc:
        selection_error = str(exc)
        passed = robust_passed[: args.target_passed_candidates]
    selected_ids = {str(row["scenario_id"]) for row in passed}
    for row in screened:
        if (
            row.get("status") == "pass"
            and str(row.get("scenario_id") or "") not in selected_ids
        ):
            row["status"] = "robust_pass_not_selected"

    diversity = passed_diversity(passed)
    passed_load_levels = diversity["load_levels"]
    passed_generator_factors = diversity["generator_factors"]
    passed_line_pairs = diversity["line_pairs"]
    diversity_pass = (
        len(passed_load_levels) >= args.min_passed_load_levels
        and len(passed_generator_factors) >= args.min_passed_generator_factors
        and len(passed_line_pairs) >= args.min_passed_line_pairs
    )
    passed_screening_rows = [
        row for row in screened if row.get("status") == "pass"
    ]
    expected_n1_checks = (
        args.post_action_n1_checks + args.post_action_generator_n1_checks
    )
    expected_uncertainty_cases_per_model = 6 * (1 + expected_n1_checks)
    expected_uncertainty_cases = 2 * expected_uncertainty_cases_per_model
    passed_denominators_complete = (
        len(passed_screening_rows) == len(passed)
        and all(
            (row.get("post_action_n1_summary") or {}).get("requested_checks")
            == expected_n1_checks
            and (row.get("post_action_n1_summary") or {}).get("completed_checks")
            == expected_n1_checks
            and (row.get("post_action_n1_summary") or {}).get("secure_checks")
            == expected_n1_checks
            and (
                row.get("post_action_n1_summary") or {}
            ).get("all_control_feasibility_checks_pass")
            is True
            and all(
                (row.get(key) or {}).get("status") == "pass"
                and (row.get(key) or {}).get("registered_case_count")
                == expected_uncertainty_cases_per_model
                and (row.get(key) or {}).get("safe_case_count")
                == expected_uncertainty_cases_per_model
                and (row.get(key) or {}).get("denominator_complete") is True
                and float(
                    (row.get(key) or {}).get(
                        "minimum_worst_voltage_margin_pu"
                    )
                    if (row.get(key) or {}).get(
                        "minimum_worst_voltage_margin_pu"
                    )
                    is not None
                    else -1.0
                )
                >= ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
                and float(
                    (row.get(key) or {}).get(
                        "minimum_thermal_margin_percent"
                    )
                    if (row.get(key) or {}).get(
                        "minimum_thermal_margin_percent"
                    )
                    is not None
                    else -1.0
                )
                >= 0.0
                for key in (
                    "active_load_uncertainty_construction_gate",
                    "constant_power_factor_uncertainty_construction_gate",
                )
            )
            and (
                row.get("load_uncertainty_construction_gate") or {}
            ).get("status")
            == "pass"
            and (
                row.get("load_uncertainty_construction_gate") or {}
            ).get("load_model_count")
            == 2
            and (
                row.get("load_uncertainty_construction_gate") or {}
            ).get("registered_case_count")
            == expected_uncertainty_cases
            and (
                row.get("load_uncertainty_construction_gate") or {}
            ).get("safe_case_count")
            == expected_uncertainty_cases
            and (
                row.get("load_uncertainty_construction_gate") or {}
            ).get("denominator_complete")
            is True
            and float(row.get("relative_violation_reduction") or 0.0) >= 1.0
            for row in passed_screening_rows
        )
    )
    status = (
        "pass"
        if len(passed) == args.target_passed_candidates
        and selection_error is None
        and diversity_pass
        and passed_denominators_complete
        and all((row.get("physical_source") or {}).get("case_sha256") for row in passed)
        else "fail"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "contract": "ieee14-opf-robust-secure-candidate-augmentation-v4",
        "selection_policy": (
            "closed decimal load grid crossed with fixed generator factors and every "
            "two-line outage containing the minimum-RATE_A slack-adjacent bottleneck "
            "line; the load register combines a 0.01-p.u. operating grid with a "
            "systematic 0.001-p.u. boundary-refinement grid over 0.620--0.630; "
            "before AC-OPF screening, one pre-action-headroom-ranked "
            "representative of every outage, load, and generator-factor stratum is "
            "placed first, followed by all remaining states in ascending pre-action "
            "branch loading, voltage-violation magnitude, load level, "
            "generator-factor distance, outage identity, and scenario ID; every "
            "ordering field is fixed before OPF and uses no uncertainty outcome; "
            "after the robust-pass pool reaches the target and required physical "
            "coverage, first representatives of the required load, generator-factor, "
            "and outage strata are retained and the earliest remaining robust passes "
            "fill an exact-size target in the original screening order; each retained "
            "candidate passes the same registered N-1 and two-model, six-point "
            "load-uncertainty construction gate used for formal publication"
        ),
        "candidate_acceptance_gate": {
            "pre_contingency_loading_margins_percent": args.loading_margins,
            "registered_n1_states_per_candidate": (
                args.post_action_n1_checks
                + args.post_action_generator_n1_checks
            ),
            "load_models": [
                "active_only_with_source_reactive_load",
                "uniform_constant_power_factor",
            ],
            "load_perturbations": [-0.10, -0.05, -0.02, 0.02, 0.05, 0.10],
            "uncertainty_states_per_model_per_candidate": 6
            * (
                1
                + args.post_action_n1_checks
                + args.post_action_generator_n1_checks
            ),
            "uncertainty_states_per_candidate": 2
            * 6
            * (
                1
                + args.post_action_n1_checks
                + args.post_action_generator_n1_checks
            ),
            "minimum_worst_voltage_margin_pu": (
                ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
            ),
            "minimum_thermal_margin_percent": 0.0,
            "denominator_policy": "all registered states must pass",
        },
        "implementation": [
            {
                "path": str(Path(__file__).resolve().relative_to(ROOT)),
                "sha256": file_sha256(Path(__file__).resolve()),
            },
            {
                "path": str(
                    Path(run_closed_loop.__code__.co_filename)
                    .resolve()
                    .relative_to(ROOT)
                ),
                "sha256": file_sha256(
                    Path(run_closed_loop.__code__.co_filename).resolve()
                ),
            },
            {
                "path": str(
                    Path(run_scenario.__code__.co_filename)
                    .resolve()
                    .relative_to(ROOT)
                ),
                "sha256": file_sha256(
                    Path(run_scenario.__code__.co_filename).resolve()
                ),
            },
        ],
        "load_levels": load_levels,
        "generator_factors": generator_factors,
        "line_pairs": [list(pair) for pair in line_pairs],
        "registered_candidate_count": len(generated),
        "registered_unique_scenario_count": len(set(scenario_ids)),
        "power_flow_outcomes": dict(
            Counter(str(row.get("solver_status")) for row in generated)
        ),
        "eligible_violation_candidate_count": len(eligible),
        "screened_candidate_count": len(screened),
        "screened_pass_count": len(passed),
        "robust_gate_pass_count": len(robust_passed),
        "robust_gate_pass_not_selected_count": (
            len(robust_passed) - len(passed)
        ),
        "screened_rejection_count": sum(
            row["status"] == "registered_ac_security_infeasible"
            for row in screened
        ),
        "screening_error_count": sum(
            row["status"] == "screening_error" for row in screened
        ),
        "eligible_not_screened_after_target_count": len(eligible) - len(screened),
        "target_passed_candidates": args.target_passed_candidates,
        "selection_error": selection_error,
        "passed_load_levels": passed_load_levels,
        "passed_generator_factors": passed_generator_factors,
        "passed_line_pairs": [list(pair) for pair in passed_line_pairs],
        "passed_by_load_level": diversity["passed_by_load_level"],
        "passed_by_generator_factor": diversity["passed_by_generator_factor"],
        "passed_by_line_pair": diversity["passed_by_line_pair"],
        "passed_diversity": {
            "status": "pass" if diversity_pass else "fail",
            "minimum_load_levels": args.min_passed_load_levels,
            "minimum_generator_factors": args.min_passed_generator_factors,
            "minimum_line_pairs": args.min_passed_line_pairs,
            "observed_load_levels": len(passed_load_levels),
            "observed_generator_factors": len(passed_generator_factors),
            "observed_line_pairs": len(passed_line_pairs),
        },
        "hard_gates": {
            "target_passed_candidate_count_met": (
                len(passed) == args.target_passed_candidates
            ),
            "deterministic_exact_target_selection_completed": (
                selection_error is None
            ),
            "passed_candidate_diversity_met": diversity_pass,
            "passed_candidate_denominators_complete": passed_denominators_complete,
            "all_passed_candidates_source_bound": all(
                (row.get("physical_source") or {}).get("case_sha256")
                for row in passed
            ),
        },
        "post_action_n1_checks": args.post_action_n1_checks,
        "post_action_generator_n1_checks": args.post_action_generator_n1_checks,
        "loading_margins": args.loading_margins,
        "power_balance_tolerance_mw": args.power_balance_tolerance_mw,
        "physical_source_binding": list(next(iter(bindings))),
        "screening_outcomes": screened,
        "output_scenario_ids": [row["scenario_id"] for row in passed],
    }
    report_path = ROOT / args.output_report
    output_path = ROOT / args.output_scenarios
    ensure_dirs(report_path.parent, output_path.parent)
    if status != "pass":
        write_json(report_path, report)
        raise RuntimeError(
            f"secure candidate target was not met: {len(passed)}/"
            f"{args.target_passed_candidates}"
        )
    write_json(output_path, passed)
    report["output_scenarios"] = {
        "path": str(output_path.relative_to(ROOT)),
        "sha256": file_sha256(output_path),
        "record_count": len(passed),
    }
    write_json(report_path, report)
    write_json(
        progress_path,
        {
            "status": "pass",
            "registered_candidate_count": len(generated),
            "eligible_violation_candidate_count": len(eligible),
            "screened_candidate_count": len(screened),
            "screened_pass_count": len(passed),
            "robust_gate_pass_count": len(robust_passed),
        },
    )
    print(json.dumps({key: report[key] for key in (
        "status",
        "registered_candidate_count",
        "eligible_violation_candidate_count",
        "screened_candidate_count",
        "screened_pass_count",
        "robust_gate_pass_count",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
