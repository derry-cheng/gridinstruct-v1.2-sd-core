"""Add hard operation-ticket examples for the monitoring boundary."""

from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl


LABEL_OUTPUT = {
    "non_compliant": "违规",
    "compliant_with_monitoring": "合规但需继续监视",
    "compliant": "合规",
}

MONITORING_MODES = [
    {
        "pending": "遥测与现场回执一致性",
        "evidence": "执行后第一个遥测刷新周期内核对远动值、现场回执和票面记录",
        "fallback": "若遥测与回执不一致，暂停后续步骤并重新申请调度确认",
        "window": 15,
    },
    {
        "pending": "保护信号稳定性",
        "evidence": "执行后连续两个采样周期确认保护信号无抖动、无异常告警",
        "fallback": "若保护信号异常，保持当前状态并通知保护专业复核",
        "window": 20,
    },
    {
        "pending": "负荷电流回落确认",
        "evidence": "执行后核对目标间隔电流、相邻间隔负荷和限额裕度",
        "fallback": "若电流未回落到许可范围，撤回后续操作并重新校核潮流",
        "window": 30,
    },
    {
        "pending": "远方/就地状态一致性",
        "evidence": "执行后比对监控后台、测控装置和现场位置指示",
        "fallback": "若三方状态不一致，闭锁关联操作并开展现场复核",
        "window": 10,
    },
    {
        "pending": "异常告警清零确认",
        "evidence": "执行后扫描监控告警窗，确认无新保护动作、闭锁或通信中断告警",
        "fallback": "若出现新增告警，维持当前运行方式并补充风险交底",
        "window": 25,
    },
    {
        "pending": "终结回执签收",
        "evidence": "执行后由监护人与调度员分别确认终结回执和操作记录归档",
        "fallback": "若回执无法签收，暂停票据终结并补录执行过程证据",
        "window": 40,
    },
]

ROUTINE_FOLLOWUP_MODES = [
    {
        "routine": "执行后例行告警扫描",
        "evidence": "执行前告警窗已清零，执行后扫描只作为值班记录归档",
        "fallback": "若出现新告警，按现场异常流程另行建单",
        "window": 15,
    },
    {
        "routine": "终结回执归档",
        "evidence": "监护人、执行人和调度员的签名已在执行前核齐，执行后只归档终结时间",
        "fallback": "若归档系统不可用，先保留纸质记录",
        "window": 30,
    },
    {
        "routine": "遥测曲线留存",
        "evidence": "远动遥测、现场位置和票面对象在执行前一致，执行后曲线留存仅用于审计追溯",
        "fallback": "若曲线缺测，由监控值班员补录说明",
        "window": 20,
    },
    {
        "routine": "保护动作记录复扫",
        "evidence": "保护压板和闭锁状态已确认，执行后复扫仅用于确认无新增动作记录",
        "fallback": "若复扫发现动作记录，按异常事件流程处理",
        "window": 25,
    },
]

NONCOMPLIANT_FOLLOWUP_ISSUES = [
    {
        "missing": "调度许可时间",
        "issue": "调度许可时间留空，仅写明执行后再补回执",
        "status": "许可时间缺失",
        "rationale": "调度许可未在执行前形成闭环，执行后复核不能替代前置许可。",
    },
    {
        "missing": "操作对象一致性",
        "issue": "票面对象与设备状态描述指向相邻间隔，仍安排执行后核对遥测",
        "status": "已确认",
        "rationale": "操作对象与状态描述不一致，执行后监测不能消除误操作风险。",
    },
    {
        "missing": "安全措施栏",
        "issue": "安全措施栏写为口头确认，计划执行后由监护人补签",
        "status": "口头许可",
        "rationale": "安全措施没有票面闭环，后续补签不能作为进入执行环节的依据。",
    },
    {
        "missing": "现场监护签名",
        "issue": "监护人签名缺失，票面只要求执行后补录监护记录",
        "status": "已确认",
        "rationale": "现场监护责任未在执行前确认，不能依赖执行后补录。",
    },
]


