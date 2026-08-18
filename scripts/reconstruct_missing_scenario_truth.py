#!/usr/bin/env python3
"""Reconstruct every dataset scenario ID absent from the compact registry.

The input IDs are parsed into typed scenario parameters.  Standard IEEE cases
use ``generate_grid_scenarios.run_scenario``; larger PGLib stress cases use
``generate_extended_topology_stress.run_case``; the sealed IEEE14 candidate
ledger is imported only after its PGLib provenance is checked.  No row is
silently dropped: failed attempts remain in the attempt ledger and the final
registry is published only when every requested ID has a matching state record.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT


STANDARD_SYSTEMS = {"ieee14", "ieee30", "ieee57", "ieee118", "ieee300"}
EXTERNAL_SYSTEMS = {"illinois200", "pegase89", "pegase1354", "pegase2869", "rte1888", "rte2848"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def factor_value(token: str) -> float:
    value = token.replace("m", "-").replace("p", ".")
    parsed = float(value)
    return parsed if ("p" in token or "m" in token and "." in token) else parsed / 100.0


def parse_standard(scenario_id: str) -> dict[str, Any]:
    if "_base_power_flow_load" in scenario_id:
        system, token = scenario_id.split("_base_power_flow_load", 1)
        return {"kind": "base_power_flow", "system": system, "load": factor_value(token)}
    match = re.match(r"^(?P<system>[^_]+)_generator_perturbation_load(?P<load>[^_]+)_gen(?P<gen>[^_]+)$", scenario_id)
    if match:
        return {"kind": "generator_perturbation", "system": match["system"], "load": factor_value(match["load"]), "gen": factor_value(match["gen"])}
    match = re.match(r"^(?P<system>[^_]+)_n2_branch_outage_load(?P<load>[^_]+)_branches_(?P<values>\d+_\d+_\d+_\d+_\d+_\d+)$", scenario_id)
    if match:
        values = match["values"].split("_")
        return {"kind": "n2_branch_outage", "system": match["system"], "load": factor_value(match["load"]), "first": int(values[0]), "second": int(values[3])}
    match = re.match(r"^(?P<system>[^_]+)_n1_transformer_outage_load(?P<load>[^_]+)_trafoidx_(?P<index>\d+)$", scenario_id)
    if match:
        return {"kind": "n1_transformer_outage", "system": match["system"], "load": factor_value(match["load"]), "index": int(match["index"])}
    match = re.match(r"^(?P<system>[^_]+)_n1_generator_outage_load(?P<load>[^_]+)_genidx_(?P<index>\d+)$", scenario_id)
    if match:
        return {"kind": "n1_generator_outage", "system": match["system"], "load": factor_value(match["load"]), "index": int(match["index"])}
    match = re.match(r"^(?P<system>[^_]+)_n1_bus_outage_load(?P<load>[^_]+)_busidx_(?P<index>\d+)$", scenario_id)
    if match:
        return {"kind": "n1_bus_outage", "system": match["system"], "load": factor_value(match["load"]), "index": int(match["index"])}
    match = re.match(r"^(?P<system>[^_]+)_n1_branch_outage_load(?P<load>[^_]+)_branchidx_(?P<index>\d+)_.*$", scenario_id)
    if match:
        return {"kind": "n1_branch_outage", "system": match["system"], "load": factor_value(match["load"]), "index": int(match["index"])}
    raise ValueError(f"unrecognised standard scenario ID: {scenario_id}")


def parse_external(scenario_id: str) -> dict[str, Any]:
    match = re.match(r"^(?P<system>[^_]+)_sdv2_(?P<kind>base_power_flow|n1_branch_outage)_load(?P<load>[^_]+)(?:_line(?P<line>\d+))?$", scenario_id)
    if not match:
        raise ValueError(f"unrecognised external scenario ID: {scenario_id}")
    return {
        "system": match["system"],
        "kind": match["kind"],
        "load": factor_value(match["load"]),
        "line": int(match["line"]) if match["line"] is not None else None,
    }


def reconstruct_one(task: tuple[str, str, str]) -> dict[str, Any]:
    scenario_id, pglib_root_text, mode = task
    try:
        root = Path(pglib_root_text)
        if scenario_id.startswith("ieee14_secure_candidate_"):
            raise ValueError("secure candidate must be supplied from its sealed ledger")
        if "_sdv2_" in scenario_id:
            from generate_extended_topology_stress import run_case

            params = parse_external(scenario_id)
            row = run_case(
                params["system"], params["load"], params["kind"], params["line"],
                scenario_namespace="sdv2", pglib_root=root,
            )
            if row.get("solver_status") != "converged":
                row = run_case(
                    params["system"], params["load"], params["kind"], params["line"],
                    scenario_namespace="sdv2", pglib_root=root,
                    solver_algorithm="iwamoto_nr", enforce_q_lims=False, max_iteration=200,
                )
        else:
            from generate_grid_scenarios import run_scenario

            params = parse_standard(scenario_id)
            kwargs: dict[str, Any] = {"pglib_root": root, "scale_generation_with_load": False}
            if params["kind"] == "n2_branch_outage":
                row = run_scenario(params["system"], params["load"], params["kind"], params["first"], 1.0, second_branch_idx=params["second"], **kwargs)
            elif params["kind"] == "generator_perturbation":
                row = run_scenario(params["system"], params["load"], params["kind"], None, params["gen"], **kwargs)
            elif params["kind"] == "n1_transformer_outage":
                row = run_scenario(params["system"], params["load"], params["kind"], None, 1.0, transformer_idx=params["index"], **kwargs)
            elif params["kind"] == "n1_generator_outage":
                row = run_scenario(params["system"], params["load"], params["kind"], None, 1.0, generator_idx=params["index"], **kwargs)
            elif params["kind"] == "n1_bus_outage":
                row = run_scenario(params["system"], params["load"], params["kind"], None, 1.0, bus_idx=params["index"], **kwargs)
            elif params["kind"] == "n1_branch_outage":
                row = run_scenario(params["system"], params["load"], params["kind"], params["index"], 1.0, **kwargs)
            else:
                row = run_scenario(params["system"], params["load"], params["kind"], None, 1.0, **kwargs)
            if row.get("solver_status") != "converged":
                fallback_kwargs = dict(kwargs, solver_algorithm="iwamoto_nr", enforce_q_lims=False, max_iteration=200)
                if params["kind"] == "n2_branch_outage":
                    row = run_scenario(params["system"], params["load"], params["kind"], params["first"], 1.0, second_branch_idx=params["second"], **fallback_kwargs)
                elif params["kind"] == "generator_perturbation":
                    row = run_scenario(params["system"], params["load"], params["kind"], None, params["gen"], **fallback_kwargs)
                elif params["kind"] == "n1_transformer_outage":
                    row = run_scenario(params["system"], params["load"], params["kind"], None, 1.0, transformer_idx=params["index"], **fallback_kwargs)
                elif params["kind"] == "n1_generator_outage":
                    row = run_scenario(params["system"], params["load"], params["kind"], None, 1.0, generator_idx=params["index"], **fallback_kwargs)
                elif params["kind"] == "n1_bus_outage":
                    row = run_scenario(params["system"], params["load"], params["kind"], None, 1.0, bus_idx=params["index"], **fallback_kwargs)
                elif params["kind"] == "n1_branch_outage":
                    row = run_scenario(params["system"], params["load"], params["kind"], params["index"], 1.0, **fallback_kwargs)
                else:
                    row = run_scenario(params["system"], params["load"], params["kind"], None, 1.0, **fallback_kwargs)
        if row.get("scenario_id") != scenario_id:
            raise ValueError(f"scenario ID mismatch: requested {scenario_id}, generated {row.get('scenario_id')}")
        row["reconstruction"] = {"method": mode, "requested_scenario_id": scenario_id}
        return {"status": "pass", "scenario_id": scenario_id, "row": row, "solver_status": row.get("solver_status")}
    except Exception as exc:  # noqa: BLE001 - retained in the attempt ledger
        return {"status": "fail", "scenario_id": scenario_id, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--existing", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--secure-candidates", default="simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json")
    parser.add_argument("--pglib-root", default="third_party/pglib-opf-v23.07")
    parser.add_argument("--output", default="simulation_outputs/contingency/scenarios_converged_complete.json")
    parser.add_argument("--attempts-output", default="simulation_outputs/contingency/scenario_reconstruction_attempts.json")
    parser.add_argument("--report-json", default="reports/scenario_truth_reconstruction_v1.2_sd_core.json")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--retry-failures", action="store_true", default=True)
    args = parser.parse_args()

    dataset_rows = read_jsonl(ROOT / args.dataset)
    requested_ids = sorted({str(row.get("scenario_id")) for row in dataset_rows if row.get("scenario_id")})
    existing_rows = json.loads((ROOT / args.existing).read_text(encoding="utf-8"))
    existing_by_id = {str(row["scenario_id"]): row for row in existing_rows}
    secure_rows = json.loads((ROOT / args.secure_candidates).read_text(encoding="utf-8"))
    secure_by_id = {str(row["scenario_id"]): row for row in secure_rows}
    missing = [scenario_id for scenario_id in requested_ids if scenario_id not in existing_by_id]
    retry_ids = [
        scenario_id for scenario_id in requested_ids
        if scenario_id in existing_by_id and existing_by_id[scenario_id].get("solver_status") != "converged"
    ] if args.retry_failures else []

    attempts: list[dict[str, Any]] = []
    bound_secure = []
    compute_tasks: list[tuple[str, str, str]] = []
    for scenario_id in sorted(set(missing + retry_ids)):
        if scenario_id in secure_by_id:
            bound_secure.append({"status": "pass", "scenario_id": scenario_id, "row": secure_by_id[scenario_id], "method": "sealed_secure_candidate_ledger"})
        elif "_sdv2_" in scenario_id:
            compute_tasks.append((scenario_id, str(ROOT / args.pglib_root), "pglib_external_topology_replay"))
        else:
            compute_tasks.append((scenario_id, str(ROOT / args.pglib_root), "pglib_standard_scenario_replay"))

    computed: list[dict[str, Any]] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(reconstruct_one, task) for task in compute_tasks]
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            computed.append(result)
            if index % max(1, args.progress_every) == 0 or index == len(futures):
                progress = {
                    "completed": index,
                    "total": len(futures),
                    "fraction": index / max(1, len(futures)),
                    "passed": sum(item.get("status") == "pass" for item in computed),
                    "failed": sum(item.get("status") == "fail" for item in computed),
                }
                print(json.dumps(progress, ensure_ascii=False), flush=True)

    attempts = bound_secure + computed
    attempt_by_id = {item["scenario_id"]: item for item in attempts}
    complete_by_id = dict(existing_by_id)
    for item in attempts:
        if item.get("status") == "pass" and item.get("row"):
            complete_by_id[item["scenario_id"]] = item["row"]
    missing_after = [scenario_id for scenario_id in requested_ids if scenario_id not in complete_by_id]
    duplicate_existing = len(existing_rows) - len(existing_by_id)
    output_rows = [complete_by_id[scenario_id] for scenario_id in sorted(complete_by_id)]
    (ROOT / args.output).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / args.output).write_text(json.dumps(output_rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (ROOT / args.attempts_output).write_text(json.dumps(sorted(attempts, key=lambda item: item["scenario_id"]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "status": "pass" if not missing_after and not duplicate_existing else "fail",
        "dataset_records": len(dataset_rows),
        "requested_unique_scenario_ids": len(requested_ids),
        "existing_registry_records": len(existing_rows),
        "existing_registry_duplicate_ids": duplicate_existing,
        "missing_before_reconstruction": len(missing),
        "nonconverged_retries_requested": len(retry_ids),
        "sealed_secure_candidate_bindings": len(bound_secure),
        "computed_attempts": len(computed),
        "computed_passes": sum(item.get("status") == "pass" for item in computed),
        "computed_failures": sum(item.get("status") == "fail" for item in computed),
        "complete_registry_records": len(output_rows),
        "missing_after_reconstruction": len(missing_after),
        "missing_after_examples": missing_after[:50],
        "by_method": dict(Counter(item.get("method", "computed") for item in attempts)),
        "output": args.output,
        "attempts_output": args.attempts_output,
        "pglib_root": args.pglib_root,
        "pglib_commit": (ROOT / args.pglib_root / "UPSTREAM_COMMIT").read_text(encoding="utf-8").strip(),
    }
    (ROOT / args.report_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
