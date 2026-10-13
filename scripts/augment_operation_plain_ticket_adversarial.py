"""Add plain-language adversarial operation-ticket records.

The plus13 monitoring boundary examples still contain wording that separates
labels too cleanly. This augmentation adds realistic ticket-style records where
all labels share post-execution review language; labels differ by whether the
pre-execution conditions are closed, monitoring is mandatory after execution,
or a blocking precondition remains open.
"""

from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from augment_operation_monitoring_counterfactuals import (
    LABEL_OUTPUT,
    MONITORING_MODES,
    ROUTINE_FOLLOWUP_MODES,
    extract_operation_object,
    operation_action_from_text,
    operation_group,
)
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl


EQUIPMENT_OBJECTS = [
    "线路开关",
    "母线刀闸",
    "主变开关",
    "旁路开关",
    "电容器开关",
]

NEGATIVE_ISSUES = [
    {
        "key": "dispatch_permit_number",
        "field": "调度许可编号",
        "description": "调度许可编号栏为待回填，许可时间只写班组口头确认",
        "status": "许可编号缺失",
        "object_consistency": "一致",
        "rationale": "调度许可编号没有在执行前闭环，后续记录不能替代进入执行环节的许可依据。",
    },
    {
        "key": "safety_measure_signature",
        "field": "安全措施确认签名",
        "description": "安全措施确认签名栏为空，仅在备注中写现场已口头交底",
        "status": "已确认并回填",
        "object_consistency": "一致",
        "rationale": "安全措施确认缺少票面签名闭环，不能只凭口头交底进入执行环节。",
    },
    {
        "key": "monitor_signature",
        "field": "监护人签名",
        "description": "监护人签名栏为空，票尾备注由执行人代记监护人到场",
        "status": "已确认并回填",
        "object_consistency": "一致",
        "rationale": "监护责任没有在执行前由票面签名确认，执行人代记不能替代监护闭环。",
    },
    {
        "key": "object_consistency",
        "field": "操作对象一致性",
        "description": "票头对象与设备状态栏指向不同间隔，操作步骤仍沿用原票编号",
        "status": "已确认并回填",
        "object_consistency": "票头对象与设备状态栏不一致",
        "rationale": "操作对象不一致是执行前阻断项，执行后复核不能消除误操作风险。",
    },
]


def base_operation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if row.get("task_type") != "operation_ticket_check":
            continue
        metadata = row.get("metadata") or {}
        if metadata.get("augmentation_type"):
            continue
        output.append(row)
    return output


def alternate_object(obj: str, variant_index: int) -> str:
    choices = [item for item in EQUIPMENT_OBJECTS if item != obj]
    if not choices:
        return "相邻间隔开关"
    return choices[variant_index % len(choices)]


def checked_fields(source: dict[str, Any], variant_index: int) -> tuple[str, str, str]:
    checks = list((source.get("input") or {}).get("key_checks") or ["设备状态", "安全措施", "调度许可"])
    return (
        checks[variant_index % len(checks)],
        checks[(variant_index + 1) % len(checks)],
        checks[(variant_index + 2) % len(checks)],
    )


def permit_time(variant_index: int) -> str:
    return f"{(variant_index * 5 + 3) % 24:02d}:{(variant_index * 19 + 7) % 60:02d}"


def permit_record(ctx: dict[str, str], variant_index: int, issue: dict[str, str] | None = None) -> str:
    if issue and issue["key"] == "dispatch_permit_number":
        return "许可记录: 调度许可编号待回填，许可时间为班组口头确认"
    return f"许可记录: 调度许可编号 T{variant_index:05d}，许可时间 {ctx['permit_time']}"


