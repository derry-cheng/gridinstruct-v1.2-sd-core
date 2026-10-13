from __future__ import annotations

import json
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_equipment_rating_provenance as rating_audit  # noqa: E402
import audit_full_n1_enumeration as n1_audit  # noqa: E402
import audit_transformer_contract_all_networks as transformer_audit  # noqa: E402
import audit_explicit_high_rating_sensitivity as sensitivity  # noqa: E402
import build_external_matched_strata as matched  # noqa: E402
import validate_independent_solver_evidence as independent  # noqa: E402
from power_evidence_common import load_network, pglib_rate_a_semantics, rating_record  # noqa: E402


def test_ieee14_placeholder_ratings_are_not_accepted_as_safe() -> None:
    net = load_network("ieee14")
    line = rating_record(net, "ieee14", "line", int(net.line.index[0]))
    transformer = rating_record(net, "ieee14", "trafo", int(net.trafo.index[0]))
    assert line["rating_status"] == "unknown"
    assert "placeholder_apparent_power_sentinel" in line["reasons"]
    assert transformer["rating_status"] == "unknown"


def test_full_n1_denominator_includes_every_requested_element() -> None:
    net = load_network("ieee14")
    expected = len(net.line) + len(net.trafo) + len(net.gen)
    rows = n1_audit.contingencies(net)
    assert len(rows) == expected
    assert {element for element, _ in rows} == {"line", "trafo", "gen"}


def test_matched_strata_balances_network_and_severity(tmp_path: Path) -> None:
    rows = []
    metrics = {
        "normal-envelope": (50.0, 0.98, 1.02),
        "operational-stress": (110.0, 0.94, 1.06),
        "emergency-stress": (200.0, 0.85, 1.15),
        "extreme-stress": (400.0, 0.70, 1.30),
    }
    for network in matched.EXTERNAL_NETWORKS:
        for severity, (loading, minimum, maximum) in metrics.items():
            for index in range(2):
                rows.append(
                    {
                        "scenario_id": f"{network}:{severity}:{index}",
                        "network_model": network,
                        "solver_status": "converged",
                        "max_branch_loading_percent": loading,
                        "min_bus_voltage_pu": minimum,
                        "max_bus_voltage_pu": maximum,
                    }
                )
    source = tmp_path / "scenarios.json"
    source.write_text(json.dumps(rows), encoding="utf-8")
    report, selected = matched.build_report(
        rows,
        target_per_cell=2,
        seed=7,
        source_path=source,
    )
    assert report["status"] == "pass"
    assert report["matched_association"]["cramers_v"] == 0.0
    assert len(selected) == 7 * 4 * 2


def test_matched_strata_uses_observed_common_support_without_imputing_zeros(
    tmp_path: Path,
) -> None:
    rows = []

    def add(network: str, severity: str, count: int) -> None:
        loading, minimum, maximum = {
            "normal-envelope": (50.0, 0.98, 1.02),
            "operational-stress": (110.0, 0.94, 1.06),
            "emergency-stress": (200.0, 0.85, 1.15),
            "extreme-stress": (400.0, 0.70, 1.30),
        }[severity]
        for index in range(count):
            rows.append(
                {
                    "scenario_id": f"{network}:{severity}:{index}",
                    "network_model": network,
                    "solver_status": "converged",
                    "load_level": 1.0,
                    "max_branch_loading_percent": loading,
                    "min_bus_voltage_pu": minimum,
                    "max_bus_voltage_pu": maximum,
                }
            )

    (
        ieee300,
        illinois200,
        pegase89,
        pegase1354,
        rte1888,
        rte2848,
        pegase2869,
    ) = matched.EXTERNAL_NETWORKS
    for severity in (
        "normal-envelope",
        "operational-stress",
        "emergency-stress",
    ):
        add(illinois200, severity, 2)
        add(pegase1354, severity, 2)
    add(pegase89, "operational-stress", 2)
    add(pegase89, "emergency-stress", 1)
    add(pegase2869, "emergency-stress", 2)
    for network in (ieee300, rte1888, rte2848, pegase2869):
        add(network, "extreme-stress", 2)
    source = tmp_path / "structural-support.json"
    source.write_text(json.dumps(rows), encoding="utf-8")

    report, selected = matched.build_report(
        rows,
        target_per_cell=2,
        seed=7,
        source_path=source,
    )

    assert report["status"] == "pass"
    assert report["full_factorial_diagnostic"]["status"] == "incomplete"
    assert report["common_support"]["networks"] == [illinois200, pegase1354]
    assert report["common_support"]["severities"] == [
        "normal-envelope",
        "operational-stress",
        "emergency-stress",
    ]
    assert report["common_support"]["selected_matrix"] == [
        [2, 2, 2],
        [2, 2, 2],
    ]
    assert report["common_support"]["association"]["cramers_v"] == 0.0
    assert len(selected) == 12
    conditioned = {
        item["severity"]: item["maximum_supported_networks"]
        for item in report[
            "severity_conditioned_maximum_network_support"
        ]["strata"]
    }
    assert conditioned == {
        "normal-envelope": [illinois200, pegase1354],
        "operational-stress": [illinois200, pegase89, pegase1354],
        "emergency-stress": [illinois200, pegase1354, pegase2869],
        "extreme-stress": [ieee300, rte1888, rte2848, pegase2869],
    }
    assert report["hard_gates"][
        "all_seven_networks_covered_across_severity_conditioned_support"
    ]
    assert "all_28_network_severity_cells_filled" not in report["hard_gates"]
    assert "not across unobserved" in report["claim_boundary"]


