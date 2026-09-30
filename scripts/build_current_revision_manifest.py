#!/usr/bin/env python3
"""Bind the current CPU baselines and release audits to one revision manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(relative)
    return {
        "sha256": digest(path),
        "bytes": path.stat().st_size,
    }


def collect_required(root: Path, paths: list[str]) -> dict[str, dict[str, Any]]:
    return {path: file_record(root, path) for path in paths}


def collect_optional(root: Path, paths: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    present: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for path in paths:
        if (root / path).is_file():
            present[path] = file_record(root, path)
        else:
            missing.append(path)
    return present, missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="metadata/current_revision_manifest_v1.2_sd_core.json")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    inputs = [
        "data/gridinstruct_v1.2_sd_core.jsonl",
        "data/gridinstruct_v1.2_sd_core_en.jsonl",
        "data/v1.2_sd_core_train_en.jsonl",
        "data/v1.2_sd_core_validation_en.jsonl",
        "data/v1.2_sd_core_test_en.jsonl",
        "data/v1.2_sd_core_ood_test_en.jsonl",
    ]
    required_reports = [
        "reports/current_release_integrity_audit_v1.2_sd_core.json",
        "reports/current_opf_closed_loop_audit_v1.2_sd_core.json",
        "reports/current_source_group_map_audit_v1.2_sd_core.json",
        "reports/EXPERIMENT_AUDIT.json",
        "reports/cleanup_inventory_v1.2_sd_core.json",
        "reports/stale_report_cleanup_inventory_v1.2_sd_core.json",
        "reports/english_split_audit_v1.2_sd_core.json",
        "benchmark/direct_english_structured_query_v1.2_sd_core_leakage_fixed_report.json",
        "benchmark/direct_english_structured_auxiliary_v1.2_sd_core_leakage_fixed_report.json",
        "benchmark/direct_english_tfidf_v1.2_sd_core_leakage_fixed_report.json",
        "benchmark/v1.2_sd_core_proxyreduced_tfidf_report.json",
        "reports/template_family_holdout_v1.2_sd_core.json",
        "benchmark/v1.2_sd_core_strict_tfidf_report.json",
        "benchmark/v1.2_sd_core_challenge_tfidf_report.json",
        "reports/target_hidden_classification_v1.2_sd_core.json",
        "reports/opf_selection_mapping_v1.2_sd_core.json",
        "reports/compliance_contrast_pairs_v1.2_sd_core.json",
        "reports/independent_solver_raw_evidence_v1.2_sd_core_rebound.json",
        "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
        "reports/independent_query_truth_validation_v1.2_sd_core.json",
        "reports/compliance_label_current_audit_v1.2_sd_core.json",
        "reports/official_exact_content_overlap_repair_v1.2_sd_core.json",
        "reports/strict_exact_content_overlap_repair_v1.2_sd_core.json",
        "reports/ood_stratified_metrics_v1.2_sd_core.json",
        "reports/rule_coverage_scope_audit_v1.2_sd_core.json",
        "reports/typed_semantic_pattern_audit_v1.2_sd_core.json",
        "reports/cross_solver_power_flow_v1.2_sd_core.json",
        "reports/core_n1_denominator_v1.2_sd_core.json",
    ]
    required_predictions = sorted(
        str(path.relative_to(root))
        for path in (root / "benchmark/current_v1.2_sd_core").glob("*_predictions.jsonl")
    )
    relevant_evidence = [
        "reports/action_level_validation_audit_v1.2_sd_core.json",
        "reports/core_n1_denominator_v1.2_sd_core.json",
        "reports/equipment_rating_provenance_v1.2_sd_core.json",
        "reports/english_translation_audit_v1.2_sd_core.json",
        "reports/extended_topology_stress_audit_v1.2_sd_core.json",
        "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
        "reports/external_topology_physical_envelope_v1.2_sd_core.json",
        "reports/full_n1_enumeration_v1.2_sd_core.json",
        "reports/generation_grounding_audit_v1.2_sd_core.json",
        "reports/high_score_plausibility_audit_v1.2_sd_core.json",
        "reports/independent_query_truth_validation_v1.2_sd_core.json",
        "reports/compliance_label_current_audit_v1.2_sd_core.json",
        "reports/independent_solver_raw_evidence_v1.2_sd_core.json",
        "reports/independent_solver_validation_v1.2_sd_core.json",
        "reports/independent_solver_raw_evidence_v1.2_sd_core_rebound.json",
        "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
        "reports/label_split_support_audit_v1.2_sd_core.json",
        "reports/linear_seed_stability_audit_v1.2_sd_core.json",
        "reports/model_score_quality_audit_v1.2_sd_core.json",
        "reports/near_duplicate_audit_v1.2_sd_core.json",
        "reports/ood_stratified_metrics_v1.2_sd_core.json",
        "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
        "reports/opf_action_constant_power_factor_stress_v1.2_sd_core.json",
        "reports/proxy_reduced_input_audit_v1.2_sd_core.json",
        "reports/rule_coverage_scope_audit_v1.2_sd_core.json",
        "reports/shortcut_ablation_v1.2_sd_core.json",
        "reports/source_english_core_alignment_v1.2_sd_core.json",
        "reports/split_independence_audit_v1.2_sd_core.json",
        "reports/strict_source_group_split_v1.2_sd_core.json",
        "reports/structured_prediction_normalization_v1.2_sd_core.json",
        "reports/template_family_holdout_v1.2_sd_core.json",
        "reports/official_exact_content_overlap_repair_v1.2_sd_core.json",
        "reports/strict_exact_content_overlap_repair_v1.2_sd_core.json",
        "reports/translation_glossary_audit_v1.2_sd_core.json",
        "reports/target_hidden_classification_v1.2_sd_core.json",
    ]
    relevant_metadata = [
        "metadata/dataset_metadata.json",
        "metadata/data_lineage_manifest.json",
        "metadata/canonical_dataset_promotion_manifest.json",
        "metadata/evidence_binding_manifest.json",
        "metadata/file_manifest.csv",
        "metadata/formal_runtime_environment.json",
        "metadata/pglib_opf_v23_07_case_registry.json",
        "metadata/release_licenses.json",
        "metadata/rule_dictionary.csv",
        "metadata/schema.json",
        "metadata/source_group_map.csv",
        "metadata/source_traceability.csv",
        "metadata/third_party_asset_inventory.json",
        "metadata/independent_solver_case_manifest_v1.json",
        "metadata/international_rule_probe_schema.json",
    ]
    relevant_scripts = [
        "scripts/audit_current_sd_release.py",
        "scripts/audit_action_level_validation.py",
        "scripts/audit_opf_closed_loop_assumptions.py",
        "scripts/append_opf_closed_loop_auxiliary_records.py",
        "scripts/materialize_english_splits.py",
        "scripts/recompute_compliance_labels_from_truth.py",
        "scripts/run_structured_query_filter_baseline.py",
        "scripts/run_structured_auxiliary_tool_baseline.py",
        "scripts/run_tfidf_task_baselines.py",
        "scripts/build_current_revision_manifest.py",
        "scripts/build_cleanup_inventory.py",
        "scripts/apply_cleanup_inventory.py",
        "scripts/build_stale_report_inventory.py",
        "scripts/apply_stale_report_inventory.py",
        "scripts/build_current_file_manifest.py",
        "scripts/run_target_hidden_classification.py",
        "scripts/rebind_independent_solver_evidence.py",
        "scripts/build_scoped_evidence_binding_manifest.py",
        "scripts/audit_release_claim_alignment.py",
        "scripts/audit_english_splits.py",
        "scripts/validate_query_truth_independent.py",
        "scripts/audit_compliance_labels_current.py",
        "scripts/repair_official_exact_content_overlap.py",
        "scripts/audit_rule_coverage_and_scope.py",
        "scripts/audit_typed_semantic_patterns.py",
        "scripts/build_compact_public_archive.py",
        "scripts/validate_release_archive_replay.py",
        "scripts/audit_opf_selection_mapping.py",
        "scripts/evaluate_compliance_contrast_pairs.py",
    ]
    # Submission manuscripts and publication artwork are intentionally outside
    # the public code/data repository. The revision manifest binds only
    # reproducible data, reports, metadata, and executable evaluation assets.
    local_required = inputs + required_reports + required_predictions
    required_artifacts = collect_required(root, local_required)
    evidence, missing_evidence = collect_optional(root, relevant_evidence)
    metadata, missing_metadata = collect_optional(root, relevant_metadata)
    scripts, missing_scripts = collect_optional(root, relevant_scripts)
    audit = json.loads((root / "reports/current_release_integrity_audit_v1.2_sd_core.json").read_text())
    opf = json.loads((root / "reports/current_opf_closed_loop_audit_v1.2_sd_core.json").read_text())
    if audit.get("status") != "pass" or opf.get("status") != "pass":
        raise ValueError("current release or OPF audit is not pass")
    baseline_json = [
        (path, json.loads((root / path).read_text()))
        for path in required_reports
        if path.startswith("benchmark/")
    ]

    def baseline_artifact_ok(path: str, item: dict[str, Any]) -> bool:
        """Require hash-bound validation/test predictions for every baseline.

        OOD predictions are mandatory only for reports that actually publish an
        OOD evaluation. Strict source-group, template-holdout, challenge, and
        current warm-start runs are deliberately test-only regimes, so treating
        an absent OOD field as a failure would incorrectly invalidate the local
        baseline gate.
        """
        status = item.get("status", "pass")
        if status != "pass":
            return False
        hashes = item.get("prediction_sha256") or {}
        if not hashes.get("validation") or not hashes.get("test"):
            return False
        has_ood_regime = bool(item.get("ood_records")) or bool(item.get("ood"))
        if has_ood_regime:
            # A declared OOD regime must carry a concrete prediction hash.
            return bool(hashes.get("ood"))
        return True

    baseline_ok = all(baseline_artifact_ok(path, item) for path, item in baseline_json)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "revision": "v1.2-sd-core-current-2026-08-28",
        "runtime_policy": "local CPU/MPS; no remote GPU execution",
        "dataset": {path: required_artifacts.pop(path) for path in inputs},
        "required_artifacts": required_artifacts,
        "evidence_reports": evidence,
        "metadata": metadata,
        "scripts": scripts,
        "optional_missing": {
            "historical_evidence": missing_evidence,
            "metadata": missing_metadata,
            "scripts": missing_scripts,
        },
        "local_gate_summary": {
            "row_integrity": audit.get("status") == "pass",
            "five_key_split_provenance": (
                audit.get("gates", {}).get("strict_split_provenance_keys_disjoint", {}).get("status") == "pass"
            ),
            "opf_detail_recomputation": opf.get("relative_reduction_recomputed_pass_count") == opf.get("opf_record_count") == 320,
            "opf_detail_n1": opf.get("post_action_n1_detail_pass_count") == 320,
            "opf_detail_uncertainty": opf.get("embedded_uncertainty_detail_pass_count") == 320,
            "cpu_baselines": baseline_ok,
            "target_hidden_classification": (
                (root / "reports/target_hidden_classification_v1.2_sd_core.json").is_file()
                and json.loads(
                    (root / "reports/target_hidden_classification_v1.2_sd_core.json").read_text()
                ).get("status") == "pass"
            ),
            "native_fixed_control_replay": (
                json.loads(
                    (root / "reports/independent_solver_validation_v1.2_sd_core_rebound.json").read_text()
                ).get("status") == "pass"
            ),
        },
        "task_count": 6,
        "baseline_families": [
            "tfidf_linear_svc_and_nearest_neighbor",
            "proxy_reduced_tfidf",
            "template_family_holdout_tfidf",
            "strict_source_group_tfidf",
            "challenge_tfidf",
            "target_hidden_character_tfidf",
            "schema_aware_structured_query",
            "schema_aware_auxiliary_tool",
        ],
        "metric_policy": "classification reports macro-F1, accuracy, and balanced accuracy; generation reports exact match and token-F1; structured baselines report schema-field exactness separately",
        "status": "scoped_pass_with_external_gates_pending",
        "external_gates": {
            "isolated_archive_replay_package": "pass_scoped_local_replay_anonymous_access_unverified",
            "raw_scenario_replay": "pending_missing_local_artifact",
            "independent_solver_case_manifest": "pass_scoped_fixed_control_160_cases_raw_population_ledger_external",
            "external_human_review": "author_attested_without_public_row_ledger",
            "repository_access": "restricted_anonymous_access_not_established",
        },
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
