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
    row["instruction"] = (
        f"Determine whether the proposed action \"{action}\" satisfies the applicable operating "
        f"requirements under the current {issue_text} condition, and explain whether it supports "
        f"{goal.lower()}."
    )
    label = text(row.get("compliance_label"), text(row.get("output"), "unknown"))
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
    target_issue = text(slots.get("target_issue"), "the active operating condition")
    scenario = text(row.get("scenario_id"), "the current scenario")
    variant = int((row.get("metadata") or {}).get("variant_index") or 0) % 12
    templates = (
        "For {scenario}, identify the dispatch tool family required by the operator request. Address {issue} with {priority} priority and retain a post-action power-flow check.",
        "Select the suitable dispatch analysis route for {scenario}. The request concerns {issue}; use {priority} priority and keep the post-action power-flow check.",
        "Determine the dispatch-tool route for {scenario}, focusing on {issue}. Preserve {priority} priority and an explicit post-action power-flow check.",
        "Given {scenario}, assign the operator request to its appropriate dispatch analysis family. Review {issue} with {priority} priority and retain the verification step.",
        "Route the current operating request for {scenario} through the suitable dispatch tool family. The active concern is {issue}, with {priority} priority and post-action verification.",
        "For the operating state {scenario}, choose the dispatch analysis family that matches the request. Cover {issue} at {priority} priority and retain the power-flow check.",
        "Identify an appropriate dispatch-tool route for {scenario}. Account for {issue}, preserve {priority} priority, and require a post-action power-flow check.",
        "The operator request refers to {scenario}. Select the dispatch analysis family, address {issue} with {priority} priority, and keep the verification step explicit.",
        "Assign {scenario} to the suitable dispatch tool family under the current request. Review {issue} at {priority} priority and repeat the power-flow check after action.",
        "For scenario {scenario}, prepare the dispatch analysis route requested by the operator. Emphasize {issue}, use {priority} priority, and retain post-action verification.",
        "Choose the dispatch-tool family for {scenario} from the operating request. The review covers {issue} with {priority} priority and includes a power-flow check after action.",
        "Route the operator request associated with {scenario} to the appropriate dispatch analysis family. Address {issue} at {priority} priority and preserve post-action verification.",
    )
    row["instruction"] = templates[variant].format(scenario=scenario, issue=issue_text, priority=priority)
    # The base intent records use a neutral routing request so that the label
    # is not copied from an auxiliary field.  Counterfactual intent records
    # must retain an observable request distinction: their augmentation
    # contract deliberately changes the requested route while holding the
    # operating state fixed.  Collapsing all three routes to the neutral text
    # creates identical prompt/input pairs with conflicting targets, which is
    # an invalid supervised-learning contract.  The following typed phrases
    # expose the request semantics without adding the gold label as a field.
    if isinstance(inp, dict):
        metadata = row.get("metadata") or {}
        requested_intent = text(metadata.get("counterfactual_intent"), intent)
        intent_phrases = {
            "mitigate_violations": [
                (
                    "The operator requests corrective measures to reduce the active violation before review.",
                    "The current shift supervisor prioritizes violation mitigation followed by a post-action review.",
                    "Follow the dispatch review sequence and retain a post-action power-flow check after mitigation.",
                ),
                (
                    "The operator asks for an action plan that brings the active violation back within its limit.",
                    "The current shift supervisor places corrective handling before the final operating review.",
                    "Form a reversible corrective plan, then verify the post-action power-flow state.",
                ),
                (
                    "The requested route is to handle the reported operating violation and then check the result.",
                    "The shift review gives priority to reducing the reported violation before closure.",
                    "Select a dispatch action for violation reduction and preserve the post-action check.",
                ),
            ],
            "diagnose_and_dispatch": [
                (
                    "The operator requests diagnosis of the active condition followed by dispatch planning.",
                    "The current shift supervisor prioritizes diagnosis before selecting a dispatch action.",
                    "Identify the operating cause first, then route the request to dispatch planning and review.",
                ),
                (
                    "The operator asks to locate the source of the reported condition before an action is prepared.",
                    "The current shift supervisor requires cause identification before dispatch execution.",
                    "Trace the operating condition, prepare the suitable dispatch route, and retain verification.",
                ),
                (
                    "The requested route starts with an operating-state diagnosis and continues with an executable plan.",
                    "The shift review places source diagnosis before any corrective dispatch decision.",
                    "Diagnose the state first, then prepare a reviewable dispatch action and check its result.",
                ),
            ],
            "security_check_and_redispatch": [
                (
                    "The operator requests a security check followed by redispatch planning.",
                    "The current shift supervisor prioritizes security verification before redispatch.",
                    "Complete the declared security check, then prepare redispatch and retain a post-action check.",
                ),
                (
                    "The operator asks for a security assessment before the generation redispatch decision.",
                    "The current shift supervisor requires the operating constraints to be checked before redispatch.",
                    "Verify security constraints first, then route the request to redispatch and post-action review.",
                ),
                (
                    "The requested route is to verify the operating margin and then form a redispatch recommendation.",
                    "The shift review places security verification ahead of the redispatch action.",
                    "Run the security check, form the redispatch plan, and retain a power-flow check afterward.",
                ),
            ],
        }
        if requested_intent in intent_phrases:
            phrase_index = int(hashlib.sha256(text(row.get("id")).encode("utf-8")).hexdigest()[:8], 16) % len(intent_phrases[requested_intent])
            utterance, priority, policy = intent_phrases[requested_intent][phrase_index]
        else:
            utterance = (
                "The operator requests dispatch analysis for the current operating condition "
                "and a reviewable recommendation."
            )
            priority = (
                "The current shift supervisor requires a safe dispatch analysis followed by "
                "a reviewable recommendation."
            )
            policy = (
                "Follow the declared dispatch review sequence and retain a post-action power-flow check."
            )
        utterance = f"{utterance} The requested operational focus is {target_issue}."
        priority = f"{priority} The review should address {target_issue}."
        inp["utterance"] = utterance
        inp["routing_priority"] = priority
        inp["routing_policy"] = policy
        row["input"] = inp
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
    action = text(inp.get("ticket_text"), "the proposed operation ticket")
    context = text(inp.get("operation_context"), "the stated operating context")
    row["instruction"] = (
        f"Check whether the operation ticket satisfies the applicable safeguards for {context.lower()}. "
        f"Use the ticket text and verify the listed pre-action safeguards: {action}"
    )
    label = text(row.get("compliance_label"), text(row.get("output"), "unknown"))
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
        f"Use the applicable source rule and identify the required evidence fields: {evidence}."
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

    # A rule summary is the target evidence for regulation-QA.  Keeping it in
    # the exposed input turns the task into answer copying, so it is removed
    # after the target has been rendered from the typed rule contract.
    if task == "regulation_qa" and isinstance(out.get("input"), dict):
        out["input"].pop("rule_summary", None)

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
    refresh_rule_evidence(out)
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


