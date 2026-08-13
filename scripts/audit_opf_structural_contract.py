#!/usr/bin/env python3
"""Verify the explicit structural denominators of released OPF records."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output", default="reports/opf_structural_contract_audit_v1.2_sd_core.json")
    args = parser.parse_args()
    input_path = ROOT / args.input
    rows = [row for row in read_jsonl(input_path) if (row.get("metadata") or {}).get("opf_closed_loop") is True]
    failures: list[dict[str, Any]] = []
    scenario_ids = {str(row.get("scenario_id") or "") for row in rows}
    variant_counts: dict[str, int] = {}
    for row in rows:
        scenario_id = str(row.get("scenario_id") or "")
        variant_counts[scenario_id] = variant_counts.get(scenario_id, 0) + 1
        validation = row.get("closed_loop_validation") or {}
        n1 = validation.get("post_action_n1") or {}
        active = validation.get("active_load_uncertainty_construction_gate") or {}
        constant_pf = validation.get("constant_power_factor_uncertainty_construction_gate") or {}
        observed = {
            "n1_requested": n1.get("requested_checks"),
            "n1_completed": n1.get("completed_checks"),
            "n1_secure": n1.get("secure_checks"),
            "n1_detail_count": len(n1.get("checks") or []),
            "active_registered": active.get("registered_case_count"),
            "active_expected": active.get("expected_case_count"),
            "active_safe": active.get("safe_case_count"),
            "active_complete": active.get("denominator_complete"),
            "constant_pf_registered": constant_pf.get("registered_case_count"),
            "constant_pf_expected": constant_pf.get("expected_case_count"),
            "constant_pf_safe": constant_pf.get("safe_case_count"),
            "constant_pf_complete": constant_pf.get("denominator_complete"),
        }
        expected = {
            "n1_requested": 7,
            "n1_completed": 7,
            "n1_secure": 7,
            "n1_detail_count": 7,
            "active_registered": 48,
            "active_expected": 48,
            "active_safe": 48,
            "active_complete": True,
            "constant_pf_registered": 48,
            "constant_pf_expected": 48,
            "constant_pf_safe": 48,
            "constant_pf_complete": True,
        }
        mismatches = {key: {"observed": observed[key], "expected": value} for key, value in expected.items() if observed[key] != value}
        if mismatches:
            failures.append({"record_id": row.get("id"), "scenario_id": scenario_id, "mismatches": mismatches})
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if rows and not failures and len(scenario_ids) == 160 else "fail",
        "contract": "released_opf_structural_denominators_v1",
        "input": {"path": args.input, "sha256": file_sha256(input_path)},
        "record_count": len(rows),
        "unique_scenario_count": len(scenario_ids),
        "variant_count_distribution": {str(value): list(variant_counts.values()).count(value) for value in sorted(set(variant_counts.values()))},
        "n1_contract": {"requested_per_record": 7, "completed_per_record": 7, "secure_per_record": 7, "detail_entries_per_record": 7},
        "uncertainty_contract": {"models": 2, "states_per_model": 48, "states_per_record": 96, "all_registered_states_safe": True},
        "checked_failures": failures,
        "scope": "Released-field structural contract audit; it does not recompute the external scenario arrays or certify independent OPF optimality.",
    }
    output = ROOT / args.output
    ensure_dirs(output.parent)
    write_json(output, report)
    print(json.dumps({"status": report["status"], "records": len(rows), "scenarios": len(scenario_ids), "failures": len(failures)}, ensure_ascii=False))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
