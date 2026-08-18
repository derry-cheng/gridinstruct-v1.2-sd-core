"""Generate reproducible topology-grounded simulation scenarios with pandapower."""

from __future__ import annotations

import argparse
import hashlib
import math
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import pandapower as pp
import pandapower.networks as pn
from pandapower.powerflow import LoadflowNotConverged
from pandapower.topology import unsupplied_buses

from gridinstruct_utils import ROOT, ensure_dirs, write_json
from pglib_network_loader import load_pglib_network, network_provenance


SYSTEM_LOADERS = {
    "ieee14": pn.case14,
    "ieee30": pn.case30,
    "ieee57": pn.case57,
    "ieee118": pn.case118,
    "ieee300": pn.case300,
}


def clone_net(system: str, pglib_root: Path | None = None):
    net = (
        load_pglib_network(system, pglib_root)
        if pglib_root is not None
        else SYSTEM_LOADERS[system]()
    )
    # pandapower 3.x expects this explicit flag.  Its bundled MATPOWER-derived
    # cases still omit the column and fall back to a deprecated spline path,
    # even though their characteristic tables are empty.  False preserves the
    # constant nameplate impedance exactly and makes the input contract forward
    # compatible with the current transformer model.
    for element in ("trafo", "trafo3w"):
        table = getattr(net, element, None)
        if table is not None and len(table) and "tap_dependency_table" not in table.columns:
            table["tap_dependency_table"] = False
    return net


def case_to_jsonable(value):
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


def apply_branch_outage(net, branch_idx: int | None) -> str | None:
    if branch_idx is None or branch_idx not in net.line.index:
        return None
    net.line.at[branch_idx, "in_service"] = False
    row = net.line.loc[branch_idx]
    return f"{int(row.from_bus)}-{int(row.to_bus)}"


def apply_two_branch_outage(net, first_idx: int, second_idx: int) -> list[str]:
    affected = []
    for idx in [first_idx, second_idx]:
        branch = apply_branch_outage(net, idx)
        if branch:
            affected.append(branch)
    return affected


def apply_generator_perturbation(net, factor: float) -> None:
    if len(net.gen):
        net.gen["p_mw"] *= factor
    elif len(net.ext_grid):
        return


def apply_transformer_outage(net, transformer_idx: int | None) -> str | None:
    if transformer_idx is None or transformer_idx not in net.trafo.index:
        return None
    net.trafo.at[transformer_idx, "in_service"] = False
    row = net.trafo.loc[transformer_idx]
    return f"{int(row.hv_bus)}-{int(row.lv_bus)}"


def apply_generator_outage(net, generator_idx: int | None) -> str | None:
    if generator_idx is None or generator_idx not in net.gen.index:
        return None
    net.gen.at[generator_idx, "in_service"] = False
    return f"gen-{int(generator_idx)}-bus-{int(net.gen.at[generator_idx, 'bus'])}"


def apply_bus_outage(net, bus_idx: int | None) -> int | None:
    if bus_idx is None or bus_idx not in net.bus.index:
        return None
    net.bus.at[bus_idx, "in_service"] = False
    return int(bus_idx)


def sample_branch_pairs(
    branch_indices: list[int],
    limit: int,
    *,
    seed: int,
    namespace: str,
) -> list[tuple[int, int]]:
    if len(branch_indices) < 2 or limit <= 0:
        return []
    pairs = list(combinations(branch_indices, 2))
    pairs.sort(
        key=lambda pair: (
            hashlib.sha256(
                f"{seed}:{namespace}:{pair[0]}:{pair[1]}".encode("utf-8")
            ).hexdigest(),
            pair,
        )
    )
    return pairs[: min(limit, len(pairs))]


def factor_token(value: float) -> str:
    scaled = value * 100.0
    if abs(scaled - round(scaled)) <= 1e-9:
        return str(int(round(scaled)))
    return format(value, ".12g").replace("-", "m").replace(".", "p")


