"""Shared utilities for GridInstruct data generation and validation."""

from __future__ import annotations

import json
import os
import random
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TASK_STAGES = ("pre-event", "real-time", "post-event")
TASK_TYPES = (
    "operation_ticket_check",
    "regulation_qa",
    "regulation_compliance_check",
    "auxiliary_decision",
    "dispatcher_intent_tool_call",
    "intelligent_data_query",
)


def ensure_dirs(*paths: str | Path) -> None:
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    ensure_dirs(path.parent)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    atomic_write_text(path, payload)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    ensure_dirs(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: str | Path, payload: str) -> None:
    """Durably replace a text artifact without exposing partial files."""
    path = Path(path)
    ensure_dirs(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def stable_shuffle(rows: list[dict[str, Any]], seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows = list(rows)
    rng.shuffle(rows)
    return rows


def default_schema() -> dict[str, Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "GridInstruct instruction record",
        "type": "object",
        "required": [
            "id",
            "task_stage",
            "task_type",
            "instruction",
            "input",
            "output",
            "source_regulation_ids",
            "metadata",
        ],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "task_stage": {"type": "string", "enum": list(TASK_STAGES)},
            "task_type": {"type": "string", "enum": list(TASK_TYPES)},
            "network_model": {"type": ["string", "null"]},
            "scenario_id": {"type": ["string", "null"]},
            "instruction": {"type": "string", "minLength": 1},
            "input": {"type": ["object", "string"]},
            "output": {"type": ["object", "string"], "minLength": 1},
            "rationale": {"type": ["string", "null"]},
            "chosen_response": {"type": ["object", "string", "null"]},
            "rejected_response": {"type": ["object", "string", "null"]},
            "preference_rationale": {"type": ["object", "null"]},
            "executable_control_target": {"type": ["object", "null"]},
            "target_replay_validation": {"type": ["object", "null"]},
            "source_regulation_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "source_simulation_case_id": {"type": ["string", "null"]},
            "tool_plan": {"type": "array"},
            "compliance_label": {"type": ["string", "null"]},
            "intent": {"type": ["string", "null"]},
            "slots": {"type": ["object", "null"]},
            "structured_query": {"type": ["object", "null"]},
            "query_result": {"type": ["array", "object", "string", "null"]},
            "metadata": {"type": "object"},
        },
        "allOf": [
            {
                "if": {"properties": {"task_type": {"const": "operation_ticket_check"}}},
                "then": {"required": ["compliance_label"]},
            },
            {
                "if": {"properties": {"task_type": {"const": "regulation_compliance_check"}}},
                "then": {"required": ["compliance_label"]},
            },
            {
                "if": {"properties": {"task_type": {"const": "dispatcher_intent_tool_call"}}},
                "then": {"required": ["intent", "slots"]},
            },
            {
                "if": {"properties": {"task_type": {"const": "intelligent_data_query"}}},
                "then": {"required": ["structured_query", "query_result"]},
            },
            {
                "if": {"properties": {"task_type": {"const": "auxiliary_decision"}}},
                "then": {"required": ["chosen_response", "rejected_response"]},
            },
        ],
        "additionalProperties": True,
    }


def default_task_taxonomy() -> dict[str, Any]:
    return {
        "pre-event": ["operation_ticket_check"],
        "real-time": [
            "regulation_qa",
            "regulation_compliance_check",
            "auxiliary_decision",
            "dispatcher_intent_tool_call",
        ],
        "post-event": ["intelligent_data_query"],
    }


def default_rules() -> list[dict[str, Any]]:
    rules_path = ROOT / "rules/regulation_rules.json"
    if rules_path.exists():
        return read_json(rules_path)
    return [
        {
            "rule_id": "REG_OVERLOAD_001",
            "rule_type": "threshold",
            "summary": "线路负载率超过 100% 进入告警，超过 110% 判定为严重越限。",
            "standard_id": "GRIDINSTRUCT-V1.2-RULEBOOK",
            "standard_title": "GridInstruct v1.2 internal rulebook",
            "clause_id": "3.1",
            "source_title": "Scenario security threshold for branch loading alarms and severe overloads",
            "evidence_fields": ["branch_loading_percent"],
            "applicable_tasks": ["regulation_compliance_check", "auxiliary_decision"],
        },
        {
            "rule_id": "REG_VOLTAGE_001",
            "rule_type": "threshold",
            "summary": "母线电压低于 0.95 p.u. 或高于 1.05 p.u. 需触发电压控制校核。",
            "standard_id": "GRIDINSTRUCT-V1.2-RULEBOOK",
            "standard_title": "GridInstruct v1.2 internal rulebook",
            "clause_id": "3.2",
            "source_title": "Scenario voltage security threshold for dispatch validation",
            "evidence_fields": ["bus_voltage_pu"],
            "applicable_tasks": ["regulation_compliance_check", "auxiliary_decision"],
        },
        {
            "rule_id": "REG_SWITCHING_001",
            "rule_type": "procedural",
            "summary": "倒闸操作前应核对设备状态、操作对象和安全措施。",
            "standard_id": "GRIDINSTRUCT-V1.2-RULEBOOK",
            "standard_title": "GridInstruct v1.2 internal rulebook",
            "clause_id": "4.1",
            "source_title": "Switching-ticket pre-check rule for equipment status and safety measures",
            "evidence_fields": ["equipment_state", "operation_ticket"],
            "applicable_tasks": ["operation_ticket_check"],
        },
        {
            "rule_id": "REG_TOOL_001",
            "rule_type": "procedural",
            "summary": "执行调度操作前应先进行状态查询、约束校核和必要的潮流计算。",
            "standard_id": "GRIDINSTRUCT-V1.2-RULEBOOK",
            "standard_title": "GridInstruct v1.2 internal rulebook",
            "clause_id": "5.1",
            "source_title": "Dispatch tool-use rule for state query, constraint check, and power-flow validation",
            "evidence_fields": ["tool_plan", "safety_checks"],
            "applicable_tasks": ["dispatcher_intent_tool_call"],
        },
        {
            "rule_id": "REG_QUERY_001",
            "rule_type": "data_query",
            "summary": "事后问数应明确时间范围、对象类型、筛选条件和聚合方式。",
            "standard_id": "GRIDINSTRUCT-V1.2-RULEBOOK",
            "standard_title": "GridInstruct v1.2 internal rulebook",
            "clause_id": "6.1",
            "source_title": "Post-event analytics query rule for scope, filters, and aggregation",
            "evidence_fields": ["structured_query"],
            "applicable_tasks": ["intelligent_data_query"],
        },
    ]


def summarize_records(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_records": len(rows),
        "by_stage": dict(Counter(row.get("task_stage") for row in rows)),
        "by_task": dict(Counter(row.get("task_type") for row in rows)),
        "by_network": dict(Counter(row.get("network_model") for row in rows)),
        "with_rationale": sum(bool(row.get("rationale")) for row in rows),
        "with_preference": sum(
            bool(row.get("chosen_response")) and bool(row.get("rejected_response"))
            for row in rows
        ),
    }
