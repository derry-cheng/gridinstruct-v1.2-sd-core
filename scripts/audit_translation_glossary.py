#!/usr/bin/env python3
"""Audit English-derived release consistency and dispatch-term coverage."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, write_json


CJK_RE = re.compile(r"[\u3400-\u9fff]")
STABLE_FIELDS = (
    "id",
    "task_stage",
    "task_type",
    "network_model",
    "scenario_id",
    "source_regulation_ids",
    "source_simulation_case_id",
    "compliance_label",
    "intent",
    "structured_query",
    "query_result",
)
STABLE_SLOT_KEYS = ("priority", "scenario_id", "intent_family")
TEXT_FIELDS = ("instruction", "input", "output", "rationale", "chosen_response", "rejected_response", "preference_rationale")
GLOSSARY_GROUPS = {
    "operation_ticket": ["operation ticket", "switching ticket", "ticket"],
    "dispatch_and_review": ["dispatch", "review", "recheck", "verification", "verify"],
    "power_flow": ["power flow", "opf", "redispatch"],
    "security_constraints": ["overload", "voltage", "thermal", "constraint", "safety margin", "security margin"],
    "structured_query": ["structured query", "filter", "aggregation", "query result"],
    "tool_call": ["tool", "run_power_flow", "run_opf_redispatch", "query_grid_state"],
}


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def text_blob(row: dict[str, Any]) -> str:
    parts = []
    for field in TEXT_FIELDS:
        value = row.get(field)
        if value is not None:
            parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Translation Glossary Audit",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        f"- Source records: {report['source_record_count']}",
        f"- English records: {report['english_record_count']}",
        f"- English records with CJK text: {report['english_records_with_cjk']}",
        f"- Stable-field mismatches: {report['stable_field_mismatch_count']}",
        "",
        "## Glossary Coverage",
        "",
        "| Group | Hits | Matched terms |",
        "| --- | ---: | --- |",
    ]
    for group, row in report["glossary_coverage"].items():
        lines.append(f"| {group} | {row['hit_count']} | {', '.join(row['matched_terms'])} |")
    lines.extend(
        [
            "",
            "## Scope",
            "",
            report["scope_statement"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--english", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--report-json", default="reports/translation_glossary_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/translation_glossary_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    source_iter = iter_jsonl(ROOT / args.source)
    english_iter = iter_jsonl(ROOT / args.english)
    source_count = 0
    english_count = 0
    id_order_mismatches = []
    stable_mismatches = []
    cjk_examples = []
    glossary_hits: dict[str, set[str]] = {group: set() for group in GLOSSARY_GROUPS}
    glossary_counts = {group: 0 for group in GLOSSARY_GROUPS}

    while True:
        try:
            src = next(source_iter)
            source_count += 1
        except StopIteration:
            src = None
        try:
            en = next(english_iter)
            english_count += 1
        except StopIteration:
            en = None
        if src is None and en is None:
            break
        if src is None or en is None:
            id_order_mismatches.append({"source_id": src.get("id") if src else None, "english_id": en.get("id") if en else None})
            continue
        if src.get("id") != en.get("id"):
            id_order_mismatches.append({"source_id": src.get("id"), "english_id": en.get("id")})
        for field in STABLE_FIELDS:
            if src.get(field) != en.get(field):
                stable_mismatches.append({"id": en.get("id"), "field": field, "source": src.get(field), "english": en.get(field)})
        src_slots = src.get("slots") if isinstance(src.get("slots"), dict) else {}
        en_slots = en.get("slots") if isinstance(en.get("slots"), dict) else {}
        for key in STABLE_SLOT_KEYS:
            if src_slots.get(key) != en_slots.get(key):
                stable_mismatches.append(
                    {
                        "id": en.get("id"),
                        "field": f"slots.{key}",
                        "source": src_slots.get(key),
                        "english": en_slots.get(key),
                    }
                )
        blob = text_blob(en)
        lower_blob = blob.lower()
        if CJK_RE.search(blob):
            cjk_examples.append({"id": en.get("id"), "task_type": en.get("task_type")})
        for group, terms in GLOSSARY_GROUPS.items():
            matched = [term for term in terms if term.lower() in lower_blob]
            if matched:
                glossary_counts[group] += 1
                glossary_hits[group].update(matched)

    uncovered_groups = [group for group, terms in glossary_hits.items() if not terms]
    status = "pass" if not id_order_mismatches and not stable_mismatches and not cjk_examples and not uncovered_groups else "fail"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "source": args.source,
        "english": args.english,
        "source_record_count": source_count,
        "english_record_count": english_count,
        "id_order_mismatch_count": len(id_order_mismatches),
        "id_order_mismatches": id_order_mismatches[:50],
        "stable_field_mismatch_count": len(stable_mismatches),
        "stable_field_mismatches": stable_mismatches[:50],
        "english_records_with_cjk": len(cjk_examples),
        "cjk_examples": cjk_examples[:50],
        "uncovered_glossary_groups": uncovered_groups,
        "glossary_coverage": {
            group: {"hit_count": glossary_counts[group], "matched_terms": sorted(glossary_hits[group])}
            for group in sorted(GLOSSARY_GROUPS)
        },
        "scope_statement": (
            "This audit checks that English natural-language fields preserve stable identifiers and contain expected dispatch terminology. "
            "It is a deterministic consistency audit and does not replace bilingual expert adjudication."
        ),
    }
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.report_json, report)
    write_markdown(report, ROOT / args.report_md)
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
