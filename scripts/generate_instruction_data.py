"""Generate higher-volume GridInstruct instruction records from rules and scenarios."""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
from datetime import datetime, timezone

from gridinstruct_utils import ROOT, default_rules, ensure_dirs, read_json, summarize_records, write_json, write_jsonl
from query_contract import execute_query, overload_query


OPERATION_OBJECTS = ["线路开关", "母联开关", "主变隔离开关", "旁路开关", "电容器开关", "母线刀闸"]
OPERATION_ACTIONS = ["停役", "投入", "转检修", "恢复运行", "由冷备用转热备用", "倒至旁路运行"]
OPERATION_CONTEXTS = [
    "夜班单人监护操作",
    "负荷高峰期间的计划倒闸",
    "检修结束后的恢复操作",
    "事故处理后的临时方式调整",
    "母线方式切换前的准备操作",
    "保电方式下的谨慎操作",
    "新能源大发期间的方式微调",
    "汛期防外破保供方式下的预控操作",
]
CHECK_ITEMS = [
    "设备状态",
    "保护压板",
    "接地刀闸位置",
    "安全措施",
    "同期条件",
    "调度许可",
    "防误闭锁状态",
    "现场监护人到位情况",
]
OPERATION_INSTRUCTION_TEMPLATES = [
    "校核该{obj}{action}操作票是否满足倒闸操作基本要求。",
    "判断这份关于{obj}{action}的操作票是否合规。",
    "请检查该{obj}{action}票据在执行前是否具备全部前置条件。",
    "从倒闸操作校核角度审查这份{obj}{action}操作票。",
    "以调度操作复核方式判断该{obj}{action}票是否可以下发。",
    "核对该{obj}{action}操作安排是否遗漏关键安全校核。",
    "请依据倒闸前置检查要求评估这份{obj}{action}操作票。",
    "如果现在执行该{obj}{action}，这张票是否还缺必要确认项？",
    "站在监护复诵角度，判定该{obj}{action}操作票是否可执行。",
    "面向运行值班场景，审核该{obj}{action}票据是否满足开工条件。",
    "请只判断合规性：该{obj}{action}操作票能否进入执行环节？",
    "按操作对象、许可和安全措施三方面复核该{obj}{action}票。",
]
QA_PROMPTS = [
    "解释规则 {rule_id} 在调度辅助中的含义。",
    "说明 {rule_id} 需要哪些证据字段支撑。",
    "规则 {rule_id} 适用于哪些 GridInstruct 任务？",
    "用调度校核语言概括 {rule_id}。",
    "把 {rule_id} 转成可以落到数据集字段的检查清单。",
    "说明 {rule_id} 在自动评测里最关键的判断点。",
    "把 {rule_id} 改写成面向标注复核的简明规则卡片。",
    "说明 {rule_id} 在模型输出审查时最该先看什么。",
    "把 {rule_id} 解释成值班调度员能直接使用的判定提示。",
    "围绕 {rule_id} 总结一段可追溯的规则说明。",
    "如果要把 {rule_id} 交给新标注员，最小必要说明是什么？",
    "从字段约束角度重述 {rule_id} 的判定逻辑。",
]
QA_COMPARE_PROMPTS = [
    "对比 {rule_id} 与 {other_rule_id} 在 GridInstruct 中分别约束什么。",
    "说明 {rule_id} 和 {other_rule_id} 在证据字段与适用任务上的区别。",
    "把 {rule_id} 与 {other_rule_id} 写成一段并列规则说明。",
    "比较 {rule_id} 和 {other_rule_id} 在调度辅助中的分工。",
]
QA_STYLE_HINTS = [
    "请用两句以内回答。",
    "请突出规则触发条件和证据字段。",
    "请按“规则-证据-任务”的顺序回答。",
    "请尽量写成适合标注员复核的格式。",
    "请写成可直接纳入评测说明的表述。",
    "请避免空泛概念，优先点名字段和任务。",
]
QA_AUDIENCES = ["值班调度员", "数据标注员", "评测工程师", "知识库维护人员", "培训学员", "专家复核员", "模型分析员"]
QA_CONTEXTS = ["实时校核", "事故复盘", "离线验收", "标注复核", "提示词设计", "错误归因", "基准评测"]
QA_DELIVERABLES = ["检查清单", "解释摘要", "评测说明", "字段映射", "复核提示", "训练提示"]
QA_FOCUS_AREAS = ["触发条件", "关键证据", "任务映射", "常见误判", "最小校核闭环", "可追溯性要求"]


def format_regqa_output(
    *,
    rule_id: str,
    summary: str,
    evidence_fields: list[str],
    applicable_tasks: list[str],
    audience: str,
    context: str,
    deliverable: str,
    focus_area: str,
    secondary_rule_id: str | None = None,
    secondary_summary: str | None = None,
    secondary_evidence_fields: list[str] | None = None,
    secondary_applicable_tasks: list[str] | None = None,
) -> str:
    evidence = ", ".join(evidence_fields)
    tasks = ", ".join(applicable_tasks)
    if secondary_rule_id:
        secondary_evidence = ", ".join(secondary_evidence_fields or [])
        secondary_tasks = ", ".join(secondary_applicable_tasks or [])
        return (
            f"面向{audience}的{deliverable}：主规则 {rule_id} 的摘要是“{summary}”，"
            f"证据字段为 {evidence}，适用任务为 {tasks}；"
            f"对比规则 {secondary_rule_id} 的摘要是“{secondary_summary}”，"
            f"证据字段为 {secondary_evidence}，适用任务为 {secondary_tasks}。"
            f"在{context}场景下，复核重点是{focus_area}。"
        )
    if deliverable == "检查清单":
        return (
            f"面向{audience}的检查清单：1) 核对规则 {rule_id} 的摘要“{summary}”；"
            f"2) 检查证据字段 {evidence}；3) 将判断映射到 {tasks}；"
            f"4) 在{context}场景中记录{focus_area}的复核结论。"
        )
    if deliverable == "字段映射":
        return (
            f"面向{audience}的字段映射：规则 {rule_id} -> 摘要“{summary}” -> "
            f"证据字段 {evidence} -> 适用任务 {tasks}；"
            f"{context}场景下的重点字段解释为{focus_area}。"
        )
    if deliverable == "评测说明":
        return (
            f"面向{audience}的评测说明：模型回答必须点明规则 {rule_id}，"
            f"覆盖摘要“{summary}”、证据字段 {evidence} 和适用任务 {tasks}；"
            f"在{context}场景下重点评测是否说明{focus_area}。"
        )
    if deliverable == "训练提示":
        return (
            f"面向{audience}的训练提示：先识别规则 {rule_id} 及其摘要“{summary}”，"
            f"再定位证据字段 {evidence}，最后把结论落到 {tasks}；"
            f"训练时需突出{context}场景中的{focus_area}。"
        )
    if deliverable == "复核提示":
        return (
            f"面向{audience}的复核提示：复核规则 {rule_id} 时，先确认摘要“{summary}”，"
            f"再逐项核查 {evidence}，并确认结论支撑 {tasks}；"
            f"{context}场景下尤其要复核{focus_area}。"
        )
    return (
        f"面向{audience}的解释摘要：规则 {rule_id} 表示“{summary}”，"
        f"判断时依赖 {evidence}，结果服务于 {tasks}；"
        f"在{context}场景中需要突出{focus_area}。"
    )
