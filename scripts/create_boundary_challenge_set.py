#!/usr/bin/env python3
"""Create a balanced boundary challenge set for high-score classification tasks."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, stable_shuffle, write_json, write_jsonl


LABEL_OUTPUT = {
    "compliant": "合规",
    "compliant_with_monitoring": "合规但需继续监视",
    "non_compliant": "违规",
}

DISPATCHER_INTENTS = [
    "diagnose_and_dispatch",
    "mitigate_violations",
    "security_check_and_redispatch",
]

DISPATCHER_POLICIES = {
    "diagnose_and_dispatch": "先定位异常来源和关键设备，再安排后续处置。",
    "mitigate_violations": "先生成压降越限或恢复裕度的处置动作，再复算动作后状态。",
    "security_check_and_redispatch": "先完成安全校核，再输出机组再调度建议。",
}


def extract_operation_object(row: dict[str, Any]) -> str:
    state = str((row.get("input") or {}).get("equipment_state") or "")
    return state.split("处于", 1)[0] if "处于" in state else "设备"


def operation_action(row: dict[str, Any]) -> str:
    text = str((row.get("input") or {}).get("ticket_text") or row.get("instruction") or "")
    for action in ["由冷备用转热备用", "倒至旁路运行", "恢复运行", "转检修", "停役", "投入"]:
        if action in text:
            return action
    return "操作"


def operation_context(row: dict[str, Any]) -> str:
    return str((row.get("input") or {}).get("operation_context") or "运行操作")


def check_triplet(row: dict[str, Any], idx: int) -> tuple[str, str, str]:
    checks = list((row.get("input") or {}).get("key_checks") or ["设备状态", "安全措施", "调度许可"])
    return checks[idx % len(checks)], checks[(idx + 1) % len(checks)], checks[(idx + 2) % len(checks)]


def operation_variant(source: dict[str, Any], label: str, idx: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    payload = copy.deepcopy(source.get("input") or {})
    obj = extract_operation_object(source)
    action = operation_action(source)
    context = operation_context(source)
    primary, secondary, tertiary = check_triplet(source, idx)
    permit = f"T-BD-{idx:05d}"
    common_head = (
        f"边界操作票 {idx}: {context}，{obj}{action}。"
        f"执行前栏: {primary}已核对，{secondary}已核对，{tertiary}已核对；"
    )
    common_tail = "票尾记录: 执行后需形成复核记录，若出现异常则暂缓终结并上报值长。"

    if label == "compliant":
        ticket_text = (
            common_head
            + f"调度许可编号 {permit} 已回填，监护人与执行人均签名，操作对象复诵为{obj}。"
            + common_tail
        )
        payload.update(
            {
                "ticket_text": ticket_text,
                "dispatch_permit_status": "已确认并回填",
                "monitoring_arrangement": "监护人在位，执行后复核为常规留痕",
                "object_consistency": "一致",
            }
        )
        rationale = "执行前许可、对象、监护和关键检查均已闭环；执行后复核属于常规留痕。"
    elif label == "compliant_with_monitoring":
        ticket_text = (
            common_head
            + f"调度许可编号 {permit} 已回填，监护人与执行人均签名，操作对象复诵为{obj}；"
            + "票面特别要求执行后15分钟内核对遥测、保护信号和现场回执一致性。"
        )
        payload.update(
            {
                "ticket_text": ticket_text,
                "dispatch_permit_status": "已确认并回填",
                "monitoring_arrangement": "监护人在位，执行后必须完成指定复核",
                "object_consistency": "一致",
                "post_execution_followup": {
                    "pending_evidence": "遥测、保护信号和现场回执一致性",
                    "window_minutes": 15,
                    "fallback_action": "复核异常时暂缓终结并上报值长",
                },
            }
        )
        rationale = "执行前条件已闭环，但票面把执行后状态复核列为强制条件，需继续监视。"
    else:
        ticket_text = (
            common_head
            + f"调度许可编号待回填，监护人签名栏为空，操作对象复诵为{obj}；"
            + common_tail
        )
        payload.update(
            {
                "ticket_text": ticket_text,
                "dispatch_permit_status": "许可编号缺失",
                "monitoring_arrangement": "监护签名缺失",
                "object_consistency": "一致",
            }
        )
        rationale = "调度许可和监护签名未在执行前闭环，执行后复核不能替代前置许可。"

    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "create_boundary_challenge_set.py",
            "augmentation_type": "operation_multilabel_plain_boundary",
            "source_record_id": source["id"],
            "boundary_family": "operation_plain_multilabel",
            "boundary_label": label,
            "boundary_split_role": "test_only_audit",
        }
    )
    row.update(
        {
            "id": f"{source['id']}__boundary_plain_{label}_{idx}",
            "input": payload,
            "output": LABEL_OUTPUT[label],
            "compliance_label": label,
            "rationale": rationale,
            "metadata": metadata,
        }
    )
    return row


def dispatcher_variant(source: dict[str, Any], intent: str, idx: int) -> dict[str, Any]:
    row = copy.deepcopy(source)
    payload = copy.deepcopy(source.get("input") or {})
    scenario_id = str(source.get("scenario_id") or payload.get("scenario_id") or "")
    target_issue = (source.get("slots") or {}).get("target_issue") or "当前风险"
    priority_text = DISPATCHER_POLICIES[intent]
    instruction = (
        f"针对场景 {scenario_id}，同一条请求同时提到诊断、处置和复核。"
        f"本次值长明确优先级为：{priority_text}请判断应进入哪个调度意图。"
    )
    payload.update(
        {
            "utterance": instruction,
            "routing_priority": priority_text,
            "routing_policy": priority_text,
        }
    )
    slots = {
        "priority": "urgent",
        "scenario_id": scenario_id,
        "target_issue": target_issue,
        "intent_family": intent,
    }
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "create_boundary_challenge_set.py",
            "augmentation_type": "dispatcher_priority_multilabel_boundary",
            "source_record_id": source["id"],
            "boundary_family": "dispatcher_priority_multilabel",
            "boundary_label": intent,
            "boundary_split_role": "test_only_audit",
        }
    )
    row.update(
        {
            "id": f"{source['id']}__boundary_dispatcher_{intent}_{idx}",
            "instruction": instruction,
            "input": payload,
            "output": {"intent": intent, "slots": slots},
            "intent": intent,
            "slots": slots,
            "rationale": f"同一句请求包含多个动作，标签由显式优先级决定：{priority_text}",
            "metadata": metadata,
        }
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output", default="data/v1.2_sd_core_boundary_challenge_test.jsonl")
    parser.add_argument("--report-json", default="reports/boundary_challenge_set_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/boundary_challenge_set_v1.2_sd_core.md")
    parser.add_argument("--operation-sources", type=int, default=1000)
    parser.add_argument("--dispatcher-sources", type=int, default=600)
    parser.add_argument("--seed", type=int, default=2041)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    base_operation = [
        row
        for row in rows
        if row.get("task_type") == "operation_ticket_check"
        and not (row.get("metadata") or {}).get("augmentation_type")
    ]
    base_dispatcher_by_scenario: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("task_type") != "dispatcher_intent_tool_call":
            continue
        if (row.get("metadata") or {}).get("augmentation_type"):
            continue
        scenario_id = str(row.get("scenario_id") or "")
        if scenario_id and scenario_id not in base_dispatcher_by_scenario:
            base_dispatcher_by_scenario[scenario_id] = row

    base_operation = stable_shuffle(base_operation, args.seed)[: args.operation_sources]
    base_dispatcher = stable_shuffle(list(base_dispatcher_by_scenario.values()), args.seed + 1)[: args.dispatcher_sources]

    output: list[dict[str, Any]] = []
    for idx, source in enumerate(base_operation):
        for label in LABEL_OUTPUT:
            output.append(operation_variant(source, label, idx))
    for idx, source in enumerate(base_dispatcher):
        for intent in DISPATCHER_INTENTS:
            output.append(dispatcher_variant(source, intent, idx))
    output = stable_shuffle(output, args.seed + 2)

    ensure_dirs((ROOT / args.output).parent, (ROOT / args.report_json).parent)
    write_jsonl(ROOT / args.output, output)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "records": len(output),
        "operation_sources": len(base_operation),
        "dispatcher_sources": len(base_dispatcher),
        "task_counts": dict(Counter(row.get("task_type") for row in output)),
        "label_counts": {
            "operation_ticket_check": dict(
                Counter(row.get("compliance_label") for row in output if row.get("task_type") == "operation_ticket_check")
            ),
            "dispatcher_intent_tool_call": dict(
                Counter(row.get("intent") for row in output if row.get("task_type") == "dispatcher_intent_tool_call")
            ),
        },
        "status": "pass",
    }
    write_json(ROOT / args.report_json, summary)
    lines = [
        "# Boundary Challenge Set",
        "",
        f"Generated: `{summary['generated_at']}`",
        f"Output: `{args.output}`",
        f"Records: {summary['records']}",
        "",
        "## Task Counts",
        "",
    ]
    for task, count in sorted(summary["task_counts"].items()):
        lines.append(f"- `{task}`: {count}")
    lines.extend(["", "## Label Counts", ""])
    for task, counts in summary["label_counts"].items():
        lines.append(f"### {task}")
        for label, count in sorted(counts.items()):
            lines.append(f"- `{label}`: {count}")
        lines.append("")
    (ROOT / args.report_md).write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
