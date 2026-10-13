#!/usr/bin/env python3
"""Rebind scenario-facing fields to the released scenario truth registry.

This pass repairs numeric units, severity metadata, issue profiles, query
results, and compliance labels for scenario-grounded rows.  Rule-only and
operation-ticket rows are left untouched.  A missing or explicitly failed
scenario is recorded as a bounded evidence case rather than silently filled.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT
from query_contract import execute_query
from validate_dataset import active_security_issues, expected_compliance_label, issue_profile


SCENARIO_FIELDS = (
    "scenario_id",
    "network_model",
    "contingency_type",
    "load_level",
    "max_branch_loading_percent",
    "min_bus_voltage_pu",
    "max_bus_voltage_pu",
    "severity_level",
    "solver_status",
    "query_truth_complete",
    "violation_type",
    "overloaded_branches",
    "overloaded_transformers",
    "voltage_violations",
)


def compact_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(scenario.get(key)) for key in SCENARIO_FIELDS}


def english_summary(scenario: dict[str, Any], variant: int) -> str:
    issues = active_security_issues(scenario)
    issue_text = ", ".join(issue.replace("_", " ") for issue in issues) if issues else "no explicit violation"
    values = {
        "network": scenario.get("network_model") or "the declared network",
        "scenario": scenario.get("scenario_id") or "the declared scenario",
        "kind": str(scenario.get("contingency_type") or "operating state").replace("_", " "),
        "load": float(scenario.get("load_level") or 1.0),
        "loading": scenario.get("max_branch_loading_percent"),
        "min_vm": scenario.get("min_bus_voltage_pu"),
        "severity": scenario.get("severity_level") or "normal",
        "issues": issue_text,
    }
    templates = (
        "{network} scenario {scenario}; mode {kind}; load level {load:.2f}; maximum branch loading {loading}; minimum bus voltage {min_vm}; severity {severity}; current focus: {issues}.",
        "Scenario {scenario} on {network} uses {kind} at load level {load:.2f}. The maximum branch loading is {loading}, the minimum bus voltage is {min_vm}, and the severity is {severity}; observed conditions: {issues}.",
        "For {network} scenario {scenario}, the {kind} state has load level {load:.2f}, maximum loading {loading}, minimum voltage {min_vm}, and severity {severity}. Review the following conditions: {issues}.",
    )
    return templates[variant % len(templates)].format(**values)


def load_registry(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("scenario registry must be a list")
    return {
        str(row.get("scenario_id")): compact_scenario(row)
        for row in payload
        if row.get("scenario_id")
    }


def repair_row(row: dict[str, Any], registry: dict[str, dict[str, Any]], counts: Counter[str]) -> dict[str, Any]:
    scenario_id = str(row.get("scenario_id") or "")
    if not scenario_id:
        counts["rule_or_non_scenario"] += 1
        return row
    scenario = registry.get(scenario_id)
    if scenario is None:
        counts["scenario_lookup_missing"] += 1
        return row
    counts["scenario_bound"] += 1
    metadata = dict(row.get("metadata") or {})
    variant = int(metadata.get("variant_index") or 0)
    old_severity = metadata.get("severity_level")
    new_severity = scenario.get("severity_level")
    if old_severity != new_severity:
        counts["severity_updates"] += 1
    metadata["severity_level"] = new_severity
    metadata["scenario_truth_binding"] = "scenario_registry_v2"
    row["metadata"] = metadata
    row["network_model"] = scenario.get("network_model") or row.get("network_model")
    row["source_simulation_case_id"] = scenario_id
    inp = dict(row.get("input") or {})
    task = str(row.get("task_type") or "")
    inp["grid_state_summary"] = english_summary(scenario, variant)
    profile = issue_profile(scenario)
    if "issue_profile" in inp:
        if inp.get("issue_profile") != profile:
            counts["issue_profile_updates"] += 1
        inp["issue_profile"] = profile
    if task == "regulation_compliance_check":
        issues = active_security_issues(scenario)
        if inp.get("observed_issues") != issues:
            counts["observed_issue_updates"] += 1
        inp["observed_issues"] = issues
        expected = expected_compliance_label(
            scenario,
            inp.get("proposed_action"),
            metadata.get("action_category"),
        )
        if expected and row.get("compliance_label") != expected:
            counts["compliance_label_updates"] += 1
            row["compliance_label"] = expected
            row["output"] = expected
    elif task == "dispatcher_intent_tool_call":
        output = dict(row.get("output") or {})
        slots = dict(output.get("slots") or {})
        slots["priority"] = "urgent" if new_severity in {"alert", "emergency"} else "normal"
        output["slots"] = slots
        row["output"] = output
        row["slots"] = slots
    elif task == "intelligent_data_query":
        query = row.get("structured_query") or {}
        if scenario.get("query_truth_complete") and scenario.get("solver_status") == "converged":
            try:
                result = execute_query(query, scenario)
                if row.get("query_result") != result:
                    counts["query_results_recomputed"] += 1
                row["query_result"] = copy.deepcopy(result)
                output = dict(row.get("output") or {})
                output["query_result"] = copy.deepcopy(result)
                row["output"] = output
            except Exception:
                counts["query_recompute_errors"] += 1
        else:
            metadata["query_truth_status"] = "solver_failed_state_bound"
            row["metadata"] = metadata
            counts["query_truth_bounded"] += 1
    row["input"] = inp
    counts["summary_rebound"] += 1
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--registry", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--report", default="reports/scenario_grounding_repair_v1.2_sd_core.json")
    args = parser.parse_args()

    registry = load_registry(ROOT / args.registry)
    source = ROOT / args.input
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False, prefix=f".{target.name}.") as handle:
        temp_path = Path(handle.name)
        with source.open(encoding="utf-8") as source_handle:
            for line in source_handle:
                if line.strip():
                    row = repair_row(json.loads(line), registry, counts)
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temp_path, target)
    report = {
        "status": "pass" if counts.get("scenario_lookup_missing", 0) == 0 and counts.get("query_recompute_errors", 0) == 0 else "warn",
        "input": str(source.relative_to(ROOT)),
        "output": str(target.relative_to(ROOT)),
        "registry": str((ROOT / args.registry).relative_to(ROOT)),
        "registry_records": len(registry),
        "counts": dict(sorted(counts.items())),
        "policy": "Scenario-facing summaries, severity fields, scenario-grounded query results, and compliance labels are rebound to the scenario registry; explicit failed states remain bounded and are not filled.",
    }
    (ROOT / args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
