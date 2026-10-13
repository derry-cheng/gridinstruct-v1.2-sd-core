#!/usr/bin/env python3
"""Run an external-topology stress check on larger pandapower networks.

Converged scenarios from this script can be converted into formal OOD
instruction records by append_topology_instruction_records.py.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandapower as pp
import pandapower.networks as pn
from pandapower.optimal_powerflow import OPFNotConverged
from pandapower.powerflow import LoadflowNotConverged
from pandapower.topology import unsupplied_buses

from generate_grid_scenarios import summarize_results
from gridinstruct_utils import ROOT, ensure_dirs, write_json
from pglib_network_loader import load_pglib_network, network_provenance
from power_evidence_common import (
    source_bound_loading_from_state_rows,
    source_bound_rating_map_from_loaded_network,
)


SYSTEM_LOADERS = {
    "ieee300": pn.case300,
    "illinois200": pn.case_illinois200,
    "pegase89": pn.case89pegase,
    "pegase1354": pn.case1354pegase,
    "rte1888": pn.case1888rte,
    "rte2848": pn.case2848rte,
    "pegase2869": pn.case2869pegase,
}

ENVELOPE_CLASSES = (
    "normal-envelope",
    "operational-stress",
    "emergency-stress",
    "extreme-stress",
)


def jsonable(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float):
        return round(value, 6)
    return value


def scale_loads(net, factor: float) -> None:
    if len(net.load):
        net.load["p_mw"] *= factor
        net.load["q_mvar"] *= factor


def scale_generation(net, factor: float) -> None:
    if len(net.gen):
        net.gen["p_mw"] *= factor


def scale_generator_voltage_setpoints(net, factor: float) -> None:
    """Perturb voltage controls while respecting source bus voltage bounds."""

    if abs(factor - 1.0) <= 1e-12:
        return
    for element in ("ext_grid", "gen"):
        table = getattr(net, element)
        for index, row in table.iterrows():
            bus = int(row["bus"])
            lower = float(net.bus.at[bus, "min_vm_pu"])
            upper = float(net.bus.at[bus, "max_vm_pu"])
            table.at[index, "vm_pu"] = max(
                lower,
                min(upper, float(row["vm_pu"]) * factor),
            )


def apply_normal_ac_opf_envelope(net) -> None:
    """Constrain an OPF construction to the preregistered normal envelope."""

    net.bus["min_vm_pu"] = net.bus["min_vm_pu"].clip(lower=0.95)
    net.bus["max_vm_pu"] = net.bus["max_vm_pu"].clip(upper=1.05)
    for element in ("ext_grid", "gen"):
        table = getattr(net, element)
        for index, row in table.iterrows():
            bus = int(row["bus"])
            table.at[index, "vm_pu"] = max(
                float(net.bus.at[bus, "min_vm_pu"]),
                min(
                    float(net.bus.at[bus, "max_vm_pu"]),
                    float(row["vm_pu"]),
                ),
            )
    for element in ("line", "trafo"):
        table = getattr(net, element)
        if len(table):
            if "max_loading_percent" not in table.columns:
                table["max_loading_percent"] = 95.0
            else:
                table["max_loading_percent"] = (
                    table["max_loading_percent"].fillna(95.0).clip(upper=95.0)
                )


def candidate_lines(
    line_count: int,
    *,
    seed: int,
    namespace: str,
) -> list[int]:
    """Return a deterministic, coverage-aware candidate order for N-1 search."""
    if line_count <= 0:
        return []
    indices = list(range(line_count))
    anchors = [idx for idx in (0, line_count // 2, line_count - 1) if idx < line_count]
    anchors = list(dict.fromkeys(anchors))
    remaining = [idx for idx in indices if idx not in set(anchors)]
    remaining.sort(
        key=lambda idx: (
            hashlib.sha256(f"{seed}:{namespace}:{idx}".encode("utf-8")).hexdigest(),
            idx,
        )
    )
    return anchors + remaining


def factor_token(value: float) -> str:
    scaled = value * 100.0
    if abs(scaled - round(scaled)) <= 1e-9:
        return str(int(round(scaled)))
    return format(value, ".12g").replace("-", "m").replace(".", "p")


def summarize(net) -> dict[str, Any]:
    return summarize_results(net)


def envelope_class(row: dict[str, Any]) -> str:
    if row.get("solver_status") != "converged":
        return "solver-not-converged"
    max_loading = float(row.get("max_branch_loading_percent") or 0.0)
    min_vm = float(row.get("min_bus_voltage_pu") or 1.0)
    max_vm = float(row.get("max_bus_voltage_pu") or 1.0)
    if max_loading <= 100.0 and 0.95 <= min_vm and max_vm <= 1.05:
        return "normal-envelope"
    if max_loading <= 120.0 and 0.90 <= min_vm and max_vm <= 1.10:
        return "operational-stress"
    if max_loading <= 300.0 and 0.80 <= min_vm and max_vm <= 1.20:
        return "emergency-stress"
    return "extreme-stress"


def run_case(
    system: str,
    load_level: float,
    contingency: str,
    line_idx: int | None,
    scenario_namespace: str | None = None,
    pglib_root: Path | None = None,
    scale_generation_with_load: bool = False,
    generator_voltage_factor: float = 1.0,
    dispatch_policy: str = "fixed_power_flow",
    solver_algorithm: str = "nr",
    enforce_q_lims: bool = True,
    max_iteration: int = 50,
) -> dict[str, Any]:
    net = (
        load_pglib_network(system, pglib_root)
        if pglib_root is not None
        else SYSTEM_LOADERS[system]()
    )
    scale_loads(net, load_level)
    if scale_generation_with_load:
        scale_generation(net, load_level)
    scale_generator_voltage_setpoints(net, generator_voltage_factor)
    affected = []
    if contingency == "n1_branch_outage" and line_idx is not None:
        if 0 <= line_idx < len(net.line):
            row = net.line.loc[line_idx]
            affected = [f"{int(row.from_bus)}-{int(row.to_bus)}"]
            net.line.at[line_idx, "in_service"] = False

    namespace = f"_{scenario_namespace}" if scenario_namespace else ""
    scenario_id = f"{system}{namespace}_{contingency}_load{factor_token(load_level)}"
    if scale_generation_with_load:
        scenario_id += "_generationlinked"
    if abs(generator_voltage_factor - 1.0) > 1e-12:
        scenario_id += f"_vm{factor_token(generator_voltage_factor)}"
    if dispatch_policy != "fixed_power_flow":
        scenario_id += f"_{dispatch_policy}"
    if line_idx is not None:
        scenario_id += f"_line{line_idx}"

    unsupplied = sorted(int(value) for value in unsupplied_buses(net))
    try:
        if unsupplied:
            raise RuntimeError(f"unsupplied buses: {unsupplied}")
        if dispatch_policy == "normal_ac_opf":
            apply_normal_ac_opf_envelope(net)
            pp.runopp(
                net,
                calculate_voltage_angles=True,
                init="flat",
                verbose=False,
                numba=False,
                suppress_warnings=True,
            )
        elif dispatch_policy == "fixed_power_flow":
            pp.runpp(
                net,
                algorithm=solver_algorithm,
                init="auto",
                tolerance_mva=1e-6,
                max_iteration=max_iteration,
                enforce_q_lims=enforce_q_lims,
                numba=False,
            )
        else:
            raise ValueError(f"unknown dispatch policy: {dispatch_policy}")
        status = "converged"
        failure_class = None
        error = None
        result = summarize(net)
        if pglib_root is not None:
            native_loading = {
                "max_branch_loading_percent": result["max_branch_loading_percent"],
                "max_line_loading_percent": result["max_line_loading_percent"],
                "max_transformer_loading_percent": result[
                    "max_transformer_loading_percent"
                ],
            }
            source_bound_loading = source_bound_loading_from_state_rows(
                result["line_results"],
                result["transformer_results"],
                source_bound_rating_map_from_loaded_network(net),
            )
            result.update(source_bound_loading)
            result["native_solver_loading_percent"] = native_loading
            result["thermal_rating_contract"] = {
                "provider": "IEEE PES PGLib-OPF",
                "repository_commit": network_provenance(net)["repository_commit"],
                "loading_definition": (
                    "maximum terminal apparent power divided by RATE_A"
                ),
            }
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        failure_class = (
            "unsupplied_island"
            if unsupplied
            else "optimization_nonconvergence"
            if isinstance(exc, OPFNotConverged)
            else "numerical_nonconvergence"
            if isinstance(exc, LoadflowNotConverged)
            else "invalid_or_infeasible"
        )
        error = str(exc)
        result = {
            "max_branch_loading_percent": None,
            "max_line_loading_percent": None,
            "max_transformer_loading_percent": None,
            "min_bus_voltage_pu": None,
            "max_bus_voltage_pu": None,
            "overloaded_branch_count": 0,
            "overloaded_transformer_count": 0,
            "voltage_violation_count": 0,
            "overloaded_branches": [],
            "overloaded_transformers": [],
            "voltage_violations": [],
            "violation_type": [failure_class],
            "severity_level": "invalid",
            "threshold_policy": {
                "policy_id": "gridinstruct_ac_operating_envelope_v2",
                "line_loading_percent": 100.0,
                "transformer_loading_percent": 100.0,
                "bus_voltage_pu": [0.95, 1.05],
                "role": "dataset_policy",
            },
        }

    return {
        "scenario_id": scenario_id,
        "network_model": system,
        "contingency_type": contingency,
        "affected_components": {"branches": affected, "buses": []},
        "load_level": load_level,
        "base_generation_scaling_factor": (
            load_level if scale_generation_with_load else 1.0
        ),
        "generator_voltage_factor": generator_voltage_factor,
        "dispatch_policy": dispatch_policy,
        "line_index": line_idx,
        "solver": (
            "pandapower-ac-opf"
            if dispatch_policy == "normal_ac_opf"
            else "pandapower-ac-power-flow"
        ),
        "physical_source": network_provenance(net) if pglib_root is not None else None,
        "solver_status": status,
        "failure_class": failure_class,
        "unsupplied_buses": unsupplied,
        "error": error,
        **result,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Extended Topology Stress Audit",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        f"- Scenario records: {report['scenario_record_count']}",
        f"- Converged records: {report['converged_record_count']}",
        f"- Convergence rate: {report['convergence_rate']:.4f}",
        "",
        "## Networks",
        "",
        "| System | Buses | Lines | Scenarios | Converged | Max loading (%) | Min voltage (p.u.) | Max voltage (p.u.) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["network_summaries"]:
        lines.append(
            "| {system} | {bus_count} | {line_count} | {scenario_count} | {converged_count} | {max_loading} | {min_voltage} | {max_voltage} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "Converged scenarios are used as external-topology validation evidence and as candidate sources for formal OOD instruction records.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", nargs="+", default=["ieee300", "pegase89", "pegase1354", "rte1888"])
    parser.add_argument(
        "--pglib-root",
        type=Path,
        help="Pinned PGLib-OPF checkout used for all formal physical cases.",
    )
    parser.add_argument("--load-levels", nargs="+", type=float, default=[0.9, 1.0, 1.1])
    parser.add_argument(
        "--system-load-levels",
        action="append",
        default=[],
        help="Per-system override as system:level,level; used to preregister a convergent stress envelope.",
    )
    parser.add_argument("--max-lines-per-system", type=int, default=8)
    parser.add_argument(
        "--scale-generation-with-load",
        action="store_true",
        help="Scale every non-slack generator P setpoint with the load factor.",
    )
    parser.add_argument(
        "--generator-voltage-factors",
        nargs="+",
        type=float,
        default=[1.0],
        help="Deterministic generator/ext_grid voltage-setpoint perturbations.",
    )
    parser.add_argument(
        "--dispatch-policy",
        choices=("fixed_power_flow", "normal_ac_opf"),
        default="fixed_power_flow",
    )
    parser.add_argument(
        "--max-attempts-per-stratum",
        type=int,
        default=64,
        help="Maximum deterministic line-outage candidates tested to fill each load stratum.",
    )
    parser.add_argument(
        "--required-envelope-count",
        action="append",
        default=[],
        help="Preregister a global physical-envelope minimum as class:count.",
    )
    parser.add_argument("--seed", type=int, default=20260709)
    parser.add_argument("--scenario-namespace", default=None)
    parser.add_argument("--output-json", default="simulation_outputs/topology_stress/extended_topology_scenarios.json")
    parser.add_argument(
        "--attempts-json",
        default="reports/extended_topology_stress_attempts_v1.2_sd_core.json",
        help="Complete construction-attempt ledger, including every rejected or non-converged candidate.",
    )
    parser.add_argument("--report-json", default="reports/extended_topology_stress_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/extended_topology_stress_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    system_load_levels = {system: list(args.load_levels) for system in args.systems}
    for specification in args.system_load_levels:
        system, separator, values = specification.partition(":")
        if not separator or system not in system_load_levels:
            raise ValueError(f"invalid --system-load-levels specification: {specification}")
        system_load_levels[system] = [float(value) for value in values.split(",") if value]
    for system, levels in system_load_levels.items():
        load_tokens = [factor_token(value) for value in levels]
        if not levels or len(load_tokens) != len(set(load_tokens)):
            raise ValueError(f"{system} load levels contain duplicate or ID-colliding values: {levels}")
    voltage_tokens = [factor_token(value) for value in args.generator_voltage_factors]
    if (
        any(value <= 0 for value in args.generator_voltage_factors)
        or len(voltage_tokens) != len(set(voltage_tokens))
    ):
        raise ValueError(
            "generator voltage factors must be positive and ID-distinct: "
            f"{args.generator_voltage_factors}"
        )
    required_envelope_counts: dict[str, int] = {}
    for specification in args.required_envelope_count:
        name, separator, raw_count = specification.partition(":")
        if not separator or name not in ENVELOPE_CLASSES:
            raise ValueError(f"invalid --required-envelope-count specification: {specification}")
        count = int(raw_count)
        if count < 0:
            raise ValueError(f"required envelope count must be non-negative: {specification}")
        required_envelope_counts[name] = count

    ensure_dirs(ROOT / "simulation_outputs/topology_stress", ROOT / "reports")

    records: list[dict[str, Any]] = []
    candidate_attempts: list[dict[str, Any]] = []
    network_summaries = []
    for system in args.systems:
        if system not in SYSTEM_LOADERS:
            raise ValueError(f"Unknown system: {system}")
        net = (
            load_pglib_network(system, args.pglib_root)
            if args.pglib_root is not None
            else SYSTEM_LOADERS[system]()
        )
        pglib_kwargs = (
            {"pglib_root": args.pglib_root}
            if args.pglib_root is not None
            else {}
        )
        start = len(records)
        for load_level in system_load_levels[system]:
            for voltage_factor in args.generator_voltage_factors:
                construction_kwargs = {}
                if args.scale_generation_with_load:
                    construction_kwargs["scale_generation_with_load"] = True
                if abs(voltage_factor - 1.0) > 1e-12:
                    construction_kwargs["generator_voltage_factor"] = voltage_factor
                if args.dispatch_policy != "fixed_power_flow":
                    construction_kwargs["dispatch_policy"] = args.dispatch_policy
                base_case = run_case(
                    system,
                    load_level,
                    "base_power_flow",
                    None,
                    args.scenario_namespace,
                    **pglib_kwargs,
                    **construction_kwargs,
                )
                candidate_attempts.append(base_case)
                if base_case["solver_status"] == "converged":
                    records.append(base_case)

                selected = 0
                attempt_limit = min(args.max_attempts_per_stratum, len(net.line))
                for line_idx in candidate_lines(
                    len(net.line),
                    seed=args.seed,
                    namespace=(
                        f"{system}:{factor_token(load_level)}:"
                        f"vm{factor_token(voltage_factor)}:{args.dispatch_policy}"
                    ),
                )[:attempt_limit]:
                    candidate = run_case(
                        system,
                        load_level,
                        "n1_branch_outage",
                        line_idx,
                        args.scenario_namespace,
                        **pglib_kwargs,
                        **construction_kwargs,
                    )
                    candidate_attempts.append(candidate)
                    if candidate["solver_status"] != "converged":
                        continue
                    records.append(candidate)
                    selected += 1
                    if selected >= args.max_lines_per_system:
                        break
        system_records = records[start:]
        converged = [row for row in system_records if row["solver_status"] == "converged"]
        network_summaries.append(
            {
                "system": system,
                "bus_count": int(len(net.bus)),
                "line_count": int(len(net.line)),
                "scenario_count": len(system_records),
                "converged_count": len(converged),
                "max_loading": max((row.get("max_branch_loading_percent") or 0 for row in converged), default=0),
                "min_voltage": min((row.get("min_bus_voltage_pu") or 1 for row in converged), default=1),
                "max_voltage": max((row.get("max_bus_voltage_pu") or 1 for row in converged), default=1),
                "envelope_counts": dict(Counter(envelope_class(row) for row in system_records)),
            }
        )

    status_counts = Counter(row["solver_status"] for row in records)
    attempt_status_counts = Counter(row["solver_status"] for row in candidate_attempts)
    envelope_counts = Counter(envelope_class(row) for row in records)
    converged_count = int(status_counts.get("converged", 0))
    convergence_rate = converged_count / max(len(records), 1)
    systems_with_converged = sum(1 for row in network_summaries if row["converged_count"] > 0)
    scenario_ids = [str(row.get("scenario_id") or "") for row in records]
    strata = {
        (
            f"{system}|{contingency}|load={factor_token(load_level)}"
            f"|vm={factor_token(voltage_factor)}|dispatch={args.dispatch_policy}"
        ): {
            "attempted": sum(
                row["network_model"] == system
                and row["contingency_type"] == contingency
                and float(row["load_level"]) == float(load_level)
                and float(row.get("generator_voltage_factor", 1.0))
                == float(voltage_factor)
                and str(row.get("dispatch_policy", "fixed_power_flow"))
                == args.dispatch_policy
                for row in candidate_attempts
            ),
            "accepted": sum(
                row["network_model"] == system
                and row["contingency_type"] == contingency
                and float(row["load_level"]) == float(load_level)
                and float(row.get("generator_voltage_factor", 1.0))
                == float(voltage_factor)
                and str(row.get("dispatch_policy", "fixed_power_flow"))
                == args.dispatch_policy
                for row in records
            ),
            "failed": sum(
                row["network_model"] == system
                and row["contingency_type"] == contingency
                and float(row["load_level"]) == float(load_level)
                and float(row.get("generator_voltage_factor", 1.0))
                == float(voltage_factor)
                and str(row.get("dispatch_policy", "fixed_power_flow"))
                == args.dispatch_policy
                and row["solver_status"] != "converged"
                for row in candidate_attempts
            ),
        }
        for system in args.systems
        for contingency in ("base_power_flow", "n1_branch_outage")
        for load_level in system_load_levels[system]
        for voltage_factor in args.generator_voltage_factors
    }
    for key, item in strata.items():
        item["required_accepted"] = 1 if "|base_power_flow|" in key else args.max_lines_per_system
        item["acceptance_quota_met"] = item["accepted"] == item["required_accepted"]
    hard_gates = {
        "scenario_ids_unique": len(scenario_ids) == len(set(scenario_ids)) and bool(scenario_ids),
        "all_base_strata_filled": all(
            item["acceptance_quota_met"]
            for key, item in strata.items()
            if "|base_power_flow|" in key
        ),
        "all_n1_strata_filled": all(
            item["acceptance_quota_met"]
            for key, item in strata.items()
            if "|n1_branch_outage|" in key
        ),
        "all_published_scenarios_converged": converged_count == len(records),
        "all_systems_have_convergence": systems_with_converged == len(args.systems),
        "required_envelope_coverage": all(
            envelope_counts.get(name, 0) >= minimum
            for name, minimum in required_envelope_counts.items()
        ),
        "all_published_states_complete_and_finite": all(
            row.get("all_in_service_states_finite") is True
            and row.get("additional_state_tables_complete") is True
            and isinstance(row.get("additional_element_results"), dict)
            and isinstance(row.get("switch_states"), list)
            and int((row.get("state_counts") or {}).get("buses", 0)) > 0
            for row in records
        ),
        "all_published_power_balance_residuals_within_1e_minus_3_mw": all(
            float(row.get("power_balance_residual_mw", float("inf"))) <= 1e-3
            for row in records
        ),
        "all_failed_attempts_classified": all(
            row.get("failure_class") in {
                "unsupplied_island",
                "numerical_nonconvergence",
                "invalid_or_infeasible",
                "optimization_nonconvergence",
            }
            for row in candidate_attempts
            if row.get("solver_status") != "converged"
        ),
    }
    status = "pass" if all(hard_gates.values()) else "fail"

    output_path = ROOT / args.output_json
    attempts_path = ROOT / args.attempts_json
    report_path = ROOT / args.report_json
    markdown_path = ROOT / args.report_md
    prior_output_sha256 = None
    if output_path.is_file():
        prior_output_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()

    write_json(attempts_path, candidate_attempts)
    output_written = status == "pass"
    if output_written:
        write_json(output_path, records)
    current_output_sha256 = (
        hashlib.sha256(output_path.read_bytes()).hexdigest()
        if output_written and output_path.is_file()
        else None
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "systems": args.systems,
        "config": {
            "seed": args.seed,
            "load_levels": args.load_levels,
            "system_load_levels": system_load_levels,
            "max_lines_per_system": args.max_lines_per_system,
            "max_attempts_per_stratum": args.max_attempts_per_stratum,
            "required_envelope_counts": required_envelope_counts,
            "scenario_namespace": args.scenario_namespace,
            "scale_generation_with_load": args.scale_generation_with_load,
            "generator_voltage_factors": args.generator_voltage_factors,
            "dispatch_policy": args.dispatch_policy,
        },
        "publication_transaction": {
            "output_path": args.output_json,
            "output_written_by_this_run": output_written,
            "output_sha256": current_output_sha256,
            "prior_output_sha256": prior_output_sha256,
            "attempt_ledger_path": args.attempts_json,
            "attempt_ledger_sha256": hashlib.sha256(attempts_path.read_bytes()).hexdigest(),
        },
        "scenario_record_count": len(records),
        "converged_record_count": converged_count,
        "convergence_rate": convergence_rate,
        "solver_status_counts": dict(status_counts),
        "candidate_attempt_count": len(candidate_attempts),
        "candidate_attempt_status_counts": dict(attempt_status_counts),
        "candidate_attempt_failure_class_counts": dict(
            Counter(
                row.get("failure_class")
                for row in candidate_attempts
                if row.get("failure_class")
            )
        ),
        "envelope_counts": dict(envelope_counts),
        "stratum_coverage": strata,
        "hard_gates": hard_gates,
        "network_summaries": network_summaries,
        "scope": "external topology validation and candidate source for formal OOD instruction records; envelope quotas are global and per-network envelope counts are reported without implying every network covers every envelope",
    }
    write_json(report_path, report)
    write_markdown(report, markdown_path)
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
