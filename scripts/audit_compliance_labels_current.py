#!/usr/bin/env python3
"""Audit released compliance labels against the current scenario registry.

The audit recomputes the deterministic label, issue list, issue profile, and
rationale in memory.  It writes only a compact receipt; no derived JSONL stage
is created or treated as a release artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json
from validate_dataset import active_security_issues, expected_compliance_label, issue_profile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--report-json", default="reports/compliance_label_current_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/compliance_label_current_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    dataset_path = ROOT / args.dataset
    scenario_path = ROOT / args.scenarios
    scenarios = {str(row.get("scenario_id")): row for row in read_json(scenario_path)}
    rows = read_jsonl(dataset_path)
    checked = 0
    missing_scenarios: list[str] = []
    unresolved_categories: list[str] = []
    label_mismatches: list[dict[str, Any]] = []
    issue_mismatches: list[dict[str, Any]] = []
    profile_mismatches: list[dict[str, Any]] = []
    output_mismatches: list[dict[str, Any]] = []
    rationale_mismatches: list[dict[str, Any]] = []
    transitions: Counter[str] = Counter()

    for row in rows:
        if row.get("task_type") != "regulation_compliance_check":
            continue
        checked += 1
        record_id = str(row.get("id") or "")
        scenario_id = str(row.get("scenario_id") or "")
        scenario = scenarios.get(scenario_id)
        if scenario is None:
            missing_scenarios.append(record_id)
            continue
        input_obj = row.get("input") or {}
        metadata = row.get("metadata") or {}
        action = str(input_obj.get("proposed_action") or "")
        category = str(metadata.get("action_category") or "") or None
        expected = expected_compliance_label(scenario, action, category)
        if expected is None:
            unresolved_categories.append(record_id)
            continue
        current = str(row.get("compliance_label") or "")
        if current != expected:
            transitions[f"{current}->{expected}"] += 1
            if len(label_mismatches) < 20:
                label_mismatches.append({"id": record_id, "actual": current, "expected": expected})
        expected_issues = active_security_issues(scenario)
        actual_issues = input_obj.get("observed_issues") or []
        if actual_issues != expected_issues and len(issue_mismatches) < 20:
            issue_mismatches.append({"id": record_id, "actual": actual_issues, "expected": expected_issues})
        expected_profile = issue_profile(scenario)
        if metadata.get("issue_profile") != expected_profile and len(profile_mismatches) < 20:
            profile_mismatches.append(
                {"id": record_id, "actual": metadata.get("issue_profile"), "expected": expected_profile}
            )
        output_value = row.get("output")
        if output_value != current and len(output_mismatches) < 20:
            output_mismatches.append({"id": record_id, "actual": output_value, "expected": current})
        issue_text = ", ".join(str(item).replace("_", " ") for item in expected_issues) or "no explicit violation"
        expected_rationale = (
            f"The typed compliance label is {expected}; the observed issues are {issue_text}, and the proposed "
            f"action is {action}. "
            + (
                "The action can be used under the stated condition after the required operating check."
                if expected == "compliant"
                else "The action is conditionally usable, provided that the post-action state is rechecked and monitored."
                if expected == "compliant_with_monitoring"
                else "The action must be withheld until the missing safeguard or operating condition is resolved."
            )
        )
        if row.get("rationale") != expected_rationale and len(rationale_mismatches) < 20:
            rationale_mismatches.append({"id": record_id})

    mismatch_counts = {
        "label": sum(transitions.values()),
        "issue_list": len(issue_mismatches),
        "issue_profile": len(profile_mismatches),
        "output_label": len(output_mismatches),
        "rationale": len(rationale_mismatches),
    }
    # The examples above are capped for a compact receipt; retain exact totals
    # for every category so a truncated example list cannot look like a count.
    # Recompute exact non-label counts in a second light pass only if needed.
    exact_issue = exact_profile = exact_output = exact_rationale = 0
    if not missing_scenarios and not unresolved_categories:
        for row in rows:
            if row.get("task_type") != "regulation_compliance_check":
                continue
            scenario = scenarios[str(row.get("scenario_id"))]
            input_obj = row.get("input") or {}
            metadata = row.get("metadata") or {}
            action = str(input_obj.get("proposed_action") or "")
            category = str(metadata.get("action_category") or "") or None
            expected = expected_compliance_label(scenario, action, category)
            expected_issues = active_security_issues(scenario)
            exact_issue += int((input_obj.get("observed_issues") or []) != expected_issues)
            exact_profile += int(metadata.get("issue_profile") != issue_profile(scenario))
            current = str(row.get("compliance_label") or "")
            exact_output += int(row.get("output") != current)
            issue_text = ", ".join(str(item).replace("_", " ") for item in expected_issues) or "no explicit violation"
            expected_rationale = (
                f"The typed compliance label is {expected}; the observed issues are {issue_text}, and the proposed "
                f"action is {action}. "
                + (
                    "The action can be used under the stated condition after the required operating check."
                    if expected == "compliant"
                    else "The action is conditionally usable, provided that the post-action state is rechecked and monitored."
                    if expected == "compliant_with_monitoring"
                    else "The action must be withheld until the missing safeguard or operating condition is resolved."
                )
            )
            exact_rationale += int(row.get("rationale") != expected_rationale)
    mismatch_counts.update(
        {"issue_list": exact_issue, "issue_profile": exact_profile, "output_label": exact_output, "rationale": exact_rationale}
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass"
        if not missing_scenarios and not unresolved_categories and not any(mismatch_counts.values())
        else "fail",
        "dataset": args.dataset,
        "dataset_sha256": sha256(dataset_path),
        "scenarios": args.scenarios,
        "scenario_truth_sha256": sha256(scenario_path),
        "records": len(rows),
        "compliance_records_checked": checked,
        "scenario_count": len(scenarios),
        "missing_scenario_count": len(missing_scenarios),
        "unresolved_category_count": len(unresolved_categories),
        "mismatch_counts": mismatch_counts,
        "label_transitions": dict(transitions),
        "examples": {
            "missing_scenarios": missing_scenarios[:20],
            "unresolved_categories": unresolved_categories[:20],
            "label": label_mismatches,
            "issue_list": issue_mismatches,
            "issue_profile": profile_mismatches,
            "output_label": output_mismatches,
            "rationale": rationale_mismatches,
        },
        "scope": "in-memory recomputation against the current scenario registry; no derived dataset stage is retained",
    }
    write_json(ROOT / args.report_json, report)
    lines = [
        "# Current Compliance Label Audit",
        "",
        f"- Status: `{report['status']}`",
        f"- Records checked: {checked:,} of {len(rows):,}",
        f"- Scenario truth: `{args.scenarios}` ({len(scenarios):,} scenarios)",
        f"- Mismatches (label/issue/profile/output/rationale): {mismatch_counts['label']}/{mismatch_counts['issue_list']}/{mismatch_counts['issue_profile']}/{mismatch_counts['output_label']}/{mismatch_counts['rationale']}",
        "- Scope: in-memory recomputation; no derived JSONL stage is retained.",
    ]
    (ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