def display_path(path: Path) -> str:
    """Use a repository-relative path when possible, otherwise an absolute path."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def refresh_rule_evidence(row: dict[str, Any]) -> None:
    """Rebind rule-evidence receipts after direct rendering changes surfaces."""

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return
    bindings = metadata.get("rule_link_evidence_fields")
    if not isinstance(bindings, dict):
        return
    for evidence_rows in bindings.values():
        if not isinstance(evidence_rows, list):
            continue
        for evidence in evidence_rows:
            if not isinstance(evidence, dict):
                continue
            pointer = str(evidence.get("json_pointer") or "")
            if not pointer.startswith("/"):
                continue
            value: Any = row
            for part in pointer.lstrip("/").split("/"):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    value = None
                    break
            if value not in (None, "", [], {}):
                evidence["normalized_evidence"] = json.dumps(
                    value, ensure_ascii=False, sort_keys=True
                ).lower()


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
        "source": display_path(source),
        "output": display_path(output),
        "records": len(rendered),
        "task_counts": dict(sorted(task_counts.items())),
        "generation_mode_counts": dict(mode_counts),
        "cjk_record_count": len(cjk_locations),
        "cjk_record_examples": cjk_locations[:20],
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        # Keep the byte-level receipt and canonical contract digest explicit.
        # Earlier reports called the latter `output_sha256`, which was easy to
        # confuse with the file hash when auditing the release.
        "output_sha256": None,
        "output_file_sha256": None,
        "output_contract_sha256": stable_digest(rendered),
        "external_translation_service_used": False,
        "source_language_fields_removed": True,
    }
    write_jsonl_atomic(output, rendered)
    report["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    report["output_file_sha256"] = report["output_sha256"]
    (ROOT / args.report_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# Direct-English materialisation report",
        "",
        f"- Status: **{report['status']}**",
        f"- Records: **{report['records']:,}**",
        f"- Renderer: `{report['renderer']}` ({CONTRACT_VERSION})",
        f"- External translation service used: **{report['external_translation_service_used']}**",
        f"- CJK-containing records after rendering: **{report['cjk_record_count']}**",
        f"- Output file SHA-256: `{report['output_sha256']}`",
        f"- Output contract SHA-256: `{report['output_contract_sha256']}`",
        "",
        "The English view is regenerated from typed scenario, rule, label, and structured-output contracts. "
        "The release metadata contains no source-language or translation-status fields.",
    ]
    (ROOT / args.report_md).write_text("\n".join(md) + "\n", encoding="utf-8")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
