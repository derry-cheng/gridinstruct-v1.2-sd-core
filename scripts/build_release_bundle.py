#!/usr/bin/env python3
"""Build a GridInstruct SD-core release-candidate archive and validation report."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import platform
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from importlib.metadata import PackageNotFoundError, distributions, version

import yaml

from capture_formal_runtime_environment import direct_pip_requirements
from evidence_contract import power_evidence_gate_results
from gridinstruct_utils import ROOT, ensure_dirs, write_json

EXCLUDE_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "wandb", "node_modules", "_archive", "_backups"}
EXCLUDE_SUFFIXES = {".pyc", ".DS_Store", ".tmp", ".log"}
REQUIRED = [
    "MANIFEST.md", "LICENSE-CODE", "LICENSE-DATA",
    "data/gridinstruct_v1.2_sd_core.jsonl", "data/gridinstruct_v1.2_sd_core_en.jsonl",
    "data/v1.2_sd_core_train.jsonl", "data/v1.2_sd_core_validation.jsonl", "data/v1.2_sd_core_test.jsonl", "data/v1.2_sd_core_ood_test.jsonl",
    "data/v1.2_sd_core_train_en.jsonl", "data/v1.2_sd_core_validation_en.jsonl", "data/v1.2_sd_core_test_en.jsonl", "data/v1.2_sd_core_ood_test_en.jsonl",
    "metadata/dataset_metadata.json", "metadata/schema.json", "metadata/data_dictionary.csv", "rules/regulation_rules.json",
    "rules/international_rule_profiles.json", "metadata/international_rule_profile_matrix.csv",
    "metadata/international_rule_probe_splits_v1.json",
    "data/international_rule_probe_v1.jsonl", "reports/international_rule_probe_v1.json",
    "reports/international_rule_probe_v1.md", "reports/international_rule_probe_splits_v1.json",
    "benchmark/international_rule_probe_v1/nearest_neighbor_report.json",
    "benchmark/international_rule_probe_v1/nearest_neighbor_report.md",
    "benchmark/international_rule_probe_v1/nearest_neighbor_predictions.jsonl",
    "reports/international_rule_review_assignments_v1.json",
    "reports/international_rule_review_assignments_v1.jsonl",
    "reports/international_rule_review_assignments_v1.csv",
    "docs/INTERNATIONAL_RULE_EXTENSION.md",
    "metadata/data_lineage_manifest.json", "docs/DATA_GENERATION_LINEAGE.md",
    "metadata/third_party_asset_inventory.json", "docs/THIRD_PARTY_ASSETS.md",
    "third_party/pglib-opf-v23.07/LICENSE",
    "docs/SCIENTIFIC_DATA_DESCRIPTOR_DRAFT.md", "docs/DATA_RECORDS.md", "docs/TECHNICAL_VALIDATION.md", "docs/AVAILABILITY_AND_LIMITATIONS.md", "docs/LICENSES_AND_CITATION.md",
    "simulation_outputs/contingency/scenarios_converged.json", "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
    "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
    "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
    "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json",
    "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_commit_v1.json",
    "simulation_outputs/opf_closed_loop/opf_action_constant_power_factor_stress_cases_v1.json",
    "simulation_outputs/opf_closed_loop/opf_action_constant_power_factor_stress_commit_v1.json",
    "metadata/independent_solver_case_manifest_v1.json",
    "simulation_outputs/power_flow/scenarios_all.json", "simulation_outputs/power_flow/scenarios_converged_core.json",
    "docs/ARTIFACT_INDEX_2026-08-13.md",
    "metadata/frozen_source_checksums_v2.json", "metadata/rule_clause_matrix.csv", "metadata/rule_clause_matrix.json",
    "metadata/environment_lock.json", "metadata/runtime_environment.json", "metadata/formal_runtime_environment.json", "environment.yml", "metadata/requirements-lock.txt", "metadata/environment-freeze.txt", "metadata/model_revision_manifest.json", "metadata/code_manifest.json",
    "metadata/evidence_binding_manifest.json",
    "reports/independent_query_truth_validation_v1.2_sd_core.json",
    "reports/cross_solver_power_flow_v1.2_sd_core.json", "reports/group_cluster_bootstrap_v1.2_sd_core.json",
    "reports/core_n1_denominator_v1.2_sd_core.json",
    "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    "reports/independent_solver_raw_evidence_v1.2_sd_core_rebound.json",
    "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
    "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
    "reports/opf_action_constant_power_factor_stress_v1.2_sd_core.json",
    "reports/sd_pre_release_quality_snapshot_v1.2_sd_core.json",
    "benchmark/multiseed_v1.2_sd_core/classification_operation_ticket_check_five_seed_summary.json",
    "benchmark/multiseed_v1.2_sd_core/classification_regulation_compliance_check_five_seed_summary.json",
    "benchmark/multiseed_v1.2_sd_core/classification_dispatcher_intent_tool_call_five_seed_summary.json",
    "benchmark/multiseed_v1.2_sd_core/generation_regulation_qa_five_seed_summary.json",
    "benchmark/multiseed_v1.2_sd_core/generation_intelligent_data_query_five_seed_summary.json",
    "benchmark/multiseed_v1.2_sd_core/generation_auxiliary_decision_five_seed_summary.json",
    "release/archive_manifest_v1.2_sd_core.csv", "release/checksums_sha256.txt",
]
EXPECTED_TASKS = {
    "operation_ticket_check",
    "regulation_compliance_check",
    "dispatcher_intent_tool_call",
    "regulation_qa",
    "intelligent_data_query",
    "auxiliary_decision",
}
SOURCE_DATA_FILES = [
    "data/gridinstruct_v1.2_sd_core.jsonl",
    "data/v1.2_sd_core_train.jsonl",
    "data/v1.2_sd_core_validation.jsonl",
    "data/v1.2_sd_core_test.jsonl",
    "data/v1.2_sd_core_ood_test.jsonl",
]
ENGLISH_DATA_FILES = [
    "data/gridinstruct_v1.2_sd_core_en.jsonl",
    "data/v1.2_sd_core_train_en.jsonl",
    "data/v1.2_sd_core_validation_en.jsonl",
    "data/v1.2_sd_core_test_en.jsonl",
    "data/v1.2_sd_core_ood_test_en.jsonl",
]
SIDECAR_REPORT_TOKENS = {
    "release_bundle_validation_v1.2_sd_core",
    "deposition_checklist_v1.2_sd_core",
    "SD_FINAL_ASSESSMENT",
    "RESULT_TO_CLAIM",
    "EXPERIMENT_AUDIT",
}
PLACEHOLDER_METADATA_VALUES = {"", "pending", "tbd", "todo", "none", "null"}


def resolved_metadata_value(explicit: str | None, existing: object) -> str:
    if explicit is not None:
        value = explicit.strip()
        if value:
            return value
    existing_value = str(existing or "").strip()
    return existing_value if existing_value.lower() not in PLACEHOLDER_METADATA_VALUES else "pending"


def successful_archive_status(status: str) -> bool:
    return status in {
        "package_ready_external_identifiers_pending",
        "package_ready_for_deposition",
    }


def deposition_status(
    archive_status: str,
    blocking_local_items: list[str],
    blocking_external_items: list[str],
) -> str:
    if blocking_local_items:
        return "local_package_failed"
    if (
        not blocking_external_items
        and archive_status == "package_ready_for_deposition"
    ):
        return "local_package_ready_for_deposition"
    return "local_package_ready_external_deposition_pending"


def build_archive_metadata(args: argparse.Namespace, existing: dict[str, object]) -> dict[str, object]:
    return {
        "generated_at": "1970-01-01T00:00:00+00:00",
        "timestamp_policy": "deterministic SOURCE_DATE_EPOCH=0 archive metadata",
        "title": "GridInstruct v1.2-sd-core release candidate",
        "version": "1.2-sd-core",
        "release_type": "local release candidate for external repository deposition",
        "license_data": "See LICENSE-DATA",
        "license_code": "See LICENSE-CODE",
        "public_repository_url": resolved_metadata_value(
            args.public_repository_url, existing.get("public_repository_url")
        ),
        "data_doi": resolved_metadata_value(args.data_doi, existing.get("data_doi")),
        "archived_code_release_doi": resolved_metadata_value(
            args.archived_code_release_doi, existing.get("archived_code_release_doi")
        ),
        "keywords": ["power dispatch", "instruction dataset", "IEEE test systems", "Scientific Data"],
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    parts = set(rel.parts)
    if parts & EXCLUDE_PARTS:
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    text = str(rel)
    if text.startswith("experiments/"):
        return False
    if text.startswith("data/"):
        return (
            text in {"data/gridinstruct_v1.2_sd_core.jsonl", "data/gridinstruct_v1.2_sd_core_en.jsonl"}
            or text == "data/international_rule_probe_v1.jsonl"
            or text == "data/gridinstruct_v1.2_paper_candidate_actionable_plus15_v16.jsonl"
            or text.startswith("data/frozen_sources/")
            or text.startswith("data/v1.2_sd_core_")
            or text.startswith("data/proxy_stress/")
            or text.startswith("data/hard_boundary_v1.2_sd_core/")
        )
    if text.startswith("benchmark/"):
        return ("v1.2_sd_core" in text or "international_rule_probe_v1" in text) and path.suffix in {".json", ".md", ".jsonl", ".csv"}
    if text.startswith("reports/"):
        if any(token in text for token in SIDECAR_REPORT_TOKENS):
            return False
        keep_tokens = [
            "data_validation_v1.2_sd_core", "data_quality_optimization_v1.2_sd_core",
            "generation_grounding_audit_v1.2_sd_core", "model_score_quality_audit_v1.2_sd_core", "shortcut_ablation_v1.2_sd_core",
            "expert_review_package_v1.2_sd_core", "structured_prediction_normalization_v1.2_sd_core", "sd_core_distribution_risk_audit",
            "query_result_consistency_repair_v1.2_sd_core", "project_milestone_audit_v1.2_sd_core",
            "sd_core_figure_summary", "proxy_stress_split_manifest_v1.2_sd_core",
            "sd_statistical_quality_audit_v1.2_sd_core", "expert_review_readiness_audit_v1.2_sd_core",
            "split_independence_audit_v1.2_sd_core", "calibration_audit_v1.2_sd_core", "source_group_map_audit_v1.2_sd_core",
            "strict_source_group_split_v1.2_sd_core", "strict_split_evidence_v1.2_sd_core",
            "proxy_reduced_input_audit_v1.2_sd_core", "proxy_reduced_split_manifest_v1.2_sd_core",
            "expert_review_execution_check_v1.2_sd_core", "evidence_tiers_v1.2_sd_core",
            "label_split_support_audit_v1.2_sd_core", "label_split_support_task_by_split_v1.2_sd_core",
            "label_split_support_network_by_split_v1.2_sd_core", "label_split_support_severity_by_split_v1.2_sd_core",
            "linear_seed_stability_audit_v1.2_sd_core", "generation_structure_coverage_audit_v1.2_sd_core",
            "sd_visualization_completeness_v1.2_sd_core", "language_position_audit_v1.2_sd_core",
            "english_translation_v1.2_sd_core", "english_translation_progress_v1.2_sd_core",
            "english_translation_audit_v1.2_sd_core", "english_split_materialization_v1.2_sd_core",
            "english_split_audit_v1.2_sd_core", "english_translation_boundary_challenge_v1.2_sd_core",
            "english_translation_progress_boundary_challenge_v1.2_sd_core",
            "english_translation_audit_boundary_challenge_v1.2_sd_core",
            "data_validation_boundary_challenge_v1.2_sd_core_en", "data_validation_v1.2_sd_core_en",
            "extended_topology_stress_audit_v1.2_sd_core", "rule_coverage_scope_audit_v1.2_sd_core",
            "translation_glossary_audit_v1.2_sd_core", "action_level_validation_audit_v1.2_sd_core",
            "topology_instruction_expansion_v1.2_sd_core", "opf_closed_loop_auxiliary_audit_v1.2_sd_core",
            "external_topology_physical_envelope_v1.2_sd_core", "opf_closed_loop_assumption_audit_v1.2_sd_core",
            "rule_taxonomy_expansion_v1.2_sd_core", "hard_boundary_benchmark_v1.2_sd_core",
            "translation_cache_override_v1.2_sd_core",
            "scenario_source_merge_v1.2_sd_core", "scenario_truth_rebuild_v1.2_sd_core",
            "scenario_truth_rebuild_attempts_v1.2_sd_core", "frozen_external_topology_migration_v2",
            "extended_topology_stress_attempts_v1.2_sd_core", "dataset_scenario_link_migration_v1.2_sd_core",
            "frozen_external_topology_v1_append", "equipment_contingency_instruction_expansion_v1.2_sd_core",
            "independent_query_truth_validation_v1.2_sd_core", "cross_solver_power_flow_v1.2_sd_core",
            "operation_ticket_state_consistency_v1.2_sd_core", "ood_stratified_metrics_v1.2_sd_core",
            "near_duplicate_audit_v1.2_sd_core", "group_cluster_bootstrap_v1.2_sd_core",
            "sd_pre_release_quality_snapshot_v1.2_sd_core", "release_claim_alignment_audit_v1.2_sd_core",
            "core_n1_denominator_v1.2_sd_core",
            "independent_solver_raw_evidence_v1.2_sd_core",
            "independent_solver_validation_v1.2_sd_core",
            "independent_solver_raw_evidence_v1.2_sd_core_rebound",
            "independent_solver_validation_v1.2_sd_core_rebound",
            "opf_action_uncertainty_stress_v1.2_sd_core",
            "opf_action_constant_power_factor_stress_v1.2_sd_core",
            "ieee14_opf_secure_candidate_augmentation_v1.2_sd_core",
            "independent_opf_envelope_v1.2_sd_core",
            "pglib_network_envelope_replay_v1.2_sd_core",
            "template_family_holdout_v1.2_sd_core", "international_rule_probe_v1", "international_rule_probe_splits_v1", "international_rule_review_assignments_v1",
        ]
        return any(token in text for token in keep_tokens)
    if text.startswith("figures/"):
        return "sd_core" in text and path.suffix.lower() in {".png", ".csv", ".json", ".md"}
    if text.startswith("simulation_outputs/topology_stress/"):
        return path.suffix.lower() in {".json", ".md"}
    if text.startswith("simulation_outputs/contingency/"):
        return path.suffix.lower() in {".json", ".md"}
    if text.startswith("simulation_outputs/opf_closed_loop/"):
        return path.suffix.lower() in {".json", ".md"}
    if text.startswith("simulation_outputs/power_flow/"):
        return path.suffix.lower() in {".json", ".md"}
    if text.startswith("simulation_outputs/independent_solver/"):
        return path.suffix.lower() in {".json", ".md"}
    if text.startswith("review_packages/"):
        return "stratified_expert_review_v1.2_sd_core" in text
    if text.startswith("release/"):
        return False
    if text == "third_party/pglib-opf-v23.07/LICENSE":
        return True
    if text.startswith("docs/") or text.startswith("metadata/") or text.startswith("rules/") or text.startswith("scripts/") or text.startswith("examples/"):
        return True
    return text in {"MANIFEST.md", "RESEARCH_BRIEF.md", "LICENSE-CODE", "LICENSE-DATA", ".env.example", "environment.yml"}


def evidence_bound_paths() -> set[str]:
    """Return every hash-bound artifact that must accompany the evidence manifest."""
    evidence_path = ROOT / "metadata/evidence_binding_manifest.json"
    if not evidence_path.is_file():
        return set()
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    paths: set[str] = set()
    for item in (evidence.get("current_inputs") or {}).values():
        if isinstance(item, dict) and item.get("path"):
            paths.add(str(item["path"]))
    evidence_items = evidence.get("evidence_bindings") or evidence.get("evidence") or {}
    for name, item in evidence_items.items():
        if name == "multiseed" and isinstance(item, dict):
            for summary in item.get("summaries") or []:
                if isinstance(summary, dict) and summary.get("path"):
                    paths.add(str(summary["path"]))
        elif isinstance(item, dict) and item.get("path"):
            paths.add(str(item["path"]))
    return paths


def iter_files() -> Iterable[Path]:
    selected: set[Path] = set()
    for path in ROOT.rglob("*"):
        if path.is_file() and should_include(path):
            selected.add(path)
    for relative in evidence_bound_paths():
        candidate = (ROOT / relative).resolve()
        if candidate.is_relative_to(ROOT) and candidate.is_file():
            selected.add(candidate)
    yield from selected


def write_csv(path: Path, rows: list[dict[str, str | int]]) -> None:
    ensure_dirs(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = ["path", "size_bytes", "sha256"]
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_reproducibility_manifests() -> None:
    fallback_packages = (
        "chardet",
        "datasketch",
        "jsonschema",
        "lightsim2grid",
        "lm-format-enforcer",
        "matplotlib",
        "numpy",
        "openai",
        "pandas",
        "pandapower",
        "power-grid-model",
        "pypower",
        "pytest",
        "PyYAML",
        "requests",
        "scipy",
        "scikit-learn",
        "sentence-transformers",
        "sentencepiece",
        "torch",
        "tqdm",
        "transformers",
    )
    environment_path = ROOT / "environment.yml"
    expected_versions: dict[str, str] = {}
    if environment_path.is_file():
        expected_versions = direct_pip_requirements(
            yaml.safe_load(environment_path.read_text(encoding="utf-8"))
        )
        packages = tuple(expected_versions)
    else:
        packages = fallback_packages
    package_versions = {}
    for package in packages:
        try:
            package_versions[package] = version(package)
        except PackageNotFoundError:
            package_versions[package] = None
    environment = {
        "schema_version": "2.0",
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable_name": Path(sys.executable).name,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "platform_string": platform.platform(),
        },
        "packages": package_versions,
        "expected_direct_package_versions": expected_versions,
        "direct_package_version_mismatches": {
            package: {
                "expected": expected_versions[package],
                "actual": package_versions[package],
            }
            for package in expected_versions
            if package_versions[package] != expected_versions[package]
        },
        "environment": {
            key: os.environ.get(key)
            for key in (
                "CUDA_VISIBLE_DEVICES",
                "TRANSFORMERS_OFFLINE",
                "HF_HUB_OFFLINE",
                "CUBLAS_WORKSPACE_CONFIG",
                "PYTHONHASHSEED",
            )
        },
        "determinism_policy": "This lock is generated deterministically from the formal pipeline runtime; volatile timestamps and host paths are excluded.",
    }
    write_json(ROOT / "metadata/environment_lock.json", environment)
    requirements = [
        f"{package}=={package_versions[package]}"
        for package in packages
        if package_versions[package] is not None
    ]
    (ROOT / "metadata/requirements-lock.txt").write_text(
        "# Direct runtime requirements used by the formal pipeline\n"
        + "\n".join(requirements)
        + "\n",
        encoding="utf-8",
    )
    full_freeze = sorted(
        {
            f"{dist.metadata['Name']}=={dist.version}"
            for dist in distributions()
            if dist.metadata.get("Name") and dist.version
        },
        key=str.lower,
    )
    (ROOT / "metadata/environment-freeze.txt").write_text(
        "# Complete installed Python distribution snapshot; platform-specific and paired with environment_lock.json\n"
        + "\n".join(full_freeze)
        + "\n",
        encoding="utf-8",
    )
    code_rows = [
        {
            "path": str(path.relative_to(ROOT)),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted((ROOT / "scripts").glob("*.py"))
    ]
    write_json(
        ROOT / "metadata/code_manifest.json",
        {"schema_version": "1.0", "file_count": len(code_rows), "files": code_rows},
    )
    split_paths = {
        "train": ROOT / "data/v1.2_sd_core_train_en.jsonl",
        "validation": ROOT / "data/v1.2_sd_core_validation_en.jsonl",
        "test": ROOT / "data/v1.2_sd_core_test_en.jsonl",
    }
    split_hashes = {
        name: sha256(path) if path.is_file() else None
        for name, path in split_paths.items()
    }
    revisions = []
    validation_errors: list[str] = []
    for path in sorted((ROOT / "benchmark/multiseed_v1.2_sd_core").glob("*_five_seed_summary.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        task = str(payload.get("task") or "")
        runs = payload.get("runs") or []
        model_revision = payload.get("model_revision") or {}
        row_errors = []
        if payload.get("status") != "pass":
            row_errors.append("summary_status_failed")
        if payload.get("input_sha256") != split_hashes:
            row_errors.append("current_split_hash_mismatch")
        if [run.get("seed") for run in runs] != [13, 29, 42, 57, 71]:
            row_errors.append("seed_set_mismatch")
        if not model_revision.get("sha256"):
            row_errors.append("model_revision_missing")
        artifact_rows = []
        for run in runs:
            artifacts = {
                "report": run.get("report"),
                "predictions": run.get("test_predictions"),
                "run_state": run.get("run_state"),
            }
            artifact_hashes = {}
            for name, relative in artifacts.items():
                artifact = ROOT / str(relative or "")
                artifact_hashes[name] = sha256(artifact) if artifact.is_file() else None
                if not artifact.is_file():
                    row_errors.append(f"missing_{name}:seed{run.get('seed')}")
            state_path = ROOT / str(run.get("run_state") or "")
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
            if artifact_hashes["predictions"] != run.get("prediction_sha256"):
                row_errors.append(f"prediction_hash_mismatch:seed{run.get('seed')}")
            if state.get("prediction_sha256") != artifact_hashes["predictions"]:
                row_errors.append(f"state_prediction_hash_mismatch:seed{run.get('seed')}")
            if state.get("report_sha256") != artifact_hashes["report"]:
                row_errors.append(f"state_report_hash_mismatch:seed{run.get('seed')}")
            if state.get("run_spec_sha256") != run.get("run_spec_sha256"):
                row_errors.append(f"run_spec_hash_mismatch:seed{run.get('seed')}")
            artifact_rows.append({"seed": run.get("seed"), "artifacts": artifacts, "sha256": artifact_hashes})
        validation_errors.extend(f"{task}:{error}" for error in row_errors)
        revisions.append(
            {
                "summary": str(path.relative_to(ROOT)),
                "summary_sha256": sha256(path),
                "task": task,
                "family": payload.get("family"),
                "input_sha256": payload.get("input_sha256"),
                "model_revision": model_revision,
                "suite_script_sha256": payload.get("suite_script_sha256"),
                "artifacts": artifact_rows,
                "status": "pass" if not row_errors else "fail",
            }
        )
    task_set = {row["task"] for row in revisions}
    if task_set != EXPECTED_TASKS:
        validation_errors.append(f"task_set_mismatch:{sorted(task_set)}")
    if any(value is None for value in split_hashes.values()):
        validation_errors.append("missing_current_english_split")
    environment_drift = {
        package: {
            "expected": expected_versions[package],
            "actual": package_versions[package],
        }
        for package in expected_versions
        if package_versions[package] != expected_versions[package]
    }
    write_json(
        ROOT / "metadata/model_revision_manifest.json",
        {
            "schema_version": "1.0",
            "expected_task_count": 6,
            "task_count": len(revisions),
            "current_split_sha256": split_hashes,
            "status": "pass" if not validation_errors else "fail",
            "validation_errors": validation_errors,
            "environment_drift": environment_drift,
            "models": revisions,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default="release/GridInstruct_v1.2_sd_core_release_candidate.tar.gz")
    parser.add_argument("--manifest", default="release/archive_manifest_v1.2_sd_core.csv")
    parser.add_argument("--checksums", default="release/checksums_sha256.txt")
    parser.add_argument("--validation-json", default="reports/release_bundle_validation_v1.2_sd_core.json")
    parser.add_argument("--validation-md", default="reports/release_bundle_validation_v1.2_sd_core.md")
    parser.add_argument("--deposition-json", default="reports/deposition_checklist_v1.2_sd_core.json")
    parser.add_argument("--deposition-md", default="reports/deposition_checklist_v1.2_sd_core.md")
    parser.add_argument("--archive-metadata", default="metadata/archive_metadata.json")
    parser.add_argument("--public-repository-url", default=None)
    parser.add_argument("--data-doi", default=None)
    parser.add_argument("--archived-code-release-doi", default=None)
    args = parser.parse_args()

    ensure_dirs(ROOT / "release", ROOT / "reports", ROOT / "metadata")
    write_reproducibility_manifests()
    archive_metadata_path = ROOT / args.archive_metadata
    try:
        existing_archive_metadata = json.loads(archive_metadata_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        existing_archive_metadata = {}
    archive_metadata = build_archive_metadata(args, existing_archive_metadata)
    write_json(archive_metadata_path, archive_metadata)

    rows = []
    for path in sorted(iter_files(), key=lambda p: str(p.relative_to(ROOT))):
        rel = str(path.relative_to(ROOT))
        rows.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha256(path)})
    write_csv(ROOT / args.manifest, rows)
    with (ROOT / args.checksums).open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(f"{row['sha256']}  {row['path']}" + chr(10))


    bundle_path = ROOT / args.bundle
    if bundle_path.exists():
        bundle_path.unlink()
    companion_files = [args.manifest, args.checksums]
    companion_rows = []
    for rel in companion_files:
        path = ROOT / rel
        companion_rows.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha256(path)})

    with bundle_path.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as gzip_handle:
            # Dereference local hardlinks so an isolated extraction receives
            # ordinary files; the release replay intentionally rejects link
            # members to keep the archive portable and self-contained.
            with tarfile.open(fileobj=gzip_handle, mode="w", dereference=True) as tar:
                for rel in [str(row["path"]) for row in rows] + companion_files:
                    src = ROOT / rel
                    if not src.exists():
                        continue
                    info = tar.gettarinfo(str(src), arcname=f"GridInstruct_v1.2_sd_core/{rel}")
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = 0o644
                    with src.open("rb") as source_handle:
                        tar.addfile(info, source_handle)

    bundle_sha = sha256(bundle_path)
    (bundle_path.with_suffix(bundle_path.suffix + ".sha256")).write_text(f"{bundle_sha}  {bundle_path.name}" + chr(10), encoding="utf-8")


    tar_hash_mismatches = []
    tar_member_hashes: dict[str, str] = {}
    with tarfile.open(bundle_path, "r:gz") as tar:
        names = {name.removeprefix("GridInstruct_v1.2_sd_core/") for name in tar.getnames() if not name.endswith("/")}
        for row in rows:
            member_name = f"GridInstruct_v1.2_sd_core/{row['path']}"
            try:
                extracted = tar.extractfile(member_name)
            except KeyError:
                tar_hash_mismatches.append({"path": row["path"], "reason": "missing_from_archive"})
                continue
            if extracted is None:
                tar_hash_mismatches.append({"path": row["path"], "reason": "not_a_regular_file"})
                continue
            actual = sha256_stream(extracted)
            tar_member_hashes[str(row["path"])] = actual
            if actual != row["sha256"]:
                tar_hash_mismatches.append({"path": row["path"], "expected": row["sha256"], "actual": actual})
    required_missing = [item for item in REQUIRED if item not in names]
    model_revision_manifest = json.loads((ROOT / "metadata/model_revision_manifest.json").read_text(encoding="utf-8"))
    evidence_path = ROOT / "metadata/evidence_binding_manifest.json"
    evidence_manifest = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {}
    power_evidence_gates = power_evidence_gate_results(ROOT)
    evidence_binding_errors: list[str] = []
    if evidence_manifest.get("status") != "pass":
        evidence_binding_errors.append("evidence_manifest_status_failed")
    bound_paths: dict[str, str | None] = {}
    for item in (evidence_manifest.get("current_inputs") or {}).values():
        if isinstance(item, dict) and item.get("path"):
            bound_paths[str(item["path"])] = item.get("sha256")
    evidence_items = evidence_manifest.get("evidence_bindings") or evidence_manifest.get("evidence") or {}
    for name, item in evidence_items.items():
        if name == "multiseed":
            for summary in item.get("summaries") or []:
                if summary.get("path"):
                    bound_paths[str(summary["path"])] = summary.get("sha256")
        elif isinstance(item, dict) and item.get("path"):
            bound_paths[str(item["path"])] = item.get("sha256")
    for relative, expected_hash in sorted(bound_paths.items()):
        if not expected_hash:
            evidence_binding_errors.append(f"missing_bound_hash:{relative}")
        elif relative not in names:
            evidence_binding_errors.append(f"bound_file_missing_from_archive:{relative}")
        elif tar_member_hashes.get(relative) != expected_hash:
            evidence_binding_errors.append(f"bound_file_hash_mismatch:{relative}")
    reproducibility_manifests_pass = (
        model_revision_manifest.get("status") == "pass"
        and not evidence_binding_errors
        and all(result["passed"] for result in power_evidence_gates.values())
    )
    external_metadata_keys = [
        "public_repository_url",
        "data_doi",
        "archived_code_release_doi",
    ]
    external_metadata_complete = {
        key: str(archive_metadata.get(key) or "").strip().lower() not in PLACEHOLDER_METADATA_VALUES
        for key in external_metadata_keys
    }
    pending_external = [key for key, complete in external_metadata_complete.items() if not complete]
    total_size = sum(int(row["size_bytes"]) for row in rows + companion_rows)
    if required_missing:
        archive_status = "missing_required_files"
    elif tar_hash_mismatches:
        archive_status = "archive_hash_mismatch"
    elif not reproducibility_manifests_pass:
        archive_status = "reproducibility_or_evidence_binding_failed"
    elif pending_external:
        archive_status = "package_ready_external_identifiers_pending"
    else:
        archive_status = "package_ready_for_deposition"
    validation = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": archive_status,
        "bundle": args.bundle,
        "bundle_sha256": bundle_sha,
        "file_count": len(rows) + len(companion_rows),
        "payload_file_count": len(rows),
        "companion_file_count": len(companion_rows),
        "payload_size_bytes": total_size,
        "manifest": args.manifest,
        "checksums": args.checksums,
        "manifest_scope": "payload files only; manifest and checksum files are included in the archive but are not self-hashed",
        "validation_reports_stored_outside_bundle": True,
        "required_missing": required_missing,
        "tar_payload_hash_mismatches": tar_hash_mismatches,
        "tar_payload_hashes_verified": not tar_hash_mismatches,
        "reproducibility_manifests_pass": reproducibility_manifests_pass,
        "evidence_binding_manifest_status": evidence_manifest.get("status"),
        "evidence_binding_errors": evidence_binding_errors,
        "power_evidence_gates": power_evidence_gates,
        "bound_archive_file_count": len(bound_paths),
        "pglib_license_in_archive": (
            "third_party/pglib-opf-v23.07/LICENSE" in names
        ),
        "deterministic_archive_headers": True,
        "public_identifiers_pending": pending_external,
        "excludes_experiments_directory": True,
        "exclusion_reason": "Training checkpoints/log-heavy experiment folders remain local reproducibility evidence; release archive contains data, metadata, code, reports, figures, and review package.",
    }
    write_json(ROOT / args.validation_json, validation)

    deposition_items = {
        "source_dataset_jsonl_present": "data/gridinstruct_v1.2_sd_core.jsonl" in names,
        "english_dataset_jsonl_present": "data/gridinstruct_v1.2_sd_core_en.jsonl" in names,
        "source_official_splits_present": all(item in names for item in SOURCE_DATA_FILES[1:]),
        "english_official_splits_present": all(item in names for item in ENGLISH_DATA_FILES[1:]),
        "metadata_present": "metadata/dataset_metadata.json" in names,
        "schema_present": "metadata/schema.json" in names,
        "licenses_present": all(item in names for item in ["LICENSE-CODE", "LICENSE-DATA"]),
        "data_records_doc_present": "docs/DATA_RECORDS.md" in names,
        "technical_validation_doc_present": "docs/TECHNICAL_VALIDATION.md" in names,
        "final_assessment_is_post_bundle_sidecar": True,
        "archive_manifest_present": args.manifest in names,
        "checksums_present": args.checksums in names,
        "expert_review_package_present": "reports/expert_review_package_v1.2_sd_core.json" in names,
        "shortcut_ablation_present": "reports/shortcut_ablation_v1.2_sd_core.json" in names,
        "opf_closed_loop_audit_present": "reports/current_opf_closed_loop_audit_v1.2_sd_core.json" in names,
        "external_topology_physical_envelope_present": "reports/external_topology_physical_envelope_v1.2_sd_core.json" in names,
        "opf_closed_loop_assumption_audit_present": "reports/opf_closed_loop_assumption_audit_v1.2_sd_core.json" in names,
        "rule_taxonomy_expansion_present": "metadata/rule_clause_matrix.json" in names,
        "hard_boundary_benchmark_present": "reports/hard_boundary_benchmark_v1.2_sd_core.json" in names,
        "hard_boundary_figure_present": "figures/sd_core/fig_hard_boundary_benchmark.png" in names,
        "opf_closed_loop_results_present": "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json" in names,
        "opf_robust_candidate_manifest_present": "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json" in names,
        "opf_robust_candidate_report_present": "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json" in names,
        "scenario_rebuild_manifest_present": "reports/scenario_truth_migration_v1.2_sd_core.json" in names,
        "core_scenario_truth_present": "simulation_outputs/power_flow/scenarios_converged_core.json" in names,
        "dataset_scenario_link_migration_present": "reports/scenario_truth_migration_v1.2_sd_core.json" in names,
        "frozen_instruction_source_present": False,
        "migrated_frozen_instruction_source_present": False,
        "frozen_topology_migration_report_present": "reports/frozen_external_topology_migration_v2.json" in names,
        "scenario_truth_attempt_ledger_present": "reports/scenario_truth_migration_v1.2_sd_core.json" in names,
        "external_topology_attempt_ledger_present": "reports/extended_topology_stress_attempts_v1.2_sd_core.json" in names,
        "frozen_scenario_source_present": False,
        "environment_lock_present": "metadata/environment_lock.json" in names,
        "model_revision_manifest_present": "metadata/model_revision_manifest.json" in names,
        "code_manifest_present": "metadata/code_manifest.json" in names,
        "independent_query_validation_present": "reports/independent_query_truth_validation_v1.2_sd_core.json" in names,
        "cross_solver_validation_present": "reports/cross_solver_power_flow_v1.2_sd_core.json" in names,
        "core_n1_denominator_present": "reports/core_n1_denominator_v1.2_sd_core.json" in names,
        "native_independent_solver_manifest_present": "metadata/independent_solver_case_manifest_v1.json" in names,
        "native_independent_solver_raw_present": "reports/independent_solver_raw_evidence_v1.2_sd_core_rebound.json" in names,
        "native_independent_solver_validation_present": "reports/independent_solver_validation_v1.2_sd_core_rebound.json" in names,
        "opf_uncertainty_cases_present": "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json" in names,
        "opf_uncertainty_report_present": "reports/opf_action_uncertainty_stress_v1.2_sd_core.json" in names,
        "opf_uncertainty_commit_present": "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_commit_v1.json" in names,
        "opf_constant_power_factor_cases_present": "simulation_outputs/opf_closed_loop/opf_action_constant_power_factor_stress_cases_v1.json" in names,
        "opf_constant_power_factor_report_present": "reports/opf_action_constant_power_factor_stress_v1.2_sd_core.json" in names,
        "opf_constant_power_factor_commit_present": "simulation_outputs/opf_closed_loop/opf_action_constant_power_factor_stress_commit_v1.json" in names,
        "core_n1_denominator_pass": power_evidence_gates["core_n1_denominator"]["passed"],
        "native_independent_solver_pass": power_evidence_gates["native_independent_solver"]["passed"],
        "opf_uncertainty_7680_pass": power_evidence_gates["opf_action_uncertainty_stress"]["passed"],
        "opf_constant_power_factor_7680_pass": power_evidence_gates["opf_action_constant_power_factor_stress"]["passed"],
        "group_bootstrap_present": "reports/group_cluster_bootstrap_v1.2_sd_core.json" in names,
        "third_party_asset_inventory_present": "metadata/third_party_asset_inventory.json" in names,
        "pglib_license_present": "third_party/pglib-opf-v23.07/LICENSE" in names,
        "evidence_binding_manifest_present": "metadata/evidence_binding_manifest.json" in names,
        "evidence_binding_manifest_pass": evidence_manifest.get("status") == "pass" and not evidence_binding_errors,
        "bundle_checksum_present": bool(bundle_sha),
        "validation_sidecar_present": (ROOT / args.validation_json).exists(),
        "public_repository_url_inserted": external_metadata_complete["public_repository_url"],
        "data_doi_inserted": external_metadata_complete["data_doi"],
        "archived_code_doi_inserted": external_metadata_complete["archived_code_release_doi"],
    }
    blocking_external_items = [
        key for key, ok in deposition_items.items() if not ok and key.endswith("inserted")
    ]
    blocking_local_items = [
        key
        for key, ok in deposition_items.items()
        if not ok and not key.endswith("inserted")
    ]
    deposition = {
        "generated_at": validation["generated_at"],
        "status": deposition_status(
            archive_status,
            blocking_local_items,
            blocking_external_items,
        ),
        "items": deposition_items,
        "blocking_local_items": blocking_local_items,
        "blocking_external_items": blocking_external_items,
        "bundle": args.bundle,
        "validation_report": args.validation_json,
        "validation_report_scope": "sidecar report generated after archive creation; it is intentionally not stored inside the archive",
    }
    write_json(ROOT / args.deposition_json, deposition)

    md = ["# Release Bundle Validation", "", f"Generated: {validation['generated_at']}", f"Status: `{validation['status']}`", f"Bundle: `{args.bundle}`", f"Bundle SHA256: `{bundle_sha}`", f"Files included: {validation['file_count']}", f"Payload files: {validation['payload_file_count']}", f"Companion files: {validation['companion_file_count']}", f"Payload size bytes: {total_size}", f"Manifest scope: {validation['manifest_scope']}", "Validation and deposition reports are sidecar files generated after archive creation.", "", "## Required Files", ""]
    if required_missing:
        md.extend(f"- Missing: `{item}`" for item in required_missing)
    else:
        md.append("- All required local release files are present in the archive.")
    md.extend(["", "## External Identifiers", ""])
    md.extend(f"- `{item}`: pending" for item in pending_external)
    (ROOT / args.validation_md).write_text(chr(10).join(md) + chr(10), encoding="utf-8")


    checklist = ["# Deposition Checklist", "", f"Generated: {deposition['generated_at']}", f"Status: `{deposition['status']}`", "", "## Local Package Items", ""]
    for key, ok in deposition_items.items():
        checklist.append(f"- `{key}`: {'complete' if ok else 'pending'}")
    checklist.extend(["", "## Blocking External Items", "", "- Public repository URL: pending external deposition.", "- Data DOI: pending external deposition.", "- Archived code-release DOI: pending external deposition."])
    (ROOT / args.deposition_md).write_text(chr(10).join(checklist) + chr(10), encoding="utf-8")

    if not successful_archive_status(archive_status):
        raise SystemExit(1)



if __name__ == "__main__":
    main()
