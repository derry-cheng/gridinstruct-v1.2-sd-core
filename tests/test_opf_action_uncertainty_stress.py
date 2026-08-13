from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pandapower as pp
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_opf_action_uncertainty_stress as stress_module  # noqa: E402
from validate_opf_action_uncertainty_stress import (  # noqa: E402
    PREREGISTERED_ACTIVE_LOAD_STRESS,
    apply_active_load_stress,
    build_report,
    completion_exit_code,
    execute_case,
    output_commit_status,
    preregistered_case_specs,
    registered_network_limit_violations,
    validate_opf_commit,
    validate_output_locations,
    validate_result_contract,
)


def source(case_sha256: str = "case-sha") -> dict[str, str]:
    return {
        "repository_commit": "source-commit",
        "case_file": "case.m",
        "case_sha256": case_sha256,
    }


def n1_security(
    *identities: tuple[str, int],
    controls: dict,
) -> dict:
    checks = []
    for element, index in identities:
        corrective = copy.deepcopy(controls)
        if element == "gen":
            corrective["gen"] = [
                row for row in corrective["gen"] if int(row["index"]) != index
            ]
        checks.append(
            {
                "element": element,
                "index": index,
                "secure": True,
                "solver_status": "converged",
                "control_feasibility": {
                    "status": "pass",
                    "post_contingency_controls": corrective,
                },
            }
        )
    return {
        "requested_checks": len(checks),
        "completed_checks": len(checks),
        "secure_checks": len(checks),
        "checks": checks,
    }


def minimal_result(
    scenario_id: str = "scenario_a",
    system: str = "ieee14",
) -> dict:
    pre_ext = [{"index": 0, "p_mw": 8.0, "q_mvar": 1.0, "vm_pu": 1.0}]
    pre_gen = [
        {"index": 0, "p_mw": 2.0, "q_mvar": 0.5, "vm_pu": 1.01},
        {"index": 1, "p_mw": 0.0, "q_mvar": 0.2, "vm_pu": 1.02},
    ]
    post_ext = [{"index": 0, "p_mw": 7.0, "q_mvar": 1.5, "vm_pu": 1.01}]
    post_gen = [
        {"index": 0, "p_mw": 3.0, "q_mvar": 0.4, "vm_pu": 1.02},
        {"index": 1, "p_mw": 0.0, "q_mvar": 0.1, "vm_pu": 1.01},
    ]
    deltas = []
    for element, before, after in (
        ("ext_grid", pre_ext, post_ext),
        ("gen", pre_gen, post_gen),
    ):
        before_by_index = {row["index"]: row for row in before}
        for row in after:
            old = before_by_index[row["index"]]
            deltas.append(
                {
                    "element": element,
                    "index": row["index"],
                    "delta_p_mw": row["p_mw"] - old["p_mw"],
                    "delta_q_mvar": row["q_mvar"] - old["q_mvar"],
                    "delta_vm_pu": row["vm_pu"] - old["vm_pu"],
                }
            )
    bounds = []
    for element, rows in (("ext_grid", post_ext), ("gen", post_gen)):
        for row in rows:
            bounds.append(
                {
                    "element": element,
                    "index": row["index"],
                    "p_bounds_mw": [-100.0, 100.0],
                    "q_bounds_mvar": [-100.0, 100.0],
                    "contingency_p_bounds_mw": [-100.0, 100.0],
                    "contingency_q_bounds_mvar": [-100.0, 100.0],
                }
            )
    return {
        "scenario_id": scenario_id,
        "system": system,
        "opf_status": "converged",
        "physical_source": source(),
        "pre_action_controls": {"ext_grid": pre_ext, "gen": pre_gen},
        "post_action_controls": {"ext_grid": post_ext, "gen": post_gen},
        "control_delta": {"elements": deltas},
        "constraint_policy": {
            "control_bounds": bounds,
            "dataset_voltage_bounds_pu": [0.95, 1.05],
        },
        "post_action_n1": n1_security(
            ("line", 0),
            controls={"ext_grid": post_ext, "gen": post_gen},
        ),
        "selected_loading_margin_percent": 90.0,
    }


