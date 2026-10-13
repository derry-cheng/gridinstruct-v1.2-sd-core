#!/usr/bin/env python3
"""Cross-check released and OPF post-action states with standalone PYPOWER."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandapower as pp
from pandapower.converter.pypower import to_ppc
from pypower.api import ppoption, runpf
from pypower.idx_brch import BR_STATUS, F_BUS, PF, PT, QF, QT, RATE_A, T_BUS
from pypower.idx_bus import BUS_I, BUS_TYPE, NONE, PD, QD, VM
from pypower.idx_gen import GEN_BUS, GEN_STATUS, PG, VG

from append_opf_closed_loop_auxiliary_records import (
    THERMAL_BOUNDARY_EPSILON_PERCENT,
    VOLTAGE_BOUNDARY_EPSILON_PU,
    VOLTAGE_MAX_PU,
    VOLTAGE_MIN_PU,
)
from gridinstruct_utils import ROOT, read_json, write_json
from power_evidence_common import load_pglib_case
from validate_query_truth_independent import build_network


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pglib_pypower_case(
    pglib_root: Path,
    system: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    case = load_pglib_case(pglib_root, system)
    expected_name = str(source.get("case_file") or "")
    expected_sha256 = str(source.get("case_sha256") or "")
    if (
        case["path"].name != expected_name
        or case["sha256"] != expected_sha256
    ):
        raise ValueError(f"{system} PGLib source binding does not match the scenario")
    text = case["path"].read_text(encoding="utf-8")
    match = re.search(r"mpc\.baseMVA\s*=\s*([0-9.eE+-]+)\s*;", text)
    if not match:
        raise ValueError(f"{system} PGLib case lacks baseMVA")
    return {
        "version": "2",
        "baseMVA": float(match.group(1)),
        "bus": np.asarray(case["bus"], dtype=float),
        "gen": np.asarray(case["gen"], dtype=float),
        "branch": np.asarray(case["branch"], dtype=float),
    }


def apply_post_controls(net, result: dict[str, Any]) -> None:
    controls = result.get("post_action_controls") or {}
    for row in controls.get("gen") or []:
        idx = int(row["index"])
        if idx in net.gen.index:
            net.gen.at[idx, "p_mw"] = float(row["p_mw"])
            net.gen.at[idx, "vm_pu"] = float(row["vm_pu"])
    for row in controls.get("ext_grid") or []:
        idx = int(row["index"])
        if idx in net.ext_grid.index:
            net.ext_grid.at[idx, "vm_pu"] = float(row["vm_pu"])
    pp.runpp(
        net,
        algorithm="nr",
        init="auto",
        tolerance_mva=1e-8,
        max_iteration=50,
        enforce_q_lims=True,
        numba=False,
    )


def recomputed_post_metrics(net) -> dict[str, float | int]:
    max_line = float(net.res_line.loading_percent.max()) if len(net.res_line) else 0.0
    max_trafo = float(net.res_trafo.loading_percent.max()) if len(net.res_trafo) else 0.0
    generation = sum(
        float(getattr(net, table).p_mw.sum())
        for table in ("res_ext_grid", "res_gen", "res_sgen")
        if hasattr(net, table) and len(getattr(net, table)) and "p_mw" in getattr(net, table)
    )
    demand = sum(
        float(getattr(net, table).p_mw.sum())
        for table in ("res_load", "res_shunt", "res_ward", "res_xward", "res_storage")
        if hasattr(net, table) and len(getattr(net, table)) and "p_mw" in getattr(net, table)
    )
    losses = sum(
        float(getattr(net, table).pl_mw.sum())
        for table in ("res_line", "res_trafo", "res_trafo3w", "res_impedance")
        if hasattr(net, table) and len(getattr(net, table)) and "pl_mw" in getattr(net, table)
    )
    min_vm = float(net.res_bus.vm_pu.min())
    max_vm = float(net.res_bus.vm_pu.max())
    score = max(
        0.0,
        max(max_line, max_trafo) - 100.0 - THERMAL_BOUNDARY_EPSILON_PERCENT,
    ) / 100.0 + 10.0 * (
        max(0.0, VOLTAGE_MIN_PU - min_vm - VOLTAGE_BOUNDARY_EPSILON_PU)
        + max(0.0, max_vm - VOLTAGE_MAX_PU - VOLTAGE_BOUNDARY_EPSILON_PU)
    )
    return {
        "max_branch_loading_percent": max(max_line, max_trafo),
        "max_line_loading_percent": max_line,
        "max_transformer_loading_percent": max_trafo,
        "min_bus_voltage_pu": min_vm,
        "max_bus_voltage_pu": max_vm,
        "overloaded_branch_count": int(
            (net.res_line.loading_percent > 100.0 + THERMAL_BOUNDARY_EPSILON_PERCENT).sum()
        ) if len(net.res_line) else 0,
        "overloaded_transformer_count": int(
            (net.res_trafo.loading_percent > 100.0 + THERMAL_BOUNDARY_EPSILON_PERCENT).sum()
        ) if len(net.res_trafo) else 0,
        "voltage_violation_count": int(
            (
                (net.res_bus.vm_pu < VOLTAGE_MIN_PU - VOLTAGE_BOUNDARY_EPSILON_PU)
                | (net.res_bus.vm_pu > VOLTAGE_MAX_PU + VOLTAGE_BOUNDARY_EPSILON_PU)
            ).sum()
        ),
        "constraint_violation_score": score,
        "generation_mw": generation,
        "demand_mw": demand,
        "network_losses_mw": losses,
        "power_balance_residual_mw": abs(generation - demand - losses),
    }


def compare_stored_post_action(net, result: dict[str, Any]) -> dict[str, Any]:
    stored = result.get("post_action") or {}
    recomputed = recomputed_post_metrics(net)
    numeric_errors = {
        key: abs(float(stored[key]) - float(value))
        for key, value in recomputed.items()
        if key in stored and isinstance(value, float)
    }
    missing_keys = sorted(set(recomputed) - set(stored))
    count_mismatches = {
        key: {"stored": stored.get(key), "recomputed": value}
        for key, value in recomputed.items()
        if isinstance(value, int) and stored.get(key) != value
    }
    return {
        "stored_post_action": stored,
        "recomputed_post_action": recomputed,
        "numeric_absolute_errors": numeric_errors,
        "missing_keys": missing_keys,
        "count_mismatches": count_mismatches,
    }


def compare_stored_state(net, result: dict[str, Any]) -> dict[str, Any]:
    stored = result.get("post_action_state") or {}
    specs = {
        "bus": (net.res_bus, ("vm_pu", "va_degree")),
        "line": (net.res_line, ("p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar", "loading_percent")),
        "trafo": (net.res_trafo, ("p_hv_mw", "q_hv_mvar", "p_lv_mw", "q_lv_mvar", "loading_percent")),
    }
    missing_rows: dict[str, list[int]] = {}
    unexpected_rows: dict[str, list[int]] = {}
    max_errors: dict[str, float] = {}
    compared_values = 0
    for element, (table, fields) in specs.items():
        rows = stored.get(element) or []
        lookup = {int(row["index"]): row for row in rows if isinstance(row, dict) and "index" in row}
        expected_indices = {int(idx) for idx in table.index}
        missing_rows[element] = sorted(expected_indices - set(lookup))
        unexpected_rows[element] = sorted(set(lookup) - expected_indices)
        for idx in sorted(expected_indices & set(lookup)):
            for field in fields:
                stored_value = lookup[idx].get(field)
                recomputed_value = table.at[idx, field]
                if stored_value is None and not np.isfinite(float(recomputed_value)):
                    continue
                key = f"{element}.{field}"
                if stored_value is None or not np.isfinite(float(recomputed_value)):
                    max_errors[key] = float("inf")
                    continue
                error = abs(float(stored_value) - float(recomputed_value))
                max_errors[key] = max(max_errors.get(key, 0.0), error)
                compared_values += 1
    return {
        "missing_rows": {key: value for key, value in missing_rows.items() if value},
        "unexpected_rows": {key: value for key, value in unexpected_rows.items() if value},
        "max_absolute_errors": max_errors,
        "compared_values": compared_values,
    }


def validate_identity_controls_and_reserve(
    net,
    scenario: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    errors = []
    expected_system = str(scenario.get("system") or scenario.get("network_model") or "").lower().replace(" ", "")
    if str(result.get("system") or "").lower().replace(" ", "") != expected_system:
        errors.append("result_system_mismatch")
    if str(result.get("network_model") or "").lower().replace(" ", "") != str(
        scenario.get("network_model") or ""
    ).lower().replace(" ", ""):
        errors.append("result_network_model_mismatch")
    controls = result.get("post_action_controls") or {}
    control_lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for element in ("ext_grid", "gen"):
        rows = controls.get(element) or []
        indices = [int(row["index"]) for row in rows if isinstance(row, dict) and "index" in row]
        expected = {int(idx) for idx in getattr(net, element).index}
        if len(indices) != len(set(indices)):
            errors.append(f"duplicate_{element}_controls")
        if set(indices) != expected:
            errors.append(f"incomplete_{element}_controls")
        for row in rows:
            key = (element, int(row["index"]))
            control_lookup[key] = row
            if any(not np.isfinite(float(row.get(field))) for field in ("p_mw", "q_mvar", "vm_pu")):
                errors.append(f"nonfinite_{element}_control:{row.get('index')}")
    upward = 0.0
    downward = 0.0
    headroom: dict[tuple[str, int], float] = {}
    for bound in (result.get("constraint_policy") or {}).get("control_bounds") or []:
        key = (str(bound["element"]), int(bound["index"]))
        if key not in control_lookup:
            errors.append(f"missing_control_bound_target:{key[0]}:{key[1]}")
            continue
        active_power = float(control_lookup[key]["p_mw"])
        normal_lower, normal_upper = [float(value) for value in bound["p_bounds_mw"]]
        if not normal_lower - 1e-5 <= active_power <= normal_upper + 1e-5:
            errors.append(f"active_power_outside_bound:{key[0]}:{key[1]}")
        lower, upper = [float(value) for value in bound["contingency_p_bounds_mw"]]
        item_upward = max(0.0, upper - active_power)
        upward += item_upward
        downward += max(0.0, active_power - lower)
        headroom[key] = item_upward
    largest_generator = max(
        (abs(float(row["p_mw"])) for row in controls.get("gen") or []),
        default=0.0,
    )
    stored_reserve = result.get("reserve_summary") or {}
    expected_outage_checks = []
    for row in controls.get("gen") or []:
        idx = int(row["index"])
        output = max(0.0, float(row["p_mw"]))
        loss_buffer = max(0.5, 0.02 * output)
        required = output + loss_buffer
        available = sum(value for key, value in headroom.items() if key != ("gen", idx))
        expected_outage_checks.append(
            {
                "generator_index": idx,
                "outaged_generator_output_mw": round(output, 6),
                "loss_buffer_mw": round(loss_buffer, 6),
                "required_replacement_mw": round(required, 6),
                "available_upward_reserve_excluding_outaged_generator_mw": round(available, 6),
                "reserve_margin_mw": round(available - required, 6),
                "adequate": available + 1e-5 >= required,
            }
        )
    reserve_errors = {
        "available_upward_reserve_mw": abs(upward - float(stored_reserve.get("available_upward_reserve_mw", float("nan")))),
        "available_downward_reserve_mw": abs(downward - float(stored_reserve.get("available_downward_reserve_mw", float("nan")))),
        "largest_non_slack_generator_mw": abs(largest_generator - float(stored_reserve.get("largest_non_slack_generator_mw", float("nan")))),
    }
    if any(not np.isfinite(value) or value > 1e-4 for value in reserve_errors.values()):
        errors.append("stored_reserve_summary_mismatch")
    if stored_reserve.get("generator_outage_reserve_checks") != expected_outage_checks:
        errors.append("stored_generator_outage_reserve_checks_mismatch")
    if not all(row["adequate"] for row in expected_outage_checks) or stored_reserve.get(
        "upward_reserve_adequate"
    ) is not True:
        errors.append("upward_reserve_inadequate")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "recomputed_available_upward_reserve_mw": upward,
        "recomputed_available_downward_reserve_mw": downward,
        "recomputed_largest_non_slack_generator_mw": largest_generator,
        "recomputed_generator_outage_reserve_checks": expected_outage_checks,
        "stored_reserve_absolute_errors": reserve_errors,
    }
def compare(net) -> dict[str, Any]:
    ppc = to_ppc(net, calculate_voltage_angles=True, init="flat")
    solved, success = runpf(ppc, ppoption(VERBOSE=0, OUT_ALL=0, PF_TOL=1e-8, PF_MAX_IT=100))
    initialization = "flat"
    if not success:
        ppc = to_ppc(net, calculate_voltage_angles=True, init="results")
        solved, success = runpf(ppc, ppoption(VERBOSE=0, OUT_ALL=0, PF_TOL=1e-8, PF_MAX_IT=50))
        initialization = "pandapower_results_fallback"
    if not success:
        raise RuntimeError("standalone PYPOWER did not converge")

    bus_lookup = net._pd2ppc_lookups.get("bus")
    if bus_lookup is None:
        raise ValueError("pandapower-to-PYPOWER bus lookup is unavailable")
    pp_vm_by_bus = {
        int(idx): float(solved["bus"][int(bus_lookup[int(idx)]), VM])
        for idx in net.bus.index
        if int(bus_lookup[int(idx)]) >= 0 and int(bus_lookup[int(idx)]) < len(solved["bus"])
    }
    active_bus_indices = [
        int(idx)
        for idx in net.bus.index
        if bool(net.bus.at[idx, "in_service"])
        and int(idx) in pp_vm_by_bus
        and np.isfinite(float(net.res_bus.at[idx, "vm_pu"]))
    ]
    if not active_bus_indices:
        raise ValueError("no active buses available for shared-case voltage comparison")
    vm_errors = [
        abs(float(net.res_bus.at[idx, "vm_pu"]) - pp_vm_by_bus[idx])
        for idx in active_bus_indices
    ]

    branch = solved["branch"]
    branch_lookup = net._pd2ppc_lookups.get("branch", {})
    branch_is = np.asarray((net._ppc.get("internal") or {}).get("branch_is"), dtype=bool)
    flow_errors: dict[str, list[float]] = {
        "line_p_from_mw": [],
        "line_q_from_mvar": [],
        "line_p_to_mw": [],
        "line_q_to_mvar": [],
        "trafo_p_hv_mw": [],
        "trafo_q_hv_mvar": [],
        "trafo_p_lv_mw": [],
        "trafo_q_lv_mvar": [],
    }
    for element, result_table, fields in (
        ("line", net.res_line, (("p_from_mw", 13), ("q_from_mvar", 14), ("p_to_mw", 15), ("q_to_mvar", 16))),
        ("trafo", net.res_trafo, (("p_hv_mw", 13), ("q_hv_mvar", 14), ("p_lv_mw", 15), ("q_lv_mvar", 16))),
    ):
        start, end = branch_lookup.get(element, (0, 0))
        if end - start != len(result_table):
            raise ValueError(f"unexpected {element} branch lookup size: {(start, end)} vs {len(result_table)}")
        for offset, idx in enumerate(result_table.index):
            raw_position = start + offset
            if raw_position >= len(branch_is) or not bool(branch_is[raw_position]):
                continue
            compressed_position = int(branch_is[:raw_position].sum())
            if compressed_position >= len(branch):
                raise IndexError(
                    f"compressed {element} position {compressed_position} outside {len(branch)} PYPOWER branches"
                )
            branch_row = branch[compressed_position]
            for field, column in fields:
                value = float(result_table.at[idx, field])
                if np.isfinite(value):
                    flow_errors[f"{element}_{field}"].append(abs(value - float(branch_row[column])))
    max_flow_errors = {key: max(values, default=0.0) for key, values in flow_errors.items()}
    return {
        "pypower_success": True,
        "pypower_initialization": initialization,
        "bus_count": len(net.bus),
        "line_count": len(net.line),
        "transformer_count": len(net.trafo),
        "max_abs_bus_voltage_error_pu": max(vm_errors, default=0.0),
        "mean_abs_bus_voltage_error_pu": float(np.mean(vm_errors)) if vm_errors else 0.0,
        "max_abs_flow_errors": max_flow_errors,
        "thermal_loading_comparison": "not compared because pandapower uses equipment-current loading while MATPOWER RATE_A is apparent-power based",
    }


def replay_registered_n1(
    dispatch_net,
    result: dict[str, Any],
    *,
    voltage_tolerance: float,
    active_power_tolerance_mw: float,
    reactive_power_tolerance_mvar: float,
    loading_tolerance_percent: float,
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    security = result.get("post_action_n1") or {}
    stored_checks = security.get("checks") or []
    rows = []
    errors = []
    active_keys = ("line_p_from_mw", "line_p_to_mw", "trafo_p_hv_mw", "trafo_p_lv_mw")
    reactive_keys = ("line_q_from_mvar", "line_q_to_mvar", "trafo_q_hv_mvar", "trafo_q_lv_mvar")
    numeric_tolerances = {
        "max_branch_loading_percent": loading_tolerance_percent,
        "max_line_loading_percent": loading_tolerance_percent,
        "max_transformer_loading_percent": loading_tolerance_percent,
        "min_bus_voltage_pu": voltage_tolerance,
        "max_bus_voltage_pu": voltage_tolerance,
        "constraint_violation_score": 10.0 * voltage_tolerance,
        "generation_mw": active_power_tolerance_mw,
        "demand_mw": active_power_tolerance_mw,
        "network_losses_mw": active_power_tolerance_mw,
        "power_balance_residual_mw": balance_tolerance_mw,
    }
    for stored in stored_checks:
        element = str(stored.get("element") or "")
        index = int(stored.get("index", -1))
        key = f"{element}:{index}"
        try:
            contingency_net = copy.deepcopy(dispatch_net)
            if element not in {"line", "trafo", "gen"} or index not in getattr(contingency_net, element).index:
                raise ValueError("registered contingency element is unavailable")
            contingency_net[element].at[index, "in_service"] = False
            for bound in (result.get("constraint_policy") or {}).get("control_bounds") or []:
                control_element = str(bound["element"])
                control_index = int(bound["index"])
                table = getattr(contingency_net, control_element)
                if control_index not in table.index:
                    continue
                table.at[control_index, "min_p_mw"], table.at[control_index, "max_p_mw"] = [
                    float(value) for value in bound["contingency_p_bounds_mw"]
                ]
                table.at[control_index, "min_q_mvar"], table.at[control_index, "max_q_mvar"] = [
                    float(value) for value in bound["contingency_q_bounds_mvar"]
                ]
            contingency_controls = (stored.get("control_feasibility") or {}).get(
                "post_contingency_controls"
            ) or {}
            for row in contingency_controls.get("gen") or []:
                idx = int(row["index"])
                if idx in contingency_net.gen.index and bool(contingency_net.gen.at[idx, "in_service"]):
                    contingency_net.gen.at[idx, "p_mw"] = float(row["p_mw"])
                    contingency_net.gen.at[idx, "vm_pu"] = float(row["vm_pu"])
            for row in contingency_controls.get("ext_grid") or []:
                idx = int(row["index"])
                if idx in contingency_net.ext_grid.index:
                    contingency_net.ext_grid.at[idx, "vm_pu"] = float(row["vm_pu"])
            pp.runpp(
                contingency_net,
                algorithm="nr",
                init="auto",
                tolerance_mva=1e-8,
                max_iteration=50,
                enforce_q_lims=True,
                numba=False,
            )
            recomputed = recomputed_post_metrics(contingency_net)
            stored_metrics = stored.get("metrics") or {}
            numeric_errors = {
                metric: abs(float(stored_metrics.get(metric, float("nan"))) - float(value))
                for metric, value in recomputed.items()
                if isinstance(value, float)
            }
            count_mismatches = {
                metric: {"stored": stored_metrics.get(metric), "recomputed": value}
                for metric, value in recomputed.items()
                if isinstance(value, int) and stored_metrics.get(metric) != value
            }
            cross = compare(contingency_net)
            cross_pass = (
                float(cross["max_abs_bus_voltage_error_pu"]) <= voltage_tolerance
                and all(float(cross["max_abs_flow_errors"][name]) <= active_power_tolerance_mw for name in active_keys)
                and all(float(cross["max_abs_flow_errors"][name]) <= reactive_power_tolerance_mvar for name in reactive_keys)
            )
            metrics_pass = not count_mismatches and all(
                np.isfinite(value) and value <= numeric_tolerances[metric]
                for metric, value in numeric_errors.items()
            ) and set(numeric_tolerances).issubset(numeric_errors)
            secure = (
                recomputed["constraint_violation_score"] <= 1e-8
                and recomputed["power_balance_residual_mw"] <= balance_tolerance_mw
            )
            passed = bool(
                stored.get("solver_status") == "converged"
                and stored.get("secure") is True
                and metrics_pass
                and secure
                and cross_pass
            )
            rows.append(
                {
                    "contingency": key,
                    "status": "pass" if passed else "fail",
                    "stored_metric_absolute_errors": numeric_errors,
                    "count_mismatches": count_mismatches,
                    "standalone_solver": cross,
                    "recomputed_secure": secure,
                }
            )
            if not passed:
                errors.append(f"registered_n1_replay_failed:{key}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"registered_n1_replay_error:{key}:{type(exc).__name__}:{str(exc)[:200]}")
    requested = int(security.get("requested_checks") or 0)
    if requested <= 0 or len(stored_checks) != requested or len(rows) != requested:
        errors.append("registered_n1_count_mismatch")
    return {
        "status": "pass" if not errors else "fail",
        "requested_checks": requested,
        "replayed_checks": len(rows),
        "passed_checks": sum(row["status"] == "pass" for row in rows),
        "errors": errors,
        "checks": rows,
    }


def native_bus_id(net, bus_idx: int) -> int:
    return int(net.bus.at[bus_idx, "name"])


def native_element_maps(net, ppc: dict[str, Any]) -> tuple[dict[tuple[str, int], int], dict[tuple[str, int], int]]:
    branch_pools: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, row in enumerate(ppc["branch"]):
        branch_pools[tuple(sorted((int(row[F_BUS]), int(row[T_BUS]))))].append(idx)
    branch_map: dict[tuple[str, int], int] = {}
    for element, table, from_col, to_col in (
        ("line", net.line, "from_bus", "to_bus"),
        ("trafo", net.trafo, "hv_bus", "lv_bus"),
        ("impedance", net.impedance, "from_bus", "to_bus"),
    ):
        for idx, row in table.iterrows():
            key = tuple(
                sorted(
                    (
                        native_bus_id(net, int(row[from_col])),
                        native_bus_id(net, int(row[to_col])),
                    )
                )
            )
            if not branch_pools[key]:
                raise ValueError(f"native case missing {element} endpoints {key}")
            branch_map[(element, int(idx))] = branch_pools[key].pop(0)
    if any(indices for indices in branch_pools.values()):
        raise ValueError("native case contains unmapped branches")

    generator_pools: dict[int, list[int]] = defaultdict(list)
    for idx, row in enumerate(ppc["gen"]):
        generator_pools[int(row[GEN_BUS])].append(idx)
    generator_map: dict[tuple[str, int], int] = {}
    for element, table in (("ext_grid", net.ext_grid), ("gen", net.gen)):
        for idx, row in table.iterrows():
            bus = native_bus_id(net, int(row["bus"]))
            if not generator_pools[bus]:
                raise ValueError(f"native case missing {element} at bus {bus}")
            generator_map[(element, int(idx))] = generator_pools[bus].pop(0)
    if any(indices for indices in generator_pools.values()):
        raise ValueError("native case contains unmapped generators")
    return branch_map, generator_map


def prepare_native_case(
    scenario: dict[str, Any],
    net,
    result: dict[str, Any],
    *,
    pglib_root: Path,
) -> tuple[dict[str, Any], dict[tuple[str, int], int]]:
    system = str(scenario.get("system") or scenario.get("network_model") or "").lower().replace(" ", "")
    source = scenario.get("physical_source") or {}
    if not source.get("case_file") or not source.get("case_sha256"):
        raise ValueError(f"{system} lacks a hash-bound PGLib source case")
    ppc = pglib_pypower_case(pglib_root, system, source)
    branch_map, generator_map = native_element_maps(net, ppc)
    factor = float(scenario.get("load_level") or 1.0)
    ppc["bus"][:, PD] *= factor
    ppc["bus"][:, QD] *= factor
    generation_factor = float(
        scenario.get("base_generation_scaling_factor") or 1.0
    ) * float(scenario.get("generator_factor") or 1.0)
    if not math.isclose(generation_factor, 1.0, rel_tol=0.0, abs_tol=1e-12):
        for idx in net.gen.index:
            ppc["gen"][generator_map[("gen", int(idx))], PG] *= generation_factor

    contingency = str(scenario.get("contingency_type") or "")
    if contingency in {"n1_branch_outage", "n2_branch_outage"}:
        indices = scenario.get("line_indices") or []
        for idx in indices:
            ppc["branch"][branch_map[("line", int(idx))], BR_STATUS] = 0
    elif contingency == "generator_perturbation":
        pass
    elif contingency == "n1_transformer_outage":
        idx = int(scenario["transformer_index"])
        ppc["branch"][branch_map[("trafo", idx)], BR_STATUS] = 0
    elif contingency == "n1_generator_outage":
        idx = int(scenario["generator_index"])
        ppc["gen"][generator_map[("gen", idx)], GEN_STATUS] = 0
    elif contingency == "n1_bus_outage":
        bus = native_bus_id(net, int(scenario["bus_index"]))
        bus_row = np.where(ppc["bus"][:, BUS_I].astype(int) == bus)[0]
        if len(bus_row) != 1:
            raise ValueError(f"native bus mapping is not unique: {bus}")
        ppc["bus"][bus_row[0], BUS_TYPE] = NONE
        ppc["bus"][bus_row[0], [PD, QD]] = 0.0
        connected = (ppc["branch"][:, F_BUS].astype(int) == bus) | (ppc["branch"][:, T_BUS].astype(int) == bus)
        ppc["branch"][connected, BR_STATUS] = 0
        ppc["gen"][ppc["gen"][:, GEN_BUS].astype(int) == bus, GEN_STATUS] = 0

    controls = result.get("post_action_controls") or {}
    for element in ("ext_grid", "gen"):
        for row in controls.get(element) or []:
            native_idx = generator_map[(element, int(row["index"]))]
            ppc["gen"][native_idx, VG] = float(row["vm_pu"])
            if element == "gen":
                ppc["gen"][native_idx, PG] = float(row["p_mw"])
    return ppc, branch_map


def compare_native_case(
    net,
    scenario: dict[str, Any],
    result: dict[str, Any],
    *,
    pglib_root: Path,
) -> dict[str, Any]:
    ppc, branch_map = prepare_native_case(
        scenario,
        net,
        result,
        pglib_root=pglib_root,
    )
    solved, success = runpf(ppc, ppoption(VERBOSE=0, OUT_ALL=0, PF_TOL=1e-8, PF_MAX_IT=50))
    if not success:
        raise RuntimeError("native-case PYPOWER did not converge")

    native_vm = {int(row[BUS_I]): float(row[VM]) for row in solved["bus"] if int(row[BUS_TYPE]) != NONE}
    active_buses = [
        int(idx)
        for idx in net.bus.index
        if bool(net.bus.at[idx, "in_service"]) and np.isfinite(float(net.res_bus.at[idx, "vm_pu"]))
    ]
    vm_errors = [
        abs(float(net.res_bus.at[idx, "vm_pu"]) - native_vm[native_bus_id(net, idx)])
        for idx in active_buses
    ]
    voltage_epsilon = 1e-6
    pp_voltage_flags = {
        native_bus_id(net, idx)
        for idx in active_buses
        if float(net.res_bus.at[idx, "vm_pu"]) < 0.95 - voltage_epsilon
        or float(net.res_bus.at[idx, "vm_pu"]) > 1.05 + voltage_epsilon
    }
    native_voltage_flags = {
        bus
        for bus, vm in native_vm.items()
        if vm < 0.95 - voltage_epsilon or vm > 1.05 + voltage_epsilon
    }

    flow_errors: dict[str, list[float]] = {
        "line_p_from_mw": [],
        "line_q_from_mvar": [],
        "line_p_to_mw": [],
        "line_q_to_mvar": [],
        "trafo_p_hv_mw": [],
        "trafo_q_hv_mvar": [],
        "trafo_p_lv_mw": [],
        "trafo_q_lv_mvar": [],
    }
    thermal_label_flips = 0
    thermal_elements_compared = 0
    for element, table, from_col, result_table, fields in (
        ("line", net.line, "from_bus", net.res_line, (("p_from_mw", PF), ("q_from_mvar", QF), ("p_to_mw", PT), ("q_to_mvar", QT))),
        ("trafo", net.trafo, "hv_bus", net.res_trafo, (("p_hv_mw", PF), ("q_hv_mvar", QF), ("p_lv_mw", PT), ("q_lv_mvar", QT))),
    ):
        for idx, element_row in table.iterrows():
            if not bool(element_row["in_service"]):
                continue
            branch_row = solved["branch"][branch_map[(element, int(idx))]]
            forward = int(branch_row[F_BUS]) == native_bus_id(net, int(element_row[from_col]))
            for field, column in fields:
                native_column = column
                if not forward:
                    native_column = {PF: PT, QF: QT, PT: PF, QT: QF}[column]
                value = float(result_table.at[idx, field])
                if np.isfinite(value):
                    flow_errors[f"{element}_{field}"].append(abs(value - float(branch_row[native_column])))
            rate = float(branch_row[RATE_A])
            loading = float(result_table.at[idx, "loading_percent"])
            if rate > 0 and np.isfinite(loading):
                native_loading = 100.0 * max(
                    float(np.hypot(branch_row[PF], branch_row[QF])),
                    float(np.hypot(branch_row[PT], branch_row[QT])),
                ) / rate
                thermal_label_flips += int((loading > 100.0) != (native_loading > 100.0))
                thermal_elements_compared += 1

    generation = float(solved["gen"][solved["gen"][:, GEN_STATUS] > 0, PG].sum())
    demand = float(solved["bus"][:, PD].sum())
    losses = float((solved["branch"][:, PF] + solved["branch"][:, PT]).sum())
    system = str(scenario.get("system") or "").lower()
    contingency = str(scenario.get("contingency_type") or "")
    return {
        "source_case": str(
            pglib_root
            / str((scenario.get("physical_source") or {}).get("case_file"))
        ),
        "pypower_success": True,
        "strict_gate_eligible": system in {"ieee14", "ieee118"}
        and contingency != "n1_bus_outage",
        "strict_gate_scope": (
            "hash-bound PGLib source-case parity is hard-gated for IEEE14 and "
            "IEEE118 except bus-removal semantics"
        ),
        "max_abs_bus_voltage_error_pu": max(vm_errors, default=0.0),
        "mean_abs_bus_voltage_error_pu": float(np.mean(vm_errors)) if vm_errors else 0.0,
        "max_abs_flow_errors": {key: max(values, default=0.0) for key, values in flow_errors.items()},
        "power_balance_residual_mw": abs(generation - demand - losses),
        "voltage_violation_label_flips": len(pp_voltage_flags.symmetric_difference(native_voltage_flags)),
        "thermal_violation_label_flips": thermal_label_flips,
        "thermal_elements_compared": thermal_elements_compared,
        "thermal_label_scope": "diagnostic only: native RATE_A apparent-power loading and pandapower equipment-current loading are not definitionally identical",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--additional-scenarios", nargs="*", default=[])
    parser.add_argument("--opf-results", default="simulation_outputs/opf_closed_loop/auxiliary_opf_results.json")
    parser.add_argument(
        "--pglib-root",
        default="third_party/pglib-opf-v23.07",
    )
    parser.add_argument(
        "--opf-commit",
        default="simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
    )
    parser.add_argument("--output-json", default="reports/cross_solver_power_flow_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/cross_solver_power_flow_v1.2_sd_core.md")
    parser.add_argument("--voltage-tolerance", type=float, default=1e-4)
    parser.add_argument("--active-power-tolerance-mw", type=float, default=0.05)
    parser.add_argument("--reactive-power-tolerance-mvar", type=float, default=0.05)
    parser.add_argument("--stored-voltage-tolerance-pu", type=float, default=1e-5)
    parser.add_argument("--stored-loading-tolerance-percent", type=float, default=0.05)
    parser.add_argument("--stored-power-tolerance-mw", type=float, default=0.10)
    parser.add_argument("--stored-balance-tolerance-mw", type=float, default=1e-3)
    args = parser.parse_args()

    commit_path = ROOT / args.opf_commit
    commit = read_json(commit_path)
    committed_results = (commit.get("artifacts") or {}).get("results") or {}
    if (
        commit.get("status") != "pass"
        or committed_results.get("path") != args.opf_results
        or committed_results.get("sha256") != file_sha256(ROOT / args.opf_results)
    ):
        raise ValueError("OPF result is not covered by a valid last-written transaction commit")
    scenario_rows = read_json(ROOT / args.scenarios)
    for path in args.additional_scenarios:
        scenario_rows.extend(read_json(ROOT / path))
    scenario_ids = [str(row.get("scenario_id") or "") for row in scenario_rows]
    if any(not value for value in scenario_ids) or len(scenario_ids) != len(
        set(scenario_ids)
    ):
        raise ValueError("cross-solver scenario sources contain missing or duplicate IDs")
    scenarios = {row["scenario_id"]: row for row in scenario_rows}
    opf_payload = read_json(ROOT / args.opf_results)
    opf_results = opf_payload.get("results") or []
    comparisons = []
    errors = []
    for result in opf_results:
        scenario_id = str(result["scenario_id"])
        try:
            net = build_network(scenarios[scenario_id])
            apply_post_controls(net, result)
            control_evidence = validate_identity_controls_and_reserve(net, scenarios[scenario_id], result)
            comparison = compare(net)
            native_comparison = compare_native_case(
                net,
                scenarios[scenario_id],
                result,
                pglib_root=ROOT / args.pglib_root,
            )
            stored_comparison = compare_stored_post_action(net, result)
            stored_state_comparison = compare_stored_state(net, result)
            n1_replay = replay_registered_n1(
                net,
                result,
                voltage_tolerance=args.voltage_tolerance,
                active_power_tolerance_mw=args.active_power_tolerance_mw,
                reactive_power_tolerance_mvar=args.reactive_power_tolerance_mvar,
                loading_tolerance_percent=args.stored_loading_tolerance_percent,
                balance_tolerance_mw=args.stored_balance_tolerance_mw,
            )
            comparisons.append(
                {
                    "scenario_id": scenario_id,
                    "network_model": result.get("network_model"),
                    **comparison,
                    "native_case_validation": native_comparison,
                    "stored_post_action_validation": stored_comparison,
                    "stored_state_validation": stored_state_comparison,
                    "identity_control_reserve_validation": control_evidence,
                    "registered_n1_cross_solver_replay": n1_replay,
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"scenario_id": scenario_id, "error_type": type(exc).__name__, "error": str(exc)[:500]})

    voltage_pass = all(float(row["max_abs_bus_voltage_error_pu"]) <= args.voltage_tolerance for row in comparisons)
    active_keys = ("line_p_from_mw", "line_p_to_mw", "trafo_p_hv_mw", "trafo_p_lv_mw")
    reactive_keys = ("line_q_from_mvar", "line_q_to_mvar", "trafo_q_hv_mvar", "trafo_q_lv_mvar")
    active_power_pass = all(
        all(float(row["max_abs_flow_errors"][key]) <= args.active_power_tolerance_mw for key in active_keys)
        for row in comparisons
    )
    reactive_power_pass = all(
        all(float(row["max_abs_flow_errors"][key]) <= args.reactive_power_tolerance_mvar for key in reactive_keys)
        for row in comparisons
    )
    stored_tolerances = {
        "max_branch_loading_percent": args.stored_loading_tolerance_percent,
        "max_line_loading_percent": args.stored_loading_tolerance_percent,
        "max_transformer_loading_percent": args.stored_loading_tolerance_percent,
        "min_bus_voltage_pu": args.stored_voltage_tolerance_pu,
        "max_bus_voltage_pu": args.stored_voltage_tolerance_pu,
        "constraint_violation_score": 10.0 * args.stored_voltage_tolerance_pu,
        "generation_mw": args.stored_power_tolerance_mw,
        "demand_mw": args.stored_power_tolerance_mw,
        "network_losses_mw": args.stored_power_tolerance_mw,
        "power_balance_residual_mw": args.stored_balance_tolerance_mw,
    }
    stored_post_pass = all(
        not row["stored_post_action_validation"]["missing_keys"]
        and not row["stored_post_action_validation"]["count_mismatches"]
        and all(
            float(error) <= stored_tolerances[key]
            for key, error in row["stored_post_action_validation"]["numeric_absolute_errors"].items()
        )
        for row in comparisons
    )
    stored_state_tolerances = {
        "bus.vm_pu": args.stored_voltage_tolerance_pu,
        "bus.va_degree": 1e-3,
        "line.p_from_mw": args.active_power_tolerance_mw,
        "line.p_to_mw": args.active_power_tolerance_mw,
        "line.q_from_mvar": args.reactive_power_tolerance_mvar,
        "line.q_to_mvar": args.reactive_power_tolerance_mvar,
        "line.loading_percent": args.stored_loading_tolerance_percent,
        "trafo.p_hv_mw": args.active_power_tolerance_mw,
        "trafo.p_lv_mw": args.active_power_tolerance_mw,
        "trafo.q_hv_mvar": args.reactive_power_tolerance_mvar,
        "trafo.q_lv_mvar": args.reactive_power_tolerance_mvar,
        "trafo.loading_percent": args.stored_loading_tolerance_percent,
    }
    stored_state_pass = all(
        not row["stored_state_validation"]["missing_rows"]
        and not row["stored_state_validation"]["unexpected_rows"]
        and int(row["stored_state_validation"]["compared_values"]) > 0
        and all(
            np.isfinite(float(error)) and float(error) <= stored_state_tolerances[key]
            for key, error in row["stored_state_validation"]["max_absolute_errors"].items()
        )
        for row in comparisons
    )
    control_evidence_pass = all(
        row["identity_control_reserve_validation"]["status"] == "pass"
        for row in comparisons
    )
    registered_n1_replay_pass = bool(comparisons) and all(
        row["registered_n1_cross_solver_replay"]["status"] == "pass"
        for row in comparisons
    )
    native_strict_rows = [
        row for row in comparisons if row["native_case_validation"]["strict_gate_eligible"]
    ]
    native_voltage_pass = bool(native_strict_rows) and all(
        float(row["native_case_validation"]["max_abs_bus_voltage_error_pu"]) <= args.voltage_tolerance
        for row in native_strict_rows
    )
    native_active_power_pass = bool(native_strict_rows) and all(
        all(
            float(row["native_case_validation"]["max_abs_flow_errors"][key])
            <= args.active_power_tolerance_mw
            for key in active_keys
        )
        for row in native_strict_rows
    )
    native_reactive_power_pass = bool(native_strict_rows) and all(
        all(
            float(row["native_case_validation"]["max_abs_flow_errors"][key])
            <= args.reactive_power_tolerance_mvar
            for key in reactive_keys
        )
        for row in native_strict_rows
    )
    native_balance_pass = bool(native_strict_rows) and all(
        float(row["native_case_validation"]["power_balance_residual_mw"])
        <= args.active_power_tolerance_mw
        for row in native_strict_rows
    )
    native_voltage_label_pass = bool(native_strict_rows) and all(
        int(row["native_case_validation"]["voltage_violation_label_flips"]) == 0
        for row in native_strict_rows
    )
    native_strict_networks = {
        str(row.get("network_model") or "").lower().replace(" ", "")
        for row in native_strict_rows
    }
    required_native_strict_networks = {
        str(row.get("network_model") or "").lower().replace(" ", "")
        for row in opf_results
    } & {"ieee14", "ieee118"}
    native_strict_coverage_pass = bool(required_native_strict_networks) and required_native_strict_networks.issubset(
        native_strict_networks
    )
    all_hard_gates = (
        comparisons
        and not errors
        and voltage_pass
        and active_power_pass
        and reactive_power_pass
        and stored_post_pass
        and stored_state_pass
        and control_evidence_pass
        and registered_n1_replay_pass
        and native_voltage_pass
        and native_active_power_pass
        and native_reactive_power_pass
        and native_balance_pass
        and native_voltage_label_pass
        and native_strict_coverage_pass
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all_hard_gates else "fail",
        "scenario_truth_sha256": file_sha256(ROOT / args.scenarios),
        "opf_results_sha256": file_sha256(ROOT / args.opf_results),
        "opf_commit": args.opf_commit,
        "opf_commit_sha256": file_sha256(commit_path),
        "comparison_projection_sha256": hashlib.sha256(
            json.dumps(comparisons, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest(),
        "primary_solver": "pandapower.runpp",
        "cross_check_solver": "pypower.api.runpf",
        "solver_independence_scope": "two checks: standalone PYPOWER over a pandapower-exported case, plus independently loaded native PYPOWER IEEE cases with separately replayed loads, contingencies, and post-action controls",
        "opf_result_count": len(opf_results),
        "compared_scenarios": len(comparisons),
        "error_count": len(errors),
        "errors": errors,
        "tolerances": {
            "bus_voltage_pu": args.voltage_tolerance,
            "active_power_mw": args.active_power_tolerance_mw,
            "reactive_power_mvar": args.reactive_power_tolerance_mvar,
            "stored_post_action_by_field": stored_tolerances,
            "stored_state_by_field": stored_state_tolerances,
        },
        "hard_gates": {
            "all_scenarios_compared": len(comparisons) == len(opf_results),
            "voltage_tolerance": voltage_pass,
            "active_power_tolerance": active_power_pass,
            "reactive_power_tolerance": reactive_power_pass,
            "stored_post_action_match": stored_post_pass,
            "stored_post_action_state_match": stored_state_pass,
            "identity_control_and_reserve_evidence": control_evidence_pass,
            "registered_n1_cross_solver_replay": registered_n1_replay_pass,
            "native_case_voltage_tolerance": native_voltage_pass,
            "native_case_active_power_tolerance": native_active_power_pass,
            "native_case_reactive_power_tolerance": native_reactive_power_pass,
            "native_case_power_balance": native_balance_pass,
            "native_case_voltage_labels": native_voltage_label_pass,
            "native_case_strict_network_coverage": native_strict_coverage_pass,
        },
        "required_native_case_strict_networks": sorted(required_native_strict_networks),
        "max_observed_errors": {
            "bus_voltage_pu": max((float(row["max_abs_bus_voltage_error_pu"]) for row in comparisons), default=None),
            "active_power_mw": max(
                (float(value) for row in comparisons for key, value in row["max_abs_flow_errors"].items() if "_p_" in key),
                default=None,
            ),
            "reactive_power_mvar": max(
                (float(value) for row in comparisons for key, value in row["max_abs_flow_errors"].items() if "_q_" in key),
                default=None,
            ),
            "stored_post_action_by_field": {
                key: max(
                    (
                        float(row["stored_post_action_validation"]["numeric_absolute_errors"].get(key, 0.0))
                        for row in comparisons
                    ),
                    default=None,
                )
                for key in stored_tolerances
            },
            "stored_post_action_state_by_field": {
                key: max(
                    (
                        float(row["stored_state_validation"]["max_absolute_errors"].get(key, 0.0))
                        for row in comparisons
                    ),
                    default=None,
                )
                for key in stored_state_tolerances
            },
            "native_case_bus_voltage_pu": max(
                (float(row["native_case_validation"]["max_abs_bus_voltage_error_pu"]) for row in comparisons),
                default=None,
            ),
            "native_case_active_power_mw": max(
                (
                    float(value)
                    for row in comparisons
                    for key, value in row["native_case_validation"]["max_abs_flow_errors"].items()
                    if "_p_" in key
                ),
                default=None,
            ),
            "native_case_reactive_power_mvar": max(
                (
                    float(value)
                    for row in comparisons
                    for key, value in row["native_case_validation"]["max_abs_flow_errors"].items()
                    if "_q_" in key
                ),
                default=None,
            ),
            "native_case_power_balance_residual_mw": max(
                (float(row["native_case_validation"]["power_balance_residual_mw"]) for row in comparisons),
                default=None,
            ),
            "native_case_voltage_label_flips": sum(
                int(row["native_case_validation"]["voltage_violation_label_flips"])
                for row in comparisons
            ),
            "native_case_thermal_label_flips_diagnostic": sum(
                int(row["native_case_validation"]["thermal_violation_label_flips"])
                for row in comparisons
            ),
        },
        "network_counts": dict(Counter(str(row.get("network_model")) for row in comparisons)),
        "native_case_strict_compared_scenarios": len(native_strict_rows),
        "native_case_diagnostic_compared_scenarios": len(comparisons) - len(native_strict_rows),
        "registered_n1_replayed_checks": sum(
            int(row["registered_n1_cross_solver_replay"]["replayed_checks"])
            for row in comparisons
        ),
        "registered_n1_passed_checks": sum(
            int(row["registered_n1_cross_solver_replay"]["passed_checks"])
            for row in comparisons
        ),
        "comparisons": comparisons,
    }
    write_json(ROOT / args.output_json, report)
    (ROOT / args.output_md).write_text(
        "\n".join(
            [
                "# Cross-Solver Power-Flow Validation",
                "",
                f"- Status: `{report['status']}`",
                f"- OPF scenarios compared: {report['compared_scenarios']}/{report['opf_result_count']}",
                f"- Solver errors: {report['error_count']}",
                f"- Maximum voltage difference: {report['max_observed_errors']['bus_voltage_pu']}",
                f"- Maximum active-power flow difference (MW): {report['max_observed_errors']['active_power_mw']}",
                f"- Maximum reactive-power flow difference (MVAr): {report['max_observed_errors']['reactive_power_mvar']}",
                f"- Stored post-action metrics match recomputation: {report['hard_gates']['stored_post_action_match']}",
                f"- Native-case maximum voltage difference: {report['max_observed_errors']['native_case_bus_voltage_pu']}",
                f"- Native-case maximum active-power difference (MW): {report['max_observed_errors']['native_case_active_power_mw']}",
                f"- Native-case maximum reactive-power difference (MVAr): {report['max_observed_errors']['native_case_reactive_power_mvar']}",
                f"- Native-case voltage-label flips: {report['max_observed_errors']['native_case_voltage_label_flips']}",
                f"- Native-case thermal-label flips (diagnostic, loading definitions differ): {report['max_observed_errors']['native_case_thermal_label_flips_diagnostic']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "compared_scenarios": report["compared_scenarios"],
                "error_count": report["error_count"],
                "registered_n1_replayed_checks": report["registered_n1_replayed_checks"],
                "registered_n1_passed_checks": report["registered_n1_passed_checks"],
                "hard_gates": report["hard_gates"],
                "max_observed_errors": report["max_observed_errors"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
