#!/usr/bin/env python3
"""Run a native-source independent AC replay with LightSim2Grid.

This adapter parses the pinned MATPOWER source text itself and initializes the
LightSim2Grid C++ data model directly.  It never imports pandapower or a
pandapower-exported PYPOWER structure.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from lightsim2grid.gridmodel import GridModel

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_matrix(text: str, name: str) -> list[list[float]]:
    match = re.search(
        rf"mpc\.{re.escape(name)}\s*=\s*\[(.*?)\];",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"MATPOWER matrix is missing: mpc.{name}")
    rows = []
    for raw_line in match.group(1).splitlines():
        line = raw_line.split("%", 1)[0].strip().rstrip(";").strip()
        if line:
            rows.append([float(value) for value in line.split()])
    return rows


def parse_scalar(text: str, name: str) -> float:
    match = re.search(rf"mpc\.{re.escape(name)}\s*=\s*([0-9.eE+-]+)\s*;", text)
    if not match:
        raise ValueError(f"MATPOWER scalar is missing: mpc.{name}")
    return float(match.group(1))


def load_native_case(path: Path, expected_sha256: str) -> dict[str, Any]:
    digest = sha256_file(path)
    if digest != expected_sha256:
        raise ValueError(
            f"source-case hash mismatch for {path.name}: "
            f"{digest} != {expected_sha256}"
        )
    text = path.read_text(encoding="utf-8")
    return {
        "path": path,
        "sha256": digest,
        "base_mva": parse_scalar(text, "baseMVA"),
        "bus": parse_matrix(text, "bus"),
        "gen": parse_matrix(text, "gen"),
        "branch": parse_matrix(text, "branch"),
    }


def is_transformer(branch: list[float]) -> bool:
    tap = float(branch[8])
    shift = float(branch[9])
    return (tap != 0.0 and not math.isclose(tap, 1.0)) or not math.isclose(shift, 0.0)


def source_branch_kind(
    case: dict[str, Any],
    branch: list[float],
) -> str:
    if is_transformer(branch):
        return "trafo"
    base_kv = {
        int(row[0]): float(row[9])
        for row in case["bus"]
    }
    if not math.isclose(
        base_kv[int(branch[0])],
        base_kv[int(branch[1])],
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        # pandapower's MATPOWER importer represents a unit-tap, cross-voltage
        # branch as an impedance.  It remains in the electrical model, while
        # the released line/transformer state contract intentionally excludes
        # impedance-table rows.
        return "impedance"
    return "line"


def endpoint_key(first: int, second: int) -> tuple[int, int]:
    return tuple(sorted((int(first), int(second))))


def component_key(identity: dict[str, Any]) -> tuple[str, int, int, int]:
    element = str(identity["element"])
    if element == "line":
        first = int(identity["from_bus"]["source_bus_id"])
        second = int(identity["to_bus"]["source_bus_id"])
    elif element == "trafo":
        first = int(identity["hv_bus"]["source_bus_id"])
        second = int(identity["lv_bus"]["source_bus_id"])
    else:
        raise ValueError(f"unsupported branch identity: {element}")
    return (
        element,
        *endpoint_key(first, second),
        int(identity.get("source_endpoint_ordinal", 0)),
    )


def generator_source_index(
    case: dict[str, Any],
    identity: dict[str, Any],
    *,
    require_in_service: bool = True,
) -> int:
    source_row = identity.get("pglib_source_row")
    if source_row is not None:
        source_index = int(source_row) - 1
        if not 0 <= source_index < len(case["gen"]):
            raise ValueError(f"generator source row is out of range: {source_row}")
    else:
        source_bus = int(identity["bus"]["source_bus_id"])
        candidates = [
            index
            for index, row in enumerate(case["gen"])
            if int(row[0]) == source_bus
            and (not require_in_service or int(row[7]) != 0)
        ]
        ordinal = int(identity.get("source_bus_ordinal", 0))
        if ordinal >= len(candidates):
            raise ValueError(
                f"generator ordinal is absent at source bus {source_bus}: {ordinal}"
            )
        source_index = candidates[ordinal]
    if require_in_service and int(case["gen"][source_index][7]) == 0:
        raise ValueError(
            f"generator source row {source_index + 1} is out of service"
        )
    return source_index


def native_branch_index(case: dict[str, Any]) -> dict[tuple[str, int, int, int], int]:
    ordinals: dict[tuple[str, int, int], int] = defaultdict(int)
    result = {}
    for source_index, row in enumerate(case["branch"]):
        element = source_branch_kind(case, row)
        if element == "impedance":
            continue
        endpoints = endpoint_key(int(row[0]), int(row[1]))
        prefix = (element, *endpoints)
        ordinal = ordinals[prefix]
        ordinals[prefix] += 1
        result[(*prefix, ordinal)] = source_index
    return result


def apply_manifest_case(case: dict[str, Any], specification: dict[str, Any]) -> None:
    load_factor = float(specification.get("load_scaling_factor") or 1.0)
    generator_factor = float(specification.get("generator_scaling_factor") or 1.0)
    for row in case["bus"]:
        row[2] *= load_factor
        row[3] *= load_factor
    bus_type_by_id = {int(row[0]): int(row[1]) for row in case["bus"]}
    for row in case["gen"]:
        if int(row[7]) != 0 and bus_type_by_id[int(row[0])] == 2:
            row[1] *= generator_factor

    lookup = native_branch_index(case)
    for identity in (specification.get("contingency") or {}).get("components") or []:
        element = str(identity.get("element") or "")
        if element in {"line", "trafo"}:
            key = component_key(identity)
            if key not in lookup:
                raise ValueError(
                    f"contingency component is absent from native case: {key}"
                )
            case["branch"][lookup[key]][10] = 0.0
        elif element in {"gen", "ext_grid"}:
            source_index = generator_source_index(case, identity)
            case["gen"][source_index][7] = 0.0
        elif element == "bus":
            source_bus = int(identity["source_bus_id"])
            matching_buses = [
                row for row in case["bus"] if int(row[0]) == source_bus
            ]
            if len(matching_buses) != 1:
                raise ValueError(f"outaged source bus is absent: {source_bus}")
            bus_row = matching_buses[0]
            bus_row[1] = 4.0
            for field in (2, 3, 4, 5):
                bus_row[field] = 0.0
            for row in case["branch"]:
                if source_bus in {int(row[0]), int(row[1])}:
                    row[10] = 0.0
            for row in case["gen"]:
                if int(row[0]) == source_bus:
                    row[7] = 0.0
        else:
            raise ValueError(f"unsupported contingency component: {element}")

    for control in specification.get("post_action_controls") or []:
        source_index = generator_source_index(case, control)
        row = case["gen"][source_index]
        row[1] = float(control["p_mw"])
        row[2] = float(control["q_mvar"])
        row[5] = float(control["vm_pu"])


def build_grid_model(
    case: dict[str, Any],
    *,
    slack_source_rows: set[int] | None = None,
) -> tuple[GridModel, dict[str, Any]]:
    bus_rows = case["bus"]
    branch_rows = case["branch"]
    generator_rows = case["gen"]
    bus_ids = [int(row[0]) for row in bus_rows]
    bus_index = {source_id: index for index, source_id in enumerate(bus_ids)}
    if len(bus_index) != len(bus_ids):
        raise ValueError("MATPOWER bus identifiers are not unique")

    active_lines = [
        (source_index, row)
        for source_index, row in enumerate(branch_rows)
        if int(row[10]) != 0 and source_branch_kind(case, row) != "trafo"
    ]
    active_transformers = [
        (source_index, row)
        for source_index, row in enumerate(branch_rows)
        if int(row[10]) != 0 and is_transformer(row)
    ]
    model = GridModel()
    model.set_sn_mva(float(case["base_mva"]))
    model.init_bus(
        np.asarray([row[9] for row in bus_rows], dtype=float),
        len(active_lines),
        len(active_transformers),
    )
    if active_lines:
        rows = [row for _, row in active_lines]
        model.init_powerlines_full(
            np.asarray([row[2] for row in rows], dtype=float),
            np.asarray([row[3] for row in rows], dtype=float),
            np.asarray([0.5j * row[4] for row in rows], dtype=complex),
            np.asarray([0.5j * row[4] for row in rows], dtype=complex),
            np.asarray([bus_index[int(row[0])] for row in rows], dtype=np.int32),
            np.asarray([bus_index[int(row[1])] for row in rows], dtype=np.int32),
        )
    if active_transformers:
        rows = [row for _, row in active_transformers]
        tap = np.asarray(
            [row[8] if row[8] != 0.0 else 1.0 for row in rows],
            dtype=float,
        )
        model.init_trafo(
            np.asarray([row[2] for row in rows], dtype=float),
            np.asarray([row[3] for row in rows], dtype=float),
            np.asarray([-1j * row[4] for row in rows], dtype=complex),
            100.0 * (tap - 1.0),
            np.ones(len(rows), dtype=float),
            np.asarray([row[9] for row in rows], dtype=float),
            [True] * len(rows),
            np.asarray([bus_index[int(row[0])] for row in rows], dtype=np.int32),
            np.asarray([bus_index[int(row[1])] for row in rows], dtype=np.int32),
        )

    load_rows = [row for row in bus_rows if row[2] != 0.0 or row[3] != 0.0]
    if load_rows:
        model.init_loads(
            np.asarray([row[2] for row in load_rows], dtype=float),
            np.asarray([row[3] for row in load_rows], dtype=float),
            np.asarray([bus_index[int(row[0])] for row in load_rows], dtype=np.int32),
        )
    shunt_rows = [row for row in bus_rows if row[4] != 0.0 or row[5] != 0.0]
    if shunt_rows:
        model.init_shunt(
            np.asarray([row[4] for row in shunt_rows], dtype=float),
            np.asarray([-row[5] for row in shunt_rows], dtype=float),
            np.asarray([bus_index[int(row[0])] for row in shunt_rows], dtype=np.int32),
        )

    active_generators = [
        (source_index, row)
        for source_index, row in enumerate(generator_rows)
        if int(row[7]) != 0
        and int(bus_rows[bus_index[int(row[0])]][1]) in {2, 3}
    ]
    if not active_generators:
        raise ValueError("native case has no in-service generator")
    rows = [row for _, row in active_generators]
    model.init_generators(
        np.asarray([row[1] for row in rows], dtype=float),
        np.asarray([row[5] for row in rows], dtype=float),
        np.asarray([row[4] for row in rows], dtype=float),
        np.asarray([row[3] for row in rows], dtype=float),
        np.asarray([bus_index[int(row[0])] for row in rows], dtype=np.int32),
    )
    static_generators = [
        (source_index, row)
        for source_index, row in enumerate(generator_rows)
        if int(row[7]) != 0
        and int(bus_rows[bus_index[int(row[0])]][1]) not in {2, 3}
    ]
    if static_generators:
        rows = [row for _, row in static_generators]
        model.init_sgens(
            np.asarray([row[1] for row in rows], dtype=float),
            np.asarray([row[2] for row in rows], dtype=float),
            np.asarray([row[9] if len(row) > 9 else -99999.0 for row in rows], dtype=float),
            np.asarray([row[8] if len(row) > 8 else 99999.0 for row in rows], dtype=float),
            np.asarray([row[4] for row in rows], dtype=float),
            np.asarray([row[3] for row in rows], dtype=float),
            np.asarray([bus_index[int(row[0])] for row in rows], dtype=np.int32),
        )
    reference_buses = {
        int(row[0]) for row in bus_rows if int(row[1]) == 3
    }
    if slack_source_rows:
        slack_candidates = [
            model_index
            for model_index, (source_index, _) in enumerate(active_generators)
            if source_index + 1 in slack_source_rows
        ]
        missing_slack_rows = slack_source_rows - {
            source_index + 1
            for source_index, _ in active_generators
        }
        if missing_slack_rows:
            raise ValueError(
                f"manifest slack rows are absent from active native generators: "
                f"{sorted(missing_slack_rows)}"
            )
    else:
        slack_candidates = [
            model_index
            for model_index, (_, row) in enumerate(active_generators)
            if int(row[0]) in reference_buses
        ]
    if not slack_candidates:
        slack_candidates = [
            max(
                range(len(active_generators)),
                key=lambda index: abs(float(active_generators[index][1][1])),
            )
        ]
    for model_index in slack_candidates:
        model.add_gen_slackbus(model_index, 1.0)
    for model_bus_index, row in enumerate(bus_rows):
        if int(row[1]) == 4:
            model.deactivate_bus(model_bus_index)

    branch_models = {}
    loss_branch_models = {}
    ordinals: dict[tuple[str, int, int], int] = defaultdict(int)
    line_model_index = 0
    trafo_model_index = 0
    for source_index, row in enumerate(branch_rows):
        source_element = source_branch_kind(case, row)
        element = "trafo" if source_element == "trafo" else "line"
        endpoints = endpoint_key(int(row[0]), int(row[1]))
        identity_prefix = (source_element, *endpoints)
        ordinal = ordinals[identity_prefix]
        ordinals[identity_prefix] += 1
        if int(row[10]) == 0:
            continue
        model_index = trafo_model_index if element == "trafo" else line_model_index
        if element == "trafo":
            trafo_model_index += 1
        else:
            line_model_index += 1
        descriptor = {
            "element": element,
            "source_element": source_element,
            "model_index": model_index,
            "source_index": source_index,
            "rate_a_mva": float(row[5]),
            "from_bus_model_index": bus_index[int(row[0])],
            "to_bus_model_index": bus_index[int(row[1])],
        }
        loss_branch_models[source_index] = descriptor
        if source_element != "impedance":
            branch_models[(*identity_prefix, ordinal)] = descriptor
    initial_voltage = np.asarray(
        [
            row[7] * np.exp(1j * np.deg2rad(row[8]))
            for row in bus_rows
        ],
        dtype=complex,
    )
    return model, {
        "bus_ids": bus_ids,
        "initial_voltage": initial_voltage,
        "branch_models": branch_models,
        "loss_branch_models": loss_branch_models,
        "active_generator_source_rows": [index for index, _ in active_generators],
    }


def branch_result(
    model: GridModel,
    descriptor: dict[str, Any],
    voltage: np.ndarray,
) -> dict[str, float]:
    index = int(descriptor["model_index"])
    if descriptor["element"] == "line":
        row = model.get_lines()[index]
        result = {
            "p_from_mw": float(row.res_p_or_mw),
            "q_from_mvar": float(row.res_q_or_mvar),
            "p_to_mw": float(row.res_p_ex_mw),
            "q_to_mvar": float(row.res_q_ex_mvar),
        }
    else:
        row = model.get_trafos()[index]
        result = {
            "p_hv_mw": float(row.res_p_hv_mw),
            "q_hv_mvar": float(row.res_q_hv_mvar),
            "p_lv_mw": float(row.res_p_lv_mw),
            "q_lv_mvar": float(row.res_q_lv_mvar),
        }
    values = list(result.values())
    from_vm_pu = float(abs(voltage[int(descriptor["from_bus_model_index"])]))
    to_vm_pu = float(abs(voltage[int(descriptor["to_bus_model_index"])]))
    if (
        not math.isfinite(from_vm_pu)
        or not math.isfinite(to_vm_pu)
        or from_vm_pu <= 0.0
        or to_vm_pu <= 0.0
    ):
        raise ValueError("branch terminal voltage is non-finite or non-positive")
    # pandapower's MATPOWER import converts RATE_A to a rated current at the
    # nominal terminal voltage.  Its default current-based loading therefore
    # equals |S| / (vm_pu * RATE_A) at each terminal.  Reproducing this
    # definition is essential: |S| / RATE_A alone can flip overload labels
    # whenever the operating voltage differs from 1 p.u.
    loading_from = math.hypot(values[0], values[1]) / from_vm_pu
    loading_to = math.hypot(values[2], values[3]) / to_vm_pu
    result["loading_percent"] = (
        100.0
        * max(loading_from, loading_to)
        / float(descriptor["rate_a_mva"])
    )
    return result


def voltage_violation_label(
    minimum_pu: float,
    maximum_pu: float,
    *,
    boundary_tolerance_pu: float,
) -> bool:
    """Classify a voltage violation outside the registered numerical band."""
    return (
        float(minimum_pu) < 0.95 - boundary_tolerance_pu
        or float(maximum_pu) > 1.05 + boundary_tolerance_pu
    )


def compare_case(
    model: GridModel,
    metadata: dict[str, Any],
    specification: dict[str, Any],
    voltage: np.ndarray,
    *,
    voltage_label_boundary_tolerance_pu: float,
) -> dict[str, Any]:
    source_bus_to_model = {
        source_id: index for index, source_id in enumerate(metadata["bus_ids"])
    }
    bus_errors = []
    angle_errors = []
    reference_buses = (specification.get("reference_state") or {}).get("buses") or []
    reference_angle_offset = float(reference_buses[0]["va_degree"]) if reference_buses else 0.0
    angle_anchor_source_id = (
        int(reference_buses[0]["source_bus_id"]) if reference_buses else None
    )
    native_angle_offset = (
        float(
            np.angle(
                voltage[source_bus_to_model[angle_anchor_source_id]],
                deg=True,
            )
        )
        if angle_anchor_source_id is not None and len(voltage)
        else 0.0
    )
    for row in reference_buses:
        source_id = int(row["source_bus_id"])
        model_index = source_bus_to_model[source_id]
        bus_errors.append(abs(float(abs(voltage[model_index])) - float(row["vm_pu"])))
        angle_errors.append(
            abs(
                (float(np.angle(voltage[model_index], deg=True)) - native_angle_offset)
                - (float(row["va_degree"]) - reference_angle_offset)
            )
        )

    active_errors = []
    reactive_errors = []
    native_loading = []
    reference_keys = set()
    for row in (specification.get("reference_state") or {}).get("branches") or []:
        key = component_key(row)
        reference_keys.add(key)
        descriptor = metadata["branch_models"].get(key)
        if descriptor is None:
            raise ValueError(f"reference branch is absent from native active topology: {key}")
        native = branch_result(model, descriptor, voltage)
        native_loading.append(native["loading_percent"])
        if row["element"] == "line":
            active_fields = ("p_from_mw", "p_to_mw")
            reactive_fields = ("q_from_mvar", "q_to_mvar")
        else:
            active_fields = ("p_hv_mw", "p_lv_mw")
            reactive_fields = ("q_hv_mvar", "q_lv_mvar")
        active_errors.extend(abs(native[field] - float(row[field])) for field in active_fields)
        reactive_errors.extend(
            abs(native[field] - float(row[field])) for field in reactive_fields
        )

    native_keys = set(metadata["branch_models"])
    topology_match = native_keys == reference_keys
    voltage_magnitudes = np.abs(voltage)
    max_loading = max(native_loading, default=0.0)
    reference_result = specification.get("reference_result") or {}
    reference_thermal = float(reference_result.get("max_branch_loading_percent") or 0.0) > 100.0
    reference_voltage = voltage_violation_label(
        float(reference_result.get("min_bus_voltage_pu") or 1.0),
        float(reference_result.get("max_bus_voltage_pu") or 1.0),
        boundary_tolerance_pu=voltage_label_boundary_tolerance_pu,
    )
    # LightSim element-info objects are short-lived C++ views.  Consume each
    # container before requesting a different one.
    generation_mw = sum(
        float(row.res_p_mw) for row in model.get_generators()
    )
    generation_mw += sum(
        float(row.res_p_mw) for row in model.get_static_generators()
    )
    demand_mw = sum(float(row.res_p_mw) for row in model.get_loads())
    shunt_mw = sum(float(row.res_p_mw) for row in model.get_shunts())
    losses_mw = 0.0
    for descriptor in metadata["loss_branch_models"].values():
        values = branch_result(model, descriptor, voltage)
        if descriptor["element"] == "line":
            losses_mw += values["p_from_mw"] + values["p_to_mw"]
        else:
            losses_mw += values["p_hv_mw"] + values["p_lv_mw"]
    balance_residual = abs(generation_mw - demand_mw - shunt_mw - losses_mw)
    return {
        "independent_result": {
            "converged": len(voltage) == len(metadata["bus_ids"]),
            "min_bus_voltage_pu": float(voltage_magnitudes.min()),
            "max_bus_voltage_pu": float(voltage_magnitudes.max()),
            "max_branch_loading_percent": max_loading,
            "generation_mw": generation_mw,
            "demand_mw": demand_mw,
            "network_losses_mw": losses_mw,
            "power_balance_residual_mw": balance_residual,
        },
        "comparison": {
            "max_abs_bus_voltage_error_pu": max(bus_errors, default=0.0),
            "max_abs_bus_angle_error_degree": max(angle_errors, default=0.0),
            "max_abs_active_power_error_mw": max(active_errors, default=0.0),
            "max_abs_reactive_power_error_mvar": max(reactive_errors, default=0.0),
            "topology_identity_match": topology_match,
            "thermal_label_match": (max_loading > 100.0) == reference_thermal,
            "voltage_label_match": voltage_violation_label(
                float(voltage_magnitudes.min()),
                float(voltage_magnitudes.max()),
                boundary_tolerance_pu=voltage_label_boundary_tolerance_pu,
            )
            == reference_voltage,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-manifest", type=Path, required=True)
    parser.add_argument("--pglib-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/independent_solver_raw_evidence_v1.2_sd_core.json",
    )
    parser.add_argument("--max-iterations", type=int, default=100)
    parser.add_argument("--solver-tolerance", type=float, default=1e-10)
    parser.add_argument("--voltage-tolerance", type=float, default=1e-6)
    parser.add_argument("--angle-tolerance", type=float, default=1e-4)
    parser.add_argument("--active-power-tolerance", type=float, default=1e-4)
    parser.add_argument("--reactive-power-tolerance", type=float, default=1e-4)
    parser.add_argument("--power-balance-tolerance", type=float, default=1e-5)
    args = parser.parse_args()
    manifest = read_json(args.case_manifest)
    if (manifest.get("solver_scope") or {}).get("task") != (
        "fixed-injection fixed-control AC power-flow replay"
    ):
        raise ValueError("case manifest does not register the fixed-control replay scope")
    specifications = manifest.get("cases") or []
    if not specifications:
        raise ValueError("case manifest has no native-parameter replay cases")
    cases = []
    for specification in specifications:
        if (
            specification.get("source_model_class")
            != "pglib_native_parameters_and_operating_point"
            or specification.get("native_parameter_replay_eligible") is not True
        ):
            raise ValueError(
                f"{specification.get('case_id')} is not eligible for native replay"
            )
        source = specification["source_case"]
        source_path = args.pglib_root / str(source["identifier"])
        native_case = load_native_case(source_path, str(source["sha256"]))
        apply_manifest_case(native_case, specification)
        slack_source_rows = {
            generator_source_index(
                native_case,
                identity,
                require_in_service=True,
            )
            + 1
            for identity in specification.get("reference_generators") or []
        }
        voltage = np.asarray([], dtype=complex)
        model = None
        metadata = None
        initialization_strategy = None
        solver_error = None
        for strategy in ("source_voltage", "flat_voltage", "dc_warm_start"):
            model, metadata = build_grid_model(
                native_case,
                slack_source_rows=slack_source_rows or None,
            )
            if strategy == "source_voltage":
                initial_voltage = metadata["initial_voltage"].copy()
            else:
                initial_voltage = np.ones(
                    len(metadata["bus_ids"]),
                    dtype=complex,
                )
                if strategy == "dc_warm_start":
                    initial_voltage = model.dc_pf(
                        initial_voltage,
                        args.max_iterations,
                        args.solver_tolerance,
                    )
                    if len(initial_voltage) != len(metadata["bus_ids"]):
                        solver_error = str(model.get_solver().get_error())
                        continue
            voltage = model.ac_pf(
                initial_voltage,
                args.max_iterations,
                args.solver_tolerance,
            )
            solver_error = str(model.get_solver().get_error())
            if len(voltage) == len(metadata["bus_ids"]):
                initialization_strategy = strategy
                break
        assert model is not None and metadata is not None
        if len(voltage) != len(metadata["bus_ids"]):
            comparison = {
                "independent_result": {
                    "converged": False,
                    "solver_error": solver_error,
                    "attempted_initialization_strategies": [
                        "source_voltage",
                        "flat_voltage",
                        "dc_warm_start",
                    ],
                    "expected_bus_count": len(metadata["bus_ids"]),
                    "returned_voltage_count": len(voltage),
                    "power_balance_residual_mw": None,
                },
                "comparison": {
                    "max_abs_bus_voltage_error_pu": None,
                    "max_abs_bus_angle_error_degree": None,
                    "max_abs_active_power_error_mw": None,
                    "max_abs_reactive_power_error_mvar": None,
                    "topology_identity_match": False,
                    "thermal_label_match": False,
                    "voltage_label_match": False,
                },
            }
        else:
            comparison = compare_case(
                model,
                metadata,
                specification,
                voltage,
                voltage_label_boundary_tolerance_pu=args.voltage_tolerance,
            )
            comparison["independent_result"]["initialization_strategy"] = (
                initialization_strategy
            )
        cases.append(
            {
                "case_id": specification["case_id"],
                "network_model": specification["network_model"],
                "source_model_class": specification["source_model_class"],
                "source_case": source,
                **comparison,
            }
        )
    payload = {
        "contract_version": "gridinstruct-independent-solver-evidence-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "solver": {
            "name": "LightSim2Grid",
            "version": importlib.metadata.version("lightsim2grid"),
            "implementation_repository": "https://github.com/Grid2op/lightsim2grid",
            "independence_class": "native_source_case",
            "input_import_path": "direct MATPOWER text parser to LightSim2Grid C++ GridModel",
            "numerical_method": "independent Newton-Raphson sparse AC power flow",
            "comparison_scope": "fixed-injection fixed-control AC power-flow replay",
            "q_limit_policy": (
                "enforce_q_lims=False; Q-bound feasibility is inherited only after "
                "the upstream target and constraint gates pass"
            ),
            "independent_opf_claimed": False,
        },
        "case_manifest": {
            "path": str(args.case_manifest),
            "sha256": sha256_file(args.case_manifest),
        },
        "tolerances": {
            "voltage_pu": args.voltage_tolerance,
            "angle_degree": args.angle_tolerance,
            "active_power_mw": args.active_power_tolerance,
            "reactive_power_mvar": args.reactive_power_tolerance,
            "power_balance_mw": args.power_balance_tolerance,
            "voltage_label_boundary_pu": args.voltage_tolerance,
        },
        "case_count": len(cases),
        "manifest_excluded_case_count": int(
            manifest.get("excluded_case_count") or 0
        ),
        "cases": cases,
    }
    ensure_dirs(args.output.parent)
    write_json(args.output, payload)
    def finite_max(field: str) -> float | None:
        values = [
            row["comparison"].get(field)
            for row in cases
        ]
        finite_values = [
            float(value)
            for value in values
            if value is not None and math.isfinite(float(value))
        ]
        return max(finite_values) if finite_values else None

    print(
        json.dumps(
            {
                "case_count": len(cases),
                "converged_case_count": sum(
                    row["independent_result"]["converged"] for row in cases
                ),
                "max_voltage_error_pu": finite_max(
                    "max_abs_bus_voltage_error_pu"
                ),
                "max_angle_error_degree": finite_max(
                    "max_abs_bus_angle_error_degree"
                ),
                "max_active_power_error_mw": finite_max(
                    "max_abs_active_power_error_mw"
                ),
                "max_reactive_power_error_mvar": finite_max(
                    "max_abs_reactive_power_error_mvar"
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
