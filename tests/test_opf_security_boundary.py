from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from append_opf_closed_loop_auxiliary_records import (  # noqa: E402
    bounded_window,
    build_executable_control_target,
    contingency_control_feasibility,
    contingency_window,
    emergency_loading_limit,
    fractional_interior_interval,
    interior_interval,
    metrics,
    physically_bound_action_text,
    post_action_n1_diagnostic_summary,
    published_result_errors,
    replay_optimized_dispatch_power_flow,
    replay_executable_control_target,
    stratified_order,
    validate_executable_control_target,
)
from validate_cross_solver_power_flow import (  # noqa: E402
    native_element_maps,
    recomputed_post_metrics,
)


def net_with_voltage(vm_pu: float) -> SimpleNamespace:
    return SimpleNamespace(
        res_bus=pd.DataFrame({"vm_pu": [vm_pu]}),
        res_line=pd.DataFrame({"loading_percent": [100.0], "pl_mw": [0.0]}),
        res_trafo=pd.DataFrame({"loading_percent": [100.0], "pl_mw": [0.0]}),
        res_trafo3w=pd.DataFrame({"pl_mw": []}),
        res_ext_grid=pd.DataFrame({"p_mw": [10.0]}),
        res_gen=pd.DataFrame({"p_mw": []}),
        res_sgen=pd.DataFrame({"p_mw": []}),
        res_load=pd.DataFrame({"p_mw": [10.0]}),
        res_shunt=pd.DataFrame({"p_mw": []}),
        res_ward=pd.DataFrame({"p_mw": []}),
        res_xward=pd.DataFrame({"p_mw": []}),
        res_storage=pd.DataFrame({"p_mw": []}),
    )


def test_voltage_roundoff_inside_boundary_epsilon_is_secure() -> None:
    net = net_with_voltage(0.95 - 5e-9)

    generated = metrics(net)
    replayed = recomputed_post_metrics(net)

    assert generated["voltage_violation_count"] == 0
    assert replayed["voltage_violation_count"] == 0
    assert generated["constraint_violation_score"] == 0.0
    assert replayed["constraint_violation_score"] == 0.0


def test_voltage_violation_beyond_boundary_epsilon_is_retained() -> None:
    net = net_with_voltage(0.95 - 2e-6)

    generated = metrics(net)
    replayed = recomputed_post_metrics(net)

    assert generated["voltage_violation_count"] == 1
    assert replayed["voltage_violation_count"] == 1
    assert generated["constraint_violation_score"] > 0.0
    assert replayed["constraint_violation_score"] > 0.0


def test_native_branch_mapping_accounts_for_impedance_table_rows() -> None:
    net = SimpleNamespace(
        bus=pd.DataFrame({"name": [1, 2, 3]}, index=[0, 1, 2]),
        line=pd.DataFrame(
            {"from_bus": [0], "to_bus": [1]},
            index=[0],
        ),
        trafo=pd.DataFrame(
            {"hv_bus": [1], "lv_bus": [2]},
            index=[0],
        ),
        impedance=pd.DataFrame(
            {"from_bus": [0], "to_bus": [2]},
            index=[0],
        ),
        ext_grid=pd.DataFrame({"bus": [0]}, index=[0]),
        gen=pd.DataFrame({"bus": [1]}, index=[0]),
    )
    ppc = {
        "branch": np.array(
            [
                [1.0, 2.0],
                [2.0, 3.0],
                [1.0, 3.0],
            ]
        ),
        "gen": np.array([[1.0], [2.0]]),
    }

    branch_map, generator_map = native_element_maps(net, ppc)

    assert set(branch_map) == {
        ("line", 0),
        ("trafo", 0),
        ("impedance", 0),
    }
    assert set(branch_map.values()) == {0, 1, 2}
    assert set(generator_map) == {("ext_grid", 0), ("gen", 0)}


def test_native_fixed_zero_active_power_interval_is_preserved() -> None:
    assert bounded_window(0.0, 10.0, 0.0, 0.0) == (0.0, 0.0)
    assert contingency_window(0.0, 25.0, 0.0, 0.0) == (0.0, 0.0)


