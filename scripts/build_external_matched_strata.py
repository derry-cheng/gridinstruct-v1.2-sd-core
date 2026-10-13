#!/usr/bin/env python3
"""Audit network/severity confounding and build a matched-strata selection."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import copy
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json
from power_evidence_common import (
    ENVELOPE_CLASSES,
    EXTERNAL_NETWORKS,
    PGLIB_EXPECTED_COMMIT,
    envelope_class,
    load_rating_overrides,
    sha256_file,
    source_bound_loading_from_state_rows,
    source_bound_rating_map_from_loaded_network,
    validate_pglib_repository,
)
from pglib_network_loader import load_pglib_network


LOAD_LEVEL_CANDIDATES = {
    "normal-envelope": (0.70, 0.76, 0.82, 0.88),
    "operational-stress": (0.88, 0.94, 1.00, 1.06),
    "emergency-stress": (1.00, 1.08, 1.16, 1.24),
    "extreme-stress": (1.12, 1.20, 1.28, 1.36),
}


def normalize_network(value: Any) -> str:
    return str(value or "").strip().replace(" ", "").lower()


def cramers_v(matrix: list[list[int]]) -> float:
    row_totals = [sum(row) for row in matrix]
    column_totals = [sum(matrix[row][column] for row in range(len(matrix))) for column in range(len(matrix[0]))]
    total = sum(row_totals)
    if total == 0:
        return 0.0
    chi2 = 0.0
    for row_index, row_total in enumerate(row_totals):
        for column_index, column_total in enumerate(column_totals):
            expected = row_total * column_total / total
            if expected > 0:
                chi2 += (matrix[row_index][column_index] - expected) ** 2 / expected
    denominator = total * min(max(len(matrix) - 1, 1), max(len(matrix[0]) - 1, 1))
    return math.sqrt(chi2 / denominator) if denominator else 0.0


def mutual_information_bits(matrix: list[list[int]]) -> float:
    row_totals = [sum(row) for row in matrix]
    column_totals = [sum(matrix[row][column] for row in range(len(matrix))) for column in range(len(matrix[0]))]
    total = sum(row_totals)
    if total == 0:
        return 0.0
    result = 0.0
    for row_index, row_total in enumerate(row_totals):
        for column_index, column_total in enumerate(column_totals):
            count = matrix[row_index][column_index]
            if count:
                result += (count / total) * math.log2(count * total / (row_total * column_total))
    return result


def stable_rank(row: dict[str, Any], seed: int) -> tuple[str, str]:
    scenario_id = str(row.get("scenario_id") or "")
    return (
        hashlib.sha256(f"{seed}:{scenario_id}".encode("utf-8")).hexdigest(),
        scenario_id,
    )


def maximal_common_support_block(
    grouped: dict[tuple[str, str], list[dict[str, Any]]],
    target_per_cell: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Find the largest fully observed network × severity block.

    The target count is enforced in every retained cell. Structural zeros
    outside the block remain visible in the report and are never imputed.
    """

    best_networks: tuple[str, ...] = ()
    best_severities: tuple[str, ...] = ()
    best_score = (0, 0, 0, ())
    for network_count in range(2, len(EXTERNAL_NETWORKS) + 1):
        for networks in combinations(EXTERNAL_NETWORKS, network_count):
            severities = tuple(
                severity
                for severity in ENVELOPE_CLASSES
                if all(
                    len(grouped[(network, severity)]) >= target_per_cell
                    for network in networks
                )
            )
            if len(severities) < 2:
                continue
            score = (
                len(networks) * len(severities),
                len(networks),
                len(severities),
                tuple(networks) + tuple(severities),
            )
            if score > best_score:
                best_score = score
                best_networks = tuple(networks)
                best_severities = severities
    return best_networks, best_severities


