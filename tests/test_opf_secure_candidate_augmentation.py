from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import append_opf_closed_loop_auxiliary_records as opf  # noqa: E402
from generate_opf_secure_candidate_augmentation import (  # noqa: E402
    coverage_first_screening_order,
    candidate_scenario_id,
    decimal_grid,
    generation_order,
    passed_diversity,
    screening_order_key,
    select_diverse_target,
    slack_adjacent_line_pairs,
)
from refresh_opf_secure_candidate_report_schema import refresh_report  # noqa: E402


def test_decimal_grid_is_closed_and_id_safe() -> None:
    assert decimal_grid("0.70", "0.74", "0.02") == [0.7, 0.72, 0.74]
    assert decimal_grid(
        "0.620",
        "0.623",
        "0.001",
        token_function=lambda value: f"{value:.3f}",
    ) == [0.62, 0.621, 0.622, 0.623]


def test_candidate_identity_preserves_boundary_load_precision() -> None:
    assert candidate_scenario_id(0.62, 0.5, 1, 9) != candidate_scenario_id(
        0.621,
        0.5,
        1,
        9,
    )


def test_slack_adjacent_pairs_are_unique_and_structurally_preregistered() -> None:
    net = SimpleNamespace(
        ext_grid=pd.DataFrame({"bus": [0]}),
        line=pd.DataFrame(
            {
                "from_bus": [0, 0, 1, 2],
                "to_bus": [1, 2, 2, 3],
                "max_i_ka": [4.0, 1.0, 3.0, 2.0],
            }
        ),
    )

    pairs = slack_adjacent_line_pairs(net)

    assert pairs == [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3)]
    assert len(pairs) == len(set(pairs))

    bottleneck_pairs = slack_adjacent_line_pairs(
        net,
        bottleneck_anchor_only=True,
    )
    assert bottleneck_pairs == [(0, 1), (1, 2), (1, 3)]


def test_generation_order_crosses_every_registered_physical_stratum() -> None:
    order = generation_order([0.7, 0.8], [0.85, 1.15], [(0, 2), (1, 3)])

    assert len(order) == 8
    assert len(order) == len(set(order))
    assert {row[0] for row in order} == {0.7, 0.8}
    assert {row[1] for row in order} == {0.85, 1.15}
    assert {(row[2], row[3]) for row in order} == {(0, 2), (1, 3)}
    assert order == generation_order(
        [0.7, 0.8],
        [0.85, 1.15],
        [(0, 2), (1, 3)],
    )


def test_passed_diversity_reports_concentration_counts() -> None:
    rows = [
        {
            "load_level": 0.7,
            "generator_factor": 0.85,
            "line_indices": [1, 9],
        },
        {
            "load_level": 0.7,
            "generator_factor": 0.95,
            "line_indices": [1, 9],
        },
        {
            "load_level": 0.8,
            "generator_factor": 0.85,
            "line_indices": [1, 14],
        },
    ]

    diversity = passed_diversity(rows)

    assert diversity["load_levels"] == [0.7, 0.8]
    assert diversity["generator_factors"] == [0.85, 0.95]
    assert diversity["line_pairs"] == [(1, 9), (1, 14)]
    assert diversity["passed_by_load_level"] == {"0.700": 2, "0.800": 1}
    assert diversity["passed_by_generator_factor"] == {"0.85": 2, "0.95": 1}
    assert diversity["passed_by_line_pair"] == {"1,14": 1, "1,9": 2}


def test_passed_diversity_keeps_boundary_refinement_loads_distinct() -> None:
    diversity = passed_diversity(
        [
            {
                "load_level": 0.62,
                "generator_factor": 0.5,
                "line_indices": [1, 9],
            },
            {
                "load_level": 0.621,
                "generator_factor": 0.5,
                "line_indices": [1, 9],
            },
        ]
    )

    assert diversity["load_levels"] == [0.62, 0.621]
    assert diversity["passed_by_load_level"] == {"0.620": 1, "0.621": 1}


def test_screening_order_uses_only_pre_action_headroom_and_identity() -> None:
    lower_loading = {
        "scenario_id": "b",
        "max_branch_loading_percent": 40.0,
        "min_bus_voltage_pu": 0.94,
        "max_bus_voltage_pu": 1.0,
        "load_level": 0.7,
        "generator_factor": 1.1,
        "line_indices": [1, 9],
    }
    higher_loading = {
        **lower_loading,
        "scenario_id": "a",
        "max_branch_loading_percent": 50.0,
    }

    assert screening_order_key(lower_loading) < screening_order_key(higher_loading)