COMPLIANCE_ACTIONS = [
    {"text": "继续增加受限断面潮流", "category": "risky_increase"},
    {"text": "维持当前潮流并启动监视", "category": "monitor_only"},
    {"text": "先执行无功支撑后复核潮流", "category": "corrective_voltage"},
    {"text": "调整邻近机组出力降低断面负载", "category": "corrective_redispatch"},
    {"text": "先压降受限断面送出再复算潮流", "category": "corrective_redispatch"},
    {"text": "先切换局部拓扑后检查越限是否消除", "category": "corrective_topology"},
    {"text": "执行分阶段负荷转供并继续监视", "category": "corrective_topology"},
    {"text": "不校核设备状态直接恢复故障线路运行", "category": "premature_restore"},
]
COMPLIANCE_INSTRUCTIONS = [
    "判断当前状态下拟执行“{action}”是否合规。",
    "根据当前电网状态，评估“{action}”能否作为可执行调度动作。",
    "请按规程校核“{action}”在该场景下的合规性。",
    "结合场景越限情况，判断调度命令“{action}”是否允许下发。",
    "从实时安全校核角度，说明“{action}”是否满足执行条件。",
]
OPERATOR_GOALS = [
    "消除越限并保持电压合格",
    "优先使用可逆措施恢复安全裕度",
    "在不扩大停电影响的前提下恢复稳态",
    "先处理最严重越限，再复核其他风险",
]
AUXILIARY_INSTRUCTIONS = [
    "基于当前故障后状态，给出符合安全约束的辅助处置建议。",
    "结合越限特征，提出一组便于执行和复核的辅助处置步骤。",
    "请按调度处置思路给出当前场景的优先辅助动作。",
    "围绕当前风险状态，给出符合规程的辅助决策建议。",
]
AUX_DECISION_WINDOWS = ["当前班次复盘", "动作前研判", "动作后复核", "专家抽检", "模型训练", "越限快处", "值长复核"]
AUX_RESPONSE_MODES = ["先处置后复核", "先定位后执行", "保守纠偏", "约束优先", "电压优先", "潮流优先", "可逆操作优先"]
INTENT_PROMPTS = {
    "mitigate_violations": [
        "把这个场景的越限先压下来，动作出来后再复算。",
        "针对当前越限给我一套处置动作，并检查调后潮流。",
        "先消掉越限，再把校核结果回给我。",
        "按调度处置流程把当前风险压下去，并确认动作后是否安全。",
    ],
    "diagnose_and_dispatch": [
        "先看这个场景哪里过载、哪里低压，再给我处置建议。",
        "先诊断越限来源，再安排调度动作。",
        "把当前异常点查清楚，然后给出可执行的调度操作。",
        "先定位问题，再给我一版调度处置方案。",
    ],
    "security_check_and_redispatch": [
        "对这个状态做一次安全校核，并给出再调度建议。",
        "先跑安全校核，再做机组再调度方案。",
        "把故障后状态校核一遍，然后给我重分配出力的建议。",
        "对当前场景执行安全校核和再调度分析。",
    ],
}
INTENT_CHANNELS = ["调度电话", "控制台输入", "复盘记录", "培训样例", "应急演练", "模型评测", "专家抽检"]
QUERY_VARIANTS = [
    {
        "question": "查询该场景下所有过载线路和变压器，并返回最大负载率。",
        "filter": "loading_percent > 100",
        "kind": "overload",
        "equipment_scope": "all",
        "source_regulation_ids": ["REG_QUERY_001", "REG_OVERLOAD_001"],
        "rationale": "该问数任务完整筛选线路和变压器中负载率超过 100% 的设备，同时返回筛选结果的最大负载率。",
    },
    {
        "question": "列出该场景的低电压母线。",
        "filter": "vm_pu < 0.95",
        "kind": "low_voltage",
        "source_regulation_ids": ["REG_QUERY_001", "REG_VOLTAGE_001"],
        "rationale": "该问数任务筛选母线电压低于 0.95 p.u. 的记录，查询对象来自关联仿真输出。",
    },
    {
        "question": "汇总该场景的越限类型和严重等级。",
        "filter": "violation_type != none",
        "kind": "severity_summary",
        "source_regulation_ids": ["REG_QUERY_001"],
        "rationale": "该问数任务汇总场景的越限类型与严重等级，用于事后事件分析和分级回顾。",
    },
    {
        "question": "按场景返回全部过载线路及其中的最大负载率。",
        "filter": "loading_percent > 100",
        "kind": "overload",
        "equipment_scope": "line",
        "source_regulation_ids": ["REG_QUERY_001", "REG_OVERLOAD_001"],
        "rationale": "该问数任务仅筛选过载线路并返回完整列表、数量和线路最大负载率。",
    },
    {
        "question": "把当前场景中电压低于 0.95 p.u. 的母线全部列出来。",
        "filter": "vm_pu < 0.95",
        "kind": "low_voltage",
        "source_regulation_ids": ["REG_QUERY_001", "REG_VOLTAGE_001"],
        "rationale": "该问数任务筛选电压低于 0.95 p.u. 的全部母线，用于电压控制分析与复盘。",
    },
    {
        "question": "给出该场景的越限类别、严重等级和事件回顾所需摘要。",
        "filter": "violation_type != none",
        "kind": "severity_summary",
        "source_regulation_ids": ["REG_QUERY_001"],
        "rationale": "该问数任务聚合越限类型与严重等级，支持事件回顾和后评估。",
    },
    {
        "question": "按场景返回全部过载变压器及其中的最大负载率。",
        "filter": "loading_percent > 100",
        "kind": "overload",
        "equipment_scope": "transformer",
        "source_regulation_ids": ["REG_QUERY_001", "REG_OVERLOAD_001"],
        "rationale": "该问数任务仅筛选过载变压器并返回完整列表、数量和变压器最大负载率。",
    },
]
QUERY_PURPOSES = ["运行复盘", "专家抽检", "事件回顾", "动作前校核", "动作后复核", "模型训练", "基准测试"]
SCENARIO_REFERENCE_TEMPLATES = [
    "在场景 {scenario_id} 下，",
    "结合 {network} 当前方式，",
    "针对这条 {severity} 等级场景，",
    "",
]
INTENT_REQUEST_TEMPLATES = {
    "mitigate_violations": [
        "{priority}把{issue_text}先压下来，再复算潮流确认动作后状态。",
        "请围绕{issue_text}先给出处置动作，完成后再回传安全校核结果。",
        "针对{issue_text}先做纠偏处置，再检查调后是否仍有风险。",
        "先处理{issue_text}，随后复核潮流和电压边界。",
    ],
    "diagnose_and_dispatch": [
        "先把{issue_text}的来源查清，再给我一版可执行的调度处置方案。",
        "请先定位{issue_text}对应的关键异常点，再安排调度动作。",
        "围绕{issue_text}做一次诊断，然后给出处置建议。",
        "先判断{issue_text}是怎么触发的，再生成后续调度操作。",
    ],
    "security_check_and_redispatch": [
        "请先对{issue_text}做安全校核，再给出机组再调度建议。",
        "围绕{issue_text}跑一遍安全校核，并输出再调度方案。",
        "先校核当前状态下的{issue_text}风险，再做出力重分配建议。",
        "请对这条{issue_text}场景执行安全校核和再调度分析。",
    ],
}
QUERY_REQUEST_TEMPLATES = {
    "overload": [
        "列出所有过载支路，并返回最大负载率。",
        "把负载率超过 100% 的线路全部找出来，并给出最高负载率。",
        "汇总当前场景的过载通道，附上最大支路负载率。",
        "返回全部越过热稳边界的支路列表及其最大负载率。",
    ],
    "low_voltage": [
        "列出所有低电压母线。",
        "把电压低于 0.95 p.u. 的母线全部找出来。",
        "汇总当前场景的低电压母线清单。",
        "返回全部触发低电压告警的母线列表。",
    ],
    "severity_summary": [
        "汇总越限类型和严重等级。",
        "给出当前场景的越限类别与严重程度摘要。",
        "返回越限类型、严重等级和简要回顾信息。",
        "整理这条场景涉及的越限种类及其严重等级。",
    ],
}
AUXILIARY_LEAD_TEMPLATES = [
    "基于当前{issue_text}状态，",
    "围绕{goal}这个目标，",
    "在{window}场景下，",
    "按照{mode}思路，",
]
COMPLIANCE_REQUEST_TEMPLATES = [
    "判断“{action}”在当前{issue_text}场景下是否合规，并说明是否满足{goal}。",
    "结合{severity}等级场景，评估“{action}”能否作为符合规程的调度动作。",
    "请按实时安全校核要求，审查“{action}”在当前方式下是否允许执行。",
    "围绕{goal}目标，判断调度命令“{action}”在该场景中是否具备执行条件。",
    "针对场景 {scenario_id}，说明“{action}”是否属于可下发且需复核的操作。",
]