def extract_operation_object(row: dict[str, Any]) -> str:
    state = str((row.get("input") or {}).get("equipment_state") or "")
    return state.split("处于", 1)[0] if "处于" in state else "设备"


def operation_action_from_text(row: dict[str, Any]) -> str:
    text = str((row.get("input") or {}).get("ticket_text") or row.get("instruction") or "")
    for action in ["由冷备用转热备用", "倒至旁路运行", "恢复运行", "转检修", "停役", "投入"]:
        if action in text:
            return action
    return "操作"


def operation_group(row: dict[str, Any]) -> str:
    input_obj = row.get("input") or {}
    context = " ".join(str(input_obj.get("operation_context") or "").split())
    obj = extract_operation_object(row)
    return f"{context}|{obj}"


def base_operation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if row.get("task_type") != "operation_ticket_check":
            continue
        metadata = row.get("metadata") or {}
        if metadata.get("augmentation_type") == "operation_ticket_rulebook_counterfactual":
            continue
        output.append(row)
    return output


def monitoring_variant(source: dict[str, Any], variant_index: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    input_obj = copy.deepcopy(source.get("input") or {})
    obj = extract_operation_object(source)
    action = operation_action_from_text(source)
    context = str(input_obj.get("operation_context") or "运行操作")
    checks = list(input_obj.get("key_checks") or ["设备状态", "安全措施", "调度许可"])
    primary = checks[variant_index % len(checks)]
    secondary = checks[(variant_index + 1) % len(checks)]
    mode = MONITORING_MODES[variant_index % len(MONITORING_MODES)]
    permit_time = f"{(variant_index * 7 + 5) % 24:02d}:{(variant_index * 13 + 11) % 60:02d}"

    ticket_text = (
        f"边界操作票 {variant_index}: {context}下执行{obj}{action}。"
        f"票面已完成{primary}、{secondary}、操作对象复诵和调度许可时间 {permit_time} 回填；"
        f"{mode['pending']}的最终证据需在执行完成后按回执补齐，票面列明异常时的暂停条件。"
    )
    input_obj.update(
        {
            "ticket_text": ticket_text,
            "dispatch_permit_status": "已确认并回填",
            "monitoring_arrangement": "监护人在位，执行后补齐指定复核证据",
            "object_consistency": "一致",
            "post_execution_followup": {
                "pending_evidence": mode["pending"],
                "evidence_collection": mode["evidence"],
                "window_minutes": mode["window"],
                "fallback_action": mode["fallback"],
            },
        }
    )

    row["id"] = f"{source['id']}__plus13_monitoring_cf_{variant_index}"
    row["input"] = input_obj
    row["output"] = LABEL_OUTPUT["compliant_with_monitoring"]
    row["compliance_label"] = "compliant_with_monitoring"
    row["rationale"] = (
        f"票据的对象、许可和核心前置检查已闭环，但{mode['pending']}依赖执行后证据归档，"
        "因此可执行但必须保留后续跟踪条件。"
    )
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_operation_monitoring_counterfactuals.py",
            "augmentation_type": "operation_monitoring_boundary_counterfactual",
            "source_record_id": source["id"],
            "operation_group": operation_group(source),
            "counterfactual_label": "compliant_with_monitoring",
            "pair_id": f"{source['id']}__plus13_pair_{variant_index}",
            "variant_index": variant_index,
        }
    )
    row["metadata"] = metadata
    return row