def test_corrective_loading_guard_stays_inside_native_and_dataset_limits() -> None:
    assert emergency_loading_limit(None) == 80.0
    assert emergency_loading_limit(120.0) == 80.0
    assert emergency_loading_limit(75.0) == 75.0


def test_robust_voltage_interval_retains_more_than_one_percent_validation_margin() -> None:
    from append_opf_closed_loop_auxiliary_records import (  # noqa: PLC0415
        ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU,
        ROBUST_OPTIMIZATION_VOLTAGE_BOUNDS_PU,
    )

    assert ROBUST_OPTIMIZATION_VOLTAGE_BOUNDS_PU == (0.96, 1.04)
    assert ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU == 0.001


def safe_published_result(uncertainty_voltage_margin: float) -> dict:
    inactive_state = {
        "bus": [{"index": 0, "in_service": False}],
        "line": [{"index": 0, "in_service": False}],
        "trafo": [{"index": 0, "in_service": False}],
    }
    return {
        "post_action": {
            "max_line_loading_percent": 0.0,
            "max_transformer_loading_percent": 0.0,
            "min_bus_voltage_pu": 1.0,
            "max_bus_voltage_pu": 1.0,
            "constraint_violation_score": 0.0,
            "power_balance_residual_mw": 0.0,
            "overloaded_branch_count": 0,
            "overloaded_transformer_count": 0,
            "voltage_violation_count": 0,
        },
        "pre_action_state": inactive_state,
        "post_action_state": inactive_state,
        "post_action_controls": {},
        "constraint_policy": {"control_bounds": []},
        "reserve_summary": {
            "upward_reserve_adequate": True,
            "external_balance_reserve_adequate": True,
        },
        "post_action_n1": {
            "requested_checks": 7,
            "completed_checks": 7,
            "secure_checks": 7,
        },
        "active_load_uncertainty_construction_gate": {
            "status": "pass",
            "registered_case_count": 48,
            "safe_case_count": 48,
            "denominator_complete": True,
            "minimum_worst_voltage_margin_pu": uncertainty_voltage_margin,
            "minimum_thermal_margin_percent": 10.0,
        },
        "constant_power_factor_uncertainty_construction_gate": {
            "status": "pass",
            "registered_case_count": 48,
            "safe_case_count": 48,
            "denominator_complete": True,
            "minimum_worst_voltage_margin_pu": uncertainty_voltage_margin,
            "minimum_thermal_margin_percent": 10.0,
        },
        "load_uncertainty_construction_gate": {
            "status": "pass",
            "load_model_count": 2,
            "registered_case_count": 96,
            "safe_case_count": 96,
            "denominator_complete": True,
        },
    }


def test_published_result_rejects_thin_uncertainty_voltage_margin() -> None:
    assert (
        "active_load_uncertainty_voltage_margin_below_minimum"
        in published_result_errors(safe_published_result(0.0005), 1e-3)
    )
    assert (
        "constant_power_factor_uncertainty_voltage_margin_below_minimum"
        in published_result_errors(safe_published_result(0.0005), 1e-3)
    )
    assert published_result_errors(safe_published_result(0.001), 1e-3) == []


def test_embedded_uncertainty_summary_enforces_published_margin() -> None:
    from append_opf_closed_loop_auxiliary_records import (  # noqa: PLC0415
        summarize_embedded_uncertainty_cases,
    )

    cases = [
        {
            "safe": True,
            "solver_status": "converged",
            "case_type": "base_stress",
            "stress_fraction": stress,
            "metrics": {
                "worst_voltage_margin_pu": 0.0005,
                "thermal_margin_percent": 10.0,
                "control_capability": {"status": "pass"},
                "registered_network_limit_violations": [],
                "incremental_active_balance_residual_mw": 0.0,
            },
        }
        for stress in (-0.10, -0.05, -0.02, 0.02, 0.05, 0.10)
    ]
    summary = summarize_embedded_uncertainty_cases(
        cases,
        {"post_action_n1": {"requested_checks": 0}},
        contract_version="test",
        reactive_load_policy="test",
        balance_tolerance_mw=1e-3,
    )

    assert summary["denominator_complete"] is True
    assert summary["safe_case_count"] == 6
    assert summary["status"] == "needs_optimization"


