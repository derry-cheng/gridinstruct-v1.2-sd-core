#!/usr/bin/env python3
"""Audit action-level consistency for simulation-linked decision records."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json
from validate_dataset import expected_compliance_label


VERIFY_TERMS = (
    "review",
    "recheck",
    "verify",
    "verification",
    "check",
    "monitor",
    "validate",
    "confirm",
    "re-evaluate",
    "reevaluate",
    "secondary correction",
    "reserve",
    "retain",
    "preserve",
)
REJECTED_RISK_TERMS = ("aggressive", "skip", "missing", "high-impact", "irreversible", "no room", "premature")
REQUIRED_AUX_TOOLS = {"run_power_flow", "run_opf_redispatch"}


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def tool_names(row: dict[str, Any]) -> set[str]:
    names = set()
    for step in row.get("tool_plan") or []:
        if isinstance(step, dict) and step.get("tool"):
            names.add(str(step["tool"]))
    return names


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Action-Level Validation Audit",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        f"- Scenario lookup available: `{report['scenario_lookup_available']}`",
        f"- Compliance recompute source: `{report['compliance_recompute_source']}`",
        f"- Auxiliary decision records: {report['auxiliary_decision_records']}",
        f"- Auxiliary records passing action-plan checks: {report['auxiliary_pass_rate']:.4f}",
        f"- Compliance records with recomputable labels: {report['compliance_recomputed_records']}",
        f"- Compliance label agreement: {report['compliance_label_agreement_rate']:.4f}",
        "",
        "## Issue Counts",
        "",
    ]
    for key, value in sorted(report["issue_counts"].items()):
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Scope",
            "",
            report["scope_statement"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument(
        "--recompute-source-stage",
        default="data/gridinstruct_v1.2_sd_core_en.jsonl",
        help="Construction-time recompute stage used as the release-integrity source when the remote scenarios file is unavailable.",
    )
    parser.add_argument("--report-json", default="reports/action_level_validation_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/action_level_validation_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    scenarios_path = ROOT / args.scenarios
    scenario_lookup_available = scenarios_path.exists()
    scenarios = (
        {row["scenario_id"]: row for row in read_json(scenarios_path)}
        if scenario_lookup_available
        else None
    )
    # Release-integrity source: the construction-time recompute stage (labels recomputed
    # from the authoritative scenario state).  Used when the remote scenarios file is
    # not mirrored locally, so the audit can still verify that the released labels are
    # exactly the scenario-recomputed labels with zero promotion drift.
    release_source = {}
    release_source_is_current = Path(args.recompute_source_stage).resolve() == (ROOT / args.dataset).resolve()
    if not scenario_lookup_available:
        with (ROOT / args.recompute_source_stage).open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("task_type") == "regulation_compliance_check":
                    release_source[str(row.get("id"))] = row.get("compliance_label")

    issue_counts: Counter[str] = Counter()
    issue_examples: list[dict[str, Any]] = []
    aux_total = 0
    aux_pass = 0
    compliance_total = 0
    compliance_recomputed = 0
    compliance_agree = 0

    for line_no, row in enumerate(iter_jsonl(ROOT / args.dataset), start=1):
        task = row.get("task_type")
        scenario_id = row.get("scenario_id")
        scenario = scenarios.get(scenario_id) if (scenarios is not None and scenario_id) else None
        if task == "auxiliary_decision":
            aux_total += 1
            row_issues = []
            if not scenario_id:
                row_issues.append("missing_or_invalid_scenario_link")
            elif scenario_lookup_available and scenario is None:
                row_issues.append("missing_or_invalid_scenario_link")
            elif not scenario_lookup_available:
                # Without the remote scenarios file, verify the scenario link is internally
                # consistent with the recorded simulation-case provenance.
                case_id = row.get("source_simulation_case_id")
                if not case_id or case_id != scenario_id:
                    row_issues.append("scenario_link_internal_inconsistency")
            names = tool_names(row)
            if not REQUIRED_AUX_TOOLS.issubset(names):
                row_issues.append("missing_required_decision_tools")
            output_text = str(row.get("output") or row.get("chosen_response") or "").lower()
            if not any(term in output_text for term in VERIFY_TERMS):
                row_issues.append("chosen_action_lacks_review_or_monitoring_step")
            chosen = row.get("chosen_response")
            rejected = row.get("rejected_response")
            if not chosen or not rejected or chosen == rejected:
                row_issues.append("invalid_preference_pair")
            rejected_text = " ".join(
                [
                    str(rejected or ""),
                    json.dumps(row.get("preference_rationale") or {}, ensure_ascii=False),
                ]
            ).lower()
            if not any(term in rejected_text for term in REJECTED_RISK_TERMS):
                row_issues.append("rejected_action_lacks_explicit_risk_reason")
            if row_issues:
                for issue in row_issues:
                    issue_counts[issue] += 1
                if len(issue_examples) < 50:
                    issue_examples.append({"line": line_no, "id": row.get("id"), "issues": row_issues})
            else:
                aux_pass += 1
        elif task == "regulation_compliance_check":
            compliance_total += 1
            if scenario_lookup_available:
                if scenario is None:
                    issue_counts["compliance_missing_or_invalid_scenario_link"] += 1
                    continue
                action = (row.get("input") or {}).get("proposed_action") if isinstance(row.get("input"), dict) else None
                action_category = (row.get("metadata") or {}).get("action_category")
                expected = expected_compliance_label(scenario, action, action_category)
                if expected is not None:
                    compliance_recomputed += 1
                    if expected == row.get("compliance_label"):
                        compliance_agree += 1
                    else:
                        issue_counts["compliance_label_mismatch"] += 1
                        if len(issue_examples) < 50:
                            issue_examples.append({"line": line_no, "id": row.get("id"), "expected": expected, "actual": row.get("compliance_label")})
            else:
                # Release-integrity comparison; without raw scenarios this is not an
                # independent physical recomputation.
                rid = str(row.get("id"))
                if rid in release_source:
                    compliance_recomputed += 1
                    if release_source[rid] == row.get("compliance_label"):
                        compliance_agree += 1
                    else:
                        issue_counts["compliance_release_drift"] += 1
                        if len(issue_examples) < 50:
                            issue_examples.append({"line": line_no, "id": rid, "expected": release_source[rid], "actual": row.get("compliance_label")})
                else:
                    issue_counts["compliance_missing_recompute_source"] += 1

    aux_pass_rate = aux_pass / max(aux_total, 1)
    compliance_label_agreement_rate = compliance_agree / max(compliance_recomputed, 1)
    if aux_pass_rate < 0.99 or compliance_label_agreement_rate < 0.999 or issue_counts:
        status = "fail"
    elif not scenario_lookup_available:
        # A release-table comparison detects promotion drift only.  It is not
        # an independent physical recomputation and must remain visibly warn.
        status = "warn_no_independent_scenario_recompute"
    else:
        status = "pass"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "dataset": args.dataset,
        "scenarios": args.scenarios,
        "scenario_lookup_available": scenario_lookup_available,
        "compliance_recompute_source": (
            "authoritative_scenarios_converged"
            if scenario_lookup_available
            else (
                "release_integrity_vs_current_release_rows"
                if release_source_is_current
                else "release_integrity_vs_historical_recompute_stage"
            )
        ),
        "auxiliary_decision_records": aux_total,
        "auxiliary_records_passing": aux_pass,
        "auxiliary_pass_rate": aux_pass_rate,
        "compliance_records": compliance_total,
        "compliance_recomputed_records": compliance_recomputed,
        "compliance_label_agreement_count": compliance_agree,
        "compliance_label_agreement_rate": compliance_label_agreement_rate,
        "issue_counts": dict(issue_counts),
        "issue_examples": issue_examples,
        "scope_statement": (
            "This audit verifies that action recommendations are tied to simulation scenarios, required validation tools, reversible-review wording, "
            "and recomputable compliance labels. When the authoritative scenarios file is mirrored locally, compliance labels are recomputed from "
            "the independently reconstructed scenario state; otherwise, the audit compares released labels with the selected local release-integrity "
            "source and does not claim an independent physical recomputation. It is not a closed-loop physical dispatch execution test."
        ),
    }
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.report_json, report)
    write_markdown(report, ROOT / args.report_md)
    if status == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
