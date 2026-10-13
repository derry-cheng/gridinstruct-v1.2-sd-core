"""Add LLM-paraphrased implicit hard cases for operation-ticket labels."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from augment_operation_monitoring_counterfactuals import (
    LABEL_OUTPUT,
    extract_operation_object,
    operation_action_from_text,
    operation_group,
)
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, stable_shuffle, write_json, write_jsonl


DEFAULT_BASE_URL = "http://127.0.0.1:8888/v1"
DEFAULT_MODEL = "/data/huggingface/hub/Qwen3.5-35B-A3"

TARGET_CASES = {
    "compliant": [
        {
            "key": "resolved_signature_gap",
            "requirement": "票面可提到签名栏或交底记录曾经需要补齐，但必须写清发令前已经形成可追溯闭环。",
        },
        {
            "key": "resolved_safety_attachment",
            "requirement": "票面可提到安全措施附件和备注，但必须写清附件编号、监护确认和发令前核验均已完成。",
        },
        {
            "key": "resolved_object_revision",
            "requirement": "票面可提到对象名称有修订痕迹，但必须写清修订已在发令前复诵确认。",
        },
        {
            "key": "routine_abnormality_note",
            "requirement": "票尾可提到异常处置或告警留痕，但必须是常规留痕，不是放行条件。",
        },
        {
            "key": "resolved_permit_trace",
            "requirement": "票面可提到许可流水和电话记录，但必须写清调度许可记录在执行前可追溯。",
        },
        {
            "key": "resolved_monitor_trace",
            "requirement": "票面可提到监护记录补充页，但必须写清监护人发令前已签认。",
        },
    ],
    "compliant_with_monitoring": [
        {
            "key": "post_exec_remote_echo",
            "requirement": "执行前条件闭环，但票面要求执行后核对远动回显和现场回执的一致性；不要使用“若复核异常”或“暂不终结”。",
        },
        {
            "key": "post_exec_load_trend",
            "requirement": "执行前条件闭环，但票面要求执行后观察负荷趋势窗口；不要使用“若复核异常”或“暂不终结”。",
        },
        {
            "key": "post_exec_protection_trace",
            "requirement": "执行前条件闭环，但票面要求执行后保留保护信号稳定记录；不要使用“若复核异常”或“暂不终结”。",
        },
        {
            "key": "post_exec_dispatch_receipt",
            "requirement": "执行前条件闭环，但调度要求执行后回传终结凭证；不要使用“若复核异常”或“暂不终结”。",
        },
        {
            "key": "post_exec_field_photo",
            "requirement": "执行前条件闭环，但要求执行后上传现场位置照片；不要使用“若复核异常”或“暂不终结”。",
        },
        {
            "key": "post_exec_alarm_window",
            "requirement": "执行前条件闭环，但要求执行后观察告警窗一个周期；不要使用“若复核异常”或“暂不终结”。",
        },
    ],
    "non_compliant": [
        {
            "key": "unclosed_safety_trace",
            "requirement": "关键安全措施只有口头交底或附件线索，发令前没有可追溯闭环；不要使用“为空”“待核”“缺失”等直白词。",
        },
        {
            "key": "unclosed_monitor_trace",
            "requirement": "现场监护责任只有转述或班组记录，发令前没有本人确认闭环；不要使用“为空”“待核”“缺失”等直白词。",
        },
        {
            "key": "ambiguous_object_trace",
            "requirement": "票头对象和操作步骤之间留下未消除的对象指向歧义；不要使用“错误”“违规”等直白词。",
        },
        {
            "key": "unclosed_permit_trace",
            "requirement": "调度许可只能追到口头转达或班组记录，发令前没有正式许可闭环；不要使用“为空”“待核”“缺失”等直白词。",
        },
        {
            "key": "unclosed_lockout_trace",
            "requirement": "防误闭锁或压板状态只有间接说明，发令前没有可复核证据；不要使用“为空”“待核”“缺失”等直白词。",
        },
        {
            "key": "unclosed_revision_trace",
            "requirement": "票面修订后没有留下发令前复诵确认链条；不要使用“为空”“待核”“缺失”等直白词。",
        },
    ],
}

DISALLOWED_IN_TICKET = ("合规", "违规", "不合规", "compliant", "non_compliant")
NONCOMPLIANT_DISALLOWED = ("为空", "待核", "缺失")
MONITORING_DISALLOWED = ("若复核异常", "暂不终结")


def parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def base_operation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if row.get("task_type") != "operation_ticket_check":
            continue
        if (row.get("metadata") or {}).get("augmentation_type"):
            continue
        output.append(row)
    return output


def checked_fields(source: dict[str, Any], variant_index: int) -> tuple[str, str, str]:
    checks = list((source.get("input") or {}).get("key_checks") or ["设备状态", "安全措施", "调度许可"])
    return (
        str(checks[variant_index % len(checks)]),
        str(checks[(variant_index + 1) % len(checks)]),
        str(checks[(variant_index + 2) % len(checks)]),
    )


def prompt_for(source: dict[str, Any], variant_index: int) -> str:
    payload = source.get("input") or {}
    obj = extract_operation_object(source)
    action = operation_action_from_text(source)
    primary, secondary, tertiary = checked_fields(source, variant_index)
    context = str(payload.get("operation_context") or "运行操作")
    cases = {
        label: TARGET_CASES[label][variant_index % len(TARGET_CASES[label])]
        for label in ("compliant", "compliant_with_monitoring", "non_compliant")
    }
    request = {
        "task": "生成三条中文电力操作票摘录 hard cases。只输出 JSON。",
        "base_context": {
            "operation_context": context,
            "object": obj,
            "action": action,
            "checks_to_mention": [primary, secondary, tertiary],
        },
        "global_constraints": [
            "ticket_text 只写票面摘录，不要出现合规、违规、不合规、标签、类别等答案词。",
            "每条 ticket_text 80 到 220 个中文字符。",
            "三条都要有相近的票据风格，避免一眼靠固定短语区分类别。",
            "不要引入新的设备名以外对象，不要编造具体变电站名称。",
            "rationale 用一句话说明标签依据，可以提到闭环、监护、许可、对象一致性。",
        ],
        "variants": [
            {
                "target_label": label,
                "case_key": case["key"],
                "requirement": case["requirement"],
            }
            for label, case in cases.items()
        ],
        "output_schema": {
            "variants": [
                {
                    "target_label": "compliant | compliant_with_monitoring | non_compliant",
                    "case_key": "复制输入中的 case_key",
                    "ticket_text": "中文票面摘录",
                    "rationale": "一句中文依据",
                }
            ]
        },
    }
    return json.dumps(request, ensure_ascii=False)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def validate_variant(variant: dict[str, Any], expected_label: str, expected_key: str) -> tuple[bool, str]:
    if variant.get("target_label") != expected_label:
        return False, "label_mismatch"
    if variant.get("case_key") != expected_key:
        return False, "case_key_mismatch"
    ticket_text = clean_text(variant.get("ticket_text"))
    rationale = clean_text(variant.get("rationale"))
    if len(ticket_text) < 70 or len(ticket_text) > 260:
        return False, "ticket_length"
    if not rationale:
        return False, "missing_rationale"
    if any(token in ticket_text for token in DISALLOWED_IN_TICKET):
        return False, "explicit_label_word"
    if expected_label == "non_compliant" and any(token in ticket_text for token in NONCOMPLIANT_DISALLOWED):
        return False, "explicit_negative_shortcut"
    if expected_label == "compliant_with_monitoring" and any(token in ticket_text for token in MONITORING_DISALLOWED):
        return False, "explicit_monitoring_shortcut"
    return True, ""


def call_llm(client: OpenAI, model: str, prompt: str, timeout: float, temperature: float) -> dict[str, Any] | None:
    resp = client.with_options(timeout=timeout).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是电力调度操作票数据集构造专家，只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=1600,
        response_format={"type": "json_object"},
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = resp.choices[0].message.content or ""
    return parse_json_object(content)


def row_from_variant(source: dict[str, Any], variant: dict[str, Any], variant_index: int) -> dict[str, Any]:
    label = str(variant["target_label"])
    case_key = str(variant["case_key"])
    row = copy.deepcopy(source)
    payload = copy.deepcopy(source.get("input") or {})
    payload["ticket_text"] = clean_text(variant["ticket_text"])
    if label == "compliant":
        payload["dispatch_permit_status"] = "已确认并回填"
        payload["monitoring_arrangement"] = "执行前监护、许可和关键检查均已闭环"
        payload["object_consistency"] = "一致"
    elif label == "compliant_with_monitoring":
        payload["dispatch_permit_status"] = "已确认并回填"
        payload["monitoring_arrangement"] = "执行前可放行，执行后仍需按票面跟踪指定证据"
        payload["object_consistency"] = "一致"
    else:
        payload["dispatch_permit_status"] = "发令前闭环证据不足"
        payload["monitoring_arrangement"] = "执行前关键责任链条未形成可追溯闭环"
        payload["object_consistency"] = "对象指向需复核" if "object" in case_key else "一致"
    payload["post_execution_followup"] = {
        "pending_evidence": case_key,
        "evidence_collection": "implicit_ticket_llm_hard_case",
        "window_minutes": 0 if label == "compliant" else 20,
        "fallback_action": "按票面依据处置",
    }

    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update(
        {
            "created_by": "augment_operation_implicit_ticket_llm.py",
            "augmentation_type": "operation_implicit_ticket_llm",
            "source_record_id": source["id"],
            "operation_group": operation_group(source),
            "counterfactual_label": label,
            "counterfactual_role": f"implicit_{label}",
            "implicit_case_key": case_key,
            "pair_id": f"{source['id']}__plus15_implicit_pair_{variant_index}",
            "variant_index": variant_index,
        }
    )
    row.update(
        {
            "id": f"{source['id']}__plus15_implicit_{label}_{case_key}_{variant_index}",
            "input": payload,
            "output": LABEL_OUTPUT[label],
            "compliance_label": label,
            "rationale": clean_text(variant["rationale"]),
            "metadata": metadata,
        }
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus14.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus15.jsonl")
    parser.add_argument("--report-json", default="reports/operation_implicit_ticket_llm_augmentation_v1.2_plus15.json")
    parser.add_argument("--report-md", default="reports/operation_implicit_ticket_llm_augmentation_v1.2_plus15.md")
    parser.add_argument("--log-jsonl", default="reports/operation_implicit_ticket_llm_augmentation_v1.2_plus15_log.jsonl")
    parser.add_argument("--max-sources", type=int, default=240)
    parser.add_argument("--seed", type=int, default=2035)
    parser.add_argument("--base-url", default=os.getenv("LOCAL_LLM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.getenv("LOCAL_LLM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--api-key", default=os.getenv("LOCAL_LLM_API_KEY", "not-needed"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--temperature", type=float, default=0.45)
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    sources = stable_shuffle(base_operation_rows(rows), args.seed)[: args.max_sources]
    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    ensure_dirs((ROOT / args.output).parent, (ROOT / args.report_json).parent)

    added: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    started = time.time()
    for idx, source in enumerate(sources):
        cases = {
            label: TARGET_CASES[label][idx % len(TARGET_CASES[label])]
            for label in ("compliant", "compliant_with_monitoring", "non_compliant")
        }
        prompt = prompt_for(source, idx)
        parsed: dict[str, Any] | None = None
        error = ""
        for attempt in range(args.retries + 1):
            try:
                parsed = call_llm(client, args.model, prompt, args.timeout, args.temperature)
                if parsed is not None:
                    break
                error = "parse_failed"
            except Exception as exc:  # noqa: BLE001 - record provider errors and retry.
                error = f"{type(exc).__name__}: {str(exc)[:240]}"
            time.sleep(1.0 + attempt)

        variants = parsed.get("variants") if isinstance(parsed, dict) else None
        accepted_for_source = 0
        reject_reasons: Counter[str] = Counter()
        if isinstance(variants, list):
            by_label = {str(item.get("target_label")): item for item in variants if isinstance(item, dict)}
            for label, case in cases.items():
                variant = by_label.get(label)
                if not variant:
                    reject_reasons[f"missing_{label}"] += 1
                    continue
                ok, reason = validate_variant(variant, label, case["key"])
                if not ok:
                    reject_reasons[reason] += 1
                    continue
                added.append(row_from_variant(source, variant, idx))
                accepted_for_source += 1
        else:
            reject_reasons[error or "missing_variants"] += 1

        event = {
            "source_id": source["id"],
            "variant_index": idx,
            "accepted": accepted_for_source,
            "reject_reasons": dict(reject_reasons),
        }
        events.append(event)
        with (ROOT / args.log_jsonl).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    output = list(rows) + added
    write_jsonl(ROOT / args.output, output)

    added_labels = Counter(row.get("compliance_label") for row in added)
    added_cases = Counter((row.get("metadata") or {}).get("implicit_case_key") for row in added)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "base_url": args.base_url,
        "model": args.model,
        "source_records_requested": len(sources),
        "added_records": len(added),
        "output_records": len(output),
        "added_label_counts": dict(added_labels),
        "added_case_counts": dict(added_cases),
        "events": events,
        "runtime_seconds": time.time() - started,
        "filters": {
            "ticket_text_disallowed": DISALLOWED_IN_TICKET,
            "non_compliant_disallowed": NONCOMPLIANT_DISALLOWED,
            "monitoring_disallowed": MONITORING_DISALLOWED,
        },
        "passed": bool(added),
    }
    write_json(ROOT / args.report_json, report)

    lines = [
        "# Operation Implicit-Ticket LLM Augmentation",
        "",
        f"- Input: `{args.input}`",
        f"- Output: `{args.output}`",
        f"- Model: `{args.model}`",
        f"- Source records requested: {len(sources)}",
        f"- Added records: {len(added)}",
        f"- Runtime seconds: {report['runtime_seconds']:.1f}",
        "",
        "## Added Label Counts",
        "",
    ]
    for label, count in sorted(added_labels.items()):
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Filters", ""])
    lines.append("- Removed ticket text with explicit label words.")
    lines.append("- Removed non-compliant ticket text using direct empty/pending/missing shortcuts.")
    lines.append("- Removed monitoring ticket text using direct abnormal/terminal-hold shortcuts.")
    Path(ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