def has_overload(scenario: dict) -> bool:
    if scenario.get("overloaded_branches"):
        return True
    max_loading = float(scenario.get("max_branch_loading_percent") or 0)
    return max_loading > 100.0


def low_voltage_violations(scenario: dict) -> list[dict]:
    violations = []
    for item in scenario.get("voltage_violations", []):
        # Trust the scenario-level violation list, then group by nominal-side.
        if isinstance(item, dict) and float(item.get("vm_pu", 1.0)) < 1.0:
            violations.append(item)
    return violations


def high_voltage_violations(scenario: dict) -> list[dict]:
    violations = []
    for item in scenario.get("voltage_violations", []):
        # Some source scenarios already mark 1.05 p.u. as a voltage violation.
        if isinstance(item, dict) and float(item.get("vm_pu", 1.0)) > 1.0:
            violations.append(item)
    return violations


def active_security_issues(scenario: dict) -> list[str]:
    issues = []
    if has_overload(scenario):
        issues.append("支路过载")
    if low_voltage_violations(scenario):
        issues.append("低电压")
    if high_voltage_violations(scenario):
        issues.append("高电压")
    return issues


def issue_profile(scenario: dict) -> str:
    issues = set(active_security_issues(scenario))
    if "支路过载" in issues and ("低电压" in issues or "高电压" in issues):
        return "combined"
    if "支路过载" in issues:
        return "thermal"
    if "低电压" in issues or "高电压" in issues:
        return "voltage"
    return "normal"


def scenario_summary(scenario: dict, variant: int = 0) -> str:
    issues = active_security_issues(scenario)
    issue_text = "、".join(issues) if issues else "未见显式越限"
    summary_templates = [
        "{network} 场景 {scenario_id}；方式 {kind}；负荷水平 {load:.2f}；最大支路负载率 {loading}；最低母线电压 {min_vm}；严重等级 {severity}；当前关注点：{issue_text}。",
        "{network} 的 {scenario_id} 为 {kind} 场景，当前负荷 {load:.2f}，最大负载率 {loading}，最低电压 {min_vm}，严重等级 {severity}，越限概况为 {issue_text}。",
        "场景 {scenario_id} 来自 {network}，故障类型 {kind}，负荷水平 {load:.2f}，最大线路负载率 {loading}，最低电压 {min_vm}，判定等级 {severity}，需关注 {issue_text}。",
    ]
    template = summary_templates[variant % len(summary_templates)]
    return template.format(
        network=scenario["network_model"],
        scenario_id=scenario["scenario_id"],
        kind=scenario["contingency_type"],
        load=scenario["load_level"],
        loading=scenario.get("max_branch_loading_percent"),
        min_vm=scenario.get("min_bus_voltage_pu"),
        severity=scenario.get("severity_level"),
        issue_text=issue_text,
    )


def base_metadata(difficulty: str, severity: str | None = None, **extra: str | int | bool | None) -> dict:
    metadata = {
        "difficulty": difficulty,
        "severity_level": severity,
        "validation_status": "generated_pending_expert_review",
        "created_by": "generate_instruction_data.py",
    }
    metadata.update(extra)
    return metadata


def issue_text_for_prompt(scenario: dict) -> str:
    issues = active_security_issues(scenario)
    if not issues:
        return "当前安全边界"
    if len(issues) == 1:
        return f"{issues[0]}问题"
    return f"{'和'.join(issues)}问题"


def severity_text(scenario: dict) -> str:
    return {
        "emergency": "紧急",
        "alert": "告警",
        "normal": "常规",
    }.get(scenario.get("severity_level"), "常规")


def scenario_reference(scenario: dict, idx: int, variant: int) -> str:
    template = SCENARIO_REFERENCE_TEMPLATES[(idx + variant) % len(SCENARIO_REFERENCE_TEMPLATES)]
    return template.format(
        scenario_id=scenario["scenario_id"],
        network=scenario["network_model"],
        severity=severity_text(scenario),
    )


def intent_prompt_for(intent: str, scenario: dict, idx: int, variant: int) -> str:
    issue_text = issue_text_for_prompt(scenario)
    template = INTENT_REQUEST_TEMPLATES[intent][(idx + 2 * variant) % len(INTENT_REQUEST_TEMPLATES[intent])]
    return scenario_reference(scenario, idx, variant) + template.format(
        issue_text=issue_text,
        priority="优先" if scenario.get("severity_level") in {"alert", "emergency"} else "先",
    )


