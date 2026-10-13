#!/usr/bin/env python3
"""Audit whether released records are grounded in declared rules and scenarios."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, write_json
from query_contract import execute_query
from validate_dataset import expected_compliance_label

SCENARIO_TASKS = {
    "regulation_compliance_check",
    "auxiliary_decision",
    "dispatcher_intent_tool_call",
    "intelligent_data_query",
}
CLASSIFICATION_OUTPUTS = {
    "compliant": {"合规", "compliant"},
    "compliant_with_monitoring": {"合规但需继续监视", "需监视复核", "compliant_with_monitoring"},
    "non_compliant": {"违规", "non_compliant"},
}


def norm_network(value: Any) -> str:
    return str(value or "").replace(" ", "").upper()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def output_matches_label(output: Any, label: Any) -> bool:
    label_text = str(label or "")
    output_text = str(output or "").strip()
    return output_text in CLASSIFICATION_OUTPUTS.get(label_text, {label_text})


def pass_status(ok: bool) -> str:
    return "pass" if ok else "fail"


def save_gate_figure(gates: dict[str, dict[str, Any]], path: Path) -> None:
    ensure_dirs(path.parent)
    labels = list(gates)
    values = [1 if gates[label]["status"] == "pass" else 0 for label in labels]
    colors = ["#54A24B" if value else "#E45756" for value in values]
    fig, ax = plt.subplots(figsize=(max(7.5, len(labels) * 0.8), 2.4), dpi=180)
    ax.bar(range(len(labels)), values, color=colors)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Gate state")
    ax.set_title("Generation Grounding Gates")
    ax.set_xticks(range(len(labels)), [label.replace("_", "\n") for label in labels], rotation=30, ha="right")
    ax.bar_label(ax.containers[0], labels=[gates[label]["status"].upper() for label in labels], padding=2, fontsize=8)
    ax.grid(axis="y", color="#D8DEE9", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def write_md(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Generation Grounding Audit",
        "",
        f"Generated: {payload['generated_at']}",
        f"Dataset: `{payload['dataset']}`",
        "",
        "## Verdict",
        "",
        f"- Overall status: `{payload['overall_status']}`",
        f"- Records audited: {payload['records']:,}",
        "",
        "## Gates",
        "",
    ]
    for name, gate in payload["gates"].items():
        lines.append(f"- {name}: `{gate['status']}` — {gate['detail']}")
    lines.extend(["", "## Task Counts", ""])
    for task, count in sorted(payload["by_task"].items()):
        lines.append(f"- {task}: {count:,}")
    lines.extend(["", "## Compliance Label Consistency", ""])
    c = payload["compliance_label_consistency"]
    lines.append(f"- Checked records: {c['checked']:,}")
    lines.append(f"- Matched records: {c['matched']:,}")
    lines.append(f"- Match rate: {c['match_rate']:.6f}")
    if c["examples"]:
        lines.extend(["", "### Example mismatches", ""])
        for item in c["examples"][:10]:
            lines.append(f"- `{item['id']}` expected={item['expected']} actual={item['actual']} action_category={item['action_category']}")
    lines.extend(["", "## Executable Query Consistency", ""])
    q = payload["query_execution_consistency"]
    lines.append(f"- Checked records: {q['checked']:,}")
    lines.append(f"- Matched records: {q['matched']:,}")
    lines.append(f"- Match rate: {q['match_rate']:.6f}")
    if q["examples"]:
        lines.extend(["", "### Example query mismatches", ""])
        for item in q["examples"][:10]:
            lines.append(f"- `{item['id']}` filter={item['filter']} expected={item['expected_preview']} actual={item['actual_preview']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--rules", default="rules/regulation_rules.json")
    parser.add_argument("--output-json", default="reports/generation_grounding_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/generation_grounding_audit_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core_grounding")
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.dataset)
    scenarios = {row["scenario_id"]: row for row in read_json(ROOT / args.scenarios)}
    rules = {row["rule_id"] for row in read_json(ROOT / args.rules)}
    by_task = Counter(row.get("task_type") for row in rows)

    invalid_rule_refs = []
    missing_scenario = []
    network_mismatch = []
    tool_plan_missing_scenario = []
    output_label_mismatch = []
    severity_mismatch = []
    compliance_checked = 0
    compliance_matched = 0
    compliance_examples = []
    query_checked = 0
    query_matched = 0
    query_examples = []

    for row in rows:
        for rid in row.get("source_regulation_ids") or []:
            if rid not in rules:
                invalid_rule_refs.append({"id": row.get("id"), "rule_id": rid})
        task = row.get("task_type")
        scenario_id = row.get("scenario_id") or row.get("source_simulation_case_id")
        scenario = scenarios.get(scenario_id) if scenario_id else None
        if task in SCENARIO_TASKS:
            if not scenario:
                missing_scenario.append({"id": row.get("id"), "scenario_id": scenario_id})
                continue
            if norm_network(row.get("network_model")) != norm_network(scenario.get("network_model")):
                network_mismatch.append({"id": row.get("id"), "row_network": row.get("network_model"), "scenario_network": scenario.get("network_model")})
            row_severity = (row.get("metadata") or {}).get("severity_level")
            if row_severity and row_severity != scenario.get("severity_level"):
                severity_mismatch.append({"id": row.get("id"), "row_severity": row_severity, "scenario_severity": scenario.get("severity_level")})
            for step in row.get("tool_plan") or []:
                args_obj = step.get("args") if isinstance(step, dict) else {}
                step_scenario = args_obj.get("scenario_id") if isinstance(args_obj, dict) else None
                if step_scenario and step_scenario not in scenarios:
                    tool_plan_missing_scenario.append({"id": row.get("id"), "tool": step.get("tool"), "scenario_id": step_scenario})
        if task == "regulation_compliance_check" and scenario:
            expected = expected_compliance_label(
                scenario,
                (row.get("input") or {}).get("proposed_action"),
                (row.get("metadata") or {}).get("action_category"),
            )
            if expected:
                compliance_checked += 1
                actual = row.get("compliance_label")
                if expected == actual:
                    compliance_matched += 1
                elif len(compliance_examples) < 50:
                    compliance_examples.append({
                        "id": row.get("id"),
                        "expected": expected,
                        "actual": actual,
                        "action_category": (row.get("metadata") or {}).get("action_category"),
                        "severity": scenario.get("severity_level"),
                    })
            if not output_matches_label(row.get("output"), row.get("compliance_label")):
                output_label_mismatch.append({"id": row.get("id"), "label": row.get("compliance_label"), "output": row.get("output")})
        if task == "operation_ticket_check":
            if not output_matches_label(row.get("output"), row.get("compliance_label")):
                output_label_mismatch.append({"id": row.get("id"), "label": row.get("compliance_label"), "output": row.get("output")})
        if task == "intelligent_data_query" and scenario:
            query = row.get("structured_query") or {}
            expected_result = execute_query(query, scenario)
            if expected_result is not None:
                query_checked += 1
                actual_result = row.get("query_result")
                if canonical(expected_result) == canonical(actual_result):
                    query_matched += 1
                elif len(query_examples) < 50:
                    query_examples.append({
                        "id": row.get("id"),
                        "filter": query.get("filter"),
                        "expected_preview": canonical(expected_result)[:300],
                        "actual_preview": canonical(actual_result)[:300],
                    })

    compliance_rate = compliance_matched / compliance_checked if compliance_checked else 0.0
    query_rate = query_matched / query_checked if query_checked else 0.0
    gates = {
        "rule_references_valid": {"status": pass_status(not invalid_rule_refs), "detail": f"invalid={len(invalid_rule_refs)}"},
        "scenario_links_valid": {"status": pass_status(not missing_scenario), "detail": f"missing={len(missing_scenario)}"},
        "network_links_consistent": {"status": pass_status(not network_mismatch), "detail": f"mismatch={len(network_mismatch)}"},
        "severity_metadata_consistent": {"status": pass_status(not severity_mismatch), "detail": f"mismatch={len(severity_mismatch)}"},
        "tool_plan_scenarios_valid": {"status": pass_status(not tool_plan_missing_scenario), "detail": f"missing={len(tool_plan_missing_scenario)}"},
        "classification_output_labels_consistent": {"status": pass_status(not output_label_mismatch), "detail": f"mismatch={len(output_label_mismatch)}"},
        "compliance_policy_recomputable": {"status": pass_status(compliance_rate >= 0.995), "detail": f"match_rate={compliance_rate:.6f}, checked={compliance_checked}"},
        "structured_queries_executable": {"status": pass_status(query_rate >= 0.995), "detail": f"match_rate={query_rate:.6f}, checked={query_checked}"},
    }
    overall = "pass" if all(gate["status"] == "pass" for gate in gates.values()) else "warn"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "records": len(rows),
        "by_task": dict(by_task),
        "overall_status": overall,
        "gates": gates,
        "invalid_rule_refs_examples": invalid_rule_refs[:50],
        "missing_scenario_examples": missing_scenario[:50],
        "network_mismatch_examples": network_mismatch[:50],
        "severity_mismatch_examples": severity_mismatch[:50],
        "tool_plan_missing_scenario_examples": tool_plan_missing_scenario[:50],
        "output_label_mismatch_examples": output_label_mismatch[:50],
        "compliance_label_consistency": {
            "checked": compliance_checked,
            "matched": compliance_matched,
            "match_rate": compliance_rate,
            "examples": compliance_examples,
        },
        "query_execution_consistency": {
            "checked": query_checked,
            "matched": query_matched,
            "match_rate": query_rate,
            "examples": query_examples,
        },
    }
    write_json(ROOT / args.output_json, payload)
    write_md(ROOT / args.output_md, payload)
    save_gate_figure(gates, ROOT / args.figure_dir / "grounding_gate_matrix.png")


if __name__ == "__main__":
    main()
