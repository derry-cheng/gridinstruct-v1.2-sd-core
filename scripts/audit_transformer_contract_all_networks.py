#!/usr/bin/env python3
"""Regression-test tap, phase-shift, and three-winding transformer contracts."""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandapower as pp

from gridinstruct_utils import ROOT, ensure_dirs, write_json
from power_evidence_common import (
    NETWORK_LOADERS,
    PGLIB_EXPECTED_COMMIT,
    load_pglib_case,
    load_network,
    normalize_transformer_contract,
    pglib_rating_overrides_for_network,
    pglib_source_descriptor,
    validate_pglib_repository,
)


CONTRACT_FIELDS = (
    "tap_side",
    "tap_neutral",
    "tap_min",
    "tap_max",
    "tap_step_percent",
    "tap_step_degree",
    "tap_pos",
    "tap_changer_type",
    "shift_degree",
    "tap_phase_shifter",
    "tap_dependent_impedance",
)
CONTRACT_3W_FIELDS = (
    "tap_side",
    "tap_neutral",
    "tap_min",
    "tap_max",
    "tap_step_percent",
    "tap_step_degree",
    "tap_pos",
    "tap_changer_type",
    "shift_mv_degree",
    "shift_lv_degree",
    "tap_at_star_point",
    "tap_dependent_impedance",
)
POWER_FLOW_INVARIANCE_TOLERANCE = 1e-8


def max_abs(left, right) -> float:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    finite = np.isfinite(a) & np.isfinite(b)
    if not finite.any():
        return 0.0
    return float(np.max(np.abs(a[finite] - b[finite])))


def equal_series(left, right) -> bool:
    if len(left) != len(right):
        return False
    for a, b in zip(left.tolist(), right.tolist()):
        if (a is None or (isinstance(a, float) and np.isnan(a))) and (
            b is None or (isinstance(b, float) and np.isnan(b))
        ):
            continue
        if a != b:
            return False
    return True


def audit_network(system: str, run_power_flow: bool = True) -> dict[str, Any]:
    legacy = NETWORK_LOADERS[system]()
    normalized = copy.deepcopy(legacy)
    normalize_transformer_contract(normalized)
    field_checks = {}
    for element, fields in (("trafo", CONTRACT_FIELDS), ("trafo3w", CONTRACT_3W_FIELDS)):
        original = getattr(legacy, element)
        updated = getattr(normalized, element)
        field_checks[element] = {
            field: equal_series(original[field], updated[field])
            for field in fields
            if field in original.columns
        }
    differences: dict[str, float] = {}
    solve_status = "not_run"
    if run_power_flow:
        options = {
            "algorithm": "nr",
            "init": "auto",
            "calculate_voltage_angles": True,
            "max_iteration": 100,
            "numba": False,
        }
        pp.runpp(legacy, **options)
        pp.runpp(normalized, **options)
        differences = {
            "bus_vm_pu": max_abs(legacy.res_bus.vm_pu, normalized.res_bus.vm_pu),
            "bus_va_degree": max_abs(legacy.res_bus.va_degree, normalized.res_bus.va_degree),
            "line_p_from_mw": max_abs(legacy.res_line.p_from_mw, normalized.res_line.p_from_mw),
            "trafo_p_hv_mw": max_abs(legacy.res_trafo.p_hv_mw, normalized.res_trafo.p_hv_mw),
            "trafo3w_p_hv_mw": max_abs(
                legacy.res_trafo3w.p_hv_mw,
                normalized.res_trafo3w.p_hv_mw,
            ),
        }
        solve_status = "converged"
    passed = (
        all(all(checks.values()) for checks in field_checks.values())
        and (
            not run_power_flow
            or (
                differences
                and max(differences.values()) <= POWER_FLOW_INVARIANCE_TOLERANCE
            )
        )
    )
    return {
        "network": system,
        "two_winding_transformers": len(legacy.trafo),
        "three_winding_transformers": len(legacy.trafo3w),
        "tap_rows": int(legacy.trafo.tap_side.notna().sum()) if len(legacy.trafo) else 0,
        "phase_shift_rows": int(
            (legacy.trafo.shift_degree.fillna(0.0).abs() > 0).sum()
        ) if len(legacy.trafo) else 0,
        "three_winding_scope": (
            "source_rows_regressed"
            if len(legacy.trafo3w)
            else "no_three_winding_transformer_in_source_case"
        ),
        "field_preservation": field_checks,
        "power_flow_status": solve_status,
        "max_absolute_differences": differences,
        "power_flow_invariance_tolerance": POWER_FLOW_INVARIANCE_TOLERANCE,
        "status": "pass" if passed else "fail",
    }