def finite_value(value) -> float | None:
    """Return a rounded finite scalar and encode unavailable results as null."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return round(numeric, 6) if math.isfinite(numeric) else None


def complete_state_results(net) -> dict:
    """Serialize the complete converged electrical state and balance evidence."""

    def bus_in_service(bus_idx: int) -> bool:
        return bool(bus_idx in net.bus.index and net.bus.at[bus_idx, "in_service"])

    def effective_in_service(table, idx, *bus_fields: str) -> tuple[bool, bool]:
        declared = bool(table.at[idx, "in_service"]) if "in_service" in table.columns else True
        connected = all(bus_in_service(int(table.at[idx, field])) for field in bus_fields)
        return declared, declared and connected

    buses = [
        {
            "bus": int(idx),
            "in_service": bool(net.bus.at[idx, "in_service"]),
            "vm_pu": finite_value(row.get("vm_pu")),
            "va_degree": finite_value(row.get("va_degree")),
        }
        for idx, row in net.res_bus.iterrows()
    ]
    lines = [
        {
            "line": int(idx),
            "from_bus": int(net.line.at[idx, "from_bus"]),
            "to_bus": int(net.line.at[idx, "to_bus"]),
            "declared_in_service": effective_in_service(
                net.line, idx, "from_bus", "to_bus"
            )[0],
            "in_service": effective_in_service(net.line, idx, "from_bus", "to_bus")[1],
            "p_from_mw": finite_value(row.get("p_from_mw")),
            "q_from_mvar": finite_value(row.get("q_from_mvar")),
            "p_to_mw": finite_value(row.get("p_to_mw")),
            "q_to_mvar": finite_value(row.get("q_to_mvar")),
            "loading_percent": finite_value(row.get("loading_percent")),
        }
        for idx, row in net.res_line.iterrows()
    ]
    transformers = [
        {
            "transformer": int(idx),
            "hv_bus": int(net.trafo.at[idx, "hv_bus"]),
            "lv_bus": int(net.trafo.at[idx, "lv_bus"]),
            "declared_in_service": effective_in_service(
                net.trafo, idx, "hv_bus", "lv_bus"
            )[0],
            "in_service": effective_in_service(net.trafo, idx, "hv_bus", "lv_bus")[1],
            "p_hv_mw": finite_value(row.get("p_hv_mw")),
            "q_hv_mvar": finite_value(row.get("q_hv_mvar")),
            "p_lv_mw": finite_value(row.get("p_lv_mw")),
            "q_lv_mvar": finite_value(row.get("q_lv_mvar")),
            "loading_percent": finite_value(row.get("loading_percent")),
        }
        for idx, row in net.res_trafo.iterrows()
    ]

    def controls(element: str, result: str) -> list[dict]:
        table = getattr(net, element)
        result_table = getattr(net, result)
        return [
            {
                "index": int(idx),
                "bus": int(table.at[idx, "bus"]),
                "declared_in_service": effective_in_service(table, idx, "bus")[0],
                "in_service": effective_in_service(table, idx, "bus")[1],
                "p_mw": finite_value(row.get("p_mw")),
                "q_mvar": finite_value(row.get("q_mvar")),
                "vm_pu": finite_value(net.res_bus.at[int(table.at[idx, "bus"]), "vm_pu"]),
            }
            for idx, row in result_table.iterrows()
        ]

    generators = controls("gen", "res_gen")
    ext_grids = controls("ext_grid", "res_ext_grid")
    loads = [
        {
            "load": int(idx),
            "bus": int(net.load.at[idx, "bus"]),
            "declared_in_service": effective_in_service(net.load, idx, "bus")[0],
            "in_service": effective_in_service(net.load, idx, "bus")[1],
            "p_mw": finite_value(row.get("p_mw")),
            "q_mvar": finite_value(row.get("q_mvar")),
        }
        for idx, row in net.res_load.iterrows()
    ]

    def generic_results(element: str) -> list[dict]:
        table = getattr(net, element, None)
        result_table = getattr(net, f"res_{element}", None)
        if table is None or result_table is None or not len(table):
            return []
        rows = []
        for idx, result in result_table.iterrows():
            bus_fields = tuple(
                field
                for field in ("bus", "from_bus", "to_bus", "hv_bus", "mv_bus", "lv_bus")
                if field in table.columns
            )
            declared, effective = effective_in_service(table, idx, *bus_fields)
            item = {
                "index": int(idx),
                "declared_in_service": declared,
                "in_service": effective,
            }
            for bus_field in ("bus", "from_bus", "to_bus", "hv_bus", "mv_bus", "lv_bus"):
                if bus_field in table.columns:
                    item[bus_field] = int(table.at[idx, bus_field])
            for field, value in result.items():
                item[str(field)] = finite_value(value)
            rows.append(item)
        return rows

    additional_elements = (
        "sgen",
        "shunt",
        "storage",
        "ward",
        "xward",
        "impedance",
        "dcline",
        "trafo3w",
    )
    additional_results = {
        element: generic_results(element)
        for element in additional_elements
        if hasattr(net, element) and len(getattr(net, element))
    }
    switch_states = [
        {
            "index": int(idx),
            "bus": int(row["bus"]),
            "element": int(row["element"]),
            "element_type": str(row["et"]),
            "closed": bool(row["closed"]),
            "declared_in_service": bool(row["in_service"])
            if "in_service" in net.switch.columns
            else True,
            "in_service": (
                bool(row["in_service"]) if "in_service" in net.switch.columns else True
            )
            and bus_in_service(int(row["bus"])),
        }
        for idx, row in net.switch.iterrows()
    ] if hasattr(net, "switch") and len(net.switch) else []

    generation_mw = sum(
        float(row["p_mw"] or 0.0)
        for row in generators + ext_grids
        if row["in_service"]
    )
    generation_mw += sum(
        float(row.get("p_mw") or 0.0)
        for row in additional_results.get("sgen", [])
        if row["in_service"]
    )
    demand_mw = sum(
        float(row["p_mw"] or 0.0) for row in loads if row["in_service"]
    )
    demand_mw += sum(
        float(row.get("p_mw") or 0.0)
        for element in ("shunt", "storage", "ward", "xward")
        for row in additional_results.get(element, [])
        if row["in_service"]
    )
    losses_mw = sum(
        float(value)
        for table, field in (
            (net.res_line, "pl_mw"),
            (net.res_trafo, "pl_mw"),
            (getattr(net, "res_trafo3w", []), "pl_mw"),
            (getattr(net, "res_impedance", []), "pl_mw"),
            (getattr(net, "res_dcline", []), "pl_mw"),
        )
        if hasattr(table, "get")
        for value in table.get(field, [])
        if math.isfinite(float(value))
    )

    specs = (
        (buses, ("vm_pu", "va_degree")),
        (lines, ("p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar", "loading_percent")),
        (
            transformers,
            ("p_hv_mw", "q_hv_mvar", "p_lv_mw", "q_lv_mvar", "loading_percent"),
        ),
        (generators, ("p_mw", "q_mvar", "vm_pu")),
        (ext_grids, ("p_mw", "q_mvar", "vm_pu")),
        (loads, ("p_mw", "q_mvar")),
    )
    base_finite_in_service = all(
        all(row.get(field) is not None for field in fields)
        for rows, fields in specs
        for row in rows
        if row["in_service"]
    )
    additional_finite_in_service = all(
        all(
            value is not None
            for field, value in row.items()
            if field not in {
                "index",
                "declared_in_service",
                "in_service",
                "bus",
                "from_bus",
                "to_bus",
                "hv_bus",
                "mv_bus",
                "lv_bus",
            }
        )
        for rows in additional_results.values()
        for row in rows
        if row["in_service"]
    )
    additional_counts_complete = all(
        len(rows) == len(getattr(net, element))
        for element, rows in additional_results.items()
    )
    return {
        "bus_results": buses,
        "line_results": lines,
        "transformer_results": transformers,
        "generator_results": generators,
        "ext_grid_results": ext_grids,
        "load_results": loads,
        "additional_element_results": additional_results,
        "switch_states": switch_states,
        "state_counts": {
            "buses": len(buses),
            "lines": len(lines),
            "transformers": len(transformers),
            "generators": len(generators),
            "ext_grids": len(ext_grids),
            "loads": len(loads),
            **{element: len(rows) for element, rows in additional_results.items()},
            "switches": len(switch_states),
        },
        "additional_state_tables_complete": additional_counts_complete,
        "all_in_service_states_finite": base_finite_in_service and additional_finite_in_service,
        "generation_mw": round(generation_mw, 6),
        "demand_mw": round(demand_mw, 6),
        "network_losses_mw": round(losses_mw, 6),
        "power_balance_residual_mw": round(abs(generation_mw - demand_mw - losses_mw), 9),
    }


def summarize_results(net) -> dict:
    """Return complete, query-grade AC power-flow truth.

    The published intelligent-query task asks for *all* violations.  Therefore
    this function deliberately keeps the complete violation lists and stores
    their counts.  Presentation-layer truncation, if ever needed, must happen
    outside the canonical scenario files.
    """
    bus_voltage = []
    if hasattr(net, "res_bus") and len(net.res_bus):
        for idx, row in net.res_bus.iterrows():
            vm = float(row.vm_pu)
            if vm < 0.95 or vm > 1.05:
                bus_voltage.append({"bus": int(idx), "vm_pu": round(vm, 6)})

    overloaded = []
    if hasattr(net, "res_line") and len(net.res_line):
        for idx, row in net.res_line.iterrows():
            loading = float(row.loading_percent)
            if loading > 100:
                line = net.line.loc[idx]
                overloaded.append(
                    {
                        "line": int(idx),
                        "branch_id": f"{int(line.from_bus)}-{int(line.to_bus)}",
                        "loading_percent": round(loading, 6),
                    }
                )

    overloaded_transformers = []
    if hasattr(net, "res_trafo") and len(net.res_trafo):
        for idx, row in net.res_trafo.iterrows():
            loading = float(row.loading_percent)
            if loading > 100:
                trafo = net.trafo.loc[idx]
                overloaded_transformers.append(
                    {
                        "transformer": int(idx),
                        "transformer_id": f"{int(trafo.hv_bus)}-{int(trafo.lv_bus)}",
                        "loading_percent": round(loading, 6),
                    }
                )

    violation_type: list[str] = []
    if overloaded or overloaded_transformers:
        violation_type.append("thermal_overload")
    if bus_voltage:
        violation_type.append("voltage_violation")
    if not violation_type:
        violation_type.append("none")

    max_line_loading = (
        float(net.res_line.loading_percent.max())
        if hasattr(net, "res_line") and len(net.res_line)
        else 0.0
    )
    max_trafo_loading = (
        float(net.res_trafo.loading_percent.max())
        if hasattr(net, "res_trafo") and len(net.res_trafo)
        else 0.0
    )
    max_loading = max(max_line_loading, max_trafo_loading)
    min_vm = (
        float(net.res_bus.vm_pu.min())
        if hasattr(net, "res_bus") and len(net.res_bus)
        else 1.0
    )
    max_vm = (
        float(net.res_bus.vm_pu.max())
        if hasattr(net, "res_bus") and len(net.res_bus)
        else 1.0
    )

    if max_loading > 110 or min_vm < 0.92 or max_vm > 1.08:
        severity = "emergency"
    elif max_loading > 100 or min_vm < 0.95 or max_vm > 1.05:
        severity = "alert"
    else:
        severity = "normal"

    return {
        "max_branch_loading_percent": round(max_loading, 6),
        "max_line_loading_percent": round(max_line_loading, 6),
        "max_transformer_loading_percent": round(max_trafo_loading, 6),
        "min_bus_voltage_pu": round(min_vm, 6),
        "max_bus_voltage_pu": round(max_vm, 6),
        "overloaded_branch_count": len(overloaded),
        "overloaded_transformer_count": len(overloaded_transformers),
        "voltage_violation_count": len(bus_voltage),
        "overloaded_branches": overloaded,
        "overloaded_transformers": overloaded_transformers,
        "voltage_violations": bus_voltage,
        "violation_type": violation_type,
        "severity_level": severity,
        "threshold_policy": {
            "policy_id": "gridinstruct_ac_operating_envelope_v2",
            "line_loading_percent": 100.0,
            "transformer_loading_percent": 100.0,
            "bus_voltage_pu": [0.95, 1.05],
            "role": "dataset_policy",
        },
        **complete_state_results(net),
    }


def run_scenario(
    system: str,
    load_level: float,
    scenario_kind: str,
    branch_idx: int | None,
    gen_factor: float,
    second_branch_idx: int | None = None,
    transformer_idx: int | None = None,
    generator_idx: int | None = None,
    bus_idx: int | None = None,
    pglib_root: Path | None = None,
    scale_generation_with_load: bool = False,
    solver_algorithm: str = "nr",
    enforce_q_lims: bool = True,
    max_iteration: int = 40,
) -> dict:
    net = clone_net(system, pglib_root)
    scale_loads(net, load_level)
    if scale_generation_with_load:
        scale_generation(net, load_level)
    if not math.isclose(float(gen_factor), 1.0, rel_tol=0.0, abs_tol=1e-12):
        apply_generator_perturbation(net, gen_factor)
    affected_branch = None
    affected_branches = []
    affected_transformers = []
    affected_generators = []
    affected_buses = []
    if scenario_kind == "n1_branch_outage":
        affected_branch = apply_branch_outage(net, branch_idx)
        affected_branches = [affected_branch] if affected_branch else []
    elif scenario_kind == "n2_branch_outage" and branch_idx is not None and second_branch_idx is not None:
        affected_branches = apply_two_branch_outage(net, branch_idx, second_branch_idx)
    elif scenario_kind == "generator_perturbation":
        pass
    elif scenario_kind == "n1_transformer_outage":
        affected = apply_transformer_outage(net, transformer_idx)
        affected_transformers = [affected] if affected else []
    elif scenario_kind == "n1_generator_outage":
        affected = apply_generator_outage(net, generator_idx)
        affected_generators = [affected] if affected else []
    elif scenario_kind == "n1_bus_outage":
        affected = apply_bus_outage(net, bus_idx)
        affected_buses = [affected] if affected is not None else []

    scenario_id = f"{system}_{scenario_kind}_load{factor_token(load_level)}"
    if affected_branch:
        scenario_id += f"_branchidx_{branch_idx}_{affected_branch.replace('-', '_')}"
    if affected_branches and scenario_kind == "n2_branch_outage":
        scenario_id += (
            f"_branches_{branch_idx}_{affected_branches[0].replace('-', '_')}"
            f"_{second_branch_idx}_{affected_branches[1].replace('-', '_')}"
        )
    if scenario_kind == "generator_perturbation" or not math.isclose(
        float(gen_factor), 1.0, rel_tol=0.0, abs_tol=1e-12
    ):
        scenario_id += f"_gen{factor_token(gen_factor)}"
    if scenario_kind == "n1_transformer_outage" and transformer_idx is not None:
        scenario_id += f"_trafoidx_{transformer_idx}"
    if scenario_kind == "n1_generator_outage" and generator_idx is not None:
        scenario_id += f"_genidx_{generator_idx}"
    if scenario_kind == "n1_bus_outage" and bus_idx is not None:
        scenario_id += f"_busidx_{bus_idx}"

    unsupplied = sorted(int(value) for value in unsupplied_buses(net))
    if unsupplied:
        solver_status = "unsupplied_island"
        error = f"unsupplied buses: {unsupplied}"
    else:
        try:
            pp.runpp(
                net,
                algorithm=solver_algorithm,
                init="auto",
                tolerance_mva=1e-6,
                max_iteration=max_iteration,
                enforce_q_lims=enforce_q_lims,
                numba=False,
            )
            solver_status = "converged"
            error = None
        except LoadflowNotConverged as exc:
            solver_status = "numerical_nonconvergence"
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - classify invalid/infeasible construction attempts.
            solver_status = "invalid_or_infeasible"
            error = str(exc)

    summary = summarize_results(net) if solver_status == "converged" else {
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
        "violation_type": [solver_status],
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
        "network_model": system.upper().replace("IEEE", "IEEE "),
        "system": system,
        "base_case_file": f"network_cases/{system}/{system}_base.json",
        "contingency_type": scenario_kind,
        "affected_components": {
            "branches": affected_branches,
            "transformers": affected_transformers,
            "generators": affected_generators,
            "buses": affected_buses,
        },
        "load_level": load_level,
        "base_generation_scaling_factor": (
            load_level if scale_generation_with_load else 1.0
        ),
        "generator_factor": (
            gen_factor
            if scenario_kind == "generator_perturbation"
            or not math.isclose(float(gen_factor), 1.0, rel_tol=0.0, abs_tol=1e-12)
            else None
        ),
        "line_indices": (
            [branch_idx, second_branch_idx]
            if scenario_kind == "n2_branch_outage" and branch_idx is not None and second_branch_idx is not None
            else [branch_idx]
            if scenario_kind == "n1_branch_outage" and branch_idx is not None
            else []
        ),
        "transformer_index": transformer_idx,
        "generator_index": generator_idx,
        "bus_index": bus_idx,
        "solver": "pandapower",
        "physical_source": network_provenance(net) if pglib_root is not None else None,
        "counts_in_security_denominator": scenario_kind == "n1_branch_outage",
        "solver_status": solver_status,
        "failure_class": None if solver_status == "converged" else solver_status,
        "unsupplied_buses": unsupplied,
        "error": error,
        **summary,
    }


def in_service_line_indices(net, max_lines: int) -> list[int]:
    """Return deterministic physical line identities for the N-1 denominator."""
    indices = [
        int(index)
        for index, row in net.line.sort_index().iterrows()
        if bool(row.get("in_service", True))
    ]
    return indices[:max_lines]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", nargs="+", default=["ieee14", "ieee30", "ieee57", "ieee118"])
    parser.add_argument(
        "--pglib-root",
        type=Path,
        help="Pinned PGLib-OPF checkout used for all formal physical cases.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-branches-per-system", type=int, default=9999)
    parser.add_argument("--max-n2-per-system", type=int, default=24)
    parser.add_argument("--load-levels", nargs="+", type=float, default=[0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3])
    parser.add_argument(
        "--system-load-levels",
        action="append",
        default=[],
        help="Per-system feasible-envelope override as system:level,level.",
    )
    parser.add_argument(
        "--scale-generation-with-load",
        action="store_true",
        help="Scale non-slack active generation with demand before applying scenario-specific perturbations.",
    )
    parser.add_argument("--generator-factors", nargs="+", type=float, default=[0.85, 0.9, 0.95, 1.05])
    parser.add_argument("--n2-load-levels", nargs="+", type=float, default=[1.0, 1.2, 1.3])
    parser.add_argument("--equipment-outage-load-levels", nargs="+", type=float, default=[1.0, 1.2])
    parser.add_argument("--max-transformers-per-system", type=int, default=12)
    parser.add_argument("--max-generators-per-system", type=int, default=12)
    parser.add_argument("--max-buses-per-system", type=int, default=12)
    parser.add_argument("--all-output", default="simulation_outputs/power_flow/scenarios_all.json")
    parser.add_argument(
        "--converged-output",
        default="simulation_outputs/contingency/scenarios_converged.json",
    )
    parser.add_argument("--report-output", default="reports/simulation_report.json")
    args = parser.parse_args()

    for name, values in (
        ("load_levels", args.load_levels),
        ("generator_factors", args.generator_factors),
        ("n2_load_levels", args.n2_load_levels),
        ("equipment_outage_load_levels", args.equipment_outage_load_levels),
    ):
        tokens = [factor_token(value) for value in values]
        if len(tokens) != len(set(tokens)):
            raise ValueError(f"{name} contains duplicate or ID-colliding values: {values}")
    system_load_levels = {system: list(args.load_levels) for system in args.systems}
    for specification in args.system_load_levels:
        system, separator, values = specification.partition(":")
        if not separator or system not in system_load_levels:
            raise ValueError(f"invalid --system-load-levels specification: {specification}")
        system_load_levels[system] = [
            float(value) for value in values.split(",") if value
        ]
    for system, values in system_load_levels.items():
        tokens = [factor_token(value) for value in values]
        if not values or len(tokens) != len(set(tokens)):
            raise ValueError(
                f"{system} load levels contain duplicate or ID-colliding values: {values}"
            )

    all_output = ROOT / args.all_output
    converged_output = ROOT / args.converged_output
    report_output = ROOT / args.report_output
    ensure_dirs(all_output.parent, converged_output.parent, report_output.parent, ROOT / "network_cases")
    all_records = []
    for system in args.systems:
        if system not in SYSTEM_LOADERS:
            raise ValueError(f"Unknown system: {system}")
        net = clone_net(system, args.pglib_root)
        case_dir = ROOT / "network_cases" / system
        ensure_dirs(case_dir)
        pp.to_json(net, str(case_dir / f"{system}_base.json"))

        for load_level in system_load_levels[system]:
            all_records.append(
                run_scenario(
                    system,
                    load_level,
                    "base_power_flow",
                    None,
                    1.0,
                    pglib_root=args.pglib_root,
                    scale_generation_with_load=args.scale_generation_with_load,
                )
            )
            for gen_factor in args.generator_factors:
                all_records.append(
                    run_scenario(
                        system,
                        load_level,
                        "generator_perturbation",
                        None,
                        gen_factor,
                        pglib_root=args.pglib_root,
                        scale_generation_with_load=args.scale_generation_with_load,
                    )
                )

        branch_indices = in_service_line_indices(
            net,
            args.max_branches_per_system,
        )
        for load_level in system_load_levels[system]:
            for branch_idx in branch_indices:
                all_records.append(
                    run_scenario(
                        system,
                        load_level,
                        "n1_branch_outage",
                        branch_idx,
                        1.0,
                        pglib_root=args.pglib_root,
                        scale_generation_with_load=args.scale_generation_with_load,
                    )
                )
        n2_pairs = sample_branch_pairs(
            branch_indices,
            args.max_n2_per_system,
            seed=args.seed,
            namespace=system,
        )
        for load_level in [
            value for value in args.n2_load_levels if value in system_load_levels[system]
        ]:
            for first_idx, second_idx in n2_pairs:
                all_records.append(
                    run_scenario(
                        system,
                        load_level,
                        "n2_branch_outage",
                        first_idx,
                        1.0,
                        second_idx,
                        pglib_root=args.pglib_root,
                        scale_generation_with_load=args.scale_generation_with_load,
                    )
                )
        transformer_indices = list(net.trafo.index)[: args.max_transformers_per_system]
        generator_indices = list(net.gen.index)[: args.max_generators_per_system]
        slack_buses = {int(value) for value in net.ext_grid.bus.tolist()}
        bus_indices = [int(value) for value in net.bus.index if int(value) not in slack_buses][
            : args.max_buses_per_system
        ]
        for load_level in args.equipment_outage_load_levels:
            for transformer_idx in transformer_indices:
                all_records.append(
                    run_scenario(
                        system,
                        load_level,
                        "n1_transformer_outage",
                        None,
                        1.0,
                        transformer_idx=int(transformer_idx),
                        pglib_root=args.pglib_root,
                        scale_generation_with_load=args.scale_generation_with_load,
                    )
                )
            for generator_idx in generator_indices:
                all_records.append(
                    run_scenario(
                        system,
                        load_level,
                        "n1_generator_outage",
                        None,
                        1.0,
                        generator_idx=int(generator_idx),
                        pglib_root=args.pglib_root,
                        scale_generation_with_load=args.scale_generation_with_load,
                    )
                )
            for bus_idx in bus_indices:
                all_records.append(
                    run_scenario(
                        system,
                        load_level,
                        "n1_bus_outage",
                        None,
                        1.0,
                        bus_idx=int(bus_idx),
                        pglib_root=args.pglib_root,
                        scale_generation_with_load=args.scale_generation_with_load,
                    )
                )

    converged = [row for row in all_records if row["solver_status"] == "converged"]
    scenario_ids = [str(row.get("scenario_id") or "") for row in all_records]
    strata: dict[str, dict[str, int]] = {}
    for row in all_records:
        key = f"{row['system']}|{row['contingency_type']}|load={factor_token(float(row['load_level']))}"
        item = strata.setdefault(key, {"generated": 0, "converged": 0})
        item["generated"] += 1
        item["converged"] += int(row["solver_status"] == "converged")

    def state_gate_errors(row: dict) -> list[str]:
        errors = []
        if row.get("all_in_service_states_finite") is not True:
            errors.append("in_service_state_contains_nonfinite_value")
        if row.get("additional_state_tables_complete") is not True:
            errors.append("additional_state_table_count_mismatch")
        if not isinstance(row.get("additional_element_results"), dict):
            errors.append("additional_element_results_not_object")
        if not isinstance(row.get("switch_states"), list):
            errors.append("switch_states_not_list")
        if int((row.get("state_counts") or {}).get("buses", 0)) <= 0:
            errors.append("empty_bus_results")
        if int((row.get("state_counts") or {}).get("lines", -1)) < 0:
            errors.append("missing_line_result_count")
        return errors

    state_gate_failures = [
        {
            "scenario_id": row.get("scenario_id"),
            "system": row.get("system"),
            "contingency_type": row.get("contingency_type"),
            "errors": state_gate_errors(row),
            "state_counts": row.get("state_counts"),
        }
        for row in converged
        if state_gate_errors(row)
    ]
    hard_gates = {
        "nonempty": bool(all_records),
        "scenario_ids_unique": len(scenario_ids) == len(set(scenario_ids)) and all(scenario_ids),
        "every_generated_stratum_has_convergence": all(item["converged"] > 0 for item in strata.values()),
        "overall_convergence_at_least_half": len(converged) / max(len(all_records), 1) >= 0.5,
        "every_system_has_convergence": all(any(row["system"] == system for row in converged) for system in args.systems),
        "all_converged_states_complete_and_finite": not state_gate_failures,
        "all_converged_power_balance_residuals_within_1e_minus_3_mw": all(
            float(row.get("power_balance_residual_mw", float("inf"))) <= 1e-3
            for row in converged
        ),
        "all_failed_scenarios_classified": all(
            row.get("failure_class") in {
                "unsupplied_island",
                "numerical_nonconvergence",
                "invalid_or_infeasible",
            }
            for row in all_records
            if row["solver_status"] != "converged"
        ),
    }
    status = "pass" if all(hard_gates.values()) else "fail"
    report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "systems": args.systems,
            "config": {
                "seed": args.seed,
                "load_levels": args.load_levels,
                "system_load_levels": system_load_levels,
                "scale_generation_with_load": args.scale_generation_with_load,
                "generator_factors": args.generator_factors,
                "n2_load_levels": args.n2_load_levels,
                "max_branches_per_system": args.max_branches_per_system,
                "max_n2_per_system": args.max_n2_per_system,
                "equipment_outage_load_levels": args.equipment_outage_load_levels,
                "max_transformers_per_system": args.max_transformers_per_system,
                "max_generators_per_system": args.max_generators_per_system,
                "max_buses_per_system": args.max_buses_per_system,
            },
            "total_scenarios": len(all_records),
            "converged_scenarios": len(converged),
            "convergence_rate": len(converged) / len(all_records) if all_records else 0,
            "stratum_coverage": strata,
            "hard_gates": hard_gates,
            "by_system": {
                system: sum(1 for row in all_records if row["system"] == system)
                for system in args.systems
            },
            "by_contingency_type": {
                kind: sum(1 for row in all_records if row["contingency_type"] == kind)
                for kind in sorted({row["contingency_type"] for row in all_records})
            },
            "failure_class_counts": dict(
                Counter(
                    row.get("failure_class")
                    for row in all_records
                    if row.get("failure_class")
                )
            ),
            "state_gate_failure_count": len(state_gate_failures),
            "state_gate_failures": state_gate_failures,
        }
    # The complete attempt ledger is evidence even when a hard gate stops
    # publication of the converged subset.
    write_json(all_output, all_records)
    if status == "pass":
        write_json(converged_output, converged)
    write_json(report_output, report)
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
