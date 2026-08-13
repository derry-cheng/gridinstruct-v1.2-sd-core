#!/usr/bin/env python3
"""Materialize the selected IEEE14 candidate payload from its bound IDs.

The historical screening report contains the registered 120 selected IDs but
the compact snapshot omitted the JSON payload.  This script recomputes the
selected pre-action AC states from the pinned PGLib case.  It does not claim to
rerun the absent 35,200-candidate screening ledger.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from gridinstruct_utils import ROOT, write_json

sys.path.insert(0, str(ROOT / "scripts"))
from generate_grid_scenarios import run_scenario  # noqa: E402


PATTERN = re.compile(
    r"^ieee14_secure_candidate_n2_load_milli(?P<load>\d{4})"
    r"_gen_centi(?P<gen>\d{3})_lines_(?P<first>\d+)_(?P<second>\d+)$"
)


def materialize(ids: list[str], pglib_root: Path) -> list[dict]:
    rows = []
    started = time.perf_counter()
    for ordinal, scenario_id in enumerate(ids, start=1):
        match = PATTERN.fullmatch(scenario_id)
        if match is None:
            raise ValueError(f"unsupported candidate ID: {scenario_id}")
        row = run_scenario(
            "ieee14",
            int(match.group("load")) / 1000.0,
            "n2_branch_outage",
            int(match.group("first")),
            int(match.group("gen")) / 100.0,
            second_branch_idx=int(match.group("second")),
            pglib_root=pglib_root,
            scale_generation_with_load=True,
        )
        row["scenario_id"] = scenario_id
        rows.append(row)
        if ordinal % 10 == 0 or ordinal == len(ids):
            elapsed = time.perf_counter() - started
            print(
                json.dumps(
                    {
                        "completed": ordinal,
                        "total": len(ids),
                        "progress_percent": round(100.0 * ordinal / len(ids), 2),
                        "elapsed_seconds": round(elapsed, 3),
                    }
                ),
                flush=True,
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pglib-root", type=Path, required=True)
    parser.add_argument(
        "--report",
        default="reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--output",
        default="simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
    )
    args = parser.parse_args()
    report_path = ROOT / args.report
    report = json.loads(report_path.read_text(encoding="utf-8"))
    ids = [str(value) for value in report.get("output_scenario_ids") or []]
    if len(ids) != 120 or len(set(ids)) != 120:
        raise SystemExit(f"bound report does not contain 120 unique candidate IDs: {len(ids)}")
    root = args.pglib_root.resolve()
    rows = materialize(ids, root)
    output = ROOT / args.output
    write_json(output, rows)
    print(json.dumps({"status": "pass", "rows": len(rows), "output": str(output.relative_to(ROOT))}, indent=2))


if __name__ == "__main__":
    main()
