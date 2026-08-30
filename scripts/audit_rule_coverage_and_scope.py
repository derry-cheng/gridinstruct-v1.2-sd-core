#!/usr/bin/env python3
"""Audit rule-card coverage and scope for the GridInstruct release."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                import json

                yield json.loads(line)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Rule Coverage and Scope Audit",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        f"- Rule cards: {report['rule_count']}",
        f"- Records with rule links: {report['records_with_rule_links']}",
        f"- Invalid rule links: {report['invalid_rule_link_count']}",
        "",
        "## Rule Cards",
        "",
        "| Rule | Type | Source URL | Applicable tasks | Record links |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for row in report["rules"]:
        lines.append(
            f"| {row['rule_id']} | {row['rule_type']} | {row['has_source_url']} | {', '.join(row['applicable_tasks'])} | {row['record_link_count']} |"
        )
    lines.extend(
        [
            "",
            "## Scope Statement",
            "",
            report["scope_statement"],
            "",
            "## Source-kind coverage",
            "",
            "Record counts in this table are non-exclusive because one record can link to more than one rule card.",
            "",
            "| Source kind | Rule cards | Unique records | Rule links |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in report["source_kind_summary"]:
        lines.append(
            f"| {row['source_kind']} | {row['rule_card_count']} | "
            f"{row['unique_record_count']} | {row['record_link_count']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--rules", default="rules/regulation_rules.json")
    parser.add_argument("--report-json", default="reports/rule_coverage_scope_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/rule_coverage_scope_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    rules = read_json(ROOT / args.rules)
    rule_ids = {row["rule_id"] for row in rules}
    by_rule: Counter[str] = Counter()
    by_task_rule: dict[str, Counter[str]] = defaultdict(Counter)
    rule_by_id = {row["rule_id"]: row for row in rules}
    source_kind_links: Counter[str] = Counter()
    source_kind_record_ids: dict[str, set[str]] = defaultdict(set)
    source_kind_rule_ids: dict[str, set[str]] = defaultdict(set)
    invalid_links = []
    records_with_rule_links = 0
    total_records = 0

    for line_no, row in enumerate(iter_jsonl(ROOT / args.dataset), start=1):
        total_records += 1
        links = row.get("source_regulation_ids") or []
        if links:
            records_with_rule_links += 1
        for rule_id in links:
            if rule_id not in rule_ids:
                invalid_links.append({"line": line_no, "id": row.get("id"), "rule_id": rule_id})
                continue
            by_rule[rule_id] += 1
            by_task_rule[row.get("task_type")][rule_id] += 1
            source_kind = str(rule_by_id[rule_id].get("source_kind") or "unspecified")
            source_kind_links[source_kind] += 1
            source_kind_record_ids[source_kind].add(str(row.get("id") or f"line-{line_no}"))
            source_kind_rule_ids[source_kind].add(rule_id)

    rule_rows = []
    missing_fields = []
    for rule in rules:
        required_fields = ["rule_id", "rule_type", "summary", "standard_id", "source_title", "source_url", "evidence_fields", "applicable_tasks"]
        absent = [field for field in required_fields if not rule.get(field)]
        if absent:
            missing_fields.append({"rule_id": rule.get("rule_id"), "missing": absent})
        rule_rows.append(
            {
                "rule_id": rule.get("rule_id"),
                "rule_type": rule.get("rule_type"),
                "standard_id": rule.get("standard_id"),
                "source_title": rule.get("source_title"),
                "has_source_url": bool(rule.get("source_url")),
                "evidence_fields": rule.get("evidence_fields") or [],
                "applicable_tasks": rule.get("applicable_tasks") or [],
                "record_link_count": by_rule.get(rule.get("rule_id"), 0),
            }
        )

    unlinked_rules = [row["rule_id"] for row in rule_rows if row["record_link_count"] == 0]
    status = "pass" if not invalid_links and not missing_fields and not unlinked_rules else "fail"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "dataset": args.dataset,
        "rule_count": len(rules),
        "total_records": total_records,
        "records_with_rule_links": records_with_rule_links,
        "invalid_rule_link_count": len(invalid_links),
        "invalid_rule_links": invalid_links[:50],
        "missing_required_rule_fields": missing_fields,
        "unlinked_rules": unlinked_rules,
        "rules": rule_rows,
        "task_rule_matrix": {task: dict(counter) for task, counter in sorted(by_task_rule.items())},
        "source_kind_summary": [
            {
                "source_kind": source_kind,
                "rule_card_count": len(source_kind_rule_ids[source_kind]),
                "unique_record_count": len(source_kind_record_ids[source_kind]),
                "record_link_count": source_kind_links[source_kind],
            }
            for source_kind in sorted(source_kind_rule_ids)
        ],
        "scope_statement": (
            f"The release uses {len(rules)} traceable rule cards as modeling abstractions over public power-grid operating documents. "
            "This audit verifies link integrity and record-level coverage of those rule cards; it does not claim that GridInstruct is a complete regulatory corpus."
        ),
    }
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.report_json, report)
    write_markdown(report, ROOT / args.report_md)
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