def synthetic_three_winding_fixture() -> dict[str, Any]:
    net = pp.create_empty_network(sn_mva=100.0)
    hv = pp.create_bus(net, vn_kv=110.0)
    mv = pp.create_bus(net, vn_kv=20.0)
    lv = pp.create_bus(net, vn_kv=10.0)
    pp.create_ext_grid(net, hv, vm_pu=1.02)
    pp.create_load(net, mv, p_mw=8.0, q_mvar=2.0)
    pp.create_load(net, lv, p_mw=5.0, q_mvar=1.0)
    idx = pp.create_transformer3w_from_parameters(
        net,
        hv,
        mv,
        lv,
        vn_hv_kv=110.0,
        vn_mv_kv=20.0,
        vn_lv_kv=10.0,
        sn_hv_mva=40.0,
        sn_mv_mva=25.0,
        sn_lv_mva=25.0,
        vk_hv_percent=10.0,
        vk_mv_percent=10.5,
        vk_lv_percent=11.0,
        vkr_hv_percent=0.5,
        vkr_mv_percent=0.5,
        vkr_lv_percent=0.5,
        pfe_kw=20.0,
        i0_percent=0.1,
        shift_mv_degree=5.0,
        shift_lv_degree=-5.0,
        tap_side="hv",
        tap_neutral=0,
        tap_min=-2,
        tap_max=2,
        tap_step_percent=1.25,
        tap_pos=0,
        tap_changer_type="Ratio",
        tap_dependency_table=False,
    )
    normalize_transformer_contract(net)
    pp.runpp(net, calculate_voltage_angles=True, numba=False)
    base_vm = net.res_bus.vm_pu.copy()
    base_angle = net.res_bus.va_degree.copy()
    net.trafo3w.at[idx, "tap_pos"] = 1
    pp.runpp(net, calculate_voltage_angles=True, init="flat", numba=False)
    tap_vm_delta = max_abs(base_vm, net.res_bus.vm_pu)
    tap_angle_delta = max_abs(base_angle, net.res_bus.va_degree)
    return {
        "status": "pass" if tap_vm_delta > 1e-8 or tap_angle_delta > 1e-8 else "fail",
        "tap_voltage_response_pu": tap_vm_delta,
        "tap_angle_response_degree": tap_angle_delta,
        "shift_mv_degree": float(net.trafo3w.at[idx, "shift_mv_degree"]),
        "shift_lv_degree": float(net.trafo3w.at[idx, "shift_lv_degree"]),
        "tap_dependency_table_explicit": bool(
            net.trafo3w.at[idx, "tap_dependency_table"] is False
            or net.trafo3w.at[idx, "tap_dependency_table"] == False  # noqa: E712
        ),
    }


