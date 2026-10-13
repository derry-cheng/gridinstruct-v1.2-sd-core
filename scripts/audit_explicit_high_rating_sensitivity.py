#!/usr/bin/env python3
"""Gate labels affected by source-explicit high PGLib RATE_A values."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_external_matched_strata import (
    normalize_network,
    recompute_source_bound_loading,
    source_bound_rating_maps,
)
from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json
from power_evidence_common import (
    PGLIB_EXPECTED_COMMIT,
    load_network,
    load_rating_overrides,
    sha256_file,
    validate_pglib_repository,
)


EXPLICIT_HIGH_LIMIT_MVA = 9900.0


def conservative_quantile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("same-voltage finite-rating pool is empty")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    position = quantile * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def voltage_class(net, element: str, index: int) -> float:
    row = getattr(net, element).loc[index]
    fields = ("from_bus", "to_bus") if element == "line" else ("hv_bus", "lv_bus")
    return max(float(net.bus.at[int(row[field]), "vn_kv"]) for field in fields)


def substitution_maps(
    rating_maps: dict[str, dict[tuple[str, int], float]],
    quantiles: list[float],
) -> tuple[dict[float, dict[str, dict[tuple[str, int], float]]], list[dict[str, Any]]]:
    variants = {
        quantile: {network: dict(values) for network, values in rating_maps.items()}
        for quantile in quantiles
    }
    components = []
    for network, ratings in rating_maps.items():
        net = load_network(network)
        pools: dict[float, list[float]] = defaultdict(list)
        for (element, index), value in ratings.items():
            if not math.isclose(value, EXPLICIT_HIGH_LIMIT_MVA, abs_tol=1e-6):
                pools[voltage_class(net, element, index)].append(value)
        for (element, index), value in ratings.items():
            if not math.isclose(value, EXPLICIT_HIGH_LIMIT_MVA, abs_tol=1e-6):
                continue
            level = voltage_class(net, element, index)
            replacements = {}
            for quantile in quantiles:
                replacement = conservative_quantile(pools[level], quantile)
                variants[quantile][network][(element, index)] = replacement
                replacements[str(quantile)] = replacement
            components.append(
                {
                    "network": network,
                    "element": element,
                    "index": index,
                    "voltage_class_kv": level,
                    "source_rating_mva": value,
                    "finite_same_voltage_pool_count": len(pools[level]),
                    "replacement_rating_mva": replacements,
                }
            )
    return variants, components


def active_keys(row: dict[str, Any]) -> set[tuple[str, int]]:
    result = set()
    for element, rows_key, index_key in (
        ("line", "line_results", "line"),
        ("trafo", "transformer_results", "transformer"),
    ):
        for state in row.get(rows_key) or []:
            if bool(state.get("in_service")):
                result.add((element, int(state[index_key])))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", nargs="+", required=True)
    parser.add_argument("--pglib-root", type=Path, required=True)
    parser.add_argument("--pglib-commit", default=PGLIB_EXPECTED_COMMIT)
    parser.add_argument("--rating-overrides", type=Path)
    parser.add_argument("--quantiles", nargs="+", type=float, default=[0.10, 0.25, 0.50])
    parser.add_argument(
        "--output-json",
        default="reports/explicit_high_rating_sensitivity_v1.2_sd_core.json",
    )
    args = parser.parse_args()
    repository = validate_pglib_repository(args.pglib_root, args.pglib_commit)
    rating_maps, coverage = source_bound_rating_maps(
        args.pglib_root,
        repository,
        load_rating_overrides(args.rating_overrides),
    )
    variants, components = substitution_maps(rating_maps, args.quantiles)
    expected = {
        (item["network"], item["element"], item["index"]) for item in components
    }
    scenario_paths = [ROOT / value for value in args.scenarios]
    comparisons, covered = [], set()
    for row in (item for path in scenario_paths for item in read_json(path)):
        network = normalize_network(row.get("network_model"))
        if row.get("solver_status") != "converged" or network not in rating_maps:
            continue
        relevant = active_keys(row) & {
            (element, index)
            for case_network, element, index in expected
            if case_network == network
        }
        if not relevant:
            continue
        covered.update((network, element, index) for element, index in relevant)
        baseline = recompute_source_bound_loading(
            row, rating_maps[network], repository["commit"]
        )
        baseline_label = float(baseline["max_branch_loading_percent"]) > 100.0
        rows = []
        for quantile in args.quantiles:
            changed = recompute_source_bound_loading(
                row, variants[quantile][network], repository["commit"]
            )
            label = float(changed["max_branch_loading_percent"]) > 100.0
            rows.append(
                {
                    "quantile": quantile,
                    "max_branch_loading_percent": changed["max_branch_loading_percent"],
                    "thermal_violation": label,
                    "label_stable": label == baseline_label,
                }
            )
        comparisons.append(
            {
                "scenario_id": row["scenario_id"],
                "network": network,
                "baseline_thermal_violation": baseline_label,
                "substitutions": rows,
            }
        )
    flips = sum(
        not variant["label_stable"]
        for row in comparisons
        for variant in row["substitutions"]
    )
    gates = {
        "all_explicit_high_components_covered": covered == expected,
        "no_label_flip_under_same_voltage_quantiles": flips == 0,
        "all_branch_ratings_source_bound": all(
            not row["missing_ratings"] for row in coverage.values()
        ),
    }
    report = {
        "contract_version": "gridinstruct-explicit-high-rating-sensitivity-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(gates.values()) else "fail",
        "pglib_repository": repository,
        "scenario_sources": [
            {"path": str(path), "sha256": sha256_file(path)} for path in scenario_paths
        ],
        "quantiles": args.quantiles,
        "explicit_high_components": components,
        "expected_component_count": len(expected),
        "covered_component_count": len(covered),
        "comparison_count": len(comparisons),
        "thermal_label_flip_count": flips,
        "hard_gates": gates,
        "comparisons": comparisons,
    }
    ensure_dirs((ROOT / args.output_json).parent)
    write_json(ROOT / args.output_json, report)
    print(json.dumps({key: report[key] for key in ("status", "expected_component_count", "comparison_count", "thermal_label_flip_count")}, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
