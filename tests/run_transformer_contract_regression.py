"""Compare pandapower's legacy case path with the normalized transformer contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandapower as pp


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_grid_scenarios as scenarios  # noqa: E402


TOLERANCE = 1e-10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def max_abs(left, right) -> float:
    values = np.abs(np.asarray(left, dtype=float) - np.asarray(right, dtype=float))
    return float(np.nanmax(values)) if values.size else 0.0


def run() -> dict:
    systems = {}
    for system, loader in scenarios.SYSTEM_LOADERS.items():
        legacy = loader()
        normalized = scenarios.clone_net(system)
        options = {
            "algorithm": "nr",
            "init": "auto",
            "calculate_voltage_angles": True,
            "max_iteration": 100,
        }
        pp.runpp(legacy, **options)
        pp.runpp(normalized, **options)
        differences = {
            "bus_vm_pu": max_abs(legacy.res_bus.vm_pu, normalized.res_bus.vm_pu),
            "bus_va_degree": max_abs(
                legacy.res_bus.va_degree, normalized.res_bus.va_degree
            ),
            "line_p_from_mw": max_abs(
                legacy.res_line.p_from_mw, normalized.res_line.p_from_mw
            ),
            "line_q_from_mvar": max_abs(
                legacy.res_line.q_from_mvar, normalized.res_line.q_from_mvar
            ),
            "trafo_p_hv_mw": max_abs(
                legacy.res_trafo.p_hv_mw, normalized.res_trafo.p_hv_mw
            ),
            "trafo_q_hv_mvar": max_abs(
                legacy.res_trafo.q_hv_mvar, normalized.res_trafo.q_hv_mvar
            ),
        }
        systems[system] = {
            "legacy_characteristic_rows": len(legacy.get("characteristic", [])),
            "transformer_rows": len(normalized.trafo),
            "normalized_flag_present": (
                not len(normalized.trafo)
                or "tap_dependency_table" in normalized.trafo.columns
            ),
            "max_absolute_differences": differences,
            "max_absolute_difference": max(differences.values()),
            "pass": max(differences.values()) <= TOLERANCE,
        }
    generator = ROOT / "scripts" / "generate_grid_scenarios.py"
    return {
        "status": "pass" if all(row["pass"] for row in systems.values()) else "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "pandapower bundled IEEE case constructors",
        "systems": systems,
        "tolerance": TOLERANCE,
        "environment": {
            "python": platform.python_version(),
            "pandapower": pp.__version__,
            "numpy": np.__version__,
        },
        "generator_sha256": sha256(generator),
        "regression_script_sha256": sha256(Path(__file__)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run()
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
