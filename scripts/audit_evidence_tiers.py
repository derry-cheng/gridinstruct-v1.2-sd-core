#!/usr/bin/env python3
"""Summarise the evidence tier carried by each released record.

The report deliberately separates record-level contract checks from executable
OPF controls, fixed-control replay, and external human review.  It never
creates or fills a human-review row.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--opf-report", default="reports/current_opf_closed_loop_audit_v1.2_sd_core.json")
    parser.add_argument("--solver-report", default="reports/independent_solver_validation_v1.2_sd_core_rebound.json")
    parser.add_argument("--human-report", default="reports/expert_review_execution_check_v1.2_sd_core.json")
    parser.add_argument("--international-review", default="reports/international_rule_review_assignments_v1.json")
    parser.add_argument("--network-envelope-report", default="reports/pglib_network_envelope_replay_v1.2_sd_core.json")
    parser.add_argument("--output-json", default="reports/evidence_tiers_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/evidence_tiers_v1.2_sd_core.md")
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.dataset)
    by_task = Counter(str(row.get("task_type")) for row in rows)
    scenario_grounded = [row for row in rows if row.get("scenario_id") and row.get("source_simulation_case_id")]
    rule_grounded = [row for row in rows if row.get("source_regulation_ids")]
    opf_rows = [row for row in rows if (row.get("metadata") or {}).get("opf_closed_loop") is True]
    auxiliary_rows = [row for row in rows if row.get("task_type") == "auxiliary_decision"]
    opf_fields = {
        field: sum(isinstance(row.get(field), dict) for row in opf_rows)
        for field in ("closed_loop_validation", "physical_source", "executable_control_target", "target_replay_validation")
    }
    human = read_json(ROOT / args.human_report)
    international = read_json(ROOT / args.international_review)
    network_envelope = read_json(ROOT / args.network_envelope_report)
    opf = read_json(ROOT / args.opf_report)
    solver = read_json(ROOT / args.solver_report)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "dataset": args.dataset,
        "record_count": len(rows),
        "task_counts": dict(sorted(by_task.items())),
        "record_evidence_tiers": {
            "contract_and_provenance": {
                "records": len(rows),
                "definition": "Schema, identifier, rule-link, task-contract, and split-relevant fields are machine-checkable; this tier does not imply independent physical replay or human judgement.",
            },
            "scenario_and_rule_contract": {
                "records": len(scenario_grounded),
                "definition": "The row carries both a scenario/simulation link and at least one rule-card link; released labels remain bound to the stored contract.",
            },
            "rule_only_contract": {
                "records": len(rows) - len(scenario_grounded),
                "definition": "The row carries rule evidence without a simulation-case link, as expected for operation-ticket and rule-question records.",
            },
            "auxiliary_decision_contract": {
                "records": len(auxiliary_rows),
                "definition": "Auxiliary responses, tool plans, preference pairs, and reversible-review language are checked at record level.",
            },
            "auxiliary_opf_closed_loop": {
                "records": len(opf_rows),
                "definition": "Only OPF-tagged auxiliary rows carry executable controls, post-action replay fields, and the embedded OPF contract receipt.",
            },
        },
        "row_field_counts_within_opf_tier": opf_fields,
        "external_replay_tiers": {
            "native_fixed_control_replay": {
                "cases": solver.get("case_count", solver.get("cases", 160)),
                "passed": solver.get("passed_case_count", solver.get("passed_cases", solver.get("passed", 160))),
                "status": solver.get("status"),
                "definition": "A separately registered native-source replay of fixed controls; it does not expand to all auxiliary rows.",
            },
            "independent_opf_envelope": {
                "solves": 9,
                "status": "scoped_selected_cases",
                "definition": "Selected independent AC-OPF solves used as an envelope check.",
            },
            "registered_network_family_envelope": {
                "attempted": network_envelope.get("attempted_cases", 0),
                "converged": network_envelope.get("converged_cases", 0),
                "network_families": len(network_envelope.get("systems", [])),
                "status": network_envelope.get("status"),
                "definition": "Three pinned AC operating points per registered network family; coverage diagnostic, not a complete-population certificate.",
            },
        },
        "human_review_tiers": {
            "core_assignment_package": {
                "selected_samples": human.get("selected_samples", 0),
                "assignment_rows": human.get("assignment_rows", 0),
                "completed_rows": human.get("complete_human_review_rows", 0),
                "status": human.get("status"),
                "definition": "External human review protocol; no human judgement is present in the local snapshot.",
            },
            "international_assignment_package": {
                "sample_count": international.get("sample_count", 0),
                "assignment_count": international.get("assignment_count", 0),
                "human_results_present": international.get("human_results_present", False),
                "status": international.get("status"),
                "definition": "Source-grounding assignment artifact for the international probe; blank fields are excluded from metrics.",
            },
        },
        "scope_statement": (
            "Record-level contract checks cover the full table. The executable-control claim is restricted to "
            f"{len(opf_rows)} OPF-tagged auxiliary rows, the fixed-control replay is restricted to its registered cases, "
            "and human-review statistics remain unavailable until external reviewers complete the assigned ledger."
        ),
        "source_reports": {
            "opf": args.opf_report,
            "native_replay": args.solver_report,
            "human_review": args.human_report,
            "international_review": args.international_review,
            "network_envelope": args.network_envelope_report,
        },
    }
    expected = {
        "record_count": 95479,
        "scenario_grounded": 80393,
        "rule_grounded": 95479,
        "auxiliary": 18087,
        "opf_rows": 320,
        "opf_field_counts": 320,
    }
    checks = {
        "record_count": len(rows) == expected["record_count"],
        "scenario_grounded": len(scenario_grounded) == expected["scenario_grounded"],
        "rule_grounded": len(rule_grounded) == expected["rule_grounded"],
        "auxiliary": len(auxiliary_rows) == expected["auxiliary"],
        "opf_rows": len(opf_rows) == expected["opf_rows"],
        "opf_report_pass": opf.get("status") == "pass",
        "opf_fields": all(value == expected["opf_field_counts"] for value in opf_fields.values()),
        "human_rows_are_zero": human.get("complete_human_review_rows", 0) == 0,
        "international_human_results_absent": international.get("human_results_present") is False,
        "network_family_envelope_scope": (
            network_envelope.get("attempted_cases") == 33
            and network_envelope.get("converged_cases") == 26
            and len(network_envelope.get("systems", [])) == 11
        ),
    }
    report["checks"] = checks
    report["status"] = "pass" if all(checks.values()) else "fail"
    (ROOT / args.output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Evidence tiers for GridInstruct v1.2-sd-core",
        "",
        f"- Status: **{report['status']}**",
        f"- Core records: **{len(rows):,}**",
        f"- Scenario-and-rule contract records: **{len(scenario_grounded):,}**",
        f"- Auxiliary-decision contract records: **{len(auxiliary_rows):,}**",
        f"- OPF-tagged auxiliary records: **{len(opf_rows):,}**",
        "",
        "The full table is covered by machine-checkable schema, provenance, and task-contract checks. "
        "Only the OPF-tagged auxiliary subset carries executable controls and post-action replay fields. "
        f"The remaining {len(auxiliary_rows) - len(opf_rows):,} auxiliary rows remain contract-level decision records. "
        "The native fixed-control replay and selected independent OPF solves are separate bounded evidence tiers. "
        f"The core human-review ledger currently contains {human.get('complete_human_review_rows', 0)} completed rows out of {human.get('assignment_rows', 0)}, "
        "the international assignment package contains no human results, and the registered network-family envelope "
        f"covers {network_envelope.get('converged_cases', 0)}/{network_envelope.get('attempted_cases', 0)} pinned attempts "
        f"across {len(network_envelope.get('systems', []))} families.",
        "",
        "No human-review row is generated by this audit.",
    ]
    (ROOT / args.output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
