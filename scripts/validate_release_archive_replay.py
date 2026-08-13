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
        default="release/GridInstruct_v1.2_sd_core_release_candidate.tar.gz",
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