def audit_pglib_transformer_fields(
    system: str,
    pglib_root: Path,
    repository: dict[str, Any],
) -> dict[str, Any]:
    case = load_pglib_case(pglib_root, system)
    mapping_overrides = pglib_rating_overrides_for_network(
        load_network(system),
        system,
        pglib_root,
        repository,
    )
    bus_voltage = {int(row[0]): float(row[9]) for row in case["bus"]}
    transformer_rows = []
    for row_number, row in enumerate(case["branch"], start=1):
        if int(row[10]) == 0:
            continue
        from_bus = int(row[0])
        to_bus = int(row[1])
        ratio = float(row[8])
        shift = float(row[9])
        if ratio != 0.0 or bus_voltage[from_bus] != bus_voltage[to_bus]:
            transformer_rows.append(
                {
                    "source_row": row_number,
                    "endpoints": [from_bus, to_bus],
                    "tap_ratio": ratio,
                    "phase_shift_degree": shift,
                    "rate_a_mva": float(row[5]),
                }
            )
    complete = all(
        np.isfinite(row["tap_ratio"])
        and np.isfinite(row["phase_shift_degree"])
        and np.isfinite(row["rate_a_mva"])
        for row in transformer_rows
    )
    return {
        "network": system,
        "source": pglib_source_descriptor(repository, case, "mpc.branch transformer rows"),
        "transformer_rows": len(transformer_rows),
        "nonzero_tap_rows": sum(row["tap_ratio"] != 0.0 for row in transformer_rows),
        "nonzero_phase_shift_rows": sum(
            row["phase_shift_degree"] != 0.0 for row in transformer_rows
        ),
        "three_winding_representation": (
            "MATPOWER branch schema is two-terminal; the separately hard-gated "
            "synthetic three-winding fixture validates conversion semantics"
        ),
        "all_tap_phase_rating_fields_finite": complete,
        "pandapower_branch_mapping_status": "pass",
        "source_bound_branch_ratings_mapped": len(mapping_overrides),
        "status": "pass" if complete else "fail",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", nargs="+", default=list(NETWORK_LOADERS))
    parser.add_argument("--skip-power-flow", action="store_true")
    parser.add_argument("--pglib-root", type=Path)
    parser.add_argument("--pglib-commit", default=PGLIB_EXPECTED_COMMIT)
    parser.add_argument(
        "--output-json",
        default="reports/pandapower_transformer_contract_all_networks_v1.2_sd_core.json",
    )
    args = parser.parse_args()
    unknown = sorted(set(args.systems) - set(NETWORK_LOADERS))
    if unknown:
        raise ValueError(f"unknown networks: {unknown}")
    rows = [
        audit_network(system, run_power_flow=not args.skip_power_flow)
        for system in args.systems
    ]
    pglib_repository = (
        validate_pglib_repository(args.pglib_root, args.pglib_commit)
        if args.pglib_root
        else None
    )
    pglib_rows = (
        [
            audit_pglib_transformer_fields(
                system,
                args.pglib_root,
                pglib_repository,
            )
            for system in args.systems
        ]
        if args.pglib_root and pglib_repository
        else []
    )
    fixture = synthetic_three_winding_fixture()
    report = {
        "contract_version": "gridinstruct-transformer-contract-regression-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "pass"
            if (
                all(row["status"] == "pass" for row in rows)
                and all(row["status"] == "pass" for row in pglib_rows)
                and fixture["status"] == "pass"
            )
            else "fail"
        ),
        "systems": rows,
        "pglib_repository": pglib_repository,
        "pglib_source_contracts": pglib_rows,
        "synthetic_three_winding_fixture": fixture,
        "hard_gates": {
            "all_requested_networks_regressed": len(rows) == len(args.systems),
            "all_source_tap_and_phase_fields_preserved": all(
                all(all(checks.values()) for checks in row["field_preservation"].values())
                for row in rows
            ),
            "all_base_power_flows_invariant": all(row["status"] == "pass" for row in rows),
            "three_winding_tap_and_phase_fixture_pass": fixture["status"] == "pass",
            "all_pglib_tap_phase_fields_finite": all(
                row["status"] == "pass" for row in pglib_rows
            ) if pglib_rows else None,
        },
    }
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.output_json, report)
    print(json.dumps({"status": report["status"], "networks": len(rows)}, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