def common_context(source: dict[str, Any], variant_index: int) -> dict[str, str]:
    input_obj = source.get("input") or {}
    obj = extract_operation_object(source)
    action = operation_action_from_text(source)
    context = str(input_obj.get("operation_context") or "运行操作")
    primary, secondary, tertiary = checked_fields(source, variant_index)
    return {
        "obj": obj,
        "alt_obj": alternate_object(obj, variant_index),
        "action": action,
        "context": context,
        "primary": primary,
        "secondary": secondary,
        "tertiary": tertiary,
        "permit_time": permit_time(variant_index),
    }


def update_metadata(
    source: dict[str, Any],
    variant_index: int,
    label: str,
    role: str,
    boundary_key: str,
    issue: str | None = None,
) -> dict[str, Any]:
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_operation_plain_ticket_adversarial.py",
            "augmentation_type": "operation_plain_ticket_adversarial",
            "source_record_id": source["id"],
            "operation_group": operation_group(source),
            "counterfactual_label": label,
            "counterfactual_role": role,
            "plain_ticket_boundary_key": boundary_key,
            "pair_id": f"{source['id']}__plus14_plain_pair_{variant_index}",
            "variant_index": variant_index,
        }
    )
    if issue:
        metadata["adversarial_issue"] = issue
    return metadata


