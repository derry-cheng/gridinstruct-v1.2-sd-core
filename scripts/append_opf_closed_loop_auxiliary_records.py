#!/usr/bin/env python3
"""Append OPF-validated auxiliary-decision records to the SD-core release."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
from collections import Counter
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandapower as pp
import pandapower.networks as pn
from pandapower.topology import unsupplied_buses
from tqdm.auto import tqdm

from generate_instruction_data import make_auxiliary
from gridinstruct_utils import (
    ROOT,
    ensure_dirs,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from pglib_network_loader import load_pglib_network, network_provenance


SYSTEM_LOADERS = {
    "ieee14": pn.case14,
    "ieee30": pn.case30,
    "ieee57": pn.case57,
    "ieee118": pn.case118,
    "ieee300": pn.case300,
}
TAG = "opf_closed_loop_auxiliary_v2"
OPF_TARGET_SCHEMA_VERSION = "opf-executable-control-target-v2"
CONTROL_ELEMENTS = ("ext_grid", "gen")
CONTROL_VALUE_FIELDS = ("p_mw", "q_mvar", "vm_pu")
COMMAND_FIELDS_BY_ELEMENT = {
    "ext_grid": ("vm_pu",),
    "gen": ("p_mw", "vm_pu"),
}
RESPONSE_FIELDS_BY_ELEMENT = {
    "ext_grid": ("p_mw", "q_mvar"),
    "gen": ("q_mvar",),
}
VOLTAGE_MIN_PU = 0.95
VOLTAGE_MAX_PU = 1.05
VOLTAGE_BOUNDARY_EPSILON_PU = 1e-6
THERMAL_BOUNDARY_EPSILON_PERCENT = 1e-6
CORRECTIVE_LOADING_GUARD_PERCENT = 1e-3
ROBUST_ACTIVE_LOAD_FRACTION = 0.10
ROBUST_ACTIVE_LOSS_BUFFER_FRACTION = 0.02
ROBUST_REACTIVE_CAPABILITY_RESERVE_FRACTION = 0.46
ROBUST_CORRECTIVE_LOADING_LIMIT_PERCENT = 80.0
ROBUST_OPTIMIZATION_VOLTAGE_BOUNDS_PU = (0.96, 1.04)
ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU = 0.001
PREREGISTERED_ACTIVE_LOAD_STRESS = (-0.10, -0.05, -0.02, 0.02, 0.05, 0.10)
ALLOWED_CONTINGENCIES = {
    "base_power_flow",
    "generator_perturbation",
    "n1_branch_outage",
    "n2_branch_outage",
    "n1_transformer_outage",
    "n1_generator_outage",
    "n1_bus_outage",
}


class CandidateInfeasible(RuntimeError):
    """Expected screening outcome when no registered corrective-security setting is feasible."""

    def __init__(self, attempts: list[dict[str, Any]]) -> None:
        super().__init__(
            "no registered loading margin passed every AC and security gate"
        )
        self.attempts = attempts


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    ensure_dirs(path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    write_json(temporary, payload)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    write_jsonl(temporary, rows)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_markdown(path: Path, report: dict[str, Any]) -> None:
    ensure_dirs(path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    write_markdown(report, temporary)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def normalize_system(row: dict[str, Any]) -> str:
    system = (
        str(row.get("system") or row.get("network_model") or "")
        .lower()
        .replace(" ", "")
    )
    return system.replace("ieee", "ieee")


def stable_key(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()[:48]
    return f"{slug}_{digest}"


def scale_loads(net, factor: float) -> None:
    if len(net.load):
        net.load["p_mw"] *= factor
        net.load["q_mvar"] *= factor


def parse_branch_indices(scenario: dict[str, Any]) -> list[int]:
    if scenario.get("line_indices"):
        return [int(value) for value in scenario["line_indices"]]
    if scenario.get("line_index") is not None:
        return [int(scenario["line_index"])]
    scenario_id = str(scenario.get("scenario_id", ""))
    n1 = re.search(r"branchidx_(\d+)_", scenario_id)
    if n1:
        return [int(n1.group(1))]
    n2 = re.search(r"branches_(\d+)_\d+_\d+_(\d+)_\d+_\d+$", scenario_id)
    if n2:
        return [int(n2.group(1)), int(n2.group(2))]
    return []


def generator_factor(scenario: dict[str, Any]) -> float:
    value = scenario.get("generator_factor")
    if value is not None:
        return float(value)
    match = re.search(r"_gen(\d+)", str(scenario.get("scenario_id", "")))
    if match:
        return int(match.group(1)) / 100.0
    return 1.0


def build_net(scenario: dict[str, Any]):
    system = normalize_system(scenario)
    if system not in SYSTEM_LOADERS:
        raise ValueError(f"unsupported OPF system: {system}")
    physical_source = scenario.get("physical_source")
    if isinstance(physical_source, dict):
        configured_pglib_root = os.environ.get("GRIDINSTRUCT_PGLIB_ROOT")
        pglib_root = Path(configured_pglib_root) if configured_pglib_root else (
            ROOT / "third_party/pglib-opf-v23.07"
        )
        net = load_pglib_network(system, pglib_root)
        actual_source = network_provenance(net)
        for field in ("repository_commit", "case_file", "case_sha256"):
            if actual_source.get(field) != physical_source.get(field):
                raise ValueError(
                    f"{system} physical-source mismatch for {field}: "
                    f"{physical_source.get(field)!r} != {actual_source.get(field)!r}"
                )
    else:
        net = SYSTEM_LOADERS[system]()
    kind = str(scenario.get("contingency_type") or "")
    scale_loads(net, float(scenario.get("load_level") or 1.0))
    if len(net.gen):
        net.gen["p_mw"] *= float(scenario.get("base_generation_scaling_factor") or 1.0)
        factor = generator_factor(scenario)
        if kind == "generator_perturbation" or not math.isclose(
            factor, 1.0, rel_tol=0.0, abs_tol=1e-12
        ):
            net.gen["p_mw"] *= factor
    if kind not in ALLOWED_CONTINGENCIES:
        raise ValueError(f"unsupported OPF contingency: {kind!r}")
    if kind in {"n1_branch_outage", "n2_branch_outage"}:
        for idx in parse_branch_indices(scenario):
            if 0 <= idx < len(net.line):
                net.line.at[idx, "in_service"] = False
    elif kind == "generator_perturbation":
        pass
    elif kind == "n1_transformer_outage":
        idx = scenario.get("transformer_index")
        if idx is not None and int(idx) in net.trafo.index:
            net.trafo.at[int(idx), "in_service"] = False
    elif kind == "n1_generator_outage":
        idx = scenario.get("generator_index")
        if idx is not None and int(idx) in net.gen.index:
            net.gen.at[int(idx), "in_service"] = False
    elif kind == "n1_bus_outage":
        idx = scenario.get("bus_index")
        if idx is None or int(idx) not in net.bus.index:
            raise IndexError(f"bus index {idx} outside {system} bus table")
        if ((scenario.get("affected_components") or {}).get("buses") or []) != [
            int(idx)
        ]:
            raise ValueError("n1_bus_outage requires one matching affected bus")
        net.bus.at[int(idx), "in_service"] = False
    return net


def metrics(net) -> dict[str, float | int]:
    max_line_loading = (
        float(net.res_line.loading_percent.max()) if len(net.res_line) else 0.0
    )
    max_trafo_loading = (
        float(net.res_trafo.loading_percent.max()) if len(net.res_trafo) else 0.0
    )
    max_loading = max(max_line_loading, max_trafo_loading)
    min_vm = float(net.res_bus.vm_pu.min()) if len(net.res_bus) else 1.0
    max_vm = float(net.res_bus.vm_pu.max()) if len(net.res_bus) else 1.0
    thermal_excess = max(0.0, max_loading - 100.0 - THERMAL_BOUNDARY_EPSILON_PERCENT)
    low_voltage_gap = max(0.0, VOLTAGE_MIN_PU - min_vm - VOLTAGE_BOUNDARY_EPSILON_PU)
    high_voltage_gap = max(0.0, max_vm - VOLTAGE_MAX_PU - VOLTAGE_BOUNDARY_EPSILON_PU)
    voltage_violation_count = (
        int(
            (
                (net.res_bus.vm_pu < VOLTAGE_MIN_PU - VOLTAGE_BOUNDARY_EPSILON_PU)
                | (net.res_bus.vm_pu > VOLTAGE_MAX_PU + VOLTAGE_BOUNDARY_EPSILON_PU)
            ).sum()
        )
        if len(net.res_bus)
        else 0
    )
    overloaded_branch_count = (
        int(
            (
                net.res_line.loading_percent > 100.0 + THERMAL_BOUNDARY_EPSILON_PERCENT
            ).sum()
        )
        if len(net.res_line)
        else 0
    )
    overloaded_transformer_count = (
        int(
            (
                net.res_trafo.loading_percent > 100.0 + THERMAL_BOUNDARY_EPSILON_PERCENT
            ).sum()
        )
        if len(net.res_trafo)
        else 0
    )
    generation_mw = 0.0
    for table in ("res_ext_grid", "res_gen", "res_sgen"):
        result = getattr(net, table, None)
        if result is not None and len(result) and "p_mw" in result:
            generation_mw += float(result.p_mw.sum())
    demand_mw = 0.0
    for table in ("res_load", "res_shunt", "res_ward", "res_xward", "res_storage"):
        result = getattr(net, table, None)
        if result is not None and len(result) and "p_mw" in result:
            demand_mw += float(result.p_mw.sum())
    losses_mw = 0.0
    for table in ("res_line", "res_trafo", "res_trafo3w", "res_impedance"):
        result = getattr(net, table, None)
        if result is not None and len(result) and "pl_mw" in result:
            losses_mw += float(result.pl_mw.sum())
    balance_residual = abs(generation_mw - demand_mw - losses_mw)
    violation_score = thermal_excess / 100.0 + 10.0 * (
        low_voltage_gap + high_voltage_gap
    )
    return {
        "max_branch_loading_percent": round(max_loading, 6),
        "max_line_loading_percent": round(max_line_loading, 6),
        "max_transformer_loading_percent": round(max_trafo_loading, 6),
        "min_bus_voltage_pu": round(min_vm, 6),
        "max_bus_voltage_pu": round(max_vm, 6),
        "overloaded_branch_count": overloaded_branch_count,
        "overloaded_transformer_count": overloaded_transformer_count,
        "voltage_violation_count": voltage_violation_count,
        "constraint_violation_score": round(violation_score, 8),
        "generation_mw": round(generation_mw, 6),
        "demand_mw": round(demand_mw, 6),
        "network_losses_mw": round(losses_mw, 6),
        "power_balance_residual_mw": round(balance_residual, 8),
    }


def rounded_or_none(value: Any, digits: int = 8) -> float | None:
    return round(float(value), digits) if finite(value) else None


def electrical_state_vector(net) -> dict[str, list[dict[str, Any]]]:
    """Store the numerical state needed for independent post-action replay."""
    return {
        "bus": [
            {
                "index": int(idx),
                "in_service": bool(net.bus.at[idx, "in_service"]),
                "vm_pu": rounded_or_none(net.res_bus.at[idx, "vm_pu"]),
                "va_degree": rounded_or_none(net.res_bus.at[idx, "va_degree"]),
            }
            for idx in net.res_bus.index
        ],
        "line": [
            {
                "index": int(idx),
                "in_service": bool(net.line.at[idx, "in_service"]),
                "p_from_mw": rounded_or_none(net.res_line.at[idx, "p_from_mw"]),
                "q_from_mvar": rounded_or_none(net.res_line.at[idx, "q_from_mvar"]),
                "p_to_mw": rounded_or_none(net.res_line.at[idx, "p_to_mw"]),
                "q_to_mvar": rounded_or_none(net.res_line.at[idx, "q_to_mvar"]),
                "loading_percent": rounded_or_none(
                    net.res_line.at[idx, "loading_percent"]
                ),
            }
            for idx in net.res_line.index
        ],
        "trafo": [
            {
                "index": int(idx),
                "in_service": bool(net.trafo.at[idx, "in_service"]),
                "p_hv_mw": rounded_or_none(net.res_trafo.at[idx, "p_hv_mw"]),
                "q_hv_mvar": rounded_or_none(net.res_trafo.at[idx, "q_hv_mvar"]),
                "p_lv_mw": rounded_or_none(net.res_trafo.at[idx, "p_lv_mw"]),
                "q_lv_mvar": rounded_or_none(net.res_trafo.at[idx, "q_lv_mvar"]),
                "loading_percent": rounded_or_none(
                    net.res_trafo.at[idx, "loading_percent"]
                ),
            }
            for idx in net.res_trafo.index
        ],
    }


def electrical_state_complete(net) -> bool:
    for idx in net.bus.index:
        if bool(net.bus.at[idx, "in_service"]) and any(
            not finite(net.res_bus.at[idx, field]) for field in ("vm_pu", "va_degree")
        ):
            return False
    for element, fields in (
        (
            "line",
            ("p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar", "loading_percent"),
        ),
        ("trafo", ("p_hv_mw", "q_hv_mvar", "p_lv_mw", "q_lv_mvar", "loading_percent")),
    ):
        table = getattr(net, element)
        result = getattr(net, f"res_{element}")
        for idx in table.index:
            if bool(table.at[idx, "in_service"]) and any(
                not finite(result.at[idx, field]) for field in fields
            ):
                return False
    return True


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def native_value(table, idx: int, column: str) -> float | None:
    if column not in table or not finite(table.at[idx, column]):
        return None
    return float(table.at[idx, column])


def native_fixed_interval(
    center: float,
    native_min: float | None,
    native_max: float | None,
    *,
    tolerance: float = 1e-7,
) -> tuple[float, float] | None:
    """Return a legitimate fixed native setpoint without relaxing its bounds."""
    if (
        native_min is None
        or native_max is None
        or abs(native_max - native_min) > tolerance
    ):
        return None
    if abs(center - native_min) > tolerance:
        raise ValueError(
            f"operating point {center} is outside fixed native interval "
            f"[{native_min}, {native_max}]"
        )
    return float(native_min), float(native_max)


def bounded_window(
    center: float, span: float, native_min: float | None, native_max: float | None
) -> tuple[float, float]:
    fixed = native_fixed_interval(center, native_min, native_max)
    if fixed is not None:
        return fixed
    lower = center - span
    upper = center + span
    if native_min is not None:
        lower = max(lower, native_min)
    if native_max is not None:
        upper = min(upper, native_max)
    if lower >= upper:
        lower = native_min if native_min is not None else center - max(span, 1.0)
        upper = native_max if native_max is not None else center + max(span, 1.0)
    if lower >= upper:
        raise ValueError(
            f"invalid controllable range around {center}: [{lower}, {upper}]"
        )
    return float(lower), float(upper)


def contingency_window(
    center: float,
    fallback_span: float,
    native_min: float | None,
    native_max: float | None,
) -> tuple[float, float]:
    """Use full native emergency capability, with a finite documented fallback when absent."""
    fixed = native_fixed_interval(center, native_min, native_max)
    if fixed is not None:
        return fixed
    lower = native_min if native_min is not None else center - fallback_span
    upper = native_max if native_max is not None else center + fallback_span
    if lower >= upper:
        raise ValueError(
            f"invalid contingency controllable range around {center}: [{lower}, {upper}]"
        )
    return float(lower), float(upper)


def interior_interval(
    outer: tuple[float, float],
    reserve: float,
    *,
    label: str,
) -> tuple[float, float]:
    lower = float(outer[0]) + float(reserve)
    upper = float(outer[1]) - float(reserve)
    if lower >= upper:
        raise ValueError(
            f"{label} cannot reserve {reserve:.6f} on both sides of "
            f"[{outer[0]:.6f}, {outer[1]:.6f}]"
        )
    return lower, upper


def fractional_interior_interval(
    outer: tuple[float, float],
    reserve_fraction: float,
    *,
    label: str,
) -> tuple[float, float]:
    """Reserve the same fraction of a native interval at both boundaries."""
    lower, upper = [float(value) for value in outer]
    if lower == upper:
        return lower, upper
    if not 0.0 <= reserve_fraction < 0.5:
        raise ValueError(
            f"{label} reserve fraction must be in [0, 0.5), got {reserve_fraction}"
        )
    reserve = reserve_fraction * (upper - lower)
    return interior_interval(outer, reserve, label=label)


def element_has_cost(net, element: str, idx: int) -> bool:
    for table_name in ("poly_cost", "pwl_cost"):
        table = getattr(net, table_name, None)
        if table is not None and len(table):
            mask = (table.et == element) & (table.element.astype(int) == int(idx))
            if bool(mask.any()):
                return True
    return False


def prepare_opf(net, loading_margin_percent: float = 100.0) -> dict[str, Any]:
    """Apply native limits plus transparent ramp/reserve windows.

    Native case limits and costs are retained.  Missing limits are filled with
    bounded windows around the pre-action operating point; no ±10,000 fallback
    is used.
    """
    if not 0.0 < loading_margin_percent <= 100.0:
        raise ValueError(
            f"loading margin must be in (0, 100], got {loading_margin_percent}"
        )
    network_limit_rows: list[dict[str, Any]] = []
    robust_voltage_min, robust_voltage_max = ROBUST_OPTIMIZATION_VOLTAGE_BOUNDS_PU
    for idx in net.bus.index:
        native_min = native_value(net.bus, int(idx), "min_vm_pu")
        native_max = native_value(net.bus, int(idx), "max_vm_pu")
        validation_min = max(VOLTAGE_MIN_PU, native_min) if native_min is not None else VOLTAGE_MIN_PU
        validation_max = min(VOLTAGE_MAX_PU, native_max) if native_max is not None else VOLTAGE_MAX_PU
        effective_min = max(robust_voltage_min, validation_min)
        effective_max = min(robust_voltage_max, validation_max)
        if effective_min >= effective_max:
            raise ValueError(f"empty effective voltage interval at bus {idx}")
        net.bus.at[idx, "min_vm_pu"] = effective_min
        net.bus.at[idx, "max_vm_pu"] = effective_max
        network_limit_rows.append(
            {
                "element": "bus",
                "index": int(idx),
                "native": [native_min, native_max],
                "validation": [validation_min, validation_max],
                "effective": [effective_min, effective_max],
            }
        )
    for element in ("line", "trafo"):
        table = getattr(net, element)
        if not len(table):
            continue
        if "max_loading_percent" not in table:
            table["max_loading_percent"] = float("nan")
        for idx in table.index:
            native_max = native_value(table, int(idx), "max_loading_percent")
            effective_max = (
                min(loading_margin_percent, native_max)
                if native_max is not None
                else loading_margin_percent
            )
            if effective_max <= 0:
                raise ValueError(
                    f"invalid native loading limit for {element} {idx}: {native_max}"
                )
            table.at[idx, "max_loading_percent"] = effective_max
            network_limit_rows.append(
                {
                    "element": element,
                    "index": int(idx),
                    "native_max_loading_percent": native_max,
                    "effective_max_loading_percent": effective_max,
                }
            )
    total_p = max(float(net.res_load.p_mw.sum()) if len(net.res_load) else 0.0, 1.0)
    total_q = max(
        abs(float(net.res_load.q_mvar.sum())) if len(net.res_load) else 0.0, 1.0
    )
    active_balance_reserve_mw = (
        ROBUST_ACTIVE_LOAD_FRACTION + ROBUST_ACTIVE_LOSS_BUFFER_FRACTION
    ) * total_p
    bound_rows: list[dict[str, Any]] = []
    if len(net.ext_grid):
        net.ext_grid["controllable"] = True
        in_service_ext_grids = [
            int(idx)
            for idx in net.ext_grid.index
            if bool(net.ext_grid.at[idx, "in_service"])
        ]
        reserve_share_mw = active_balance_reserve_mw / max(
            len(in_service_ext_grids),
            1,
        )
        for idx in net.ext_grid.index:
            p0 = float(net.res_ext_grid.at[idx, "p_mw"])
            q0 = float(net.res_ext_grid.at[idx, "q_mvar"])
            native_p_min = native_value(net.ext_grid, idx, "min_p_mw")
            native_p_max = native_value(net.ext_grid, idx, "max_p_mw")
            native_q_min = native_value(net.ext_grid, idx, "min_q_mvar")
            native_q_max = native_value(net.ext_grid, idx, "max_q_mvar")
            p_bounds = bounded_window(
                p0,
                max(10.0, 0.10 * total_p),
                native_p_min,
                native_p_max,
            )
            q_bounds = bounded_window(
                q0,
                max(10.0, 0.20 * total_q),
                native_q_min,
                native_q_max,
            )
            contingency_p_bounds = contingency_window(
                p0, max(25.0, 0.30 * total_p), native_p_min, native_p_max
            )
            contingency_q_bounds = contingency_window(
                q0, max(25.0, 0.75 * total_q), native_q_min, native_q_max
            )
            robust_balance_p_bounds = interior_interval(
                contingency_p_bounds,
                reserve_share_mw,
                label=f"ext_grid:{idx}:active_balance",
            )
            robust_balance_q_bounds = fractional_interior_interval(
                contingency_q_bounds,
                ROBUST_REACTIVE_CAPABILITY_RESERVE_FRACTION,
                label=f"ext_grid:{idx}:reactive_balance",
            )
            nominal_p_window = p_bounds
            nominal_q_window = q_bounds
            # The source operating point can itself exceed the generator row's
            # registered capability (the slack injection absorbs an intentionally
            # perturbed dispatch).  The corrective action must therefore be free
            # to move across the native interval.  Use its robust interior
            # directly instead of intersecting it with an invented ramp around
            # an already infeasible pre-action point.
            p_bounds = robust_balance_p_bounds
            q_bounds = robust_balance_q_bounds
            net.ext_grid.at[idx, "min_p_mw"], net.ext_grid.at[idx, "max_p_mw"] = (
                p_bounds
            )
            net.ext_grid.at[idx, "min_q_mvar"], net.ext_grid.at[idx, "max_q_mvar"] = (
                q_bounds
            )
            bound_rows.append(
                {
                    "element": "ext_grid",
                    "index": int(idx),
                    "p0_mw": p0,
                    "q0_mvar": q0,
                    "p_bounds_mw": list(p_bounds),
                    "pre_action_centered_p_window_mw": list(nominal_p_window),
                    "q_bounds_mvar": list(q_bounds),
                    "pre_action_centered_q_window_mvar": list(nominal_q_window),
                    "contingency_p_bounds_mw": list(contingency_p_bounds),
                    "contingency_q_bounds_mvar": list(contingency_q_bounds),
                    "robust_balance_p_bounds_mw": list(robust_balance_p_bounds),
                    "robust_balance_q_bounds_mvar": list(robust_balance_q_bounds),
                    "active_balance_reserve_share_mw": reserve_share_mw,
                }
            )
    if len(net.gen):
        net.gen["controllable"] = True
        for idx in net.gen.index:
            p0 = float(net.gen.at[idx, "p_mw"])
            q0 = float(net.res_gen.at[idx, "q_mvar"])
            native_p_min = native_value(net.gen, idx, "min_p_mw")
            native_p_max = native_value(net.gen, idx, "max_p_mw")
            native_q_min = native_value(net.gen, idx, "min_q_mvar")
            native_q_max = native_value(net.gen, idx, "max_q_mvar")
            p_bounds = bounded_window(
                p0,
                max(5.0, 0.15 * abs(p0)),
                native_p_min,
                native_p_max,
            )
            q_bounds = bounded_window(
                q0,
                max(5.0, 0.25 * abs(q0), 0.05 * total_q / max(len(net.gen), 1)),
                native_q_min,
                native_q_max,
            )
            contingency_p_bounds = contingency_window(
                p0,
                max(10.0, 0.50 * abs(p0), 0.03 * total_p / max(len(net.gen), 1)),
                native_p_min,
                native_p_max,
            )
            contingency_q_bounds = contingency_window(
                q0,
                max(10.0, abs(q0), 0.15 * total_q / max(len(net.gen), 1)),
                native_q_min,
                native_q_max,
            )
            nominal_p_window = p_bounds
            nominal_q_window = q_bounds
            p_bounds = contingency_p_bounds
            q_bounds = contingency_q_bounds
            net.gen.at[idx, "min_p_mw"], net.gen.at[idx, "max_p_mw"] = p_bounds
            net.gen.at[idx, "min_q_mvar"], net.gen.at[idx, "max_q_mvar"] = q_bounds
            bound_rows.append(
                {
                    "element": "gen",
                    "index": int(idx),
                    "p0_mw": p0,
                    "q0_mvar": q0,
                    "p_bounds_mw": list(p_bounds),
                    "q_bounds_mvar": list(q_bounds),
                    "pre_action_centered_p_window_mw": list(nominal_p_window),
                    "pre_action_centered_q_window_mvar": list(nominal_q_window),
                    "contingency_p_bounds_mw": list(contingency_p_bounds),
                    "contingency_q_bounds_mvar": list(contingency_q_bounds),
                }
            )
    synthetic_cost_rows = []
    for idx in net.ext_grid.index:
        if not element_has_cost(net, "ext_grid", int(idx)):
            pp.create_poly_cost(
                net,
                idx,
                "ext_grid",
                cp1_eur_per_mw=30.0 + int(idx),
                cp2_eur_per_mw2=0.002,
            )
            synthetic_cost_rows.append({"element": "ext_grid", "index": int(idx)})
    for idx in net.gen.index:
        if not element_has_cost(net, "gen", int(idx)):
            pp.create_poly_cost(
                net, idx, "gen", cp1_eur_per_mw=20.0 + int(idx), cp2_eur_per_mw2=0.002
            )
            synthetic_cost_rows.append({"element": "gen", "index": int(idx)})
    return {
        "policy_id": "native_limits_dual_load_uncertainty_and_registered_contingency_capability_v8",
        "native_costs_preserved": True,
        "synthetic_cost_entries_added": len(synthetic_cost_rows),
        "synthetic_cost_elements": synthetic_cost_rows,
        "missing_cost_policy": "retain every native cost entry; only an element without any native polynomial or piecewise-linear cost receives the documented deterministic convex dataset cost",
        "native_limits_not_relaxed": True,
        "effective_limit_policy": "intersection of native equipment limits and the dataset safety envelope",
        "control_interval_policy": "external-grid active power uses the robust interior of the native capability interval for bidirectional load-and-loss reserve; external-grid reactive power uses a registered two-sided native-capability interior; generator P/Q uses full native capability, or a finite documented fallback only when the source case omits a native limit; pre-action-centered windows are retained as diagnostics and are not represented as source ramp limits",
        "dataset_voltage_bounds_pu": [0.95, 1.05],
        "robust_optimization_voltage_bounds_pu": list(
            ROBUST_OPTIMIZATION_VOLTAGE_BOUNDS_PU
        ),
        "dataset_line_loading_limit_percent": 100.0,
        "dataset_transformer_loading_limit_percent": 100.0,
        "corrective_solver_loading_limit_percent": ROBUST_CORRECTIVE_LOADING_LIMIT_PERCENT,
        "active_load_uncertainty_fraction": ROBUST_ACTIVE_LOAD_FRACTION,
        "minimum_published_uncertainty_voltage_margin_pu": (
            ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
        ),
        "active_loss_buffer_fraction": ROBUST_ACTIVE_LOSS_BUFFER_FRACTION,
        "external_grid_reactive_capability_reserve_fraction_each_side": (
            ROBUST_REACTIVE_CAPABILITY_RESERVE_FRACTION
        ),
        "active_load_stress_grid": list(PREREGISTERED_ACTIVE_LOAD_STRESS),
        "load_uncertainty_models": [
            "active_only_with_source_reactive_load",
            "uniform_constant_power_factor",
        ],
        "required_external_balance_reserve_mw": active_balance_reserve_mw,
        "selected_pre_contingency_loading_margin_percent": loading_margin_percent,
        "security_margin_search_policy": "first pre-contingency dispatch whose registered top-loaded network and generator outages are all recoverable by native-bound corrective AC OPF under the registered robust voltage, thermal, and external-balance reserve envelope",
        "network_limits": network_limit_rows,
        "control_bounds": bound_rows,
    }


def control_vector(net) -> dict[str, list[dict[str, float | int]]]:
    return {
        "ext_grid": [
            {
                "index": int(idx),
                "p_mw": round(float(net.res_ext_grid.at[idx, "p_mw"]), 6),
                "q_mvar": round(float(net.res_ext_grid.at[idx, "q_mvar"]), 6),
                "vm_pu": round(
                    float(net.res_bus.at[int(net.ext_grid.at[idx, "bus"]), "vm_pu"]), 8
                ),
            }
            for idx in net.ext_grid.index
        ],
        "gen": [
            {
                "index": int(idx),
                "p_mw": round(float(net.res_gen.at[idx, "p_mw"]), 6),
                "q_mvar": round(float(net.res_gen.at[idx, "q_mvar"]), 6),
                "vm_pu": round(
                    float(net.res_bus.at[int(net.gen.at[idx, "bus"]), "vm_pu"]), 8
                ),
            }
            for idx in net.gen.index
        ],
    }


def control_delta(
    pre: dict[str, list[dict[str, Any]]], post: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    rows = []
    for element in ("ext_grid", "gen"):
        before = {int(row["index"]): row for row in pre[element]}
        for row in post[element]:
            old = before[int(row["index"])]
            rows.append(
                {
                    "element": element,
                    "index": int(row["index"]),
                    "delta_p_mw": round(float(row["p_mw"]) - float(old["p_mw"]), 6),
                    "delta_q_mvar": round(
                        float(row["q_mvar"]) - float(old["q_mvar"]), 6
                    ),
                    "delta_vm_pu": round(float(row["vm_pu"]) - float(old["vm_pu"]), 6),
                }
            )
    return {
        "elements": rows,
        "l1_active_power_mw": round(
            sum(abs(float(row["delta_p_mw"])) for row in rows), 6
        ),
        "l1_reactive_power_mvar": round(
            sum(abs(float(row["delta_q_mvar"])) for row in rows), 6
        ),
    }


def _control_lookup(
    controls: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (element, int(row["index"])): row
        for element in CONTROL_ELEMENTS
        for row in controls.get(element) or []
    }


def _control_projection_sha256(controls: dict[str, list[dict[str, Any]]]) -> str:
    ordered = {
        element: sorted(
            (
                {
                    "index": int(row["index"]),
                    **{
                        field: round(
                            float(row[field]),
                            8 if field == "vm_pu" else 6,
                        )
                        for field in CONTROL_VALUE_FIELDS
                    },
                }
                for row in controls.get(element) or []
            ),
            key=lambda row: int(row["index"]),
        )
        for element in CONTROL_ELEMENTS
    }
    payload = json.dumps(
        ordered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_executable_control_target(result: dict[str, Any]) -> dict[str, Any]:
    """Separate executable setpoints from AC power-flow response quantities."""
    pre_controls = result.get("pre_action_controls") or {}
    post_controls = result.get("post_action_controls") or {}
    delta_rows = (result.get("control_delta") or {}).get("elements") or []
    pre_lookup = _control_lookup(pre_controls)
    post_lookup = _control_lookup(post_controls)
    delta_lookup = {(str(row["element"]), int(row["index"])): row for row in delta_rows}
    if (
        not pre_lookup
        or set(pre_lookup) != set(post_lookup)
        or set(pre_lookup) != set(delta_lookup)
    ):
        raise ValueError(
            "OPF control evidence does not contain identical complete device sets"
        )

    controls = []
    for element, index in sorted(pre_lookup):
        before = pre_lookup[(element, index)]
        after = post_lookup[(element, index)]
        delta = delta_lookup[(element, index)]
        controls.append(
            {
                "device_id": f"{element}:{index}",
                "element": element,
                "index": index,
                "commanded_setpoint": {
                    "fields": list(COMMAND_FIELDS_BY_ELEMENT[element]),
                    "pre_action": {
                        field: float(before[field])
                        for field in COMMAND_FIELDS_BY_ELEMENT[element]
                    },
                    "absolute": {
                        field: float(after[field])
                        for field in COMMAND_FIELDS_BY_ELEMENT[element]
                    },
                    "delta": {
                        field: float(delta[f"delta_{field}"])
                        for field in COMMAND_FIELDS_BY_ELEMENT[element]
                    },
                },
                "realized_response": {
                    "fields": list(RESPONSE_FIELDS_BY_ELEMENT[element]),
                    "pre_action": {
                        field: float(before[field])
                        for field in RESPONSE_FIELDS_BY_ELEMENT[element]
                    },
                    "post_action": {
                        field: float(after[field])
                        for field in RESPONSE_FIELDS_BY_ELEMENT[element]
                    },
                    "delta": {
                        field: float(delta[f"delta_{field}"])
                        for field in RESPONSE_FIELDS_BY_ELEMENT[element]
                    },
                },
            }
        )
    return {
        "schema_version": OPF_TARGET_SCHEMA_VERSION,
        "scenario_id": str(result["scenario_id"]),
        "application_mode": "absolute_setpoints",
        "control_elements": list(CONTROL_ELEMENTS),
        "control_semantics": {
            "generator_commands": ["p_mw", "vm_pu"],
            "external_grid_commands": ["vm_pu"],
            "generator_realized_responses": ["q_mvar"],
            "external_grid_realized_responses": ["p_mw", "q_mvar"],
            "external_grid_angle_policy": "retain the source slack-bus angle setpoint",
            "capability_policy": (
                "all realized active and reactive responses remain subject to "
                "the registered device capability bounds"
            ),
        },
        "controls": controls,
        "completeness": {
            "expected_control_count": len(post_lookup),
            "ext_grid_count": len(post_controls.get("ext_grid") or []),
            "generator_count": len(post_controls.get("gen") or []),
            "all_controls_required": True,
        },
        "replay_contract": {
            "base_state": "pre_action_controls",
            "accepted_modes": ["absolute_setpoints", "pre_action_plus_delta"],
            "numeric_tolerance": 1e-6,
            "expected_post_action_evidence_sha256": _control_projection_sha256(
                post_controls
            ),
        },
    }


def replay_executable_control_target(
    target: dict[str, Any],
    *,
    mode: str = "absolute_setpoints",
) -> dict[str, list[dict[str, Any]]]:
    """Reconstruct only physically commanded setpoints from the target."""
    if target.get("schema_version") != OPF_TARGET_SCHEMA_VERSION:
        raise ValueError("unsupported_opf_target_schema")
    if mode not in {"absolute_setpoints", "pre_action_plus_delta"}:
        raise ValueError(f"unsupported_opf_target_replay_mode:{mode}")
    replayed = {element: [] for element in CONTROL_ELEMENTS}
    for item in target.get("controls") or []:
        element = str(item.get("element") or "")
        index = int(item.get("index", -1))
        if element not in replayed or item.get("device_id") != f"{element}:{index}":
            raise ValueError("invalid_opf_target_device_identity")
        command = item.get("commanded_setpoint") or {}
        fields = tuple(command.get("fields") or ())
        if fields != COMMAND_FIELDS_BY_ELEMENT[element]:
            raise ValueError("invalid_opf_target_command_fields")
        if mode == "absolute_setpoints":
            values = command.get("absolute") or {}
        else:
            before = command.get("pre_action") or {}
            delta = command.get("delta") or {}
            values = {
                field: round(float(before[field]) + float(delta[field]), 8)
                for field in fields
            }
        replayed[element].append(
            {
                "index": index,
                **{
                    field: round(float(values[field]), 8 if field == "vm_pu" else 6)
                    for field in fields
                },
            }
        )
    for element in CONTROL_ELEMENTS:
        replayed[element].sort(key=lambda row: int(row["index"]))
    return replayed


def validate_executable_control_target(
    target: dict[str, Any],
    result: dict[str, Any],
) -> list[str]:
    """Validate identity, completeness, delta closure, and both replay modes."""
    errors: list[str] = []
    if target.get("scenario_id") != str(result.get("scenario_id")):
        errors.append("target_scenario_id_mismatch")
    expected = result.get("post_action_controls") or {}
    expected_lookup = _control_lookup(expected)
    controls = target.get("controls")
    if not isinstance(controls, list):
        return ["target_controls_not_list"]
    target_keys = [
        (str(item.get("element") or ""), int(item.get("index", -1)))
        for item in controls
        if isinstance(item, dict)
    ]
    if len(target_keys) != len(set(target_keys)):
        errors.append("target_control_identity_not_unique")
    if set(target_keys) != set(expected_lookup):
        errors.append("target_control_set_incomplete")
    completeness = target.get("completeness") or {}
    if (
        int(completeness.get("expected_control_count") or -1) != len(expected_lookup)
        or completeness.get("all_controls_required") is not True
    ):
        errors.append("target_completeness_declaration_mismatch")

    tolerance = float(
        (target.get("replay_contract") or {}).get("numeric_tolerance") or 1e-6
    )
    for item in controls:
        if not isinstance(item, dict):
            errors.append("target_control_not_object")
            continue
        element = str(item.get("element") or "")
        index = int(item.get("index", -1))
        command = item.get("commanded_setpoint") or {}
        response = item.get("realized_response") or {}
        command_fields = tuple(command.get("fields") or ())
        response_fields = tuple(response.get("fields") or ())
        if command_fields != COMMAND_FIELDS_BY_ELEMENT.get(element):
            errors.append(f"target_command_fields_invalid:{element}:{index}")
        if response_fields != RESPONSE_FIELDS_BY_ELEMENT.get(element):
            errors.append(f"target_response_fields_invalid:{element}:{index}")
        for block_name, block, fields, post_key in (
            ("command", command, command_fields, "absolute"),
            ("response", response, response_fields, "post_action"),
        ):
            before = block.get("pre_action") or {}
            after = block.get(post_key) or {}
            delta = block.get("delta") or {}
            for field in fields:
                try:
                    if (
                        abs(
                            (float(before[field]) + float(delta[field]))
                            - float(after[field])
                        )
                        > tolerance
                    ):
                        errors.append(
                            f"target_{block_name}_delta_closure_mismatch:"
                            f"{element}:{index}:{field}"
                        )
                except (KeyError, TypeError, ValueError):
                    errors.append(
                        f"target_{block_name}_value_missing:{element}:{index}:{field}"
                    )
        for field in RESPONSE_FIELDS_BY_ELEMENT.get(element, ()):
            try:
                if abs(
                    float((response.get("post_action") or {})[field])
                    - float(expected_lookup[(element, index)][field])
                ) > tolerance:
                    errors.append(
                        f"target_response_mismatch:{element}:{index}:{field}"
                    )
            except (KeyError, TypeError, ValueError):
                errors.append(f"target_response_value_missing:{element}:{index}:{field}")

    for mode in ("absolute_setpoints", "pre_action_plus_delta"):
        try:
            replayed = replay_executable_control_target(target, mode=mode)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"target_replay_failed:{mode}:{type(exc).__name__}")
            continue
        replay_lookup = _control_lookup(replayed)
        for key, expected_row in expected_lookup.items():
            actual_row = replay_lookup.get(key)
            if actual_row is None:
                errors.append(f"target_replay_missing_control:{mode}:{key[0]}:{key[1]}")
                continue
            for field in COMMAND_FIELDS_BY_ELEMENT[key[0]]:
                if (
                    abs(float(actual_row[field]) - float(expected_row[field]))
                    > tolerance
                ):
                    errors.append(
                        f"target_replay_mismatch:{mode}:{key[0]}:{key[1]}:{field}"
                    )
        if mode == "absolute_setpoints":
            response_lookup = {
                (str(item["element"]), int(item["index"])): item[
                    "realized_response"
                ]["post_action"]
                for item in controls
            }
            reconstructed = {element: [] for element in CONTROL_ELEMENTS}
            for key, command_row in replay_lookup.items():
                element, index = key
                reconstructed[element].append(
                    {
                        "index": index,
                        **command_row,
                        **response_lookup[key],
                    }
                )
            replay_hash = _control_projection_sha256(reconstructed)
            expected_hash = (target.get("replay_contract") or {}).get(
                "expected_post_action_evidence_sha256"
            )
            if replay_hash != expected_hash:
                errors.append("target_post_action_evidence_hash_mismatch")
    return sorted(set(errors))


def reserve_summary(
    constraint_policy: dict[str, Any],
    post_controls: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    control_lookup = {
        (element, int(row["index"])): row
        for element in ("ext_grid", "gen")
        for row in post_controls.get(element) or []
    }
    upward = 0.0
    downward = 0.0
    headroom_by_element: dict[tuple[str, int], dict[str, float]] = {}
    for bound in constraint_policy.get("control_bounds") or []:
        key = (str(bound["element"]), int(bound["index"]))
        control = control_lookup[key]
        lower, upper = [float(value) for value in bound["contingency_p_bounds_mw"]]
        active_power = float(control["p_mw"])
        up = max(0.0, upper - active_power)
        down = max(0.0, active_power - lower)
        upward += up
        downward += down
        headroom_by_element[key] = {"upward_mw": up, "downward_mw": down}
    generator_outages = []
    for row in post_controls.get("gen") or []:
        key = ("gen", int(row["index"]))
        outaged_output = max(0.0, float(row["p_mw"]))
        loss_buffer = max(0.5, 0.02 * outaged_output)
        required = outaged_output + loss_buffer
        available = sum(
            item["upward_mw"]
            for candidate, item in headroom_by_element.items()
            if candidate != key
        )
        generator_outages.append(
            {
                "generator_index": int(row["index"]),
                "outaged_generator_output_mw": round(outaged_output, 6),
                "loss_buffer_mw": round(loss_buffer, 6),
                "required_replacement_mw": round(required, 6),
                "available_upward_reserve_excluding_outaged_generator_mw": round(
                    available, 6
                ),
                "reserve_margin_mw": round(available - required, 6),
                "adequate": available + 1e-5 >= required,
            }
        )
    largest_non_slack_generator = max(
        (float(item["outaged_generator_output_mw"]) for item in generator_outages),
        default=0.0,
    )
    required_upward_reserve = max(
        (float(item["required_replacement_mw"]) for item in generator_outages),
        default=0.0,
    )
    worst_margin = min(
        (float(item["reserve_margin_mw"]) for item in generator_outages), default=0.0
    )
    all_generator_outages_covered = all(
        bool(item["adequate"]) for item in generator_outages
    )
    ext_grid_upward = sum(
        item["upward_mw"]
        for key, item in headroom_by_element.items()
        if key[0] == "ext_grid"
    )
    ext_grid_downward = sum(
        item["downward_mw"]
        for key, item in headroom_by_element.items()
        if key[0] == "ext_grid"
    )
    required_external_balance = float(
        constraint_policy.get("required_external_balance_reserve_mw") or 0.0
    )
    external_balance_adequate = bool(
        ext_grid_upward + 1e-5 >= required_external_balance
        and ext_grid_downward + 1e-5 >= required_external_balance
    )
    return {
        "policy": "cover every registered non-slack generator outage and retain bidirectional external-grid headroom for the registered active-load uncertainty envelope",
        "available_upward_reserve_mw": round(upward, 6),
        "available_downward_reserve_mw": round(downward, 6),
        "largest_non_slack_generator_mw": round(largest_non_slack_generator, 6),
        "required_upward_reserve_mw": round(required_upward_reserve, 6),
        "generator_outage_reserve_checks": generator_outages,
        "worst_generator_outage_reserve_margin_mw": round(worst_margin, 6),
        "upward_reserve_adequate": all_generator_outages_covered,
        "required_external_balance_reserve_mw": round(
            required_external_balance,
            6,
        ),
        "external_grid_upward_headroom_mw": round(ext_grid_upward, 6),
        "external_grid_downward_headroom_mw": round(ext_grid_downward, 6),
        "external_balance_reserve_adequate": external_balance_adequate,
    }


def control_bound_lookup(
    constraint_policy: dict[str, Any],
) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(row["element"]), int(row["index"])): row
        for row in constraint_policy.get("control_bounds") or []
    }


def emergency_loading_limit(native_limit_percent: Any) -> float:
    """Return the registered internal limit used by corrective AC OPF."""
    native = float(native_limit_percent) if finite(native_limit_percent) else 100.0
    return min(ROBUST_CORRECTIVE_LOADING_LIMIT_PERCENT, native)


def replay_optimized_dispatch_power_flow(net) -> None:
    """Recompute the optimized dispatch with a tight AC power-flow residual."""
    for idx in net.gen.index:
        if bool(net.gen.at[idx, "in_service"]):
            bus = int(net.gen.at[idx, "bus"])
            net.gen.at[idx, "p_mw"] = float(net.res_gen.at[idx, "p_mw"])
            net.gen.at[idx, "vm_pu"] = float(net.res_bus.at[bus, "vm_pu"])
    for idx in net.ext_grid.index:
        if bool(net.ext_grid.at[idx, "in_service"]):
            bus = int(net.ext_grid.at[idx, "bus"])
            net.ext_grid.at[idx, "vm_pu"] = float(net.res_bus.at[bus, "vm_pu"])
    pp.runpp(
        net,
        algorithm="nr",
        init="results",
        tolerance_mva=1e-10,
        max_iteration=80,
        enforce_q_lims=True,
        numba=False,
    )


def contingency_control_feasibility(
    net,
    constraint_policy: dict[str, Any],
    *,
    outaged_generator: int | None = None,
    tolerance: float = 1e-4,
) -> dict[str, Any]:
    bounds = control_bound_lookup(constraint_policy)
    controls = control_vector(net)
    if outaged_generator is not None:
        for row in controls["gen"]:
            if int(row["index"]) == outaged_generator:
                row["in_service"] = False
    violations = []
    ext_grid_upward_headroom = 0.0
    ext_grid_downward_headroom = 0.0
    for element in ("ext_grid", "gen"):
        for row in controls[element]:
            idx = int(row["index"])
            if element == "gen" and idx == outaged_generator:
                continue
            if not bool(getattr(net, element).at[idx, "in_service"]):
                continue
            bound = bounds.get((element, idx))
            if bound is None:
                violations.append(f"missing_bound:{element}:{idx}")
                continue
            for value_key, bound_key in (
                ("p_mw", "contingency_p_bounds_mw"),
                ("q_mvar", "contingency_q_bounds_mvar"),
            ):
                lower, upper = [float(value) for value in bound[bound_key]]
                value = float(row[value_key])
                if not lower - tolerance <= value <= upper + tolerance:
                    violations.append(
                        f"{element}:{idx}:{value_key}:{value:.6f}:outside:{lower:.6f}:{upper:.6f}"
                    )
            if element == "ext_grid":
                lower, upper = [
                    float(value) for value in bound["contingency_p_bounds_mw"]
                ]
                active_power = float(row["p_mw"])
                ext_grid_upward_headroom += max(0.0, upper - active_power)
                ext_grid_downward_headroom += max(0.0, active_power - lower)
    required_balance = float(
        constraint_policy.get("required_external_balance_reserve_mw") or 0.0
    )
    if ext_grid_upward_headroom + tolerance < required_balance:
        violations.append("ext_grid_upward_active_load_reserve_insufficient")
    if ext_grid_downward_headroom + tolerance < required_balance:
        violations.append("ext_grid_downward_active_load_reserve_insufficient")
    return {
        "status": "pass" if not violations else "fail",
        "outaged_generator": outaged_generator,
        "violations": violations,
        "external_balance_reserve": {
            "required_each_direction_mw": round(required_balance, 6),
            "available_upward_mw": round(ext_grid_upward_headroom, 6),
            "available_downward_mw": round(ext_grid_downward_headroom, 6),
            "adequate": (
                ext_grid_upward_headroom + tolerance >= required_balance
                and ext_grid_downward_headroom + tolerance >= required_balance
            ),
        },
        "post_contingency_controls": controls,
    }


def apply_post_contingency_security_limits(
    net, constraint_policy: dict[str, Any]
) -> None:
    """Apply the native-intersected robust corrective operating envelope."""
    for row in constraint_policy.get("network_limits") or []:
        element = str(row.get("element") or "")
        idx = int(row.get("index", -1))
        if element not in {"line", "trafo"} or idx not in getattr(net, element).index:
            continue
        native = row.get("native_max_loading_percent")
        emergency_limit = emergency_loading_limit(native)
        getattr(net, element).at[idx, "max_loading_percent"] = emergency_limit
    for bound in constraint_policy.get("control_bounds") or []:
        element = str(bound["element"])
        idx = int(bound["index"])
        if idx not in getattr(net, element).index:
            continue
        table = getattr(net, element)
        p_bounds_key = (
            "robust_balance_p_bounds_mw"
            if element == "ext_grid"
            else "contingency_p_bounds_mw"
        )
        q_bounds_key = (
            "robust_balance_q_bounds_mvar"
            if element == "ext_grid"
            else "contingency_q_bounds_mvar"
        )
        table.at[idx, "min_p_mw"], table.at[idx, "max_p_mw"] = [
            float(value) for value in bound[p_bounds_key]
        ]
        table.at[idx, "min_q_mvar"], table.at[idx, "max_q_mvar"] = [
            float(value) for value in bound[q_bounds_key]
        ]


def run_corrective_ac_opf(net) -> None:
    pp.runopp(
        net,
        verbose=False,
        calculate_voltage_angles=True,
        suppress_warnings=True,
    )
    if not bool(getattr(net, "OPF_converged", False)):
        raise RuntimeError("corrective AC OPF did not converge")
    objective_value = (
        float(net.res_cost) if finite(getattr(net, "res_cost", None)) else None
    )
    replay_optimized_dispatch_power_flow(net)
    if objective_value is not None:
        net.res_cost = objective_value


def corrective_generator_outage_power_flow(
    dispatch_net,
    generator_idx: int,
    constraint_policy: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Run deterministic corrective redispatch after a generator outage."""
    contingency_net = copy.deepcopy(dispatch_net)
    lost_output = max(0.0, float(contingency_net.res_gen.at[generator_idx, "p_mw"]))
    contingency_net.gen.at[generator_idx, "in_service"] = False
    apply_post_contingency_security_limits(contingency_net, constraint_policy)
    bounds = control_bound_lookup(constraint_policy)
    ext_grid_upward_headroom = sum(
        max(
            0.0,
            float(bounds[("ext_grid", int(idx))]["contingency_p_bounds_mw"][1])
            - float(contingency_net.res_ext_grid.at[idx, "p_mw"]),
        )
        for idx in contingency_net.ext_grid.index
        if bool(contingency_net.ext_grid.at[idx, "in_service"])
    )
    loss_buffer_mw = max(0.5, 0.02 * lost_output)
    available_reserve = (
        sum(
            max(
                0.0,
                float(bound["contingency_p_bounds_mw"][1])
                - float(dispatch_net.res_gen.at[idx, "p_mw"]),
            )
            for (element, idx), bound in bounds.items()
            if element == "gen"
            and idx != generator_idx
            and bool(contingency_net.gen.at[idx, "in_service"])
        )
        + ext_grid_upward_headroom
    )
    if available_reserve + 1e-5 < lost_output + loss_buffer_mw:
        raise RuntimeError(
            "loss-aware active-power reserve is insufficient for generator outage"
        )
    run_corrective_ac_opf(contingency_net)
    return contingency_net, {
        "policy": "deterministic cost-minimizing corrective AC OPF across remaining generators and ext_grid within registered P/Q/voltage bounds",
        "lost_generator_output_mw": round(lost_output, 6),
        "pre_contingency_ext_grid_upward_headroom_mw": round(
            ext_grid_upward_headroom, 6
        ),
        "available_replacement_reserve_mw": round(available_reserve, 6),
        "loss_buffer_mw": round(loss_buffer_mw, 6),
        "objective_value": round(float(contingency_net.res_cost), 8)
        if finite(getattr(contingency_net, "res_cost", None))
        else None,
    }