def source_bound_rating_maps(
    pglib_root: Path,
    pglib_repository: dict[str, Any],
    manual_overrides: list[dict[str, Any]],
) -> tuple[dict[str, dict[tuple[str, int], float]], dict[str, Any]]:
    maps = {}
    coverage = {}
    for network in EXTERNAL_NETWORKS:
        net = load_pglib_network(
            network,
            pglib_root,
            expected_commit=str(pglib_repository["commit"]),
        )
        rating_map = source_bound_rating_map_from_loaded_network(net)
        rating_map.update(
            {
                (str(row["element"]), int(row["index"])): float(row["rating_value"])
                for row
                in manual_overrides
                if str(row.get("network", "")).lower() == network
                and row.get("element") in {"line", "trafo"}
            }
        )
        expected = {
            (element, int(index))
            for element in ("line", "trafo")
            for index in getattr(net, element).index
            if bool(getattr(net, element).at[index, "in_service"])
        }
        maps[network] = rating_map
        coverage[network] = {
            "expected_in_service_branches": len(expected),
            "source_bound_ratings": len(expected & set(rating_map)),
            "missing_ratings": [
                {"element": element, "index": index}
                for element, index in sorted(expected - set(rating_map))
            ],
        }
    return maps, coverage


def recompute_source_bound_loading(
    row: dict[str, Any],
    rating_map: dict[tuple[str, int], float],
    repository_commit: str,
) -> dict[str, Any]:
    result = copy.deepcopy(row)
    line_results = result.get("line_results")
    transformer_results = result.get("transformer_results")
    if not isinstance(line_results, list) or not isinstance(transformer_results, list):
        result["thermal_rating_status"] = "state_rows_missing"
        return result
    loading = source_bound_loading_from_state_rows(
        line_results,
        transformer_results,
        rating_map,
    )
    result.update(loading)
    result["thermal_rating_contract"] = {
        "provider": "IEEE PES PGLib-OPF plus explicit source overrides",
        "repository_commit": repository_commit,
        "loading_definition": "maximum terminal apparent power divided by RATE_A",
    }
    return result


