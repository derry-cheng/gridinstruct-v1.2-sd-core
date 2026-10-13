#!/usr/bin/env python3
"""Recompute the four-network Core-4 N-1 denominator from pinned PGLib cases.

This entry point deliberately emits only the registered Core-4 line-outage
scope.  It does not pretend to recreate the broader historical scenario
population.  Every emitted row is produced by the shared pandapower runner,
and the report records the exact load strata and source checkout used.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from gridinstruct_utils import ROOT, ensure_dirs, write_json

sys.path.insert(0, str(ROOT / "scripts"))
from generate_grid_scenarios import (  # noqa: E402
    clone_net,
    in_service_line_indices,
    run_scenario,
)


CORE_SYSTEM_LOAD_LEVELS = {
    "ieee14": (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3),
    "ieee30": (0.7, 0.75, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3),
    "ieee57": (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3),
    "ieee118": (0.7, 0.8, 0.9, 1.0, 1.1, 1.2),
}


def build_core_inputs(pglib_root: Path) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    by_system: dict[str, dict[str, int]] = {}
    total = sum(
        len(in_service_line_indices(clone_net(system, pglib_root), 9999))
        * len(levels)
        for system, levels in CORE_SYSTEM_LOAD_LEVELS.items()
    )
    completed = 0
    started = time.perf_counter()
    for system, levels in CORE_SYSTEM_LOAD_LEVELS.items():
        base = clone_net(system, pglib_root)
        line_indices = in_service_line_indices(base, 9999)
        system_rows = 0
        system_converged = 0
        for load_level in levels:
            for branch_idx in line_indices:
                row = run_scenario(
                    system,
                    load_level,
                    "n1_branch_outage",
                    branch_idx,
                    1.0,
                    pglib_root=pglib_root,
                    scale_generation_with_load=True,
                )
                rows.append(row)
                system_rows += 1
                system_converged += int(row["solver_status"] == "converged")
                completed += 1
                if completed % 100 == 0 or completed == total:
                    elapsed = time.perf_counter() - started
                    rate = completed / max(elapsed, 1e-9)
                    print(
                        json.dumps(
                            {
                                "completed": completed,
                                "total": total,
                                "progress_percent": round(100.0 * completed / total, 2),
                                "elapsed_seconds": round(elapsed, 3),
                                "rows_per_second": round(rate, 3),
                            }
                        ),
                        flush=True,
                    )
        by_system[system] = {
            "attempt_count": system_rows,
            "converged_count": system_converged,
            "line_count": len(line_indices),
            "load_strata": len(levels),
        }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "scope": "core4_n1_only",
        "systems": list(CORE_SYSTEM_LOAD_LEVELS),
        "system_load_levels": {
            system: list(levels) for system, levels in CORE_SYSTEM_LOAD_LEVELS.items()
        },
        "total_attempt_count": len(rows),
        "converged_count": sum(row["solver_status"] == "converged" for row in rows),
        "failed_count": sum(row["solver_status"] != "converged" for row in rows),
        "by_system": by_system,
        "source": {
            "kind": "pinned_pglib_matpower_cases_with_pandapower_runner",
            "pglib_root": str(pglib_root),
        },
        "interpretation": (
            "This report is a fresh Core-4 N-1 denominator recomputation. "
            "It does not establish the absent broader scenario ledger."
        ),
    }
    return rows, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pglib-root", type=Path, required=True)
    parser.add_argument(
        "--all-output",
        default="simulation_outputs/power_flow/scenarios_all.json",
    )
    parser.add_argument(
        "--converged-core-output",
        default="simulation_outputs/power_flow/scenarios_converged_core.json",
    )
    parser.add_argument(
        "--complete-truth-output",
        default="simulation_outputs/contingency/scenarios_converged.json",
    )
    parser.add_argument(
        "--simulation-report",
        default="reports/simulation_report.json",
    )
    args = parser.parse_args()
    pglib_root = args.pglib_root.resolve()
    if not pglib_root.is_dir():
        raise SystemExit(f"missing PGLib checkout: {pglib_root}")
    rows, report = build_core_inputs(pglib_root)
    converged = [row for row in rows if row["solver_status"] == "converged"]
    report["config"] = {
        "system_load_levels": report["system_load_levels"],
        "contingency_type": "n1_branch_outage",
        "scale_generation_with_load": True,
    }
    report["convergence_rate"] = len(converged) / max(len(rows), 1)
    all_path = ROOT / args.all_output
    core_path = ROOT / args.converged_core_output
    truth_path = ROOT / args.complete_truth_output
    simulation_report_path = ROOT / args.simulation_report
    ensure_dirs(all_path.parent, core_path.parent, truth_path.parent)
    write_json(all_path, rows)
    write_json(core_path, converged)
    write_json(truth_path, converged)
    write_json(simulation_report_path, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "attempts": len(rows),
                "converged": len(converged),
                "failed": len(rows) - len(converged),
                "outputs": [
                    args.all_output,
                    args.converged_core_output,
                    args.complete_truth_output,
                    args.simulation_report,
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
