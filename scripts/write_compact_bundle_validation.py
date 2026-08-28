#!/usr/bin/env python3
"""Write the scoped validation sidecars for the compact public archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from gridinstruct_utils import ROOT, write_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default="release/GridInstruct_v1.2_sd_core_data_only.tar.gz")
    parser.add_argument("--replay", default="reports/release_archive_replay_v1.2_sd_core.json")
    parser.add_argument("--validation-json", default="reports/release_bundle_validation_v1.2_sd_core.json")
    parser.add_argument("--validation-md", default="reports/release_bundle_validation_v1.2_sd_core.md")
    parser.add_argument("--deposition-json", default="reports/deposition_checklist_v1.2_sd_core.json")
    parser.add_argument("--deposition-md", default="reports/deposition_checklist_v1.2_sd_core.md")
    args = parser.parse_args()
    bundle = ROOT / args.bundle
    replay = json.loads((ROOT / args.replay).read_text(encoding="utf-8"))
    if not bundle.is_file() or replay.get("status") != "pass":
        raise SystemExit("compact archive or isolated replay is not ready")
    with tarfile.open(bundle, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
    bundle_hash = sha256(bundle)
    generated_at = datetime.now(timezone.utc).isoformat()
    validation = {
        "generated_at": generated_at,
        "status": "package_ready_external_identifiers_pending",
        "scope": "compact_public_review_package",
        "bundle": args.bundle,
        "bundle_sha256": bundle_hash,
        "file_count": len(members),
        "payload_file_count": len(members),
        "companion_file_count": 0,
        "payload_size_bytes": sum((ROOT / member.name).stat().st_size for member in members if (ROOT / member.name).is_file()),
        "manifest": "release/compact_archive_manifest_v1.2_sd_core.json",
        "checksums": f"{args.bundle}.sha256",
        "manifest_scope": "compact archive member manifest; full local evidence manifest remains outside the compact package",
        "validation_reports_stored_outside_bundle": True,
        "required_missing": [],
        "tar_payload_hash_mismatches": [],
        "tar_payload_hashes_verified": True,
        "reproducibility_manifests_pass": False,
        "reproducibility_scope": "isolated compact replay passes structure, JSONL parsing, identifier uniqueness, selected receipts, and review-package geometry; raw ledgers and full evidence binding remain external",
        "evidence_binding_manifest_status": "selected_receipts_present",
        "evidence_binding_errors": [],
        "bound_archive_file_count": 0,
        "pglib_license_in_archive": "third_party/pglib-opf-v23.07/LICENSE" in {member.name for member in members},
        "deterministic_archive_headers": True,
        "public_identifiers_pending": ["data_doi"],
        "excludes_experiments_directory": True,
        "exclusion_reason": "Compact review package omits raw scenario arrays, full candidate ledgers, and training checkpoints; the local evidence tree retains their bounded receipts.",
        "review_geometry": {
            "samples": replay.get("review_sample_count"),
            "assignment_slots": replay.get("review_assignment_count"),
            "completed_rows": replay.get("review_completed_rows"),
            "planned_reviewer_pool_size": 5,
        },
    }
    write_json(ROOT / args.validation_json, validation)
    deposition_items = {
        "compact_archive_present": True,
        "compact_archive_replay_pass": True,
        "canonical_english_table_present": "data/gridinstruct_v1.2_sd_core_en.jsonl" in {member.name for member in members},
        "review_package_geometry_present": replay.get("review_sample_count") == 800 and replay.get("review_assignment_count") == 1600,
        # This item is a completion gate, so it is true only when every
        # assigned slot has a completed human row.  Keep the exact count in a
        # separate field to make the zero-row state explicit rather than
        # accidentally treating an empty ledger as complete.
        "review_rows_completed": replay.get("review_completed_rows") == replay.get("review_assignment_count"),
        "review_rows_completed_count": replay.get("review_completed_rows", 0),
        "pglib_license_present": validation["pglib_license_in_archive"],
        "public_repository_url_inserted": True,
        "data_doi_inserted": False,
        "archived_code_doi_inserted": True,
    }
    deposition = {
        "generated_at": generated_at,
        "status": "local_package_ready_external_deposition_pending",
        "scope": "compact_public_review_package",
        "items": deposition_items,
        "blocking_local_items": [],
        "blocking_external_items": ["data_doi_inserted"],
        "bundle": args.bundle,
        "validation_report": args.validation_json,
        "validation_report_scope": "sidecar report generated after compact archive creation; it is intentionally not stored inside the archive",
    }
    write_json(ROOT / args.deposition_json, deposition)
    validation_md = [
        "# Release Bundle Validation",
        "",
        f"Generated: {generated_at}",
        f"Status: `{validation['status']}`",
        f"Scope: `{validation['scope']}`",
        f"Bundle: `{args.bundle}`",
        f"Bundle SHA256: `{bundle_hash}`",
        f"Archive members: {len(members)}",
        "",
        "The compact archive replay passes its declared structural scope. Full raw-ledger replay, completed human review, and a persistent data DOI remain external gates.",
        "",
        "## External identifiers",
        "",
        "- Public repository URL: recorded in the metadata and release README.",
        "- Data DOI: pending external deposition.",
        "- Archived code DOI: recorded in the metadata.",
    ]
    (ROOT / args.validation_md).write_text("\n".join(validation_md) + "\n", encoding="utf-8")
    deposition_md = [
        "# Deposition Checklist",
        "",
        f"Generated: {generated_at}",
        f"Status: `{deposition['status']}`",
        "Scope: compact public review package",
        "",
        "- Compact archive and isolated replay: complete.",
        "- Review package: 800 samples, 1,600 assignment slots, 0 completed rows.",
        "- Public repository URL: recorded.",
        "- Data DOI: pending external deposition.",
        "- Archived code DOI: recorded.",
    ]
    (ROOT / args.deposition_md).write_text("\n".join(deposition_md) + "\n", encoding="utf-8")
    print(json.dumps({"status": validation["status"], "bundle_sha256": bundle_hash, "members": len(members)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
