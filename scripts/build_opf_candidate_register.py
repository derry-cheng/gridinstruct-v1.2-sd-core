#!/usr/bin/env python3
"""Create a compact, hash-bound candidate denominator register.

The full screening report is intentionally large.  This register keeps the
counts and selection contract needed to audit the paper's denominator without
copying solver arrays into a second artifact or claiming that omitted raw
arrays are locally replayable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--output",
        default="reports/opf_candidate_register_v1.2_sd_core.json",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / args.source
    payload: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    register = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if payload.get("status") == "pass" else "fail",
        "source_report": args.source,
        "source_report_sha256": sha256(source),
        "contract": payload.get("contract"),
        "selection_policy": payload.get("selection_policy"),
        "denominator": {
            "registered_candidates": payload.get("registered_candidate_count"),
            "registered_unique_scenarios": payload.get("registered_unique_scenario_count"),
            "power_flow_converged": (payload.get("power_flow_outcomes") or {}).get("converged"),
            "power_flow_unsupplied_island": (payload.get("power_flow_outcomes") or {}).get("unsupplied_island"),
            "eligible_violation_candidates": payload.get("eligible_violation_candidate_count"),
            "screened_candidates": payload.get("screened_candidate_count"),
            "screened_passes": payload.get("screened_pass_count"),
            "screened_rejections": payload.get("screened_rejection_count"),
            "eligible_not_screened_after_target": payload.get("eligible_not_screened_after_target_count"),
            "selected_target": payload.get("target_passed_candidates"),
        },
        "acceptance_gate": payload.get("candidate_acceptance_gate"),
        "post_action_checks": {
            "network_n1_checks": payload.get("post_action_n1_checks"),
            "generator_n1_checks": payload.get("post_action_generator_n1_checks"),
            "loading_margins_percent": payload.get("loading_margins"),
            "power_balance_tolerance_mw": payload.get("power_balance_tolerance_mw"),
        },
        "selected_coverage": {
            "load_levels": payload.get("passed_load_levels"),
            "generator_factors": payload.get("passed_generator_factors"),
            "line_pairs": payload.get("passed_line_pairs"),
            "diversity": payload.get("passed_diversity"),
        },
        "hard_gates": payload.get("hard_gates"),
        "scope_boundary": {
            "local_artifact": "compact denominator and selection receipt",
            "raw_solver_arrays_present": False,
            "full_candidate_replay_claim": False,
            "required_external_artifacts": [
                "registered candidate input ledger",
                "all 35,200 power-flow attempt arrays and solver logs",
                "retained 120-candidate fixed-control replay arrays",
            ],
        },
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(register, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(register, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
