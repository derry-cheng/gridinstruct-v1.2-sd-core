#!/usr/bin/env python3
"""Audit release-facing claims against the current GridInstruct evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json


REQUIRED_ENGLISH_FILES = {
    "data/gridinstruct_v1.2_sd_core_en.jsonl",
    "data/v1.2_sd_core_train_en.jsonl",
    "data/v1.2_sd_core_validation_en.jsonl",
    "data/v1.2_sd_core_test_en.jsonl",
    "data/v1.2_sd_core_ood_test_en.jsonl",
    "reports/english_translation_audit_v1.2_sd_core.json",
    "reports/english_split_audit_v1.2_sd_core.json",
    "reports/data_validation_v1.2_sd_core_en.json",
}

REQUIRED_POWER_REVIEW_FILES = {
    "reports/topology_instruction_expansion_v1.2_sd_core.json",
    "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    "reports/current_opf_closed_loop_audit_v1.2_sd_core.json",
    "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
    "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
    "reports/extended_topology_stress_audit_v1.2_sd_core.json",
    "reports/rule_coverage_scope_audit_v1.2_sd_core.json",
    "reports/translation_glossary_audit_v1.2_sd_core.json",
    "reports/action_level_validation_audit_v1.2_sd_core.json",
}

FORBIDDEN_POSITIVE_PATTERNS = {
    "regulatory_corpus": [
        "bilingual regulatory corpus",
        "regulatory corpus collected",
        "collected bilingual regulations",
    ],
    "real_grid_logs": [
        "contains real-world dispatch logs",
        "includes real-world dispatch logs",
        "contains real supervisory-control logs",
        "includes real supervisory-control logs",
        "real grid operating records are included",
    ],
    "solved_dispatch_reasoning": [
        "model solves dispatch reasoning",
        "dataset solves dispatch reasoning",
    ],
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


SUPERSEDED_DATASET_TOTALS = {86127, 89215, 93503}


def canonical_dataset_counts(path: Path) -> dict[str, Any]:
    task_counts: dict[str, int] = {}
    total = 0
    topology_grounded = 0
    rule_linked = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            total += 1
            task = str(row.get("task_type") or "")
            task_counts[task] = task_counts.get(task, 0) + 1
            if row.get("scenario_id") or row.get("source_simulation_case_id"):
                topology_grounded += 1
            if row.get("source_regulation_ids"):
                rule_linked += 1
    return {
        "total": total,
        "by_task": task_counts,
        "topology_grounded": topology_grounded,
        "rule_linked": rule_linked,
    }


def load_text(paths: list[Path]) -> str:
    chunks = []
    for path in paths:
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks).lower()


def normalized_integer_occurrences(text: str) -> set[int]:
    return {
        int(token.replace(",", ""))
        for token in re.findall(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?![\w.])", text)
    }


def check_pass(name: str, condition: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "pass" if condition else "fail", "detail": detail}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", default="metadata/dataset_metadata.json")
    parser.add_argument("--english-audit", default="reports/english_translation_audit_v1.2_sd_core.json")
    parser.add_argument("--english-split-audit", default="reports/english_split_audit_v1.2_sd_core.json")
    parser.add_argument("--expert-execution", default="reports/expert_review_execution_check_v1.2_sd_core.json")
    parser.add_argument("--high-score", default="reports/high_score_plausibility_audit_v1.2_sd_core.json")
    parser.add_argument("--split-audit", default="reports/split_independence_audit_v1.2_sd_core.json")
    parser.add_argument("--strict-split", default="reports/strict_source_group_split_v1.2_sd_core.json")
    parser.add_argument("--topology-instruction-expansion", default="reports/frozen_external_topology_migration_v2.json")
    parser.add_argument("--extended-topology", default="reports/extended_topology_stress_audit_v1.2_sd_core.json")
    parser.add_argument("--rule-coverage", default="reports/rule_coverage_scope_audit_v1.2_sd_core.json")
    parser.add_argument("--translation-glossary", default="reports/translation_glossary_audit_v1.2_sd_core.json")
    parser.add_argument("--action-validation", default="reports/action_level_validation_audit_v1.2_sd_core.json")
    parser.add_argument("--opf-closed-loop", default="reports/current_opf_closed_loop_audit_v1.2_sd_core.json")
    parser.add_argument("--opf-candidate", default="reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json")
    parser.add_argument("--opf-uncertainty", default="reports/opf_action_uncertainty_stress_v1.2_sd_core.json")
    parser.add_argument("--independent-solver", default="reports/independent_solver_validation_v1.2_sd_core_rebound.json")
    parser.add_argument("--evidence-binding", default="metadata/evidence_binding_manifest.json")
    parser.add_argument("--json-out", default="reports/release_claim_alignment_audit_v1.2_sd_core.json")
    parser.add_argument("--md-out", default="reports/release_claim_alignment_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    metadata = read_json(ROOT / args.metadata)
    english_audit = read_json(ROOT / args.english_audit)
    english_split_audit = read_json(ROOT / args.english_split_audit)
    expert_execution = read_json(ROOT / args.expert_execution)
    high_score = read_json(ROOT / args.high_score)
    split_audit = read_json(ROOT / args.split_audit)
    strict_split = read_json(ROOT / args.strict_split)
    topology_instruction = read_json(ROOT / args.topology_instruction_expansion)
    extended_topology = read_json(ROOT / args.extended_topology)
    rule_coverage = read_json(ROOT / args.rule_coverage)
    translation_glossary = read_json(ROOT / args.translation_glossary)
    action_validation = read_json(ROOT / args.action_validation)
    opf_closed_loop = read_json(ROOT / args.opf_closed_loop)
    opf_candidate = read_json(ROOT / args.opf_candidate)
    opf_uncertainty = read_json(ROOT / args.opf_uncertainty)
    independent_solver = read_json(ROOT / args.independent_solver)
    evidence_binding = read_json(ROOT / args.evidence_binding)
    dataset = metadata.get("dataset", {})
    record_summary = metadata.get("record_summary", {})
    source_dataset_path = ROOT / str(
        dataset.get("source_dataset_path") or "data/gridinstruct_v1.2_sd_core.jsonl"
    )
    canonical_counts = canonical_dataset_counts(source_dataset_path)
    expected_counts = {
        canonical_counts["total"],
        *(
            int(value)
            for value in canonical_counts["by_task"].values()
            if int(value) >= 1000
        ),
        canonical_counts["topology_grounded"],
        canonical_counts["rule_linked"],
    }
    manuscript_paths = [
        ROOT / "paper/scientific_data/manuscript.md",
        ROOT / "paper/scientific_data_latex/main.tex",
    ]
    manuscript_text = load_text(manuscript_paths)
    manuscript_large_numbers = normalized_integer_occurrences(manuscript_text)
    known_dataset_counts = SUPERSEDED_DATASET_TOTALS | expected_counts
    stale_dataset_counts = sorted(
        (manuscript_large_numbers & known_dataset_counts)
        - expected_counts
    )
    required_manuscript_counts = {
        canonical_counts["total"],
        *canonical_counts["by_task"].values(),
        canonical_counts["topology_grounded"],
        canonical_counts["rule_linked"],
    }
    missing_manuscript_counts = sorted(required_manuscript_counts - manuscript_large_numbers)
    positive_pattern_hits = []
    for group, patterns in FORBIDDEN_POSITIVE_PATTERNS.items():
        for pattern in patterns:
            if pattern in manuscript_text:
                positive_pattern_hits.append({"group": group, "pattern": pattern})

    filled_human_rows = int(expert_execution.get("filled_human_review_rows") or 0)
    external_review_completed = expert_execution.get("status") == "external_human_review_completed"
    manuscript_declares_human_review_incomplete = any(
        phrase in manuscript_text
        for phrase in (
            "external human-review outcomes are not included",
            "independent human review has not been completed",
            "human-review outcomes are not included",
        )
    )
    human_review_claim_consistent = (
        external_review_completed and not manuscript_declares_human_review_incomplete
    ) or (
        not external_review_completed
        and manuscript_declares_human_review_incomplete
        and filled_human_rows == 0
    )
    high_score_guidance = " ".join(high_score.get("claim_guidance", [])).lower()

    checks = [
        check_pass(
            "evidence_binding_is_current_and_passed",
            evidence_binding.get("status") == "pass"
            and evidence_binding.get("invalid_evidence_count") == 0,
            f"status={evidence_binding.get('status')}, invalid={evidence_binding.get('invalid_evidence_count')}",
        ),
        check_pass(
            "manuscript_dataset_counts_match_current_canonical_release",
            not stale_dataset_counts
            and not missing_manuscript_counts
            and int(dataset.get("record_count") or 0) == canonical_counts["total"]
            and (record_summary.get("by_task") or {}) == canonical_counts["by_task"],
            (
                f"canonical={canonical_counts}, stale={stale_dataset_counts}, "
                f"missing={missing_manuscript_counts}"
            ),
        ),
        check_pass(
            "external_topology_output_bound_to_passing_transaction",
            extended_topology.get("status") == "pass"
            and (extended_topology.get("publication_transaction") or {}).get("output_written_by_this_run") is True
            and bool((extended_topology.get("publication_transaction") or {}).get("output_sha256")),
            f"transaction={extended_topology.get('publication_transaction')}",
        ),
        check_pass(
            "english_dataset_is_primary",
            dataset.get("dataset_path") == "data/gridinstruct_v1.2_sd_core_en.jsonl",
            f"dataset_path={dataset.get('dataset_path')}",
        ),
        check_pass(
            "source_dataset_is_retained_separately",
            dataset.get("source_dataset_path") == "data/gridinstruct_v1.2_sd_core.jsonl",
            f"source_dataset_path={dataset.get('source_dataset_path')}",
        ),
        check_pass(
            "language_position_is_english_derived",
            "english-derived" in str(dataset.get("language", "")).lower() or "english natural-language" in str(dataset.get("language", "")).lower(),
            f"language={dataset.get('language')}",
        ),
        check_pass(
            "english_translation_audit_passed",
            english_audit.get("status") == "pass"
            and english_audit.get("record_count_match") is True
            and english_audit.get("records_with_cjk", 1) == 0,
            f"status={english_audit.get('status')}, records_with_cjk={english_audit.get('records_with_cjk')}",
        ),
        check_pass(
            "english_split_audit_passed",
            english_split_audit.get("status") == "pass" and english_split_audit.get("failed_split_count") == 0,
            f"status={english_split_audit.get('status')}, failed={english_split_audit.get('failed_split_count')}",
        ),
        check_pass(
            "human_review_claim_matches_execution_state",
            human_review_claim_consistent,
            (
                f"status={expert_execution.get('status')}, "
                f"filled_human_review_rows={filled_human_rows}, "
                f"manuscript_declares_incomplete={manuscript_declares_human_review_incomplete}"
            ),
        ),
        check_pass(
            "high_score_claims_are_qualified",
            "trainability" in high_score_guidance and "near-perfect" in high_score_guidance,
            "high-score claim guidance explicitly limits interpretation",
        ),
        check_pass(
            "standard_and_strict_split_scopes_are_separate",
            split_audit.get("status") == "pass" and strict_split.get("status") == "pass",
            f"standard={split_audit.get('status')}, strict={strict_split.get('status')}",
        ),
        check_pass(
            "topology_instruction_expansion_passed",
            topology_instruction.get("status") == "pass"
            and (
                (
                    topology_instruction.get("new_records_appended", 0) > 0
                    and topology_instruction.get("source_records_after", 0) <= dataset.get("record_count", 0)
                )
                or (
                    topology_instruction.get("migrated_record_count", 0) > 0
                    and topology_instruction.get("record_count_after", 0) <= dataset.get("record_count", 0)
                )
            ),
            (
                f"status={topology_instruction.get('status')}, "
                f"new_records={topology_instruction.get('new_records_appended')}, "
                f"records_after_topology={topology_instruction.get('source_records_after')}, "
                f"final_records={dataset.get('record_count')}"
            ),
        ),
        check_pass(
            "extended_topology_stress_passed",
            extended_topology.get("status") == "pass"
            and extended_topology.get("convergence_rate", 0) >= 0.5
            and len(extended_topology.get("network_summaries", [])) >= 4,
            f"status={extended_topology.get('status')}, convergence_rate={extended_topology.get('convergence_rate')}",
        ),
        check_pass(
            "rule_coverage_scope_audit_passed",
            rule_coverage.get("status") == "pass"
            and rule_coverage.get("invalid_rule_link_count") == 0
            and rule_coverage.get("rule_count", 0) >= 5,
            f"status={rule_coverage.get('status')}, rules={rule_coverage.get('rule_count')}",
        ),
        check_pass(
            "translation_glossary_audit_passed",
            translation_glossary.get("status") == "pass"
            and translation_glossary.get("english_records_with_cjk") == 0
            and translation_glossary.get("stable_field_mismatch_count") == 0,
            f"status={translation_glossary.get('status')}, cjk={translation_glossary.get('english_records_with_cjk')}",
        ),
        check_pass(
            "action_level_validation_scoped",
            action_validation.get("status") in {"pass", "warn_no_independent_scenario_recompute"}
            and action_validation.get("auxiliary_pass_rate", 0) >= 0.99
            and action_validation.get("compliance_label_agreement_rate", 0) >= 0.999,
            (
                f"status={action_validation.get('status')}, "
                f"auxiliary_pass_rate={action_validation.get('auxiliary_pass_rate')}, "
                "independent_physical_recompute=false"
            ),
        ),
        check_pass(
            "opf_robust_candidate_register_passed",
            opf_candidate.get("status") == "pass"
            and opf_candidate.get("screened_pass_count") == 120
            and opf_candidate.get("target_passed_candidates") == 120
            and bool(opf_candidate.get("hard_gates"))
            and all((opf_candidate.get("hard_gates") or {}).values()),
            (
                f"status={opf_candidate.get('status')}, "
                f"passed={opf_candidate.get('screened_pass_count')}, "
                f"target={opf_candidate.get('target_passed_candidates')}"
            ),
        ),
        check_pass(
            "opf_closed_loop_auxiliary_passed",
            opf_closed_loop.get("status") == "pass"
            and (
                (
                    opf_closed_loop.get("new_records_appended", 0) > 0
                    and opf_closed_loop.get("source_records_after", 0) == dataset.get("record_count")
                    and opf_closed_loop.get("opf_error_count", 1) == 0
                )
                or (
                    opf_closed_loop.get("opf_record_count") == 320
                    and opf_closed_loop.get("tool_sequence_pass_count") == 320
                    and opf_closed_loop.get("closed_loop_field_pass_count") == 320
                    and opf_closed_loop.get("relative_reduction_recomputed_pass_count") == 320
                )
            )
            and opf_closed_loop.get("mean_relative_violation_reduction", 1.0) >= 0.9,
            (
                f"status={opf_closed_loop.get('status')}, "
                f"new_records={opf_closed_loop.get('new_records_appended')}, "
                f"opf_errors={opf_closed_loop.get('opf_error_count')}"
            ),
        ),
        check_pass(
            "opf_uncertainty_complete_denominator_passed",
            opf_uncertainty.get("status") == "pass"
            and opf_uncertainty.get("integrity_status") == "pass"
            and opf_uncertainty.get("robustness_status") == "pass"
            and (
                opf_uncertainty.get("registered_denominator") or {}
            ).get("observed_total_case_count")
            == 7680
            and (
                opf_uncertainty.get("registered_denominator") or {}
            ).get("complete")
            is True,
            (
                f"status={opf_uncertainty.get('status')}, "
                f"denominator={opf_uncertainty.get('registered_denominator')}"
            ),
        ),
        check_pass(
            "native_independent_solver_replay_passed",
            independent_solver.get("status") == "pass"
            and independent_solver.get("case_count") == 160
            and independent_solver.get("passed_case_count") == 160
            and not independent_solver.get("errors")
            and not independent_solver.get("case_errors"),
            (
                f"status={independent_solver.get('status')}, "
                f"cases={independent_solver.get('case_count')}, "
                f"passed={independent_solver.get('passed_case_count')}"
            ),
        ),
        check_pass(
            "no_forbidden_positive_claim_patterns",
            not positive_pattern_hits,
            f"hits={positive_pattern_hits}",
        ),
    ]

    status = "pass" if all(row["status"] == "pass" for row in checks) else "fail"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "evidence_binding": args.evidence_binding,
        "evidence_binding_sha256": file_sha256(ROOT / args.evidence_binding),
        "checks": checks,
        "required_english_files": sorted(REQUIRED_ENGLISH_FILES),
        "required_power_review_files": sorted(REQUIRED_POWER_REVIEW_FILES),
        "opf_closed_loop_audit": args.opf_closed_loop,
        "opf_candidate_audit": args.opf_candidate,
        "opf_uncertainty_audit": args.opf_uncertainty,
        "independent_solver_audit": args.independent_solver,
        "positive_pattern_hits": positive_pattern_hits,
    }
    write_json(ROOT / args.json_out, report)

    lines = ["# Release Claim Alignment Audit", "", f"Generated: {report['generated_at']}", f"Status: `{status}`", "", "| Check | Status | Detail |", "| --- | --- | --- |"]
    for row in checks:
        lines.append(f"| {row['name']} | {row['status']} | {row['detail']} |")
    ensure_dirs((ROOT / args.md_out).parent)
    (ROOT / args.md_out).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
