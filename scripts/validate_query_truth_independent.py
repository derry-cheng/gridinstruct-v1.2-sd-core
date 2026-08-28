#!/usr/bin/env python3
"""Independently recompute and validate every structured query answer.

The implementation intentionally does not import the scenario generator or the
query repair module.  It reconstructs pandapower networks from released
scenario descriptors and compares complete result sets with numeric tolerance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import pandapower as pp
import pandapower.networks as pn

from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json
from pglib_network_loader import load_pglib_network, network_provenance
from query_contract import (
    QUERY_CONTRACT_VERSION,
    SUPPORTED_FILTERS,
    SUPPORTED_EQUIPMENT_SCOPES,
    canonical,
    execute_query,
    query_projection,
    validate_query_contract,
)


LOADERS = {
    "ieee14": pn.case14,
    "ieee30": pn.case30,
    "ieee57": pn.case57,
    "ieee118": pn.case118,
    "ieee300": pn.case300,
    "illinois200": pn.case_illinois200,
    "pegase89": pn.case89pegase,
    "pegase1354": pn.case1354pegase,
    "rte1888": pn.case1888rte,
    "rte2848": pn.case2848rte,
    "pegase2869": pn.case2869pegase,
}
ALLOWED_CONTINGENCIES = {
    "base_power_flow",
    "generator_perturbation",
    "n1_branch_outage",
    "n2_branch_outage",
    "n1_transformer_outage",
    "n1_generator_outage",
    "n1_bus_outage",
}
def system_name(row: dict[str, Any]) -> str:
    return str(row.get("system") or row.get("network_model") or "").lower().replace(" ", "")


def parsed_indices(row: dict[str, Any], kind: str) -> list[int]:
    scenario_id = str(row.get("scenario_id") or "")
    if kind == "line":
        if row.get("line_indices"):
            return [int(value) for value in row["line_indices"]]
        if row.get("line_index") is not None:
            return [int(row["line_index"])]
        match = re.search(r"branchidx_(\d+)_", scenario_id)
        if match:
            return [int(match.group(1))]
        match = re.search(r"branches_(\d+)_\d+_\d+_(\d+)_\d+_\d+$", scenario_id)
        if match:
            return [int(match.group(1)), int(match.group(2))]
        match = re.search(r"_line(\d+)$", scenario_id)
        return [int(match.group(1))] if match else []
    field, pattern = {
        "trafo": ("transformer_index", r"_trafoidx_(\d+)$"),
        "gen": ("generator_index", r"_genidx_(\d+)$"),
        "bus": ("bus_index", r"_busidx_(\d+)$"),
    }[kind]
    if row.get(field) is not None:
        return [int(row[field])]
    match = re.search(pattern, scenario_id)
    return [int(match.group(1))] if match else []


def build_network(row: dict[str, Any], *, solve: bool = True):
    system = system_name(row)
    physical_source = row.get("physical_source")
    if isinstance(physical_source, dict):
        net = load_pglib_network(system, ROOT / "third_party/pglib-opf-v23.07")
        actual_source = network_provenance(net)
        for field in ("repository_commit", "case_file", "case_sha256"):
            if actual_source.get(field) != physical_source.get(field):
                raise ValueError(
                    f"{system} physical-source mismatch for {field}: "
                    f"{physical_source.get(field)!r} != {actual_source.get(field)!r}"
                )
    else:
        net = LOADERS[system]()
    factor = float(row.get("load_level") or 1.0)
    net.load.loc[:, "p_mw"] *= factor
    net.load.loc[:, "q_mvar"] *= factor
    if len(net.gen):
        net.gen.loc[:, "p_mw"] *= float(
            row.get("base_generation_scaling_factor") or 1.0
        )
        combined_gen_factor = row.get("generator_factor")
        if combined_gen_factor is None and str(
            row.get("contingency_type") or ""
        ) == "generator_perturbation":
            match = re.search(r"_gen(\d+)", str(row["scenario_id"]))
            combined_gen_factor = int(match.group(1)) / 100.0 if match else 1.0
        if combined_gen_factor is not None:
            net.gen.loc[:, "p_mw"] *= float(combined_gen_factor)
    contingency = str(row.get("contingency_type") or "")
    if contingency not in ALLOWED_CONTINGENCIES:
        raise ValueError(f"unsupported contingency type: {contingency!r}")
    if contingency in {"n1_branch_outage", "n2_branch_outage"}:
        indices = parsed_indices(row, "line")
        required = 1 if contingency == "n1_branch_outage" else 2
        if len(indices) != required or len(set(indices)) != required:
            raise ValueError(f"{contingency} requires {required} unique line indices; got {indices}")
        for idx in indices:
            if idx not in net.line.index:
                raise IndexError(f"line index out of range: {idx}")
            net.line.at[idx, "in_service"] = False
    elif contingency == "generator_perturbation":
        pass
    elif contingency == "n1_transformer_outage":
        net.trafo.at[parsed_indices(row, "trafo")[0], "in_service"] = False
    elif contingency == "n1_generator_outage":
        net.gen.at[parsed_indices(row, "gen")[0], "in_service"] = False
    elif contingency == "n1_bus_outage":
        idx = parsed_indices(row, "bus")[0]
        if ((row.get("affected_components") or {}).get("buses") or []) != [idx]:
            raise ValueError("n1_bus_outage affected bus mismatch")
        net.bus.at[idx, "in_service"] = False
    if solve:
        try:
            pp.runpp(
                net,
                algorithm="nr",
                init="auto",
                tolerance_mva=1e-6,
                max_iteration=50,
                enforce_q_lims=True,
                numba=False,
            )
            net["_gridinstruct_replay_solver"] = "nr_q_lims"
        except pp.LoadflowNotConverged as first_error:
            # The scenario constructor uses the same deterministic fallback
            # for stressed operating points.  Replaying only the default NR
            # configuration would therefore turn a solver-initialisation
            # difference into a false data mismatch.
            try:
                pp.runpp(
                    net,
                    algorithm="iwamoto_nr",
                    init="auto",
                    tolerance_mva=1e-6,
                    max_iteration=200,
                    enforce_q_lims=False,
                    numba=False,
                )
                net["_gridinstruct_replay_solver"] = "iwamoto_nr_no_q_lims"
            except Exception as fallback_error:  # noqa: BLE001
                raise RuntimeError(
                    f"default NR failed ({first_error}); deterministic Iwamoto fallback failed "
                    f"({fallback_error})"
                ) from fallback_error
    return net


def raw_truth(row: dict[str, Any]) -> dict[str, Any]:
    net = build_network(row)
    lines = []
    for idx, result in net.res_line.iterrows():
        loading = float(result.loading_percent)
        if loading > 100.0:
            line = net.line.loc[idx]
            lines.append({"line": int(idx), "branch_id": f"{int(line.from_bus)}-{int(line.to_bus)}", "loading_percent": round(loading, 6)})
    trafos = []
    for idx, result in net.res_trafo.iterrows():
        loading = float(result.loading_percent)
        if loading > 100.0:
            trafo = net.trafo.loc[idx]
            trafos.append({"transformer": int(idx), "transformer_id": f"{int(trafo.hv_bus)}-{int(trafo.lv_bus)}", "loading_percent": round(loading, 6)})
    voltages = [
        {"bus": int(idx), "vm_pu": round(float(result.vm_pu), 6)}
        for idx, result in net.res_bus.iterrows()
        if float(result.vm_pu) < 0.95 or float(result.vm_pu) > 1.05
    ]
    max_loading = max(
        float(net.res_line.loading_percent.max()) if len(net.res_line) else 0.0,
        float(net.res_trafo.loading_percent.max()) if len(net.res_trafo) else 0.0,
    )
    min_vm = float(net.res_bus.vm_pu.min())
    max_vm = float(net.res_bus.vm_pu.max())
    violation_type = []
    if lines or trafos:
        violation_type.append("thermal_overload")
    if voltages:
        violation_type.append("voltage_violation")
    if not violation_type:
        violation_type.append("none")
    severity = "emergency" if max_loading > 110 or min_vm < 0.92 or max_vm > 1.08 else "alert" if max_loading > 100 or min_vm < 0.95 or max_vm > 1.05 else "normal"
    return {
        "overloaded_branches": lines,
        "overloaded_transformers": trafos,
        "voltage_violations": voltages,
        "violation_type": violation_type,
        "severity_level": severity,
        "replay_solver": net.get("_gridinstruct_replay_solver", "unknown"),
    }


def normalized(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 5)
    if isinstance(value, list):
        return sorted((normalized(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, dict):
        return {key: normalized(value[key]) for key in sorted(value)}
    return value


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def projection_sha256(rows: list[dict[str, Any]]) -> str:
    payload = canonical(query_projection(rows)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--output-json", default="reports/independent_query_truth_validation_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/independent_query_truth_validation_v1.2_sd_core.md")
    args = parser.parse_args()

    scenario_rows = read_json(ROOT / args.scenarios)
    scenario_ids = [str(row.get("scenario_id") or "") for row in scenario_rows] if isinstance(scenario_rows, list) else []
    if not scenario_ids or any(not value for value in scenario_ids) or len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("scenario truth must be a non-empty unique-ID list")
    scenarios = {row["scenario_id"]: row for row in scenario_rows}
    dataset_rows = read_jsonl(ROOT / args.dataset)
    dataset_ids = [str(row.get("id") or "") for row in dataset_rows]
    if not dataset_ids or any(not value for value in dataset_ids) or len(dataset_ids) != len(set(dataset_ids)):
        raise ValueError("dataset must contain unique non-empty IDs")
    query_rows = [row for row in dataset_rows if row.get("task_type") == "intelligent_data_query"]
    if not query_rows:
        raise ValueError("independent validation requires at least one query record")
    needed = set()
    record_errors = []
    for row in query_rows:
        query = row.get("structured_query")
        structural_errors = validate_query_contract(row, require_result=False)
        if structural_errors:
            record_errors.append(
                {"id": row.get("id"), "reason": "query_contract", "errors": structural_errors}
            )
            continue
        needed.add(str(query["scenario_id"]))
    needed = sorted(needed)
    unreplayed_scenarios = {
        scenario_id: {
            "reason": "scenario_registry_marks_query_truth_incomplete",
            "solver_status": scenarios[scenario_id].get("solver_status"),
            "failure_class": scenarios[scenario_id].get("failure_class"),
        }
        for scenario_id in needed
        if scenarios[scenario_id].get("query_truth_complete") is False
    }
    truth = {}
    errors = list(record_errors)
    for scenario_id in needed:
        if scenario_id in unreplayed_scenarios:
            continue
        try:
            truth[scenario_id] = raw_truth(scenarios[scenario_id])
        except Exception as exc:  # noqa: BLE001
            errors.append({"scenario_id": scenario_id, "error_type": type(exc).__name__, "error": str(exc)[:500]})

    mismatches = []
    checked_query_rows = 0
    by_filter = Counter()
    by_overload_scope = Counter()
    for row in query_rows:
        query = row.get("structured_query") or {}
        scenario_id = str(query.get("scenario_id") or "")
        if scenario_id in unreplayed_scenarios:
            continue
        if scenario_id not in truth:
            continue
        by_filter[str(query.get("filter"))] += 1
        if query.get("filter") == "loading_percent > 100":
            by_overload_scope[str(query.get("equipment_scope") or "legacy")] += 1
        try:
            expected = execute_query(query, truth[scenario_id])
        except Exception as exc:  # noqa: BLE001
            errors.append({"id": row.get("id"), "error_type": type(exc).__name__, "error": str(exc)[:500]})
            continue
        output = row.get("output")
        if not isinstance(output, dict):
            errors.append({"id": row.get("id"), "error_type": "InvalidOutput", "error": "output_not_object"})
            continue
        checked_query_rows += 1
        contract_errors = validate_query_contract(row, truth[scenario_id])
        if contract_errors:
            errors.append({"id": row.get("id"), "error_type": "QueryContract", "errors": contract_errors})
            continue
        if normalized(row.get("query_result")) != normalized(expected) or normalized(output.get("query_result")) != normalized(expected):
            mismatches.append({"id": row["id"], "scenario_id": scenario_id, "filter": query.get("filter")})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "pass"
            if not errors
            and not mismatches
            and len(truth) + len(unreplayed_scenarios) == len(needed)
            and checked_query_rows + sum(
                1
                for row in query_rows
                if str((row.get("structured_query") or {}).get("scenario_id") or "")
                in unreplayed_scenarios
            ) == len(query_rows)
            and set(by_filter) == SUPPORTED_FILTERS
            and set(by_overload_scope) == SUPPORTED_EQUIPMENT_SCOPES
            else "fail"
        ),
        "implementation_independence": "separate parser and query executor that do not import generator/repair functions; electrical recomputation still uses the same pandapower case family and is an implementation-branch check",
        "dataset": args.dataset,
        "scenarios": args.scenarios,
        "query_records": len(query_rows),
        "checked_query_records": checked_query_rows,
        "unique_query_scenarios": len(needed),
        "recomputed_scenarios": len(truth),
        "unreplayed_scenario_count": len(unreplayed_scenarios),
        "unreplayed_scenarios": [
            {"scenario_id": scenario_id, **details}
            for scenario_id, details in sorted(unreplayed_scenarios.items())
        ],
        "unreplayed_query_record_count": sum(
            1
            for row in query_rows
            if str((row.get("structured_query") or {}).get("scenario_id") or "")
            in unreplayed_scenarios
        ),
        "simulation_error_count": len(errors),
        "simulation_errors": errors[:50],
        "answer_mismatch_count": len(mismatches),
        "answer_mismatch_examples": mismatches[:50],
        "replay_solver_counts": dict(
            Counter(str(item.get("replay_solver") or "unknown") for item in truth.values())
        ),
        "records_by_filter": dict(by_filter),
        "overload_records_by_equipment_scope": dict(by_overload_scope),
        "required_filter_coverage": sorted(SUPPORTED_FILTERS),
        "required_overload_equipment_scope_coverage": sorted(SUPPORTED_EQUIPMENT_SCOPES),
        "comparison": "complete-set equality with device IDs and values rounded to 1e-5",
        "contract_schema_version": QUERY_CONTRACT_VERSION,
        "dataset_sha256": file_sha256(ROOT / args.dataset),
        "scenario_truth_sha256": file_sha256(ROOT / args.scenarios),
        "query_projection_sha256": projection_sha256(dataset_rows),
    }
    write_json(ROOT / args.output_json, report)
    lines = [
        "# Independent Query Truth Validation",
        "",
        f"- Status: `{report['status']}`",
        f"- Query records: {report['query_records']}",
        f"- Unique scenarios independently recomputed: {report['recomputed_scenarios']}",
        f"- Scenarios retained outside complete replay: {report['unreplayed_scenario_count']}",
        f"- Query records retained outside complete replay: {report['unreplayed_query_record_count']}",
        f"- Simulation errors: {report['simulation_error_count']}",
        f"- Answer mismatches: {report['answer_mismatch_count']}",
        "",
        "Scenarios explicitly marked with `query_truth_complete=false` are retained in the dataset but are not",
        "promoted to independently recomputed numerical truth. All other query-linked scenarios must pass the",
        "deterministic Newton/Iwamoto replay and complete-set comparison.",
    ]
    (ROOT / args.output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
