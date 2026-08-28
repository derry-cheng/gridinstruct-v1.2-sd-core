#!/usr/bin/env python3
"""Build the small, current public-review archive.

The full local evidence tree contains raw ledgers and duplicated stress
projections that are useful for reconstruction but are not required for the
anonymous review package.  This script selects the current compact surface,
dereferences every member, and writes deterministic tar/gzip metadata.  It
does not create human judgements or assign a data DOI.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path

from gridinstruct_utils import ROOT, ensure_dirs


BASE_MEMBERS = {
    "LICENSE-CODE",
    "LICENSE-DATA",
    "MANIFEST.md",
    "README.md",
    "release/README.md",
    "release/reviewer_access_manifest_2026-08-13.json",
    "paper/scientific_data_latex/PAPER_CLAIM_AUDIT.json",
    "data/gridinstruct_v1.2_sd_core_en.jsonl",
    "data/international_rule_probe_v1.jsonl",
    "data/v1.2_sd_core_exact_surface_train_ids.jsonl",
    "data/v1.2_sd_core_exact_surface_validation_ids.jsonl",
    "data/v1.2_sd_core_exact_surface_test_ids.jsonl",
    "data/v1.2_sd_core_exact_surface_ood_test_ids.jsonl",
    "data/v1.2_sd_core_near_neighbor_free_train_ids.jsonl",
    "data/v1.2_sd_core_near_neighbor_free_validation_ids.jsonl",
    "data/v1.2_sd_core_near_neighbor_free_test_ids.jsonl",
    "data/v1.2_sd_core_near_neighbor_free_ood_test_ids.jsonl",
    "data/v1.2_sd_core_template_family_holdout_train_ids.jsonl",
    "data/v1.2_sd_core_template_family_holdout_validation_ids.jsonl",
    "data/v1.2_sd_core_template_family_holdout_test_ids.jsonl",
    "metadata/archive_metadata.json",
    "metadata/data_descriptor_audit.json",
    "metadata/data_dictionary.csv",
    "metadata/data_lineage_manifest.json",
    "metadata/dataset_metadata.json",
    "metadata/evidence_binding_manifest.json",
    "metadata/expert_review_protocol.json",
    "metadata/international_rule_probe_splits_v1.json",
    "metadata/rule_clause_matrix.json",
    "metadata/rule_dictionary.csv",
    "metadata/schema.json",
    "metadata/third_party_asset_inventory.json",
    "third_party/pglib-opf-v23.07/LICENSE",
    "third_party/pglib-opf-v23.07/UPSTREAM_COMMIT",
    "benchmark/direct_english_tfidf_v1.2_sd_core_leakage_fixed_report.json",
    "benchmark/instruction_surface_balanced_v1.2_sd_core_leakage_fixed/tfidf_report.json",
    "benchmark/international_rule_probe_v1/nearest_neighbor_report.json",
    "benchmark/international_rule_probe_v1/nearest_neighbor_report.md",
    "reports/action_level_validation_audit_v1.2_sd_core.json",
    "reports/core_envelope_replay_v1.2_sd_core.json",
    "reports/current_opf_closed_loop_audit_v1.2_sd_core.json",
    "reports/current_quality_snapshot_v1.2_sd_core.json",
    "reports/current_release_integrity_audit_v1.2_sd_core.json",
    "reports/current_surface_seed_stability_v1.2_sd_core.json",
    "reports/direct_english_canonical_materialization_v1.2_sd_core.json",
    "reports/direct_english_canonical_materialization_v1.2_sd_core.md",
    "reports/evidence_tiers_v1.2_sd_core.json",
    "reports/evidence_tiers_v1.2_sd_core.md",
    "reports/expert_review_execution_check_v1.2_sd_core.json",
    "reports/expert_review_execution_check_v1.2_sd_core.md",
    "reports/expert_review_package_v1.2_sd_core.json",
    "reports/expert_review_package_v1.2_sd_core.md",
    "reports/expert_review_readiness_audit_v1.2_sd_core.json",
    "reports/expert_review_readiness_audit_v1.2_sd_core.md",
    "reports/independent_opf_envelope_v1.2_sd_core.json",
    "reports/independent_solver_raw_evidence_v1.2_sd_core_rebound.json",
    "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
    "reports/instruction_surface_balanced_split_v1.2_sd_core.json",
    "reports/instruction_surface_balanced_surface_audit_v1.2_sd_core.json",
    "reports/international_rule_probe_controls_v1.json",
    "reports/international_rule_probe_controls_v1.md",
    "reports/international_rule_probe_splits_v1.json",
    "reports/international_rule_probe_v1.json",
    "reports/international_rule_probe_v1.md",
    "reports/international_rule_review_assignments_v1.csv",
    "reports/international_rule_review_assignments_v1.json",
    "reports/international_rule_review_assignments_v1.jsonl",
    "rules/international_rule_profiles.json",
    "rules/regulation_rules.json",
}

CURRENT_DOCUMENTS = {
    "docs/AVAILABILITY_AND_LIMITATIONS.md",
    "docs/DATA_GENERATION_LINEAGE.md",
    "docs/DATA_RECORDS.md",
    "docs/EXPERT_REVIEW_PROTOCOL.md",
    "docs/REPRODUCTION.md",
    "docs/REVIEW_ISSUE_CLOSURE_2026-08-28.md",
    "docs/SCIENTIFIC_DATA_READINESS_CHECKLIST.md",
    "docs/PROJECT_STRUCTURE.md",
    "docs/ARTIFACT_INDEX_2026-08-13.md",
    "docs/THIRD_PARTY_ASSETS.md",
    "docs/LICENSES_AND_CITATION.md",
    "docs/TECHNICAL_VALIDATION.md",
    "reports/release_claim_alignment_audit_v1.2_sd_core.json",
    "figures/sd_core_publication/fig0_framework.drawio",
    "figures/sd_core_publication/fig_opf_detail.drawio",
    "figures/sd_core_publication/fig0_framework.png",
    "figures/sd_core_publication/fig_opf_detail.png",
    "review_packages/stratified_expert_review_v1.2_sd_core/README.md",
    "review_packages/stratified_expert_review_v1.2_sd_core/sample_manifest.csv",
    "review_packages/stratified_expert_review_v1.2_sd_core/review_packet_blinded.jsonl",
    "review_packages/stratified_expert_review_v1.2_sd_core/review_assignments.csv",
    "review_packages/stratified_expert_review_v1.2_sd_core/human_review_log_template.csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default="release/GridInstruct_v1.2_sd_core_data_only.tar.gz")
    parser.add_argument("--manifest", default="release/compact_archive_manifest_v1.2_sd_core.json")
    args = parser.parse_args()
    bundle = ROOT / args.bundle
    members = sorted(BASE_MEMBERS | CURRENT_DOCUMENTS)
    missing = [relative for relative in members if not (ROOT / relative).is_file()]
    if missing:
        raise SystemExit("missing compact archive members: " + ", ".join(missing))
    ensure_dirs(bundle.parent)
    if bundle.exists():
        bundle.unlink()
    with bundle.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as gzip_handle:
            with tarfile.open(fileobj=gzip_handle, mode="w", dereference=True) as archive:
                for relative in members:
                    source = ROOT / relative
                    info = archive.gettarinfo(str(source), arcname=relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = 0o644
                    with source.open("rb") as handle:
                        archive.addfile(info, handle)
    bundle_hash = sha256(bundle)
    sidecar = bundle.with_suffix(bundle.suffix + ".sha256")
    sidecar.write_text(f"{bundle_hash}  {bundle.name}\n", encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "generated_at": "1970-01-01T00:00:00+00:00",
        "timestamp_policy": "deterministic SOURCE_DATE_EPOCH=0 archive metadata",
        "archive_layout": "compact_public_review_package",
        "bundle": str(bundle.relative_to(ROOT)),
        "bundle_sha256": bundle_hash,
        "member_count": len(members),
        "members": [
            {"path": relative, "bytes": (ROOT / relative).stat().st_size, "sha256": sha256(ROOT / relative)}
            for relative in members
        ],
        "scope": {
            "canonical_records": 95479,
            "international_probe_records": 512,
            "core_review_samples": 800,
            "core_review_assignment_slots": 1600,
            "core_review_completed_rows": 0,
            "planned_reviewer_pool_size": 5,
            "strict_source_group_projections": "retained in the local evidence tree; the compact archive exposes their receipt, not duplicate full projections",
            "raw_scenario_and_full_candidate_ledgers": "deferred outside compact package",
            "data_doi": "pending",
        },
    }
    (ROOT / args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "bundle": str(bundle), "bundle_sha256": bundle_hash, "member_count": len(members)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
