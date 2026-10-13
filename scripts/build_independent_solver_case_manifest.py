#!/usr/bin/env python3
"""Build a source-native case manifest for an independent solver adapter.

The manifest contains source identities, perturbations, controls, and reference
metrics.  It does not serialize pandapower's internal ppc representation.
An external adapter must load the named source case through its own data model.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json
from power_evidence_common import (
    PGLIB_CASE_FILES,
    PGLIB_CASE_SHA256,
    PGLIB_EXPECTED_COMMIT,
    sha256_file,
    validate_pglib_repository,
)
from validate_query_truth_independent import build_network


REQUIRED_SOURCE_FIELDS = {
    "provider",
    "identifier",
    "version",
    "sha256",
    "download_uri",
}


def normalize_system(value: Any) -> str:
    return str(value or "").strip().replace(" ", "").lower()


def source_bus_identity(net, bus_index: int) -> dict[str, Any]:
    row = net.bus.loc[bus_index]
    source_bus_id = row.get("name")
    try:
        source_bus_id = str(int(float(source_bus_id)))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"bus {bus_index} has no numeric source identifier: {source_bus_id!r}"
        ) from exc
    return {
        "pandapower_index_for_trace_only": int(bus_index),
        "source_bus_id": source_bus_id,
        "vn_kv": float(row["vn_kv"]),
    }


def element_identity(net, element: str, index: int) -> dict[str, Any]:
    table = getattr(net, element)
    row = table.loc[index]
    result: dict[str, Any] = {
        "element": element,
        "pandapower_index_for_trace_only": int(index),
    }
    if element == "line":
        result["from_bus"] = source_bus_identity(net, int(row["from_bus"]))
        result["to_bus"] = source_bus_identity(net, int(row["to_bus"]))
    elif element == "trafo":
        result["hv_bus"] = source_bus_identity(net, int(row["hv_bus"]))
        result["lv_bus"] = source_bus_identity(net, int(row["lv_bus"]))
    elif element in {"gen", "ext_grid"}:
        result["bus"] = source_bus_identity(net, int(row["bus"]))
        same_bus = [
            int(candidate)
            for candidate in table.index
            if int(table.at[candidate, "bus"]) == int(row["bus"])
        ]
        result["source_bus_ordinal"] = same_bus.index(int(index))
        source_row = row.get("pglib_source_row")
        if source_row is not None and int(source_row) > 0:
            result["pglib_source_row"] = int(source_row)
    elif element == "bus":
        result.update(source_bus_identity(net, int(index)))
    else:
        raise ValueError(f"unsupported identity element: {element}")
    if element in {"line", "trafo"}:
        first_field, second_field = (
            ("from_bus", "to_bus") if element == "line" else ("hv_bus", "lv_bus")
        )
        endpoints = tuple(
            sorted(
                (
                    str(net.bus.at[int(row[first_field]), "name"]),
                    str(net.bus.at[int(row[second_field]), "name"]),
                )
            )
        )
        if "_source_endpoint_ordinal" in table.columns:
            result["source_endpoint_ordinal"] = int(
                table.at[index, "_source_endpoint_ordinal"]
            )
        else:
            same_endpoints = []
            for candidate, candidate_row in table.iterrows():
                candidate_endpoints = tuple(
                    sorted(
                        (
                            str(net.bus.at[int(candidate_row[first_field]), "name"]),
                            str(net.bus.at[int(candidate_row[second_field]), "name"]),
                        )
                    )
                )
                if candidate_endpoints == endpoints:
                    same_endpoints.append(int(candidate))
            result["source_endpoint_ordinal"] = same_endpoints.index(int(index))
    return result


def prepare_endpoint_identity_ordinals(net) -> None:
    """Precompute parallel-branch ordinals in O(branches).

    Computing the ordinal by rescanning a 4,000-row branch table for every
    reference branch makes a large-case manifest quadratic.
    """
    for element, first_field, second_field in (
        ("line", "from_bus", "to_bus"),
        ("trafo", "hv_bus", "lv_bus"),
    ):
        table = getattr(net, element)
        counters: dict[tuple[str, str], int] = defaultdict(int)
        ordinals = []
        for _, row in table.iterrows():
            endpoints = tuple(
                sorted(
                    (
                        str(net.bus.at[int(row[first_field]), "name"]),
                        str(net.bus.at[int(row[second_field]), "name"]),
                    )
                )
            )
            ordinals.append(counters[endpoints])
            counters[endpoints] += 1
        table["_source_endpoint_ordinal"] = ordinals


def reference_state(net, result: dict[str, Any]) -> dict[str, Any]:
    state = result.get("post_action_state") or {}
    buses = [
        {
            **source_bus_identity(net, int(row["index"])),
            "vm_pu": float(row["vm_pu"]),
            "va_degree": float(row["va_degree"]),
        }
        for row in state.get("bus") or []
        if bool(row.get("in_service", True))
    ]
    branches = []
    for element, rows_key in (("line", "line"), ("trafo", "trafo")):
        for row in state.get(rows_key) or []:
            if not bool(row.get("in_service", True)):
                continue
            identity = element_identity(net, element, int(row["index"]))
            if element == "line":
                terminal_values = {
                    field: float(row[field])
                    for field in (
                        "p_from_mw",
                        "q_from_mvar",
                        "p_to_mw",
                        "q_to_mvar",
                    )
                }
            else:
                terminal_values = {
                    field: float(row[field])
                    for field in (
                        "p_hv_mw",
                        "q_hv_mvar",
                        "p_lv_mw",
                        "q_lv_mvar",
                    )
                }
            branches.append(
                {
                    **identity,
                    **terminal_values,
                    "loading_percent": float(row["loading_percent"]),
                }
            )
    return {"buses": buses, "branches": branches}


def contingency_identity(net, scenario: dict[str, Any]) -> dict[str, Any]:
    kind = str(scenario.get("contingency_type") or "")
    result: dict[str, Any] = {"type": kind, "components": []}
    if kind in {"n1_branch_outage", "n2_branch_outage"}:
        indices = scenario.get("line_indices")
        if indices is None and scenario.get("line_index") is not None:
            indices = [scenario["line_index"]]
        result["components"] = [
            element_identity(net, "line", int(index))
            for index in (indices or [])
        ]
    elif kind == "n1_transformer_outage" and scenario.get("transformer_index") is not None:
        result["components"] = [
            element_identity(net, "trafo", int(scenario["transformer_index"]))
        ]
    elif kind == "n1_generator_outage" and scenario.get("generator_index") is not None:
        result["components"] = [
            element_identity(net, "gen", int(scenario["generator_index"]))
        ]
    elif kind == "n1_bus_outage" and scenario.get("bus_index") is not None:
        result["components"] = [
            element_identity(net, "bus", int(scenario["bus_index"]))
        ]
    return result


def control_rows(net, result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for element in ("ext_grid", "gen"):
        for control in (result.get("post_action_controls") or {}).get(element) or []:
            index = int(control["index"])
            if index not in getattr(net, element).index:
                raise ValueError(f"control references absent {element} row {index}")
            if not bool(getattr(net, element).at[index, "in_service"]):
                continue
            values = {
                field: float(control[field])
                for field in ("p_mw", "q_mvar", "vm_pu")
            }
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"active {element} control {index} is non-finite")
            identity = element_identity(net, element, index)
            rows.append(
                {
                    **identity,
                    **values,
                }
            )
    return rows


def reference_generator_rows(net) -> list[dict[str, Any]]:
    rows = [
        element_identity(net, "ext_grid", int(index))
        for index in net.ext_grid.index
        if bool(net.ext_grid.at[index, "in_service"])
    ]
    if len(net.gen):
        for index in net.gen.index:
            if not bool(net.gen.at[index, "in_service"]):
                continue
            slack = (
                bool(net.gen.at[index, "slack"])
                if "slack" in net.gen.columns
                else False
            )
            slack_weight = (
                float(net.gen.at[index, "slack_weight"])
                if "slack_weight" in net.gen.columns
                and math.isfinite(float(net.gen.at[index, "slack_weight"]))
                else 0.0
            )
            if slack or slack_weight > 0.0:
                rows.append(element_identity(net, "gen", int(index)))
    if not rows:
        raise ValueError("reference network has no in-service slack generator")
    return rows


def validate_scenario_source_binding(
    scenario: dict[str, Any],
    source: dict[str, Any],
    *,
    system: str,
) -> dict[str, Any]:
    physical_source = scenario.get("physical_source")
    if not isinstance(physical_source, dict):
        raise ValueError(
            f"{scenario.get('scenario_id')} has no hash-bound physical_source; "
            "an implicit pandapower built-in case cannot be relabelled as PGLib"
        )
    expected = {
        "repository_commit": str(source["version"]),
        "case_file": str(source["identifier"]),
        "case_sha256": str(source["sha256"]),
    }
    mismatches = {
        field: {
            "scenario": physical_source.get(field),
            "registry": value,
        }
        for field, value in expected.items()
        if str(physical_source.get(field) or "") != value
    }
    if mismatches:
        raise ValueError(
            f"{scenario.get('scenario_id')} physical_source mismatch: {mismatches}"
        )
    operating_point = physical_source.get("operating_point") or {}
    if system == "ieee300" and operating_point.get("provider") != "PGLib MATPOWER case":
        return {
            "source_model_class": "hybrid_operating_point_pglib_topology_ratings",
            "native_parameter_replay_eligible": False,
            "exclusion_reason": (
                "The reference uses the separately documented convergent "
                "pandapower/PYPOWER IEEE-300 operating point.  A direct PGLib "
                "parameter replay would compare a different source model."
            ),
            "physical_source": physical_source,
        }
    return {
        "source_model_class": "pglib_native_parameters_and_operating_point",
        "native_parameter_replay_eligible": True,
        "physical_source": physical_source,
    }


def validate_source_registry(registry: dict[str, Any], systems: set[str]) -> None:
    missing_systems = sorted(systems - set(registry))
    if missing_systems:
        raise ValueError(f"source registry lacks systems: {missing_systems}")
    for system in systems:
        row = registry[system]
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(row))
        if missing:
            raise ValueError(f"{system} source registry missing: {missing}")
        digest = str(row["sha256"]).lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"{system} source-case sha256 is invalid")


def build_manifest(
    scenarios_payload: list[dict[str, Any]],
    opf_payload: dict[str, Any],
    registry: dict[str, Any],
    *,
    scenarios_path: Path,
    additional_scenario_paths: list[Path] | None = None,
    opf_path: Path,
    source_registry_path: Path | None,
    source_registry_binding: dict[str, Any],
) -> dict[str, Any]:
    scenarios = {str(row["scenario_id"]): row for row in scenarios_payload}
    results = opf_payload.get("results") or []
    systems = {
        normalize_system(
            scenarios[str(row["scenario_id"])].get("system")
            or scenarios[str(row["scenario_id"])].get("network_model")
        )
        for row in results
    }
    validate_source_registry(registry, systems)
    cases = []
    excluded_cases = []
    for result in results:
        scenario_id = str(result["scenario_id"])
        scenario = scenarios[scenario_id]
        system = normalize_system(
            scenario.get("system") or scenario.get("network_model")
        )
        source_model = validate_scenario_source_binding(
            scenario,
            registry[system],
            system=system,
        )
        if not source_model["native_parameter_replay_eligible"]:
            excluded_cases.append(
                {
                    "case_id": scenario_id,
                    "network_model": system,
                    "source_case": registry[system],
                    **source_model,
                }
            )
            continue
        # Only source identities and in-service topology are needed here.
        # Re-solving every large network would add minutes per manifest while
        # duplicating the already hash-bound post-action result.
        net = build_network(scenario, solve=False)
        prepare_endpoint_identity_ordinals(net)
        cases.append(
            {
                "case_id": scenario_id,
                "network_model": system,
                "source_case": registry[system],
                **source_model,
                "load_scaling_factor": float(scenario.get("load_level") or 1.0),
                "generator_scaling_factor": float(
                    scenario.get("base_generation_scaling_factor") or 1.0
                )
                * float(scenario.get("generator_factor") or 1.0),
                "contingency": contingency_identity(net, scenario),
                "post_action_controls": control_rows(net, result),
                "reference_generators": reference_generator_rows(net),
                "reference_result": {
                    key: (result.get("post_action") or {}).get(key)
                    for key in (
                        "max_branch_loading_percent",
                        "max_line_loading_percent",
                        "max_transformer_loading_percent",
                        "min_bus_voltage_pu",
                        "max_bus_voltage_pu",
                        "power_balance_residual_mw",
                    )
                },
                "reference_state": reference_state(net, result),
            }
        )
    return {
        "contract_version": "gridinstruct-independent-solver-case-manifest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "construction_rule": (
            "The independent adapter must load source_case through its own native "
            "data model, then apply the declared scaling, contingency, and controls. "
            "Importing a pandapower-exported ppc does not satisfy the contract."
        ),
        "solver_scope": {
            "task": "fixed-injection fixed-control AC power-flow replay",
            "q_limit_policy": (
                "enforce_q_lims=False in the independent replay; generator Q bounds "
                "are validated by the upstream OPF target and constraint audit"
            ),
            "not_claimed": "This replay is not a second independent OPF.",
        },
        "inputs": {
            "scenarios": {
                "path": str(scenarios_path),
                "sha256": sha256_file(scenarios_path),
            },
            "opf_results": {
                "path": str(opf_path),
                "sha256": sha256_file(opf_path),
            },
            "source_registry": {
                **source_registry_binding,
            },
            "additional_scenarios": [
                {"path": str(path), "sha256": sha256_file(path)}
                for path in (additional_scenario_paths or [])
            ],
        },
        "case_count": len(cases),
        "excluded_case_count": len(excluded_cases),
        "excluded_cases": excluded_cases,
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=ROOT / "simulation_outputs/contingency/scenarios_converged.json",
    )
    parser.add_argument("--additional-scenarios", nargs="*", type=Path, default=[])
    parser.add_argument(
        "--opf-results",
        type=Path,
        default=ROOT / "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
    )
    parser.add_argument("--source-registry", type=Path)
    parser.add_argument("--pglib-root", type=Path)
    parser.add_argument("--pglib-commit", default=PGLIB_EXPECTED_COMMIT)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "simulation_outputs/independent_solver/case_manifest_v1.json",
    )
    args = parser.parse_args()
    if bool(args.source_registry) == bool(args.pglib_root):
        raise ValueError("provide exactly one of --source-registry or --pglib-root")
    if args.pglib_root:
        repository = validate_pglib_repository(args.pglib_root, args.pglib_commit)
        registry = {}
        for system, filename in PGLIB_CASE_FILES.items():
            registry[system] = {
                "provider": "IEEE PES PGLib-OPF",
                "identifier": filename,
                "version": repository["commit"],
                "sha256": PGLIB_CASE_SHA256[system],
                "download_uri": (
                    "https://raw.githubusercontent.com/power-grid-lib/pglib-opf/"
                    f"{repository['commit']}/{filename}"
                ),
            }
        source_registry_binding = {
            "kind": "pglib-opf-fixed-commit",
            "repository": repository["repository"],
            "commit": repository["commit"],
            "license": repository["license"],
            "license_sha256": repository["license_sha256"],
        }
    else:
        registry = read_json(args.source_registry)
        source_registry_binding = {
            "kind": "source-registry-file",
            "path": str(args.source_registry),
            "sha256": sha256_file(args.source_registry),
        }
    scenario_rows = read_json(args.scenarios)
    for path in args.additional_scenarios:
        scenario_rows.extend(read_json(path))
    scenario_ids = [str(row.get("scenario_id") or "") for row in scenario_rows]
    if any(not value for value in scenario_ids) or len(scenario_ids) != len(
        set(scenario_ids)
    ):
        raise ValueError(
            "independent-solver scenario sources contain missing or duplicate IDs"
        )
    payload = build_manifest(
        scenario_rows,
        read_json(args.opf_results),
        registry,
        scenarios_path=args.scenarios,
        additional_scenario_paths=args.additional_scenarios,
        opf_path=args.opf_results,
        source_registry_path=args.source_registry,
        source_registry_binding=source_registry_binding,
    )
    ensure_dirs(args.output.parent)
    write_json(args.output, payload)
    print(json.dumps({"case_count": payload["case_count"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
