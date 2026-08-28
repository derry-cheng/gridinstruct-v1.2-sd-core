#!/usr/bin/env python3
"""Bind query-truth completeness flags to the reconstructed scenario ledger."""

from __future__ import annotations

import json
from collections import Counter

from gridinstruct_utils import ROOT
from recompute_scenario_truth import validate_complete


def main() -> None:
    path = ROOT / "simulation_outputs/contingency/scenarios_converged.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    complete = 0
    incomplete: list[dict[str, str]] = []
    for row in rows:
        if row.get("solver_status") != "converged":
            row["query_truth_complete"] = False
            incomplete.append({"scenario_id": row["scenario_id"], "reason": "solver_not_converged"})
            continue
        try:
            validate_complete(row)
        except Exception as exc:  # noqa: BLE001 - recorded in the truth gate
            row["query_truth_complete"] = False
            incomplete.append({"scenario_id": row["scenario_id"], "reason": str(exc)[:240]})
            continue
        row["query_truth_complete"] = True
        row["scenario_truth_schema_version"] = "2.0"
        complete += 1
    path.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "status": "pass" if not incomplete else "warn",
        "records": len(rows),
        "query_truth_complete": complete,
        "query_truth_incomplete": len(incomplete),
        "incomplete_examples": incomplete[:30],
        "solver_status_counts": dict(Counter(str(row.get("solver_status")) for row in rows)),
        "scenario_path": str(path.relative_to(ROOT)),
    }
    report_path = ROOT / "reports/query_truth_completeness_gate_v1.2_sd_core.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
