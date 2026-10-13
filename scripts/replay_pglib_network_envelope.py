#!/usr/bin/env python3
"""Replay a deterministic AC operating envelope over all pinned PGLib families."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandapower as pp

from gridinstruct_utils import ROOT, ensure_dirs, write_json
from pglib_network_loader import FORMAL_SYSTEMS, load_pglib_network


DEFAULT_LEVELS = (0.90, 1.00, 1.05)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def run_case(system: str, level: float, root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    net = load_pglib_network(system, root)
    if len(net.load):
        net.load["p_mw"] *= level
        net.load["q_mvar"] *= level
    status = "solver_error"
    error_type = None
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            pp.runpp(
                net,
                algorithm="nr",
                init="auto",
                tolerance_mva=1e-6,
                max_iteration=50,
                enforce_q_lims=True,
                calculate_voltage_angles=True,
                numba=False,
            )
            status = "converged" if bool(getattr(net, "converged", False)) else "not_converged"
        except Exception as exc:  # pragma: no cover - solver-dependent branch
            error_type = type(exc).__name__
    result: dict[str, Any] = {
        "case_id": f"{system}_envelope_load{level:.2f}".replace(".", "p"),
        "system": system,
        "load_level": level,
        "status": status,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "source_case": (net.get("pglib_provenance") or {}).get("case_file"),
        "source_case_sha256": (net.get("pglib_provenance") or {}).get("case_sha256"),
    }
    if error_type:
        result["error_type"] = error_type
    if status == "converged":
        line_loading = list(net.res_line.loading_percent) if len(net.res_line) else []
        trafo_loading = list(net.res_trafo.loading_percent) if len(net.res_trafo) else []
        result.update(
            {
                "min_voltage_pu": finite(net.res_bus.vm_pu.min()),
                "max_voltage_pu": finite(net.res_bus.vm_pu.max()),
                "max_loading_percent": finite(max(line_loading + trafo_loading) if line_loading or trafo_loading else 0.0),
                "bus_count": len(net.bus),
                "branch_count": len(net.line) + len(net.trafo),
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pglib-root", type=Path, required=True)
    parser.add_argument("--systems", nargs="+", default=list(FORMAL_SYSTEMS))
    parser.add_argument("--load-levels", nargs="+", type=float, default=list(DEFAULT_LEVELS))
    parser.add_argument("--output", default="reports/pglib_network_envelope_replay_v1.2_sd_core.json")
    args = parser.parse_args()
    started = time.perf_counter()
    cases = [run_case(system, level, args.pglib_root) for system in args.systems for level in args.load_levels]
    converged = [case for case in cases if case["status"] == "converged"]
    by_system = {
        system: {
            "attempted": sum(case["system"] == system for case in cases),
            "converged": sum(case["system"] == system and case["status"] == "converged" for case in cases),
        }
        for system in args.systems
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass_full" if len(converged) == len(cases) else "pass_partial_coverage" if converged else "fail",
        "contract": "pinned_pglib_ac_operating_envelope_v1",
        "systems": list(args.systems),
        "load_levels": list(args.load_levels),
        "attempted_cases": len(cases),
        "converged_cases": len(converged),
        "elapsed_seconds_total": round(time.perf_counter() - started, 6),
        "elapsed_seconds_max_case": round(max((case["elapsed_seconds"] for case in cases), default=0.0), 6),
        "by_system": by_system,
        "severity_counts": dict(Counter("converged" if case["status"] == "converged" else "solver_nonconvergence" for case in cases)),
        "case_results": cases,
        "scope": {
            "full_population_replay": False,
            "n1_n2_outage_replay": False,
            "objective_or_opf_claimed": False,
            "purpose": "independent parser and AC-state envelope diagnostic across the eleven pinned network families",
        },
        "hard_gates": {
            "nonempty_converged_subset": bool(converged),
            "all_attempts_converged": len(converged) == len(cases),
            "all_networks_attempted": set(args.systems) == set(FORMAL_SYSTEMS) if set(args.systems) == set(FORMAL_SYSTEMS) else False,
        },
        "input": {"pglib_root": str(args.pglib_root)},
        "interpretation": "Independent AC power-flow envelope on the pinned PGLib source cases; the case list is a diagnostic grid and is not the released scenario-generation ledger.",
    }
    output = ROOT / args.output
    ensure_dirs(output.parent)
    write_json(output, report)
    print(json.dumps({"status": report["status"], "attempted": len(cases), "converged": len(converged)}, ensure_ascii=False))
    if report["status"] == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