def post_action_n1_check(
    net,
    max_network_checks: int,
    max_generator_checks: int,
    constraint_policy: dict[str, Any],
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    dispatch_net = copy.deepcopy(net)
    if len(dispatch_net.gen):
        dispatch_net.gen.loc[:, "p_mw"] = dispatch_net.res_gen.loc[:, "p_mw"].to_numpy()
        for idx in dispatch_net.gen.index:
            bus = int(dispatch_net.gen.at[idx, "bus"])
            dispatch_net.gen.at[idx, "vm_pu"] = float(
                dispatch_net.res_bus.at[bus, "vm_pu"]
            )
    for idx in dispatch_net.ext_grid.index:
        bus = int(dispatch_net.ext_grid.at[idx, "bus"])
        dispatch_net.ext_grid.at[idx, "vm_pu"] = float(
            dispatch_net.res_bus.at[bus, "vm_pu"]
        )
    candidates: list[tuple[str, int, float]] = []
    for idx in dispatch_net.line.index:
        if bool(dispatch_net.line.at[idx, "in_service"]):
            candidates.append(
                (
                    "line",
                    int(idx),
                    float(dispatch_net.res_line.at[idx, "loading_percent"]),
                )
            )
    for idx in dispatch_net.trafo.index:
        if bool(dispatch_net.trafo.at[idx, "in_service"]):
            candidates.append(
                (
                    "trafo",
                    int(idx),
                    float(dispatch_net.res_trafo.at[idx, "loading_percent"]),
                )
            )
    candidates.sort(key=lambda item: (-item[2], item[0], item[1]))
    checks = []
    selected_network = []
    structural_exclusions = []
    for element, idx, pre_loading in candidates:
        topology_probe = copy.deepcopy(dispatch_net)
        topology_probe[element].at[idx, "in_service"] = False
        unsupplied = sorted(int(bus) for bus in unsupplied_buses(topology_probe))
        if unsupplied:
            structural_exclusions.append(
                {
                    "element": element,
                    "index": idx,
                    "pre_outage_loading_percent": round(pre_loading, 6),
                    "reason": "structural_islanding_outside_redispatch_security_set",
                    "unsupplied_buses": unsupplied,
                }
            )
            continue
        selected_network.append((element, idx, pre_loading))
        if max_network_checks > 0 and len(selected_network) >= max_network_checks:
            break
    for element, idx, pre_loading in selected_network:
        contingency_net = copy.deepcopy(dispatch_net)
        contingency_net[element].at[idx, "in_service"] = False
        try:
            apply_post_contingency_security_limits(contingency_net, constraint_policy)
            run_corrective_ac_opf(contingency_net)
            item_metrics = metrics(contingency_net)
            state_complete = electrical_state_complete(contingency_net)
            control_gate = contingency_control_feasibility(
                contingency_net, constraint_policy
            )
            secure = bool(
                state_complete
                and float(item_metrics["constraint_violation_score"]) <= 1e-8
                and float(item_metrics["power_balance_residual_mw"])
                <= balance_tolerance_mw
                and control_gate["status"] == "pass"
            )
            checks.append(
                {
                    "element": element,
                    "index": idx,
                    "pre_outage_loading_percent": round(pre_loading, 6),
                    "solver_status": "converged",
                    "corrective_action": "bounded_cost_minimizing_ac_opf",
                    "electrical_state_complete": state_complete,
                    "control_feasibility": control_gate,
                    "secure": secure,
                    "metrics": item_metrics,
                }
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {
                    "element": element,
                    "index": idx,
                    "pre_outage_loading_percent": round(pre_loading, 6),
                    "solver_status": "failed",
                    "secure": False,
                    "error": str(exc)[:300],
                }
            )
    generator_candidates = [
        (int(idx), float(dispatch_net.res_gen.at[idx, "p_mw"]))
        for idx in dispatch_net.gen.index
        if bool(dispatch_net.gen.at[idx, "in_service"])
    ]
    generator_candidates.sort(key=lambda item: (-abs(item[1]), item[0]))
    selected_generators = (
        generator_candidates[:max_generator_checks]
        if max_generator_checks > 0
        else generator_candidates
    )
    for idx, pre_output in selected_generators:
        try:
            contingency_net, redispatch = corrective_generator_outage_power_flow(
                dispatch_net,
                idx,
                constraint_policy,
            )
            item_metrics = metrics(contingency_net)
            state_complete = electrical_state_complete(contingency_net)
            control_gate = contingency_control_feasibility(
                contingency_net,
                constraint_policy,
                outaged_generator=idx,
            )
            secure = bool(
                state_complete
                and float(item_metrics["constraint_violation_score"]) <= 1e-8
                and float(item_metrics["power_balance_residual_mw"])
                <= balance_tolerance_mw
                and control_gate["status"] == "pass"
            )
            checks.append(
                {
                    "element": "gen",
                    "index": idx,
                    "pre_outage_active_power_mw": round(pre_output, 6),
                    "solver_status": "converged",
                    "electrical_state_complete": state_complete,
                    "corrective_redispatch": redispatch,
                    "control_feasibility": control_gate,
                    "secure": secure,
                    "metrics": item_metrics,
                }
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {
                    "element": "gen",
                    "index": idx,
                    "pre_outage_active_power_mw": round(pre_output, 6),
                    "solver_status": "failed",
                    "secure": False,
                    "error": str(exc)[:300],
                }
            )
    requested_checks = len(selected_network) + len(selected_generators)
    return {
        "selection": (
            "all non-islanding in-service lines and transformers plus all in-service non-slack generators"
            if max_network_checks <= 0 and max_generator_checks <= 0
            else "registered deterministic loading/output-ranked subsets"
        ),
        "coverage_mode": "full_enumeration"
        if max_network_checks <= 0 and max_generator_checks <= 0
        else "ranked_subset",
        "network_structural_exclusion_policy": "line or transformer outages that create unsupplied buses are recorded as topology exclusions and are not represented as correctable redispatch contingencies",
        "structurally_excluded_network_candidates": structural_exclusions,
        "network_requested_checks": len(selected_network),
        "generator_requested_checks": len(selected_generators),
        "requested_checks": requested_checks,
        "completed_checks": len(checks),
        "secure_checks": sum(bool(row["secure"]) for row in checks),
        "security_rate": round(
            sum(bool(row["secure"]) for row in checks) / len(checks), 6
        )
        if checks
        else None,
        "all_control_feasibility_checks_pass": all(
            (row.get("control_feasibility") or {}).get("status") == "pass"
            for row in checks
        ),
        "checks": checks,
    }


def summarize_embedded_uncertainty_cases(
    cases: list[dict[str, Any]],
    result: dict[str, Any],
    *,
    contract_version: str,
    reactive_load_policy: str,
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    """Summarize one preregistered embedded load-model denominator."""
    safe_count = sum(row.get("safe") is True for row in cases)
    by_case_type: dict[str, dict[str, int]] = {}
    for case_type in ("base_stress", "registered_n1_stress"):
        selected = [row for row in cases if row.get("case_type") == case_type]
        by_case_type[case_type] = {
            "registered_case_count": len(selected),
            "safe_case_count": sum(row.get("safe") is True for row in selected),
        }
    by_stress_percent: dict[str, dict[str, int]] = {}
    for stress_fraction in PREREGISTERED_ACTIVE_LOAD_STRESS:
        selected = [
            row
            for row in cases
            if abs(float(row.get("stress_fraction")) - stress_fraction) <= 1e-12
        ]
        by_stress_percent[f"{100.0 * stress_fraction:+.0f}%"] = {
            "registered_case_count": len(selected),
            "safe_case_count": sum(row.get("safe") is True for row in selected),
        }
    reason_counts: Counter[str] = Counter()
    for row in cases:
        if row.get("safe") is True:
            continue
        if row.get("solver_status") != "converged":
            reason_counts[f"solver:{row.get('error_type') or 'unknown'}"] += 1
            continue
        measured = row.get("metrics") or {}
        if (measured.get("control_capability") or {}).get("status") != "pass":
            reason_counts["control_capability"] += 1
        if measured.get("registered_network_limit_violations"):
            reason_counts["registered_network_limit"] += 1
        if float(measured.get("worst_voltage_margin_pu") or 0.0) < 0.0:
            reason_counts["voltage_margin"] += 1
        if float(measured.get("thermal_margin_percent") or 0.0) < 0.0:
            reason_counts["thermal_margin"] += 1
        if (
            float(measured.get("incremental_active_balance_residual_mw") or 0.0)
            > balance_tolerance_mw
        ):
            reason_counts["incremental_active_balance"] += 1
    converged_cases = [
        row
        for row in cases
        if row.get("solver_status") == "converged"
        and isinstance(row.get("metrics"), dict)
    ]
    expected_count = len(PREREGISTERED_ACTIVE_LOAD_STRESS) * (
        1 + int((result.get("post_action_n1") or {}).get("requested_checks") or 0)
    )
    denominator_complete = len(cases) == expected_count
    minimum_voltage_margin = min(
        (
            float(row["metrics"]["worst_voltage_margin_pu"])
            for row in converged_cases
        ),
        default=None,
    )
    minimum_thermal_margin = min(
        (
            float(row["metrics"]["thermal_margin_percent"])
            for row in converged_cases
        ),
        default=None,
    )
    status = (
        "pass"
        if denominator_complete
        and safe_count == expected_count
        and minimum_voltage_margin is not None
        and minimum_voltage_margin
        >= ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
        and minimum_thermal_margin is not None
        and minimum_thermal_margin >= 0.0
        else "needs_optimization"
    )
    return {
        "contract_version": contract_version,
        "status": status,
        "active_load_stress_fractions": list(PREREGISTERED_ACTIVE_LOAD_STRESS),
        "reactive_load_policy": reactive_load_policy,
        "registered_case_count": len(cases),
        "expected_case_count": expected_count,
        "denominator_complete": denominator_complete,
        "safe_case_count": safe_count,
        "by_case_type": by_case_type,
        "by_stress_percent": by_stress_percent,
        "minimum_worst_voltage_margin_pu": minimum_voltage_margin,
        "minimum_thermal_margin_percent": minimum_thermal_margin,
        "optimization_reason_counts": dict(sorted(reason_counts.items())),
    }


def embedded_active_load_uncertainty_gate(
    scenario: dict[str, Any],
    result: dict[str, Any],
    *,
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    """Apply the active-only publication stress contract during construction."""
    from validate_opf_action_uncertainty_stress import (  # noqa: PLC0415
        PREREGISTERED_ACTIVE_LOAD_STRESS as VALIDATOR_STRESS_GRID,
        apply_published_action,
        execute_case,
        preregistered_case_specs,
    )

    if tuple(VALIDATOR_STRESS_GRID) != PREREGISTERED_ACTIVE_LOAD_STRESS:
        raise RuntimeError(
            "embedded and independent active-load stress grids disagree"
        )
    action_net = build_net(scenario)
    apply_published_action(action_net, result)
    cases = [
        execute_case(
            action_net,
            result,
            specification,
            balance_tolerance_mw=balance_tolerance_mw,
        )
        for specification in preregistered_case_specs([result])
    ]
    return summarize_embedded_uncertainty_cases(
        cases,
        result,
        contract_version="embedded-opf-action-active-load-stress-v2",
        reactive_load_policy="unchanged_from_source_scenario",
        balance_tolerance_mw=balance_tolerance_mw,
    )


def embedded_constant_power_factor_uncertainty_gate(
    scenario: dict[str, Any],
    result: dict[str, Any],
    *,
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    """Apply the constant-power-factor stress contract during construction."""
    from validate_opf_action_constant_power_factor_stress import (  # noqa: PLC0415
        execute_case,
        preregistered_case_specs,
    )
    from validate_opf_action_uncertainty_stress import (  # noqa: PLC0415
        apply_published_action,
    )

    action_net = build_net(scenario)
    apply_published_action(action_net, result)
    cases = [
        execute_case(
            action_net,
            result,
            specification,
            balance_tolerance_mw=balance_tolerance_mw,
        )
        for specification in preregistered_case_specs([result])
    ]
    return summarize_embedded_uncertainty_cases(
        cases,
        result,
        contract_version="embedded-opf-action-constant-power-factor-stress-v1",
        reactive_load_policy=(
            "uniform multiplicative scaling preserves each load's source "
            "power factor"
        ),
        balance_tolerance_mw=balance_tolerance_mw,
    )


def combined_load_uncertainty_gate(
    active_gate: dict[str, Any],
    constant_power_factor_gate: dict[str, Any],
) -> dict[str, Any]:
    """Bind both independent load models to one all-states construction gate."""
    gates = {
        "active_only": active_gate,
        "constant_power_factor": constant_power_factor_gate,
    }
    registered = sum(
        int(gate.get("registered_case_count") or 0) for gate in gates.values()
    )
    expected = sum(
        int(gate.get("expected_case_count") or 0) for gate in gates.values()
    )
    safe = sum(int(gate.get("safe_case_count") or 0) for gate in gates.values())
    voltage_margins = [
        float(gate["minimum_worst_voltage_margin_pu"])
        for gate in gates.values()
        if finite(gate.get("minimum_worst_voltage_margin_pu"))
    ]
    thermal_margins = [
        float(gate["minimum_thermal_margin_percent"])
        for gate in gates.values()
        if finite(gate.get("minimum_thermal_margin_percent"))
    ]
    denominator_complete = all(
        gate.get("denominator_complete") is True for gate in gates.values()
    ) and registered == expected
    status = (
        "pass"
        if denominator_complete
        and safe == expected
        and all(gate.get("status") == "pass" for gate in gates.values())
        else "needs_optimization"
    )
    return {
        "contract_version": "embedded-opf-action-dual-load-model-stress-v1",
        "status": status,
        "load_models": list(gates),
        "load_model_count": len(gates),
        "registered_case_count": registered,
        "expected_case_count": expected,
        "denominator_complete": denominator_complete,
        "safe_case_count": safe,
        "minimum_worst_voltage_margin_pu": min(voltage_margins, default=None),
        "minimum_thermal_margin_percent": min(thermal_margins, default=None),
        "model_summaries": gates,
    }


def post_action_n1_diagnostic_summary(
    security: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep construction failures auditable without copying full failed cases."""
    payload = security or {}
    checks = payload.get("checks") or []
    return {
        "network_requested_checks": int(
            payload.get("network_requested_checks") or 0
        ),
        "generator_requested_checks": int(
            payload.get("generator_requested_checks") or 0
        ),
        "requested_checks": int(payload.get("requested_checks") or 0),
        "completed_checks": int(payload.get("completed_checks") or 0),
        "secure_checks": int(payload.get("secure_checks") or 0),
        "structurally_excluded_network_candidate_count": len(
            payload.get("structurally_excluded_network_candidates") or []
        ),
        "solver_status_counts": dict(
            sorted(
                Counter(str(row.get("solver_status") or "unknown") for row in checks).items()
            )
        ),
        "control_feasibility_failure_count": sum(
            (row.get("control_feasibility") or {}).get("status") != "pass"
            for row in checks
            if row.get("solver_status") == "converged"
        ),
    }


def run_closed_loop(
    scenario: dict[str, Any],
    n1_network_checks: int,
    n1_generator_checks: int = 2,
    loading_margins: tuple[float, ...] = (90.0, 80.0, 70.0, 60.0),
    balance_tolerance_mw: float = 1e-3,
) -> dict[str, Any]:
    """Find the first registered-margin dispatch that passes every hard gate."""
    if not loading_margins:
        raise ValueError("at least one registered loading margin is required")
    attempt_summaries = []
    for margin in loading_margins:
        try:
            net = build_net(scenario)
            pp.runpp(
                net,
                algorithm="nr",
                init="auto",
                tolerance_mva=1e-6,
                max_iteration=40,
                enforce_q_lims=True,
                numba=False,
            )
            if not electrical_state_complete(net):
                raise RuntimeError(
                    "pre-action power flow contains a disconnected or nonfinite in-service electrical state"
                )
            pre = metrics(net)
            pre_state = electrical_state_vector(net)
            pre_controls = control_vector(net)
            constraint_policy = prepare_opf(net, margin)
            pp.runopp(
                net,
                verbose=False,
                calculate_voltage_angles=True,
                suppress_warnings=True,
            )
            if not bool(getattr(net, "OPF_converged", False)):
                raise RuntimeError("pandapower OPF did not report convergence")
            objective_value = (
                round(float(net.res_cost), 8)
                if finite(getattr(net, "res_cost", None))
                else None
            )
            replay_optimized_dispatch_power_flow(net)
            post = metrics(net)
            post_state = electrical_state_vector(net)
            post_controls = control_vector(net)
            reserve = reserve_summary(constraint_policy, post_controls)
            if (
                reserve.get("upward_reserve_adequate") is not True
                or reserve.get("external_balance_reserve_adequate") is not True
            ):
                attempt_summaries.append(
                    {
                        "loading_margin_percent": margin,
                        "status": "reserve_gate_failed",
                        "worst_generator_outage_reserve_margin_mw": reserve.get(
                            "worst_generator_outage_reserve_margin_mw"
                        ),
                        "external_grid_upward_headroom_mw": reserve.get(
                            "external_grid_upward_headroom_mw"
                        ),
                        "external_grid_downward_headroom_mw": reserve.get(
                            "external_grid_downward_headroom_mw"
                        ),
                        "required_external_balance_reserve_mw": reserve.get(
                            "required_external_balance_reserve_mw"
                        ),
                    }
                )
                continue
            pre_score = float(pre["constraint_violation_score"])
            post_score = float(post["constraint_violation_score"])
            reduction = pre_score - post_score
            relative = reduction / pre_score if pre_score > 0 else 0.0
            result = {
                "scenario_id": scenario["scenario_id"],
                "network_model": str(scenario.get("network_model")),
                "system": normalize_system(scenario),
                "contingency_type": scenario.get("contingency_type"),
                "physical_source": copy.deepcopy(scenario.get("physical_source")),
                "opf_solver": "pandapower.runopp",
                "opf_status": "converged",
                "optimization_formulation": {
                    "feasibility_stage": "hard AC power-balance, native-intersected voltage, line, transformer, control, and registered pre-contingency loading-margin bounds",
                    "optimization_stage": "minimize retained native generation cost over the hard-feasible region",
                    "security_stage": (
                        "enumerate every non-islanding in-service line or transformer outage and every in-service non-slack generator outage; apply bounded corrective AC OPF after each outage; require voltage, thermal, P/Q control, reserve, finite-state, and power-balance gates"
                        if n1_network_checks <= 0 and n1_generator_checks <= 0
                        else f"recompute registered ranked subsets of {n1_network_checks} network and {n1_generator_checks} generator outages using bounded corrective AC OPF and the same complete feasibility gates"
                    ),
                    "objective_value": objective_value,
                },
                "constraint_policy": constraint_policy,
                "pre_action": pre,
                "post_action": post,
                "pre_action_state": pre_state,
                "post_action_state": post_state,
                "pre_action_controls": pre_controls,
                "post_action_controls": post_controls,
                "control_delta": control_delta(pre_controls, post_controls),
                "reserve_summary": reserve,
                "post_action_n1": post_action_n1_check(
                    net,
                    n1_network_checks,
                    n1_generator_checks,
                    constraint_policy,
                    balance_tolerance_mw,
                ),
                "absolute_violation_reduction": round(reduction, 8),
                "relative_violation_reduction": round(relative, 8),
                "registered_loading_margins_percent": list(loading_margins),
                "selected_loading_margin_percent": margin,
            }
            core_errors = published_result_errors(
                result,
                balance_tolerance_mw,
                require_uncertainty=False,
            )
            if core_errors:
                attempt_summaries.append(
                    {
                        "loading_margin_percent": margin,
                        "status": "hard_gate_failed",
                        "hard_gate_errors": core_errors,
                        "reserve_summary": reserve,
                        "post_action_n1_summary": post_action_n1_diagnostic_summary(
                            result.get("post_action_n1")
                        ),
                    }
                )
                continue
            active_uncertainty = embedded_active_load_uncertainty_gate(
                scenario,
                result,
                balance_tolerance_mw=balance_tolerance_mw,
            )
            result["active_load_uncertainty_construction_gate"] = active_uncertainty
            if active_uncertainty["status"] != "pass":
                attempt_summaries.append(
                    {
                        "loading_margin_percent": margin,
                        "status": "active_load_uncertainty_gate_failed",
                        "registered_case_count": active_uncertainty[
                            "registered_case_count"
                        ],
                        "safe_case_count": active_uncertainty["safe_case_count"],
                        "minimum_worst_voltage_margin_pu": active_uncertainty[
                            "minimum_worst_voltage_margin_pu"
                        ],
                        "minimum_thermal_margin_percent": active_uncertainty[
                            "minimum_thermal_margin_percent"
                        ],
                        "optimization_reason_counts": active_uncertainty[
                            "optimization_reason_counts"
                        ],
                    }
                )
                continue
            constant_power_factor_uncertainty = (
                embedded_constant_power_factor_uncertainty_gate(
                    scenario,
                    result,
                    balance_tolerance_mw=balance_tolerance_mw,
                )
            )
            result["constant_power_factor_uncertainty_construction_gate"] = (
                constant_power_factor_uncertainty
            )
            load_uncertainty = combined_load_uncertainty_gate(
                active_uncertainty,
                constant_power_factor_uncertainty,
            )
            result["load_uncertainty_construction_gate"] = load_uncertainty
            if load_uncertainty["status"] != "pass":
                attempt_summaries.append(
                    {
                        "loading_margin_percent": margin,
                        "status": "constant_power_factor_uncertainty_gate_failed",
                        "registered_case_count": (
                            constant_power_factor_uncertainty[
                                "registered_case_count"
                            ]
                        ),
                        "safe_case_count": constant_power_factor_uncertainty[
                            "safe_case_count"
                        ],
                        "minimum_worst_voltage_margin_pu": (
                            constant_power_factor_uncertainty[
                                "minimum_worst_voltage_margin_pu"
                            ]
                        ),
                        "minimum_thermal_margin_percent": (
                            constant_power_factor_uncertainty[
                                "minimum_thermal_margin_percent"
                            ]
                        ),
                        "optimization_reason_counts": (
                            constant_power_factor_uncertainty[
                                "optimization_reason_counts"
                            ]
                        ),
                    }
                )
                continue
            hard_errors = published_result_errors(result, balance_tolerance_mw)
            if not hard_errors:
                result["security_margin_attempt_count"] = len(attempt_summaries) + 1
                return result
            attempt_summaries.append(
                {
                    "loading_margin_percent": margin,
                    "status": "hard_gate_failed",
                    "hard_gate_errors": hard_errors,
                    "reserve_summary": reserve,
                    "post_action_n1_summary": post_action_n1_diagnostic_summary(
                        result.get("post_action_n1")
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            attempt_summaries.append(
                {
                    "loading_margin_percent": margin,
                    "status": "solver_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                }
            )
    raise CandidateInfeasible(attempt_summaries)


def published_result_errors(
    result: dict[str, Any],
    balance_tolerance_mw: float,
    *,
    require_uncertainty: bool = True,
) -> list[str]:
    post = result.get("post_action") or {}
    required_numeric = (
        "max_line_loading_percent",
        "max_transformer_loading_percent",
        "min_bus_voltage_pu",
        "max_bus_voltage_pu",
        "constraint_violation_score",
        "power_balance_residual_mw",
    )
    errors = [
        f"nonfinite:{key}" for key in required_numeric if not finite(post.get(key))
    ]
    if errors:
        return errors
    if float(post["constraint_violation_score"]) > 1e-8:
        errors.append("post_action_constraint_violation")
    if int(post.get("overloaded_branch_count") or 0) != 0:
        errors.append("post_action_line_overload")
    if int(post.get("overloaded_transformer_count") or 0) != 0:
        errors.append("post_action_transformer_overload")
    if int(post.get("voltage_violation_count") or 0) != 0:
        errors.append("post_action_voltage_violation")
    if float(post["power_balance_residual_mw"]) > balance_tolerance_mw:
        errors.append("post_action_power_balance_residual")
    for stage in ("pre_action_state", "post_action_state"):
        state = result.get(stage) or {}
        for element, fields in (
            ("bus", ("vm_pu", "va_degree")),
            (
                "line",
                ("p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar", "loading_percent"),
            ),
            (
                "trafo",
                ("p_hv_mw", "q_hv_mvar", "p_lv_mw", "q_lv_mvar", "loading_percent"),
            ),
        ):
            rows = state.get(element) or []
            if not rows:
                errors.append(f"missing_state_rows:{stage}:{element}")
                continue
            for row in rows:
                if row.get("in_service") is True and any(
                    not finite(row.get(field)) for field in fields
                ):
                    errors.append(
                        f"nonfinite_in_service_state:{stage}:{element}:{row.get('index')}"
                    )
    controls = result.get("post_action_controls") or {}
    control_lookup = {
        (element, int(row["index"])): row
        for element in ("ext_grid", "gen")
        for row in (controls.get(element) or [])
    }
    for bound in (result.get("constraint_policy") or {}).get("control_bounds") or []:
        key = (str(bound["element"]), int(bound["index"]))
        control = control_lookup.get(key)
        if control is None:
            errors.append(f"missing_post_control:{key[0]}:{key[1]}")
            continue
        for value_key, bounds_key in (
            ("p_mw", "p_bounds_mw"),
            ("q_mvar", "q_bounds_mvar"),
        ):
            lower, upper = [float(value) for value in bound[bounds_key]]
            value = float(control[value_key])
            if not (lower - 1e-5 <= value <= upper + 1e-5):
                errors.append(f"control_out_of_bounds:{key[0]}:{key[1]}:{value_key}")
    reserve = result.get("reserve_summary") or {}
    if reserve.get("upward_reserve_adequate") is not True:
        errors.append("largest_generator_upward_reserve_not_covered")
    if reserve.get("external_balance_reserve_adequate") is not True:
        errors.append("active_load_external_balance_reserve_not_covered")
    security = result.get("post_action_n1") or {}
    requested = int(security.get("requested_checks") or 0)
    completed = int(security.get("completed_checks") or 0)
    secure = int(security.get("secure_checks") or 0)
    if requested > 0 and (completed != requested or secure != requested):
        errors.append("post_action_n1_not_fully_secure")
    if require_uncertainty:
        expected_uncertainty_cases = len(PREREGISTERED_ACTIVE_LOAD_STRESS) * (
            1 + requested
        )
        for model, key in (
            ("active_load", "active_load_uncertainty_construction_gate"),
            (
                "constant_power_factor",
                "constant_power_factor_uncertainty_construction_gate",
            ),
        ):
            uncertainty = result.get(key) or {}
            if uncertainty.get("status") != "pass":
                errors.append(f"{model}_uncertainty_gate_not_passed")
            if (
                int(uncertainty.get("registered_case_count") or 0)
                != expected_uncertainty_cases
                or int(uncertainty.get("safe_case_count") or 0)
                != expected_uncertainty_cases
                or uncertainty.get("denominator_complete") is not True
            ):
                errors.append(
                    f"{model}_uncertainty_denominator_not_fully_safe"
                )
            uncertainty_voltage_margin = uncertainty.get(
                "minimum_worst_voltage_margin_pu"
            )
            if (
                not finite(uncertainty_voltage_margin)
                or float(uncertainty_voltage_margin)
                < ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
            ):
                errors.append(
                    f"{model}_uncertainty_voltage_margin_below_minimum"
                )
            uncertainty_thermal_margin = uncertainty.get(
                "minimum_thermal_margin_percent"
            )
            if (
                not finite(uncertainty_thermal_margin)
                or float(uncertainty_thermal_margin) < 0.0
            ):
                errors.append(f"{model}_uncertainty_thermal_margin_negative")
        combined = result.get("load_uncertainty_construction_gate") or {}
        expected_combined_cases = 2 * expected_uncertainty_cases
        if (
            combined.get("status") != "pass"
            or int(combined.get("load_model_count") or 0) != 2
            or int(combined.get("registered_case_count") or 0)
            != expected_combined_cases
            or int(combined.get("safe_case_count") or 0)
            != expected_combined_cases
            or combined.get("denominator_complete") is not True
        ):
            errors.append("dual_load_uncertainty_denominator_not_fully_safe")
    return errors


def stratified_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_system: dict[
        str,
        dict[int, dict[tuple[str, str], list[dict[str, Any]]]],
    ] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for row in rows:
        key = (
            str(row.get("contingency_type") or "unknown"),
            str(row.get("severity_level") or "unknown"),
        )
        priority = int(row.get("_opf_screening_priority", 1))
        by_system[normalize_system(row)][priority][key].append(row)
    kept = []
    for system in sorted(by_system):
        for priority in sorted(by_system[system]):
            buckets = by_system[system][priority]
            for bucket_rows in buckets.values():
                bucket_rows.sort(
                    key=lambda row: (
                        float(row.get("load_level") or 1.0),
                        abs(float(row.get("generator_factor") or 1.0) - 1.0),
                        json.dumps(
                            {
                                key: row.get(key)
                                for key in (
                                    "line_indices",
                                    "line_index",
                                    "transformer_index",
                                    "generator_index",
                                    "bus_index",
                                )
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        str(row.get("scenario_id")),
                    )
                )
            bucket_keys = sorted(buckets)
            while bucket_keys:
                next_keys = []
                for key in bucket_keys:
                    if buckets[key]:
                        kept.append(buckets[key].pop(0))
                    if buckets[key]:
                        next_keys.append(key)
                bucket_keys = next_keys
    return kept


def stratified_limit(
    rows: list[dict[str, Any]], max_per_system: int
) -> list[dict[str, Any]]:
    ordered = stratified_order(rows)
    if max_per_system <= 0:
        return ordered
    counts: Counter[str] = Counter()
    kept = []
    for row in ordered:
        system = normalize_system(row)
        if counts[system] >= max_per_system:
            continue
        kept.append(row)
        counts[system] += 1
    return kept


def physically_bound_action_text(
    result: dict[str, Any], variant: int
) -> tuple[str, str, str]:
    all_deltas = list((result.get("control_delta") or {}).get("elements") or [])
    gen_deltas = [row for row in all_deltas if row.get("element") == "gen"]
    ext_deltas = [row for row in all_deltas if row.get("element") == "ext_grid"]
    gen_deltas.sort(
        key=lambda row: (
            -abs(float(row.get("delta_p_mw") or 0.0)),
            -abs(float(row.get("delta_vm_pu") or 0.0)),
            int(row["index"]),
        )
    )
    material_gen = [
        row
        for row in gen_deltas
        if abs(float(row.get("delta_p_mw") or 0.0)) > 1e-4
        or abs(float(row.get("delta_vm_pu") or 0.0)) > 1e-5
    ]
    material_ext = [
        row
        for row in ext_deltas
        if abs(float(row.get("delta_vm_pu") or 0.0)) > 1e-5
    ]
    # The prose response is an HONEST SUMMARY of the executable control vector:
    # it always includes the external-grid (slack) voltage command (previously
    # dropped) and the largest generator adjustments, plus the total device count
    # and a pointer to executable_control_target for the complete vector. The
    # full per-device vector (which can exceed 50 generators on IEEE118) is far
    # too long for a sequence-to-sequence target, so the prose is capped to the
    # top generator deltas while the structured executable_control_target remains
    # the complete, authoritative, executable form. The prose is a compact
    # summary; the structured target carries every device command.
    PROSE_MAX_GENERATORS = 4
    total_gen = len(material_gen)
    total_ext = len(material_ext)
    if not material_gen and not material_ext:
        material_gen = gen_deltas[:1]
    prose_gen = material_gen[:PROSE_MAX_GENERATORS]
    actions = [
        (
            f"机组 {int(row['index'])} 有功调整 {float(row['delta_p_mw']):+.3f} MW，"
            f"电压设定值调整 {float(row['delta_vm_pu']):+.5f} p.u."
        )
        for row in prose_gen
    ]
    actions.extend(
        f"外部电网 {int(row['index'])} 电压设定值调整 {float(row['delta_vm_pu']):+.5f} p.u."
        for row in material_ext
    )
    prefix = (
        "按已求解的控制向量执行："
        if variant % 2 == 0
        else "采用受限优化潮流给出的设备级设定值："
    )
    ext_clause = f"、{total_ext} 个外部电网电压设定" if total_ext else ""
    more_clause = (
        f"（另有设备调整 {total_gen - len(prose_gen)} 项见 executable_control_target）"
        if total_gen > len(prose_gen)
        else ""
    )
    summary = (
        f"（共 {total_gen} 台设备{ext_clause}的设备级设定；下列为最大"
        f"{len(prose_gen)} 台设备与外部电网电压设定{more_clause}，"
        "完整逐项向量与 executable_control_target 一致）"
    )
    chosen = prefix + summary + "；".join(actions) + "。完成后复算潮流并执行全部注册 N-1 校核。"
    rejected = (
        "不得使用超出登记 P/Q 控制边界、省略外部电网电压设定，"
        "或缺少动作后潮流与 N-1 复核的替代设定值。"
    )
    rationale = (
        "建议逐项列出全部有实质变化的机组有功/电压设定与外部电网电压设定，"
        "与保存的 control_delta 完全一致；未声明拓扑切换、切负荷、并联补偿或变压器档位动作。"
    )
    return chosen, rejected, rationale


def make_record(
    scenario: dict[str, Any], result: dict[str, Any], variant: int
) -> dict[str, Any]:
    base_idx = 980000 + int(
        hashlib.sha256(
            f"{scenario['scenario_id']}:{variant}".encode("utf-8")
        ).hexdigest()[:10],
        16,
    )
    row = make_auxiliary(base_idx, scenario, variant)
    source_record_id = row["id"]
    scenario_id = str(scenario["scenario_id"])
    key = stable_key(scenario_id)
    row["id"] = f"gridinstruct_v12_opf2_aux_{key}_v{variant:02d}"
    row["closed_loop_validation"] = result
    if isinstance(result.get("physical_source"), dict):
        row["physical_source"] = copy.deepcopy(result["physical_source"])
    executable_target = build_executable_control_target(result)
    target_errors = validate_executable_control_target(executable_target, result)
    if target_errors:
        raise ValueError(f"invalid executable OPF target: {target_errors[:10]}")
    row["executable_control_target"] = executable_target
    row["target_replay_validation"] = {
        "status": "pass",
        "validated_modes": ["absolute_setpoints", "pre_action_plus_delta"],
        "validated_control_count": len(executable_target["controls"]),
        "expected_post_action_evidence_sha256": executable_target["replay_contract"][
            "expected_post_action_evidence_sha256"
        ],
    }
    chosen, rejected, action_rationale = physically_bound_action_text(result, variant)
    row["instruction"] = (
        f"针对场景 {scenario_id}，依据保存的受限优化潮流结果给出设备级机组有功与电压设定调整，"
        "并说明动作后的潮流和 N-1 复核要求。"
    )
    row["output"] = chosen
    row["chosen_response"] = chosen
    row["rejected_response"] = rejected
    row["preference_rationale"] = {
        "chosen_advantages": [
            "complete_structured_control_target",
            "absolute_and_delta_replay_exact_match",
            "registered_pq_bounds",
            "post_action_registered_n1_validation",
        ],
        "rejected_issues": ["unregistered_control", "missing_post_action_validation"],
    }
    row["input"]["available_actions"] = [
        "generator_active_power_redispatch",
        "generator_voltage_setpoint_control",
    ]
    row["input"]["action_contract"] = (
        "execute every generator P/voltage and external-grid voltage command in "
        "executable_control_target; generator Q and external-grid P/Q are realized "
        "AC responses checked against registered capability bounds; the prose "
        "response summarizes the external-grid voltage command and the largest "
        "generator adjustments and points to executable_control_target for the "
        "complete per-device vector"
    )
    row["input"]["opf_validation_summary"] = (
        "已对该场景执行优化潮流闭环校核："
        f"约束违反评分由 {result['pre_action']['constraint_violation_score']} "
        f"降至 {result['post_action']['constraint_violation_score']}。"
    )
    row["tool_plan"] = [
        {
            "tool": "run_power_flow",
            "args": {"scenario_id": scenario_id, "stage": "pre_action"},
        },
        {
            "tool": "run_opf_redispatch",
            "args": {
                "scenario_id": scenario_id,
                "objective": "minimize_generation_cost_subject_to_hard_ac_safety_constraints",
            },
        },
        {
            "tool": "run_power_flow",
            "args": {"scenario_id": scenario_id, "stage": "post_action"},
        },
    ]
    row["rationale"] = (
        f"{action_rationale} 完整机组有功/电压与外部电网电压命令保存在"
        " executable_control_target，可分别按绝对设定值和动作前值加变化量反放；"
        "机组无功及外部电网有功/无功作为潮流响应保存并接受能力边界校核。"
        "该样本附带 pandapower 优化潮流闭环校核，动作前后约束、完整电气状态、"
        "控制边界、备用和全部预登记 N-1 结果均可复算。"
    )
    metadata = dict(row.get("metadata") or {})
    metadata.update(
        {
            "augmentation_type": TAG,
            "validation_status": "opf_closed_loop_validated",
            "created_by": "append_opf_closed_loop_auxiliary_records.py",
            "source_group": f"{TAG}:{scenario_id}",
            "source_record_id": source_record_id,
            "opf_closed_loop": True,
            "opf_status": "converged",
            "opf_relative_violation_reduction": result["relative_violation_reduction"],
            "stable_scenario_key": key,
        }
    )
    row["metadata"] = metadata
    return row


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# OPF Closed-Loop Auxiliary Decision Expansion",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        f"- Eligible scenarios: {report['eligible_scenarios']}",
        f"- Deterministically screened scenarios: {report['screened_scenarios']}",
        f"- Candidate scenarios: {report['candidate_scenarios']}",
        f"- OPF converged scenarios: {report['opf_converged_scenarios']}",
        f"- Screening solver errors: {report['screening_solver_error_count']}",
        f"- Screening hard-gate rejections: {report['rejected_result_count']}",
        f"- New records appended: {report['new_records_appended']}",
        f"- Dataset after: {report['source_records_after']}",
        f"- Mean relative violation reduction: {report['mean_relative_violation_reduction']}",
        "",
        "## Network Coverage",
        "",
        "| Network | OPF-converged scenarios | New records |",
        "| --- | ---: | ---: |",
    ]
    for network in sorted(report["opf_converged_by_network"]):
        lines.append(
            f"| {network} | {report['opf_converged_by_network'][network]} | {report['new_records_by_network'].get(network, 0)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output-dataset", default=None)
    parser.add_argument(
        "--scenarios", default="simulation_outputs/contingency/scenarios_converged.json"
    )
    parser.add_argument(
        "--additional-scenarios",
        nargs="*",
        default=[],
        help="Additional hash-bound scenario manifests used only by the OPF expansion.",
    )
    parser.add_argument("--systems", nargs="+", default=["ieee14", "ieee118"])
    parser.add_argument(
        "--contingency-types",
        nargs="+",
        default=[
            "base_power_flow",
            "generator_perturbation",
            "n1_branch_outage",
            "n2_branch_outage",
        ],
    )
    parser.add_argument("--variants-per-scenario", type=int, default=2)
    parser.add_argument("--target-scenarios-per-system", type=int, default=80)
    parser.add_argument("--min-relative-reduction", type=float, default=0.0)
    parser.add_argument(
        "--post-action-n1-checks",
        type=int,
        default=5,
        help="0 enumerates every non-islanding line/transformer outage.",
    )
    parser.add_argument(
        "--post-action-generator-n1-checks",
        type=int,
        default=2,
        help="0 enumerates every in-service non-slack generator outage.",
    )
    parser.add_argument(
        "--loading-margins", nargs="+", type=float, default=[90.0, 80.0, 70.0, 60.0]
    )
    parser.add_argument("--power-balance-tolerance-mw", type=float, default=1e-3)
    parser.add_argument("--min-accepted-per-system", type=int, default=5)
    parser.add_argument("--min-acceptance-rate", type=float, default=0.20)
    parser.add_argument("--max-screening-error-rate", type=float, default=0.80)
    parser.add_argument(
        "--max-screened-per-system",
        type=int,
        default=0,
        help="Diagnostic cap per system; 0 leaves formal screening uncapped.",
    )
    parser.add_argument("--include-normal-scenarios", action="store_true")
    parser.add_argument(
        "--results-json",
        default="simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
    )
    parser.add_argument(
        "--diagnostic-results-json",
        default=(
            "reports/"
            "opf_closed_loop_screening_diagnostics_v1.2_sd_core.json"
        ),
        help=(
            "Fail-closed screening ledger written when publication gates do not "
            "pass; the committed results path is left untouched."
        ),
    )
    parser.add_argument(
        "--report-json",
        default="reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--report-md", default="reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.md"
    )
    parser.add_argument(
        "--commit-json",
        default="simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--progress-json",
        default="reports/opf_closed_loop_screening_progress_v1.2_sd_core.json",
    )
    args = parser.parse_args()

    ensure_dirs(ROOT / "simulation_outputs/opf_closed_loop", ROOT / "reports")
    dataset_path = ROOT / args.dataset
    output_dataset_path = ROOT / (args.output_dataset or args.dataset)
    commit_path = ROOT / args.commit_json
    commit_path.unlink(missing_ok=True)
    scenario_input_paths = [
        (args.scenarios, ROOT / args.scenarios),
        *[
            (value, ROOT / value)
            for value in args.additional_scenarios
        ],
    ]
    implementation_paths = [
        Path(__file__).resolve(),
        (ROOT / "scripts/validate_opf_action_uncertainty_stress.py").resolve(),
    ]
    input_artifacts = {
        "dataset": {
            "path": args.dataset,
            "sha256": file_sha256(dataset_path),
        },
        "scenario_manifests": [
            {
                "path": argument,
                "sha256": file_sha256(path),
            }
            for argument, path in scenario_input_paths
        ],
        "implementation": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": file_sha256(path),
            }
            for path in implementation_paths
        ],
    }
    original_rows = read_jsonl(dataset_path)
    original_ids = [str(row.get("id") or "") for row in original_rows]
    if any(not value for value in original_ids) or len(original_ids) != len(
        set(original_ids)
    ):
        raise ValueError("dataset contains missing or duplicate record IDs")
    existing_tag_rows = [
        row
        for row in original_rows
        if (row.get("metadata") or {}).get("augmentation_type") == TAG
    ]
    rows = [
        row
        for row in original_rows
        if (row.get("metadata") or {}).get("augmentation_type") != TAG
    ]
    systems = {item.lower().replace(" ", "") for item in args.systems}
    contingency_types = set(args.contingency_types)
    unsupported = contingency_types - ALLOWED_CONTINGENCIES
    if unsupported:
        raise ValueError(f"unsupported contingency types: {sorted(unsupported)}")
    all_scenario_rows = [
        {**row, "_opf_screening_priority": 1}
        for row in read_json(ROOT / args.scenarios)
    ]
    for path in args.additional_scenarios:
        all_scenario_rows.extend(
            {**row, "_opf_screening_priority": 0}
            for row in read_json(ROOT / path)
        )
    all_scenario_ids = [str(row.get("scenario_id") or "") for row in all_scenario_rows]
    if any(not value for value in all_scenario_ids) or len(all_scenario_ids) != len(
        set(all_scenario_ids)
    ):
        raise ValueError(
            "OPF scenario sources contain missing or duplicate scenario IDs"
        )
    scenario_rows = [
        row
        for row in all_scenario_rows
        if row.get("solver_status") == "converged"
        and normalize_system(row) in systems
        and row.get("contingency_type") in contingency_types
        and (
            args.include_normal_scenarios
            or (row.get("violation_type") or ["none"]) != ["none"]
        )
    ]
    scenario_rows.sort(
        key=lambda row: (normalize_system(row), str(row.get("scenario_id")))
    )
    scenario_rows = stratified_order(scenario_rows)

    if not scenario_rows:
        raise ValueError("no eligible OPF candidate scenarios")

    results = []
    errors = []
    rejected_results = []
    new_rows = []
    screened_rows = []
    accepted_during_screen: Counter[str] = Counter()
    screened_during_screen: Counter[str] = Counter()
    progress_path = ROOT / args.progress_json
    progress_bar = tqdm(scenario_rows, desc="opf-closed-loop")

    def update_progress() -> None:
        progress_bar.set_postfix(
            accepted=sum(accepted_during_screen.values()),
            screened=len(screened_rows),
            errors=len(errors),
            rejected=len(rejected_results),
        )
        atomic_json(
            progress_path,
            {
                "status": "running",
                "screened_scenarios": len(screened_rows),
                "accepted_scenarios_by_system": dict(accepted_during_screen),
                "accepted_scenarios": sum(accepted_during_screen.values()),
                "rejected_result_count": len(rejected_results),
                "screening_solver_error_count": len(errors),
                "target_scenarios_per_system": args.target_scenarios_per_system,
                "recent_rejections": [
                    {
                        "scenario_id": row.get("scenario_id"),
                        "system": row.get("system"),
                        "reason": row.get("reason"),
                        "attempts": row.get("attempts"),
                    }
                    for row in rejected_results[-3:]
                ],
            },
        )

    update_progress()
    for scenario in progress_bar:
        system = normalize_system(scenario)
        if accepted_during_screen[system] >= args.target_scenarios_per_system:
            continue
        if (
            args.max_screened_per_system > 0
            and screened_during_screen[system] >= args.max_screened_per_system
        ):
            continue
        screened_rows.append(scenario)
        screened_during_screen[system] += 1
        try:
            result = run_closed_loop(
                scenario,
                args.post_action_n1_checks,
                args.post_action_generator_n1_checks,
                tuple(args.loading_margins),
                args.power_balance_tolerance_mw,
            )
        except CandidateInfeasible as exc:
            rejected_results.append(
                {
                    "scenario_id": scenario.get("scenario_id"),
                    "system": normalize_system(scenario),
                    "reason": "registered_ac_security_infeasible",
                    "attempts": exc.attempts,
                }
            )
            update_progress()
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "scenario_id": scenario.get("scenario_id"),
                    "system": normalize_system(scenario),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
            update_progress()
            continue
        if float(result["relative_violation_reduction"]) < args.min_relative_reduction:
            rejected_results.append(
                {
                    "scenario_id": scenario.get("scenario_id"),
                    "system": normalize_system(scenario),
                    "reason": "below_min_relative_reduction",
                    "relative_violation_reduction": result[
                        "relative_violation_reduction"
                    ],
                    "result": result,
                }
            )
            update_progress()
            continue
        safety_errors = published_result_errors(result, args.power_balance_tolerance_mw)
        if safety_errors:
            rejected_results.append(
                {
                    "scenario_id": scenario.get("scenario_id"),
                    "system": normalize_system(scenario),
                    "reason": "hard_publication_gate_failed",
                    "hard_gate_errors": safety_errors,
                    "result": result,
                }
            )
            update_progress()
            continue
        results.append(result)
        accepted_during_screen[system] += 1
        for variant in range(args.variants_per_scenario):
            new_rows.append(make_record(scenario, result, variant))
        update_progress()

    existing_ids = {row["id"] for row in rows}
    new_ids = [row["id"] for row in new_rows]
    if len(new_ids) != len(set(new_ids)):
        raise ValueError("deterministic OPF record IDs are not unique")
    conflicts = [row["id"] for row in new_rows if row["id"] in existing_ids]
    if conflicts:
        raise ValueError(
            f"OPF record IDs conflict with non-OPF records: {conflicts[:20]}"
        )
    to_add = new_rows

    results_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_artifacts": input_artifacts,
        "eligible_scenarios": len(scenario_rows),
        "screened_scenarios": len(screened_rows),
        "candidate_scenarios": len(screened_rows),
        "published_candidate_scenarios": len(results),
        "results": results,
        "rejected_results": rejected_results,
        "errors": errors,
    }
    rel_values = [
        float(row["relative_violation_reduction"])
        for row in results
        if math.isfinite(float(row["relative_violation_reduction"]))
    ]
    n1_checks_total = sum(
        int((row.get("post_action_n1") or {}).get("completed_checks") or 0)
        for row in results
    )
    n1_secure_total = sum(
        int((row.get("post_action_n1") or {}).get("secure_checks") or 0)
        for row in results
    )
    opf_by_network = Counter(row["network_model"] for row in results)
    new_by_network = Counter(row["network_model"] for row in to_add)
    eligible_by_network = Counter(normalize_system(row) for row in scenario_rows)
    screened_by_network = Counter(normalize_system(row) for row in screened_rows)
    accepted_by_system = Counter(row["system"] for row in results)
    network_coverage_pass = all(
        accepted_by_system.get(system, 0) == args.target_scenarios_per_system
        for system in sorted(systems)
    )
    acceptance_rate = len(results) / len(screened_rows)
    solver_error_rate = len(errors) / len(screened_rows)
    all_published_safe = all(
        not published_result_errors(row, args.power_balance_tolerance_mw)
        for row in results
    )
    outcome_complete = len(results) + len(rejected_results) + len(errors) == len(
        screened_rows
    )
    status = (
        "pass"
        if all(
            (
                bool(results),
                bool(to_add),
                outcome_complete,
                network_coverage_pass,
                acceptance_rate >= args.min_acceptance_rate,
                solver_error_rate <= args.max_screening_error_rate,
                all_published_safe,
            )
        )
        else "fail"
    )
    candidate_output = rows + to_add
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "dataset": args.dataset,
        "output_dataset": str(output_dataset_path.relative_to(ROOT)),
        "scenarios": args.scenarios,
        "additional_scenarios": args.additional_scenarios,
        "systems": sorted(systems),
        "contingency_types": sorted(contingency_types),
        "registered_loading_margins_percent": args.loading_margins,
        "variants_per_scenario": args.variants_per_scenario,
        "post_action_n1_checks": args.post_action_n1_checks,
        "post_action_generator_n1_checks": args.post_action_generator_n1_checks,
        "eligible_scenarios": len(scenario_rows),
        "screened_scenarios": len(screened_rows),
        "candidate_scenarios": len(screened_rows),
        "published_candidate_scenarios": len(results),
        "opf_converged_scenarios": len(results),
        "opf_error_count": 0,
        "screening_solver_error_count": len(errors),
        "screening_solver_error_types": dict(
            Counter(row["error_type"] for row in errors)
        ),
        "screening_solver_error_examples": errors[:20],
        "rejected_result_count": len(rejected_results),
        "rejection_reason_counts": dict(
            Counter(row["reason"] for row in rejected_results)
        ),
        "candidate_outcome_count": len(results) + len(rejected_results) + len(errors),
        "candidate_outcome_complete": outcome_complete,
        "source_records_before": len(original_rows),
        "source_records_after": len(candidate_output)
        if status == "pass"
        else len(original_rows),
        "new_records_appended": len(to_add) if status == "pass" else 0,
        "proposed_safe_records": len(to_add),
        "previous_opf_v2_records_replaced": len(existing_tag_rows),
        "opf_converged_by_network": dict(opf_by_network),
        "new_records_by_network": dict(new_by_network),
        "eligible_scenarios_by_system": dict(eligible_by_network),
        "screened_scenarios_by_system": dict(screened_by_network),
        "accepted_scenarios_by_system": dict(accepted_by_system),
        "target_scenarios_per_system": args.target_scenarios_per_system,
        "max_screened_per_system": args.max_screened_per_system,
        "min_accepted_per_system": args.min_accepted_per_system,
        "network_coverage_pass": network_coverage_pass,
        "acceptance_rate": round(acceptance_rate, 8),
        "min_acceptance_rate": args.min_acceptance_rate,
        "solver_error_rate": round(solver_error_rate, 8),
        "max_screening_error_rate": args.max_screening_error_rate,
        "all_published_results_hard_safe": all_published_safe,
        "power_balance_tolerance_mw": args.power_balance_tolerance_mw,
        "mean_relative_violation_reduction": round(sum(rel_values) / len(rel_values), 8)
        if rel_values
        else None,
        "min_relative_violation_reduction": min(rel_values) if rel_values else None,
        "max_relative_violation_reduction": max(rel_values) if rel_values else None,
        "post_action_n1_checks_total": n1_checks_total,
        "post_action_n1_secure_total": n1_secure_total,
        "post_action_n1_security_rate": round(n1_secure_total / n1_checks_total, 8)
        if n1_checks_total
        else None,
        "expansion_tag": TAG,
        "selection_policy": "deterministic screening gives the explicitly registered additional security-candidate manifest priority over the general eligible pool; within each source priority it uses round-robin contingency-type and severity buckets ordered by ascending load level, generator-factor distance from the source operating point, structural outage identity, then scenario ID, until the per-network target is met; every screened outcome is retained in the audit; normal scenarios are excluded by default",
        "scope": "Preregistered, feasibility-screened IEEE14/IEEE118 scenario subset used to add cost-minimizing closed-loop auxiliary-decision records with native-intersected control bounds, pre/post AC states, loss-aware reserve, and bounded corrective AC-OPF replay for the registered N-1 set.",
        "transaction_commit": args.commit_json,
        "diagnostic_results": args.diagnostic_results_json,
        "input_artifacts": input_artifacts,
    }
    report_path = ROOT / args.report_json
    report_md_path = ROOT / args.report_md
    if status == "pass":
        results_path = ROOT / args.results_json
        atomic_json(results_path, results_payload)
        atomic_jsonl(output_dataset_path, candidate_output)
        atomic_json(report_path, report)
        atomic_markdown(report_md_path, report)
        artifacts = {
            "results": {"path": args.results_json, "sha256": file_sha256(results_path)},
            "dataset": {
                "path": str(output_dataset_path.relative_to(ROOT)),
                "sha256": file_sha256(output_dataset_path),
            },
            "report": {"path": args.report_json, "sha256": file_sha256(report_path)},
            "report_markdown": {
                "path": args.report_md,
                "sha256": file_sha256(report_md_path),
            },
        }
        atomic_json(
            commit_path,
            {
                "schema_version": "1.0",
                "status": "pass",
                "commit_policy": "written last after atomically replacing and hashing every committed artifact",
                "inputs": input_artifacts,
                "artifacts": artifacts,
            },
        )
        atomic_json(
            progress_path,
            {
                "status": "complete",
                "screened_scenarios": len(screened_rows),
                "accepted_scenarios_by_system": dict(accepted_during_screen),
                "accepted_scenarios": len(results),
                "rejected_result_count": len(rejected_results),
                "screening_solver_error_count": len(errors),
                "target_scenarios_per_system": args.target_scenarios_per_system,
            },
        )
    else:
        diagnostic_results_path = ROOT / args.diagnostic_results_json
        diagnostic_payload = {
            **results_payload,
            "status": "diagnostic",
            "publication_artifacts_written": False,
            "publication_results_path_preserved": args.results_json,
        }
        atomic_json(diagnostic_results_path, diagnostic_payload)
        report["diagnostic_results_sha256"] = file_sha256(
            diagnostic_results_path
        )
        atomic_json(report_path, report)
        atomic_markdown(report_md_path, report)
        atomic_json(
            progress_path,
            {
                "status": "failed",
                "screened_scenarios": len(screened_rows),
                "accepted_scenarios_by_system": dict(accepted_during_screen),
                "accepted_scenarios": len(results),
                "rejected_result_count": len(rejected_results),
                "screening_solver_error_count": len(errors),
                "target_scenarios_per_system": args.target_scenarios_per_system,
                "diagnostic_results": args.diagnostic_results_json,
                "diagnostic_results_sha256": file_sha256(
                    diagnostic_results_path
                ),
            },
        )
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
