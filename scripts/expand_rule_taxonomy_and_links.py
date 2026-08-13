#!/usr/bin/env python3
"""Expand rule-card taxonomy and attach secondary rule links.

The script keeps labels, outputs, ids, scenarios, and splits unchanged. It adds
more granular rule cards and appends deterministic secondary rule identifiers
to records according to existing evidence fields.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, atomic_write_text, ensure_dirs, read_json, write_json, write_jsonl as atomic_write_jsonl


RULEBOOK_ID = "GRIDINSTRUCT-V1.2-RULEBOOK"
RULEBOOK_TITLE = "GridInstruct v1.2 internal rulebook"
GRID_RULE_URL = "https://gzb.nea.gov.cn/dtyw/tzgg/202308/t20230821_3254.html"
DISPATCH_RULE_URL = "https://www.nea.gov.cn/2011-08/19/c_131060575.htm"
QUERY_RULE_URL = "https://www.nea.gov.cn/2017-11/02/c_136723008.htm"
SWITCHING_RULE_URL = "https://www.nea.gov.cn/2011-08/19/c_131060575.htm"
ACCESS_DATE = "2026-07-10"

RULE_EVIDENCE_FIELDS = {
    "REG_OVERLOAD_001": ["instruction", "input"],
    "REG_VOLTAGE_001": ["instruction", "input"],
    "REG_SWITCHING_001": ["instruction", "input"],
    "REG_TOOL_001": ["instruction", "input"],
    "REG_QUERY_001": ["instruction", "input"],
    "REG_OVERLOAD_ALERT_002": ["input.observed_issues", "input.grid_state_summary"],
    "REG_OVERLOAD_EMERGENCY_003": ["input.observed_issues", "input.grid_state_summary"],
    "REG_VOLTAGE_LOW_002": ["input.observed_issues", "input.grid_state_summary"],
    "REG_VOLTAGE_HIGH_003": ["input.observed_issues", "input.grid_state_summary"],
    "REG_SECURITY_REDISPATCH_004": ["input.available_actions", "input.utterance"],
    "REG_SWITCHING_PERMISSION_002": ["input.dispatch_permit_status"],
    "REG_SWITCHING_MONITORING_003": ["input.monitoring_arrangement"],
    "REG_TOOL_DIAGNOSIS_002": ["input.utterance"],
    "REG_TOOL_MITIGATION_003": ["input.utterance"],
    "REG_TOOL_POSTCHECK_004": ["input.decision_window", "input.utterance", "instruction"],
    "REG_QUERY_FILTER_002": ["input.scenario_id", "input.question", "instruction"],
    "REG_QUERY_AGGREGATION_003": ["input.question", "instruction"],
}


EXPANDED_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "REG_OVERLOAD_ALERT_002",
        "rule_type": "threshold_detail",
        "summary": "Branch loading above the alarm threshold requires monitoring, limit verification, and review before further transfer increase.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "3.1.1",
        "source_title": "Public reference: grid operating boundary and security-constraint requirements",
        "source_url": GRID_RULE_URL,
        "evidence_fields": ["branch_loading_percent", "severity_level", "issue_profile"],
        "applicable_tasks": ["regulation_compliance_check", "auxiliary_decision", "intelligent_data_query"],
    },
    {
        "rule_id": "REG_OVERLOAD_EMERGENCY_003",
        "rule_type": "threshold_detail",
        "summary": "Severe overload states require corrective dispatch or redispatch before accepting actions that increase constrained flow.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "3.1.2",
        "source_title": "Public reference: grid operating boundary and security-constraint requirements",
        "source_url": GRID_RULE_URL,
        "evidence_fields": ["branch_loading_percent", "severity_level", "action_category"],
        "applicable_tasks": ["regulation_compliance_check", "auxiliary_decision"],
    },
    {
        "rule_id": "REG_VOLTAGE_LOW_002",
        "rule_type": "threshold_detail",
        "summary": "Low-voltage states require voltage-control review before actions that may further reduce voltage margin.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "3.2.1",
        "source_title": "Public reference: voltage-quality and security-check requirements",
        "source_url": GRID_RULE_URL,
        "evidence_fields": ["bus_voltage_pu", "observed_issues", "issue_profile"],
        "applicable_tasks": ["regulation_compliance_check", "auxiliary_decision"],
    },
    {
        "rule_id": "REG_VOLTAGE_HIGH_003",
        "rule_type": "threshold_detail",
        "summary": "High-voltage states require reactive-power or voltage-setpoint review before action approval.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "3.2.2",
        "source_title": "Public reference: voltage-quality and security-check requirements",
        "source_url": GRID_RULE_URL,
        "evidence_fields": ["bus_voltage_pu", "observed_issues", "issue_profile"],
        "applicable_tasks": ["regulation_compliance_check", "auxiliary_decision"],
    },
    {
        "rule_id": "REG_SECURITY_REDISPATCH_004",
        "rule_type": "closed_loop_validation",
        "summary": "Corrective redispatch suggestions must include post-action power-flow validation and constraint review.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "3.3",
        "source_title": "Public reference: dispatch operation security-check and operating-control requirements",
        "source_url": DISPATCH_RULE_URL,
        "evidence_fields": ["tool_plan", "closed_loop_validation", "post_action_violation_summary"],
        "applicable_tasks": ["auxiliary_decision", "dispatcher_intent_tool_call"],
    },
    {
        "rule_id": "REG_SWITCHING_PERMISSION_002",
        "rule_type": "procedural_detail",
        "summary": "Operation-ticket execution requires explicit dispatch permission or a documented permit status before execution.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "4.2",
        "source_title": "Public reference: operation-ticket, equipment-state, and safety-measure requirements",
        "source_url": SWITCHING_RULE_URL,
        "evidence_fields": ["dispatch_permit_status", "operation_ticket", "key_checks"],
        "applicable_tasks": ["operation_ticket_check"],
    },
    {
        "rule_id": "REG_SWITCHING_MONITORING_003",
        "rule_type": "procedural_detail",
        "summary": "Monitoring arrangement and supervisory presence must be checked when a switching ticket is conditionally acceptable.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "4.3",
        "source_title": "Public reference: operation-ticket, equipment-state, and safety-measure requirements",
        "source_url": SWITCHING_RULE_URL,
        "evidence_fields": ["monitoring_arrangement", "operation_context", "equipment_state"],
        "applicable_tasks": ["operation_ticket_check"],
    },
    {
        "rule_id": "REG_TOOL_DIAGNOSIS_002",
        "rule_type": "tool_routing_detail",
        "summary": "Diagnostic dispatch requests should first query violations and relevant state records before suggesting corrective actions.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "5.2",
        "source_title": "Public reference: dispatch instruction execution and security-check requirements",
        "source_url": DISPATCH_RULE_URL,
        "evidence_fields": ["intent", "tool_plan", "slots"],
        "applicable_tasks": ["dispatcher_intent_tool_call"],
    },
    {
        "rule_id": "REG_TOOL_MITIGATION_003",
        "rule_type": "tool_routing_detail",
        "summary": "Mitigation requests should bind violation retrieval to corrective-action suggestion and follow-up validation tools.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "5.3",
        "source_title": "Public reference: dispatch instruction execution and security-check requirements",
        "source_url": DISPATCH_RULE_URL,
        "evidence_fields": ["intent", "tool_plan", "target_issue"],
        "applicable_tasks": ["dispatcher_intent_tool_call"],
    },
    {
        "rule_id": "REG_TOOL_POSTCHECK_004",
        "rule_type": "tool_routing_detail",
        "summary": "Post-action dispatch confirmation should include a power-flow recalculation or equivalent state validation step.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "5.4",
        "source_title": "Public reference: dispatch instruction execution and security-check requirements",
        "source_url": DISPATCH_RULE_URL,
        "evidence_fields": ["tool_plan", "scenario_id", "post_action"],
        "applicable_tasks": ["dispatcher_intent_tool_call", "auxiliary_decision"],
    },
    {
        "rule_id": "REG_QUERY_FILTER_002",
        "rule_type": "data_query_detail",
        "summary": "Post-event data queries must state the simulation source and filter condition used to retrieve records.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "6.2",
        "source_title": "Public reference: dispatch automation monitoring and operating-information processing requirements",
        "source_url": QUERY_RULE_URL,
        "evidence_fields": ["input.scenario_id", "input.question", "instruction"],
        "applicable_tasks": ["intelligent_data_query"],
    },
    {
        "rule_id": "REG_QUERY_AGGREGATION_003",
        "rule_type": "data_query_detail",
        "summary": "Queries asking for overload lists or maxima must preserve line, transformer, or all-equipment scope and return the complete typed list, count, and maximum in the structured target.",
        "standard_id": RULEBOOK_ID,
        "standard_title": RULEBOOK_TITLE,
        "clause_id": "6.3",
        "source_title": "Public reference: dispatch automation monitoring and operating-information processing requirements",
        "source_url": QUERY_RULE_URL,
        "evidence_fields": [
            "input.question",
            "instruction",
            "structured_query.equipment_scope",
            "structured_query.aggregation",
            "query_result.records",
            "query_result.max_loading_percent",
        ],
        "applicable_tasks": ["intelligent_data_query"],
    },
]


def modernize_rule_provenance(rule: dict[str, Any]) -> dict[str, Any]:
    rule = dict(rule)
    rule_id = str(rule["rule_id"])
    if "OVERLOAD" in rule_id or "VOLTAGE" in rule_id:
        rule.update(
            {
                "source_title": "电网调度管理条例第十八条：电压、设备负载和稳定限额超过规定范围时可采取调度措施",
                "source_url": DISPATCH_RULE_URL,
                "external_clause_locator": "第十八条第（二）至（四）项",
                "provenance_type": "dataset_threshold_policy_with_regulatory_principle",
                "source_kind": "dataset_policy_with_formal_regulatory_principle",
                "issuing_body": "State Council of the People's Republic of China; Standardization Administration of China",
                "document_id": "国务院令第115号; GB/T 31464-2022; GB 38755-2019",
                "document_version": "official current registry entries accessed 2026-07-10",
                "numeric_threshold_source": "GridInstruct dataset policy; external regulation does not define 0.95/1.05 or 100/110 percent",
                "normative_sources": [
                    {
                        "document_id": "国务院令第115号",
                        "title": "电网调度管理条例",
                        "clause": "第十八条第（二）至（四）项",
                        "url": DISPATCH_RULE_URL,
                        "relationship": "supports intervention when voltage, equipment loading, or stability limits exceed prescribed ranges",
                    },
                    {
                        "document_id": "GB/T 31464-2022",
                        "title": "电网运行准则",
                        "clause": "standard-level contextual reference; exact numeric thresholds are not claimed",
                        "url": "https://std.samr.gov.cn/gb/search/gbDetailed?id=F159DFC2A89547EFE05397BE0A0AF334",
                        "relationship": "current grid-operation standard status and scope",
                    },
                    {
                        "document_id": "GB 38755-2019",
                        "title": "电力系统安全稳定导则",
                        "clause": "standard-level contextual reference; exact numeric thresholds are not claimed",
                        "url": "https://std.samr.gov.cn/gb/search/gbDetailed?id=9B70DDA94011A80CE05397BE0A0A84AC",
                        "relationship": "current security and stability standard status and scope",
                    },
                ],
            }
        )
    elif "SWITCHING" in rule_id:
        rule.update(
            {
                "source_title": "电网调度管理条例第二十条：调度管辖设备操作须经值班调度人员许可",
                "source_url": SWITCHING_RULE_URL,
                "external_clause_locator": "第二十条",
                "provenance_type": "regulatory_principle_plus_dataset_procedure",
                "source_kind": "formal_clause_plus_dataset_procedure",
                "issuing_body": "State Council of the People's Republic of China; National Energy Administration",
                "document_id": "国务院令第115号; 国能发监管规〔2021〕60号",
                "document_version": "2021 current grid-connection operation regulation",
                "normative_sources": [
                    {
                        "document_id": "国务院令第115号",
                        "title": "电网调度管理条例",
                        "clause": "第二十条",
                        "url": SWITCHING_RULE_URL,
                        "relationship": "supports explicit dispatch permission before operating dispatch-controlled equipment",
                    },
                    {
                        "document_id": "国能发监管规〔2021〕60号",
                        "title": "电力并网运行管理规定",
                        "clause": "current regulation replacing the 2006 generator-grid connection regulation",
                        "url": "https://zfxxgk.nea.gov.cn/2021-12/21/c_1310391369.htm",
                        "relationship": "current version reference",
                    },
                ],
            }
        )
    elif "TOOL" in rule_id or "SECURITY_REDISPATCH" in rule_id:
        rule.update(
            {
                "source_title": "电网调度管理条例第十八、二十一、二十二条：安全处置与调度指令执行",
                "source_url": DISPATCH_RULE_URL,
                "external_clause_locator": "第十八条、第二十一条、第二十二条",
                "provenance_type": "regulatory_principle_plus_internal_tool_contract",
                "source_kind": "formal_clause_plus_internal_tool_contract",
                "issuing_body": "State Council of the People's Republic of China",
                "document_id": "国务院令第115号",
                "document_version": "official source page accessed 2026-07-10",
                "normative_sources": [
                    {
                        "document_id": "国务院令第115号",
                        "title": "电网调度管理条例",
                        "clause": "第十八条、第二十一条、第二十二条",
                        "url": DISPATCH_RULE_URL,
                        "relationship": "supports safe corrective dispatch and controlled execution; tool ordering remains a dataset contract",
                    }
                ],
            }
        )
    elif "QUERY" in rule_id:
        rule.update(
            {
                "source_title": "GridInstruct structured-query data contract",
                "source_url": "https://std.samr.gov.cn/gb/search/gbDetailed?id=F159DFC2A89547EFE05397BE0A0AF334",
                "external_clause_locator": "not statutory; internal structured-data contract",
                "provenance_type": "internal_data_contract",
                "source_kind": "internal_data_contract",
                "issuing_body": "GridInstruct dataset maintainers; Standardization Administration of China for context only",
                "document_id": "GRIDINSTRUCT-QUERY-CONTRACT-V3; GB/T 31464-2022",
                "document_version": "query-contract-v3",
                "normative_sources": [
                    {
                        "document_id": "GB/T 31464-2022",
                        "title": "电网运行准则",
                        "clause": "standard-level contextual reference",
                        "url": "https://std.samr.gov.cn/gb/search/gbDetailed?id=F159DFC2A89547EFE05397BE0A0AF334",
                        "relationship": "context for grid-operation information; filter and aggregation syntax are defined by the dataset",
                    }
                ],
            }
        )
    rule["internal_clause_id"] = rule.get("clause_id")
    if rule_id in {"REG_OVERLOAD_001", "REG_VOLTAGE_001", "REG_SWITCHING_001", "REG_TOOL_001", "REG_QUERY_001"}:
        applicable = list(rule.get("applicable_tasks") or [])
        if "regulation_qa" not in applicable:
            applicable.append("regulation_qa")
        if rule_id in {"REG_OVERLOAD_001", "REG_VOLTAGE_001"} and "intelligent_data_query" not in applicable:
            applicable.append("intelligent_data_query")
        rule["applicable_tasks"] = applicable
    if rule_id == "REG_TOOL_POSTCHECK_004":
        applicable = list(rule.get("applicable_tasks") or [])
        if "regulation_compliance_check" not in applicable:
            applicable.append("regulation_compliance_check")
        rule["applicable_tasks"] = applicable
    rule["evidence_fields"] = RULE_EVIDENCE_FIELDS[rule_id]
    rule["access_date"] = ACCESS_DATE
    source_payload = {
        "rule_id": rule_id,
        "source_kind": rule.get("source_kind"),
        "issuing_body": rule.get("issuing_body"),
        "document_id": rule.get("document_id"),
        "document_version": rule.get("document_version"),
        "external_clause_locator": rule.get("external_clause_locator"),
        "source_url": rule.get("source_url"),
        "normative_sources": rule.get("normative_sources"),
        "access_date": ACCESS_DATE,
    }
    rule["source_metadata_sha256"] = hashlib.sha256(
        json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    rule["linkage_stage"] = "pre_target_input_evidence"
    rule["provenance_schema_version"] = "2.0"
    return rule


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_jsonl(path, rows)


def normalize_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()


def add_rule(existing: list[str], rule_id: str) -> None:
    if rule_id not in existing:
        existing.append(rule_id)


def resolve_evidence(row: dict[str, Any], dotted_path: str) -> Any:
    value: Any = row
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def infer_secondary_rules(row: dict[str, Any]) -> tuple[list[str], dict[str, list[dict[str, str]]]]:
    """Link rules from pre-target instruction/input evidence only."""
    task = row.get("task_type")
    existing_links = list(row.get("source_regulation_ids") or [])
    base_rule_ids = {"REG_OVERLOAD_001", "REG_VOLTAGE_001", "REG_SWITCHING_001", "REG_TOOL_001", "REG_QUERY_001"}
    links: list[str] = []
    for rule_id in existing_links:
        if rule_id in base_rule_ids:
            add_rule(links, rule_id)
    input_obj = row.get("input") if isinstance(row.get("input"), dict) else {}
    text = normalize_text({"instruction": row.get("instruction"), "input": input_obj})
    basis: dict[str, list[dict[str, str]]] = defaultdict(list)

    def linked(rule_id: str, *fields: str) -> None:
        evidence = []
        for field in fields:
            value = resolve_evidence(row, field)
            if value in (None, "", [], {}):
                continue
            evidence.append(
                {
                    "json_pointer": "/" + field.replace(".", "/"),
                    "normalized_evidence": normalize_text(value),
                }
            )
        if not evidence:
            return
        add_rule(links, rule_id)
        existing = {item["json_pointer"] for item in basis[rule_id]}
        basis[rule_id].extend(item for item in evidence if item["json_pointer"] not in existing)

    for base_rule_id in list(links):
        linked(base_rule_id, "instruction", "input")

    if task in {"regulation_compliance_check", "auxiliary_decision"}:
        issues = normalize_text(input_obj.get("observed_issues") or input_obj.get("grid_state_summary") or "")
        if any(token in issues for token in ("overload", "loading", "过载", "负载率")):
            linked("REG_OVERLOAD_ALERT_002", "input.observed_issues", "input.grid_state_summary")
            if any(token in issues for token in ("severe", "emergency", "严重", "紧急", "110")):
                linked("REG_OVERLOAD_EMERGENCY_003", "input.observed_issues", "input.grid_state_summary")
        if any(token in issues for token in ("low voltage", "undervoltage", "低电压", "电压偏低")):
            linked("REG_VOLTAGE_LOW_002", "input.observed_issues", "input.grid_state_summary")
        if any(token in issues for token in ("high voltage", "overvoltage", "高电压", "电压偏高")):
            linked("REG_VOLTAGE_HIGH_003", "input.observed_issues", "input.grid_state_summary")
        if "generator_redispatch" in normalize_text(input_obj.get("available_actions")):
            linked("REG_SECURITY_REDISPATCH_004", "input.available_actions")
        if input_obj.get("decision_window") or any(token in text for token in ("post-action", "recheck", "复核", "复算")):
            linked("REG_TOOL_POSTCHECK_004", "input.decision_window", "instruction")

    if task == "operation_ticket_check":
        if input_obj.get("dispatch_permit_status") is not None:
            linked("REG_SWITCHING_PERMISSION_002", "input.dispatch_permit_status")
        if input_obj.get("monitoring_arrangement") is not None:
            linked("REG_SWITCHING_MONITORING_003", "input.monitoring_arrangement")

    if task == "dispatcher_intent_tool_call":
        utterance = normalize_text(input_obj.get("utterance") or row.get("instruction") or "")
        if any(token in utterance for token in ("diagnos", "violation", "check", "查询", "诊断", "越限", "校核")):
            linked("REG_TOOL_DIAGNOSIS_002", "input.utterance")
        if any(token in utterance for token in ("mitigat", "redispatch", "correct", "处置", "再调度", "纠正", "消除")):
            linked("REG_TOOL_MITIGATION_003", "input.utterance")
            linked("REG_SECURITY_REDISPATCH_004", "input.utterance")
        if any(token in utterance for token in ("recalculate", "power flow", "confirm", "复算", "潮流", "确认")):
            linked("REG_TOOL_POSTCHECK_004", "input.utterance")

    if task == "intelligent_data_query":
        if input_obj.get("scenario_id") or input_obj.get("question"):
            linked("REG_QUERY_FILTER_002", "input.scenario_id", "input.question", "instruction")
        question = normalize_text(input_obj.get("question") or row.get("instruction") or "")
        if any(token in question for token in ("maximum", "list", "summary", "aggregate", "return", "最大", "列出", "汇总", "返回")):
            linked("REG_QUERY_AGGREGATION_003", "input.question", "instruction")

    return links, dict(basis)


def expand_rules(rules_path: Path) -> tuple[int, int]:
    rules = read_json(rules_path)
    rule_ids = [str(row.get("rule_id") or "") for row in rules]
    if any(not value for value in rule_ids) or len(rule_ids) != len(set(rule_ids)):
        raise ValueError("rulebook contains missing or duplicate rule IDs")
    by_id = {row["rule_id"]: modernize_rule_provenance(row) for row in rules}
    before = len(by_id)
    for rule in EXPANDED_RULES:
        by_id[rule["rule_id"]] = modernize_rule_provenance(rule)
    base_order = [row["rule_id"] for row in rules]
    expanded_order = base_order + [row["rule_id"] for row in EXPANDED_RULES if row["rule_id"] not in base_order]
    if len(expanded_order) != 17 or len(expanded_order) != len(set(expanded_order)):
        raise ValueError(f"expanded rulebook must contain exactly 17 unique rules; got {len(expanded_order)}")
    write_json(rules_path, [by_id[rule_id] for rule_id in expanded_order])
    return before, len(expanded_order)


def build_id_to_links(canonical_dataset: Path) -> dict[str, tuple[list[str], dict[str, list[dict[str, str]]]]]:
    mapping = {}
    for row in read_jsonl(canonical_dataset):
        mapping[row["id"]] = infer_secondary_rules(row)
    return mapping


def update_jsonl_file(
    path: Path,
    id_to_links: dict[str, tuple[list[str], dict[str, list[dict[str, str]]]]],
) -> dict[str, Any]:
    rows = read_jsonl(path)
    changed = 0
    with_rule_rows = 0
    for row in rows:
        if "id" not in row or "source_regulation_ids" not in row:
            continue
        inferred = id_to_links.get(row["id"])
        if not inferred:
            inferred = infer_secondary_rules(row)
        links, basis = inferred
        old = row.get("source_regulation_ids") or []
        metadata = row.setdefault("metadata", {})
        old_stage = metadata.get("rule_linkage_stage") if isinstance(metadata, dict) else None
        old_basis = metadata.get("rule_link_evidence_fields") if isinstance(metadata, dict) else None
        if links != old or old_stage != "pre_target_input_evidence" or old_basis != basis:
            row["source_regulation_ids"] = links
            if isinstance(metadata, dict):
                metadata["rule_taxonomy_version"] = "v1.2-sd-core-expanded"
                metadata["rule_linkage_stage"] = "pre_target_input_evidence"
                metadata["rule_link_evidence_fields"] = basis
            changed += 1
        if row.get("source_regulation_ids"):
            with_rule_rows += 1
    if changed:
        write_jsonl(path, rows)
    return {"path": str(path.relative_to(ROOT)), "records": len(rows), "changed_records": changed, "records_with_rule_links": with_rule_rows}


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Rule Taxonomy Expansion",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "## Summary",
        "",
        f"- Rule cards before: {report['rule_count_before']}",
        f"- Rule cards after: {report['rule_count_after']}",
        f"- Canonical records: {report['canonical_record_count']}",
        f"- Canonical records changed: {report['canonical_changed_records']}",
        "",
        "## New Rule Link Counts",
        "",
        "| rule_id | linked records |",
        "| --- | ---: |",
    ]
    for rule_id, count in sorted(report["new_rule_link_counts"].items()):
        lines.append(f"| {rule_id} | {count} |")
    lines.extend(["", "## Updated Files", "", "| path | records | changed records |", "| --- | ---: | ---: |"])
    for item in report["updated_files"]:
        lines.append(f"| {item['path']} | {item['records']} | {item['changed_records']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_clause_matrix(rules_path: Path, csv_path: Path, json_path: Path) -> None:
    rules = read_json(rules_path)
    rows = []
    for rule in rules:
        rows.append(
            {
                "rule_id": rule["rule_id"],
                "internal_clause_id": rule.get("internal_clause_id"),
                "rule_type": rule.get("rule_type"),
                "provenance_type": rule.get("provenance_type"),
                "external_clause_locator": rule.get("external_clause_locator"),
                "numeric_threshold_source": rule.get("numeric_threshold_source", "not_applicable"),
                "source_title": rule.get("source_title"),
                "source_url": rule.get("source_url"),
                "source_kind": rule.get("source_kind"),
                "issuing_body": rule.get("issuing_body"),
                "document_id": rule.get("document_id"),
                "document_version": rule.get("document_version"),
                "access_date": rule.get("access_date"),
                "source_metadata_sha256": rule.get("source_metadata_sha256"),
                "evidence_fields": "|".join(rule.get("evidence_fields") or []),
                "applicable_tasks": "|".join(rule.get("applicable_tasks") or []),
                "linkage_stage": rule.get("linkage_stage"),
            }
        )
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_json(
        json_path,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "pass" if len(rows) == 17 else "fail",
            "rule_count": len(rows),
            "rules": rules,
        },
    )


def validate_rule_links(rows: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    rule_ids = [str(rule.get("rule_id") or "") for rule in rules]
    if len(rule_ids) != 17 or len(rule_ids) != len(set(rule_ids)) or any(not value for value in rule_ids):
        errors.append({"reason": "rulebook_not_17_unique_rules"})
    by_id = {rule["rule_id"]: rule for rule in rules}
    record_ids = [str(row.get("id") or "") for row in rows]
    if any(not value for value in record_ids) or len(record_ids) != len(set(record_ids)):
        errors.append({"reason": "dataset_missing_or_duplicate_ids"})
    link_count = 0
    for row in rows:
        links = list(row.get("source_regulation_ids") or [])
        if len(links) != len(set(links)):
            errors.append({"id": row.get("id"), "reason": "duplicate_rule_links"})
        basis = (row.get("metadata") or {}).get("rule_link_evidence_fields") or {}
        for rule_id in links:
            link_count += 1
            rule = by_id.get(rule_id)
            if not rule:
                errors.append({"id": row.get("id"), "rule_id": rule_id, "reason": "unknown_rule_id"})
                continue
            if row.get("task_type") not in (rule.get("applicable_tasks") or []):
                errors.append({"id": row.get("id"), "rule_id": rule_id, "reason": "task_not_applicable"})
            evidence_rows = basis.get(rule_id) or []
            if not evidence_rows:
                errors.append({"id": row.get("id"), "rule_id": rule_id, "reason": "missing_link_basis"})
                continue
            for evidence in evidence_rows:
                pointer = str(evidence.get("json_pointer") or "")
                dotted = pointer.removeprefix("/").replace("/", ".")
                if dotted not in set(rule.get("evidence_fields") or []):
                    errors.append(
                        {
                            "id": row.get("id"),
                            "rule_id": rule_id,
                            "reason": "evidence_pointer_not_declared_by_rule_card",
                            "json_pointer": pointer,
                        }
                    )
                value = resolve_evidence(row, dotted) if dotted else None
                if value in (None, "", [], {}) or evidence.get("normalized_evidence") != normalize_text(value):
                    errors.append(
                        {
                            "id": row.get("id"),
                            "rule_id": rule_id,
                            "reason": "evidence_pointer_or_value_mismatch",
                            "json_pointer": pointer,
                        }
                    )
    required_provenance = (
        "source_kind",
        "issuing_body",
        "document_id",
        "document_version",
        "external_clause_locator",
        "access_date",
        "source_metadata_sha256",
    )
    provenance_errors = [
        rule["rule_id"]
        for rule in rules
        if not rule.get("applicable_tasks")
        or not rule.get("evidence_fields")
        or rule.get("linkage_stage") != "pre_target_input_evidence"
        or rule.get("provenance_schema_version") != "2.0"
        or any(not rule.get(key) for key in required_provenance)
    ]
    if provenance_errors:
        errors.append({"reason": "rule_provenance_schema_invalid", "rule_ids": provenance_errors})
    return {
        "status": "pass" if not errors and link_count > 0 else "fail",
        "link_count": link_count,
        "error_count": len(errors),
        "errors": errors[:100],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-canonical-dataset", default=None)
    parser.add_argument("--rules", default="rules/regulation_rules.json")
    parser.add_argument("--data-glob", default=None)
    parser.add_argument("--report-json", default="reports/rule_taxonomy_expansion_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/rule_taxonomy_expansion_v1.2_sd_core.md")
    parser.add_argument("--clause-matrix-csv", default="metadata/rule_clause_matrix.csv")
    parser.add_argument("--clause-matrix-json", default="metadata/rule_clause_matrix.json")
    args = parser.parse_args()
    if args.data_glob:
        raise ValueError("--data-glob is incompatible with transactional rule linking; run each immutable stage explicitly")

    rules_path = ROOT / args.rules
    canonical_input = ROOT / args.canonical_dataset
    canonical_output = ROOT / (args.output_canonical_dataset or args.canonical_dataset)
    ensure_dirs(ROOT / "tmp")
    with tempfile.TemporaryDirectory(prefix="rule-link-transaction-", dir=ROOT / "tmp") as transaction_dir:
        transaction_root = Path(transaction_dir)
        staged_rules = transaction_root / "regulation_rules.json"
        staged_output = transaction_root / "rule_linked.jsonl"
        staged_matrix_csv = transaction_root / "rule_clause_matrix.csv"
        staged_matrix_json = transaction_root / "rule_clause_matrix.json"
        staged_rules.write_bytes(rules_path.read_bytes())
        before, after = expand_rules(staged_rules)
        write_clause_matrix(staged_rules, staged_matrix_csv, staged_matrix_json)
        write_jsonl(staged_output, read_jsonl(canonical_input))
        id_to_links = build_id_to_links(canonical_input)
        updated = [update_jsonl_file(staged_output, id_to_links)]

        canonical_rows = read_jsonl(staged_output)
        rules = read_json(staged_rules)
        link_validation = validate_rule_links(canonical_rows, rules)
        clause_matrix = read_json(staged_matrix_json)
        new_rule_ids = {rule["rule_id"] for rule in EXPANDED_RULES}
        link_counts: Counter[str] = Counter()
        task_rule_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for row in canonical_rows:
            for rule_id in row.get("source_regulation_ids") or []:
                if rule_id in new_rule_ids:
                    link_counts[rule_id] += 1
                    task_rule_counts[row.get("task_type")][rule_id] += 1

        unlinked_new_rules = sorted(rule_id for rule_id in new_rule_ids if link_counts[rule_id] == 0)
        transaction_passed = (
            not unlinked_new_rules
            and link_validation["status"] == "pass"
            and clause_matrix.get("status") == "pass"
        )
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "pass" if transaction_passed else "fail",
            "canonical_dataset": args.canonical_dataset,
            "output_canonical_dataset": str(canonical_output.relative_to(ROOT)),
            "rule_count_before": before,
            "rule_count_after": after,
            "new_rule_count": after - before,
            "canonical_record_count": len(canonical_rows),
            "canonical_changed_records": updated[0]["changed_records"],
            "new_rule_link_counts": dict(sorted(link_counts.items())),
            "task_rule_matrix_for_new_rules": {task: dict(counter) for task, counter in sorted(task_rule_counts.items())},
            "unlinked_new_rules": unlinked_new_rules,
            "link_validation": link_validation,
            "clause_matrix_status": clause_matrix.get("status"),
            "transaction": {
                "committed": transaction_passed,
                "artifacts": {
                    str(canonical_output.relative_to(ROOT)): hashlib.sha256(staged_output.read_bytes()).hexdigest(),
                    args.rules: hashlib.sha256(staged_rules.read_bytes()).hexdigest(),
                    args.clause_matrix_csv: hashlib.sha256(staged_matrix_csv.read_bytes()).hexdigest(),
                    args.clause_matrix_json: hashlib.sha256(staged_matrix_json.read_bytes()).hexdigest(),
                },
            },
            "updated_files": [
                {
                    "path": str(canonical_output.relative_to(ROOT)),
                    "records": len(canonical_rows),
                    "changed_records": updated[0]["changed_records"],
                    "records_with_rule_links": updated[0]["records_with_rule_links"],
                }
            ],
            "clause_matrix_csv": args.clause_matrix_csv,
            "clause_matrix_json": args.clause_matrix_json,
        }
        if transaction_passed:
            for source, destination in (
                (staged_output, canonical_output),
                (staged_rules, rules_path),
                (staged_matrix_csv, ROOT / args.clause_matrix_csv),
                (staged_matrix_json, ROOT / args.clause_matrix_json),
            ):
                atomic_write_text(destination, source.read_text(encoding="utf-8"))

    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.report_json, report)
    write_markdown(report, ROOT / args.report_md)
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
