#!/usr/bin/env python3
"""Replay published OPF actions under preregistered active-load uncertainty."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandapower as pp
from pandapower.topology import unsupplied_buses

from append_opf_closed_loop_auxiliary_records import (
    ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU,
    THERMAL_BOUNDARY_EPSILON_PERCENT,
    VOLTAGE_BOUNDARY_EPSILON_PU,
    build_executable_control_target,
    build_net,
    control_vector,
    electrical_state_complete,
    metrics,
    validate_executable_control_target,
)
from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json


CONTRACT_VERSION = "opf-action-active-load-uncertainty-stress-v2"
PREREGISTERED_ACTIVE_LOAD_STRESS = (-0.10, -0.05, -0.02, 0.02, 0.05, 0.10)
CONTROL_ELEMENTS = ("ext_grid", "gen")
CONTROL_FIELDS = ("p_mw", "q_mvar", "vm_pu")
SOURCE_FIELDS = ("repository_commit", "case_file", "case_sha256")
CASE_TYPES = ("base_stress", "registered_n1_stress")


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


def resolve_input_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def validate_output_locations(
    input_paths: list[Path],
    output_paths: list[Path],
) -> None:
    resolved_inputs = {path.resolve() for path in input_paths}
    resolved_outputs = [path.resolve() for path in output_paths]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ValueError("uncertainty-stress output paths must be unique")
    overlap = sorted(str(path) for path in set(resolved_outputs) & resolved_inputs)
    if overlap:
        raise ValueError(f"uncertainty-stress outputs overlap inputs: {overlap}")


def validate_opf_commit(results_path: Path, commit_path: Path) -> dict[str, Any]:
    commit = read_json(commit_path)
    if not isinstance(commit, dict):
        raise ValueError("OPF commit must be a JSON object")
    if commit.get("status") != "pass":
        raise ValueError("OPF commit status must be pass")
    artifact = (commit.get("artifacts") or {}).get("results")
    if not isinstance(artifact, dict):
        raise ValueError("OPF commit has no results artifact")
    declared_path = resolve_input_path(Path(str(artifact.get("path") or ""))).resolve()
    if declared_path != results_path.resolve():
        raise ValueError(
            f"OPF commit results path mismatch: {declared_path} != {results_path.resolve()}"
        )
    actual_sha256 = file_sha256(results_path)
    if str(artifact.get("sha256") or "") != actual_sha256:
        raise ValueError("OPF results SHA256 does not match the committed artifact")
    return {
        "commit_sha256": file_sha256(commit_path),
        "results_sha256": actual_sha256,
        "schema_version": commit.get("schema_version"),
        "status": "pass",
    }


def load_published_results(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("OPF results payload must contain a results list")
    results = payload["results"]
    scenario_ids = [str(row.get("scenario_id") or "") for row in results]
    if any(not value for value in scenario_ids):
        raise ValueError("OPF results contain a missing scenario_id")
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("OPF results contain duplicate scenario_id values")
    if not results:
        raise ValueError("OPF results contain no published actions")
    return sorted(
        results,
        key=lambda row: (str(row.get("system") or ""), str(row["scenario_id"])),
    )


def load_scenario_registry(paths: list[Path]) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for path in paths:
        rows = read_json(path)
        if not isinstance(rows, list):
            raise ValueError(f"scenario manifest must be a list: {path}")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"scenario manifest contains a non-object row: {path}")
            scenario_id = str(row.get("scenario_id") or "")
            if not scenario_id:
                raise ValueError(
                    f"scenario manifest contains a missing scenario_id: {path}"
                )
            if scenario_id in registry:
                raise ValueError(
                    f"duplicate scenario_id across manifests: {scenario_id}"
                )
            registry[scenario_id] = row
    return registry


def physical_source_binding(row: dict[str, Any]) -> tuple[str, str, str]:
    source = row.get("physical_source")
    if not isinstance(source, dict):
        raise ValueError(f"{row.get('scenario_id')} has no hash-bound physical_source")
    binding = tuple(str(source.get(field) or "") for field in SOURCE_FIELDS)
    if any(not value for value in binding):
        raise ValueError(
            f"{row.get('scenario_id')} has an incomplete physical_source binding"
        )
    return binding


def control_lookup(
    controls: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, int], dict[str, Any]]:
    lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for element in CONTROL_ELEMENTS:
        rows = controls.get(element)
        if not isinstance(rows, list):
            raise ValueError(f"post_action_controls.{element} must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(
                    f"post_action_controls.{element} contains a non-object"
                )
            key = (element, int(row.get("index", -1)))
            if key in lookup:
                raise ValueError(f"duplicate published control: {element}:{key[1]}")
            for field in CONTROL_FIELDS:
                if not math.isfinite(float(row.get(field))):
                    raise ValueError(
                        f"nonfinite published control: {element}:{key[1]}:{field}"
                    )
            lookup[key] = row
    return lookup


def registered_n1_check_lookup(
    result: dict[str, Any],
) -> dict[tuple[str, int], dict[str, Any]]:
    security = result.get("post_action_n1")
    if not isinstance(security, dict):
        raise ValueError(f"{result.get('scenario_id')} has no registered N-1 evidence")
    checks = security.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError(f"{result.get('scenario_id')} has an empty registered N-1 set")
    requested = int(security.get("requested_checks") or 0)
    completed = int(security.get("completed_checks") or 0)
    secure = int(security.get("secure_checks") or 0)
    if requested != len(checks) or completed != requested or secure != requested:
        raise ValueError(
            f"{result.get('scenario_id')} registered N-1 denominator is inconsistent"
        )
    lookup = {}
    for check in checks:
        element = str(check.get("element") or "")
        index = int(check.get("index", -1))
        if element not in {"line", "trafo", "gen"} or index < 0:
            raise ValueError(f"{result.get('scenario_id')} has an invalid N-1 identity")
        key = (element, index)
        if key in lookup:
            raise ValueError(f"{result.get('scenario_id')} repeats an N-1 identity")
        feasibility = check.get("control_feasibility")
        if (
            check.get("secure") is not True
            or check.get("solver_status") != "converged"
            or not isinstance(feasibility, dict)
            or feasibility.get("status") != "pass"
        ):
            raise ValueError(
                f"{result.get('scenario_id')} has invalid stored N-1 evidence: "
                f"{element}:{index}"
            )
        controls = feasibility.get("post_contingency_controls")
        if not isinstance(controls, dict):
            raise ValueError(
                f"{result.get('scenario_id')} has no stored N-1 corrective controls: "
                f"{element}:{index}"
            )
        control_lookup(controls)
        lookup[key] = check
    return dict(sorted(lookup.items()))


def registered_n1_identities(result: dict[str, Any]) -> list[tuple[str, int]]:
    return list(registered_n1_check_lookup(result))


def validate_registered_n1_control_sets(result: dict[str, Any], net: Any) -> None:
    scenario_id = str(result.get("scenario_id") or "")
    for (element, index), check in registered_n1_check_lookup(result).items():
        contingency_net = copy.deepcopy(net)
        getattr(contingency_net, element).at[index, "in_service"] = False
        controls = check["control_feasibility"]["post_contingency_controls"]
        lookup = control_lookup(controls)
        expected = {
            (control_element, int(control_index))
            for control_element in CONTROL_ELEMENTS
            for control_index in getattr(contingency_net, control_element).index
            if bool(
                getattr(contingency_net, control_element).at[
                    control_index, "in_service"
                ]
            )
        }
        allowed_extra = set()
        for key in set(lookup) - expected:
            control_element, control_index = key
            is_declared_out = (
                control_element == "gen"
                and control_index in contingency_net.gen.index
                and not bool(contingency_net.gen.at[control_index, "in_service"])
            )
            if is_declared_out and lookup[key].get("in_service") is False:
                allowed_extra.add(key)
                continue
            raise ValueError(
                f"{scenario_id} includes an out-of-service corrective control "
                f"without in_service=false: {control_element}:{control_index}"
            )
        if set(lookup) != expected | allowed_extra:
            missing = sorted(expected - set(lookup))
            unexpected = sorted(set(lookup) - expected - allowed_extra)
            raise ValueError(
                f"{scenario_id} N-1 corrective control vector is incomplete for "
                f"{element}:{index}; missing={missing[:10]} "
                f"unexpected={unexpected[:10]}"
            )


def validate_result_contract(
    result: dict[str, Any],
    scenario: dict[str, Any],
    net: Any,
) -> dict[str, Any]:
    scenario_id = str(result.get("scenario_id") or "")
    if scenario_id != str(scenario.get("scenario_id") or ""):
        raise ValueError(f"{scenario_id} scenario identity mismatch")
    if result.get("opf_status") != "converged":
        raise ValueError(f"{scenario_id} is not a published converged OPF action")
    if physical_source_binding(result) != physical_source_binding(scenario):
        raise ValueError(f"{scenario_id} physical_source mismatch")

    controls = result.get("post_action_controls")
    if not isinstance(controls, dict):
        raise ValueError(f"{scenario_id} has no complete post_action_controls")
    lookup = control_lookup(controls)
    expected = {
        (element, int(index))
        for element in CONTROL_ELEMENTS
        for index in getattr(net, element).index
    }
    if set(lookup) != expected:
        missing = sorted(expected - set(lookup))
        unexpected = sorted(set(lookup) - expected)
        raise ValueError(
            f"{scenario_id} control vector is incomplete; "
            f"missing={missing[:10]} unexpected={unexpected[:10]}"
        )

    target = build_executable_control_target(result)
    target_errors = validate_executable_control_target(target, result)
    if target_errors:
        raise ValueError(
            f"{scenario_id} executable target is invalid: {target_errors[:10]}"
        )

    bounds = {
        (str(row.get("element") or ""), int(row.get("index", -1)))
        for row in (result.get("constraint_policy") or {}).get("control_bounds") or []
    }
    if bounds != expected:
        raise ValueError(f"{scenario_id} control-bound set is incomplete")
    identities = registered_n1_identities(result)
    for element, index in identities:
        if index not in getattr(net, element).index:
            raise ValueError(f"{scenario_id} N-1 identity is absent: {element}:{index}")
    validate_registered_n1_control_sets(result, net)

    return {
        "control_count": len(lookup),
        "ext_grid_control_count": len(controls["ext_grid"]),
        "generator_control_count": len(controls["gen"]),
        "registered_n1_count": len(identities),
        "source_binding": list(physical_source_binding(result)),
        "target_evidence_sha256": target["replay_contract"][
            "expected_post_action_evidence_sha256"
        ],
        "status": "pass",
    }


def apply_control_vector(net: Any, controls: dict[str, list[dict[str, Any]]]) -> None:
    for row in controls["gen"]:
        index = int(row["index"])
        if bool(net.gen.at[index, "in_service"]):
            net.gen.at[index, "p_mw"] = float(row["p_mw"])
            net.gen.at[index, "vm_pu"] = float(row["vm_pu"])
    for row in controls["ext_grid"]:
        index = int(row["index"])
        if bool(net.ext_grid.at[index, "in_service"]):
            net.ext_grid.at[index, "vm_pu"] = float(row["vm_pu"])


def apply_published_action(net: Any, result: dict[str, Any]) -> None:
    apply_control_vector(net, result["post_action_controls"])


def run_power_flow(net: Any, *, init: str = "auto") -> None:
    pp.runpp(
        net,
        algorithm="nr",
        init=init,
        tolerance_mva=1e-8,
        max_iteration=80,
        enforce_q_lims=True,
        numba=False,
    )


def validate_control_replay(
    net: Any,
    expected_controls: dict[str, list[dict[str, Any]]],
    *,
    label: str,
    outaged_generator: int | None,
    active_power_tolerance_mw: float,
    reactive_power_tolerance_mvar: float,
    voltage_tolerance_pu: float,
) -> dict[str, Any]:
    run_power_flow(net)
    actual = control_lookup(control_vector(net))
    expected = {
        key: row
        for key, row in control_lookup(expected_controls).items()
        if row.get("in_service") is not False
    }
    if outaged_generator is not None:
        expected.pop(("gen", outaged_generator), None)
    maximum = {"p_mw": 0.0, "q_mvar": 0.0, "vm_pu": 0.0}
    errors = []
    tolerances = {
        "p_mw": active_power_tolerance_mw,
        "q_mvar": reactive_power_tolerance_mvar,
        "vm_pu": voltage_tolerance_pu,
    }
    for key in sorted(expected):
        for field in CONTROL_FIELDS:
            error = abs(float(actual[key][field]) - float(expected[key][field]))
            maximum[field] = max(maximum[field], error)
            if error > tolerances[field]:
                errors.append(f"{key[0]}:{key[1]}:{field}:{error:.12g}")
    if errors:
        raise ValueError(
            f"{label} zero-stress full-control replay failed: {errors[:10]}"
        )
    return {
        "maximum_absolute_error": maximum,
        "validated_control_count": len(expected),
        "status": "pass",
    }


def validate_zero_stress_replay(
    net: Any,
    result: dict[str, Any],
    *,
    active_power_tolerance_mw: float,
    reactive_power_tolerance_mvar: float,
    voltage_tolerance_pu: float,
) -> dict[str, Any]:
    return validate_control_replay(
        net,
        result["post_action_controls"],
        label=str(result.get("scenario_id") or ""),
        outaged_generator=None,
        active_power_tolerance_mw=active_power_tolerance_mw,
        reactive_power_tolerance_mvar=reactive_power_tolerance_mvar,
        voltage_tolerance_pu=voltage_tolerance_pu,
    )


def validate_zero_stress_registered_n1_replays(
    action_net: Any,
    result: dict[str, Any],
    *,
    active_power_tolerance_mw: float,
    reactive_power_tolerance_mvar: float,
    voltage_tolerance_pu: float,
    balance_tolerance_mw: float,
) -> list[dict[str, Any]]:
    rows = []
    for (element, index), check in registered_n1_check_lookup(result).items():
        net = copy.deepcopy(action_net)
        getattr(net, element).at[index, "in_service"] = False
        disconnected = sorted(int(value) for value in unsupplied_buses(net))
        if disconnected:
            raise ValueError(
                f"{result.get('scenario_id')} stored N-1 replay islands buses "
                f"for {element}:{index}: {disconnected}"
            )
        controls = check["control_feasibility"]["post_contingency_controls"]
        apply_control_vector(net, controls)
        replay = validate_control_replay(
            net,
            controls,
            label=f"{result.get('scenario_id')}:{element}:{index}",
            outaged_generator=index if element == "gen" else None,
            active_power_tolerance_mw=active_power_tolerance_mw,
            reactive_power_tolerance_mvar=reactive_power_tolerance_mvar,
            voltage_tolerance_pu=voltage_tolerance_pu,
        )
        measured = case_metrics(
            net,
            result,
            balance_tolerance_mw=balance_tolerance_mw,
            outaged_generator=index if element == "gen" else None,
        )
        if measured["safe"] is not True:
            raise ValueError(
                f"{result.get('scenario_id')} stored zero-stress N-1 corrective "
                f"control is unsafe for {element}:{index}"
            )
        rows.append(
            {
                "element": element,
                "index": index,
                "control_replay": replay,
                "safety": measured,
                "status": "pass",
            }
        )
    return rows


def apply_active_load_stress(net: Any, stress_fraction: float) -> dict[str, float]:
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
        net.load.loc[active_indices, "p_mw"] *= 1.0 + stress_fraction
    after_p_mw = (
        float(net.load.loc[active_indices, "p_mw"].sum()) if active_indices else 0.0
    )
    after_q_mvar = (
        float(net.load.loc[active_indices, "q_mvar"].sum()) if active_indices else 0.0
    )
    return {
        "active_load_before_mw": before_p_mw,
        "active_load_after_mw": after_p_mw,
        "active_load_perturbation_mw": after_p_mw - before_p_mw,
        "reactive_load_before_mvar": before_q_mvar,
        "reactive_load_after_mvar": after_q_mvar,
    }


def bound_lookup(result: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(row["element"]), int(row["index"])): row
        for row in (result.get("constraint_policy") or {}).get("control_bounds") or []
    }


def control_capability(
    net: Any,
    result: dict[str, Any],
    *,
    outaged_generator: int | None,
    tolerance: float = 1e-4,
) -> dict[str, Any]:
    bounds = bound_lookup(result)
    observed = control_lookup(control_vector(net))
    violations = []
    for key, row in observed.items():
        element, index = key
        if element == "gen" and (
            index == outaged_generator or not bool(net.gen.at[index, "in_service"])
        ):
            continue
        if element == "ext_grid" and not bool(net.ext_grid.at[index, "in_service"]):
            continue
        bound = bounds.get(key)
        if bound is None:
            violations.append(f"missing_bound:{element}:{index}")
            continue
        for field, bound_field in (
            ("p_mw", "contingency_p_bounds_mw"),
            ("q_mvar", "contingency_q_bounds_mvar"),
        ):
            lower, upper = [float(value) for value in bound[bound_field]]
            value = float(row[field])
            if not lower - tolerance <= value <= upper + tolerance:
                violations.append(
                    f"{element}:{index}:{field}:{value:.6f}:"
                    f"outside:{lower:.6f}:{upper:.6f}"
                )
    return {
        "status": "pass" if not violations else "fail",
        "violations": violations,
    }


def external_balance(
    net: Any,
    reference_controls: dict[str, list[dict[str, Any]]],
    load_stress: dict[str, float],
    *,
    reference_losses_mw: float,
    realized_losses_mw: float,
) -> dict[str, Any]:
    published = {int(row["index"]): row for row in reference_controls["ext_grid"]}
    rows = []
    for index in net.ext_grid.index:
        if not bool(net.ext_grid.at[index, "in_service"]):
            continue
        actual_p = float(net.res_ext_grid.at[index, "p_mw"])
        actual_q = float(net.res_ext_grid.at[index, "q_mvar"])
        baseline = published[int(index)]
        rows.append(
            {
                "index": int(index),
                "published_p_mw": float(baseline["p_mw"]),
                "published_q_mvar": float(baseline["q_mvar"]),
                "realized_p_mw": actual_p,
                "realized_q_mvar": actual_q,
                "active_balance_adjustment_mw": actual_p - float(baseline["p_mw"]),
                "reactive_balance_adjustment_mvar": actual_q
                - float(baseline["q_mvar"]),
            }
        )
    total_active_adjustment = sum(
        row["active_balance_adjustment_mw"] for row in rows
    )
    incremental_losses_mw = realized_losses_mw - reference_losses_mw
    incremental_balance_residual_mw = abs(
        total_active_adjustment
        - load_stress["active_load_perturbation_mw"]
        - incremental_losses_mw
    )
    return {
        "policy": (
            "all non-slack generator active-power and voltage setpoints remain at "
            "the published OPF action; in-service external-grid buses balance the "
            "active-load perturbation and incremental AC losses, while generator "
            "reactive outputs respond through the AC power-flow equations"
        ),
        "active_load_perturbation_mw": load_stress["active_load_perturbation_mw"],
        "external_grid": rows,
        "total_active_balance_adjustment_mw": total_active_adjustment,
        "total_reactive_balance_adjustment_mvar": sum(
            row["reactive_balance_adjustment_mvar"] for row in rows
        ),
        "reference_network_losses_mw": reference_losses_mw,
        "realized_network_losses_mw": realized_losses_mw,
        "incremental_network_losses_mw": incremental_losses_mw,
        "incremental_active_balance_residual_mw": incremental_balance_residual_mw,
    }


def registered_network_limit_violations(
    net: Any,
    result: dict[str, Any],
    *,
    tolerance: float = 1e-6,
) -> list[str]:
    violations = []
    limits = (result.get("constraint_policy") or {}).get("network_limits") or []
    for row in limits:
        element = str(row.get("element") or "")
        index = int(row.get("index", -1))
        if element == "bus":
            if index not in net.res_bus.index or not bool(net.bus.at[index, "in_service"]):
                continue
            lower, upper = [
                float(value)
                for value in (
                    row.get("validation")
                    or (result.get("constraint_policy") or {}).get(
                        "dataset_voltage_bounds_pu",
                        [0.95, 1.05],
                    )
                )
            ]
            value = float(net.res_bus.at[index, "vm_pu"])
            if not lower - tolerance <= value <= upper + tolerance:
                violations.append(
                    f"bus:{index}:vm_pu:{value:.8f}:outside:{lower:.8f}:{upper:.8f}"
                )
            continue
        if element not in {"line", "trafo"}:
            continue
        table = getattr(net, element)
        result_table = getattr(net, f"res_{element}")
        if index not in table.index or not bool(table.at[index, "in_service"]):
            continue
        native = row.get("native_max_loading_percent")
        limit = min(100.0, float(native)) if native is not None else 100.0
        value = float(result_table.at[index, "loading_percent"])
        if value > limit + tolerance:
            violations.append(
                f"{element}:{index}:loading_percent:{value:.8f}:above:{limit:.8f}"
            )
    return violations


def case_metrics(
    net: Any,
    result: dict[str, Any],
    *,
    balance_tolerance_mw: float,
    outaged_generator: int | None,
) -> dict[str, Any]:
    snapshot = metrics(net)
    voltage_min, voltage_max = [
        float(value)
        for value in (result.get("constraint_policy") or {}).get(
            "dataset_voltage_bounds_pu", [0.95, 1.05]
        )
    ]
    max_loading = float(snapshot["max_branch_loading_percent"])
    min_voltage = float(snapshot["min_bus_voltage_pu"])
    max_voltage = float(snapshot["max_bus_voltage_pu"])
    capability = control_capability(
        net,
        result,
        outaged_generator=outaged_generator,
    )
    state_complete = electrical_state_complete(net)
    network_limit_violations = registered_network_limit_violations(net, result)
    voltage_lower_margin = min_voltage - voltage_min
    voltage_upper_margin = voltage_max - max_voltage
    thermal_margin = 100.0 - max_loading
    safe = bool(
        state_complete
        and voltage_lower_margin >= -VOLTAGE_BOUNDARY_EPSILON_PU
        and voltage_upper_margin >= -VOLTAGE_BOUNDARY_EPSILON_PU
        and thermal_margin >= -THERMAL_BOUNDARY_EPSILON_PERCENT
        and float(snapshot["power_balance_residual_mw"]) <= balance_tolerance_mw
        and capability["status"] == "pass"
        and not network_limit_violations
    )
    return {
        **snapshot,
        "electrical_state_complete": state_complete,
        "voltage_lower_margin_pu": voltage_lower_margin,
        "voltage_upper_margin_pu": voltage_upper_margin,
        "worst_voltage_margin_pu": min(voltage_lower_margin, voltage_upper_margin),
        "thermal_margin_percent": thermal_margin,
        "control_capability": capability,
        "registered_network_limit_violations": network_limit_violations,
        "safe": safe,
    }


def stable_case_id(
    scenario_id: str,
    stress_fraction: float,
    element: str | None,
    index: int | None,
) -> str:
    identity = json.dumps(
        {
            "scenario_id": scenario_id,
            "stress_fraction": f"{stress_fraction:+.2f}",
            "element": element,
            "index": index,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def preregistered_case_specs(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    specs = []
    ordered = sorted(
        results,
        key=lambda row: (
            str(row.get("system") or ""),
            str(row.get("scenario_id") or ""),
        ),
    )
    for result in ordered:
        scenario_id = str(result["scenario_id"])
        identities = registered_n1_identities(result)
        for stress_fraction in PREREGISTERED_ACTIVE_LOAD_STRESS:
            specs.append(
                {
                    "case_id": stable_case_id(scenario_id, stress_fraction, None, None),
                    "scenario_id": scenario_id,
                    "system": str(result.get("system") or ""),
                    "stress_fraction": stress_fraction,
                    "case_type": "base_stress",
                    "contingency": None,
                }
            )
            for element, index in identities:
                specs.append(
                    {
                        "case_id": stable_case_id(
                            scenario_id, stress_fraction, element, index
                        ),
                        "scenario_id": scenario_id,
                        "system": str(result.get("system") or ""),
                        "stress_fraction": stress_fraction,
                        "case_type": "registered_n1_stress",
                        "contingency": {"element": element, "index": index},
                    }
                )
    return specs


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
        check = registered_n1_check_lookup(result)[(element, index)]
        reference_controls = check["control_feasibility"]["post_contingency_controls"]
        reference_metrics = check.get("metrics") or {}
        apply_control_vector(net, reference_controls)
        control_replay_policy = "stored_bounded_corrective_post_contingency_controls"
        disconnected = sorted(int(value) for value in unsupplied_buses(net))
    else:
        disconnected = sorted(int(value) for value in unsupplied_buses(net))
        reference_metrics = result.get("post_action") or {}
    load_stress = apply_active_load_stress(net, float(spec["stress_fraction"]))

    common = {
        **spec,
        "stress_percent": 100.0 * float(spec["stress_fraction"]),
        "active_load_model": (
            "uniform multiplicative perturbation of every in-service load.p_mw; "
            "load.q_mvar is unchanged"
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
        run_power_flow(net)
        measured = case_metrics(
            net,
            result,
            balance_tolerance_mw=balance_tolerance_mw,
            outaged_generator=outaged_generator,
        )
        balancing = external_balance(
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
        selected_margin = float(result.get("selected_loading_margin_percent") or 100.0)
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


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    converged_rows = [row for row in rows if row.get("solver_status") == "converged"]
    safe_rows = [row for row in rows if row.get("safe") is True]
    voltage_margins = [
        float(row["metrics"]["worst_voltage_margin_pu"]) for row in converged_rows
    ]
    thermal_margins = [
        float(row["metrics"]["thermal_margin_percent"]) for row in converged_rows
    ]
    balance_adjustments = [
        abs(float(row["external_balance"]["total_active_balance_adjustment_mw"]))
        for row in converged_rows
    ]
    incremental_balance_residuals = [
        abs(
            float(
                row["external_balance"]["incremental_active_balance_residual_mw"]
            )
        )
        for row in converged_rows
    ]
    selected_margin_flags = [
        bool(row["precontingency_margin_preserved"])
        for row in rows
        if row.get("precontingency_margin_preserved") is not None
    ]
    return {
        "registered_case_count": total,
        "converged_case_count": len(converged_rows),
        "safe_case_count": len(safe_rows),
        "convergence_rate": len(converged_rows) / total if total else None,
        "safety_rate_over_registered_denominator": len(safe_rows) / total
        if total
        else None,
        "solver_status_counts": dict(
            sorted(Counter(str(row.get("solver_status")) for row in rows).items())
        ),
        "minimum_worst_voltage_margin_pu": min(voltage_margins)
        if voltage_margins
        else None,
        "minimum_thermal_margin_percent": min(thermal_margins)
        if thermal_margins
        else None,
        "maximum_absolute_external_active_balance_adjustment_mw": max(
            balance_adjustments, default=None
        ),
        "maximum_incremental_active_balance_residual_mw": max(
            incremental_balance_residuals,
            default=None,
        ),
        "selected_precontingency_margin_preservation_rate": (
            sum(selected_margin_flags) / len(selected_margin_flags)
            if selected_margin_flags
            else None
        ),
    }


def grouped_statistics(
    rows: list[dict[str, Any]],
    key_function: Any,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_function(row))].append(row)
    return {key: summarize_rows(groups[key]) for key in sorted(groups)}


def optimization_diagnostics(
    rows: list[dict[str, Any]],
    *,
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    unsafe = []
    for row in rows:
        if row.get("safe") is True:
            continue
        unsafe.append(row)
        if row.get("solver_status") != "converged":
            reason_counts[f"solver:{row.get('error_type') or 'unknown'}"] += 1
            continue
        measured = row["metrics"]
        if measured.get("electrical_state_complete") is not True:
            reason_counts["incomplete_electrical_state"] += 1
        if float(measured["worst_voltage_margin_pu"]) < -VOLTAGE_BOUNDARY_EPSILON_PU:
            reason_counts["voltage_margin_negative"] += 1
        if (
            float(measured["thermal_margin_percent"])
            < -THERMAL_BOUNDARY_EPSILON_PERCENT
        ):
            reason_counts["thermal_margin_negative"] += 1
        if float(measured["power_balance_residual_mw"]) > balance_tolerance_mw:
            reason_counts["power_balance_residual"] += 1
        if (measured.get("control_capability") or {}).get("status") != "pass":
            reason_counts["control_capability"] += 1
        if measured.get("registered_network_limit_violations"):
            reason_counts["registered_network_limit"] += 1
        if (
            float(
                (row.get("external_balance") or {}).get(
                    "incremental_active_balance_residual_mw",
                    float("inf"),
                )
            )
            > balance_tolerance_mw
        ):
            reason_counts["incremental_active_balance_residual"] += 1

    converged = [row for row in rows if row.get("solver_status") == "converged"]

    def worst(metric: str) -> dict[str, Any] | None:
        if not converged:
            return None
        row = min(converged, key=lambda item: float(item["metrics"][metric]))
        return {
            "case_id": row["case_id"],
            "scenario_id": row["scenario_id"],
            "system": row["system"],
            "stress_percent": row["stress_percent"],
            "case_type": row["case_type"],
            "contingency": row["contingency"],
            metric: row["metrics"][metric],
        }

    return {
        "unsafe_case_count": len(unsafe),
        "unsafe_case_rate_over_registered_denominator": len(unsafe) / len(rows)
        if rows
        else None,
        "reason_counts": dict(sorted(reason_counts.items())),
        "worst_voltage_case": worst("worst_voltage_margin_pu"),
        "worst_thermal_case": worst("thermal_margin_percent"),
        "full_case_diagnostics_location": (
            "case-results artifact; every registered case is retained"
        ),
    }


def build_report(
    results: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    validation: list[dict[str, Any]],
    input_artifacts: dict[str, Any],
    *,
    balance_tolerance_mw: float,
) -> dict[str, Any]:
    expected_base = len(results) * len(PREREGISTERED_ACTIVE_LOAD_STRESS)
    expected_n1 = sum(
        len(registered_n1_identities(result)) for result in results
    ) * len(PREREGISTERED_ACTIVE_LOAD_STRESS)
    base_rows = [row for row in cases if row["case_type"] == "base_stress"]
    n1_rows = [row for row in cases if row["case_type"] == "registered_n1_stress"]
    denominator_complete = (
        len(cases) == expected_base + expected_n1
        and len(base_rows) == expected_base
        and len(n1_rows) == expected_n1
        and len({row["case_id"] for row in cases}) == len(cases)
    )
    if not denominator_complete:
        raise RuntimeError("registered stress denominator is incomplete")

    base_summary = summarize_rows(base_rows)
    n1_summary = summarize_rows(n1_rows)
    gates = {
        "input_contract_complete": len(validation) == len(results),
        "registered_denominator_complete": denominator_complete,
        "base_stress_convergence_rate_is_one": base_summary["convergence_rate"] == 1.0,
        "base_stress_safety_rate_is_one": (
            base_summary["safety_rate_over_registered_denominator"] == 1.0
        ),
        "registered_n1_convergence_rate_is_one": n1_summary["convergence_rate"] == 1.0,
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
            value is not None and float(value) >= -THERMAL_BOUNDARY_EPSILON_PERCENT
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
    integrity_pass = bool(
        gates["input_contract_complete"] and gates["registered_denominator_complete"]
    )
    robustness_pass = bool(all(gates.values()))
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "pass" if robustness_pass else "diagnostic",
        "integrity_status": "pass" if integrity_pass else "fail",
        "robustness_status": "pass" if robustness_pass else "needs_optimization",
        "claim_tested": (
            "published complete base and stored bounded corrective OPF control "
            "vectors remain AC-feasible and secure under the full preregistered "
            "active-load uncertainty grid without post-selection"
        ),
        "stress_contract": {
            "active_load_stress_fractions": list(PREREGISTERED_ACTIVE_LOAD_STRESS),
            "active_load_stress_percent": [
                100.0 * value for value in PREREGISTERED_ACTIVE_LOAD_STRESS
            ],
            "reactive_load_policy": "unchanged from the source scenario",
            "external_balance_policy": (
                "base cases fix the published post-action generator P/voltage "
                "vector; N-1 cases fix the corresponding stored bounded corrective "
                "generator P/voltage vector; external-grid buses balance only the "
                "incremental active-load error and AC losses; realized external-grid "
                "P/Q and generator Q must remain inside registered capability"
            ),
            "n1_policy": (
                "replay every published registered N-1 element identity at every "
                "stress point from its stored bounded corrective "
                "post-contingency control vector; hold that corrective vector "
                "fixed while external-grid buses balance the incremental active-"
                "load error; no additional re-optimization is performed"
            ),
            "selection_policy": (
                "full Cartesian denominator over every published result, all six "
                "stress points, the base topology, and every registered N-1 identity"
            ),
        },
        "hard_thresholds": {
            "voltage_bounds_pu": [0.95, 1.05],
            "thermal_loading_limit_percent": 100.0,
            "minimum_worst_voltage_margin_pu": (
                ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
            ),
            "power_balance_tolerance_mw": balance_tolerance_mw,
            "required_base_convergence_rate": 1.0,
            "required_base_safety_rate": 1.0,
            "required_registered_n1_convergence_rate": 1.0,
            "required_registered_n1_safety_rate": 1.0,
            "control_capability_tolerance": 1e-4,
        },
        "numerical_method": {
            "solver": "pandapower.runpp",
            "algorithm": "Newton-Raphson",
            "tolerance_mva": 1e-8,
            "maximum_iterations": 80,
            "reactive_limit_enforcement": True,
        },
        "registered_denominator": {
            "published_action_count": len(results),
            "stress_point_count_per_action": len(PREREGISTERED_ACTIVE_LOAD_STRESS),
            "expected_base_case_count": expected_base,
            "expected_registered_n1_case_count": expected_n1,
            "expected_total_case_count": expected_base + expected_n1,
            "observed_total_case_count": len(cases),
            "complete": denominator_complete,
        },
        "base_stress_summary": base_summary,
        "registered_n1_stress_summary": n1_summary,
        "system_statistics": grouped_statistics(cases, lambda row: row.get("system")),
        "perturbation_statistics": grouped_statistics(
            cases, lambda row: f"{float(row['stress_percent']):+.0f}%"
        ),
        "case_type_statistics": grouped_statistics(
            cases, lambda row: row.get("case_type")
        ),
        "n1_element_statistics": grouped_statistics(
            n1_rows, lambda row: (row.get("contingency") or {}).get("element")
        ),
        "solver_error_types": dict(
            sorted(
                Counter(
                    str(row.get("error_type"))
                    for row in cases
                    if row.get("solver_status") != "converged"
                ).items()
            )
        ),
        "optimization_diagnostics": optimization_diagnostics(
            cases,
            balance_tolerance_mw=balance_tolerance_mw,
        ),
        "hard_gates": gates,
        "input_validation": validation,
        "input_artifacts": input_artifacts,
        "implementation_sha256": file_sha256(Path(__file__)),
    }


def output_commit_status(report: dict[str, Any]) -> str:
    return "pass" if report.get("robustness_status") == "pass" else "diagnostic"


def completion_exit_code(
    report: dict[str, Any],
    *,
    allow_diagnostic: bool,
) -> int:
    if report.get("robustness_status") == "pass" or allow_diagnostic:
        return 0
    return 1


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
        help=(
            "Zero-stress Q-response replay tolerance. Stored responses are "
            "rounded to 1e-6 Mvar and are re-solved under a nonlinear AC model."
        ),
    )
    parser.add_argument("--voltage-replay-tolerance-pu", type=float, default=1e-6)
    parser.add_argument(
        "--allow-diagnostic",
        action="store_true",
        help=(
            "Return exit code 0 for a complete diagnostic result that does not "
            "pass every robustness gate. The output commit remains diagnostic."
        ),
    )
    parser.add_argument(
        "--case-results-json",
        default=(
            "simulation_outputs/opf_closed_loop/"
            "opf_action_uncertainty_stress_cases_v1.json"
        ),
    )
    parser.add_argument(
        "--report-json",
        default="reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--commit-json",
        default=(
            "simulation_outputs/opf_closed_loop/"
            "opf_action_uncertainty_stress_commit_v1.json"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results_path = resolve_input_path(Path(args.opf_results))
    commit_path = resolve_input_path(Path(args.opf_commit))
    scenario_paths = [
        resolve_input_path(Path(args.scenarios)),
        *[resolve_input_path(Path(value)) for value in args.additional_scenarios],
    ]
    scenario_arguments = [args.scenarios, *args.additional_scenarios]
    case_path = resolve_input_path(Path(args.case_results_json))
    report_path = resolve_input_path(Path(args.report_json))
    output_commit_path = resolve_input_path(Path(args.commit_json))
    validate_output_locations(
        [results_path, commit_path, *scenario_paths],
        [case_path, report_path, output_commit_path],
    )
    output_commit_path.unlink(missing_ok=True)
    commit_validation = validate_opf_commit(results_path, commit_path)
    results = load_published_results(results_path)
    scenarios = load_scenario_registry(scenario_paths)
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
        net = build_net(scenario)
        contract = validate_result_contract(result, scenario, net)
        apply_published_action(net, result)
        replay = validate_zero_stress_replay(
            net,
            result,
            active_power_tolerance_mw=args.active_power_replay_tolerance_mw,
            reactive_power_tolerance_mvar=args.reactive_power_replay_tolerance_mvar,
            voltage_tolerance_pu=args.voltage_replay_tolerance_pu,
        )
        n1_replays = validate_zero_stress_registered_n1_replays(
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
            "sha256": file_sha256(commit_path),
        },
        "opf_results": {
            "path": args.opf_results,
            "sha256": file_sha256(results_path),
        },
        "scenario_manifests": [
            {"path": argument, "sha256": file_sha256(path)}
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
    atomic_json(case_path, case_payload)
    atomic_json(report_path, report)
    atomic_json(
        output_commit_path,
        {
            "schema_version": "1.0",
            "status": output_commit_status(report),
            "robustness_status": report["robustness_status"],
            "commit_policy": (
                "written last after atomically replacing and hashing every "
                "uncertainty-stress artifact"
            ),
            "inputs": input_artifacts,
            "artifacts": {
                "cases": {
                    "path": args.case_results_json,
                    "sha256": file_sha256(case_path),
                },
                "report": {
                    "path": args.report_json,
                    "sha256": file_sha256(report_path),
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
    exit_code = completion_exit_code(
        report,
        allow_diagnostic=args.allow_diagnostic,
    )
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
