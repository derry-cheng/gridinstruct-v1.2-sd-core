#!/usr/bin/env python3
"""Audit equipment-rating provenance across all eleven released networks."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, write_json
from power_evidence_common import (
    NETWORK_LOADERS,
    PGLIB_EXPECTED_COMMIT,
    apply_rating_overrides,
    finite_number,
    load_pglib_case,
    load_network,
    load_rating_overrides,
    pglib_source_descriptor,
    pglib_rate_a_semantics,
    rating_record,
    sha256_file,
    source_descriptor,
    validate_pglib_repository,
)


def audit_system(system: str, overrides: list[dict[str, Any]]) -> dict[str, Any]:
    net = load_network(system)
    applied = apply_rating_overrides(net, system, overrides)
    records = []
    for element in ("line", "trafo", "trafo3w", "gen"):
        table = getattr(net, element)
        for index in table.index:
            record = rating_record(net, system, element, int(index))
            if (element, int(index)) in applied:
                matching = next(
                    row
                    for row in overrides
                    if str(row.get("network", "")).lower() == system
                    and row.get("element") == element
                    and int(row.get("index")) == int(index)
                )
                record["source"] = matching["source"]
                record["rating_status"] = "known"
                record["reasons"] = []
                record["source_override_applied"] = True
            records.append(record)
    counts = Counter(row["rating_status"] for row in records if row["in_service"])
    unknown_by_element = Counter(
        row["element"]
        for row in records
        if row["in_service"] and row["rating_status"] != "known"
    )
    return {
        "network": system,
        "network_source": source_descriptor(system),
        "counts": {
            "audited_in_service": sum(row["in_service"] for row in records),
            "known": counts.get("known", 0),
            "unknown": counts.get("unknown", 0),
            "unknown_by_element": dict(unknown_by_element),
        },
        "hard_gate": counts.get("unknown", 0) == 0,
        "equipment": records,
    }


def audit_pglib_system(
    system: str,
    pglib_root: Path,
    repository: dict[str, Any],
    overrides: list[dict[str, Any]],
) -> dict[str, Any]:
    case = load_pglib_case(pglib_root, system)
    bus_voltage = {int(row[0]): float(row[9]) for row in case["bus"]}
    records = []
    for row_number, row in enumerate(case["branch"], start=1):
        if int(row[10]) == 0:
            continue
        rating = finite_number(row[5])
        reasons = []
        semantics = pglib_rate_a_semantics(rating)
        if semantics == "missing_or_nonpositive":
            reasons.append("missing_or_nonpositive_rate_a")
        from_bus = int(row[0])
        to_bus = int(row[1])
        tap_ratio = float(row[8])
        is_transformer = tap_ratio != 0.0 or bus_voltage[from_bus] != bus_voltage[to_bus]
        records.append(
            {
                "network": system,
                "element": "trafo" if is_transformer else "line",
                "source_row": row_number,
                "endpoints": [from_bus, to_bus],
                "in_service": True,
                "rating_field": "RATE_A",
                "rating_value": rating,
                "rating_status": (
                    "unknown"
                    if reasons
                    else "source_explicit_high_limit"
                    if semantics == "source_explicit_high_limit"
                    else "known"
                ),
                "rating_semantics": semantics,
                "reasons": reasons,
                "source": pglib_source_descriptor(
                    repository,
                    case,
                    f"mpc.branch row {row_number}, RATE_A",
                ),
            }
        )
    for row_number, row in enumerate(case["gen"], start=1):
        if int(row[7]) == 0:
            continue
        maximum = finite_number(row[8])
        minimum = finite_number(row[9])
        maximum_q = finite_number(row[3])
        minimum_q = finite_number(row[4])
        machine_base_mva = finite_number(row[6])
        reasons = []
        if (
            maximum is None
            or minimum is None
            or maximum_q is None
            or minimum_q is None
            or machine_base_mva is None
        ):
            reasons.append("missing_or_nonfinite_generator_capability")
        elif minimum > maximum or minimum_q > maximum_q or machine_base_mva <= 0:
            reasons.append("invalid_generator_capability")
        records.append(
            {
                "network": system,
                "element": "gen",
                "source_row": row_number,
                "bus": int(row[0]),
                "in_service": True,
                "rating_field": "PMIN,PMAX,QMIN,QMAX,MBASE",
                "rating_value": {
                    "min_p_mw": minimum,
                    "max_p_mw": maximum,
                    "min_q_mvar": minimum_q,
                    "max_q_mvar": maximum_q,
                    "machine_base_mva": machine_base_mva,
                },
                "rating_status": "unknown" if reasons else "known",
                "reasons": reasons,
                "source": pglib_source_descriptor(
                    repository,
                    case,
                    f"mpc.gen row {row_number}, PMIN/PMAX",
                ),
            }
        )
    for record in records:
        matching = next(
            (
                row
                for row in overrides
                if str(row.get("network", "")).lower() == system
                and row.get("element") == record["element"]
                and int(row.get("pglib_source_row", -1)) == record["source_row"]
            ),
            None,
        )
        if matching is None:
            continue
        value = finite_number(matching.get("rating_value"))
        if value is None or value <= 0:
            raise ValueError(
                f"invalid PGLib rating override: {system}/{record['element']}/"
                f"{record['source_row']}"
            )
        record["rating_value"] = value
        record["rating_status"] = "known"
        record["reasons"] = []
        record["source"] = matching["source"]
        record["source_override_applied"] = True
    counts = Counter(row["rating_status"] for row in records)
    unknown_by_element = Counter(
        row["element"] for row in records if row["rating_status"] == "unknown"
    )
    return {
        "network": system,
        "network_source": {
            **pglib_source_descriptor(repository, case, "complete MATPOWER case"),
            "bus_count": len(case["bus"]),
            "branch_count": len(case["branch"]),
            "generator_count": len(case["gen"]),
        },
        "counts": {
            "audited_in_service": len(records),
            "known": counts.get("known", 0),
            "source_explicit_high_limit": counts.get(
                "source_explicit_high_limit", 0
            ),
            "unknown": counts.get("unknown", 0),
            "unknown_by_element": dict(unknown_by_element),
        },
        "hard_gate": counts.get("unknown", 0) == 0,
        "thermal_sensitivity_required": (
            counts.get("source_explicit_high_limit", 0) > 0
        ),
        "equipment": records,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Equipment Rating Provenance Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Status: `{report['status']}`",
        f"- Networks: {report['network_count']}",
        f"- In-service equipment audited: {report['summary']['audited_in_service']}",
        f"- Unknown or placeholder ratings: {report['summary']['unknown']}",
        f"- Source-explicit high limits: {report['summary'].get('source_explicit_high_limit', 0)}",
        "",
        "A converged power flow is not accepted as thermal-security evidence when any",
        "in-service branch rating is missing, invalid, or encoded by a known placeholder.",
        "",
        "| Network | Audited | Known | Explicit high | Unknown | Gate |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["networks"]:
        counts = item["counts"]
        lines.append(
            f"| {item['network']} | {counts['audited_in_service']} | "
            f"{counts['known']} | {counts.get('source_explicit_high_limit', 0)} | "
            f"{counts['unknown']} | "
            f"{'PASS' if item['hard_gate'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Gate",
            "",
            "Every in-service line, two-winding transformer, three-winding transformer,",
            "and controllable generator must have a finite source-bound rating. Overrides",
            "are accepted only with a source document identifier, SHA-256, and locator.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(
    systems: list[str],
    overrides: list[dict[str, Any]],
    override_path: Path | None,
    *,
    pglib_root: Path | None = None,
    pglib_commit: str = PGLIB_EXPECTED_COMMIT,
) -> dict[str, Any]:
    repository = (
        validate_pglib_repository(pglib_root, pglib_commit)
        if pglib_root
        else None
    )
    networks = (
        [
            audit_pglib_system(system, pglib_root, repository, overrides)
            for system in systems
        ]
        if pglib_root and repository
        else [audit_system(system, overrides) for system in systems]
    )
    summary = {
        "audited_in_service": sum(row["counts"]["audited_in_service"] for row in networks),
        "known": sum(row["counts"]["known"] for row in networks),
        "source_explicit_high_limit": sum(
            row["counts"].get("source_explicit_high_limit", 0)
            for row in networks
        ),
        "unknown": sum(row["counts"]["unknown"] for row in networks),
    }
    return {
        "contract_version": "gridinstruct-equipment-rating-provenance-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(row["hard_gate"] for row in networks) else "fail",
        "network_count": len(networks),
        "network_input_contract": "pglib-opf" if pglib_root else "pandapower-bundled",
        "pglib_repository": repository,
        "networks": networks,
        "summary": summary,
        "rating_override": (
            {"path": str(override_path), "sha256": sha256_file(override_path)}
            if override_path
            else None
        ),
        "hard_gates": {
            "all_requested_networks_loaded": len(networks) == len(systems),
            "all_in_service_ratings_source_bound": summary["unknown"] == 0,
            "unknown_rating_never_defaults_to_safe": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", nargs="+", default=list(NETWORK_LOADERS))
    parser.add_argument("--rating-overrides", type=Path)
    parser.add_argument(
        "--pglib-root",
        type=Path,
        help="Use source-native PGLib MATPOWER cases for the formal rating audit.",
    )
    parser.add_argument("--pglib-commit", default=PGLIB_EXPECTED_COMMIT)
    parser.add_argument(
        "--output-json",
        default="reports/equipment_rating_provenance_v1.2_sd_core.json",
    )
    parser.add_argument(
        "--output-md",
        default="reports/equipment_rating_provenance_v1.2_sd_core.md",
    )
    args = parser.parse_args()
    unknown = sorted(set(args.systems) - set(NETWORK_LOADERS))
    if unknown:
        raise ValueError(f"unknown networks: {unknown}")
    overrides = load_rating_overrides(args.rating_overrides)
    report = build_report(
        args.systems,
        overrides,
        args.rating_overrides,
        pglib_root=args.pglib_root,
        pglib_commit=args.pglib_commit,
    )
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.output_json, report)
    write_markdown(report, ROOT / args.output_md)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