def test_coverage_first_order_frontloads_each_registered_stratum() -> None:
    rows = [
        {
            "scenario_id": "best",
            "max_branch_loading_percent": 10.0,
            "min_bus_voltage_pu": 1.0,
            "max_bus_voltage_pu": 1.0,
            "load_level": 0.6,
            "generator_factor": 0.5,
            "line_indices": [1, 9],
        },
        {
            "scenario_id": "second_pair",
            "max_branch_loading_percent": 30.0,
            "min_bus_voltage_pu": 1.0,
            "max_bus_voltage_pu": 1.0,
            "load_level": 0.6,
            "generator_factor": 0.5,
            "line_indices": [1, 6],
        },
        {
            "scenario_id": "second_load",
            "max_branch_loading_percent": 20.0,
            "min_bus_voltage_pu": 1.0,
            "max_bus_voltage_pu": 1.0,
            "load_level": 0.7,
            "generator_factor": 0.5,
            "line_indices": [1, 9],
        },
        {
            "scenario_id": "second_factor",
            "max_branch_loading_percent": 40.0,
            "min_bus_voltage_pu": 1.0,
            "max_bus_voltage_pu": 1.0,
            "load_level": 0.6,
            "generator_factor": 0.6,
            "line_indices": [1, 9],
        },
    ]

    ordered = coverage_first_screening_order(rows)

    assert [row["scenario_id"] for row in ordered] == [
        "second_pair",
        "best",
        "second_load",
        "second_factor",
    ]
    assert len({row["scenario_id"] for row in ordered}) == len(rows)


def test_exact_target_selector_preserves_order_and_required_diversity() -> None:
    rows = [
        {
            "scenario_id": f"candidate-{index}",
            "load_level": load,
            "generator_factor": factor,
            "line_indices": [1, 9],
        }
        for index, (load, factor) in enumerate(
            [
                (0.62, 0.50),
                (0.62, 0.51),
                (0.62, 0.52),
                (0.62, 0.53),
                (0.63, 0.50),
                (0.63, 0.51),
                (0.69, 0.50),
            ]
        )
    ]

    selected = select_diverse_target(rows, 5, 3, 3, 1)

    assert [row["scenario_id"] for row in selected] == [
        "candidate-0",
        "candidate-1",
        "candidate-2",
        "candidate-4",
        "candidate-6",
    ]
    diversity = passed_diversity(selected)
    assert diversity["load_levels"] == [0.62, 0.63, 0.69]
    assert diversity["generator_factors"] == [0.5, 0.51, 0.52]
    assert diversity["line_pairs"] == [(1, 9)]


def test_opf_reconstruction_applies_combined_generation_factor(monkeypatch) -> None:
    net = SimpleNamespace(
        load=pd.DataFrame({"p_mw": [50.0], "q_mvar": [10.0]}),
        gen=pd.DataFrame({"p_mw": [100.0]}),
        line=pd.DataFrame({"in_service": [True, True]}),
        trafo=pd.DataFrame({"in_service": []}),
        bus=pd.DataFrame({"in_service": [True, True]}),
        ext_grid=pd.DataFrame({"in_service": [True]}),
    )
    monkeypatch.setitem(opf.SYSTEM_LOADERS, "ieee14", lambda: net)
    scenario = {
        "scenario_id": "combined",
        "system": "ieee14",
        "contingency_type": "n2_branch_outage",
        "load_level": 0.8,
        "base_generation_scaling_factor": 0.8,
        "generator_factor": 0.85,
        "line_indices": [0, 1],
    }

    rebuilt = opf.build_net(scenario)

    assert rebuilt.gen.at[0, "p_mw"] == 68.0
    assert rebuilt.load.at[0, "p_mw"] == 40.0
    assert rebuilt.line.in_service.tolist() == [False, False]


def test_report_schema_refresh_is_derived_from_complete_published_rows() -> None:
    state_tables = {
        "bus_results": [],
        "line_results": [],
        "transformer_results": [],
        "ext_grid_results": [],
        "generator_results": [],
        "load_results": [],
        "switch_states": [],
    }
    scenarios = [
        {
            "scenario_id": "candidate_a",
            "load_level": 0.7,
            "generator_factor": 0.8,
            "line_indices": [1, 9],
            "all_in_service_states_finite": True,
            "additional_state_tables_complete": True,
            **state_tables,
        },
        {
            "scenario_id": "candidate_b",
            "load_level": 0.8,
            "generator_factor": 1.2,
            "line_indices": [1, 14],
            "all_in_service_states_finite": True,
            "additional_state_tables_complete": True,
            **state_tables,
        },
    ]
    report = {
        "status": "pass",
        "target_passed_candidates": 2,
        "output_scenario_ids": ["candidate_a", "candidate_b"],
    }

    refreshed = refresh_report(
        report,
        scenarios,
        scenario_sha256="scenario-sha",
        generator_script_sha256="script-sha",
    )

    assert refreshed["passed_by_load_level"] == {"0.70": 1, "0.80": 1}
    assert refreshed["passed_by_generator_factor"] == {"0.80": 1, "1.20": 1}
    assert refreshed["passed_by_line_pair"] == {"1,14": 1, "1,9": 1}
    assert refreshed["published_output_completeness"]["status"] == "pass"
    assert refreshed["report_schema_refresh"] == {
        "contract": "ieee14-opf-secure-candidate-report-schema-refresh-v1",
        "current_generator_script_sha256": "script-sha",
        "input_output_scenarios_sha256": "scenario-sha",
        "method": "deterministic_counts_and_completeness_from_published_scenarios",
        "published_scenario_count": 2,
        "status": "pass",
    }
