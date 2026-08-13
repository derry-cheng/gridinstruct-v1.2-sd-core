"""Add rule-grounded hard examples for ticket checks and dispatcher routing."""

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

INTENT_ORDER = [
    "mitigate_violations",
    "diagnose_and_dispatch",
    "security_check_and_redispatch",
]

INTENT_OUTPUT_TEXT = {
    "mitigate_violations": "先按越限压降目标生成处置动作，再复算动作后潮流。",
    "diagnose_and_dispatch": "先定位异常来源，再形成可执行调度动作。",
    "security_check_and_redispatch": "先做安全校核，再输出再调度建议。",
}


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


def ticket_variant(
    source: dict[str, Any],
    label: str,
    variant_index: int,
    suffix: str,
) -> dict[str, Any]:
    row = copy.deepcopy(source)
    input_obj = copy.deepcopy(source.get("input") or {})
    obj = extract_operation_object(source)
    action = operation_action_from_text(source)
    context = str(input_obj.get("operation_context") or "运行操作")
    checks = list(input_obj.get("key_checks") or ["设备状态", "安全措施", "调度许可"])
    primary = checks[variant_index % len(checks)]
    secondary = checks[(variant_index + 1) % len(checks)]
    permit_time = f"{(variant_index * 5 + 3) % 24:02d}:{(variant_index * 11 + 7) % 60:02d}"

    if label == "compliant":
        ticket_text = (
            f"反事实操作票 {suffix}: {context}下执行{obj}{action}。"
            f"票面已完成{primary}、{secondary}、操作对象复诵和安全措施确认，"
            f"调度许可编号与时间 {permit_time} 均已回填，监护人与执行人签名齐全。"
        )
        input_obj.update(
            {
                "ticket_text": ticket_text,
                "dispatch_permit_status": "已确认并回填",
                "monitoring_arrangement": "监护人在位且已复诵",
                "object_consistency": "一致",
            }
        )
        rationale = f"该反事实票补齐{primary}、{secondary}、许可和监护闭环，具备执行条件。"
    elif label == "compliant_with_monitoring":
        ticket_text = (
            f"反事实操作票 {suffix}: {context}下执行{obj}{action}。"
            f"已核对{primary}、操作对象和调度许可，{secondary}已有现场确认记录但需在执行后复核归档，"
            f"票面要求监护人全过程复诵并在结束回执中补充复核结论。"
        )
        input_obj.update(
            {
                "ticket_text": ticket_text,
                "dispatch_permit_status": "已确认并回填",
                "monitoring_arrangement": "监护人在位且需全过程复诵",
                "object_consistency": "一致",
            }
        )
        rationale = f"核心许可和对象校核已闭环，但{secondary}需执行后持续复核，结论为合规但需继续监视。"
    else:
        issue_mode = variant_index % 4
        if issue_mode == 0:
            ticket_text = (
                f"反事实操作票 {suffix}: {context}下执行{obj}{action}。"
                f"票面记录了{secondary}和操作对象，但{primary}栏为空，仍计划直接执行。"
            )
            permit_status = "已申请但票面未回填"
            monitor_status = "监护人在位"
            object_consistency = "一致"
            rationale = f"{primary}缺少票面核对记录，关键前置校核不闭环。"
        elif issue_mode == 1:
            ticket_text = (
                f"反事实操作票 {suffix}: {context}下拟执行{obj}{action}。"
                f"票面对象写为{obj}，设备状态描述却指向相邻间隔，仅补记了{secondary}。"
            )
            permit_status = "已确认"
            monitor_status = "监护人在位"
            object_consistency = "票面对象与状态描述不一致"
            rationale = "操作对象与设备状态描述不一致，存在误操作风险。"
        elif issue_mode == 2:
            ticket_text = (
                f"反事实操作票 {suffix}: {context}下执行{obj}{action}。"
                f"已完成{primary}和{secondary}复诵，但调度许可时间留空，现场准备状态仍为待确认。"
            )
            permit_status = "许可时间缺失"
            monitor_status = "监护人在位"
            object_consistency = "一致"
            rationale = "调度许可未形成有效闭环记录，不能进入执行环节。"
        else:
            ticket_text = (
                f"反事实操作票 {suffix}: {context}下执行{obj}{action}。"
                f"票面注明先操作后补票，虽然记录了{primary}，但安全措施栏写为口头确认。"
            )
            permit_status = "口头许可"
            monitor_status = "监护签名缺失"
            object_consistency = "一致"
            rationale = "以口头确认替代票面安全措施和正式许可，不满足规范化倒闸要求。"
        input_obj.update(
            {
                "ticket_text": ticket_text,
                "dispatch_permit_status": permit_status,
                "monitoring_arrangement": monitor_status,
                "object_consistency": object_consistency,
            }
        )

    row["id"] = f"{source['id']}__plus12_operation_cf_{suffix}"
    row["input"] = input_obj
    row["output"] = LABEL_OUTPUT[label]
    row["compliance_label"] = label
    row["rationale"] = rationale
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_operation_dispatcher_counterfactuals.py",
            "augmentation_type": "operation_ticket_rulebook_counterfactual",
            "source_record_id": source["id"],
            "operation_group": operation_group(source),
            "counterfactual_label": label,
        }
    )
    row["metadata"] = metadata
    return row