def compliant_pair_variant(source: dict[str, Any], variant_index: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    input_obj = copy.deepcopy(source.get("input") or {})
    obj = extract_operation_object(source)
    action = operation_action_from_text(source)
    context = str(input_obj.get("operation_context") or "运行操作")
    checks = list(input_obj.get("key_checks") or ["设备状态", "安全措施", "调度许可"])
    primary = checks[variant_index % len(checks)]
    secondary = checks[(variant_index + 1) % len(checks)]
    tertiary = checks[(variant_index + 2) % len(checks)]
    permit_time = f"{(variant_index * 7 + 5) % 24:02d}:{(variant_index * 13 + 11) % 60:02d}"

    ticket_text = (
        f"边界操作票 {variant_index}: {context}下执行{obj}{action}。"
        f"票面已完成{primary}、{secondary}、{tertiary}、操作对象复诵和调度许可时间 {permit_time} 回填；"
        "执行前已有远动值、现场回执和监护签名三方证据，终结记录可直接归档。"
    )
    input_obj.update(
        {
            "ticket_text": ticket_text,
            "dispatch_permit_status": "已确认并回填",
            "monitoring_arrangement": "监护人在位且已复诵",
            "object_consistency": "一致",
            "post_execution_followup": {
                "pending_evidence": "无",
                "evidence_collection": "执行前证据已闭环，终结记录可直接归档",
                "window_minutes": 0,
                "fallback_action": "无需额外暂停条件",
            },
        }
    )

    row["id"] = f"{source['id']}__plus13_compliant_pair_{variant_index}"
    row["input"] = input_obj
    row["output"] = LABEL_OUTPUT["compliant"]
    row["compliance_label"] = "compliant"
    row["rationale"] = "票据在执行前完成对象、许可、监护和证据闭环，没有遗留执行后条件。"
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_operation_monitoring_counterfactuals.py",
            "augmentation_type": "operation_monitoring_boundary_counterfactual",
            "source_record_id": source["id"],
            "operation_group": operation_group(source),
            "counterfactual_label": "compliant",
            "pair_id": f"{source['id']}__plus13_pair_{variant_index}",
            "variant_index": variant_index,
        }
    )
    row["metadata"] = metadata
    return row


def routine_compliant_variant(source: dict[str, Any], variant_index: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    input_obj = copy.deepcopy(source.get("input") or {})
    obj = extract_operation_object(source)
    action = operation_action_from_text(source)
    context = str(input_obj.get("operation_context") or "运行操作")
    checks = list(input_obj.get("key_checks") or ["设备状态", "安全措施", "调度许可"])
    primary = checks[variant_index % len(checks)]
    secondary = checks[(variant_index + 1) % len(checks)]
    routine = ROUTINE_FOLLOWUP_MODES[variant_index % len(ROUTINE_FOLLOWUP_MODES)]
    permit_time = f"{(variant_index * 11 + 9) % 24:02d}:{(variant_index * 17 + 5) % 60:02d}"

    ticket_text = (
        f"边界操作票 {variant_index}: {context}下执行{obj}{action}。"
        f"票面已完成{primary}、{secondary}、操作对象复诵、监护签名和调度许可时间 {permit_time} 回填；"
        f"{routine['routine']}只作为执行后的常规记录，不是放行前的遗留条件。"
    )
    input_obj.update(
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

    row["id"] = f"{source['id']}__plus13_routine_compliant_decoy_{variant_index}"
    row["input"] = input_obj
    row["output"] = LABEL_OUTPUT["compliant"]
    row["compliance_label"] = "compliant"
    row["rationale"] = "执行后的扫描或归档属于常规留痕；票据在执行前已经完成对象、许可、监护和核心安全条件。"
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_operation_monitoring_counterfactuals.py",
            "augmentation_type": "operation_monitoring_boundary_counterfactual",
            "source_record_id": source["id"],
            "operation_group": operation_group(source),
            "counterfactual_label": "compliant",
            "counterfactual_role": "routine_followup_decoy",
            "pair_id": f"{source['id']}__plus13_decoy_pair_{variant_index}",
            "variant_index": variant_index,
        }
    )
    row["metadata"] = metadata
    return row


