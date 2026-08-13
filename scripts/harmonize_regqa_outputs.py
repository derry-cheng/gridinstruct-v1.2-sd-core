"""Create a schema-consistent regulation-QA snapshot.

The repair is grounded in explicit rule-card fields already stored in each
record input. It does not infer new labels, scenarios, or simulation facts.
"""

from __future__ import annotations

import argparse
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from generate_instruction_data import format_regqa_output
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl


def contains_all(text: str, values: list[Any]) -> bool:
    return all(str(value) in text for value in values if value not in (None, ""))


def regqa_alignment(rows: list[dict[str, Any]]) -> dict[str, Any]:
    regqa_rows = [row for row in rows if row.get("task_type") == "regulation_qa"]
    counters = {
        "mentions_rule_id": 0,
        "mentions_deliverable": 0,
        "mentions_audience": 0,
        "mentions_usage_context": 0,
        "mentions_focus_area": 0,
        "mentions_all_evidence_fields": 0,
        "mentions_all_applicable_tasks": 0,
        "mentions_secondary_rule_id": 0,
    }
    secondary_total = 0
    for row in regqa_rows:
        input_obj = row.get("input") if isinstance(row.get("input"), dict) else {}
        text = str(row.get("output", ""))
        for key, input_key in [
            ("mentions_rule_id", "rule_id"),
            ("mentions_deliverable", "deliverable"),
            ("mentions_audience", "audience"),
            ("mentions_usage_context", "usage_context"),
            ("mentions_focus_area", "focus_area"),
        ]:
            value = input_obj.get(input_key)
            counters[key] += int(bool(value) and str(value) in text)
        counters["mentions_all_evidence_fields"] += int(contains_all(text, input_obj.get("evidence_fields") or []))
        counters["mentions_all_applicable_tasks"] += int(contains_all(text, input_obj.get("applicable_tasks") or []))
        if input_obj.get("secondary_rule_id"):
            secondary_total += 1
            counters["mentions_secondary_rule_id"] += int(str(input_obj["secondary_rule_id"]) in text)
    denominator = len(regqa_rows) or 1
    rates = {key: value / denominator for key, value in counters.items() if key != "mentions_secondary_rule_id"}
    rates["mentions_secondary_rule_id"] = (
        counters["mentions_secondary_rule_id"] / secondary_total if secondary_total else 1.0
    )
    return {
        "records": len(regqa_rows),
        "secondary_records": secondary_total,
        "counts": counters,
        "rates": rates,
    }


def harmonized_output(row: dict[str, Any]) -> str | None:
    input_obj = row.get("input") if isinstance(row.get("input"), dict) else None
    if not input_obj:
        return None
    required = [
        "rule_id",
        "rule_summary",
        "evidence_fields",
        "applicable_tasks",
        "audience",
        "usage_context",
        "deliverable",
        "focus_area",
    ]
    if any(input_obj.get(key) in (None, "", []) for key in required):
        return None
    return format_regqa_output(
        rule_id=input_obj["rule_id"],
        summary=input_obj["rule_summary"],
        evidence_fields=list(input_obj["evidence_fields"]),
        applicable_tasks=list(input_obj["applicable_tasks"]),
        audience=input_obj["audience"],
        context=input_obj["usage_context"],
        deliverable=input_obj["deliverable"],
        focus_area=input_obj["focus_area"],
        secondary_rule_id=input_obj.get("secondary_rule_id"),
        secondary_summary=input_obj.get("secondary_rule_summary"),
        secondary_evidence_fields=list(input_obj.get("secondary_evidence_fields") or []),
        secondary_applicable_tasks=list(input_obj.get("secondary_applicable_tasks") or []),
    )


def should_harmonize(row: dict[str, Any], include_reviewed: bool) -> bool:
    if row.get("task_type") != "regulation_qa":
        return False
    if include_reviewed:
        return True
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return metadata.get("created_by") == "generate_instruction_data.py"


def write_markdown(path: str, payload: dict[str, Any]) -> None:
    before = payload["alignment_before"]["rates"]
    after = payload["alignment_after"]["rates"]
    lines = [
        "# Regulation-QA Target Harmonization",
        "",
        f"Generated: {payload['generated_at']}",
        f"Input: `{payload['input']}`",
        f"Output: `{payload['output']}`",
        "",
        "## Summary",
        "",
        f"- Records: {payload['records']}",
        f"- Regulation-QA records: {payload['regqa_records']}",
        f"- Harmonized records: {payload['harmonized_records']}",
        f"- Skipped reviewed records: {payload['skipped_reviewed_records']}",
        f"- Skipped incomplete records: {payload['skipped_incomplete_records']}",
        "",
        "## Alignment Rates",
        "",
        "| check | before | after |",
        "| --- | ---: | ---: |",
    ]
    for key in sorted(after):
        lines.append(f"| {key} | {before.get(key, 0.0):.4f} | {after.get(key, 0.0):.4f} |")
    lines.extend(
        [
            "",
            "## Scope",
            "",
            (
                "Only regulation-QA natural-language targets are harmonized, and only from fields already present in "
                "`input`; labels, tool plans, scenarios, simulation outputs, source regulation identifiers, and non-regQA "
                "records are unchanged."
            ),
        ]
    )
    output_path = ROOT / path
    ensure_dirs(output_path.parent)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus7.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus8.jsonl")
    parser.add_argument("--report-json", default="reports/regqa_target_harmonization_v1.2_plus8.json")
    parser.add_argument("--report-md", default="reports/regqa_target_harmonization_v1.2_plus8.md")
    parser.add_argument("--include-reviewed", action="store_true")
    args = parser.parse_args()

    original_rows = read_jsonl(ROOT / args.input)
    rows = deepcopy(original_rows)
    harmonized = 0
    skipped_reviewed = 0
    skipped_incomplete = 0
    for row in rows:
        if row.get("task_type") != "regulation_qa":
            continue
        if not should_harmonize(row, args.include_reviewed):
            skipped_reviewed += 1
            continue
        new_output = harmonized_output(row)
        if new_output is None:
            skipped_incomplete += 1
            continue
        old_output = str(row.get("output", ""))
        if old_output != new_output:
            metadata = row.setdefault("metadata", {})
            metadata["regqa_target_alignment"] = "schema_harmonized"
            metadata["harmonized_by"] = "harmonize_regqa_outputs.py"
            metadata["previous_output_sha256"] = hashlib.sha256(old_output.encode("utf-8")).hexdigest()
            row["output"] = new_output
            row["rationale"] = (
                f"回答依据规则卡片和显式交付物字段构造，主规则为 {row['input']['rule_id']}；"
                f"重点围绕 {', '.join(row['input']['evidence_fields'])} 与 {row['input']['focus_area']} 展开。"
            )
            harmonized += 1

    write_jsonl(ROOT / args.output, rows)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "include_reviewed": args.include_reviewed,
        "records": len(rows),
        "regqa_records": sum(1 for row in rows if row.get("task_type") == "regulation_qa"),
        "harmonized_records": harmonized,
        "skipped_reviewed_records": skipped_reviewed,
        "skipped_incomplete_records": skipped_incomplete,
        "alignment_before": regqa_alignment(original_rows),
        "alignment_after": regqa_alignment(rows),
    }
    write_json(ROOT / args.report_json, payload)
    write_markdown(args.report_md, payload)


if __name__ == "__main__":
    main()