def query_question_for(query_variant: dict, scenario: dict, query_purpose: str, idx: int, variant: int) -> str:
    if query_variant.get("equipment_scope"):
        request = query_variant["question"]
    else:
        request = QUERY_REQUEST_TEMPLATES[query_variant["kind"]][
            (idx + 2 * variant) % len(QUERY_REQUEST_TEMPLATES[query_variant["kind"]])
        ]
    scope = scenario_reference(scenario, idx, variant)
    suffixes = [
        f"供{query_purpose}使用。",
        f"用于{query_purpose}。",
        "便于后续复核。",
        "",
    ]
    return scope + request + suffixes[(idx + variant) % len(suffixes)]


def auxiliary_instruction_for(
    scenario: dict,
    operator_goal: str,
    decision_window: str,
    response_mode: str,
    idx: int,
    variant: int,
) -> str:
    lead = AUXILIARY_LEAD_TEMPLATES[(idx + variant) % len(AUXILIARY_LEAD_TEMPLATES)].format(
        issue_text=issue_text_for_prompt(scenario),
        goal=operator_goal,
        window=decision_window,
        mode=response_mode,
    )
    bodies = [
        "给出符合安全约束且便于复核的辅助处置建议。",
        "提出一组优先级清晰的辅助动作，并保留后续复核空间。",
        "整理当前场景下应优先执行的辅助决策步骤。",
        "输出符合规程的处置建议，并说明动作后还需关注什么。",
    ]
    suffixes = [
        "请避免直接给出高影响且不可逆的动作。",
        "优先考虑可逆、可校核的调整顺序。",
        "若需升级处置，请体现触发条件。",
        "",
    ]
    return (
        scenario_reference(scenario, idx, variant)
        + lead
        + bodies[(idx + 2 * variant) % len(bodies)]
        + suffixes[(idx + 3 * variant) % len(suffixes)]
    )


def compliance_instruction_for(scenario: dict, action: str, operator_goal: str, idx: int, variant: int) -> str:
    template = COMPLIANCE_REQUEST_TEMPLATES[(idx + variant) % len(COMPLIANCE_REQUEST_TEMPLATES)]
    return template.format(
        action=action,
        issue_text=issue_text_for_prompt(scenario),
        goal=operator_goal,
        severity=severity_text(scenario),
        scenario_id=scenario["scenario_id"],
    )


def auxiliary_response_bundle(
    scenario: dict,
    profile: str,
    operator_goal: str,
    decision_window: str,
    response_mode: str,
    idx: int,
    variant: int,
) -> tuple[str, str, str]:
    chosen_intro = {
        "combined": [
            "先确认过载断面和异常电压母线的耦合关系",
            "优先核对潮流越限与电压偏移是同源还是相互传导",
            "先把热稳和电压风险的主导约束分开识别",
        ],
        "thermal": [
            "先复算潮流定位过载来源",
            "优先核对受限断面及相邻通道的负载分布",
            "先识别哪条送出路径在推高过载断面负载",
        ],
        "voltage": [
            "先定位低电压或高电压母线的集中区域",
            "优先确认无功缺额和电压控制薄弱点",
            "先核对异常母线与附近无功支撑资源的对应关系",
        ],
        "normal": [
            "先保持当前方式并核对边界裕度",
            "优先确认是否只是贴近约束而非已经越限",
            "先整理当前方式的监视重点和备用调节资源",
        ],
    }
    chosen_action = {
        "combined": [
            "随后同步安排机组再调度与无功支撑，必要时再做小范围拓扑调整",
            "再按先有功后无功的顺序压降受限断面送出并抬升薄弱母线电压",
            "然后联动优化出力分配、无功补偿和局部转供，避免一步到位切负荷",
        ],
        "thermal": [
            "再安排机组再调度压降受限断面送出，必要时配合局部转供",
            "随后下发温和降送出和局部拓扑优化，避免把风险硬转移到相邻线路",
            "然后按受限断面优先级重分配出力，并保留可逆拓扑调整作为后手",
        ],
        "voltage": [
            "再投入无功补偿并优化机组电压控制，必要时小幅调整潮流分布",
            "随后先做无功支撑，再通过温和再调度缓解电压薄弱点",
            "然后联动调压设备和邻近机组励磁，优先恢复电压裕度",
        ],
        "normal": [
            "随后准备温和再调度预案，并维持监视",
            "再保留可逆调节手段，暂不触发高影响动作",
            "然后只做低风险预置，不急于实施大幅控制",
        ],
    }
    chosen_close = [
        f"动作后按{decision_window}节奏复核潮流和电压",
        f"并按照{response_mode}方式保留二次修正空间",
        "最后确认是否出现新的断面转移越限或电压反弹",
        "同时把后续监视点收敛到关键断面和关键母线",
    ]
    rejected_action = {
        "combined": [
            "直接大范围切负荷且不区分热稳还是电压主因",
            "忽略电压问题，只做单一有功调整",
            "不核对设备状态就强行恢复故障元件并退出全部控制措施",
        ],
        "thermal": [
            "不复算潮流就直接切除非故障负荷",
            "忽略过载来源，直接下发大幅降出力命令",
            "先恢复原方式再观察是否继续过载",
        ],
        "voltage": [
            "不做无功支撑，只等待电压自行恢复",
            "只盯支路潮流，忽略异常母线电压",
            "在未核对设备状态前强行恢复故障元件运行",
        ],
        "normal": [
            "在尚未越限时就直接实施高影响切负荷",
            "跳过监视环节，立刻执行激进拓扑切换",
            "不保留备用方案，直接把可逆措施全部用尽",
        ],
    }
    rejected_close = [
        "这会放大操作代价且不给后续复核留空间",
        "这种做法既难以审计，也容易把风险转移到别处",
        "它既不符合渐进纠偏原则，也不利于后续校核",
    ]

    intro = chosen_intro[profile][(idx + variant) % len(chosen_intro[profile])]
    action = chosen_action[profile][(idx + 2 * variant) % len(chosen_action[profile])]
    close = chosen_close[(idx + 3 * variant) % len(chosen_close)]
    chosen = f"{intro}，{action}，{close}，以便{operator_goal}。"

    bad_action = rejected_action[profile][(idx + variant) % len(rejected_action[profile])]
    bad_close = rejected_close[(idx + 2 * variant) % len(rejected_close)]
    rejected = f"{bad_action}，{bad_close}。"

    rationale_focus = {
        "combined": "同时兼顾热稳与电压约束",
        "thermal": "先消除过载再检查风险转移",
        "voltage": "先恢复电压裕度再校核潮流连带影响",
        "normal": "在不放大操作影响的前提下保留可逆调节空间",
    }[profile]
    rationale = (
        f"优选方案强调{rationale_focus}，并结合{decision_window}与{response_mode}保留复核空间；"
        f"拒选方案的问题在于动作过猛或跳过关键校核，不利于{operator_goal}。"
    )
    return chosen, rejected, rationale


