#!/usr/bin/env python3
"""Enumerate every line, transformer, and non-slack generator N-1 event."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandapower as pp
from pandapower.powerflow import LoadflowNotConverged
from pandapower.topology import unsupplied_buses

from gridinstruct_utils import ROOT, ensure_dirs, write_json
from power_evidence_common import (
    CORE_N1_NETWORKS,
    PGLIB_EXPECTED_COMMIT,
    apply_rating_overrides,
    load_network,
    load_rating_overrides,
    pglib_rating_overrides_for_network,
    rating_record,
    sha256_file,
    validate_pglib_repository,
)


def contingencies(net) -> list[tuple[str, int]]:
    rows = []
    for element in ("line", "trafo", "gen"):
        table = getattr(net, element)
        rows.extend(
            (element, int(index))
            for index in table.index
            if bool(table.at[index, "in_service"])
        )
    return rows


def scale_demand(net, factor: float) -> None:
    for element in ("load", "sgen", "storage"):
        table = getattr(net, element, None)
        if table is None or not len(table):
            continue
        for field in ("p_mw", "q_mvar"):
            if field in table.columns:
                table[field] *= factor


def unknown_active_ratings(
    net,
    system: str,
    source_bound_keys: set[tuple[str, int]],
) -> list[dict[str, Any]]:
    rows = []
    for element in ("line", "trafo", "trafo3w"):
        table = getattr(net, element)
        for index in table.index:
            record = rating_record(net, system, element, int(index))
            if (
                record["in_service"]
                and record["rating_status"] != "known"
                and (element, int(index)) not in source_bound_keys
            ):
                rows.append(
                    {
                        "element": element,
                        "index": int(index),
                        "reasons": record["reasons"],
                    }
                )
    return rows


def solve_contingency(
    base_net,
    system: str,
    element: str,
    index: int,
    *,
    voltage_min: float,
    voltage_max: float,
    source_bound_ratings_mva: dict[tuple[str, int], float],
) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    net[element].at[index, "in_service"] = False
    unsupplied = sorted(int(value) for value in unsupplied_buses(net))
    common = {
        "contingency_id": f"{system}:{element}:{index}",
        "element": element,
        "index": index,
        "unsupplied_bus_count": len(unsupplied),
        "unsupplied_buses": unsupplied,
    }
    if unsupplied:
        return {
            **common,
            "classification": "unsupplied_island",
            "solver_status": "not_run",
            "counts_in_security_denominator": True,
        }
    try:
        pp.runpp(
            net,
            algorithm="nr",
            init="auto",
            calculate_voltage_angles=True,
            tolerance_mva=1e-8,
            max_iteration=100,
            enforce_q_lims=True,
            numba=False,
        )
    except LoadflowNotConverged as exc:
        return {
            **common,
            "classification": "numerical_nonconvergence",
            "solver_status": "failed",
            "error": str(exc)[:300],
            "counts_in_security_denominator": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            **common,
            "classification": "invalid_or_infeasible",
            "solver_status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
            "counts_in_security_denominator": True,
        }

    source_bound_keys = set(source_bound_ratings_mva)
    unknown = unknown_active_ratings(net, system, source_bound_keys)
    min_vm = float(net.res_bus.vm_pu.min())
    max_vm = float(net.res_bus.vm_pu.max())
    line_loading = []
    for candidate in net.line.index:
        if not bool(net.line.at[candidate, "in_service"]):
            continue
        rating = source_bound_ratings_mva.get(("line", int(candidate)))
        if rating is None:
            value = float(net.res_line.at[candidate, "loading_percent"])
        else:
            value = 100.0 * max(
                float(
                    np.hypot(
                        net.res_line.at[candidate, "p_from_mw"],
                        net.res_line.at[candidate, "q_from_mvar"],
                    )
                ),
                float(
                    np.hypot(
                        net.res_line.at[candidate, "p_to_mw"],
                        net.res_line.at[candidate, "q_to_mvar"],
                    )
                ),
            ) / rating
        line_loading.append(value)
    transformer_loading = []
    for candidate in net.trafo.index:
        if not bool(net.trafo.at[candidate, "in_service"]):
            continue
        rating = source_bound_ratings_mva.get(("trafo", int(candidate)))
        if rating is None:
            value = float(net.res_trafo.at[candidate, "loading_percent"])
        else:
            value = 100.0 * max(
                float(
                    np.hypot(
                        net.res_trafo.at[candidate, "p_hv_mw"],
                        net.res_trafo.at[candidate, "q_hv_mvar"],
                    )
                ),
                float(
                    np.hypot(
                        net.res_trafo.at[candidate, "p_lv_mw"],
                        net.res_trafo.at[candidate, "q_lv_mvar"],
                    )
                ),
            ) / rating
        transformer_loading.append(value)
    max_line = max(line_loading, default=0.0)
    max_trafo = max(transformer_loading, default=0.0)
    finite = all(
        np.isfinite(value)
        for value in (min_vm, max_vm, max_line, max_trafo)
    )
    thermal_violation = max(max_line, max_trafo) > 100.0
    voltage_violation = min_vm < voltage_min or max_vm > voltage_max
    if unknown:
        classification = "converged_rating_indeterminate"
    elif not finite:
        classification = "converged_nonfinite"
    elif thermal_violation or voltage_violation:
        classification = "converged_limit_violation"
    else:
        classification = "converged_within_registered_limits"
    return {
        **common,
        "classification": classification,
        "solver_status": "converged",
        "unknown_active_rating_count": len(unknown),
        "unknown_active_rating_examples": unknown[:20],
        "max_line_loading_percent": max_line,
        "max_transformer_loading_percent": max_trafo,
        "min_bus_voltage_pu": min_vm,
        "max_bus_voltage_pu": max_vm,
        "thermal_violation": thermal_violation,
        "voltage_violation": voltage_violation,
        "counts_in_security_denominator": True,
    }


def audit_network(
    system: str,
    load_level: float,
    overrides: list[dict[str, Any]],
    *,
    voltage_min: float,
    voltage_max: float,
    pglib_root: Path | None = None,
    pglib_repository: dict[str, Any] | None = None,
) -> dict[str, Any]:
    net = load_network(system)
    scale_demand(net, load_level)
    effective_overrides = list(overrides)
    if pglib_root and pglib_repository:
        effective_overrides.extend(
            pglib_rating_overrides_for_network(
                net,
                system,
                pglib_root,
                pglib_repository,
            )
        )
    applied = apply_rating_overrides(net, system, effective_overrides)
    source_bound_ratings_mva = {
        (str(row["element"]), int(row["index"])): float(row["rating_value"])
        for row in effective_overrides
        if str(row.get("network", "")).lower() == system
        and row.get("element") in {"line", "trafo"}
    }
    candidate_rows = contingencies(net)
    results = [
        solve_contingency(
            net,
            system,
            element,
            index,
            voltage_min=voltage_min,
            voltage_max=voltage_max,
            source_bound_ratings_mva=source_bound_ratings_mva,
        )
        for element, index in candidate_rows
    ]
    class_counts = Counter(row["classification"] for row in results)
    element_denominators = Counter(element for element, _ in candidate_rows)
    element_results = Counter(
        (row["element"], row["classification"])
        for row in results
    )
    hard_gates = {
        "full_enumeration": len(results) == len(candidate_rows),
        "all_events_classified": len(results) == sum(class_counts.values()),
        "no_unknown_rating": class_counts.get("converged_rating_indeterminate", 0) == 0,
        "no_nonfinite_solution": class_counts.get("converged_nonfinite", 0) == 0,
        "denominator_preserves_islands_and_solver_outcomes": all(
            row.get("counts_in_security_denominator") is True for row in results
        ),
    }
    security_outcomes = {
        "unsupplied_island_count": class_counts.get("unsupplied_island", 0),
        "numerical_nonconvergence_count": class_counts.get(
            "numerical_nonconvergence", 0
        ),
        "invalid_or_infeasible_count": class_counts.get(
            "invalid_or_infeasible", 0
        ),
        "registered_limit_violation_count": class_counts.get(
            "converged_limit_violation", 0
        ),
        "all_converged_cases_within_registered_limits": (
            class_counts.get("converged_limit_violation", 0) == 0
        ),
    }
    return {
        "network": system,
        "load_level": load_level,
        "candidate_denominator": len(candidate_rows),
        "denominator_by_element": dict(element_denominators),
        "classification_counts": dict(class_counts),
        "classification_by_element": {
            element: {
                classification: element_results.get((element, classification), 0)
                for classification in sorted(class_counts)
            }
            for element in ("line", "trafo", "gen")
        },
        "source_bound_rating_overrides_applied": len(applied),
        "hard_gates": hard_gates,
        "security_outcomes": security_outcomes,
        "status": "pass" if all(hard_gates.values()) else "fail",
        "contingencies": results,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Full N-1 Enumeration Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Status: `{report['status']}`",
        f"- Enumerated events: {report['summary']['candidate_denominator']}",
        "",
        "Every in-service line, two-winding transformer, and non-slack generator",
        "is included in the denominator. Islanding, unsupplied load, non-convergence,",
        "and unknown ratings remain in the denominator and cannot be reported as safe.",
        "",
        "| Network | Load | Line | Transformer | Generator | Total | Within limits | Rating indeterminate | Gate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["networks"]:
        denominator = row["denominator_by_element"]
        counts = row["classification_counts"]
        lines.append(
            f"| {row['network']} | {row['load_level']:.3f} | "
            f"{denominator.get('line', 0)} | {denominator.get('trafo', 0)} | "
            f"{denominator.get('gen', 0)} | {row['candidate_denominator']} | "
            f"{counts.get('converged_within_registered_limits', 0)} | "
            f"{counts.get('converged_rating_indeterminate', 0)} | "
            f"{row['status'].upper()} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", nargs="+", default=list(CORE_N1_NETWORKS))
    parser.add_argument("--load-levels", nargs="+", type=float, default=[1.0])
    parser.add_argument("--rating-overrides", type=Path)
    parser.add_argument("--pglib-root", type=Path)
    parser.add_argument("--pglib-commit", default=PGLIB_EXPECTED_COMMIT)
    parser.add_argument("--voltage-min", type=float, default=0.95)
    parser.add_argument("--voltage-max", type=float, default=1.05)
    parser.add_argument(
        "--output-json",
        default="reports/full_n1_enumeration_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--output-md",
        default="reports/full_n1_enumeration_v1.2_sd_core.md",
    )
    args = parser.parse_args()
    if set(args.systems) - set(CORE_N1_NETWORKS):
        raise ValueError("formal full N-1 scope is preregistered to IEEE14 and IEEE118")
    overrides = load_rating_overrides(args.rating_overrides)
    pglib_repository = (
        validate_pglib_repository(args.pglib_root, args.pglib_commit)
        if args.pglib_root
        else None
    )
    rows = [
        audit_network(
            system,
            load_level,
            overrides,
            voltage_min=args.voltage_min,
            voltage_max=args.voltage_max,
            pglib_root=args.pglib_root,
            pglib_repository=pglib_repository,
        )
        for system in args.systems
        for load_level in args.load_levels
    ]
    report = {
        "contract_version": "gridinstruct-full-n1-enumeration-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(row["status"] == "pass" for row in rows) else "fail",
        "scope": {
            "networks": args.systems,
            "load_levels": args.load_levels,
            "elements": ["line", "trafo", "gen"],
            "ext_grid": "excluded because it is the slack/source reference, not a non-slack generator",
            "voltage_bounds_pu": [args.voltage_min, args.voltage_max],
        },
        "rating_override": (
            {"path": str(args.rating_overrides), "sha256": sha256_file(args.rating_overrides)}
            if args.rating_overrides
            else None
        ),
        "pglib_repository": pglib_repository,
        "summary": {
            "candidate_denominator": sum(row["candidate_denominator"] for row in rows),
            "passed_network_load_strata": sum(row["status"] == "pass" for row in rows),
            "total_network_load_strata": len(rows),
        },
        "networks": rows,
    }
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.output_json, report)
    write_markdown(report, ROOT / args.output_md)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
