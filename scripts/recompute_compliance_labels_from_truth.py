#!/usr/bin/env python3
"""Recompute compliance labels from authoritative physical truth.

After scenario truth rebuild / migration, every regulation_compliance_check record
must carry the label implied by expected_compliance_label on the linked scenario.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from augment_compliance_counterfactual_actions import OUTPUT_BY_LABEL, rationale_for
from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json, write_jsonl
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
    parser.add_argument(
        "--output-dataset",
        default="data/gridinstruct_v1.2_sd_core_en_recomputed.jsonl",
        help="Immutable derived output. It must differ from --dataset and is not the promoted release file.",
    )
    parser.add_argument("--report-json", default="reports/compliance_label_recompute_v1.2_sd_core.json")
    args = parser.parse_args()

    input_path = (ROOT / args.dataset).resolve()
    output_path = (ROOT / args.output_dataset).resolve()
    if input_path == output_path:
        raise ValueError("compliance label recomputation must create a new immutable stage")
    if output_path == (ROOT / "data/gridinstruct_v1.2_sd_core.jsonl").resolve():
        raise ValueError("compliance label recomputation cannot write the promoted canonical dataset")

    scenario_path = ROOT / args.scenarios
    input_hash = sha256(input_path)
    rows = read_jsonl(input_path)
    original_ids = [str(row.get("id") or "") for row in rows]
    if not rows or any(not value for value in original_ids) or len(original_ids) != len(set(original_ids)):
        raise ValueError("recomputation input must be non-empty with unique non-empty IDs")
    scenarios = {str(row["scenario_id"]): row for row in read_json(scenario_path)}
    updated = 0
    missing_scenario = 0
    unresolved_category = 0
    transitions: Counter[tuple[str, str]] = Counter()

    for row in rows:
        if row.get("task_type") != "regulation_compliance_check":
            continue
        scenario_id = str(row.get("scenario_id") or "")
        scenario = scenarios.get(scenario_id)
        if scenario is None:
            missing_scenario += 1
            continue
        action = str((row.get("input") or {}).get("proposed_action") or "")
        category = str((row.get("metadata") or {}).get("action_category") or "") or None
        expected = expected_compliance_label(scenario, action, category)
        if expected is None:
            unresolved_category += 1
            continue
        # Re-derive the observed-issue evidence and the issue profile from the
        # authoritative scenario so input.observed_issues, metadata.issue_profile,
        # the compliance label, and the rationale all agree with the final
        # electrical truth. Previously only the label was recomputed when it
        # differed, which left stale "normal" profiles and "no violation"
        # rationales on records whose scenario had later become emergency.
        true_issues = list(active_security_issues(scenario))
        true_profile = issue_profile(scenario)
        payload = dict(row.get("input") or {})
        issues_changed = payload.get("observed_issues") != true_issues
        payload["observed_issues"] = true_issues
        row["input"] = payload
        metadata = dict(row.get("metadata") or {})
        profile_changed = metadata.get("issue_profile") != true_profile
        metadata["issue_profile"] = true_profile
        current = str(row.get("compliance_label") or "")
        label_changed = current != expected
        if label_changed:
            transitions[(current, expected)] += 1
            row["compliance_label"] = expected
            row["output"] = OUTPUT_BY_LABEL[expected]
            metadata["compliance_label_recomputed_by"] = "recompute_compliance_labels_from_truth.py"
            metadata["compliance_label_previous"] = current
        row["metadata"] = metadata
        row["rationale"] = rationale_for(row, action, expected)
        if label_changed or issues_changed or profile_changed:
            updated += 1

    output_ids = [str(row.get("id") or "") for row in rows]
    if output_ids != original_ids:
        raise RuntimeError("compliance label recomputation changed record IDs or order")
    write_jsonl(output_path, rows)
    output_hash = sha256(output_path)
    input_unchanged = sha256(input_path) == input_hash
    if not input_unchanged:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("recomputation input stage changed during processing")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if missing_scenario == 0 and unresolved_category == 0 else "fail",
        "dataset": args.dataset,
        "output_dataset": str(output_path.relative_to(ROOT)),
        "records": len(rows),
        "updated_compliance_labels": updated,
        "missing_scenario": missing_scenario,
        "unresolved_category": unresolved_category,
        "transitions": {f"{a}->{b}": n for (a, b), n in sorted(transitions.items())},
        "input_sha256": input_hash,
        "scenario_truth_sha256": sha256(scenario_path),
        "output_sha256": output_hash,
        "record_ids_preserved": True,
        "input_mutated": not input_unchanged,
        "transaction": {
            "committed": missing_scenario == 0 and unresolved_category == 0,
            "artifacts": {str(output_path.relative_to(ROOT)): output_hash},
        },
    }
    write_json(ROOT / args.report_json, report)
    print(report)
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
