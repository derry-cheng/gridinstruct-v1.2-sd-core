from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandapower as pp


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_core_n1_denominator as audit  # noqa: E402


SYSTEMS = ("ieee14", "ieee30", "ieee57", "ieee118")
COMMIT = "fixed-commit"
LICENSE_SHA = "license-sha"


def source(system: str) -> dict:
    return {
        "repository_commit": COMMIT,
        "license_sha256": LICENSE_SHA,
        "case_file": f"{system}.m",
        "case_sha256": f"{system}-sha",
    }


def fake_net(system: str):
    net = pp.create_empty_network()
    first = pp.create_bus(net, vn_kv=110.0)
    second = pp.create_bus(net, vn_kv=110.0)
    line = pp.create_line_from_parameters(
        net,
        first,
        second,
        length_km=1.0,
        r_ohm_per_km=0.01,
        x_ohm_per_km=0.02,
        c_nf_per_km=0.0,
        max_i_ka=1.0,
    )
    net.line.rename(index={line: 10}, inplace=True)
    net["pglib_provenance"] = source(system)
    return net


def attempt(
    system: str,
    *,
    load_level: float = 1.0,
    status: str = "converged",
) -> dict:
    failed = status != "converged"
    load_token = str(load_level).replace(".", "p")
    return {
        "scenario_id": (
            f"{system}_n1_branch_outage_load{load_token}_branchidx_10_0_1"
        ),
        "system": system,
        "contingency_type": "n1_branch_outage",
        "load_level": load_level,
        "base_generation_scaling_factor": load_level,
        "line_indices": [10],
        "affected_components": {"branches": ["0-1"]},
        "physical_source": source(system),
        "counts_in_security_denominator": True,
        "solver_status": status,
        "failure_class": status if failed else None,
        "unsupplied_buses": [1] if status == "unsupplied_island" else [],
        "error": f"{status} diagnostic" if failed else None,
    }


def registry() -> dict:
    return {
        "repository": "https://example.invalid/pglib",
        "commit": COMMIT,
        "license": "CC BY 4.0",
        "license_sha256": LICENSE_SHA,
        "cases": {
            system: {"file": f"{system}.m", "sha256": f"{system}-sha"}
            for system in SYSTEMS
        },
    }


def input_paths(tmp_path: Path) -> dict[str, Path]:
    paths = {}
    for name in (
        "attempts",
        "converged_core",
        "complete_truth",
        "simulation_report",
        "case_registry",
        "generator_script",
        "pglib_loader_script",
        "power_evidence_common_script",
        "audit_script",
        "pglib_license",
    ):
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        paths[name] = path
    return paths


def build(
    tmp_path: Path,
    monkeypatch,
    *,
    attempts: list[dict] | None = None,
    converged: list[dict] | None = None,
    truth: list[dict] | None = None,
) -> dict:
    monkeypatch.setattr(audit, "load_pglib_network", lambda system, _: fake_net(system))
    monkeypatch.setattr(
        audit,
        "network_provenance",
        lambda net: copy.deepcopy(net["pglib_provenance"]),
    )
    rows = (
        attempts
        if attempts is not None
        else [
            attempt(system, load_level=load_level)
            for system, levels in audit.CORE_SYSTEM_LOAD_LEVELS.items()
            for load_level in levels
        ]
    )
    released = (
        converged
        if converged is not None
        else [copy.deepcopy(row) for row in rows if row["solver_status"] == "converged"]
    )
    complete = truth if truth is not None else copy.deepcopy(released)
    levels = dict(audit.CORE_SYSTEM_LOAD_LEVELS)
    return audit.build_audit(
        attempts=rows,
        converged_core=released,
        complete_truth=complete,
        simulation_report={
            "status": "pass",
            "config": {"system_load_levels": levels},
        },
        case_registry=registry(),
        system_load_levels=levels,
        pglib_root=tmp_path,
        input_paths=input_paths(tmp_path),
    )


def test_exact_core4_identity_sets_pass(tmp_path: Path, monkeypatch) -> None:
    report = build(tmp_path, monkeypatch)

    assert report["status"] == "pass"
    assert report["scope"]["registered_system_load_strata"] == 28
    assert report["denominator"]["expected_attempt_count"] == 28
    assert report["denominator"]["observed_attempt_count"] == 28
    assert report["set_audit"]["missing_identity_count"] == 0
    assert report["set_audit"]["extra_identity_count"] == 0


def test_same_count_with_replaced_identity_fails(tmp_path: Path, monkeypatch) -> None:
    rows = [
        attempt(system, load_level=load_level)
        for system, levels in audit.CORE_SYSTEM_LOAD_LEVELS.items()
        for load_level in levels
    ]
    rows[0]["line_indices"] = [11]
    rows[0]["affected_components"]["branches"] = ["0-1"]
    report = build(tmp_path, monkeypatch, attempts=rows)

    assert report["status"] == "fail"
    assert report["set_audit"]["missing_identity_count"] == 1
    assert report["set_audit"]["extra_identity_count"] == 1
    assert (
        report["hard_gates"]["expected_and_attempt_identity_sets_match"] is False
    )


def test_missing_denominator_flag_fails(tmp_path: Path, monkeypatch) -> None:
    rows = [
        attempt(system, load_level=load_level)
        for system, levels in audit.CORE_SYSTEM_LOAD_LEVELS.items()
        for load_level in levels
    ]
    del rows[0]["counts_in_security_denominator"]
    report = build(tmp_path, monkeypatch, attempts=rows)

    assert report["status"] == "fail"
    assert report["hard_gates"]["attempt_rows_are_contract_complete"] is False
    assert any(
        "missing_security_denominator_flag" in error
        for error in report["diagnostics"]["row_contract_errors"]
    )


def test_failed_attempt_is_retained_but_not_released(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rows = [
        attempt(system, load_level=load_level)
        for system, levels in audit.CORE_SYSTEM_LOAD_LEVELS.items()
        for load_level in levels
    ]
    rows[0] = attempt(
        "ieee14",
        load_level=audit.CORE_SYSTEM_LOAD_LEVELS["ieee14"][0],
        status="unsupplied_island",
    )
    released = [copy.deepcopy(row) for row in rows[1:]]
    report = build(
        tmp_path,
        monkeypatch,
        attempts=rows,
        converged=released,
        truth=copy.deepcopy(released),
    )

    assert report["status"] == "pass"
    assert report["outcomes"]["failed_attempt_count"] == 1
    assert report["outcomes"]["converged_attempt_count"] == 27


def test_truth_leakage_of_failed_attempt_fails(tmp_path: Path, monkeypatch) -> None:
    rows = [
        attempt(system, load_level=load_level)
        for system, levels in audit.CORE_SYSTEM_LOAD_LEVELS.items()
        for load_level in levels
    ]
    rows[0] = attempt(
        "ieee14",
        load_level=audit.CORE_SYSTEM_LOAD_LEVELS["ieee14"][0],
        status="unsupplied_island",
    )
    released = [copy.deepcopy(row) for row in rows[1:]]
    truth = copy.deepcopy(released) + [copy.deepcopy(rows[0])]
    report = build(
        tmp_path,
        monkeypatch,
        attempts=rows,
        converged=released,
        truth=truth,
    )

    assert report["status"] == "fail"
    assert (
        report["hard_gates"]["failed_attempts_are_excluded_from_released_truth"]
        is False
    )


def test_report_is_json_serializable(tmp_path: Path, monkeypatch) -> None:
    report = build(tmp_path, monkeypatch)
    json.dumps(report)