def test_active_balance_reserve_requires_a_nonempty_interior_interval() -> None:
    assert interior_interval((-100.0, 100.0), 12.0, label="slack") == (
        -88.0,
        88.0,
    )
    try:
        interior_interval((-10.0, 10.0), 10.0, label="slack")
    except ValueError as exc:
        assert "cannot reserve" in str(exc)
    else:
        raise AssertionError("empty robust interval must fail")


def test_reactive_capability_reserve_uses_a_two_sided_native_interior() -> None:
    assert fractional_interior_interval(
        (0.0, 10.0),
        0.46,
        label="slack_q",
    ) == pytest.approx((4.6, 5.4))
    assert fractional_interior_interval(
        (2.0, 2.0),
        0.46,
        label="fixed_q",
    ) == (2.0, 2.0)
    with pytest.raises(ValueError, match="reserve fraction"):
        fractional_interior_interval((0.0, 10.0), 0.5, label="slack_q")


def test_n1_failure_ledger_keeps_aggregate_denominator_without_case_payloads() -> None:
    summary = post_action_n1_diagnostic_summary(
        {
            "network_requested_checks": 5,
            "generator_requested_checks": 2,
            "requested_checks": 7,
            "completed_checks": 7,
            "secure_checks": 6,
            "structurally_excluded_network_candidates": [{"index": 9}],
            "checks": [
                {
                    "solver_status": "converged",
                    "control_feasibility": {"status": "pass"},
                    "metrics": {"large": "payload"},
                },
                {"solver_status": "failed", "error": "individual failure"},
            ],
        }
    )

    assert summary["requested_checks"] == 7
    assert summary["secure_checks"] == 6
    assert summary["solver_status_counts"] == {"converged": 1, "failed": 1}
    assert "checks" not in summary


def test_generator_contingency_control_marks_outaged_device_explicitly(
    monkeypatch,
) -> None:
    net = SimpleNamespace(
        ext_grid=pd.DataFrame({"in_service": [True]}, index=[0]),
        gen=pd.DataFrame({"in_service": [True, False]}, index=[0, 1]),
    )
    controls = {
        "ext_grid": [{"index": 0, "p_mw": 10.0, "q_mvar": 1.0, "vm_pu": 1.0}],
        "gen": [
            {"index": 0, "p_mw": 5.0, "q_mvar": 0.5, "vm_pu": 1.01},
            {"index": 1, "p_mw": 0.0, "q_mvar": 0.0, "vm_pu": 1.02},
        ],
    }
    monkeypatch.setattr(
        "append_opf_closed_loop_auxiliary_records.control_vector",
        lambda _net: controls,
    )
    policy = {
        "control_bounds": [
            {
                "element": "ext_grid",
                "index": 0,
                "contingency_p_bounds_mw": [-100.0, 100.0],
                "contingency_q_bounds_mvar": [-100.0, 100.0],
            },
            {
                "element": "gen",
                "index": 0,
                "contingency_p_bounds_mw": [0.0, 100.0],
                "contingency_q_bounds_mvar": [-100.0, 100.0],
            },
            {
                "element": "gen",
                "index": 1,
                "contingency_p_bounds_mw": [0.0, 100.0],
                "contingency_q_bounds_mvar": [-100.0, 100.0],
            },
        ]
    }

    result = contingency_control_feasibility(
        net,
        policy,
        outaged_generator=1,
    )

    assert result["status"] == "pass"
    assert result["post_contingency_controls"]["gen"][1]["in_service"] is False
    assert "in_service" not in result["post_contingency_controls"]["gen"][0]


