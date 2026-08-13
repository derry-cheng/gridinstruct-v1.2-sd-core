#!/usr/bin/env python3
"""Build the hash-bound local evidence manifest for the current SD candidate.

The manifest deliberately separates local reproducibility from external
submission inputs.  It never upgrades a missing raw scenario ledger, human
review assignment, or public accession from a summary report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


LOCAL_BINDINGS = [
    "data/gridinstruct_v1.2_sd_core.jsonl",
    "data/gridinstruct_v1.2_sd_core_en.jsonl",
    "data/v1.2_sd_core_train_en.jsonl",
    "data/v1.2_sd_core_validation_en.jsonl",
    "data/v1.2_sd_core_test_en.jsonl",
    "data/v1.2_sd_core_ood_test_en.jsonl",
    "reports/current_release_integrity_audit_v1.2_sd_core.json",
    "reports/current_opf_closed_loop_audit_v1.2_sd_core.json",
    "reports/current_source_group_map_audit_v1.2_sd_core.json",
    "reports/current_quality_snapshot_v1.2_sd_core.json",
    "reports/high_score_plausibility_audit_v1.2_sd_core.json",
    "reports/group_cluster_bootstrap_v1.2_sd_core.json",
    "reports/linear_seed_stability_audit_v1.2_sd_core.json",
    "reports/model_score_quality_audit_v1.2_sd_core.json",
    "reports/near_duplicate_audit_v1.2_sd_core.json",
    "reports/split_independence_audit_v1.2_sd_core.json",
    "reports/strict_source_group_split_v1.2_sd_core.json",
    "reports/template_holdout_split_v1.2_sd_core.json",
    "reports/action_level_validation_audit_v1.2_sd_core.json",
    "reports/source_english_alignment_v1.2_sd_core.json",
    "reports/english_translation_audit_v1.2_sd_core.json",
    "reports/english_split_audit_v1.2_sd_core.json",
    "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
    "reports/opf_action_constant_power_factor_stress_v1.2_sd_core.json",
    "reports/independent_solver_raw_evidence_v1.2_sd_core_rebound.json",
    "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
    "reports/target_hidden_classification_v1.2_sd_core.json",
    "reports/target_hidden_classification_replay_20260812.json",
    "reports/instruction_surface_balanced_split_v1.2_sd_core.json",
    "reports/instruction_surface_balanced_surface_audit_v1.2_sd_core.json",
    "reports/near_neighbor_balanced_surface_audit_v1.2_sd_core.json",
    "reports/exact_surface_component_split_v1.2_sd_core.json",
    "reports/exact_surface_target_hidden_classification_v1.2_sd_core.json",
    "reports/independent_opf_envelope_v1.2_sd_core.json",
    "reports/pglib_network_envelope_replay_v1.2_sd_core.json",
    "reports/template_family_holdout_v1.2_sd_core.json",
    "reports/opf_structural_contract_audit_v1.2_sd_core.json",
    "data/v1.2_sd_core_template_family_holdout_train_ids.jsonl",
    "data/v1.2_sd_core_template_family_holdout_validation_ids.jsonl",
    "data/v1.2_sd_core_template_family_holdout_test_ids.jsonl",
    "reports/near_duplicate_audit_v1.2_sd_core.json",
    "reports/equipment_rating_provenance_v1.2_sd_core.json",
    "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
    "reports/opf_candidate_register_v1.2_sd_core.json",
    "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    "reports/core_n1_denominator_v1.2_sd_core.json",
    "reports/shortcut_ablation_v1.2_sd_core.json",
    "reports/evidence_reconciliation_v1.2_sd_core.json",
    "reports/claim_evidence_delta_2026-08-12.json",
    "reports/manuscript_final_audit_20260812.json",
    "reports/manuscript_metric_bindings_v1.2_sd_core.json",
    "reports/manuscript_metric_bindings_v1.2_sd_core.md",
    "paper/scientific_data_latex/PAPER_CLAIM_AUDIT.json",
    "paper/scientific_data_latex/main.pdf",
    "paper/scientific_data_latex/LATEX_BUILD_REPORT.json",
    "paper/scientific_data_latex/LATEX_BUILD_REPORT.md",
    "paper/scientific_data_latex/build/main.pdf",
    "paper/scientific_data_latex/build_embedded/main_with_bbl.pdf",
    "paper/scientific_data_latex/GridInstruct_SD_scientific_data_official_template.pdf",
    "paper/scientific_data_latex/GridInstruct_SD_scientific_data_latex_source.zip",
    "figures/sd_core_quality/fig_exact_surface_target_hidden.png",
    "benchmark/current_v1.2_sd_core/tfidf_standard_report.json",
    "benchmark/current_v1.2_sd_core/tfidf_strict_report.json",
    "benchmark/current_v1.2_sd_core/tfidf_template_holdout_report.json",
    "benchmark/current_v1.2_sd_core/tfidf_proxyreduced_report.json",
    "benchmark/current_v1.2_sd_core/tfidf_challenge_report.json",
    "benchmark/instruction_surface_balanced_v1.2_sd_core/tfidf_report.json",
    "benchmark/instruction_surface_balanced_v1.2_sd_core/query_report.json",
    "benchmark/instruction_surface_balanced_v1.2_sd_core/auxiliary_report.json",
    "benchmark/instruction_surface_balanced_v1.2_sd_core/calibrated_tfidf_report.json",
    "benchmark/near_neighbor_free_v1.2_sd_core/tfidf_report.json",
    "benchmark/near_neighbor_free_v1.2_sd_core/structured_query_report.json",
    "benchmark/near_neighbor_free_v1.2_sd_core/structured_auxiliary_report.json",
    "benchmark/near_neighbor_free_multiseed_v1.2_sd_core/classification_operation_ticket_check_five_seed_summary.json",
    "benchmark/near_neighbor_free_multiseed_v1.2_sd_core/classification_regulation_compliance_check_five_seed_summary.json",
    "benchmark/near_neighbor_free_multiseed_v1.2_sd_core/classification_dispatcher_intent_tool_call_five_seed_summary.json",
    "benchmark/near_neighbor_free_multiseed_v1.2_sd_core/classification_operation_ticket_check_seed13_report.json",
    "benchmark/near_neighbor_free_multiseed_v1.2_sd_core/classification_regulation_compliance_check_seed42_report.json",
    "benchmark/near_neighbor_free_multiseed_v1.2_sd_core/classification_dispatcher_intent_tool_call_seed13_report.json",
    "benchmark/v1.2_sd_core_boundary_challenge_tfidf_report.json",
    "benchmark/v1.2_sd_core_challenge_proxyreduced_tfidf_report.json",
    "benchmark/v1.2_sd_core_rule_context_tfidf_report.json",
    "benchmark/v1.2_sd_core_tfidf_report.json",
    "benchmark/v1.2_sd_core_strict_tfidf_report.json",
    "benchmark/v1.2_sd_core_template_holdout_tfidf_report.json",
    "benchmark/current_v1.2_sd_core/transformer_operation_ticket_current_report.json",
    "metadata/independent_solver_case_manifest_v1.json",
    "metadata/dataset_metadata.json",
    "metadata/data_lineage_manifest.json",
    "metadata/third_party_asset_inventory.json",
    "paper/scientific_data_latex/main.tex",
    "paper/scientific_data_latex/references.bib",
    "figures/sd_core/fig_target_hidden_classification.png",
    "figures/sd_core_publication/fig0_framework.drawio",
    "figures/sd_core_publication/fig_opf_detail.drawio",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_status(path: Path) -> str | None:
    if path.suffix.lower() != ".json":
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return str(payload.get("status") or payload.get("verdict") or "") or None


def bind(root: Path, relative: str) -> dict[str, Any] | None:
    path = root / relative
    if not path.is_file():
        return None
    return {
        "path": relative,
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "reported_status": payload_status(path),
        "bound": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", default="metadata/evidence_binding_manifest.json"
    )
    args = parser.parse_args()
    root = ROOT

    bindings: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for relative in LOCAL_BINDINGS:
        record = bind(root, relative)
        if record is None:
            missing.append(relative)
        else:
            bindings[relative] = record

    local_gate_summary = {
        "row_integrity": bindings.get(
            "reports/current_release_integrity_audit_v1.2_sd_core.json", {}
        ).get("reported_status")
        == "pass",
        "embedded_opf": bindings.get(
            "reports/current_opf_closed_loop_audit_v1.2_sd_core.json", {}
        ).get("reported_status")
        == "pass",
        "source_group_provenance": bindings.get(
            "reports/current_source_group_map_audit_v1.2_sd_core.json", {}
        ).get("reported_status")
        == "pass",
        "split_independence": bindings.get(
            "reports/split_independence_audit_v1.2_sd_core.json", {}
        ).get("reported_status")
        == "pass",
        "template_holdout": bindings.get(
            "reports/template_holdout_split_v1.2_sd_core.json", {}
        ).get("reported_status")
        == "pass",
        "target_hidden_classification": bindings.get(
            "reports/target_hidden_classification_v1.2_sd_core.json", {}
        ).get("reported_status")
        == "pass",
        "native_fixed_control_replay": bindings.get(
            "reports/independent_solver_validation_v1.2_sd_core_rebound.json", {}
        ).get("reported_status")
        == "pass",
    }
    local_failures = [name for name, passed in local_gate_summary.items() if not passed]
    external_gates = {
        "isolated_archive_replay_package": {
            "passed": False,
            "kind": "external",
            "status": "pending_archive_materialization",
            "scope": "compressed release archive replay; kept outside the working tree to respect the 4 GB project budget",
        },
        "raw_scenario_replay_ledger": {
            "passed": False,
            "kind": "external",
            "status": "pending_raw_scenario_arrays_and_construction_ledger",
            "scope": "full scenario-generation population; not inferred from fixed-control summaries",
        },
        "external_human_review": {
            "passed": False,
            "kind": "external",
            "status": "pending_0_of_1600_assignments",
            "scope": "double independent review of the registered sample",
        },
        "public_repository_and_doi": {
            "passed": False,
            "kind": "external",
            "status": "pending_deposition",
            "scope": "versioned public data and code accession",
        },
        "final_author_funding_metadata": {
            "passed": False,
            "kind": "external",
            "status": "pending_author_supplied_metadata",
            "scope": "named contributions and final funding declaration",
        },
    }
    native_validation = bindings.get(
        "reports/independent_solver_validation_v1.2_sd_core_rebound.json", {}
    )
    native_manifest = bindings.get(
        "metadata/independent_solver_case_manifest_v1.json", {}
    )
    power_evidence_gates = {
        "embedded_opf": {
            "passed": local_gate_summary["embedded_opf"],
            "path": "reports/current_opf_closed_loop_audit_v1.2_sd_core.json",
            "scope": "320 released records over 160 registered scenarios",
        },
        "native_independent_solver": {
            "passed": local_gate_summary["native_fixed_control_replay"],
            "path": "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
            "case_count": 160,
            "raw_scenario_arrays_present": False,
            "manifest_sha256": native_manifest.get("sha256"),
            "validation_sha256": native_validation.get("sha256"),
            "scope": "retained native-source fixed-control evidence bound to a local case manifest",
        },
        "raw_scenario_replay": external_gates["raw_scenario_replay_ledger"],
    }
    manifest = {
        "schema_version": "gridinstruct-scoped-evidence-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not missing and not local_failures else "fail",
        "scope": "local_hash_bound_release_candidate",
        "runtime_policy": "local CPU/MPS only; no remote GPU execution",
        "dataset_sha256": bindings.get(
            "data/gridinstruct_v1.2_sd_core_en.jsonl", {}
        ).get("sha256"),
        "split_sha256": {
            path: bindings[path]["sha256"]
            for path in bindings
            if path.startswith("data/v1.2_sd_core_") and path.endswith("_en.jsonl")
        },
        "evidence_bindings": bindings,
        "total_evidence": len(set(LOCAL_BINDINGS)),
        "evidence_bound": len(bindings),
        "missing_local_bindings": missing,
        "invalid_evidence_count": len(missing) + len(local_failures),
        "local_gate_summary": local_gate_summary,
        "local_gate_failures": local_failures,
        "power_evidence_gates": power_evidence_gates,
        "external_gates": external_gates,
        "claim_boundary": (
            "Local hash-bound audits support data integrity, provenance-controlled splits, "
            "embedded OPF closure, target-hidden learnability diagnostics, and 160 retained "
            "native-source fixed-control replays. They do not establish full-population raw "
            "scenario replay, human agreement, or public accession."
        ),
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": manifest["status"],
        "bound": len(bindings),
        "missing": missing,
        "local_failures": local_failures,
        "output": str(output),
    }, ensure_ascii=False))
    if manifest["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
