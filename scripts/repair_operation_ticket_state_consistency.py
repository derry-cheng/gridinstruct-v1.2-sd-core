#!/usr/bin/env python3
"""Repair operation-ticket pre-action states without changing labels."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone

from gridinstruct_utils import ROOT, read_jsonl, write_json, write_jsonl


ACTION_STATES = {
    "由冷备用转热备用": "冷备用状态",
    "倒至旁路运行": "主回路运行、旁路备用状态",
    "恢复运行": "检修结束待恢复状态",
    "转检修": "运行状态",
    "停役": "运行状态",
    "投入": "冷备用状态",
}


def detect_action(row: dict) -> str | None:
    text = json.dumps(
        {"instruction": row.get("instruction"), "ticket_text": (row.get("input") or {}).get("ticket_text")},
        ensure_ascii=False,
    )
    for action in ACTION_STATES:
        if action in text:
            return action
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output-dataset", default=None)
    parser.add_argument("--report-json", default="reports/operation_ticket_state_consistency_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/operation_ticket_state_consistency_v1.2_sd_core.md")
    args = parser.parse_args()

    path = ROOT / args.dataset
    output_path = ROOT / (args.output_dataset or args.dataset)
    rows = read_jsonl(path)
    changed = 0
    actions = Counter()
    unresolved = []
    contradictions = []
    for row in rows:
        if row.get("task_type") != "operation_ticket_check":
            continue
        action = detect_action(row)
        if action is None:
            unresolved.append(row.get("id"))
            continue
        actions[action] += 1
        expected = ACTION_STATES[action]
        input_obj = row.get("input") if isinstance(row.get("input"), dict) else {}
        obj_text = str(input_obj.get("equipment_state") or "设备")
        obj = obj_text.split("处于", 1)[0] if "处于" in obj_text else "设备"
        context = str(input_obj.get("operation_context") or "当前运行方式")
        new_state = f"{obj}处于{expected}，{context}，保护按当前方式投入。"
        if input_obj.get("equipment_state") != new_state or input_obj.get("pre_action_state") != expected:
            input_obj["equipment_state"] = new_state
            input_obj["pre_action_state"] = expected
            row["input"] = input_obj
            row.setdefault("metadata", {})["state_consistency_repair"] = "pre_action_state_v2"
            changed += 1
        if action in {"投入", "由冷备用转热备用"} and "运行状态" in str(input_obj.get("equipment_state")):
            contradictions.append(row.get("id"))

    repair_status = "pass" if not unresolved and not contradictions else "fail"
    if repair_status == "pass":
        write_jsonl(output_path, rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": repair_status,
        "dataset": args.dataset,
        "output_dataset": str(output_path.relative_to(ROOT)),
        "operation_ticket_records": sum(actions.values()),
        "changed_records": changed,
        "records_by_action": dict(actions),
        "unresolved_action_count": len(unresolved),
        "unresolved_action_examples": unresolved[:50],
        "remaining_state_contradiction_count": len(contradictions),
        "remaining_state_contradiction_examples": contradictions[:50],
    }
    write_json(ROOT / args.report_json, report)
    (ROOT / args.report_md).write_text(
        "\n".join(
            [
                "# Operation Ticket State Consistency",
                "",
                f"- Status: `{report['status']}`",
                f"- Records checked: {report['operation_ticket_records']}",
                f"- Records repaired: {report['changed_records']}",
                f"- Unresolved actions: {report['unresolved_action_count']}",
                f"- Remaining contradictions: {report['remaining_state_contradiction_count']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
