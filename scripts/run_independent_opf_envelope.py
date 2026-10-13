#!/usr/bin/env python3
"""Run a small, independent AC-OPF envelope on pinned PGLib cases.

The released 160-case replay is fixed-control evidence.  This script adds an
independent OPF calculation on three representative PGLib cases and three
deterministic load multipliers.  It reports solver status, objective values,
power-balance residuals, voltage margins, and thermal margins.  It does not
claim global optimality or replace the raw release scenario ledger.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandapower as pp

from gridinstruct_utils import ROOT, ensure_dirs, write_json
from pglib_network_loader import load_pglib_network


DEFAULT_SYSTEMS = ("ieee14", "ieee57", "ieee118")
DEFAULT_LOAD_LEVELS = (0.95, 1.0, 1.05)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def load_scale(net: Any, factor: float) -> None:
    if len(net.load):
        net.load["p_mw"] *= factor
        net.load["q_mvar"] *= factor


def max_loading(net: Any) -> float | None:
    values: list[float] = []
    if len(net.res_line):
        values.extend(float(value) for value in net.res_line.loading_percent)
    if len(net.res_trafo):
        values.extend(float(value) for value in net.res_trafo.loading_percent)
    return max(values) if values else 0.0


def balance_residual(net: Any) -> float | None:
    generation = 0.0
    demand = 0.0
    losses = 0.0
    for table_name in ("res_ext_grid", "res_gen", "res_sgen"):
        table = getattr(net, table_name, None)
        if table is not None and len(table) and "p_mw" in table:
            generation += float(table.p_mw.sum())
    for table_name in ("res_load", "res_sgen", "res_storage"):
        table = getattr(net, table_name, None)
        if table is not None and len(table) and "p_mw" in table:
            demand += float(table.p_mw.sum())
    for table_name in ("res_line", "res_trafo", "res_trafo3w", "res_impedance"):
        table = getattr(net, table_name, None)
        if table is not None and len(table) and "pl_mw" in table:
            losses += float(table.pl_mw.sum())
    return abs(generation - demand - losses)


def run_case(system: str, load_level: float, pglib_root: Path) -> dict[str, Any]:
    case_id = f"{system}_independent_opf_load{load_level:.2f}".replace(".", "p")
    started = time.perf_counter()
    net = load_pglib_network(system, pglib_root)
    load_scale(net, load_level)
    captured = io.StringIO()
    status = "solver_error"
    error_type: str | None = None
    error_message: str | None = None
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            pp.runopp(
                net,
                calculate_voltage_angles=True,
                verbose=False,
                check_connectivity=True,
                suppress_warnings=True,
                init="flat",
                delta=1e-8,
                tolerance_mva=1e-6,
                numba=False,
                max_iteration=100,
            )
            status = "converged" if bool(getattr(net, "OPF_converged", False)) else "not_converged"
        except Exception as exc:  # pragma: no cover - solver-dependent branch
            error_type = type(exc).__name__
            error_message = str(exc)[:500]

    result: dict[str, Any] = {
        "case_id": case_id,
        "system": system,
        "load_level": load_level,
        "solver": "pandapower.runopp",
        "status": status,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "source_case": (net.get("pglib_provenance") or {}).get("case_file"),
        "source_case_sha256": (net.get("pglib_provenance") or {}).get("case_sha256"),
    }
    if error_type:
        result.update({"error_type": error_type, "error_message": error_message})
    if status == "converged":
        result.update(
            {
                "objective_value": finite(getattr(net, "res_cost", None)),
                "min_voltage_pu": finite(net.res_bus.vm_pu.min()),
                "max_voltage_pu": finite(net.res_bus.vm_pu.max()),
                "max_loading_percent": finite(max_loading(net)),
                "power_balance_residual_mw": finite(balance_residual(net)),
                "voltage_interval_pu": [0.95, 1.05],
                "thermal_limit_percent": 100.0,
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pglib-root", type=Path, required=True)
    parser.add_argument("--systems", nargs="+", default=list(DEFAULT_SYSTEMS))
    parser.add_argument("--load-levels", nargs="+", type=float, default=list(DEFAULT_LOAD_LEVELS))
    parser.add_argument("--output", default="reports/independent_opf_envelope_v1.2_sd_core.json")
    args = parser.parse_args()
    if not args.systems or not args.load_levels:
        raise SystemExit("systems and load-levels must be non-empty")
    started = time.perf_counter()
    cases = [run_case(system, level, args.pglib_root) for system in args.systems for level in args.load_levels]
    converged = [case for case in cases if case["status"] == "converged"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if len(converged) == len(cases) else "warn_partial_convergence",
        "contract": "independent_ac_opf_envelope_v1",
        "systems": list(args.systems),
        "load_levels": list(args.load_levels),
        "attempted_cases": len(cases),
        "converged_cases": len(converged),
        "elapsed_seconds_total": round(time.perf_counter() - started, 6),
        "elapsed_seconds_max_case": round(max((case["elapsed_seconds"] for case in cases), default=0.0), 6),
        "case_results": cases,
        "solver_scope": {
            "objective": "native pandapower/PGLib generation-cost objective",
            "independence": "independent OPF calculation; separate from released fixed-control replay",
            "global_optimality_claimed": False,
            "full_population_replay_claimed": False,
        },
        "input": {"pglib_root": str(args.pglib_root), "systems": list(args.systems), "load_levels": list(args.load_levels)},
        "interpretation": "Independent OPF feasibility and objective diagnostics on selected pinned PGLib cases; this report does not validate every released record or certify global AC-OPF optimality.",
    }
    output = ROOT / args.output
    ensure_dirs(output.parent)
    write_json(output, report)
    print(json.dumps({"status": report["status"], "attempted": len(cases), "converged": len(converged)}, ensure_ascii=False))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