def plain_compliant_routine(source: dict[str, Any], variant_index: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    payload = copy.deepcopy(source.get("input") or {})
    ctx = common_context(source, variant_index)
    routine = ROUTINE_FOLLOWUP_MODES[variant_index % len(ROUTINE_FOLLOWUP_MODES)]
    ticket_text = (
        f"票据摘录 {variant_index}: {ctx['context']}，{ctx['obj']}{ctx['action']}。"
        f"执行前栏: {ctx['primary']}已核对，{ctx['secondary']}已核对，{ctx['tertiary']}已核对；"
        f"{permit_record(ctx, variant_index)}；"
        f"人员记录: 执行人、监护人均签名，操作对象复诵为{ctx['obj']}。"
        f"票尾记录: {routine['routine']}，异常另建缺陷单。"
    )
    payload.update(
        {
            "ticket_text": ticket_text,
            "dispatch_permit_status": "已确认并回填",
            "monitoring_arrangement": "监护人在位且前置复诵完成",
            "object_consistency": "一致",
            "post_execution_followup": {
                "pending_evidence": routine["routine"],
                "evidence_collection": routine["evidence"],
                "window_minutes": routine["window"],
                "fallback_action": routine["fallback"],
            },
        }
    )
    row.update(
        {
            "id": f"{source['id']}__plus14_plain_compliant_{variant_index}",
            "input": payload,
            "output": LABEL_OUTPUT["compliant"],
            "compliance_label": "compliant",
            "rationale": "执行前的许可、对象、监护和关键检查已经闭环；执行后复核属于常规留痕。",
            "metadata": update_metadata(
                source,
                variant_index,
                "compliant",
                "plain_compliant_routine",
                f"plain_routine:{routine['routine']}",
            ),
        }
    )
    return row


def plain_monitoring_required(source: dict[str, Any], variant_index: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    payload = copy.deepcopy(source.get("input") or {})
    ctx = common_context(source, variant_index)
    mode = MONITORING_MODES[variant_index % len(MONITORING_MODES)]
    ticket_text = (
        f"票据摘录 {variant_index}: {ctx['context']}，{ctx['obj']}{ctx['action']}。"
        f"执行前栏: {ctx['primary']}已核对，{ctx['secondary']}已核对，{ctx['tertiary']}已核对；"
        f"{permit_record(ctx, variant_index)}；"
        f"人员记录: 执行人、监护人均签名，操作对象复诵为{ctx['obj']}。"
        f"票尾记录: 执行后{mode['window']}分钟内复核{mode['pending']}，"
        f"若复核异常，后续票据暂不终结并按调度要求处置。"
    )
    payload.update(
        {
            "ticket_text": ticket_text,
            "dispatch_permit_status": "已确认并回填",
            "monitoring_arrangement": "监护人在位，执行后必须完成指定复核",
            "object_consistency": "一致",
            "post_execution_followup": {
                "pending_evidence": mode["pending"],
                "evidence_collection": mode["evidence"],
                "window_minutes": mode["window"],
                "fallback_action": mode["fallback"],
            },
        }
    )
    row.update(
        {
            "id": f"{source['id']}__plus14_plain_monitoring_{variant_index}",
            "input": payload,
            "output": LABEL_OUTPUT["compliant_with_monitoring"],
            "compliance_label": "compliant_with_monitoring",
            "rationale": f"执行前条件已闭环，但{mode['pending']}被票面列为执行后的强制复核条件。",
            "metadata": update_metadata(
                source,
                variant_index,
                "compliant_with_monitoring",
                "plain_monitoring_required",
                f"plain_monitoring:{mode['pending']}",
            ),
        }
    )
    return row


def plain_noncompliant_precondition(source: dict[str, Any], variant_index: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    payload = copy.deepcopy(source.get("input") or {})
    ctx = common_context(source, variant_index)
    mode = MONITORING_MODES[(variant_index + 2) % len(MONITORING_MODES)]
    issue = NEGATIVE_ISSUES[variant_index % len(NEGATIVE_ISSUES)]
    object_text = ctx["alt_obj"] if issue["key"] == "object_consistency" else ctx["obj"]
    ticket_text = (
        f"票据摘录 {variant_index}: {ctx['context']}，{ctx['obj']}{ctx['action']}。"
        f"执行前栏: {ctx['primary']}已核对，{ctx['secondary']}已核对，{issue['description']}；"
        f"{permit_record(ctx, variant_index, issue)}；"
        f"人员记录: 执行人签名，操作对象复诵为{object_text}。"
        f"票尾记录: 执行后{mode['window']}分钟内复核{mode['pending']}，异常另建缺陷单。"
    )
    payload.update(
        {
            "ticket_text": ticket_text,
            "dispatch_permit_status": issue["status"],
            "monitoring_arrangement": "监护人在位" if issue["key"] != "monitor_signature" else "监护签名缺失",
            "object_consistency": issue["object_consistency"],
            "post_execution_followup": {
                "pending_evidence": mode["pending"],
                "evidence_collection": mode["evidence"],
                "window_minutes": mode["window"],
                "fallback_action": mode["fallback"],
            },
        }
    )
    row.update(
        {
            "id": f"{source['id']}__plus14_plain_noncompliant_{variant_index}",
            "input": payload,
            "output": LABEL_OUTPUT["non_compliant"],
            "compliance_label": "non_compliant",
            "rationale": issue["rationale"],
            "metadata": update_metadata(
                source,
                variant_index,
                "non_compliant",
                "plain_noncompliant_precondition",
                f"plain_negative:{issue['key']}",
                issue["key"],
            ),
        }
    )
    return row


def plain_noncompliant_same_words(source: dict[str, Any], variant_index: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    payload = copy.deepcopy(source.get("input") or {})
    ctx = common_context(source, variant_index)
    routine = ROUTINE_FOLLOWUP_MODES[(variant_index + 1) % len(ROUTINE_FOLLOWUP_MODES)]
    issue = NEGATIVE_ISSUES[(variant_index + 1) % len(NEGATIVE_ISSUES)]
    object_text = ctx["alt_obj"] if issue["key"] == "object_consistency" else ctx["obj"]
    ticket_text = (
        f"票据摘录 {variant_index}: {ctx['context']}，{ctx['obj']}{ctx['action']}。"
        f"执行前栏: {ctx['primary']}已核对，{ctx['secondary']}已核对，{issue['description']}；"
        f"{permit_record(ctx, variant_index, issue)}；"
        f"人员记录: 执行人、监护记录待核，操作对象复诵为{object_text}。"
        f"票尾记录: {routine['routine']}，异常另建缺陷单。"
    )
    payload.update(
        {
            "ticket_text": ticket_text,
            "dispatch_permit_status": issue["status"],
            "monitoring_arrangement": "监护人在位" if issue["key"] != "monitor_signature" else "监护签名缺失",
            "object_consistency": issue["object_consistency"],
            "post_execution_followup": {
                "pending_evidence": routine["routine"],
                "evidence_collection": routine["evidence"],
                "window_minutes": routine["window"],
                "fallback_action": routine["fallback"],
            },
        }
    )
    row.update(
        {
            "id": f"{source['id']}__plus14_plain_noncompliant_same_words_{variant_index}",
            "input": payload,
            "output": LABEL_OUTPUT["non_compliant"],
            "compliance_label": "non_compliant",
            "rationale": issue["rationale"],
            "metadata": update_metadata(
                source,
                variant_index,
                "non_compliant",
                "plain_noncompliant_same_words",
                f"plain_negative:{issue['key']}",
                issue["key"],
            ),
        }
    )
    return row


def augment(rows: list[dict[str, Any]], max_sources: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sources = base_operation_rows(rows)
    if max_sources is not None:
        sources = sources[:max_sources]

    added = []
    for idx, source in enumerate(sources):
        added.append(plain_compliant_routine(source, idx))
        added.append(plain_monitoring_required(source, idx))
        added.append(plain_noncompliant_precondition(source, idx))
        added.append(plain_noncompliant_same_words(source, idx))

    output = list(rows) + added
    before_labels = Counter(row.get("compliance_label") for row in rows if row.get("task_type") == "operation_ticket_check")
    added_labels = Counter(row.get("compliance_label") for row in added)
    after_labels = Counter(row.get("compliance_label") for row in output if row.get("task_type") == "operation_ticket_check")
    return output, {
        "input_records": len(rows),
        "output_records": len(output),
        "source_operation_records": len(sources),
        "added_records": len(added),
        "operation_label_counts_before": dict(before_labels),
        "operation_label_counts_added": dict(added_labels),
        "operation_label_counts_after": dict(after_labels),
        "monitoring_modes": [mode["pending"] for mode in MONITORING_MODES],
        "routine_followup_modes": [mode["routine"] for mode in ROUTINE_FOLLOWUP_MODES],
        "negative_issues": [issue["key"] for issue in NEGATIVE_ISSUES],
        "design_note": (
            "Plain ticket records share post-execution review wording across labels; "
            "the label is determined by pre-execution closure and mandatory monitoring facts."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus13.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus14.jsonl")
    parser.add_argument("--report-json", default="reports/operation_plain_ticket_adversarial_augmentation_v1.2_plus14.json")
    parser.add_argument("--report-md", default="reports/operation_plain_ticket_adversarial_augmentation_v1.2_plus14.md")
    parser.add_argument("--max-sources", type=int, default=None)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    output, summary = augment(rows, args.max_sources)
    ensure_dirs((ROOT / args.output).parent, (ROOT / args.report_json).parent)
    write_jsonl(ROOT / args.output, output)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        **summary,
        "passed": True,
    }
    write_json(ROOT / args.report_json, report)

    lines = [
        "# Operation Plain-Ticket Adversarial Augmentation",
        "",
        f"- Input: `{args.input}`",
        f"- Output: `{args.output}`",
        f"- Source operation records: {summary['source_operation_records']}",
        f"- Added records: {summary['added_records']}",
        f"- Output records: {summary['output_records']}",
        "",
        "## Added Label Counts",
        "",
    ]
    for label, count in sorted(summary["operation_label_counts_added"].items()):
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Boundary Families", ""])
    lines.append("- plain_routine: compliant records with routine post-execution audit language")
    lines.append("- plain_monitoring: compliant records requiring mandatory post-execution monitoring")
    lines.append("- plain_negative: non-compliant records sharing the same post-execution wording")
    lines.extend(["", "## Negative Issue Types", ""])
    for issue in summary["negative_issues"]:
        lines.append(f"- {issue}")
    Path(ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
