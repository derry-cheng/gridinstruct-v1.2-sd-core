#!/usr/bin/env python3
"""Generate the jurisdictional rule-probe extension.

The core GridInstruct table remains unchanged. This extension provides a
small, directly generated English regulation-QA probe set whose cards are
bound to official NERC and EU system-operation sources. It is deliberately
separate from the domestic rule registry because the jurisdictions define
different responsibilities and operating-limit procedures.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCESS_DATE = "2026-08-18"
EXTENSION_VERSION = "international-rule-probe-v2"

AUDIENCES = (
    "duty dispatcher",
    "transmission operator",
    "reliability coordinator",
    "control-room engineer",
)
DELIVERABLES = (
    "a concise rule summary",
    "an evidence checklist",
    "an applicability note",
    "a verification procedure",
)
FOCI = (
    "the governing obligation",
    "the required evidence",
    "the operating state or limit",
    "the contingency or post-action check",
)


RULES: list[dict[str, Any]] = [
    {
        "rule_id": "INTL_NERC_TOP_001_6_ACTION",
        "jurisdiction": "NERC North America",
        "authority": "North American Electric Reliability Corporation",
        "standard_id": "TOP-001-6",
        "standard_title": "Transmission Operations",
        "clause_id": "R1-R4",
        "source_title": "NERC TOP-001-6 Transmission Operations",
        "source_url": "https://www.nerc.com/globalassets/standards/reliability-standards/top/top-001-6.pdf",
        "source_kind": "enforceable_reliability_standard",
        "summary": "Transmission operators maintain reliability through operating actions or instructions and communicate inability to comply with an instruction.",
        "evidence_fields": ["operating_instruction", "operator_log", "reliability_status"],
        "threshold_origin": "No universal numeric threshold is asserted; the obligation is linked to the applicable operating area and instruction record.",
    },
    {
        "rule_id": "INTL_NERC_TOP_002_5_PLAN",
        "jurisdiction": "NERC North America",
        "authority": "North American Electric Reliability Corporation",
        "standard_id": "TOP-002-5",
        "standard_title": "Operations Planning",
        "clause_id": "R1-R3",
        "source_title": "NERC TOP-002-5 Operations Planning",
        "source_url": "https://www.nerc.com/globalassets/standards/reliability-standards/top/top-002-5.pdf",
        "source_kind": "enforceable_reliability_standard",
        "summary": "Transmission operators perform next-day operational planning analysis, identify possible system operating limit exceedances, and maintain an operating plan for them.",
        "evidence_fields": ["operational_planning_analysis", "system_operating_limit", "operating_plan"],
        "threshold_origin": "System operating limits remain entity- and system-specific; this probe does not invent a numeric limit.",
    },
    {
        "rule_id": "INTL_NERC_FAC_011_4_SOL",
        "jurisdiction": "NERC North America",
        "authority": "North American Electric Reliability Corporation",
        "standard_id": "FAC-011-4",
        "standard_title": "System Operating Limits Methodology for the Operations Horizon",
        "clause_id": "R1-R4",
        "source_title": "NERC FAC-011-4 System Operating Limits Methodology",
        "source_url": "https://www.nerc.com/globalassets/standards/reliability-standards/fac/fac-011-4.pdf",
        "source_kind": "enforceable_reliability_standard",
        "summary": "A reliability coordinator documents the method for facility ratings, system voltage limits, and stability limits used in operations.",
        "evidence_fields": ["facility_rating", "system_voltage_limit", "stability_limit"],
        "threshold_origin": "Limits are determined by the documented methodology and facility-specific ratings; no universal numeric threshold is asserted.",
    },
    {
        "rule_id": "INTL_NERC_VAR_001_5_VOLTAGE",
        "jurisdiction": "NERC North America",
        "authority": "North American Electric Reliability Corporation",
        "standard_id": "VAR-001-5",
        "standard_title": "Voltage and Reactive Control",
        "clause_id": "Purpose; R1-R7",
        "source_title": "NERC VAR-001-5 Voltage and Reactive Control",
        "source_url": "https://www.nerc.com/standards/reliability-standards/var/var-001-5",
        "source_kind": "enforceable_reliability_standard",
        "summary": "Voltage levels, reactive flows, and reactive resources are monitored and controlled within applicable limits in real time.",
        "evidence_fields": ["voltage_schedule", "reactive_power_schedule", "reactive_resource_status"],
        "threshold_origin": "Voltage and reactive schedules are operationally specified; this probe does not convert them into a fixed dataset threshold.",
    },
    {
        "rule_id": "INTL_EU_SOGL_ART18_STATE",
        "jurisdiction": "European Union",
        "authority": "European Commission",
        "standard_id": "Commission Regulation (EU) 2017/1485",
        "standard_title": "Guideline on electricity transmission system operation",
        "clause_id": "Article 18",
        "source_title": "EU System Operation Guideline, Article 18: Classification of system states",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex:32017R1485",
        "source_kind": "eu_regulation",
        "summary": "A transmission system is classified as normal, alert, emergency, or blackout according to operating limits, reserves, frequency, and contingency consequences.",
        "evidence_fields": ["voltage", "power_flow", "frequency", "reserve", "contingency_status"],
        "threshold_origin": "The applicable ranges and triggers follow the regulation and the relevant TSO definitions; no domestic dataset threshold is reused.",
    },
    {
        "rule_id": "INTL_EU_SOGL_ART25_LIMITS",
        "jurisdiction": "European Union",
        "authority": "European Commission",
        "standard_id": "Commission Regulation (EU) 2017/1485",
        "standard_title": "Guideline on electricity transmission system operation",
        "clause_id": "Article 25",
        "source_title": "EU System Operation Guideline, Article 25: Operational security limits",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex:32017R1485",
        "source_kind": "eu_regulation",
        "summary": "Each transmission system operator specifies operational security limits using voltage, short-circuit, and thermal characteristics for each transmission element.",
        "evidence_fields": ["voltage_limit", "short_circuit_limit", "thermal_rating", "element_id"],
        "threshold_origin": "The limits are operator-defined within the regulation's physical requirements and are not replaced by a fixed 100/110 percent rule.",
    },
    {
        "rule_id": "INTL_EU_SOGL_ART33_CONTINGENCY",
        "jurisdiction": "European Union",
        "authority": "European Commission",
        "standard_id": "Commission Regulation (EU) 2017/1485",
        "standard_title": "Guideline on electricity transmission system operation",
        "clause_id": "Article 33",
        "source_title": "EU System Operation Guideline, Article 33: Contingency list",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex:32017R1485",
        "source_kind": "eu_regulation",
        "summary": "Each transmission system operator establishes and updates a contingency list that supports coordinated operational security analysis.",
        "evidence_fields": ["contingency_list", "external_contingency", "observability_area", "list_update"],
        "threshold_origin": "Contingency inclusion follows the regulation and the coordinated analysis methodology; no probability threshold is assumed in this probe.",
    },
    {
        "rule_id": "INTL_EU_SOGL_ART72_ANALYSIS",
        "jurisdiction": "European Union",
        "authority": "European Commission",
        "standard_id": "Commission Regulation (EU) 2017/1485",
        "standard_title": "Guideline on electricity transmission system operation",
        "clause_id": "Article 72(3)",
        "source_title": "EU System Operation Guideline, Article 72: Operational security analysis",
        "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex:32017R1485",
        "source_kind": "eu_regulation",
        "summary": "Operational security analysis simulates each listed contingency from the N state and verifies that N-1 operating security limits are not exceeded.",
        "evidence_fields": ["n_state", "n_minus_1_state", "contingency_list", "operational_security_limit"],
        "threshold_origin": "The pass condition is defined against the applicable operational security limits for the area and element under analysis.",
    },
]


def source_hash(rule: dict[str, Any], access_date: str) -> str:
    payload = {
        "rule_id": rule["rule_id"],
        "jurisdiction": rule["jurisdiction"],
        "standard_id": rule["standard_id"],
        "clause_id": rule["clause_id"],
        "source_url": rule["source_url"],
        "source_kind": rule["source_kind"],
        "access_date": access_date,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def materialize_rules(access_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in RULES:
        row = dict(raw)
        row.update(
            {
                "applicable_tasks": ["regulation_qa"],
                "access_date": access_date,
                "extension_version": EXTENSION_VERSION,
                "generation_scope": "jurisdictional_rule_probe_only",
                "source_metadata_sha256": source_hash(raw, access_date),
            }
        )
        rows.append(row)
    return rows


def make_record(rule: dict[str, Any], index: int, access_date: str) -> dict[str, Any]:
    audience = AUDIENCES[(index // 16) % len(AUDIENCES)]
    deliverable = DELIVERABLES[(index // 4) % len(DELIVERABLES)]
    focus = FOCI[index % len(FOCI)]
    rule_id = rule["rule_id"]
    instruction = (
        f"Explain rule {rule_id} under the {rule['jurisdiction']} profile for a {audience}. "
        f"Provide {deliverable}, focusing on {focus}."
    )
    question = (
        f"For a {audience}, explain the {rule['standard_id']} requirement in one concise answer. "
        f"Use the rule summary and address {focus}. Rule summary: {rule['summary']}"
    )
    answer_openers = {
        "a concise rule summary": "Operational meaning",
        "an evidence checklist": "Evidence required for review",
        "an applicability note": "Applicability",
        "a verification procedure": "Verification procedure",
    }
    focus_clauses = {
        "the governing obligation": "The governing obligation is",
        "the required evidence": "The review should retain",
        "the operating state or limit": "The operating-state condition is",
        "the contingency or post-action check": "The contingency or post-action check is",
    }
    opener = answer_openers[deliverable]
    focus_sentence = focus_clauses[focus]
    output = (
        f"{rule['standard_id']} ({rule['clause_id']}) — {opener}: {rule['summary']} "
        f"{focus_sentence} {focus}. Record {', '.join(rule['evidence_fields'])}. "
        f"{rule['threshold_origin']} The answer is scoped to {rule['jurisdiction']} and the cited source clause; "
        f"it is written for a {audience}."
    )
    rationale = (
        f"The answer is generated directly from the typed English rule contract for {rule_id}; "
        f"the source clause, jurisdiction, evidence fields, and threshold-origin statement are preserved."
    )
    return {
        "id": f"gridinstruct_{EXTENSION_VERSION.replace('-', '_')}_{index:04d}",
        "task_stage": "rule-only",
        "task_type": "regulation_qa",
        "network_model": None,
        "scenario_id": None,
        "instruction": instruction,
        "input": {
            "question": question,
            "rule_id": rule_id,
            "rule_summary": rule["summary"],
            "evidence_fields": rule["evidence_fields"],
            "applicable_tasks": rule["applicable_tasks"],
            "style_hint": "Answer in no more than three sentences.",
            "audience": audience,
            "usage_context": "jurisdictional rule grounding",
            "deliverable": deliverable,
            "focus_area": focus,
            "jurisdiction": rule["jurisdiction"],
            "standard_id": rule["standard_id"],
            "clause_id": rule["clause_id"],
            "source_url": rule["source_url"],
            "threshold_origin": rule["threshold_origin"],
            "answer_variant": f"audience={audience};deliverable={deliverable};focus={focus}",
        },
        "output": output,
        "target_contract": {
            "rule_id": rule_id,
            "jurisdiction": rule["jurisdiction"],
            "standard_id": rule["standard_id"],
            "clause_id": rule["clause_id"],
            "evidence_fields": list(rule["evidence_fields"]),
            "threshold_origin": rule["threshold_origin"],
        },
        "rationale": rationale,
        "source_regulation_ids": [rule_id],
        "source_simulation_case_id": None,
        "tool_plan": [],
        "metadata": {
            "difficulty": "jurisdictional_rule_grounding",
            "validation_status": "schema_validated_pending_expert_review",
            "created_by": "generate_international_rule_probe.py",
            "template_family": "international_regulation_qa",
            "language": "en",
            "generation_mode": "direct_english_from_typed_rule_contract",
            "jurisdiction": rule["jurisdiction"],
            "standard_id": rule["standard_id"],
            "clause_id": rule["clause_id"],
            "source_metadata_sha256": rule["source_metadata_sha256"],
            "access_date": access_date,
            "extension_version": EXTENSION_VERSION,
            "answer_variant": f"audience={audience};deliverable={deliverable};focus={focus}",
        },
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def write_matrix(path: Path, rules: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rule_id", "jurisdiction", "authority", "standard_id", "clause_id",
        "source_kind", "source_url", "threshold_origin", "applicable_tasks",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rule in rules:
            writer.writerow(
                {
                    field: ";".join(rule[field]) if field == "applicable_tasks" else rule.get(field, "")
                    for field in fields
                }
            )


def validate(rules: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    rule_ids = {rule["rule_id"] for rule in rules}
    errors: list[str] = []
    if len(rules) != 8:
        errors.append(f"expected 8 rules, got {len(rules)}")
    if len(records) != 512:
        errors.append(f"expected 512 records, got {len(records)}")
    if len({row["id"] for row in records}) != len(records):
        errors.append("duplicate record id")
    per_rule = {rule_id: 0 for rule_id in sorted(rule_ids)}
    outputs_per_rule: dict[str, set[str]] = {rule_id: set() for rule_id in sorted(rule_ids)}
    for row in records:
        linked = row.get("source_regulation_ids") or []
        if len(linked) != 1 or linked[0] not in rule_ids:
            errors.append(f"invalid rule link for {row.get('id')}")
        else:
            per_rule[linked[0]] += 1
            outputs_per_rule[linked[0]].add(str(row.get("output")))
        if row.get("task_type") != "regulation_qa":
            errors.append(f"wrong task for {row.get('id')}")
        if row.get("metadata", {}).get("generation_mode") != "direct_english_from_typed_rule_contract":
            errors.append(f"wrong generation mode for {row.get('id')}")
        if not row.get("instruction") or not row.get("output") or not row.get("rationale"):
            errors.append(f"empty language field for {row.get('id')}")
        target = row.get("target_contract") or {}
        if target.get("rule_id") != linked[0] or not target.get("evidence_fields"):
            errors.append(f"target contract mismatch for {row.get('id')}")
    if any(count != 64 for count in per_rule.values()):
        errors.append(f"per-rule count mismatch: {per_rule}")
    if any(len(values) < 32 for values in outputs_per_rule.values()):
        errors.append(f"insufficient answer diversity: { {key: len(value) for key, value in outputs_per_rule.items()} }")
    return {
        "status": "pass" if not errors else "fail",
        "extension_version": EXTENSION_VERSION,
        "rule_count": len(rules),
        "record_count": len(records),
        "records_per_rule": per_rule,
        "unique_outputs_per_rule": {key: len(value) for key, value in outputs_per_rule.items()},
        "unique_output_count": len({str(row.get("output")) for row in records}),
        "jurisdictions": {name: sum(1 for rule in rules if rule["jurisdiction"] == name) for name in sorted({r["jurisdiction"] for r in rules})},
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--access-date", default=DEFAULT_ACCESS_DATE)
    parser.add_argument("--rules", default="rules/international_rule_profiles.json")
    parser.add_argument("--matrix", default="metadata/international_rule_profile_matrix.csv")
    parser.add_argument("--data", default="data/international_rule_probe_v1.jsonl")
    parser.add_argument("--report-json", default="reports/international_rule_probe_v1.json")
    parser.add_argument("--report-md", default="reports/international_rule_probe_v1.md")
    args = parser.parse_args()
    rules = materialize_rules(args.access_date)
    records = [
        make_record(rule, rule_index * 64 + offset, args.access_date)
        for rule_index, rule in enumerate(rules)
        for offset in range(64)
    ]
    result = validate(rules, records)
    data_sha = write_jsonl(ROOT / args.data, records)
    (ROOT / args.rules).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / args.rules).write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_matrix(ROOT / args.matrix, rules)
    result.update({"generated_at": args.access_date, "data_sha256": data_sha, "data_path": args.data, "rules_path": args.rules, "matrix_path": args.matrix})
    (ROOT / args.report_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# International rule-probe extension",
        "",
        f"Status: `{result['status']}`",
        "",
        "This extension is separate from the 95,479-record domestic core. It contains directly generated English regulation-QA records linked to four NERC standards and four EU System Operation Guideline articles.",
        "",
        "| Jurisdiction | Rule cards | Records |",
        "| --- | ---: | ---: |",
    ]
    for jurisdiction, count in result["jurisdictions"].items():
        md.append(f"| {jurisdiction} | {count} | {count * 64} |")
    md.extend([
        "",
        f"The JSONL data SHA-256 is `{data_sha}`. No domestic numeric threshold is copied into the international cards; each card records whether limits are operator-defined or regulation-specific.",
        "",
        "The extension still requires independent expert review and a jurisdiction-held-out evaluation before any cross-jurisdiction generalization claim is made.",
    ])
    (ROOT / args.report_md).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