def choose_label_output(label: str) -> str:
    return {
        "non_compliant": "违规",
        "compliant_with_monitoring": "合规但需继续监视",
        "compliant": "合规",
    }[label]


def make_operation_ticket(idx: int) -> dict:
    action = OPERATION_ACTIONS[idx % len(OPERATION_ACTIONS)]
    obj = OPERATION_OBJECTS[(idx // len(OPERATION_ACTIONS)) % len(OPERATION_OBJECTS)]
    context = OPERATION_CONTEXTS[(idx // (len(OPERATION_ACTIONS) * len(OPERATION_OBJECTS))) % len(OPERATION_CONTEXTS)]
    primary_check = CHECK_ITEMS[idx % len(CHECK_ITEMS)]
    secondary_check = CHECK_ITEMS[(idx + 3) % len(CHECK_ITEMS)]
    tertiary_check = CHECK_ITEMS[(idx + 5) % len(CHECK_ITEMS)]
    instruction = OPERATION_INSTRUCTION_TEMPLATES[(idx * 5 + idx // 9) % len(OPERATION_INSTRUCTION_TEMPLATES)].format(
        obj=obj,
        action=action,
    )
    # Decide violation from the ticket's semantic content rather than the raw
    # record position. The previous `idx % 5 in {0, 2}` made the label a pure
    # function of record index; keying on (object, action, context, idx) keeps a
    # stable ~40% violation rate while removing the simple positional pattern.
    _ticket_digest = hashlib.sha256(f"{obj}|{action}|{context}|{idx}".encode("utf-8")).digest()
    violation_mode = _ticket_digest[0] % 5 in {0, 2}
    permit_time = f"{(idx % 23) + 1:02d}:{(idx * 7) % 60:02d}"
    execution_window = ["立即执行", "待复诵后执行", "保持热备用待命", "班后窗口执行"][(idx // 6) % 4]
    if violation_mode:
        issue_variant = (idx // 5) % 6
        if issue_variant == 0:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}，计划{execution_window}。"
                f"已核对{secondary_check}和操作对象，但{primary_check}栏为空。"
            )
            permit_status = "已申请但票面未回填"
            monitor_status = "监护人在位"
            object_consistency = "一致"
            rationale = f"操作票缺少{primary_check}核对记录，前置检查链条不完整。"
        elif issue_variant == 1:
            ticket_text = (
                f"操作票 {idx}: {context}下拟执行{obj}{action}。"
                f"票面写明对象为{obj}，但设备状态描述与实际间隔不一致，仅补记了{secondary_check}。"
            )
            permit_status = "已确认"
            monitor_status = "监护人在位"
            object_consistency = "票面对象与状态描述不一致"
            rationale = "操作对象与设备状态描述不一致，存在误操作风险，不满足执行条件。"
        elif issue_variant == 2:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}。"
                f"已完成{primary_check}、{secondary_check}复诵，但调度许可时间留空，现场准备写为“待确认”。"
            )
            permit_status = "许可时间缺失"
            monitor_status = "监护人在位"
            object_consistency = "一致"
            rationale = "调度许可未形成有效闭环记录，不能视为具备完整执行条件。"
        elif issue_variant == 3:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}。"
                f"票面列出{primary_check}和{secondary_check}，但监护人与执行人均未签名，且未见复诵记录。"
            )
            permit_status = "已确认"
            monitor_status = "监护签名缺失"
            object_consistency = "一致"
            rationale = "监护和复诵记录缺失，不能证明操作过程受控。"
        elif issue_variant == 4:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}。"
                f"已说明操作顺序，但{tertiary_check}状态写为“未复核”，仍计划直接执行。"
            )
            permit_status = "已确认"
            monitor_status = "监护人在位"
            object_consistency = "一致"
            rationale = f"{tertiary_check}未复核，关键安全校核项仍然缺口。"
        else:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}。"
                f"票面注明“先操作后补票”，虽然已记录{primary_check}和{secondary_check}，但安全措施栏写为“现场口头确认”。"
            )
            permit_status = "口头许可"
            monitor_status = "监护人在位"
            object_consistency = "一致"
            rationale = "以口头确认替代票面安全措施和正式许可，不满足规范化倒闸要求。"
        label = "non_compliant"
    else:
        ok_variant = (idx // 5) % 5
        if ok_variant == 0:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}，计划{execution_window}。"
                f"已逐项核对{primary_check}、{secondary_check}、操作对象和安全措施，并记录调度许可时间 {permit_time}。"
            )
        elif ok_variant == 1:
            ticket_text = (
                f"操作票 {idx}: {context}下拟执行{obj}{action}。"
                f"票面已完成操作对象复诵、{primary_check}与{secondary_check}检查，监护人与执行人签名齐全。"
            )
        elif ok_variant == 2:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}。"
                f"已核对{primary_check}、{secondary_check}和{tertiary_check}，并补记现场隔离措施与许可编号。"
            )
        elif ok_variant == 3:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}。"
                f"调度许可、监护复诵、设备状态和操作顺序均已回填，关键检查项覆盖{primary_check}与{secondary_check}。"
            )
        else:
            ticket_text = (
                f"操作票 {idx}: {context}下执行{obj}{action}。"
                f"票面先后记录了{primary_check}、{secondary_check}、安全措施确认和结束回执，执行窗口为{execution_window}。"
            )
        # Decorrelate the structured status fields from the compliant label.
        # Previously every compliant ticket carried the unique value
        # "已确认并回填" / "监护人在位且已复诵", which made a single proxy field a
        # perfect label predictor (value-label purity ~1.0). Cycling these
        # values (including values shared with violation tickets) forces the
        # compliance verdict to rest on the complete ticket text, not on one
        # status field.
        compliant_permits = ["已确认并回填", "已确认", "已回填", "已确认"]
        compliant_monitors = ["监护人在位且已复诵", "监护人在位", "待复诵后执行", "监护人在位"]
        permit_status = compliant_permits[ok_variant % len(compliant_permits)]
        monitor_status = compliant_monitors[ok_variant % len(compliant_monitors)]
        object_consistency = "一致"
        rationale = (
            f"操作票覆盖{primary_check}、{secondary_check}及许可/监护闭环，"
            "关键执行条件完整，满足基本倒闸要求。"
        )
        label = "compliant"
    pre_action_state = {
        "投入": "冷备用状态",
        "恢复运行": "检修结束待恢复状态",
        "由冷备用转热备用": "冷备用状态",
        "停役": "运行状态",
        "转检修": "运行状态",
        "倒至旁路运行": "主回路运行、旁路备用状态",
    }[action]
    return {
        "id": f"gridinstruct_v01_ticket_{idx:05d}",
        "task_stage": "pre-event",
        "task_type": "operation_ticket_check",
        "network_model": None,
        "scenario_id": None,
        "instruction": instruction,
        "input": {
            "ticket_text": ticket_text,
            "equipment_state": f"{obj}处于{pre_action_state}，{context}，保护按当前方式投入。",
            "pre_action_state": pre_action_state,
            "operation_context": context,
            "dispatch_permit_status": permit_status,
            "monitoring_arrangement": monitor_status,
            "object_consistency": object_consistency,
            "key_checks": [primary_check, secondary_check, tertiary_check],
        },
        "output": choose_label_output(label),
        "compliance_label": label,
        "rationale": rationale,
        "source_regulation_ids": ["REG_SWITCHING_001"],
        "source_simulation_case_id": None,
        "tool_plan": [],
        "metadata": base_metadata("easy", None, template_family="operation_ticket", variant_index=idx % 20),
    }