def test_preregistered_grid_and_denominator_are_deterministic() -> None:
    first = minimal_result("scenario_b", "ieee118")
    first["post_action_n1"] = n1_security(
        ("line", 2),
        ("gen", 0),
        controls=first["post_action_controls"],
    )
    second = minimal_result("scenario_a", "ieee14")

    forward = preregistered_case_specs([first, second])
    reverse = preregistered_case_specs([second, first])

    assert forward == reverse
    assert len(forward) == 30
    assert len({row["case_id"] for row in forward}) == 30
    assert {row["stress_fraction"] for row in forward} == set(
        PREREGISTERED_ACTIVE_LOAD_STRESS
    )
    assert sum(row["case_type"] == "base_stress" for row in forward) == 12
    assert sum(row["case_type"] == "registered_n1_stress" for row in forward) == 18


def test_report_refuses_an_incomplete_registered_denominator() -> None:
    result = minimal_result()
    specs = preregistered_case_specs([result])
    cases = [
        {
            **spec,
            "stress_percent": 100.0 * spec["stress_fraction"],
            "solver_status": "converged",
            "safe": True,
            "metrics": {
                "worst_voltage_margin_pu": 0.01,
                "thermal_margin_percent": 10.0,
                "registered_network_limit_violations": [],
            },
            "external_balance": {
                "total_active_balance_adjustment_mw": 1.0,
                "incremental_active_balance_residual_mw": 0.0,
            },
        }
        for spec in specs
    ]
    validation = [{"scenario_id": result["scenario_id"], "status": "pass"}]

    report = build_report(
        [result],
        cases,
        validation,
        {},
        balance_tolerance_mw=1e-3,
    )

    assert report["status"] == "pass"
    assert report["registered_denominator"] == {
        "published_action_count": 1,
        "stress_point_count_per_action": 6,
        "expected_base_case_count": 6,
        "expected_registered_n1_case_count": 6,
        "expected_total_case_count": 12,
        "observed_total_case_count": 12,
        "complete": True,
    }
    thin_margin_cases = copy.deepcopy(cases)
    for row in thin_margin_cases:
        row["metrics"]["worst_voltage_margin_pu"] = 0.0005
    thin_margin_report = build_report(
        [result],
        thin_margin_cases,
        validation,
        {},
        balance_tolerance_mw=1e-3,
    )
    assert thin_margin_report["status"] == "diagnostic"
    assert thin_margin_report["hard_gates"][
        "worst_voltage_margin_meets_published_minimum"
    ] is False
    diagnostic_cases = [dict(row) for row in cases]
    diagnostic_cases[0] = {
        **diagnostic_cases[0],
        "safe": False,
        "metrics": {
            "worst_voltage_margin_pu": -0.01,
            "thermal_margin_percent": 10.0,
            "electrical_state_complete": True,
            "power_balance_residual_mw": 0.0,
            "control_capability": {"status": "pass"},
        },
    }
    diagnostic_report = build_report(
        [result],
        diagnostic_cases,
        validation,
        {},
        balance_tolerance_mw=1e-3,
    )
    assert diagnostic_report["status"] == "diagnostic"
    assert diagnostic_report["integrity_status"] == "pass"
    assert diagnostic_report["robustness_status"] == "needs_optimization"
    assert diagnostic_report["optimization_diagnostics"]["reason_counts"] == {
        "voltage_margin_negative": 1
    }
    with pytest.raises(RuntimeError, match="denominator is incomplete"):
        build_report(
            [result],
            cases[:-1],
            validation,
            {},
            balance_tolerance_mw=1e-3,
        )


