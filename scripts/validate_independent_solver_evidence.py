#!/usr/bin/env python3
"""Validate evidence produced by a genuinely independent power-flow solver."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json
from power_evidence_common import sha256_file


DISALLOWED_SOLVER_NAMES = {"pandapower", "pypower"}
DISALLOWED_IMPORT_TOKENS = {
    "pandapower",
    "pandapower.converter",
    "pandapower-to-ppc",
    "pandapower_to_ppc",
    "to_ppc",
}
ALLOWED_INDEPENDENCE_CLASSES = {
    "native_source_case",
    "independent_data_model",
}


def evidence_class(solver: dict[str, Any]) -> str:
    name = str(solver.get("name") or "").strip().lower()
    import_path = " ".join(
        str(value).lower()
        for value in (
            solver.get("input_import_path"),
            solver.get("conversion_chain"),
            solver.get("case_origin"),
        )
        if value is not None
    )
    if (
        any(disallowed in name for disallowed in DISALLOWED_SOLVER_NAMES)
        or any(token in import_path for token in DISALLOWED_IMPORT_TOKENS)
    ):
        return "exported_replay"
    if solver.get("independence_class") in ALLOWED_INDEPENDENCE_CLASSES:
        return "independent_solver"
    return "unclassified"


def validate_report(payload: dict[str, Any]) -> dict[str, Any]:
    errors = []
    if payload.get("contract_version") != "gridinstruct-independent-solver-evidence-v1":
        errors.append("invalid_contract_version")
    solver = payload.get("solver") or {}
    classification = evidence_class(solver)
    for field in ("name", "version", "implementation_repository", "independence_class"):
        if not solver.get(field):
            errors.append(f"missing_solver_{field}")
    if classification != "independent_solver":
        errors.append(f"solver_evidence_class:{classification}")
    if solver.get("comparison_scope") != (
        "fixed-injection fixed-control AC power-flow replay"
    ):
        errors.append("invalid_solver_comparison_scope")
    if solver.get("independent_opf_claimed") is not False:
        errors.append("independent_opf_scope_misclaimed")
    if "enforce_q_lims=False" not in str(solver.get("q_limit_policy") or ""):
        errors.append("q_limit_policy_not_registered")
    manifest = payload.get("case_manifest") or {}
    if not manifest.get("sha256") or len(str(manifest.get("sha256"))) != 64:
        errors.append("missing_case_manifest_sha256")
    tolerances = payload.get("tolerances") or {}
    required_tolerances = {
        "voltage_pu",
        "angle_degree",
        "active_power_mw",
        "reactive_power_mvar",
        "power_balance_mw",
    }
    if not required_tolerances.issubset(tolerances):
        errors.append("incomplete_tolerances")
    cases = payload.get("cases") or []
    if not cases:
        errors.append("empty_case_set")
    case_errors = []
    for row in cases:
        row_errors = []
        source = row.get("source_case") or {}
        for field in ("provider", "identifier", "version", "sha256"):
            if not source.get(field):
                row_errors.append(f"missing_source_case_{field}")
        source_digest = str(source.get("sha256") or "").lower()
        if len(source_digest) != 64 or any(
            character not in "0123456789abcdef" for character in source_digest
        ):
            row_errors.append("invalid_source_case_sha256")
        if row.get("source_model_class") != (
            "pglib_native_parameters_and_operating_point"
        ):
            row_errors.append("invalid_source_model_class")
        result = row.get("independent_result") or {}
        if result.get("converged") is not True:
            row_errors.append("independent_solver_not_converged")
        comparison = row.get("comparison") or {}
        checks = {
            "voltage": (
                comparison.get("max_abs_bus_voltage_error_pu"),
                tolerances.get("voltage_pu"),
            ),
            "active_power": (
                comparison.get("max_abs_active_power_error_mw"),
                tolerances.get("active_power_mw"),
            ),
            "angle": (
                comparison.get("max_abs_bus_angle_error_degree"),
                tolerances.get("angle_degree"),
            ),
            "reactive_power": (
                comparison.get("max_abs_reactive_power_error_mvar"),
                tolerances.get("reactive_power_mvar"),
            ),
            "power_balance": (
                result.get("power_balance_residual_mw"),
                tolerances.get("power_balance_mw"),
            ),
        }
        for name, (value, tolerance) in checks.items():
            try:
                numeric_value = float(value)
                numeric_tolerance = float(tolerance)
                if (
                    not math.isfinite(numeric_value)
                    or not math.isfinite(numeric_tolerance)
                    or numeric_tolerance < 0.0
                    or numeric_value > numeric_tolerance
                ):
                    row_errors.append(f"{name}_tolerance_exceeded")
            except (TypeError, ValueError):
                row_errors.append(f"{name}_comparison_missing")
        if comparison.get("topology_identity_match") is not True:
            row_errors.append("topology_identity_mismatch")
        if comparison.get("thermal_label_match") is not True:
            row_errors.append("thermal_label_mismatch")
        if comparison.get("voltage_label_match") is not True:
            row_errors.append("voltage_label_mismatch")
        if row_errors:
            case_errors.append({"case_id": row.get("case_id"), "errors": row_errors})
    if case_errors:
        errors.append("case_validation_failed")
    case_ids = [str(row.get("case_id") or "") for row in cases]
    if "" in case_ids or len(case_ids) != len(set(case_ids)):
        errors.append("case_ids_missing_or_nonunique")
    if payload.get("case_count") not in {None, len(cases)}:
        errors.append("case_count_mismatch")
    return {
        "status": "pass" if not errors else "fail",
        "solver_evidence_class": classification,
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(case_errors),
        "errors": errors,
        "case_errors": case_errors,
        "interpretation": (
            "pandapower to ppc to PYPOWER is classified as exported replay. "
            "Only a native-source or independent-data-model run by a separately "
            "implemented solver can satisfy this gate."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument(
        "--case-manifest",
        type=Path,
        help="Bind the claimed case-manifest digest to the actual manifest file.",
    )
    parser.add_argument(
        "--output",
        default="reports/independent_solver_validation_v1.2_sd_core.json",
    )
    args = parser.parse_args()
    payload = read_json(args.input)
    validation = validate_report(payload)
    manifest_binding = None
    if args.case_manifest:
        actual_manifest_sha256 = sha256_file(args.case_manifest)
        claimed_manifest_sha256 = str(
            (payload.get("case_manifest") or {}).get("sha256") or ""
        )
        manifest_binding = {
            "path": str(args.case_manifest),
            "actual_sha256": actual_manifest_sha256,
            "claimed_sha256": claimed_manifest_sha256,
            "match": actual_manifest_sha256 == claimed_manifest_sha256,
        }
        if not manifest_binding["match"]:
            validation["status"] = "fail"
            validation["errors"].append("case_manifest_sha256_mismatch")
        manifest_payload = read_json(args.case_manifest)
        manifest_case_ids = {
            str(row.get("case_id") or "")
            for row in (manifest_payload.get("cases") or [])
        }
        evidence_case_ids = {
            str(row.get("case_id") or "")
            for row in (payload.get("cases") or [])
        }
        manifest_binding["case_ids_match"] = manifest_case_ids == evidence_case_ids
        if not manifest_binding["case_ids_match"]:
            validation["status"] = "fail"
            validation["errors"].append("case_manifest_case_ids_mismatch")
    report = {
        "contract_version": "gridinstruct-independent-solver-evidence-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {"path": str(args.input), "sha256": sha256_file(args.input)},
        "case_manifest_binding": manifest_binding,
        **validation,
    }
    ensure_dirs((ROOT / args.output).parent)
    write_json(ROOT / args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
