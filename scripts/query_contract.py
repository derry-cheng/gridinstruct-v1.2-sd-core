#!/usr/bin/env python3
"""Shared executable contract for intelligent-data-query records."""

from __future__ import annotations

import copy
import json
import re
from typing import Any


SUPPORTED_FILTERS = {
    "loading_percent > 100",
    "vm_pu < 0.95",
    "violation_type != none",
}
LEGACY_QUERY_KEYS = {"source", "filter", "scenario_id"}
OVERLOAD_QUERY_KEYS = LEGACY_QUERY_KEYS | {
    "contract_version",
    "equipment_scope",
    "aggregation",
}
SUPPORTED_EQUIPMENT_SCOPES = {"line", "transformer", "all"}
OVERLOAD_AGGREGATION = "complete_list_and_max"
QUERY_CONTRACT_VERSION = "query-contract-v3"
QUERY_SOURCE = "simulation_outputs"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def truth_is_complete(scenario: dict[str, Any]) -> bool:
    checks = (
        ("overloaded_branch_count", "overloaded_branches"),
        ("overloaded_transformer_count", "overloaded_transformers"),
        ("voltage_violation_count", "voltage_violations"),
    )
    return bool(scenario.get("query_truth_complete")) and all(
        int(scenario.get(count_key) or 0) == len(scenario.get(list_key) or [])
        for count_key, list_key in checks
    )


def is_legacy_query(query: dict[str, Any]) -> bool:
    return set(query) == LEGACY_QUERY_KEYS


def query_shape_errors(query: dict[str, Any]) -> list[str]:
    if set(query) == LEGACY_QUERY_KEYS:
        return []
    if set(query) != OVERLOAD_QUERY_KEYS:
        return ["structured_query_key_set_mismatch"]
    if query.get("filter") != "loading_percent > 100":
        return ["extended_query_only_supported_for_loading_filter"]
    if query.get("contract_version") != QUERY_CONTRACT_VERSION:
        return ["structured_query_contract_version_mismatch"]
    if query.get("equipment_scope") not in SUPPORTED_EQUIPMENT_SCOPES:
        return ["structured_query_equipment_scope_unsupported"]
    if query.get("aggregation") != OVERLOAD_AGGREGATION:
        return ["structured_query_aggregation_unsupported"]
    return []


def overload_query(
    scenario_id: str,
    *,
    equipment_scope: str = "all",
) -> dict[str, Any]:
    if equipment_scope not in SUPPORTED_EQUIPMENT_SCOPES:
        raise ValueError(f"unsupported equipment scope: {equipment_scope!r}")
    return {
        "source": QUERY_SOURCE,
        "filter": "loading_percent > 100",
        "scenario_id": scenario_id,
        "contract_version": QUERY_CONTRACT_VERSION,
        "equipment_scope": equipment_scope,
        "aggregation": OVERLOAD_AGGREGATION,
    }