def build_report(
    rows: list[dict[str, Any]],
    *,
    target_per_cell: int,
    seed: int,
    source_path: Path,
    rating_maps: dict[str, dict[tuple[str, int], float]] | None = None,
    repository_commit: str | None = None,
    rating_coverage: dict[str, Any] | None = None,
    pglib_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    unique_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        scenario_id = str(row.get("scenario_id") or "")
        if not scenario_id:
            raise ValueError("every matched-strata input row needs a scenario_id")
        if scenario_id in unique_rows and unique_rows[scenario_id] != row:
            raise ValueError(f"conflicting duplicate scenario_id: {scenario_id}")
        unique_rows[scenario_id] = row
    rows = list(unique_rows.values())
    if rating_maps is not None:
        rows = [
            recompute_source_bound_loading(
                row,
                rating_maps.get(normalize_network(row.get("network_model")), {}),
                str(repository_commit),
            )
            if normalize_network(row.get("network_model")) in EXTERNAL_NETWORKS
            and row.get("solver_status") == "converged"
            else row
            for row in rows
        ]
    converged = [
        row
        for row in rows
        if row.get("solver_status") == "converged"
        and normalize_network(row.get("network_model")) in EXTERNAL_NETWORKS
        and (
            rating_maps is None
            or row.get("thermal_rating_status") == "source_bound"
        )
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in converged:
        grouped[(normalize_network(row.get("network_model")), envelope_class(row))].append(row)
    raw_matrix = [
        [len(grouped[(network, severity)]) for severity in ENVELOPE_CLASSES]
        for network in EXTERNAL_NETWORKS
    ]
    support_networks, support_severities = maximal_common_support_block(
        grouped,
        target_per_cell,
    )
    selected = []
    cells = []
    for network in EXTERNAL_NETWORKS:
        for severity in ENVELOPE_CLASSES:
            candidates = sorted(grouped[(network, severity)], key=lambda row: stable_rank(row, seed))
            in_common_support = (
                network in support_networks and severity in support_severities
            )
            chosen = candidates[:target_per_cell] if in_common_support else []
            selected.extend(chosen)
            shortage = max(0, target_per_cell - len(chosen))
            cells.append(
                {
                    "network": network,
                    "severity": severity,
                    "available": len(candidates),
                    "selected": len(chosen),
                    "target": target_per_cell,
                    "full_factorial_shortage": max(
                        0,
                        target_per_cell - len(candidates),
                    ),
                    "in_common_support_block": in_common_support,
                    "common_support_shortage": shortage if in_common_support else None,
                    "candidate_load_levels": (
                        list(LOAD_LEVEL_CANDIDATES[severity])
                        if len(candidates) < target_per_cell
                        else []
                    ),
                }
            )
    matched_matrix = [
        [
            sum(
                normalize_network(row.get("network_model")) == network
                and envelope_class(row) == severity
                for row in selected
            )
            for severity in ENVELOPE_CLASSES
        ]
        for network in EXTERNAL_NETWORKS
    ]
    commands = []
    for network in EXTERNAL_NETWORKS:
        missing_severities = [
            cell["severity"]
            for cell in cells
            if cell["network"] == network
            and cell["full_factorial_shortage"] > 0
        ]
        if not missing_severities:
            continue
        root_argument = (
            f"--pglib-root {pglib_root} " if pglib_root is not None else ""
        )
        if "normal-envelope" in missing_severities:
            normal_loads = " ".join(
                f"{value:.2f}" for value in LOAD_LEVEL_CANDIDATES["normal-envelope"]
            )
            commands.append(
                {
                    "network": network,
                    "target_severities": ["normal-envelope"],
                    "construction_family": "normal_ac_opf",
                    "command": (
                        "python scripts/generate_extended_topology_stress.py "
                        f"--systems {network} {root_argument}"
                        f"--load-levels {normal_loads} "
                        "--dispatch-policy normal_ac_opf "
                        f"--max-lines-per-system {target_per_cell} "
                        "--max-attempts-per-stratum 128 "
                        f"--seed {seed} --scenario-namespace matched_normal_v2_{network} "
                        "--output-json "
                        f"simulation_outputs/topology_stress/matched_normal_pool_{network}.json "
                        "--attempts-json "
                        f"reports/matched_normal_attempts_{network}.json "
                        "--report-json "
                        f"reports/matched_normal_audit_{network}.json "
                        "--report-md "
                        f"reports/matched_normal_audit_{network}.md"
                    ),
                }
            )
        stress_severities = [
            severity
            for severity in missing_severities
            if severity != "normal-envelope"
        ]
        if stress_severities:
            load_levels = sorted(
                {
                    level
                    for severity in stress_severities
                    for level in LOAD_LEVEL_CANDIDATES[severity]
                }
            )
            load_text = " ".join(f"{value:.2f}" for value in load_levels)
            commands.append(
                {
                    "network": network,
                    "target_severities": stress_severities,
                    "construction_family": (
                        "load_generation_linked_voltage_setpoint_sweep"
                    ),
                    "command": (
                        "python scripts/generate_extended_topology_stress.py "
                        f"--systems {network} {root_argument}"
                        f"--load-levels {load_text} "
                        "--scale-generation-with-load "
                        "--generator-voltage-factors 0.98 1.00 1.02 "
                        f"--max-lines-per-system {target_per_cell} "
                        "--max-attempts-per-stratum 128 "
                        f"--seed {seed} --scenario-namespace matched_stress_v2_{network} "
                        "--output-json "
                        f"simulation_outputs/topology_stress/matched_stress_pool_{network}.json "
                        "--attempts-json "
                        f"reports/matched_stress_attempts_{network}.json "
                        "--report-json "
                        f"reports/matched_stress_audit_{network}.json "
                        "--report-md "
                        f"reports/matched_stress_audit_{network}.md"
                    ),
                }
            )
    full_factorial_shortage = sum(
        cell["full_factorial_shortage"] for cell in cells
    )
    common_support_shortage = sum(
        cell["common_support_shortage"] or 0
        for cell in cells
        if cell["in_common_support_block"]
    )
    indeterminate_rows = sum(
        row.get("solver_status") == "converged"
        and normalize_network(row.get("network_model")) in EXTERNAL_NETWORKS
        and row.get("thermal_rating_status") != "source_bound"
        for row in rows
    ) if rating_maps is not None else 0
    rating_gate_applicable = rating_maps is not None
    all_network_ratings_bound = (
        all(not item["missing_ratings"] for item in (rating_coverage or {}).values())
        if rating_gate_applicable
        else True
    )
    support_matrix = [
        [
            len(grouped[(network, severity)])
            for severity in support_severities
        ]
        for network in support_networks
    ]
    selected_support_matrix = [
        [
            sum(
                normalize_network(row.get("network_model")) == network
                and envelope_class(row) == severity
                for row in selected
            )
            for severity in support_severities
        ]
        for network in support_networks
    ]
    severity_conditioned_support = []
    severity_conditioned_selected = []
    for severity in ENVELOPE_CLASSES:
        supported_networks = [
            network
            for network in EXTERNAL_NETWORKS
            if len(grouped[(network, severity)]) >= target_per_cell
        ]
        per_network_counts = {}
        selected_ids = {}
        for network in supported_networks:
            chosen = sorted(
                grouped[(network, severity)],
                key=lambda row: stable_rank(row, seed),
            )[:target_per_cell]
            severity_conditioned_selected.extend(chosen)
            per_network_counts[network] = len(chosen)
            selected_ids[network] = [
                str(row["scenario_id"]) for row in chosen
            ]
        severity_conditioned_support.append(
            {
                "severity": severity,
                "maximum_supported_networks": supported_networks,
                "network_count": len(supported_networks),
                "target_per_network": target_per_cell,
                "selected_per_network": per_network_counts,
                "selected_scenario_ids": selected_ids,
                "exactly_balanced": (
                    len(supported_networks) >= 2
                    and all(
                        count == target_per_cell
                        for count in per_network_counts.values()
                    )
                ),
            }
        )
    severity_conditioned_network_union = sorted(
        {
            network
            for item in severity_conditioned_support
            for network in item["maximum_supported_networks"]
        }
    )
    network_reachability = []
    for network in EXTERNAL_NETWORKS:
        network_rows = [
            row
            for row in rows
            if normalize_network(row.get("network_model")) == network
        ]
        converged_network_rows = [
            row for row in converged
            if normalize_network(row.get("network_model")) == network
        ]
        per_severity = {}
        for severity in ENVELOPE_CLASSES:
            severity_rows = [
                row
                for row in converged_network_rows
                if envelope_class(row) == severity
            ]
            load_levels = sorted(
                {
                    float(row["load_level"])
                    for row in severity_rows
                    if row.get("load_level") is not None
                }
            )
            per_severity[severity] = {
                "observed_count": len(severity_rows),
                "observed_load_levels": load_levels,
            }
        network_reachability.append(
            {
                "network": network,
                "attempted_scenario_count": len(network_rows),
                "converged_scenario_count": len(converged_network_rows),
                "explored_load_levels": sorted(
                    {
                        float(row["load_level"])
                        for row in network_rows
                        if row.get("load_level") is not None
                    }
                ),
                "construction_families": sorted(
                    {
                        str(
                            row.get("dispatch_policy")
                            or "fixed_power_flow"
                        )
                        for row in network_rows
                    }
                ),
                "severity_support": per_severity,
                "interpretation": (
                    "observed support under the preregistered construction "
                    "families; zero cells are not mathematical impossibility claims"
                ),
            }
        )
    global_severity_observed = all(
        any(raw_matrix[row][column] > 0 for row in range(len(raw_matrix)))
        for column in range(len(ENVELOPE_CLASSES))
    )
    every_network_observed = all(sum(row) > 0 for row in raw_matrix)
    hard_gates = {
        "all_seven_external_networks_observed": every_network_observed,
        "all_four_severity_classes_observed_globally": global_severity_observed,
        "common_support_has_at_least_two_networks": len(support_networks) >= 2,
        "common_support_has_at_least_two_severities": len(support_severities) >= 2,
        "common_support_cells_reach_preregistered_target": (
            bool(support_networks)
            and common_support_shortage == 0
        ),
        "common_support_selection_exactly_balanced": (
            bool(selected_support_matrix)
            and all(
                value == target_per_cell
                for row in selected_support_matrix
                for value in row
            )
        ),
        "conditional_network_severity_cramers_v_zero": (
            bool(selected_support_matrix)
            and cramers_v(selected_support_matrix) <= 1e-12
        ),
        "every_severity_has_at_least_two_supported_networks": all(
            item["network_count"] >= 2
            for item in severity_conditioned_support
        ),
        "severity_conditioned_selections_exactly_balanced": all(
            item["exactly_balanced"]
            for item in severity_conditioned_support
        ),
        "all_seven_networks_covered_across_severity_conditioned_support": (
            severity_conditioned_network_union
            == sorted(EXTERNAL_NETWORKS)
        ),
        "all_external_branch_ratings_source_bound": all_network_ratings_bound,
        "all_converged_severities_use_source_bound_ratings": indeterminate_rows == 0,
    }
    report = {
        "contract_version": "gridinstruct-external-matched-strata-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "fail",
        "source": {"path": str(source_path), "sha256": sha256_file(source_path)},
        "seed": seed,
        "target_per_network_severity_cell": target_per_cell,
        "networks": list(EXTERNAL_NETWORKS),
        "severity_classes": list(ENVELOPE_CLASSES),
        "raw_converged_scenario_count": len(converged),
        "rating_indeterminate_converged_scenario_count": indeterminate_rows,
        "source_bound_rating_coverage": rating_coverage,
        "source_bound_rating_gate_applicable": rating_gate_applicable,
        "unique_input_scenario_count": len(rows),
        "raw_matrix": raw_matrix,
        "raw_association": {
            "cramers_v": cramers_v(raw_matrix),
            "mutual_information_bits": mutual_information_bits(raw_matrix),
        },
        "network_reachability": network_reachability,
        "full_factorial_diagnostic": {
            "status": "pass" if full_factorial_shortage == 0 else "incomplete",
            "filled_cell_count": sum(
                cell["available"] >= target_per_cell for cell in cells
            ),
            "total_cell_count": len(EXTERNAL_NETWORKS) * len(ENVELOPE_CLASSES),
            "shortage_to_target": full_factorial_shortage,
            "claim": (
                "diagnostic only; the report does not claim that all 28 cells "
                "are physically reachable or filled"
            ),
        },
        "common_support": {
            "networks": list(support_networks),
            "severities": list(support_severities),
            "available_matrix": support_matrix,
            "selected_matrix": selected_support_matrix,
            "target_per_cell": target_per_cell,
            "association": {
                "cramers_v": cramers_v(selected_support_matrix),
                "mutual_information_bits": mutual_information_bits(
                    selected_support_matrix
                ),
            },
            "interpretation": (
                "conditional network-severity association inside the fully "
                "observed support block; structural-zero cells are excluded, "
                "reported, and never imputed"
            ),
        },
        "severity_conditioned_maximum_network_support": {
            "strata": severity_conditioned_support,
            "selected_scenario_count": len(severity_conditioned_selected),
            "covered_networks": severity_conditioned_network_union,
            "interpretation": (
                "Within each fixed severity, every network with at least the "
                "preregistered target count is retained and downsampled to the "
                "same count. No absent network-severity cell is imputed."
            ),
        },
        "matched_scenario_count": len(selected),
        "matched_matrix": matched_matrix,
        "matched_association": {
            "cramers_v": cramers_v(matched_matrix),
            "mutual_information_bits": mutual_information_bits(matched_matrix),
        },
        "cells": cells,
        "total_shortage": full_factorial_shortage,
        "common_support_shortage": common_support_shortage,
        "resampling_commands": commands,
        "hard_gates": hard_gates,
        "claim_boundary": (
            "Seven-network external coverage and four global severity classes "
            "are preserved. Deconfounding is claimed only inside the reported "
            "common-support block, not across unobserved network-severity cells."
        ),
    }
    return report, selected


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# External Network × Severity Matched-Strata Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Status: `{report['status']}`",
        f"- Target per cell: {report['target_per_network_severity_cell']}",
        (
            "- Full-factorial diagnostic shortage: "
            f"{report['full_factorial_diagnostic']['shortage_to_target']}"
        ),
        (
            "- Common-support networks: "
            f"{', '.join(report['common_support']['networks'])}"
        ),
        (
            "- Common-support severities: "
            f"{', '.join(report['common_support']['severities'])}"
        ),
        f"- Raw Cramer's V: {report['raw_association']['cramers_v']:.6f}",
        (
            "- Conditional matched Cramer's V: "
            f"{report['common_support']['association']['cramers_v']:.6f}"
        ),
        "",
        (
            "The 7×4 table is a transparent support diagnostic. Unobserved "
            "cells are not imputed and are not claimed physically unreachable. "
            "Balance and conditional association are evaluated only in the "
            "fully observed common-support block."
        ),
        "",
        "| Network | Normal | Operational | Emergency | Extreme |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for network, row in zip(report["networks"], report["raw_matrix"]):
        lines.append(f"| {network} | " + " | ".join(str(value) for value in row) + " |")
    lines.extend(
        [
            "",
            "## Severity-conditioned maximum network support",
            "",
            "| Severity | Supported networks | Per-network selected | Total |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for item in report["severity_conditioned_maximum_network_support"]["strata"]:
        lines.append(
            "| {severity} | {networks} | {target} | {total} |".format(
                severity=item["severity"],
                networks=", ".join(item["maximum_supported_networks"]),
                target=item["target_per_network"],
                total=sum(item["selected_per_network"].values()),
            )
        )
    if report["resampling_commands"]:
        lines.extend(["", "## Executable resampling commands", ""])
        for item in report["resampling_commands"]:
            lines.append(f"- `{item['command']}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenarios",
        default="reports/extended_topology_stress_attempts_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--additional-scenarios",
        action="append",
        default=[],
        help="Additional generated scenario pools to combine before matching.",
    )
    parser.add_argument("--target-per-cell", type=int, default=13)
    parser.add_argument("--seed", type=int, default=20260723)
    parser.add_argument("--pglib-root", type=Path, required=True)
    parser.add_argument("--pglib-commit", default=PGLIB_EXPECTED_COMMIT)
    parser.add_argument("--rating-overrides", type=Path)
    parser.add_argument(
        "--output-json",
        default="reports/external_network_severity_matched_strata_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--output-md",
        default="reports/external_network_severity_matched_strata_v1.2_sd_core.md",
    )
    parser.add_argument(
        "--selected-json",
        default="simulation_outputs/topology_stress/external_matched_strata_v1.json",
    )
    args = parser.parse_args()
    if args.target_per_cell <= 0:
        raise ValueError("--target-per-cell must be positive")
    source_path = ROOT / args.scenarios
    rows = read_json(source_path)
    additional_sources = [ROOT / value for value in args.additional_scenarios]
    for path in additional_sources:
        rows.extend(read_json(path))
    repository = validate_pglib_repository(args.pglib_root, args.pglib_commit)
    manual_overrides = load_rating_overrides(args.rating_overrides)
    rating_maps, rating_coverage = source_bound_rating_maps(
        args.pglib_root,
        repository,
        manual_overrides,
    )
    report, selected = build_report(
        rows,
        target_per_cell=args.target_per_cell,
        seed=args.seed,
        source_path=source_path,
        rating_maps=rating_maps,
        repository_commit=repository["commit"],
        rating_coverage=rating_coverage,
        pglib_root=args.pglib_root,
    )
    report["additional_sources"] = [
        {"path": str(path), "sha256": sha256_file(path)}
        for path in additional_sources
    ]
    report["pglib_repository"] = repository
    report["rating_override"] = (
        {"path": str(args.rating_overrides), "sha256": sha256_file(args.rating_overrides)}
        if args.rating_overrides
        else None
    )
    ensure_dirs((ROOT / args.output_json).parent, (ROOT / args.selected_json).parent)
    write_json(ROOT / args.output_json, report)
    write_json(ROOT / args.selected_json, selected)
    write_markdown(report, ROOT / args.output_md)
    print(
        json.dumps(
            {
                "status": report["status"],
                "full_factorial_diagnostic_shortage": report["total_shortage"],
                "common_support": report["common_support"],
            },
            indent=2,
        )
    )
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
