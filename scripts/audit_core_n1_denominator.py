#!/usr/bin/env python3
"""Audit the complete core-network N-1 line-outage generation denominator."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_json, write_json
from pglib_network_loader import load_pglib_network, network_provenance


CONTRACT_VERSION = "gridinstruct-core4-line-n1-denominator-v2"
CORE_SYSTEM_LOAD_LEVELS = {
    "ieee14": (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3),
    "ieee30": (0.7, 0.75, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3),
    "ieee57": (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3),
    "ieee118": (0.7, 0.8, 0.9, 1.0, 1.1, 1.2),
}
FAILURE_CLASSES = {
    "unsupplied_island",
    "numerical_nonconvergence",
    "invalid_or_infeasible",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_load(value: Any) -> str:
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise ValueError(f"non-finite load level: {value!r}")
    return format(decimal.normalize(), "f")


def identity(system: str, load_level: Any, line_index: int) -> tuple[str, str, int]:
    return system, canonical_load(load_level), int(line_index)


def serialized_identities(values: set[tuple[str, str, int]]) -> list[str]:
    return [f"{system}|{load}|line:{index}" for system, load, index in sorted(values)]


def identity_sha256(values: set[tuple[str, str, int]]) -> str:
    payload = json.dumps(
        serialized_identities(values),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_system_load_levels(
    specifications: list[str],
) -> dict[str, tuple[float, ...]]:
    if not specifications:
        return dict(CORE_SYSTEM_LOAD_LEVELS)
    result: dict[str, tuple[float, ...]] = {}
    for specification in specifications:
        system, separator, values = specification.partition(":")
        if not separator or not system or not values:
            raise ValueError(f"invalid system-load specification: {specification}")
        parsed = tuple(float(value) for value in values.split(",") if value)
        tokens = [canonical_load(value) for value in parsed]
        if not parsed or len(tokens) != len(set(tokens)):
            raise ValueError(
                f"duplicate or empty load levels for {system}: {specification}"
            )
        result[system] = parsed
    if set(result) != set(CORE_SYSTEM_LOAD_LEVELS):
        raise ValueError(
            "core N-1 scope must be exactly ieee14, ieee30, ieee57, and ieee118"
        )
    return result


def input_entry(path: Path) -> dict[str, Any]:
    try:
        display_path = str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        display_path = str(path)
    return {"path": display_path, "sha256": file_sha256(path)}


def provenance_errors(
    row: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    source = row.get("physical_source")
    if not isinstance(source, dict):
        return ["missing_physical_source"]
    errors = []
    for field in (
        "repository_commit",
        "license_sha256",
        "case_file",
        "case_sha256",
    ):
        if str(source.get(field) or "") != str(expected.get(field) or ""):
            errors.append(f"physical_source_{field}_mismatch")
    return errors


def row_identity(row: dict[str, Any]) -> tuple[str, str, int] | None:
    indices = row.get("line_indices")
    if not isinstance(indices, list) or len(indices) != 1:
        return None
    try:
        return identity(
            str(row.get("system") or ""),
            row.get("load_level"),
            int(indices[0]),
        )
    except (TypeError, ValueError):
        return None


def filtered_n1(
    rows: list[dict[str, Any]],
    systems: set[str],
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("system") or "") in systems
        and row.get("contingency_type") == "n1_branch_outage"
    ]


def build_audit(
    *,
    attempts: list[dict[str, Any]],
    converged_core: list[dict[str, Any]],
    complete_truth: list[dict[str, Any]],
    simulation_report: dict[str, Any],
    case_registry: dict[str, Any],
    system_load_levels: dict[str, tuple[float, ...]],
    pglib_root: Path,
    input_paths: dict[str, Path],
) -> dict[str, Any]:
    systems = set(system_load_levels)
    expected: set[tuple[str, str, int]] = set()
    expected_endpoints: dict[tuple[str, int], str] = {}
    expected_sources: dict[str, dict[str, Any]] = {}
    denominator_by_system: dict[str, dict[str, Any]] = {}
    source_contract: dict[str, Any] = {
        "repository": case_registry.get("repository"),
        "repository_commit": case_registry.get("commit"),
        "license": case_registry.get("license"),
        "license_sha256": case_registry.get("license_sha256"),
        "cases": {},
    }
    source_construction_errors = []

    for system in sorted(systems):
        net = load_pglib_network(system, pglib_root)
        source = network_provenance(net)
        expected_sources[system] = source
        registry_case = (case_registry.get("cases") or {}).get(system) or {}
        source_contract["cases"][system] = {
            "case_file": source.get("case_file"),
            "case_sha256": source.get("case_sha256"),
        }
        if (
            str(source.get("repository_commit") or "")
            != str(case_registry.get("commit") or "")
            or str(source.get("license_sha256") or "")
            != str(case_registry.get("license_sha256") or "")
            or str(source.get("case_file") or "")
            != str(registry_case.get("file") or "")
            or str(source.get("case_sha256") or "")
            != str(registry_case.get("sha256") or "")
        ):
            source_construction_errors.append(f"{system}:registry_binding_mismatch")
        line_indices = [
            int(index)
            for index, row in net.line.sort_index().iterrows()
            if bool(row.get("in_service", True))
        ]
        for index in line_indices:
            line = net.line.loc[index]
            expected_endpoints[(system, index)] = (
                f"{int(line['from_bus'])}-{int(line['to_bus'])}"
            )
        for load_level in system_load_levels[system]:
            for index in line_indices:
                expected.add(identity(system, load_level, index))
        denominator_by_system[system] = {
            "registered_load_levels": list(system_load_levels[system]),
            "registered_load_strata": len(system_load_levels[system]),
            "in_service_line_count": len(line_indices),
            "expected_attempt_count": len(line_indices)
            * len(system_load_levels[system]),
        }

    attempt_rows = filtered_n1(attempts, systems)
    core_rows = filtered_n1(converged_core, systems)
    truth_rows = filtered_n1(complete_truth, systems)

    row_errors: list[str] = []
    observed_keys: list[tuple[str, str, int]] = []
    attempt_ids: list[str] = []
    converged_attempt_ids: set[str] = set()
    failed_attempt_ids: set[str] = set()
    outcome_counts: Counter[str] = Counter()
    expected_load_tokens = {
        system: {canonical_load(value) for value in levels}
        for system, levels in system_load_levels.items()
    }

    for row in attempt_rows:
        scenario_id = str(row.get("scenario_id") or "")
        attempt_ids.append(scenario_id)
        key = row_identity(row)
        if key is None:
            row_errors.append(f"{scenario_id}:invalid_line_identity")
            continue
        observed_keys.append(key)
        system, load_token, line_index = key
        if load_token not in expected_load_tokens.get(system, set()):
            row_errors.append(f"{scenario_id}:unregistered_load_level")
        branches = ((row.get("affected_components") or {}).get("branches") or [])
        expected_endpoint = expected_endpoints.get((system, line_index))
        if branches != [expected_endpoint]:
            row_errors.append(f"{scenario_id}:affected_branch_identity_mismatch")
        if row.get("counts_in_security_denominator") is not True:
            row_errors.append(f"{scenario_id}:missing_security_denominator_flag")
        try:
            scaling_matches = math.isclose(
                float(row.get("base_generation_scaling_factor")),
                float(row.get("load_level")),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        except (TypeError, ValueError):
            scaling_matches = False
        if not scaling_matches:
            row_errors.append(f"{scenario_id}:generation_scaling_mismatch")
        row_errors.extend(
            f"{scenario_id}:{error}"
            for error in provenance_errors(row, expected_sources.get(system, {}))
        )

        status = str(row.get("solver_status") or "")
        outcome_counts[status] += 1
        if status == "converged":
            converged_attempt_ids.add(scenario_id)
            if row.get("failure_class") is not None or row.get("error") is not None:
                row_errors.append(f"{scenario_id}:converged_failure_fields_not_empty")
        else:
            failed_attempt_ids.add(scenario_id)
            if (
                status not in FAILURE_CLASSES
                or row.get("failure_class") != status
                or not str(row.get("error") or "")
            ):
                row_errors.append(f"{scenario_id}:unclassified_failed_attempt")

    observed = set(observed_keys)
    duplicate_keys = [
        key for key, count in Counter(observed_keys).items() if count != 1
    ]
    duplicate_ids = [
        value
        for value, count in Counter(attempt_ids).items()
        if not value or count != 1
    ]
    core_ids = {str(row.get("scenario_id") or "") for row in core_rows}
    truth_ids = {str(row.get("scenario_id") or "") for row in truth_rows}
    core_id_count_valid = len(core_ids) == len(core_rows) and "" not in core_ids
    truth_id_count_valid = len(truth_ids) == len(truth_rows) and "" not in truth_ids

    report_levels = (
        (simulation_report.get("config") or {}).get("system_load_levels") or {}
    )
    report_level_tokens = {
        system: {canonical_load(value) for value in values}
        for system, values in report_levels.items()
        if system in systems
    }
    registered_level_tokens_match = report_level_tokens == expected_load_tokens

    hard_gates = {
        "scope_is_exactly_four_core_networks": systems
        == set(CORE_SYSTEM_LOAD_LEVELS),
        "registered_load_levels_are_formal": {
            system: {canonical_load(value) for value in levels}
            for system, levels in system_load_levels.items()
        }
        == {
            system: {canonical_load(value) for value in levels}
            for system, levels in CORE_SYSTEM_LOAD_LEVELS.items()
        },
        "pglib_registry_and_loaded_sources_match": not source_construction_errors,
        "simulation_generation_report_passed": simulation_report.get("status")
        == "pass",
        "registered_load_levels_match_generation_report": registered_level_tokens_match,
        "expected_and_attempt_identity_sets_match": observed == expected,
        "attempt_identity_keys_unique": not duplicate_keys,
        "attempt_scenario_ids_unique_and_nonempty": not duplicate_ids,
        "attempt_rows_are_contract_complete": not row_errors,
        "converged_attempt_subset_matches_core_truth": converged_attempt_ids
        == core_ids,
        "complete_truth_subset_matches_core_truth": truth_ids == core_ids,
        "core_truth_ids_unique_and_nonempty": core_id_count_valid,
        "complete_truth_ids_unique_and_nonempty": truth_id_count_valid,
        "failed_attempts_are_excluded_from_released_truth": not (
            failed_attempt_ids & (core_ids | truth_ids)
        ),
        "all_input_hashes_present": all(
            path.is_file() for path in input_paths.values()
        ),
    }
    missing = expected - observed
    extra = observed - expected
    status = "pass" if all(hard_gates.values()) else "fail"

    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "scope": {
            "systems": sorted(systems),
            "contingency_type": "n1_branch_outage",
            "element_table": "line",
            "registered_system_load_strata": sum(
                len(values) for values in system_load_levels.values()
            ),
        },
        "source_contract": source_contract,
        "inputs": {
            name: input_entry(path) for name, path in sorted(input_paths.items())
        },
        "denominator": {
            "by_system": denominator_by_system,
            "expected_attempt_count": len(expected),
            "observed_attempt_count": len(attempt_rows),
            "unique_observed_identity_count": len(observed),
        },
        "outcomes": {
            "by_solver_status": dict(sorted(outcome_counts.items())),
            "converged_attempt_count": len(converged_attempt_ids),
            "failed_attempt_count": len(failed_attempt_ids),
            "core_converged_truth_count": len(core_rows),
            "complete_truth_core_n1_count": len(truth_rows),
        },
        "set_audit": {
            "expected_identity_sha256": identity_sha256(expected),
            "attempt_identity_sha256": identity_sha256(observed),
            "core_converged_scenario_id_sha256": identity_sha256(
                {
                    ("scenario_id", scenario_id, 0)
                    for scenario_id in core_ids
                }
            ),
            "complete_truth_scenario_id_sha256": identity_sha256(
                {
                    ("scenario_id", scenario_id, 0)
                    for scenario_id in truth_ids
                }
            ),
            "missing_identity_count": len(missing),
            "extra_identity_count": len(extra),
            "duplicate_identity_count": len(duplicate_keys),
            "duplicate_scenario_id_count": len(duplicate_ids),
            "missing_identity_examples": serialized_identities(missing)[:20],
            "extra_identity_examples": serialized_identities(extra)[:20],
            "duplicate_identity_examples": serialized_identities(
                set(duplicate_keys)
            )[:20],
        },
        "diagnostics": {
            "source_construction_errors": source_construction_errors[:50],
            "row_contract_error_count": len(row_errors),
            "row_contract_errors": row_errors[:100],
        },
        "hard_gates": hard_gates,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    denominator = report["denominator"]
    outcomes = report["outcomes"]
    lines = [
        "# Core-4 full N-1 line denominator audit",
        "",
        f"- Status: `{report['status']}`",
        f"- Registered system-load strata: {report['scope']['registered_system_load_strata']}",
        f"- Expected attempts: {denominator['expected_attempt_count']}",
        f"- Observed attempts: {denominator['observed_attempt_count']}",
        f"- Converged attempts: {outcomes['converged_attempt_count']}",
        f"- Classified non-converged attempts: {outcomes['failed_attempt_count']}",
        "",
        "| System | Load strata | In-service lines | Expected attempts |",
        "| --- | ---: | ---: | ---: |",
    ]
    for system, row in denominator["by_system"].items():
        lines.append(
            f"| {system.upper()} | {row['registered_load_strata']} | "
            f"{row['in_service_line_count']} | {row['expected_attempt_count']} |"
        )
    lines.extend(["", "## Hard gates", ""])
    lines.extend(
        f"- `{name}`: {'PASS' if passed else 'FAIL'}"
        for name, passed in report["hard_gates"].items()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--attempts",
        default="simulation_outputs/power_flow/scenarios_all.json",
    )
    parser.add_argument(
        "--converged-core",
        default="simulation_outputs/power_flow/scenarios_converged_core.json",
    )
    parser.add_argument(
        "--complete-truth",
        default="simulation_outputs/contingency/scenarios_converged.json",
    )
    parser.add_argument("--simulation-report", default="reports/simulation_report.json")
    parser.add_argument(
        "--case-registry",
        default="metadata/pglib_opf_v23_07_case_registry.json",
    )
    parser.add_argument(
        "--pglib-root",
        default="third_party/pglib-opf-v23.07",
    )
    parser.add_argument("--system-load-levels", action="append", default=[])
    parser.add_argument(
        "--output-json",
        default="reports/core_n1_denominator_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--output-md",
        default="reports/core_n1_denominator_v1.2_sd_core.md",
    )
    args = parser.parse_args()

    paths = {
        "attempts": ROOT / args.attempts,
        "converged_core": ROOT / args.converged_core,
        "complete_truth": ROOT / args.complete_truth,
        "simulation_report": ROOT / args.simulation_report,
        "case_registry": ROOT / args.case_registry,
        "generator_script": ROOT / "scripts/generate_grid_scenarios.py",
        "pglib_loader_script": ROOT / "scripts/pglib_network_loader.py",
        "power_evidence_common_script": ROOT / "scripts/power_evidence_common.py",
        "audit_script": Path(__file__).resolve(),
        "pglib_license": ROOT / args.pglib_root / "LICENSE",
        "pglib_case_ieee14": ROOT
        / args.pglib_root
        / "pglib_opf_case14_ieee.m",
        "pglib_case_ieee30": ROOT
        / args.pglib_root
        / "pglib_opf_case30_ieee.m",
        "pglib_case_ieee57": ROOT
        / args.pglib_root
        / "pglib_opf_case57_ieee.m",
        "pglib_case_ieee118": ROOT
        / args.pglib_root
        / "pglib_opf_case118_ieee.m",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"core N-1 audit inputs are missing: {missing}")

    report = build_audit(
        attempts=read_json(paths["attempts"]),
        converged_core=read_json(paths["converged_core"]),
        complete_truth=read_json(paths["complete_truth"]),
        simulation_report=read_json(paths["simulation_report"]),
        case_registry=read_json(paths["case_registry"]),
        system_load_levels=parse_system_load_levels(args.system_load_levels),
        pglib_root=ROOT / args.pglib_root,
        input_paths=paths,
    )
    output_json = ROOT / args.output_json
    output_md = ROOT / args.output_md
    write_json(output_json, report)
    write_markdown(report, output_md)
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
