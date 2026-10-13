#!/usr/bin/env python3
"""Create classification-only proxy-reduced JSONL splits for baseline audits."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl, write_json, write_jsonl

LABEL_FIELDS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}
DROP_KEYS = {
    "metadata",
    "rationale",
    "source_regulation_ids",
    "source_simulation_case_id",
    "scenario_id",
    "network_model",
    "tool_plan",
    "slots",
    "structured_query",
    "query_result",
    "chosen_response",
    "rejected_response",
    "preference_rationale",
}
# Structured input sub-fields that are near-perfect proxies for the closed
# label and therefore must NOT survive the proxy-reduced view. Previously these
# were retained, which made the proxy-reduced split bit-identical to the
# standard split (a no-op stress test). They are dropped here so the proxy
# reduced view forces a model to read the natural-language evidence (ticket
# text / utterance / proposed action) instead of reading off the label.
PROXY_INPUT_FIELDS = {
    "dispatch_permit_status",
    "monitoring_arrangement",
    "monitor_status",
    "object_consistency",
    "key_checks",
    "pre_action_state",
    "operation_context",
    "issue_profile",
    "action_category",
    "severity",
    "severity_level",
    "template_family",
    "augmentation_type",
    "variant_index",
    "validation_status",
    "routing_policy",
    "routing_priority",
    "post_execution_followup",
    "available_actions",
    "action_contract",
    "opf_validation_summary",
}
DROP_INPUT_TOKENS = (
    "label",
    "intent",
    "source",
    "scenario_id",
    "simulation",
    "template",
    "metadata",
    "validation_status",
    "tool_plan",
    "expected",
    "permit",
    "monitor",
    "consistency",
    "augmentation",
    "variant",
    "followup",
    "action_category",
    "issue_profile",
    "severity",
)


def _is_proxy_field(key: Any) -> bool:
    key_text = str(key).strip().lower()
    if key_text in PROXY_INPUT_FIELDS:
        return True
    return any(token in key_text for token in DROP_INPUT_TOKENS)


def scrub_input(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if _is_proxy_field(key):
                continue
            out[key] = scrub_input(child)
        return out
    if isinstance(value, list):
        return [scrub_input(item) for item in value]
    return value


def reduce_row(row: dict[str, Any]) -> dict[str, Any] | None:
    task = row.get("task_type")
    if task not in LABEL_FIELDS:
        return None
    label_field = LABEL_FIELDS[str(task)]
    reduced = {
        "id": row.get("id"),
        "task_stage": row.get("task_stage"),
        "task_type": task,
        "instruction": row.get("instruction"),
        "input": scrub_input(row.get("input") or {}),
        "output": row.get("output"),
        label_field: row.get(label_field),
    }
    return {key: value for key, value in reduced.items() if value is not None}


def convert_split(src: str, dst: str) -> dict[str, Any]:
    rows = read_jsonl(ROOT / src)
    reduced = [item for row in rows if (item := reduce_row(row)) is not None]
    write_jsonl(ROOT / dst, reduced)
    return {
        "source": src,
        "output": dst,
        "source_records": len(rows),
        "output_records": len(reduced),
        "task_counts": dict(Counter(row["task_type"] for row in reduced)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", default="reports/proxy_reduced_split_manifest_v1.2_sd_core.json")
    args = parser.parse_args()
    specs = [
        ("data/v1.2_sd_core_train.jsonl", "data/v1.2_sd_core_proxyreduced_train.jsonl"),
        ("data/v1.2_sd_core_validation.jsonl", "data/v1.2_sd_core_proxyreduced_validation.jsonl"),
        ("data/v1.2_sd_core_test.jsonl", "data/v1.2_sd_core_proxyreduced_test.jsonl"),
        ("data/v1.2_sd_core_challenge_train.jsonl", "data/v1.2_sd_core_challenge_proxyreduced_train.jsonl"),
        ("data/v1.2_sd_core_challenge_validation.jsonl", "data/v1.2_sd_core_challenge_proxyreduced_validation.jsonl"),
        ("data/v1.2_sd_core_challenge_test.jsonl", "data/v1.2_sd_core_challenge_proxyreduced_test.jsonl"),
        ("data/v1.2_sd_core_strict_train.jsonl", "data/v1.2_sd_core_strict_proxyreduced_train.jsonl"),
        ("data/v1.2_sd_core_strict_validation.jsonl", "data/v1.2_sd_core_strict_proxyreduced_validation.jsonl"),
        ("data/v1.2_sd_core_strict_test.jsonl", "data/v1.2_sd_core_strict_proxyreduced_test.jsonl"),
    ]
    conversions = [convert_split(src, dst) for src, dst in specs if (ROOT / src).exists()]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if len(conversions) == len(specs) else "warn",
        "policy": "classification-only JSONL derived from released splits; metadata, source ids, scenario ids, tool plans, slots, rationale, and explicit proxy keys are removed from model input records",
        "conversions": conversions,
        "missing_sources": [src for src, _ in specs if not (ROOT / src).exists()],
    }
    write_json(ROOT / args.output_json, report)
    print({"status": report["status"], "files": len(conversions)})


if __name__ == "__main__":
    main()
