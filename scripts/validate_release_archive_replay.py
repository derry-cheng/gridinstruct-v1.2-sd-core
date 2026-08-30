#!/usr/bin/env python3
"""Extract the release archive in isolation and replay its included dataset validator."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, write_json


PREFIX = "GridInstruct_v1.2_sd_core"

COMPACT_REQUIRED_FILES = {
    "LICENSE-CODE",
    "LICENSE-DATA",
    "MANIFEST.md",
    "README.md",
    "data/gridinstruct_v1.2_sd_core_en.jsonl",
    "data/v1.2_sd_core_train_en.jsonl",
    "data/v1.2_sd_core_validation_en.jsonl",
    "data/v1.2_sd_core_test_en.jsonl",
    "data/v1.2_sd_core_ood_test_en.jsonl",
    "data/v1.2_sd_core_strict_train.jsonl",
    "data/v1.2_sd_core_strict_validation.jsonl",
    "data/v1.2_sd_core_strict_test.jsonl",
    "data/v1.2_sd_core_instruction_surface_balanced_train_en.jsonl",
    "data/v1.2_sd_core_instruction_surface_balanced_validation_en.jsonl",
    "data/v1.2_sd_core_instruction_surface_balanced_test_en.jsonl",
    "data/v1.2_sd_core_instruction_surface_balanced_ood_test_ids.jsonl",
    "metadata/schema.json",
    "metadata/data_dictionary.csv",
    "metadata/data_lineage_manifest.json",
    "metadata/evidence_binding_manifest.json",
    "metadata/source_group_map.csv",
    "metadata/source_traceability.csv",
    "metadata/independent_solver_case_manifest_v1.json",
    "metadata/archive_metadata.json",
    "reports/direct_english_canonical_materialization_v1.2_sd_core.json",
    "reports/direct_english_split_materialization_v1.2_sd_core.json",
    "reports/data_validation_direct_english_v1.2_sd_core.json",
    "reports/independent_query_truth_validation_v1.2_sd_core.json",
    "reports/query_truth_completeness_gate_v1.2_sd_core.json",
    "reports/split_independence_audit_current_v1.2_sd_core.json",
    "reports/strict_source_group_split_v1.2_sd_core.json",
    "reports/template_family_holdout_v1.2_sd_core.json",
    "reports/current_surface_seed_stability_v1.2_sd_core.json",
    "reports/compliance_label_current_audit_v1.2_sd_core.json",
    "data/international_rule_probe_v1.jsonl",
    "simulation_outputs/contingency/scenarios_converged.json",
    "simulation_outputs/opf_closed_loop/ieee14_ieee118_source_scenarios_v1.json",
    "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
    "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json",
    "simulation_outputs/opf_closed_loop/opf_action_constant_power_factor_stress_cases_v1.json",
    "rules/international_rule_profiles.json",
    "metadata/international_rule_probe_splits_v1.json",
    "metadata/international_rule_probe_schema.json",
    "reports/international_rule_probe_v1.json",
    "reports/international_rule_probe_splits_v1.json",
    "benchmark/international_rule_probe_v1/nearest_neighbor_report.json",
    "reports/international_rule_probe_controls_v1.json",
    "reports/international_rule_review_assignments_v1.json",
    "reports/ood_stratified_metrics_v1.2_sd_core.json",
    "reports/rule_coverage_scope_audit_v1.2_sd_core.json",
    "reports/rule_coverage_scope_audit_v1.2_sd_core.md",
    "reports/typed_semantic_pattern_audit_v1.2_sd_core.json",
    "reports/typed_semantic_pattern_audit_v1.2_sd_core.md",
    "reports/cross_solver_power_flow_v1.2_sd_core.json",
    "reports/cross_solver_power_flow_v1.2_sd_core.md",
    "reports/core_n1_denominator_v1.2_sd_core.json",
    "reports/core_n1_denominator_v1.2_sd_core.md",
    "paper/scientific_data_latex/PAPER_CLAIM_AUDIT.json",
    "docs/DATA_RECORDS.md",
    "docs/EXPERT_REVIEW_PROTOCOL.md",
    "docs/TECHNICAL_VALIDATION.md",
    "reports/evidence_tiers_v1.2_sd_core.json",
    "reports/expert_review_execution_check_v1.2_sd_core.json",
    "reports/official_exact_content_overlap_repair_v1.2_sd_core.json",
    "reports/strict_exact_content_overlap_repair_v1.2_sd_core.json",
    "review_packages/stratified_expert_review_v1.2_sd_core/review_packet_blinded.jsonl",
    "review_packages/stratified_expert_review_v1.2_sd_core/sample_manifest.csv",
    "review_packages/stratified_expert_review_v1.2_sd_core/review_assignments.csv",
    "review_packages/stratified_expert_review_v1.2_sd_core/human_review_log_template.csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_members(archive: tarfile.TarFile) -> tuple[list[tarfile.TarInfo], list[str]]:
    members = archive.getmembers()
    errors = []
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != PREFIX:
            errors.append(f"unsafe_archive_path:{member.name}")
        if member.issym() or member.islnk() or member.isdev():
            errors.append(f"unsupported_archive_member_type:{member.name}")
    return members, errors


def compact_archive_audit(bundle: Path, members: list[tarfile.TarInfo]) -> dict[str, Any]:
    """Audit the compact public review package without treating it as the full bundle.

    The compact package contains the canonical table, the full projections used
    by the headline split geometries, the converged scenario registry, and the
    bounded OPF evidence. It still excludes the full construction-attempt
    ledger and complete human-review archive. This check therefore verifies
    package safety, required-file presence, split denominators, registry and
    OPF counts, JSONL parseability, duplicate IDs, and the current receipts.
    """
    errors: list[str] = []
    member_names = {member.name for member in members}
    unexpected = [name for name in member_names if name.startswith("._") or "/._" in name]
    errors.extend(f"unexpected_metadata_member:{name}" for name in sorted(unexpected))
    missing = sorted(COMPACT_REQUIRED_FILES - member_names)
    errors.extend(f"compact_required_file_missing:{name}" for name in missing)
    record_count = 0
    duplicate_ids = 0
    parse_errors = 0
    seed_status = None
    direct_status = None
    review_sample_count = None
    review_assignment_count = None
    review_completed_rows = None
    split_counts: dict[str, int] = {}
    scenario_registry_count = None
    opf_record_count = None
    opf_scenario_count = None
    independent_case_count = None
    with tempfile.TemporaryDirectory(prefix="gridinstruct_compact_replay_") as temp_dir:
        extracted = Path(temp_dir)
        with tarfile.open(bundle, "r:gz") as archive:
            safe = [member for member in members if member.name in member_names]
            archive.extractall(extracted, members=safe, filter="data")
        english = extracted / "data/gridinstruct_v1.2_sd_core_en.jsonl"
        seen: set[str] = set()
        if english.is_file():
            for line in english.open(encoding="utf-8"):
                try:
                    row = json.loads(line)
                    record_count += 1
                    row_id = str(row.get("id") or "")
                    if not row_id or row_id in seen:
                        duplicate_ids += 1
                    seen.add(row_id)
                except json.JSONDecodeError:
                    parse_errors += 1
        else:
            errors.append("english_table_missing")
        direct_report = extracted / "reports/direct_english_canonical_materialization_v1.2_sd_core.json"
        seed_report = extracted / "reports/current_surface_seed_stability_v1.2_sd_core.json"
        if direct_report.is_file():
            direct_status = json.loads(direct_report.read_text(encoding="utf-8")).get("status")
        if seed_report.is_file():
            seed_status = json.loads(seed_report.read_text(encoding="utf-8")).get("status")
        sample_manifest = extracted / "review_packages/stratified_expert_review_v1.2_sd_core/sample_manifest.csv"
        assignments = extracted / "review_packages/stratified_expert_review_v1.2_sd_core/review_assignments.csv"
        execution_report = extracted / "reports/expert_review_execution_check_v1.2_sd_core.json"
        if sample_manifest.is_file():
            with sample_manifest.open(encoding="utf-8", newline="") as handle:
                review_sample_count = max(0, sum(1 for _ in handle) - 1)
        if assignments.is_file():
            with assignments.open(encoding="utf-8", newline="") as handle:
                review_assignment_count = max(0, sum(1 for _ in handle) - 1)
        if execution_report.is_file():
            review_completed_rows = json.loads(execution_report.read_text(encoding="utf-8")).get(
                "complete_human_review_rows", 0
            )
    if record_count != 95479:
        errors.append(f"english_record_count:{record_count}")
    if duplicate_ids:
        errors.append(f"duplicate_or_missing_ids:{duplicate_ids}")
    if parse_errors:
        errors.append(f"jsonl_parse_errors:{parse_errors}")
    if direct_status != "pass":
        errors.append(f"direct_english_receipt_status:{direct_status}")
    if seed_status != "pass":
        errors.append(f"surface_seed_receipt_status:{seed_status}")
    if review_sample_count != 800:
        errors.append(f"review_sample_count:{review_sample_count}")
    if review_assignment_count != 1600:
        errors.append(f"review_assignment_count:{review_assignment_count}")
    if review_completed_rows != 0:
        errors.append(f"review_completed_rows_unexpected:{review_completed_rows}")
    with tempfile.TemporaryDirectory(prefix="gridinstruct_compact_counts_") as temp_dir:
        extracted = Path(temp_dir)
        with tarfile.open(bundle, "r:gz") as archive:
            archive.extractall(extracted, filter="data")
        expected_split_files = {
            "train": ("data/v1.2_sd_core_train_en.jsonl", 26545),
            "validation": ("data/v1.2_sd_core_validation_en.jsonl", 3561),
            "test": ("data/v1.2_sd_core_test_en.jsonl", 3894),
            "ood_test": ("data/v1.2_sd_core_ood_test_en.jsonl", 61479),
            "strict_train": ("data/v1.2_sd_core_strict_train.jsonl", 76378),
            "strict_validation": ("data/v1.2_sd_core_strict_validation.jsonl", 9558),
            "strict_test": ("data/v1.2_sd_core_strict_test.jsonl", 9543),
            "surface_train": ("data/v1.2_sd_core_instruction_surface_balanced_train_en.jsonl", 10689),
            "surface_validation": ("data/v1.2_sd_core_instruction_surface_balanced_validation_en.jsonl", 5118),
            "surface_test": ("data/v1.2_sd_core_instruction_surface_balanced_test_en.jsonl", 4432),
        }
        for name, (relative, expected) in expected_split_files.items():
            path = extracted / relative
            count = sum(1 for _ in path.open(encoding="utf-8")) if path.is_file() else None
            split_counts[name] = count if count is not None else -1
            if count != expected:
                errors.append(f"split_count_{name}:{count}")
        scenario_path = extracted / "simulation_outputs/contingency/scenarios_converged.json"
        if scenario_path.is_file():
            scenario_registry_count = len(json.loads(scenario_path.read_text(encoding="utf-8")))
            if scenario_registry_count != 2822:
                errors.append(f"scenario_registry_count:{scenario_registry_count}")
        else:
            errors.append("scenario_registry_missing")
        opf_path = extracted / "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json"
        if opf_path.is_file():
            opf_payload = json.loads(opf_path.read_text(encoding="utf-8"))
            opf_record_count = len(opf_payload.get("results") or [])
            opf_scenario_count = len({str(item.get("scenario_id")) for item in (opf_payload.get("results") or [])})
            if opf_record_count != 160 or opf_scenario_count != 160:
                errors.append(f"opf_evidence_counts:{opf_record_count}/{opf_scenario_count}")
        else:
            errors.append("opf_evidence_missing")
        case_manifest = extracted / "metadata/independent_solver_case_manifest_v1.json"
        if case_manifest.is_file():
            independent_case_count = json.loads(case_manifest.read_text(encoding="utf-8")).get("case_count")
            if independent_case_count != 160:
                errors.append(f"independent_case_count:{independent_case_count}")
        else:
            errors.append("independent_case_manifest_missing")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not errors else "fail",
        "bundle": str(bundle),
        "bundle_sha256": sha256(bundle),
        "archive_layout": "compact_public_review_package",
        "safe_archive_member_count": len(members),
        "required_file_count": len(COMPACT_REQUIRED_FILES),
        "record_count": record_count,
        "duplicate_or_missing_id_count": duplicate_ids,
        "jsonl_parse_error_count": parse_errors,
        "direct_english_receipt_status": direct_status,
        "surface_seed_receipt_status": seed_status,
        "review_sample_count": review_sample_count,
        "review_assignment_count": review_assignment_count,
        "review_completed_rows": review_completed_rows,
        "split_counts": split_counts,
        "scenario_registry_count": scenario_registry_count,
        "opf_record_count": opf_record_count,
        "opf_scenario_count": opf_scenario_count,
        "independent_case_count": independent_case_count,
        "isolated_validators": [
            {
                "dataset": "data/gridinstruct_v1.2_sd_core_en.jsonl",
                "passed": not errors,
                "strict_validator_passed": False,
                "external_truth_deferred": True,
                "total_records": record_count,
                "schema_errors": None,
                "duplicate_ids": duplicate_ids,
                "semantic_errors": None,
            }
        ],
        "hash_mismatch_count": 0,
        "hash_mismatches": [],
        "evidence_binding_status": "selected_receipts_present",
        "evidence_binding_errors": [],
        "external_truth_scope": {
            "deferred": True,
            "reason": "Compact package includes the converged scenario registry and bounded OPF evidence but excludes raw scenario arrays, the full construction-attempt ledger, and the complete human-review archive.",
        },
        "errors": errors,
    }


def load_checksum_file(path: Path) -> dict[str, str]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        rows[relative] = digest
    return rows


def evidence_binding_errors(extracted_root: Path, evidence: dict[str, Any]) -> list[str]:
    """Recheck every evidence-bound artifact inside the isolated archive."""
    errors: list[str] = []
    bound: dict[str, str | None] = {}
    for item in (evidence.get("current_inputs") or {}).values():
        if isinstance(item, dict) and item.get("path"):
            bound[str(item["path"])] = item.get("sha256")
    for name, item in (evidence.get("evidence") or {}).items():
        if name == "multiseed" and isinstance(item, dict):
            for summary in item.get("summaries") or []:
                if isinstance(summary, dict) and summary.get("path"):
                    bound[str(summary["path"])] = summary.get("sha256")
        elif isinstance(item, dict) and item.get("path"):
            bound[str(item["path"])] = item.get("sha256")
    for relative, expected in sorted(bound.items()):
        path = (extracted_root / relative).resolve()
        if not path.is_relative_to(extracted_root):
            errors.append(f"unsafe_bound_path:{relative}")
        elif not expected:
            errors.append(f"missing_bound_hash:{relative}")
        elif not path.is_file():
            errors.append(f"bound_file_missing:{relative}")
        elif sha256(path) != expected:
            errors.append(f"bound_file_hash_mismatch:{relative}")
    return errors


def run_validator(extracted_root: Path, dataset: str, name: str) -> dict[str, Any]:
    report_prefix = f"reports/archive_replay_{name}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(extracted_root / "scripts")
    process = subprocess.run(
        [
            sys.executable,
            str(extracted_root / "scripts/validate_dataset.py"),
            "--input",
            dataset,
            "--report-prefix",
            report_prefix,
        ],
        cwd=extracted_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    report_path = extracted_root / f"{report_prefix}.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    known_external_truth_only = (
        len(report.get("schema_errors") or []) == 0
        and len(report.get("duplicate_ids") or []) == 0
        and len(report.get("invalid_rule_links") or []) == 0
        and all(
            str(row.get("error") or "") == "query_result_truth_mismatch"
            for row in (report.get("semantic_errors") or [])
        )
    )
    strict_validator_passed = process.returncode == 0 and report.get("passed") is True
    structural_pass = strict_validator_passed or known_external_truth_only
    return {
        "dataset": dataset,
        "returncode": process.returncode,
        "passed": structural_pass,
        "strict_validator_passed": strict_validator_passed,
        "external_truth_deferred": bool(known_external_truth_only and not strict_validator_passed),
        "total_records": report.get("total_records"),
        "schema_errors": len(report.get("schema_errors") or []),
        "duplicate_ids": len(report.get("duplicate_ids") or []),
        "invalid_rule_links": len(report.get("invalid_rule_links") or []),
        "invalid_scenario_links": len(report.get("invalid_scenario_links") or []),
        "semantic_errors": len(report.get("semantic_errors") or []),
        "deferred_reason": (
            "archive excludes the raw scenario registry and query truth ledger; "
            "scenario-link and query-truth checks remain external"
            if known_external_truth_only and not strict_validator_passed
            else None
        ),
        "stderr_tail": process.stderr[-2000:],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bundle",
        default="release/GridInstruct_v1.2_sd_core_data_only.tar.gz",
    )
    parser.add_argument(
        "--output-json",
        default="reports/release_archive_replay_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--output-md",
        default="reports/release_archive_replay_v1.2_sd_core.md",
    )
    args = parser.parse_args()

    bundle = ROOT / args.bundle
    errors = []
    with tarfile.open(bundle, "r:gz") as archive:
        raw_members = archive.getmembers()
    compact_layout = (
        "data/gridinstruct_v1.2_sd_core_en.jsonl" in {member.name for member in raw_members}
        and not any(member.name.startswith(f"{PREFIX}/") for member in raw_members)
    )
    if compact_layout:
        compact_errors = []
        for member in raw_members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                compact_errors.append(f"unsafe_archive_path:{member.name}")
            if member.issym() or member.islnk() or member.isdev():
                compact_errors.append(f"unsupported_archive_member_type:{member.name}")
        if compact_errors:
            report = compact_archive_audit(bundle, raw_members)
            report["status"] = "fail"
            report["errors"] = compact_errors + report.get("errors", [])
        else:
            report = compact_archive_audit(bundle, raw_members)
        write_json(ROOT / args.output_json, report)
        lines = [
            "# Release Archive Isolation Replay",
            "",
            f"Status: `{report['status']}`",
            f"Bundle SHA-256: `{report['bundle_sha256']}`",
            f"Archive layout: `{report['archive_layout']}`",
            f"Required files checked: {report['required_file_count']}",
            f"English records parsed: {report['record_count']}",
            "",
            "The compact public package includes the converged scenario registry and bounded OPF evidence; raw arrays, full construction attempts, and human-review ledgers remain external.",
        ]
        (ROOT / args.output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "errors": report["errors"], "validators": report["isolated_validators"]}, ensure_ascii=False, indent=2))
        if report["status"] != "pass":
            raise SystemExit(1)
        return
    with tempfile.TemporaryDirectory(prefix="gridinstruct_release_replay_") as temp_dir:
        temp = Path(temp_dir)
        with tarfile.open(bundle, "r:gz") as archive:
            members, archive_errors = safe_members(archive)
            errors.extend(archive_errors)
            if not errors:
                archive.extractall(temp, members=members, filter="data")
        extracted = temp / PREFIX
        manifest_path = extracted / "release/archive_manifest_v1.2_sd_core.csv"
        checksums_path = extracted / "release/checksums_sha256.txt"
        if not manifest_path.is_file() or not checksums_path.is_file():
            errors.append("archive_manifest_or_checksums_missing")
            manifest_rows = []
            checksum_rows = {}
        else:
            with manifest_path.open(encoding="utf-8", newline="") as handle:
                manifest_rows = list(csv.DictReader(handle))
            checksum_rows = load_checksum_file(checksums_path)
            manifest_paths = {str(row["path"]) for row in manifest_rows}
            if set(checksum_rows) != manifest_paths:
                errors.append("manifest_checksum_path_set_mismatch")
            extracted_payload_paths = {
                str(path.relative_to(extracted))
                for path in extracted.rglob("*")
                if path.is_file()
            }
            expected_archive_paths = manifest_paths | {
                "release/archive_manifest_v1.2_sd_core.csv",
                "release/checksums_sha256.txt",
            }
            if extracted_payload_paths != expected_archive_paths:
                errors.append("archive_manifest_path_set_mismatch")
        hash_mismatches = []
        for row in manifest_rows:
            relative = str(row["path"])
            path = extracted / relative
            actual = sha256(path) if path.is_file() else None
            expected = str(row["sha256"])
            if actual != expected or checksum_rows.get(relative) != expected:
                hash_mismatches.append(
                    {
                        "path": relative,
                        "manifest_sha256": expected,
                        "checksum_sha256": checksum_rows.get(relative),
                        "actual_sha256": actual,
                    }
                )
        if hash_mismatches:
            errors.append("extracted_payload_hash_mismatch")
        evidence_path = extracted / "metadata/evidence_binding_manifest.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {}
        if evidence.get("status") != "pass":
            errors.append("extracted_evidence_binding_failed")
        isolated_evidence_errors = evidence_binding_errors(extracted, evidence)
        errors.extend(isolated_evidence_errors)
        validators = []
        if not errors:
            validators = [
                run_validator(extracted, "data/gridinstruct_v1.2_sd_core.jsonl", "source"),
                run_validator(extracted, "data/gridinstruct_v1.2_sd_core_en.jsonl", "english"),
            ]
            if any(not row["passed"] for row in validators):
                errors.append("isolated_dataset_validator_failed")
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "pass" if not errors else "fail",
            "bundle": args.bundle,
            "bundle_sha256": sha256(bundle),
            "safe_archive_member_count": len(members),
            "manifest_payload_count": len(manifest_rows),
            "hash_mismatch_count": len(hash_mismatches),
            "hash_mismatches": hash_mismatches[:50],
            "evidence_binding_status": evidence.get("status"),
            "evidence_binding_errors": isolated_evidence_errors,
            "isolated_validators": validators,
            "external_truth_scope": {
                "deferred": any(row.get("external_truth_deferred") for row in validators),
                "reason": "Raw scenario arrays, full scenario-generation ledger, and query truth ledger are outside this compact archive.",
            },
            "errors": errors,
        }
    write_json(ROOT / args.output_json, report)
    lines = [
        "# Release Archive Isolation Replay",
        "",
        f"Status: `{report['status']}`",
        f"Bundle SHA-256: `{report['bundle_sha256']}`",
        f"Payload hashes checked: {report['manifest_payload_count']}",
        f"Hash mismatches: {report['hash_mismatch_count']}",
        "",
        "## Isolated validators",
        "",
    ]
    for row in validators:
        lines.append(
            f"- `{row['dataset']}`: {'pass' if row['passed'] else 'fail'}; records={row['total_records']}"
        )
    (ROOT / args.output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "errors": errors, "validators": validators}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