def make_regulation_qa(idx: int, rules: list[dict]) -> dict:
    rule = rules[idx % len(rules)]
    secondary_rule = rules[(idx * 7 + 3) % len(rules)]
    prompt = QA_PROMPTS[(idx * 3 + idx // 5) % len(QA_PROMPTS)]
    style_hint = QA_STYLE_HINTS[(idx // len(QA_PROMPTS)) % len(QA_STYLE_HINTS)]
    audience = QA_AUDIENCES[(idx // (len(QA_PROMPTS) * len(QA_STYLE_HINTS))) % len(QA_AUDIENCES)]
    context = QA_CONTEXTS[(idx // 7) % len(QA_CONTEXTS)]
    deliverable = QA_DELIVERABLES[(idx // 11) % len(QA_DELIVERABLES)]
    focus_area = QA_FOCUS_AREAS[(idx // 13) % len(QA_FOCUS_AREAS)]
    question_frames = [
        "{style_hint} 面向{audience}，在{context}场景中输出一段{deliverable}，重点说明{focus_area}。规则摘要：{summary}",
        "{style_hint} 你正在为{audience}准备{deliverable}，请围绕{focus_area}解释这条规则：{summary}",
        "{style_hint} 在{context}任务里，把下面规则整理成{deliverable}，要突出{focus_area}：{summary}",
        "{style_hint} 请把这条规则转写成适合{audience}使用的{deliverable}，并点明{focus_area}：{summary}",
        "{style_hint} 如果这条规则用于{context}复核，请写出面向{audience}的{deliverable}，重点放在{focus_area}：{summary}",
    ]
    question = question_frames[(idx // 17) % len(question_frames)].format(
        style_hint=style_hint,
        audience=audience,
        context=context,
        deliverable=deliverable,
        focus_area=focus_area,
        summary=rule["summary"],
    )
    source_rule_ids = [rule["rule_id"]]
    mode = idx % 8
    if mode == 6:
        prompt = QA_COMPARE_PROMPTS[(idx // 19) % len(QA_COMPARE_PROMPTS)]
        question = (
            f"{style_hint} 面向{audience}，在{context}场景里对比 {rule['summary']} 和 {secondary_rule['summary']}，"
            f"分别说明它们约束的证据字段、适用任务以及{focus_area}。"
        )
        source_rule_ids.append(secondary_rule["rule_id"])
        output = format_regqa_output(
            rule_id=rule["rule_id"],
            summary=rule["summary"],
            evidence_fields=rule["evidence_fields"],
            applicable_tasks=rule["applicable_tasks"],
            audience=audience,
            context=context,
            deliverable=deliverable,
            focus_area=focus_area,
            secondary_rule_id=secondary_rule["rule_id"],
            secondary_summary=secondary_rule["summary"],
            secondary_evidence_fields=secondary_rule["evidence_fields"],
            secondary_applicable_tasks=secondary_rule["applicable_tasks"],
        )
    else:
        output = format_regqa_output(
            rule_id=rule["rule_id"],
            summary=rule["summary"],
            evidence_fields=rule["evidence_fields"],
            applicable_tasks=rule["applicable_tasks"],
            audience=audience,
            context=context,
            deliverable=deliverable,
            focus_area=focus_area,
        )
    instruction = prompt.format(rule_id=rule["rule_id"], other_rule_id=secondary_rule["rule_id"])
    instruction += f" 请面向{audience}，结合{context}场景输出{deliverable}，突出{focus_area}。"
    return {
        "id": f"gridinstruct_v01_regqa_{idx:05d}",
        "task_stage": "real-time",
        "task_type": "regulation_qa",
        "network_model": None,
        "scenario_id": None,
        "instruction": instruction,
        "input": {
            "question": question,
            "rule_id": rule["rule_id"],
            "rule_summary": rule["summary"],
            "evidence_fields": rule["evidence_fields"],
            "applicable_tasks": rule["applicable_tasks"],
            "style_hint": style_hint,
            "audience": audience,
            "usage_context": context,
            "deliverable": deliverable,
            "focus_area": focus_area,
            "secondary_rule_id": secondary_rule["rule_id"] if mode == 6 else None,
            "secondary_rule_summary": secondary_rule["summary"] if mode == 6 else None,
            "secondary_evidence_fields": secondary_rule["evidence_fields"] if mode == 6 else None,
            "secondary_applicable_tasks": secondary_rule["applicable_tasks"] if mode == 6 else None,
        },
        "output": output,
        "rationale": (
            f"回答依据规则卡片构造，主规则为 {rule['rule_id']}；"
            f"重点围绕证据字段 {', '.join(rule['evidence_fields'])} 与任务映射展开。"
        ),
        "source_regulation_ids": source_rule_ids,
        "source_simulation_case_id": None,
        "tool_plan": [],
        "metadata": base_metadata("easy", None, template_family="regulation_qa", variant_index=idx % 24),
    }


def choose_compliance_label(scenario: dict, category: str) -> str:
    profile = issue_profile(scenario)
    severity = scenario.get("severity_level") or "normal"
    max_loading = float(scenario.get("max_branch_loading_percent") or 0.0)
    min_vm = float(scenario.get("min_bus_voltage_pu") or 1.0)
    max_vm = float(scenario.get("max_bus_voltage_pu") or 1.0)
    near_limit = max_loading >= 98.0 or min_vm < 0.97 or max_vm > 1.04
    severe_thermal = max_loading >= 115.0
    severe_voltage = min_vm < 0.92 or max_vm > 1.08

    if category == "premature_restore":
        return "non_compliant" if profile != "normal" or severity != "normal" else "compliant_with_monitoring"

    if category == "risky_increase":
        return "non_compliant" if profile != "normal" or near_limit else "compliant_with_monitoring"

    if profile == "normal":
        if category == "monitor_only":
            return "compliant" if not near_limit else "compliant_with_monitoring"
        if category in {"corrective_voltage", "corrective_redispatch"}:
            return "compliant"
        return "compliant_with_monitoring"

    if category == "monitor_only":
        if severity == "emergency" or profile == "combined" or severe_thermal or severe_voltage:
            return "non_compliant"
        return "compliant_with_monitoring"

    if category == "corrective_voltage":
        if profile == "thermal" and severe_thermal:
            return "non_compliant"
        if profile == "thermal" and max_loading < 108.0:
            return "compliant"
        return "compliant_with_monitoring"

    if category == "corrective_redispatch":
        if profile == "voltage" and severe_voltage:
            return "non_compliant"
        if profile == "thermal" and max_loading < 105.0:
            return "compliant"
        return "compliant_with_monitoring"

    if category == "corrective_topology":
        if profile == "voltage" and severe_voltage and not has_overload(scenario):
            return "non_compliant"
        if profile == "thermal" and max_loading < 108.0:
            return "compliant"
        return "compliant_with_monitoring"

    return "compliant_with_monitoring" if active_security_issues(scenario) else "compliant"


def make_compliance(idx: int, scenario: dict, variant: int) -> dict:
    action_info = COMPLIANCE_ACTIONS[(idx + variant) % len(COMPLIANCE_ACTIONS)]
    action = action_info["text"]
    label = choose_compliance_label(scenario, action_info["category"])
    issues = active_security_issues(scenario)
    issue_text = "、".join(issues) if issues else "未见热稳定或电压越限"
    operator_goal = OPERATOR_GOALS[(idx + 2 * variant) % len(OPERATOR_GOALS)]
    if label == "non_compliant":
        if issues:
            rationale = f"当前场景存在{issue_text}，动作“{action}”不能有效消除风险，且会进一步压缩安全裕度，不满足规程要求。"
        else:
            rationale = f"当前虽未出现显式越限，但安全裕度已接近边界，动作“{action}”会进一步压缩可用裕度，因此不满足规程要求。"
    elif label == "compliant_with_monitoring":
        if issues:
            rationale = f"当前场景存在{issue_text}，动作“{action}”属于允许的纠正或受控操作，但执行后仍需复核潮流并继续监视。"
        else:
            rationale = f"当前未见热稳定或电压越限，但动作“{action}”涉及恢复方式或较强控制，执行后仍需复核潮流并继续监视。"
    else:
        if issues:
            rationale = f"当前场景虽存在{issue_text}，但动作“{action}”属于可执行的低风险纠正措施，完成后按常规流程校核并监视即可。"
        else:
            rationale = f"当前未见热稳定或电压越限，动作“{action}”可执行，但仍应完成常规安全校核与监视。"
    return {
        "id": f"gridinstruct_v01_compliance_{idx:05d}",
        "task_stage": "real-time",
        "task_type": "regulation_compliance_check",
        "network_model": scenario["network_model"].replace(" ", ""),
        "scenario_id": scenario["scenario_id"],
        "instruction": compliance_instruction_for(scenario, action, operator_goal, idx, variant),
        "input": {
            "grid_state_summary": scenario_summary(scenario, variant),
            "proposed_action": action,
            "operator_goal": operator_goal,
            "observed_issues": issues,
        },
        "output": choose_label_output(label),
        "compliance_label": label,
        "rationale": rationale,
        "source_regulation_ids": ["REG_OVERLOAD_001", "REG_VOLTAGE_001"],
        "source_simulation_case_id": scenario["scenario_id"],
        "tool_plan": [],
        "metadata": base_metadata(
            "medium",
            scenario["severity_level"],
            template_family="regulation_compliance",
            variant_index=variant,
            issue_profile=issue_profile(scenario),
        ),
    }


def make_auxiliary(idx: int, scenario: dict, variant: int) -> dict:
    profile = issue_profile(scenario)
    operator_goal = OPERATOR_GOALS[(idx + variant) % len(OPERATOR_GOALS)]
    decision_window = AUX_DECISION_WINDOWS[(idx + variant) % len(AUX_DECISION_WINDOWS)]
    response_mode = AUX_RESPONSE_MODES[(idx + 2 * variant) % len(AUX_RESPONSE_MODES)]
    chosen, rejected, rationale = auxiliary_response_bundle(
        scenario,
        profile,
        operator_goal,
        decision_window,
        response_mode,
        idx,
        variant,
    )
    return {
        "id": f"gridinstruct_v01_aux_{idx:05d}",
        "task_stage": "real-time",
        "task_type": "auxiliary_decision",
        "network_model": scenario["network_model"].replace(" ", ""),
        "scenario_id": scenario["scenario_id"],
        "instruction": auxiliary_instruction_for(scenario, operator_goal, decision_window, response_mode, idx, variant),
        "input": {
            "decision_window": decision_window,
            "response_mode": response_mode,
            "operator_goal": operator_goal,
            "issue_profile": profile,
            "grid_state_summary": scenario_summary(scenario, variant),
            "available_actions": ["generator_redispatch", "reactive_power_support", "topology_switching", "load_shedding"],
        },
        "output": chosen,
        "rationale": rationale,
        "chosen_response": chosen,
        "rejected_response": rejected,
        "preference_rationale": {
            "chosen_advantages": ["regulation_compliance", "lower_operational_risk", "allows_recheck"],
            "rejected_issues": ["premature_high_impact_action", "missing_power_flow_check"],
        },
        "source_regulation_ids": ["REG_OVERLOAD_001", "REG_VOLTAGE_001"],
        "source_simulation_case_id": scenario["scenario_id"],
        "tool_plan": [
            {"tool": "run_power_flow", "args": {"scenario_id": scenario["scenario_id"]}},
            {"tool": "run_opf_redispatch", "args": {"objective": "remove_violations"}},
        ],
        "metadata": base_metadata(
            "hard",
            scenario["severity_level"],
            template_family="auxiliary_decision",
            variant_index=variant,
            issue_profile=profile,
        ),
    }


def intent_slots_for(scenario: dict, intent: str) -> dict:
    issues = active_security_issues(scenario)
    target_issue = issues[0] if issues else "常规安全校核"
    priority = "urgent" if scenario["severity_level"] in {"alert", "emergency"} else "normal"
    return {
        "priority": priority,
        "scenario_id": scenario["scenario_id"],
        "target_issue": target_issue,
        "intent_family": intent,
    }


def tool_plan_for(intent: str, scenario_id: str) -> list[dict]:
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


def make_intent(idx: int, scenario: dict, variant: int) -> dict:
    intents = list(INTENT_PROMPTS.keys())
    intent = intents[(idx + variant) % len(intents)]
    utterance = intent_prompt_for(intent, scenario, idx, variant)
    command_channel = INTENT_CHANNELS[(idx + variant) % len(INTENT_CHANNELS)]
    slots = intent_slots_for(scenario, intent)
    tool_plan = tool_plan_for(intent, scenario["scenario_id"])
    return {
        "id": f"gridinstruct_v01_intent_{idx:05d}",
        "task_stage": "real-time",
        "task_type": "dispatcher_intent_tool_call",
        "network_model": scenario["network_model"].replace(" ", ""),
        "scenario_id": scenario["scenario_id"],
        "instruction": utterance,
        "input": {
            "command_channel": command_channel,
            "utterance": utterance,
            "grid_state_summary": scenario_summary(scenario, variant),
        },
        "output": {"intent": intent, "slots": slots},
        "intent": intent,
        "slots": slots,
        "rationale": "该意图要求先定位越限，再生成调度动作并复算潮流，因此工具顺序必须覆盖状态查询、处置建议和潮流校核。",
        "source_regulation_ids": ["REG_TOOL_001"],
        "source_simulation_case_id": scenario["scenario_id"],
        "tool_plan": tool_plan,
        "metadata": base_metadata(
            "medium",
            scenario["severity_level"],
            template_family="dispatcher_intent",
            variant_index=variant,
            issue_profile=issue_profile(scenario),
        ),
    }


def structured_query_for(query_variant: dict, scenario_id: str) -> dict:
    if query_variant.get("filter") == "loading_percent > 100":
        return overload_query(
            scenario_id,
            equipment_scope=str(query_variant["equipment_scope"]),
        )
    return {
        "source": "simulation_outputs",
        "filter": query_variant["filter"],
        "scenario_id": scenario_id,
    }


def make_query(idx: int, scenario: dict, variant: int) -> dict:
    query_variant = QUERY_VARIANTS[(idx + 2 * variant) % len(QUERY_VARIANTS)]
    query_purpose = QUERY_PURPOSES[(idx + variant) % len(QUERY_PURPOSES)]
    question = query_question_for(query_variant, scenario, query_purpose, idx, variant)
    query = structured_query_for(query_variant, scenario["scenario_id"])
    result = execute_query(query, scenario)
    return {
        "id": f"gridinstruct_v01_query_{idx:05d}",
        "task_stage": "post-event",
        "task_type": "intelligent_data_query",
        "network_model": scenario["network_model"].replace(" ", ""),
        "scenario_id": scenario["scenario_id"],
        "instruction": question,
        "input": {
            "request_purpose": query_purpose,
            "question": question,
            "scenario_id": scenario["scenario_id"],
            "analysis_focus": issue_profile(scenario),
        },
        "output": {
            "structured_query": query,
            "query_result": result,
        },
        "structured_query": query,
        "query_result": result,
        "rationale": query_variant["rationale"],
        "source_regulation_ids": query_variant["source_regulation_ids"],
        "source_simulation_case_id": scenario["scenario_id"],
        "tool_plan": [
            {
                "tool": "query_simulation_records",
                "args": query,
            }
        ],
        "metadata": base_metadata(
            "easy",
            scenario["severity_level"],
            template_family="intelligent_query",
            variant_index=variant,
            issue_profile=issue_profile(scenario),
        ),
    }


def write_data_dictionary(path) -> None:
    ensure_dirs(path.parent)
    fields = [
        ("id", "唯一指令记录标识"),
        ("task_stage", "事前、实时或事后"),
        ("task_type", "细粒度任务类型"),
        ("network_model", "关联电网模型"),
        ("scenario_id", "关联仿真场景"),
        ("instruction", "自然语言任务指令"),
        ("input", "任务上下文"),
        ("output", "参考答案或目标响应"),
        ("rationale", "专家校验解释性标注或待审解释"),
        ("source_regulation_ids", "规程规则标识"),
        ("source_simulation_case_id", "关联仿真输出标识"),
        ("tool_plan", "有序工具调用"),
        ("compliance_label", "合规任务标签"),
        ("intent", "调度员意图识别标签"),
        ("slots", "意图识别任务中的结构化槽位"),
        ("structured_query", "智能问数任务中的结构化查询"),
        ("query_result", "结构化查询对应的参考结果"),
        ("chosen_response", "偏好任务中的优选响应"),
        ("rejected_response", "偏好任务中的拒绝响应"),
        ("preference_rationale", "偏好标注的结构化原因"),
        (
            "executable_control_target",
            "OPF 辅助决策中的完整 ext_grid/gen P/Q/电压绝对设定值、变化量及反放合同",
        ),
        ("target_replay_validation", "OPF 结构化目标的完整性和双模式反放校验结果"),
        ("metadata", "难度、严重度、模板族、验证和来源元数据"),
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["field", "description"])
        writer.writerows(fields)


def scenario_for(task_scenarios: list[dict], idx: int) -> tuple[dict, int]:
    scenario = task_scenarios[idx % len(task_scenarios)]
    variant = idx // len(task_scenarios)
    return scenario, variant


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--output", default="data/gridinstruct_v0.1.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target-per-task", type=int, default=None)
    parser.add_argument("--flat-target-per-task", type=int, default=4000)
    parser.add_argument("--scenario-target-per-task", type=int, default=15000)
    args = parser.parse_args()

    flat_target = args.target_per_task if args.target_per_task is not None else args.flat_target_per_task
    scenario_target = args.target_per_task if args.target_per_task is not None else args.scenario_target_per_task

    rules = default_rules()
    scenarios = read_json(ROOT / args.scenarios)
    if not scenarios:
        raise RuntimeError("No scenarios found. Run generate_grid_scenarios.py first.")

    rng = random.Random(args.seed)
    scenarios = list(scenarios)
    rng.shuffle(scenarios)
    compliance_scenarios = list(scenarios)
    auxiliary_scenarios = list(scenarios)
    intent_scenarios = list(scenarios)
    query_scenarios = list(scenarios)
    random.Random(args.seed + 11).shuffle(compliance_scenarios)
    random.Random(args.seed + 17).shuffle(auxiliary_scenarios)
    random.Random(args.seed + 23).shuffle(intent_scenarios)
    random.Random(args.seed + 29).shuffle(query_scenarios)

    rows = []
    for i in range(flat_target):
        rows.append(make_operation_ticket(i))
        rows.append(make_regulation_qa(i, rules))
    for i in range(scenario_target):
        scenario, variant = scenario_for(compliance_scenarios, i)
        rows.append(make_compliance(i, scenario, variant))
        scenario, variant = scenario_for(auxiliary_scenarios, i)
        rows.append(make_auxiliary(i, scenario, variant))
        scenario, variant = scenario_for(intent_scenarios, i)
        rows.append(make_intent(i, scenario, variant))
        scenario, variant = scenario_for(query_scenarios, i)
        rows.append(make_query(i, scenario, variant))

    ensure_dirs(ROOT / "data", ROOT / "metadata", ROOT / "reports")
    write_jsonl(ROOT / args.output, rows)
    write_data_dictionary(ROOT / "metadata/data_dictionary.csv")
    write_json(
        ROOT / "reports/generation_stats.json",
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "output": args.output,
            "targets": {
                "flat_target_per_task": flat_target,
                "scenario_target_per_task": scenario_target,
                "scenario_source_count": len(scenarios),
                "seed": args.seed,
            },
            "summary": summarize_records(rows),
        },
    )


if __name__ == "__main__":
    main()
