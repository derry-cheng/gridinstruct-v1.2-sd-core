from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandapower as pp


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_opf_action_uncertainty_stress as active_stress  # noqa: E402
from validate_opf_action_constant_power_factor_stress import (  # noqa: E402
    CONTRACT_VERSION,
    apply_constant_power_factor_stress,
    build_report,
    preregistered_case_specs,
)


def test_constant_power_factor_stress_scales_active_and_reactive_load() -> None:
    net = pp.create_empty_network()
    bus = pp.create_bus(net, vn_kv=110.0)
    pp.create_load(net, bus=bus, p_mw=10.0, q_mvar=4.0)
    pp.create_load(net, bus=bus, p_mw=5.0, q_mvar=1.0, in_service=False)

    evidence = apply_constant_power_factor_stress(net, 0.10)

    assert float(net.load.at[0, "p_mw"]) == 11.0
    assert float(net.load.at[0, "q_mvar"]) == 4.4
    assert float(net.load.at[1, "p_mw"]) == 5.0
    assert float(net.load.at[1, "q_mvar"]) == 1.0
    assert evidence["active_load_perturbation_mw"] == 1.0
    assert abs(evidence["reactive_load_perturbation_mvar"] - 0.4) < 1e-12
    assert abs(
        evidence["active_load_after_mw"] / evidence["reactive_load_after_mvar"]
        - evidence["active_load_before_mw"]
        / evidence["reactive_load_before_mvar"]
    ) < 1e-12


def test_constant_power_factor_case_ids_are_model_specific() -> None:
    results = [
        {
            "scenario_id": "scenario_a",
            "system": "ieee14",
            "post_action_n1": {
                "requested_checks": 1,
                "completed_checks": 1,
                "secure_checks": 1,
                "checks": [
                    {
                        "element": "line",
                        "index": 0,
                        "secure": True,
                        "solver_status": "converged",
                        "control_feasibility": {
                            "status": "pass",
                            "post_contingency_controls": {
                                "ext_grid": [],
                                "gen": [],
                            },
                        },
                    }
                ],
            },
        }
    ]
    active_specs = active_stress.preregistered_case_specs(results)
    constant_power_factor_specs = preregistered_case_specs(results)

    assert len(active_specs) == len(constant_power_factor_specs) == 12
    assert {
        row["case_id"] for row in active_specs
    }.isdisjoint(
        row["case_id"] for row in constant_power_factor_specs
    )
    assert all(
        row["case_id"].startswith("constant_power_factor::")
        for row in constant_power_factor_specs
    )


def test_constant_power_factor_report_enforces_complete_safe_denominator() -> None:
    result = {
        "scenario_id": "scenario_a",
        "system": "ieee14",
        "post_action_n1": {
            "requested_checks": 1,
            "completed_checks": 1,
            "secure_checks": 1,
            "checks": [
                {
                    "element": "line",
                    "index": 0,
                    "secure": True,
                    "solver_status": "converged",
                    "control_feasibility": {
                        "status": "pass",
                        "post_contingency_controls": {
                            "ext_grid": [],
                            "gen": [],
                        },
                    },
                }
            ],
        },
    }
    specs = active_stress.preregistered_case_specs([result])
    cases = [
        {
            **spec,
            "stress_percent": 100.0 * spec["stress_fraction"],
            "solver_status": "converged",
            "safe": True,
            "metrics": {
                "worst_voltage_margin_pu": 0.01,
                "thermal_margin_percent": 20.0,
                "registered_network_limit_violations": [],
            },
            "external_balance": {
                "total_active_balance_adjustment_mw": 1.0,
                "incremental_active_balance_residual_mw": 0.0,
            },
        }
        for spec in specs
    ]
    validation = [{"scenario_id": "scenario_a", "status": "pass"}]

    report = build_report(
        [result],
        cases,
        validation,
        {},
        balance_tolerance_mw=1e-3,
    )

    assert report["contract_version"] == CONTRACT_VERSION
    assert report["status"] == "pass"
    assert report["registered_denominator"]["expected_total_case_count"] == 12
    assert report["registered_denominator"]["complete"] is True

    thin_margin_cases = copy.deepcopy(cases)
    thin_margin_cases[0]["metrics"]["worst_voltage_margin_pu"] = 0.0005
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