def noncompliant_followup_decoy_variant(source: dict[str, Any], variant_index: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    input_obj = copy.deepcopy(source.get("input") or {})
    obj = extract_operation_object(source)
    action = operation_action_from_text(source)
    context = str(input_obj.get("operation_context") or "运行操作")
    checks = list(input_obj.get("key_checks") or ["设备状态", "安全措施", "调度许可"])
    primary = checks[variant_index % len(checks)]
    issue = NONCOMPLIANT_FOLLOWUP_ISSUES[variant_index % len(NONCOMPLIANT_FOLLOWUP_ISSUES)]
    mode = MONITORING_MODES[(variant_index + 2) % len(MONITORING_MODES)]

    ticket_text = (
        f"边界操作票 {variant_index}: {context}下拟执行{obj}{action}。"
        f"票面记录了{primary}和执行后{mode['pending']}复核安排，"
        f"但{issue['issue']}。"
    )
    object_consistency = "票面对象与状态描述不一致" if "对象" in issue["missing"] else "一致"
    input_obj.update(
        {
            "ticket_text": ticket_text,
            "dispatch_permit_status": issue["status"],
            "monitoring_arrangement": "监护安排待执行后补录" if "监护" in issue["missing"] else "监护人在位",
            "object_consistency": object_consistency,
            "post_execution_followup": {
                "pending_evidence": mode["pending"],
                "evidence_collection": mode["evidence"],
                "window_minutes": mode["window"],
                "fallback_action": mode["fallback"],
            },
        }
    )

    row["id"] = f"{source['id']}__plus13_noncompliant_followup_decoy_{variant_index}"
    row["input"] = input_obj
    row["output"] = LABEL_OUTPUT["non_compliant"]
    row["compliance_label"] = "non_compliant"
    row["rationale"] = issue["rationale"]
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_operation_monitoring_counterfactuals.py",
            "augmentation_type": "operation_monitoring_boundary_counterfactual",
            "source_record_id": source["id"],
            "operation_group": operation_group(source),
            "counterfactual_label": "non_compliant",
            "counterfactual_role": "followup_language_negative_decoy",
            "pair_id": f"{source['id']}__plus13_decoy_pair_{variant_index}",
            "variant_index": variant_index,
        }
    )
    row["metadata"] = metadata
    return row


def augment(rows: list[dict[str, Any]], max_sources: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sources = base_operation_rows(rows)
    if max_sources is not None:
        sources = sources[:max_sources]

    added: list[dict[str, Any]] = []
    for idx, source in enumerate(sources):
        added.append(monitoring_variant(source, idx))
        added.append(compliant_pair_variant(source, idx))
        added.append(routine_compliant_variant(source, idx))
        added.append(noncompliant_followup_decoy_variant(source, idx))

    output = list(rows) + added
    before_labels = Counter(row.get("compliance_label") for row in rows if row.get("task_type") == "operation_ticket_check")
    added_labels = Counter(row.get("compliance_label") for row in added)
    after_labels = Counter(row.get("compliance_label") for row in output if row.get("task_type") == "operation_ticket_check")
    return output, {
        "input_records": len(rows),
        "output_records": len(output),
        "source_operation_records": len(sources),
        "added_records": len(added),
        "added_operation_records": len(added),
        "operation_label_counts_before": dict(before_labels),
        "operation_label_counts_added": dict(added_labels),
        "operation_label_counts_after": dict(after_labels),
        "monitoring_modes": [mode["pending"] for mode in MONITORING_MODES],
        "routine_followup_modes": [mode["routine"] for mode in ROUTINE_FOLLOWUP_MODES],
        "negative_decoy_issues": [issue["missing"] for issue in NONCOMPLIANT_FOLLOWUP_ISSUES],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus12.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus13.jsonl")
    parser.add_argument("--report-json", default="reports/operation_monitoring_boundary_augmentation_v1.2_plus13.json")
    parser.add_argument("--report-md", default="reports/operation_monitoring_boundary_augmentation_v1.2_plus13.md")
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
        "# Operation Monitoring Boundary Augmentation",
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
    lines.extend(["", "## Monitoring Boundary Modes", ""])
    for mode in summary["monitoring_modes"]:
        lines.append(f"- {mode}")
    lines.extend(["", "## Routine Follow-Up Decoys", ""])
    for mode in summary["routine_followup_modes"]:
        lines.append(f"- {mode}")
    lines.extend(["", "## Negative Follow-Up Decoys", ""])
    for issue in summary["negative_decoy_issues"]:
        lines.append(f"- {issue}")
    Path(ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