def test_transformer_contract_smoke_for_small_networks() -> None:
    for system in ("ieee14", "ieee30"):
        row = transformer_audit.audit_network(system)
        assert row["status"] == "pass"
    assert transformer_audit.synthetic_three_winding_fixture()["status"] == "pass"


def test_exported_pypower_replay_cannot_pass_independent_solver_gate() -> None:
    payload = {
        "contract_version": "gridinstruct-independent-solver-evidence-v1",
        "solver": {
            "name": "PYPOWER",
            "version": "5.1",
            "implementation_repository": "https://github.com/rwl/PYPOWER",
            "independence_class": "native_source_case",
            "input_import_path": "pandapower.converter.to_ppc",
        },
        "case_manifest": {"sha256": "0" * 64},
        "tolerances": {
            "voltage_pu": 1e-4,
            "active_power_mw": 0.05,
            "reactive_power_mvar": 0.05,
            "power_balance_mw": 1e-3,
        },
        "cases": [
            {
                "case_id": "case",
                "source_case": {
                    "provider": "x",
                    "identifier": "y",
                    "version": "z",
                    "sha256": "1" * 64,
                },
                "independent_result": {
                    "converged": True,
                    "power_balance_residual_mw": 0.0,
                },
                "comparison": {
                    "max_abs_bus_voltage_error_pu": 0.0,
                    "max_abs_active_power_error_mw": 0.0,
                    "max_abs_reactive_power_error_mvar": 0.0,
                    "topology_identity_match": True,
                    "thermal_label_match": True,
                    "voltage_label_match": True,
                },
            }
        ],
    }
    result = independent.validate_report(payload)
    assert result["status"] == "fail"
    assert result["solver_evidence_class"] == "exported_replay"


def test_independent_solver_gate_rejects_nonfinite_comparisons() -> None:
    payload = {
        "contract_version": "gridinstruct-independent-solver-evidence-v1",
        "solver": {
            "name": "LightSim2Grid",
            "version": "0.10.3",
            "implementation_repository": "https://github.com/Grid2op/lightsim2grid",
            "independence_class": "native_source_case",
            "input_import_path": "direct MATPOWER text parser",
            "comparison_scope": "fixed-injection fixed-control AC power-flow replay",
            "q_limit_policy": "enforce_q_lims=False",
            "independent_opf_claimed": False,
        },
        "case_manifest": {"sha256": "0" * 64},
        "tolerances": {
            "voltage_pu": 1e-6,
            "angle_degree": 1e-4,
            "active_power_mw": 1e-4,
            "reactive_power_mvar": 1e-4,
            "power_balance_mw": 1e-5,
        },
        "case_count": 1,
        "cases": [
            {
                "case_id": "case",
                "source_model_class": "pglib_native_parameters_and_operating_point",
                "source_case": {
                    "provider": "x",
                    "identifier": "y",
                    "version": "z",
                    "sha256": "1" * 64,
                },
                "independent_result": {
                    "converged": True,
                    "power_balance_residual_mw": 0.0,
                },
                "comparison": {
                    "max_abs_bus_voltage_error_pu": math.nan,
                    "max_abs_bus_angle_error_degree": 0.0,
                    "max_abs_active_power_error_mw": 0.0,
                    "max_abs_reactive_power_error_mvar": 0.0,
                    "topology_identity_match": True,
                    "thermal_label_match": True,
                    "voltage_label_match": True,
                },
            }
        ],
    }
    result = independent.validate_report(payload)
    assert result["status"] == "fail"
    assert result["case_errors"][0]["errors"] == ["voltage_tolerance_exceeded"]


def test_rating_override_requires_hash_bound_source(tmp_path: Path) -> None:
    payload = {
        "ratings": [
            {
                "network": "ieee14",
                "element": "line",
                "index": 0,
                "rating_value": 100.0,
                "source": {
                    "document_id": "planning-case",
                    "document_sha256": "a" * 64,
                    "locator": "branch 1-2",
                },
            }
        ]
    }
    path = tmp_path / "ratings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = rating_audit.build_report(
        ["ieee14"],
        rating_audit.load_rating_overrides(path),
        path,
    )
    assert report["networks"][0]["equipment"][0]["source_override_applied"] is True


def test_pglib_explicit_high_limit_and_conservative_quantile_contract() -> None:
    assert pglib_rate_a_semantics(9900.0) == "source_explicit_high_limit"
    assert sensitivity.conservative_quantile([100.0, 200.0, 300.0], 0.25) == 150.0
