"""Append rule-consistent compliance action paraphrases for plus10.

The plus9 compliance challenge split exposed an unhealthy artifact: validation
and test could each be dominated by a single proposed-action surface. This
script keeps the original simulation cases and labels, but adds alternative
operator action phrasings within the same rulebook action category so models
can be evaluated on category understanding instead of one memorized string.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


ACTION_SURFACES: dict[str, list[str]] = {
    "risky_increase": [
        "继续增加受限断面潮流",
        "继续提升受限断面潮流",
        "继续提升受限断面输送功率",
        "持续增加受限断面输送功率",
        "进一步提高受限断面送出",
        "增加受限通道输送水平",
        "继续扩大受限断面传输功率",
        "维持增送计划并提高断面潮流",
    ],
    "monitor_only": [
        "维持当前潮流并启动监视",
        "保持当前潮流并开展连续监视",
        "暂不调整潮流并加强监视",
        "维持现有运行方式并跟踪越限变化",
        "不改变潮流分布，仅执行实时监视",
        "保持当前断面送出并启动复核监视",
    ],
    "corrective_voltage": [
        "先执行无功支撑后复核潮流",
        "先实施无功支持措施，随后验证潮流分布",
        "先执行无功补偿再复核潮流",
        "先投切无功补偿再复核电压和潮流",
        "先进行电压支撑后重新校核潮流",
        "先安排无功调节并复核电压越限",
    ],
    "corrective_redispatch": [
        "调整邻近机组出力降低断面负载",
        "先压降受限断面送出再复算潮流",
        "重新分配邻近机组出力以降低断面负载",
        "下调受限断面送出并复核潮流",
        "通过机组再调度释放断面裕度",
        "调整发电出力组合以缓解过载断面",
    ],
    "corrective_topology": [
        "先切换局部拓扑后检查越限是否消除",
        "执行分阶段负荷转供并继续监视",
        "先调整局部网络拓扑再复核越限",
        "分阶段转供负荷后持续监视潮流",
        "先实施拓扑转移再校核低压和过载",
        "局部倒换供电路径后检查安全裕度",
    ],
    "premature_restore": [
        "不校核设备状态直接恢复故障线路运行",
        "直接恢复故障线路运行，不校核设备状态",
        "直接恢复故障线路运行，不进行设备状态校核",
        "未进行设备状态确认即恢复故障线路运行",
        "未确认设备状态即恢复故障线路运行",
        "跳过状态校核直接送电故障线路",
        "不做设备状态确认即恢复退出线路",
    ],
}

ACTION_TO_CATEGORY = {
    surface: category for category, surfaces in ACTION_SURFACES.items() for surface in surfaces
}

INSTRUCTION_TEMPLATES = [
    "判断当前状态下拟执行“{action}”是否合规。",
    "根据当前电网状态，评估“{action}”能否作为可执行调度动作。",
    "请按规程校核“{action}”在该场景下的合规性。",
    "结合场景越限情况，判断调度命令“{action}”是否允许下发。",
    "从实时安全校核角度，说明“{action}”是否满足执行条件。",
    "面向调度复核，判断“{action}”是否满足当前安全边界。",
    "请依据当前越限状态审查“{action}”的执行许可。",
    "以实时安全校核结果为依据，判定“{action}”是否可下发。",
]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def replace_text(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace_text(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: replace_text(item, old, new) for key, item in value.items()}
    return value


def action_category(row: dict[str, Any]) -> str | None:
    metadata = row.get("metadata") or {}
    if metadata.get("action_category"):
        return str(metadata["action_category"])
    action = infer_action_surface(row)
    return ACTION_TO_CATEGORY.get(str(action or ""))


def infer_action_surface(row: dict[str, Any]) -> str | None:
    action = row.get("input", {}).get("proposed_action")
    if action and str(action) in ACTION_TO_CATEGORY:
        return str(action)
    haystack = "\n".join(
        [str(action or "")]
        + [str(row.get(field) or "") for field in ("instruction", "rationale", "output")]
    )
    for surface in sorted(ACTION_TO_CATEGORY, key=len, reverse=True):
        if surface in haystack:
            return surface
    return None


def annotate_original(row: dict[str, Any], category: str | None, inferred_action: str | None) -> dict[str, Any]:
    out = copy.deepcopy(row)
    metadata = dict(out.get("metadata") or {})
    if category:
        metadata["action_category"] = category
    if inferred_action and not (out.get("input") or {}).get("proposed_action"):
        out["input"] = dict(out.get("input") or {})
        out["input"]["proposed_action"] = inferred_action
        metadata["proposed_action_recovered_by"] = "augment_compliance_action_paraphrases.py"
    out["metadata"] = metadata
    return out


def paraphrase_row(row: dict[str, Any], category: str, action: str, variant: int) -> dict[str, Any]:
    old_action = str(infer_action_surface(row) or "")
    out = copy.deepcopy(row)
    out["id"] = f"{row['id']}_actionpara_{variant:02d}"
    out["instruction"] = INSTRUCTION_TEMPLATES[variant % len(INSTRUCTION_TEMPLATES)].format(action=action)
    out["input"] = replace_text(out.get("input") or {}, old_action, action)
    out["input"]["proposed_action"] = action
    out["rationale"] = replace_text(out.get("rationale"), old_action, action)
    metadata = dict(out.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_compliance_action_paraphrases.py",
            "augmentation_type": "rulebook_action_surface_paraphrase",
            "source_record_id": row["id"],
            "action_category": category,
            "action_surface_variant": variant,
        }
    )
    out["metadata"] = metadata
    return out


def build_augmented_rows(rows: list[dict[str, Any]], compliant_extra: int) -> tuple[list[dict[str, Any]], Counter[str]]:
    out: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for row_index, row in enumerate(rows):
        if row["id"] in seen_ids:
            raise ValueError(f"Duplicate input id: {row['id']}")
        seen_ids.add(row["id"])

        if row.get("task_type") != "regulation_compliance_check":
            out.append(row)
            continue

        inferred_action = infer_action_surface(row)
        category = action_category(row)
        out.append(annotate_original(row, category, inferred_action))
        if not category:
            stats["unknown_category"] += 1
            continue

        surfaces = ACTION_SURFACES[category]
        old_action = str(inferred_action or "")
        candidates = [surface for surface in surfaces if surface != old_action]
        if not candidates:
            continue

        base_count = 1
        extra_count = compliant_extra if row.get("compliance_label") == "compliant" else 0
        add_count = min(base_count + extra_count, len(candidates))
        start = row_index % len(candidates)
        for offset in range(add_count):
            action = candidates[(start + offset) % len(candidates)]
            variant = offset + 1
            new_row = paraphrase_row(row, category, action, variant)
            if new_row["id"] in seen_ids:
                raise ValueError(f"Duplicate generated id: {new_row['id']}")
            seen_ids.add(new_row["id"])
            out.append(new_row)
            stats[f"added_{row.get('compliance_label')}"] += 1
            stats[f"added_category_{category}"] += 1

    return out, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus9.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus10.jsonl")
    parser.add_argument("--report-json", default="reports/compliance_action_paraphrase_augmentation_v1.2_plus10.json")
    parser.add_argument("--report-md", default="reports/compliance_action_paraphrase_augmentation_v1.2_plus10.md")
    parser.add_argument("--compliant-extra", type=int, default=5)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    out_rows, stats = build_augmented_rows(rows, args.compliant_extra)
    write_jsonl(ROOT / args.output, out_rows)

    before_counts = Counter(
        row.get("compliance_label")
        for row in rows
        if row.get("task_type") == "regulation_compliance_check"
    )
    after_counts = Counter(
        row.get("compliance_label")
        for row in out_rows
        if row.get("task_type") == "regulation_compliance_check"
    )
    category_counts = Counter(
        (row.get("metadata") or {}).get("action_category")
        for row in out_rows
        if row.get("task_type") == "regulation_compliance_check"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "input_records": len(rows),
        "output_records": len(out_rows),
        "added_records": len(out_rows) - len(rows),
        "compliant_extra": args.compliant_extra,
        "before_regulation_compliance_labels": dict(before_counts),
        "after_regulation_compliance_labels": dict(after_counts),
        "action_category_counts": dict(category_counts),
        "stats": dict(stats),
        "passed": stats.get("unknown_category", 0) == 0,
    }
    write_json(ROOT / args.report_json, report)
    lines = [
        "# Compliance Action Paraphrase Augmentation",
        "",
        f"Generated: {report['generated_at']}",
        f"Input: `{args.input}`",
        f"Output: `{args.output}`",
        f"Input records: {len(rows)}",
        f"Output records: {len(out_rows)}",
        f"Added records: {report['added_records']}",
        f"Passed: `{report['passed']}`",
        "",
        "## Regulation Compliance Label Counts",
        "",
        f"- Before: {dict(before_counts)}",
        f"- After: {dict(after_counts)}",
        "",
        "## Action Category Counts",
        "",
    ]
    lines.extend(f"- {key or 'unknown'}: {value}" for key, value in sorted(category_counts.items(), key=lambda item: str(item[0])))
    (ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