def test_contingency_control_fails_when_external_balance_reserve_is_missing(
    monkeypatch,
) -> None:
    net = SimpleNamespace(
        ext_grid=pd.DataFrame({"in_service": [True]}, index=[0]),
        gen=pd.DataFrame({"in_service": [True]}, index=[0]),
    )
    monkeypatch.setattr(
        "append_opf_closed_loop_auxiliary_records.control_vector",
        lambda _net: {
            "ext_grid": [
                {"index": 0, "p_mw": 90.0, "q_mvar": 0.0, "vm_pu": 1.0}
            ],
            "gen": [
                {"index": 0, "p_mw": 5.0, "q_mvar": 0.0, "vm_pu": 1.0}
            ],
        },
    )
    policy = {
        "required_external_balance_reserve_mw": 25.0,
        "control_bounds": [
            {
                "element": "ext_grid",
                "index": 0,
                "contingency_p_bounds_mw": [-100.0, 100.0],
                "contingency_q_bounds_mvar": [-100.0, 100.0],
            },
            {
                "element": "gen",
                "index": 0,
                "contingency_p_bounds_mw": [0.0, 100.0],
                "contingency_q_bounds_mvar": [-100.0, 100.0],
            },
        ],
    }

    result = contingency_control_feasibility(net, policy)

    assert result["status"] == "fail"
    assert result["external_balance_reserve"] == {
        "required_each_direction_mw": 25.0,
        "available_upward_mw": 10.0,
        "available_downward_mw": 190.0,
        "adequate": False,
    }
    assert (
        "ext_grid_upward_active_load_reserve_insufficient"
        in result["violations"]
    )


def test_stratified_screening_preregisters_low_load_first_within_each_bucket() -> None:
    rows = [
        {
            "scenario_id": "ieee118_n1_load100",
            "system": "ieee118",
            "contingency_type": "n1_branch_outage",
            "severity_level": "alert",
            "load_level": 1.0,
        },
        {
            "scenario_id": "ieee118_n1_load70_b",
            "system": "ieee118",
            "contingency_type": "n1_branch_outage",
            "severity_level": "alert",
            "load_level": 0.7,
            "generator_factor": 1.05,
        },
        {
            "scenario_id": "ieee118_n1_load70_a",
            "system": "ieee118",
            "contingency_type": "n1_branch_outage",
            "severity_level": "alert",
            "load_level": 0.7,
            "generator_factor": 1.0,
        },
    ]

    ordered = stratified_order(rows)

    assert [row["scenario_id"] for row in ordered] == [
        "ieee118_n1_load70_a",
        "ieee118_n1_load70_b",
        "ieee118_n1_load100",
    ]


def test_stratified_screening_prioritizes_registered_security_pool() -> None:
    rows = [
        {
            "scenario_id": "general_low_load",
            "system": "ieee14",
            "contingency_type": "n2_branch_outage",
            "severity_level": "alert",
            "load_level": 0.70,
            "_opf_screening_priority": 1,
        },
        {
            "scenario_id": "registered_security_candidate",
            "system": "ieee14",
            "contingency_type": "n2_branch_outage",
            "severity_level": "alert",
            "load_level": 0.80,
            "_opf_screening_priority": 0,
        },
    ]

    assert [row["scenario_id"] for row in stratified_order(rows)] == [
        "registered_security_candidate",
        "general_low_load",
    ]


def test_tight_replay_pins_optimized_active_power_and_voltage(monkeypatch) -> None:
    net = SimpleNamespace(
        gen=pd.DataFrame(
            {
                "bus": [1],
                "p_mw": [1.0],
                "vm_pu": [1.0],
                "in_service": [True],
            }
        ),
        ext_grid=pd.DataFrame({"bus": [0], "vm_pu": [1.0], "in_service": [True]}),
        res_gen=pd.DataFrame({"p_mw": [12.5]}),
        res_bus=pd.DataFrame({"vm_pu": [1.02, 1.03]}),
    )
    calls = []

    def fake_runpp(candidate, **kwargs):
        calls.append((candidate, kwargs))

    monkeypatch.setattr(
        "append_opf_closed_loop_auxiliary_records.pp.runpp",
        fake_runpp,
    )

    replay_optimized_dispatch_power_flow(net)

    assert net.gen.at[0, "p_mw"] == 12.5
    assert net.gen.at[0, "vm_pu"] == 1.03
    assert net.ext_grid.at[0, "vm_pu"] == 1.02
    assert calls[0][1]["tolerance_mva"] == 1e-10
    assert calls[0][1]["init"] == "results"


