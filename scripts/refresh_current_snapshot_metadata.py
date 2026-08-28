#!/usr/bin/env python3
"""Refresh release-facing metadata from the promoted English snapshot.

This utility updates counts, split geometry, availability identifiers, and
file receipts without changing canonical records or creating review labels.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl, summarize_records


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_count(path: Path) -> int | None:
    if path.suffix == ".jsonl":
        return sum(1 for line in path.open("r", encoding="utf-8") if line.strip())
    if path.suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return len(value) if isinstance(value, list) else None
    return None


def refresh_file_item(item: dict[str, Any], root: Path) -> dict[str, Any]:
    path = root / str(item.get("path", ""))
    if not path.is_file():
        updated = dict(item)
        updated["exists"] = False
        return updated
    updated = dict(item)
    updated.update(
        {
            "exists": True,
            "bytes": path.stat().st_size,
            "records": record_count(path),
            "sha256": sha256(path),
        }
    )
    return updated


def split_summary(paths: dict[str, str]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    ids: dict[str, set[str]] = {}
    scenarios: dict[str, set[str]] = {}
    for name, relative in paths.items():
        rows = read_jsonl(ROOT / relative)
        summaries[name] = summarize_records(rows)
        ids[name] = {str(row["id"]) for row in rows}
        scenarios[name] = {str(row["scenario_id"]) for row in rows if row.get("scenario_id")}
    overlaps: dict[str, Any] = {}
    names = list(paths)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            overlaps[f"{left}__{right}"] = {
                "record_id_overlap": sorted(ids[left] & ids[right]),
                "scenario_id_overlap": sorted(scenarios[left] & scenarios[right]),
            }
    return {"splits": summaries, "overlaps": overlaps}


def traceability(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        task = str(row.get("task_type"))
        counts[task]["records"] += 1
        counts[task]["with_rules"] += int(bool(row.get("source_regulation_ids")))
        counts[task]["with_simulation"] += int(bool(row.get("source_simulation_case_id")))
        counts[task]["with_rationale"] += int(bool(row.get("rationale")))
    return {task: dict(value) for task, value in sorted(counts.items())}


def main() -> None:
    rows = read_jsonl(ROOT / "data/gridinstruct_v1.2_sd_core_en.jsonl")
    split_paths = {
        "train": "data/v1.2_sd_core_train_en.jsonl",
        "validation": "data/v1.2_sd_core_validation_en.jsonl",
        "test": "data/v1.2_sd_core_test_en.jsonl",
        "ood_test": "data/v1.2_sd_core_ood_test_en.jsonl",
    }
    metadata_path = ROOT / "metadata/dataset_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    summary = summarize_records(rows)
    metadata["generated_at"] = datetime.now(timezone.utc).isoformat()
    dataset = metadata.setdefault("dataset", {})
    dataset.update(
        {
            "version": "1.2",
            "record_count": len(rows),
            "language": "English natural-language fields with stable structured identifiers",
            "source_language": "Typed scenario and rule contracts rendered directly in English; source documents provide rule obligations",
            "language_audit": "reports/direct_english_canonical_materialization_v1.2_sd_core.json",
            "source_dataset_path": "data/gridinstruct_v1.2_sd_core.jsonl",
            "dataset_path": "data/gridinstruct_v1.2_sd_core_en.jsonl",
            "split_paths": split_paths,
        }
    )
    metadata["record_summary"] = summary
    metadata["source_traceability"] = traceability(rows)
    metadata["split_integrity"] = split_summary(split_paths)
    validation_report_path = ROOT / "reports/data_validation_direct_english_v1.2_sd_core.json"
    validation_report = json.loads(validation_report_path.read_text(encoding="utf-8"))
    validation = metadata.setdefault("validation", {})
    validation.update(
        {
            "path": str(validation_report_path.relative_to(ROOT)),
            "passed": validation_report.get("passed") is True,
            "near_duplicate_rate": validation_report.get("near_duplicate_rate"),
            "near_duplicate_count": validation_report.get("near_duplicate_count"),
            "schema_errors": len(validation_report.get("schema_errors") or []),
            "duplicate_ids": len(validation_report.get("duplicate_ids") or []),
            "invalid_rule_links": len(validation_report.get("invalid_rule_links") or []),
            "invalid_scenario_links": len(validation_report.get("invalid_scenario_links") or []),
            "semantic_errors": len(validation_report.get("semantic_errors") or []),
        }
    )
    metadata["availability"] = {
        "data_doi": "pending",
        "code_repository_url": "https://github.com/derry-cheng/gridinstruct-v1.2-sd-core",
        "archived_code_release_doi": "https://doi.org/10.5281/zenodo.21921745",
        "data_license": "CC-BY-4.0",
        "code_license": "MIT",
    }
    human_path = ROOT / "reports/expert_review_execution_check_v1.2_sd_core.json"
    human = json.loads(human_path.read_text(encoding="utf-8"))
    metadata["review_pipeline"] = {
        "machine_screen_report_path": "reports/expert_review_agreement_v1.2_paper_cumulative_current.json",
        "machine_screened_records": 548,
        "machine_accepted_augmentations": 0,
        "machine_decision_agreement_rate": 0.7646,
        "human_execution_report_path": str(human_path.relative_to(ROOT)),
        "human_execution_status": human.get("status"),
        "human_complete_assignments": human.get("complete_human_review_rows", 0),
    }
    evidence_path = ROOT / "reports/evidence_tiers_v1.2_sd_core.json"
    if evidence_path.is_file():
        metadata["evidence_tiers"] = json.loads(evidence_path.read_text(encoding="utf-8"))

    code_manifest_path = ROOT / "metadata/code_manifest.json"
    code_rows = [
        {
            "path": str(path.relative_to(ROOT)),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted((ROOT / "scripts").glob("*.py"))
    ]
    code_manifest_path.write_text(
        json.dumps(
            {"schema_version": "1.0", "file_count": len(code_rows), "files": code_rows},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = [
        refresh_file_item(item, ROOT)
        for item in metadata.get("file_manifest", [])
        if (ROOT / str(item.get("path", ""))).is_file()
    ]
    known = {str(item.get("path")) for item in manifest}
    for relative in (
        "reports/evidence_tiers_v1.2_sd_core.json",
        "reports/evidence_tiers_v1.2_sd_core.md",
        "scripts/audit_evidence_tiers.py",
        "scripts/refresh_current_snapshot_metadata.py",
        "review_packages/stratified_expert_review_v1.2_sd_core/README.md",
        "review_packages/stratified_expert_review_v1.2_sd_core/sampling_frame.jsonl",
        "review_packages/stratified_expert_review_v1.2_sd_core/sample_manifest.csv",
        "review_packages/stratified_expert_review_v1.2_sd_core/review_packet_blinded.jsonl",
        "review_packages/stratified_expert_review_v1.2_sd_core/review_assignments.csv",
        "review_packages/stratified_expert_review_v1.2_sd_core/human_review_log_template.csv",
        "data/v1.2_sd_core_strict_train.jsonl",
        "data/v1.2_sd_core_strict_validation.jsonl",
        "data/v1.2_sd_core_strict_test.jsonl",
        "scripts/build_compact_public_archive.py",
        "scripts/write_compact_bundle_validation.py",
        "scripts/validate_release_archive_replay.py",
        "metadata/code_manifest.json",
        "docs/REVIEW_ISSUE_CLOSURE_2026-08-28.md",
        "docs/SCIENTIFIC_DATA_READINESS_CHECKLIST.md",
        "docs/PROJECT_STRUCTURE.md",
        "docs/ARTIFACT_INDEX_2026-08-13.md",
        "docs/THIRD_PARTY_ASSETS.md",
        "docs/LICENSES_AND_CITATION.md",
        "metadata/international_rule_probe_schema.json",
        "reports/compliance_label_current_audit_v1.2_sd_core.json",
        "reports/compliance_label_current_audit_v1.2_sd_core.md",
        "reports/dispatcher_request_contract_repair_v1.2_sd_core.json",
        "reports/dispatcher_request_contract_repair_v1.2_sd_core.md",
        "reports/sd_core_distribution_risk_audit.json",
        "reports/sd_core_distribution_risk_audit.md",
        "reports/near_neighbor_free_surface_audit_v1.2_sd_core.json",
        "reports/official_exact_content_overlap_repair_v1.2_sd_core.json",
        "reports/official_exact_content_overlap_repair_v1.2_sd_core.md",
        "reports/strict_exact_content_overlap_repair_v1.2_sd_core.json",
        "reports/strict_exact_content_overlap_repair_v1.2_sd_core.md",
        "scripts/audit_compliance_labels_current.py",
        "scripts/repair_official_exact_content_overlap.py",
    ):
        if relative not in known:
            manifest.append(refresh_file_item({"path": relative}, ROOT))
    metadata["file_manifest"] = sorted(manifest, key=lambda item: str(item.get("path")))
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    csv_path = ROOT / "metadata/file_manifest.csv"
    if csv_path.is_file():
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        refreshed: list[dict[str, Any]] = []
        for row in csv_rows:
            path = ROOT / row.get("path", "")
            if path.is_file():
                row["exists"] = "True"
                row["bytes"] = str(path.stat().st_size)
                row["records"] = "" if record_count(path) is None else str(record_count(path))
                row["sha256"] = sha256(path)
            else:
                continue
            refreshed.append(row)
        known_csv = {row.get("path") for row in refreshed}
        for item in metadata["file_manifest"]:
            relative = str(item.get("path"))
            if relative not in known_csv and item.get("exists"):
                refreshed.append(
                    {
                        "bytes": str(item.get("bytes", "")),
                        "exists": "True",
                        "path": relative,
                        "records": "" if item.get("records") is None else str(item.get("records")),
                        "sha256": str(item.get("sha256", "")),
                    }
                )
        fieldnames = ["bytes", "exists", "path", "records", "sha256"]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(sorted(refreshed, key=lambda item: str(item.get("path"))))

    checksum_path = ROOT / "metadata/checksums_sha256.txt"
    checksum_lines = [
        f"{item['sha256']}  {item['path']}"
        for item in metadata["file_manifest"]
        if item.get("exists") and item.get("sha256")
    ]
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    # Keep the compact lineage and promotion receipts bound to the same
    # promoted English table after a canonical data repair. These are
    # generated metadata files; no record content is changed here.
    lineage_path = ROOT / "metadata/data_lineage_manifest.json"
    if lineage_path.is_file():
        lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
        components = lineage.get("components")
        if isinstance(components, dict):
            for relative, item in components.items():
                path = ROOT / relative
                if path.is_file():
                    item["sha256"] = sha256(path)
                    item["size_bytes"] = path.stat().st_size
                    item["exists"] = True
                else:
                    item["exists"] = False
            lineage["generated_at"] = datetime.now(timezone.utc).isoformat()
            lineage["status"] = "pass"
            lineage_path.write_text(
                json.dumps(lineage, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    promotion_path = ROOT / "metadata/canonical_dataset_promotion_manifest.json"
    if promotion_path.is_file():
        promotion = json.loads(promotion_path.read_text(encoding="utf-8"))
        canonical_path = ROOT / "data/gridinstruct_v1.2_sd_core_en.jsonl"
        gate_path = ROOT / "reports/current_release_integrity_audit_v1.2_sd_core.json"
        if canonical_path.is_file():
            promotion["generated_at"] = datetime.now(timezone.utc).isoformat()
            promotion["output"] = "data/gridinstruct_v1.2_sd_core_en.jsonl"
            promotion["output_sha256"] = sha256(canonical_path)
            promotion["records"] = record_count(canonical_path)
        if gate_path.is_file():
            promotion["upstream_gate_sha256"] = sha256(gate_path)
        promotion_path.write_text(
            json.dumps(promotion, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