def _overload_records(scenario: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if scope in {"line", "all"}:
        for source in scenario.get("overloaded_branches") or []:
            item = copy.deepcopy(source)
            if float(item.get("loading_percent", 0.0)) <= 100.0:
                continue
            line_index = int(item.get("line", item.get("index", -1)))
            item["equipment_type"] = "line"
            item["equipment_id"] = str(item.get("branch_id") or f"line:{line_index}")
            records.append(item)
    if scope in {"transformer", "all"}:
        for source in scenario.get("overloaded_transformers") or []:
            item = copy.deepcopy(source)
            if float(item.get("loading_percent", 0.0)) <= 100.0:
                continue
            transformer_index = int(item.get("transformer", item.get("index", -1)))
            item["equipment_type"] = "transformer"
            item["equipment_id"] = str(
                item.get("transformer_id") or f"transformer:{transformer_index}"
            )
            records.append(item)
    return sorted(
        records,
        key=lambda item: (
            str(item["equipment_type"]),
            str(item["equipment_id"]),
            -float(item["loading_percent"]),
        ),
    )


def _valid_overload_result_record(
    item: Any,
    allowed_equipment_types: set[str],
) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("equipment_type") not in allowed_equipment_types:
        return False
    if not str(item.get("equipment_id") or ""):
        return False
    try:
        return float(item.get("loading_percent", 0.0)) > 100.0
    except (TypeError, ValueError):
        return False


def execute_query(query: dict[str, Any], scenario: dict[str, Any]) -> Any:
    shape_errors = query_shape_errors(query)
    if shape_errors:
        raise ValueError(f"invalid structured_query shape: {shape_errors}")
    if query.get("source") != QUERY_SOURCE:
        raise ValueError(f"unsupported structured_query source: {query.get('source')!r}")
    filt = str(query.get("filter") or "")
    if filt == "loading_percent > 100":
        if is_legacy_query(query):
            return scenario.get("overloaded_branches") or []
        scope = str(query["equipment_scope"])
        records = _overload_records(scenario, scope)
        return {
            "equipment_scope": scope,
            "records": records,
            "count": len(records),
            "max_loading_percent": (
                max(float(item["loading_percent"]) for item in records)
                if records
                else None
            ),
        }
    if filt == "vm_pu < 0.95":
        return [
            item
            for item in scenario.get("voltage_violations") or []
            if float(item.get("vm_pu", 1.0)) < 0.95
        ]
    if filt == "violation_type != none":
        return {
            "violation_type": scenario.get("violation_type") or [],
            "severity_level": scenario.get("severity_level"),
        }
    raise ValueError(f"unsupported structured_query filter: {filt!r}")


def normalize_query_mirrors(row: dict[str, Any]) -> dict[str, Any]:
    """Migrate missing mirror fields while refusing conflicting values."""
    migrated = copy.deepcopy(row)
    query = migrated.get("structured_query")
    if not isinstance(query, dict) or query_shape_errors(query):
        raise ValueError("structured_query_contract_invalid")
    if query.get("source") != QUERY_SOURCE or query.get("filter") not in SUPPORTED_FILTERS:
        raise ValueError("structured_query_source_or_filter_invalid")
    output = migrated.get("output")
    if not isinstance(output, dict):
        raise ValueError("output_not_object")
    output_query = output.get("structured_query")
    if output_query is not None and canonical(output_query) != canonical(query):
        raise ValueError("output_structured_query_mismatch")
    output["structured_query"] = copy.deepcopy(query)

    tool_plan = migrated.get("tool_plan")
    query_tools = [
        tool
        for tool in tool_plan or []
        if isinstance(tool, dict) and tool.get("tool") == "query_simulation_records"
    ]
    if len(query_tools) != 1 or len(tool_plan or []) != 1:
        raise ValueError("query_tool_plan_must_contain_exactly_one_query_tool")
    args = query_tools[0].get("args")
    if not isinstance(args, dict):
        raise ValueError("query_tool_args_not_object")
    for key in ("scenario_id", "filter"):
        if args.get(key) != query.get(key):
            raise ValueError(f"query_tool_{key}_mismatch")
    if args.get("source") not in (None, query["source"]):
        raise ValueError("query_tool_source_mismatch")
    args["source"] = query["source"]
    return migrated


def infer_legacy_overload_scope(row: dict[str, Any]) -> str:
    """Infer the narrowest scope explicitly requested by legacy prose."""
    text = " ".join(
        str(value or "")
        for value in (
            row.get("instruction"),
            (row.get("input") or {}).get("question")
            if isinstance(row.get("input"), dict)
            else None,
        )
    )
    lower = text.lower()
    if any(token in text for token in ("变压器", "主变")) or re.search(
        r"\b(?:transformers?|trafos?)\b",
        lower,
    ):
        return "transformer"
    if "线路" in text or re.search(r"\blines?\b", lower):
        return "line"
    return "all"


def upgrade_legacy_overload_contract(
    row: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    """Upgrade a v2 overload row without silently discarding transformer truth."""
    migrated = normalize_query_mirrors(row)
    old_query = migrated["structured_query"]
    if (
        not is_legacy_query(old_query)
        or old_query.get("filter") != "loading_percent > 100"
    ):
        return migrated
    scope = infer_legacy_overload_scope(migrated)
    query = overload_query(str(old_query["scenario_id"]), equipment_scope=scope)
    result = execute_query(query, scenario)
    migrated["structured_query"] = copy.deepcopy(query)
    migrated["output"]["structured_query"] = copy.deepcopy(query)
    migrated["tool_plan"][0]["args"] = copy.deepcopy(query)
    migrated["query_result"] = copy.deepcopy(result)
    migrated["output"]["query_result"] = copy.deepcopy(result)
    metadata = dict(migrated.get("metadata") or {})
    metadata["query_contract_migration"] = {
        "from": "query-contract-v2",
        "to": QUERY_CONTRACT_VERSION,
        "equipment_scope": scope,
        "scope_inference": "legacy_instruction_text",
    }
    migrated["metadata"] = metadata
    return migrated


def validate_query_contract(
    row: dict[str, Any],
    scenario: dict[str, Any] | None = None,
    *,
    require_result: bool = True,
) -> list[str]:
    errors: list[str] = []
    query = row.get("structured_query")
    output = row.get("output")
    output_query = output.get("structured_query") if isinstance(output, dict) else None
    if not isinstance(query, dict):
        return ["structured_query_not_object"]
    errors.extend(query_shape_errors(query))
    if query.get("source") != QUERY_SOURCE:
        errors.append("structured_query_source_mismatch")
    if query.get("filter") not in SUPPORTED_FILTERS:
        errors.append("structured_query_filter_unsupported")
    if not isinstance(output, dict):
        errors.append("output_not_object")
    elif not isinstance(output_query, dict) or canonical(output_query) != canonical(query):
        errors.append("output_structured_query_mismatch")

    input_value = row.get("input") if isinstance(row.get("input"), dict) else {}
    ids = {
        str(value)
        for value in (
            row.get("scenario_id"),
            row.get("source_simulation_case_id"),
            input_value.get("scenario_id"),
            query.get("scenario_id"),
            output_query.get("scenario_id") if isinstance(output_query, dict) else None,
        )
        if value not in (None, "")
    }
    if len(ids) != 1:
        errors.append("scenario_id_mismatch")

    tool_plan = row.get("tool_plan")
    tools = [tool for tool in tool_plan or [] if isinstance(tool, dict)]
    if len(tools) != 1 or tools[0].get("tool") != "query_simulation_records":
        errors.append("query_tool_plan_mismatch")
    else:
        args = tools[0].get("args")
        if not isinstance(args, dict) or query_shape_errors(args):
            errors.append("query_tool_args_key_set_mismatch")
        elif canonical(args) != canonical(query):
            errors.append("query_tool_args_mismatch")

    if require_result:
        if not isinstance(output, dict) or canonical(row.get("query_result")) != canonical(output.get("query_result")):
            errors.append("query_result_mirror_mismatch")
        if scenario is None:
            errors.append("scenario_truth_missing")
        else:
            try:
                expected = execute_query(query, scenario)
                if canonical(row.get("query_result")) != canonical(expected):
                    errors.append("query_result_truth_mismatch")
            except ValueError:
                errors.append("query_execution_failed")
        result = row.get("query_result")
        if (
            isinstance(query, dict)
            and query.get("filter") == "loading_percent > 100"
            and not is_legacy_query(query)
        ):
            if not isinstance(result, dict):
                errors.append("overload_query_result_not_object")
            else:
                records = result.get("records")
                if set(result) != {
                    "equipment_scope",
                    "records",
                    "count",
                    "max_loading_percent",
                }:
                    errors.append("overload_query_result_key_set_mismatch")
                if result.get("equipment_scope") != query.get("equipment_scope"):
                    errors.append("overload_query_result_scope_mismatch")
                if not isinstance(records, list):
                    errors.append("overload_query_records_not_list")
                else:
                    try:
                        count_matches = int(result.get("count", -1)) == len(records)
                    except (TypeError, ValueError):
                        count_matches = False
                    if not count_matches:
                        errors.append("overload_query_count_mismatch")
                    scope = str(query.get("equipment_scope"))
                    allowed_types = (
                        {"line", "transformer"}
                        if scope == "all"
                        else {scope}
                    )
                    if any(
                        not _valid_overload_result_record(item, allowed_types)
                        for item in records
                    ):
                        errors.append("overload_query_records_invalid")
                    try:
                        expected_max = (
                            max(float(item["loading_percent"]) for item in records)
                            if records
                            else None
                        )
                        if result.get("max_loading_percent") != expected_max:
                            errors.append("overload_query_max_aggregation_mismatch")
                    except (KeyError, TypeError, ValueError):
                        errors.append("overload_query_max_aggregation_invalid")
    return sorted(set(errors))


def query_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projection = []
    for row in rows:
        if row.get("task_type") != "intelligent_data_query":
            continue
        output = row.get("output") if isinstance(row.get("output"), dict) else {}
        projection.append(
            {
                "id": row.get("id"),
                "scenario_id": row.get("scenario_id"),
                "source_simulation_case_id": row.get("source_simulation_case_id"),
                "input_scenario_id": (row.get("input") or {}).get("scenario_id"),
                "structured_query": row.get("structured_query"),
                "query_result": row.get("query_result"),
                "output_structured_query": output.get("structured_query"),
                "output_query_result": output.get("query_result"),
                "tool_plan": row.get("tool_plan"),
            }
        )
    return projection