def test_metrics_include_impedance_active_power_losses() -> None:
    net = net_with_voltage(1.0)
    net.res_impedance = pd.DataFrame({"pl_mw": [0.125]})
    net.res_ext_grid.at[0, "p_mw"] = 10.125

    generated = metrics(net)
    replayed = recomputed_post_metrics(net)

    assert generated["network_losses_mw"] == 0.125
    assert generated["power_balance_residual_mw"] == 0.0
    assert replayed["network_losses_mw"] == 0.125
    assert replayed["power_balance_residual_mw"] == 0.0


def complete_control_result() -> dict:
    pre_gen = [
        {"index": idx, "p_mw": 20.0 + idx, "q_mvar": 2.0 + idx, "vm_pu": 1.0}
        for idx in range(6)
    ]
    post_gen = [
        {
            "index": idx,
            "p_mw": 20.5 + idx,
            "q_mvar": 1.75 + idx,
            "vm_pu": round(1.001 + idx * 0.0001, 8),
        }
        for idx in range(6)
    ]
    pre_ext_grid = [{"index": 0, "p_mw": 50.0, "q_mvar": 5.0, "vm_pu": 1.0}]
    post_ext_grid = [{"index": 0, "p_mw": 47.0, "q_mvar": 6.5, "vm_pu": 1.002}]
    deltas = []
    for element, before_rows, after_rows in (
        ("ext_grid", pre_ext_grid, post_ext_grid),
        ("gen", pre_gen, post_gen),
    ):
        before = {row["index"]: row for row in before_rows}
        for after in after_rows:
            old = before[after["index"]]
            deltas.append(
                {
                    "element": element,
                    "index": after["index"],
                    "delta_p_mw": round(after["p_mw"] - old["p_mw"], 6),
                    "delta_q_mvar": round(after["q_mvar"] - old["q_mvar"], 6),
                    "delta_vm_pu": round(after["vm_pu"] - old["vm_pu"], 6),
                }
            )
    return {
        "scenario_id": "opf-complete-controls",
        "pre_action_controls": {"ext_grid": pre_ext_grid, "gen": pre_gen},
        "post_action_controls": {"ext_grid": post_ext_grid, "gen": post_gen},
        "control_delta": {"elements": deltas},
    }


def test_executable_target_preserves_ext_grid_and_more_than_four_generators() -> None:
    result = complete_control_result()

    target = build_executable_control_target(result)

    assert target["completeness"] == {
        "expected_control_count": 7,
        "ext_grid_count": 1,
        "generator_count": 6,
        "all_controls_required": True,
    }
    assert {item["device_id"] for item in target["controls"]} == {
        "ext_grid:0",
        *(f"gen:{idx}" for idx in range(6)),
    }
    ext_grid_target = next(
        item for item in target["controls"] if item["element"] == "ext_grid"
    )
    generator_target = next(
        item for item in target["controls"] if item["element"] == "gen"
    )
    assert ext_grid_target["commanded_setpoint"]["fields"] == ["vm_pu"]
    assert ext_grid_target["realized_response"]["fields"] == ["p_mw", "q_mvar"]
    assert generator_target["commanded_setpoint"]["fields"] == ["p_mw", "vm_pu"]
    assert generator_target["realized_response"]["fields"] == ["q_mvar"]
    assert validate_executable_control_target(target, result) == []
    expected_commands = {
        "ext_grid": [
            {"index": row["index"], "vm_pu": row["vm_pu"]}
            for row in result["post_action_controls"]["ext_grid"]
        ],
        "gen": [
            {"index": row["index"], "p_mw": row["p_mw"], "vm_pu": row["vm_pu"]}
            for row in result["post_action_controls"]["gen"]
        ],
    }
    assert replay_executable_control_target(
        target, mode="absolute_setpoints"
    ) == expected_commands
    assert replay_executable_control_target(
        target, mode="pre_action_plus_delta"
    ) == expected_commands
    summary, _, _ = physically_bound_action_text(result, variant=0)
    assert summary.count("机组") == 4
    assert len(target["controls"]) == 7


def test_executable_target_rejects_tampered_reactive_power_delta() -> None:
    result = complete_control_result()
    target = build_executable_control_target(result)
    target["controls"][0]["realized_response"]["delta"]["q_mvar"] += 0.5

    errors = validate_executable_control_target(target, result)

    assert any(
        "target_response_delta_closure_mismatch:ext_grid:0:q_mvar" == error
        for error in errors
    )