def test_commit_hash_or_path_mismatch_fails_closed(tmp_path: Path) -> None:
    results_path = tmp_path / "results.json"
    results_path.write_text('{"results": []}\\n', encoding="utf-8")
    result_sha256 = hashlib.sha256(results_path.read_bytes()).hexdigest()
    commit_path = tmp_path / "commit.json"
    commit_path.write_text(
        json.dumps(
            {
                "status": "pass",
                "schema_version": "1.0",
                "artifacts": {
                    "results": {
                        "path": str(results_path),
                        "sha256": result_sha256,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert validate_opf_commit(results_path, commit_path)["status"] == "pass"

    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    commit["artifacts"]["results"]["sha256"] = "wrong"
    commit_path.write_text(json.dumps(commit), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256"):
        validate_opf_commit(results_path, commit_path)

    commit["artifacts"]["results"]["sha256"] = result_sha256
    commit["artifacts"]["results"]["path"] = str(tmp_path / "other.json")
    commit_path.write_text(json.dumps(commit), encoding="utf-8")
    with pytest.raises(ValueError, match="path mismatch"):
        validate_opf_commit(results_path, commit_path)

    with pytest.raises(ValueError, match="overlap inputs"):
        validate_output_locations(
            [results_path, commit_path],
            [results_path, tmp_path / "report.json", tmp_path / "output-commit.json"],
        )


def test_complete_control_vector_and_source_binding_are_hard_gates() -> None:
    net = SimpleNamespace(
        ext_grid=pd.DataFrame({"in_service": [True]}, index=[0]),
        gen=pd.DataFrame({"in_service": [True, True]}, index=[0, 1]),
        line=pd.DataFrame({"in_service": [True]}, index=[0]),
        trafo=pd.DataFrame(index=[]),
    )
    result = minimal_result()
    scenario = {
        "scenario_id": result["scenario_id"],
        "physical_source": source(),
    }

    assert validate_result_contract(result, scenario, net)["control_count"] == 3

    incomplete = minimal_result()
    incomplete["post_action_controls"]["gen"] = incomplete["post_action_controls"][
        "gen"
    ][:1]
    with pytest.raises(ValueError, match="control vector is incomplete"):
        validate_result_contract(incomplete, scenario, net)

    mismatched_source = dict(scenario)
    mismatched_source["physical_source"] = source("different-case-sha")
    with pytest.raises(ValueError, match="physical_source mismatch"):
        validate_result_contract(result, mismatched_source, net)

    missing_recourse = minimal_result()
    del missing_recourse["post_action_n1"]["checks"][0]["control_feasibility"][
        "post_contingency_controls"
    ]
    with pytest.raises(ValueError, match="no stored N-1 corrective controls"):
        validate_result_contract(missing_recourse, scenario, net)

    incomplete_recourse = minimal_result()
    incomplete_recourse["post_action_n1"]["checks"][0]["control_feasibility"][
        "post_contingency_controls"
    ]["gen"] = incomplete_recourse["post_action_controls"]["gen"][:1]
    with pytest.raises(ValueError, match="corrective control vector is incomplete"):
        validate_result_contract(incomplete_recourse, scenario, net)


def test_n1_stress_applies_stored_recourse_before_load_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = minimal_result()
    recourse = result["post_action_n1"]["checks"][0]["control_feasibility"][
        "post_contingency_controls"
    ]
    recourse["gen"][0]["p_mw"] = 9.0
    net = SimpleNamespace(
        ext_grid=pd.DataFrame(
            {"in_service": [True], "vm_pu": [1.0]},
            index=[0],
        ),
        gen=pd.DataFrame(
            {
                "in_service": [True, True],
                "p_mw": [3.0, 0.0],
                "vm_pu": [1.02, 1.01],
            },
            index=[0, 1],
        ),
        line=pd.DataFrame({"in_service": [True]}, index=[0]),
        trafo=pd.DataFrame(index=[]),
        load=pd.DataFrame({"p_mw": [10.0], "q_mvar": [2.0], "in_service": [True]}),
    )
    observed = {}
    monkeypatch.setattr(stress_module, "unsupplied_buses", lambda _: set())

    def fake_run(current_net) -> None:
        observed["generator_p_mw"] = float(current_net.gen.at[0, "p_mw"])

    def fake_metrics(*args, **kwargs) -> dict:
        return {
            "safe": True,
            "max_branch_loading_percent": 0.0,
            "network_losses_mw": 0.0,
        }

    def fake_balance(
        current_net,
        controls,
        load_stress,
        *,
        reference_losses_mw,
        realized_losses_mw,
    ) -> dict:
        observed["reference_generator_p_mw"] = float(controls["gen"][0]["p_mw"])
        return {
            "total_active_balance_adjustment_mw": 0.0,
            "incremental_active_balance_residual_mw": 0.0,
        }

    monkeypatch.setattr(stress_module, "run_power_flow", fake_run)
    monkeypatch.setattr(stress_module, "case_metrics", fake_metrics)
    monkeypatch.setattr(stress_module, "external_balance", fake_balance)
    spec = {
        "case_id": "recourse",
        "scenario_id": result["scenario_id"],
        "system": result["system"],
        "stress_fraction": 0.02,
        "case_type": "registered_n1_stress",
        "contingency": {"element": "line", "index": 0},
    }

    row = execute_case(net, result, spec, balance_tolerance_mw=1e-3)

    assert observed == {
        "generator_p_mw": 9.0,
        "reference_generator_p_mw": 9.0,
    }
    assert (
        row["control_replay_policy"]
        == "stored_bounded_corrective_post_contingency_controls"
    )


def test_incremental_active_balance_error_marks_case_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = minimal_result()
    net = SimpleNamespace(
        ext_grid=pd.DataFrame(
            {"in_service": [True], "vm_pu": [1.0]},
            index=[0],
        ),
        gen=pd.DataFrame(
            {
                "in_service": [True, True],
                "p_mw": [3.0, 0.0],
                "vm_pu": [1.02, 1.01],
            },
            index=[0, 1],
        ),
        line=pd.DataFrame({"in_service": [True]}, index=[0]),
        trafo=pd.DataFrame(index=[]),
        load=pd.DataFrame(
            {"p_mw": [10.0], "q_mvar": [2.0], "in_service": [True]}
        ),
    )
    monkeypatch.setattr(stress_module, "unsupplied_buses", lambda _: set())
    monkeypatch.setattr(stress_module, "run_power_flow", lambda _: None)
    monkeypatch.setattr(
        stress_module,
        "case_metrics",
        lambda *args, **kwargs: {
            "safe": True,
            "max_branch_loading_percent": 0.0,
            "network_losses_mw": 0.0,
        },
    )
    monkeypatch.setattr(
        stress_module,
        "external_balance",
        lambda *args, **kwargs: {
            "total_active_balance_adjustment_mw": 1.0,
            "incremental_active_balance_residual_mw": 0.01,
        },
    )
    spec = {
        "case_id": "bad_balance",
        "scenario_id": result["scenario_id"],
        "system": result["system"],
        "stress_fraction": 0.10,
        "case_type": "base_stress",
        "contingency": None,
    }

    row = execute_case(net, result, spec, balance_tolerance_mw=1e-3)

    assert row["solver_status"] == "converged"
    assert row["safe"] is False
    assert row["metrics"]["incremental_active_balance_residual_mw"] == 0.01


def test_registered_device_limits_use_device_specific_bounds() -> None:
    net = SimpleNamespace(
        bus=pd.DataFrame({"in_service": [True]}, index=[0]),
        res_bus=pd.DataFrame({"vm_pu": [1.041]}, index=[0]),
        line=pd.DataFrame({"in_service": [True]}, index=[0]),
        res_line=pd.DataFrame({"loading_percent": [96.0]}, index=[0]),
        trafo=pd.DataFrame(index=[]),
        res_trafo=pd.DataFrame(index=[]),
    )
    result = {
        "constraint_policy": {
            "dataset_voltage_bounds_pu": [0.95, 1.05],
            "network_limits": [
                {
                    "element": "bus",
                    "index": 0,
                    "validation": [0.97, 1.04],
                },
                {
                    "element": "line",
                    "index": 0,
                    "native_max_loading_percent": 95.0,
                },
            ],
        }
    }

    violations = registered_network_limit_violations(net, result)

    assert any(value.startswith("bus:0:vm_pu") for value in violations)
    assert any(value.startswith("line:0:loading_percent") for value in violations)


def test_diagnostic_commit_and_exit_are_fail_closed_by_default() -> None:
    diagnostic = {"robustness_status": "needs_optimization"}
    passed = {"robustness_status": "pass"}

    assert output_commit_status(diagnostic) == "diagnostic"
    assert completion_exit_code(diagnostic, allow_diagnostic=False) == 1
    assert completion_exit_code(diagnostic, allow_diagnostic=True) == 0
    assert output_commit_status(passed) == "pass"
    assert completion_exit_code(passed, allow_diagnostic=False) == 0


def test_active_load_stress_changes_only_active_power() -> None:
    net = SimpleNamespace(
        load=pd.DataFrame(
            {
                "p_mw": [10.0, 20.0, 100.0],
                "q_mvar": [3.0, 4.0, 50.0],
                "in_service": [True, True, False],
            }
        )
    )

    stress = apply_active_load_stress(net, 0.10)

    assert net.load.p_mw.tolist() == pytest.approx([11.0, 22.0, 100.0])
    assert net.load.q_mvar.tolist() == [3.0, 4.0, 50.0]
    assert stress["active_load_perturbation_mw"] == pytest.approx(3.0)
    assert stress["reactive_load_after_mvar"] == 7.0


def test_small_ac_fixture_replays_external_balance_and_registered_line_outage() -> None:
    net = pp.create_empty_network(sn_mva=100.0)
    buses = [
        pp.create_bus(net, vn_kv=110.0, min_vm_pu=0.95, max_vm_pu=1.05)
        for _ in range(3)
    ]
    pp.create_ext_grid(
        net,
        buses[0],
        vm_pu=1.0,
        min_p_mw=-100.0,
        max_p_mw=100.0,
        min_q_mvar=-100.0,
        max_q_mvar=100.0,
    )
    for from_bus, to_bus in (
        (buses[0], buses[1]),
        (buses[1], buses[2]),
        (buses[2], buses[0]),
    ):
        pp.create_line_from_parameters(
            net,
            from_bus,
            to_bus,
            length_km=1.0,
            r_ohm_per_km=0.05,
            x_ohm_per_km=0.20,
            c_nf_per_km=0.0,
            max_i_ka=1.0,
        )
    pp.create_load(net, buses[1], p_mw=10.0, q_mvar=2.0)
    pp.runpp(net, numba=False)
    ext_control = {
        "index": 0,
        "p_mw": float(net.res_ext_grid.at[0, "p_mw"]),
        "q_mvar": float(net.res_ext_grid.at[0, "q_mvar"]),
        "vm_pu": float(net.res_bus.at[buses[0], "vm_pu"]),
    }
    result = {
        "scenario_id": "three_bus",
        "system": "three_bus",
        "post_action_controls": {"ext_grid": [ext_control], "gen": []},
        "constraint_policy": {
            "dataset_voltage_bounds_pu": [0.95, 1.05],
            "control_bounds": [
                {
                    "element": "ext_grid",
                    "index": 0,
                    "contingency_p_bounds_mw": [-100.0, 100.0],
                    "contingency_q_bounds_mvar": [-100.0, 100.0],
                }
            ],
        },
        "selected_loading_margin_percent": 100.0,
    }
    result["post_action_n1"] = n1_security(
        ("line", 0),
        controls=result["post_action_controls"],
    )
    base_spec = {
        "case_id": "base",
        "scenario_id": "three_bus",
        "system": "three_bus",
        "stress_fraction": 0.10,
        "case_type": "base_stress",
        "contingency": None,
    }
    outage_spec = {
        **base_spec,
        "case_id": "line_outage",
        "case_type": "registered_n1_stress",
        "contingency": {"element": "line", "index": 0},
    }

    base = execute_case(net, result, base_spec, balance_tolerance_mw=1e-3)
    outage = execute_case(net, result, outage_spec, balance_tolerance_mw=1e-3)

    assert base["solver_status"] == "converged"
    assert outage["solver_status"] == "converged"
    assert base["load_stress"]["active_load_perturbation_mw"] == pytest.approx(1.0)
    assert base["load_stress"]["reactive_load_after_mvar"] == pytest.approx(2.0)
    assert (
        base["external_balance"]["total_active_balance_adjustment_mw"]
        > base["load_stress"]["active_load_perturbation_mw"]
    )
    assert base["metrics"]["power_balance_residual_mw"] <= 1e-3
    assert outage["metrics"]["power_balance_residual_mw"] <= 1e-3