def tool_plan_for(intent: str, scenario_id: str) -> list[dict[str, Any]]:
    if intent == "security_check_and_redispatch":
        return [
            {"tool": "get_violations", "args": {"scenario_id": scenario_id}},
            {"tool": "run_power_flow", "args": {"case": "base_and_post_action"}},
            {"tool": "suggest_corrective_actions", "args": {"scenario_id": scenario_id}},
        ]
    return [
        {"tool": "get_violations", "args": {"scenario_id": scenario_id}},
        {"tool": "suggest_corrective_actions", "args": {"scenario_id": scenario_id}},
        {"tool": "run_power_flow", "args": {"case": "post_action"}},
    ]


def dispatcher_variant(source: dict[str, Any], intent: str, variant_index: int, suffix: str) -> dict[str, Any]:
    row = copy.deepcopy(source)
    input_obj = copy.deepcopy(source.get("input") or {})
    scenario_id = str(source.get("scenario_id") or input_obj.get("scenario_id") or "")
    summary = str(input_obj.get("grid_state_summary") or "")
    target_issue = (source.get("slots") or {}).get("target_issue") or "当前风险"
    priority = (source.get("slots") or {}).get("priority") or "urgent"
    route_note = {
        "mitigate_violations": "当前值长要求先形成压降越限的处置动作，再复算动作后状态。",
        "diagnose_and_dispatch": "当前值长要求先定位异常来源和关键设备，再安排后续动作。",
        "security_check_and_redispatch": "当前值长要求先完成安全校核，再输出机组再调度建议。",
    }[intent]
    instruction = (
        f"针对场景 {scenario_id}，请按同一条调度链路处理：先诊断、再处置、后复核。"
        f"{route_note}请给出应路由的意图。"
    )
    input_obj.update(
        {
            "utterance": instruction,
            "routing_priority": route_note,
            "routing_policy": INTENT_OUTPUT_TEXT[intent],
            "grid_state_summary": summary,
        }
    )
    slots = {
        "priority": priority,
        "scenario_id": scenario_id,
        "target_issue": target_issue,
        "intent_family": intent,
    }
    row["id"] = f"{source['id']}__plus12_dispatcher_cf_{suffix}"
    row["instruction"] = instruction
    row["input"] = input_obj
    row["output"] = {"intent": intent, "slots": slots}
    row["intent"] = intent
    row["slots"] = slots
    row["tool_plan"] = tool_plan_for(intent, scenario_id)
    row["rationale"] = (
        f"该样本同时包含诊断、处置和复核描述，最终标签由 routing_priority 中的首要动作决定："
        f"{INTENT_OUTPUT_TEXT[intent]}"
    )
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_operation_dispatcher_counterfactuals.py",
            "augmentation_type": "dispatcher_intent_compound_counterfactual",
            "source_record_id": source["id"],
            "dispatcher_group": f"scenario:{scenario_id}",
            "counterfactual_intent": intent,
            "variant_index": variant_index,
        }
    )
    row["metadata"] = metadata
    return row


def augment(rows: list[dict[str, Any]], max_dispatcher_scenarios: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output = list(rows)
    added: list[dict[str, Any]] = []

    operation_rows = [row for row in rows if row.get("task_type") == "operation_ticket_check"]
    for idx, row in enumerate(operation_rows):
        current = row.get("compliance_label")
        if idx % 5 == 0:
            target = "compliant_with_monitoring"
        elif current == "compliant":
            target = "non_compliant"
        else:
            target = "compliant"
        added.append(ticket_variant(row, target, idx, str(idx)))

    dispatcher_by_scenario: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("task_type") != "dispatcher_intent_tool_call":
            continue
        scenario_id = str(row.get("scenario_id") or "")
        if scenario_id and scenario_id not in dispatcher_by_scenario:
            dispatcher_by_scenario[scenario_id] = row
        if len(dispatcher_by_scenario) >= max_dispatcher_scenarios:
            break
    for idx, source in enumerate(dispatcher_by_scenario.values()):
        for offset, intent in enumerate(INTENT_ORDER):
            added.append(dispatcher_variant(source, intent, offset, f"{idx}_{intent}"))

    output.extend(added)
    before_counts = Counter(row.get("task_type") for row in rows)
    after_counts = Counter(row.get("task_type") for row in output)
    added_counts = Counter(row.get("task_type") for row in added)
    label_counts = {
        "operation_ticket_check": Counter(
            row.get("compliance_label") for row in output if row.get("task_type") == "operation_ticket_check"
        ),
        "dispatcher_intent_tool_call": Counter(
            row.get("intent") for row in output if row.get("task_type") == "dispatcher_intent_tool_call"
        ),
    }
    return output, {
        "input_records": len(rows),
        "output_records": len(output),
        "added_records": len(added),
        "task_counts_before": dict(before_counts),
        "task_counts_after": dict(after_counts),
        "added_task_counts": dict(added_counts),
        "label_counts_after": {task: dict(counter) for task, counter in label_counts.items()},
        "max_dispatcher_scenarios": max_dispatcher_scenarios,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus11_full.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus12.jsonl")
    parser.add_argument("--report-json", default="reports/operation_dispatcher_counterfactual_augmentation_v1.2_plus12.json")
    parser.add_argument("--report-md", default="reports/operation_dispatcher_counterfactual_augmentation_v1.2_plus12.md")
    parser.add_argument("--max-dispatcher-scenarios", type=int, default=2400)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    output, summary = augment(rows, args.max_dispatcher_scenarios)
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
        "# Operation and Dispatcher Counterfactual Augmentation",
        "",
        f"- Input: `{args.input}`",
        f"- Output: `{args.output}`",
        f"- Added records: {summary['added_records']}",
        f"- Output records: {summary['output_records']}",
        "",
        "## Added Task Counts",
        "",
    ]
    for task, count in sorted(summary["added_task_counts"].items()):
        lines.append(f"- {task}: {count}")
    Path(ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
