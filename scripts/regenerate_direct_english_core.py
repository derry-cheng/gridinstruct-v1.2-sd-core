#!/usr/bin/env python3
"""Regenerate the released English view directly from typed contracts.

The previous release carried stale provenance fields from an earlier bilingual
materialisation path.  This renderer reconstructs all natural-language fields
from the typed input, labels, and structured outputs already present in the
canonical record.  It never reads a source-language string and never calls an
external translation service.  The output is written atomically so a failed
run cannot leave a partially materialised release.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT


CJK_RE = re.compile(r"[\u4e00-\u9fff]")
DIRECT_MODE = "direct_english_from_typed_contract"
CONTRACT_VERSION = "direct-en-v2"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        temp = Path(handle.name)
    temp.replace(path)


def text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, (list, tuple)):
        return ", ".join(text(item) for item in value)
    return str(value)


def issues_from_summary(summary: str) -> str:
    value = summary.lower()
    terms = []
    if "overload" in value or "loading" in value:
        terms.append("branch overload")
    if "low voltage" in value or "voltage drop" in value:
        terms.append("low voltage")
    if "high voltage" in value:
        terms.append("high voltage")
    return ", ".join(dict.fromkeys(terms)) or "no explicit violation"


def render_auxiliary(row: dict[str, Any]) -> None:
    inp = row.get("input") or {}
    issue = text(inp.get("issue_profile"), "normal")
    goal = text(inp.get("operator_goal"), "restore the operating margin")
    window = text(inp.get("decision_window"), "the current review window")
    mode = text(inp.get("response_mode"), "a conservative sequence")
    actions = [text(item) for item in (inp.get("available_actions") or [])]
    action_text = ", ".join(actions) if actions else "the permitted control actions"
    row["instruction"] = (
        f"For scenario {text(row.get('scenario_id'), 'the current scenario')}, organize auxiliary "
        f"actions for the {issue} condition. The objective is to {goal}; use {mode.lower()} "
        f"during {window.lower()} and keep the sequence reviewable. Available actions are {action_text}."
    )
    if issue == "voltage":
        first = "identify the affected voltage buses and apply the smallest suitable reactive or voltage-control adjustment"
    elif issue == "thermal":
        first = "identify the overloaded elements and reduce the corresponding transfer with a reversible redispatch"
    elif issue == "combined":
        first = "separate thermal and voltage constraints, then coordinate redispatch and reactive support"
    else:
        first = "verify the margin before selecting a reversible adjustment"
    row["output"] = (
        f"First, {first}. Next, execute only an available and reversible action, then rerun the power-flow "
        f"check and record the post-action state. Escalate only when the verified margin remains outside the "
        f"operating requirement; this sequence supports the objective to {goal}."
    )
    row["chosen_response"] = row["output"]
    row["rejected_response"] = (
        "Do not bypass the state check or apply an irreversible action before the post-action power-flow "
        "and voltage review."
    )
    row["rationale"] = (
        f"The response is constructed from the typed issue profile ({issue}), operator goal ({goal}), "
        f"review window ({window}), response mode ({mode}), and available-action contract. It therefore "
        "preserves a reversible action, an explicit verification step, and a defined escalation condition."
    )


def render_compliance(row: dict[str, Any]) -> None:
    inp = row.get("input") or {}
    action = text(inp.get("proposed_action"), "the proposed action")
    goal = text(inp.get("operator_goal"), "the stated operating objective")
    issues = inp.get("observed_issues") or []
    issue_text = ", ".join(text(item).replace("_", " ") for item in issues) or "no explicit violation"
    label = text(row.get("compliance_label"), text(row.get("output"), "unknown"))
    row["instruction"] = (
        f"Determine whether the proposed action \"{action}\" is {label.replace('_', ' ')} under the "
        f"current {issue_text} condition, and explain whether it supports {goal.lower()}."
    )
    if label == "compliant":
        conclusion = "The action can be used under the stated condition after the required operating check."
    elif label == "compliant_with_monitoring":
        conclusion = "The action is conditionally usable, provided that the post-action state is rechecked and monitored."
    else:
        conclusion = "The action must be withheld until the missing safeguard or operating condition is resolved."
    row["rationale"] = (
        f"The typed compliance label is {label}; the observed issues are {issue_text}, and the proposed "
        f"action is {action}. {conclusion}"
    )
    row["output"] = label


def render_intent(row: dict[str, Any]) -> None:
    inp = row.get("input") or {}
    summary = text(inp.get("grid_state_summary"))
    issue_text = issues_from_summary(summary)
    output = row.get("output") if isinstance(row.get("output"), dict) else {}
    intent = text(output.get("intent"), "mitigate_violations")
    slots = output.get("slots") if isinstance(output.get("slots"), dict) else {}
    priority = text(slots.get("priority"), "review")
    row["instruction"] = (
        f"For {text(row.get('scenario_id'), 'the current scenario')}, route the operator request to "
        f"{intent.replace('_', ' ')}. Address {issue_text} with {priority} priority and retain a "
        "post-action power-flow check."
    )
    row["rationale"] = (
        f"The typed intent contract is {intent} with priority {priority}. The routing request is grounded "
        f"in the scenario state ({summary}) and retains the structured slots and verification step."
    )


def render_query(row: dict[str, Any]) -> None:
    inp = row.get("input") or {}
    question = text(inp.get("question"))
    focus = text(inp.get("analysis_focus"), "the relevant operating constraint")
    purpose = text(inp.get("request_purpose"), "operational review")
    row["instruction"] = (
        f"For scenario {text(inp.get('scenario_id'), text(row.get('scenario_id'), 'the current scenario'))}, "
        f"answer the structured query for {purpose.lower()} with emphasis on {focus}. Preserve the complete "
        f"record set and the requested aggregation. Query wording: {question}"
    )
    output = row.get("output") if isinstance(row.get("output"), dict) else {}
    structured = output.get("structured_query") if isinstance(output.get("structured_query"), dict) else {}
    filter_expr = text(structured.get("filter"), "the declared filter")
    if filter_expr == "loading_percent > 100":
        evidence = "loading rates above 100 percent, together with the complete list and maximum loading rate"
    elif filter_expr == "vm_pu < 0.95":
        evidence = "bus voltages below 0.95 p.u."
    elif filter_expr == "violation_type != none":
        evidence = "violation type and severity"
    else:
        evidence = "the records selected by the declared filter"
    row["rationale"] = (
        f"The query is rendered from the typed scenario, request purpose, analysis focus, and structured "
        f"query contract ({text(structured.get('contract_version'), 'declared contract')}). It selects "
        f"{evidence}; the returned records and aggregation are kept unchanged."
    )


def render_ticket(row: dict[str, Any]) -> None:
    inp = row.get("input") or {}
    label = text(row.get("compliance_label"), text(row.get("output"), "unknown"))
    action = text(inp.get("ticket_text"), "the proposed operation ticket")
    context = text(inp.get("operation_context"), "the stated operating context")
    row["instruction"] = (
        f"Check whether the operation ticket is {label.replace('_', ' ')} for {context.lower()}. "
        f"Use the ticket text and verify the listed pre-action safeguards: {action}"
    )
    row["output"] = label
    row["rationale"] = (
        f"The typed ticket label is {label}. The decision is grounded in the equipment state, dispatch "
        f"permission, supervision arrangement, object consistency, and key checks supplied by the ticket contract."
    )


def render_rule_qa(row: dict[str, Any]) -> None:
    inp = row.get("input") or {}
    rule_id = text(inp.get("rule_id"), "the declared rule")
    summary = text(inp.get("rule_summary"), "the rule summary")
    audience = text(inp.get("audience"), "the reviewer")
    deliverable = text(inp.get("deliverable"), "a concise explanation")
    focus = text(inp.get("focus_area"), "the required evidence")
    evidence = ", ".join(text(item) for item in (inp.get("evidence_fields") or [])) or "the declared evidence fields"
    row["instruction"] = (
        f"For a {audience}, explain {rule_id} as {deliverable.lower()}, focusing on {focus}. "
        f"Use this typed rule summary: {summary}"
    )
    row["output"] = (
        f"{rule_id} states: {summary} Evidence to record includes {evidence}. "
        f"The answer should address {focus} for the stated audience."
    )
    row["rationale"] = (
        f"The answer is rendered directly from the typed rule contract {rule_id}, including its summary, "
        f"audience, deliverable, focus, and evidence fields ({evidence})."
    )


def render_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    out = copy.deepcopy(row)
    task = text(out.get("task_type"))
    if task == "auxiliary_decision":
        render_auxiliary(out)
    elif task == "regulation_compliance_check":
        render_compliance(out)
    elif task == "dispatcher_intent_tool_call":
        render_intent(out)
    elif task == "intelligent_data_query":
        render_query(out)
    elif task == "operation_ticket_check":
        render_ticket(out)
    elif task == "regulation_qa":
        render_rule_qa(out)
    else:
        raise ValueError(f"Unsupported task_type at row {index}: {task}")

    metadata = dict(out.get("metadata") or {})
    for key in ("source_language", "translation_status", "translation_cache_version", "english_rematerialized_from_promoted"):
        metadata.pop(key, None)
    metadata.update(
        {
            "language": "en",
            "language_contract_version": CONTRACT_VERSION,
            "generation_mode": DIRECT_MODE,
            "direct_renderer": "regenerate_direct_english_core.py",
        }
    )
    out["metadata"] = metadata
    return out


def has_cjk(value: Any) -> bool:
    if isinstance(value, str):
        return bool(CJK_RE.search(value))
    if isinstance(value, dict):
        return any(has_cjk(item) for item in value.values())
    if isinstance(value, list):
        return any(has_cjk(item) for item in value)
    return False


def stable_digest(rows: list[dict[str, Any]]) -> str:
    payload = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--report-json", default="reports/direct_english_materialization_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/direct_english_materialization_v1.2_sd_core.md")
    args = parser.parse_args()

    source = ROOT / args.input
    output = ROOT / args.output
    rows = read_jsonl(source)
    rendered = [render_row(row, i) for i, row in enumerate(rows)]
    cjk_locations = [str(row.get("id")) for row in rendered if has_cjk(row)]
    task_counts = Counter(str(row.get("task_type")) for row in rendered)
    mode_counts = Counter(str((row.get("metadata") or {}).get("generation_mode")) for row in rendered)
    report = {
        "status": "pass" if len(rendered) == len(rows) and not cjk_locations and mode_counts == {DIRECT_MODE: len(rows)} else "fail",
        "renderer": "regenerate_direct_english_core.py",
        "language_contract_version": CONTRACT_VERSION,
        "generation_mode": DIRECT_MODE,
        "source": str(source.relative_to(ROOT)),
        "output": str(output.relative_to(ROOT)),
        "records": len(rendered),
        "task_counts": dict(sorted(task_counts.items())),
        "generation_mode_counts": dict(mode_counts),
        "cjk_record_count": len(cjk_locations),
        "cjk_record_examples": cjk_locations[:20],
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_sha256": stable_digest(rendered),
        "external_translation_service_used": False,
        "source_language_fields_removed": True,
    }
    write_jsonl_atomic(output, rendered)
    (ROOT / args.report_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# Direct-English materialisation report",
        "",
        f"- Status: **{report['status']}**",
        f"- Records: **{report['records']:,}**",
        f"- Renderer: `{report['renderer']}` ({CONTRACT_VERSION})",
        f"- External translation service used: **{report['external_translation_service_used']}**",
        f"- CJK-containing records after rendering: **{report['cjk_record_count']}**",
        "",
        "The English view is regenerated from typed scenario, rule, label, and structured-output contracts. "
        "The release metadata contains no source-language or translation-status fields.",
    ]
    (ROOT / args.report_md).write_text("\n".join(md) + "\n", encoding="utf-8")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
