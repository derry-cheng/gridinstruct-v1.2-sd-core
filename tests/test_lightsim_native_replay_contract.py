from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


pytest.importorskip("lightsim2grid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_lightsim_native_independent_solver import (  # noqa: E402
    apply_manifest_case,
    voltage_violation_label,
)


def native_fixture() -> dict:
    return {
        "base_mva": 100.0,
        "bus": [
            [1.0, 3.0, 10.0, 2.0, 0.0, 0.0, 1.0, 1.0, 0.0, 110.0],
            [2.0, 2.0, 20.0, 4.0, 0.0, 0.0, 1.0, 1.0, 0.0, 110.0],
            [3.0, 1.0, 5.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 110.0],
        ],
        "gen": [
            [1.0, 30.0, 0.0, 50.0, -50.0, 1.0, 100.0, 1.0, 100.0, 0.0],
            [2.0, 10.0, 0.0, 50.0, -50.0, 1.0, 100.0, 1.0, 100.0, 0.0],
        ],
        "branch": [
            [1.0, 2.0, 0.01, 0.1, 0.0, 100.0, 100.0, 100.0, 0.0, 0.0, 1.0],
            [2.0, 3.0, 0.01, 0.1, 0.0, 100.0, 100.0, 100.0, 0.0, 0.0, 1.0],
        ],
    }


def test_generator_outage_uses_hash_stable_source_row() -> None:
    case = copy.deepcopy(native_fixture())
    apply_manifest_case(
        case,
        {
            "contingency": {
                "components": [
                    {
                        "element": "gen",
                        "pglib_source_row": 2,
                        "bus": {"source_bus_id": "2"},
                        "source_bus_ordinal": 0,
                    }
                ]
            },
            "post_action_controls": [],
        },
    )
    assert case["gen"][1][7] == 0.0
    assert case["gen"][0][7] == 1.0


def test_load_scaling_applies_once_to_active_and_reactive_demand() -> None:
    case = copy.deepcopy(native_fixture())
    apply_manifest_case(
        case,
        {
            "load_scaling_factor": 0.7,
            "generator_scaling_factor": 1.0,
            "contingency": {"components": []},
            "post_action_controls": [],
        },
    )
    assert case["bus"][0][2:4] == pytest.approx([7.0, 1.4])
    assert case["bus"][1][2:4] == pytest.approx([14.0, 2.8])


def test_voltage_label_uses_registered_numerical_boundary_band() -> None:
    assert not voltage_violation_label(
        0.95 - 5e-7,
        1.05 + 5e-7,
        boundary_tolerance_pu=1e-6,
    )
    assert voltage_violation_label(
        0.95 - 2e-6,
        1.05,
        boundary_tolerance_pu=1e-6,
    )


def test_bus_outage_deactivates_incident_injections_and_branches() -> None:
    case = copy.deepcopy(native_fixture())
    apply_manifest_case(
        case,
        {
            "contingency": {
                "components": [{"element": "bus", "source_bus_id": "2"}]
            },
            "post_action_controls": [],
        },
    )
    assert case["bus"][1][1] == 4.0
    assert case["bus"][1][2:6] == [0.0, 0.0, 0.0, 0.0]
    assert [row[10] for row in case["branch"]] == [0.0, 0.0]
    assert case["gen"][1][7] == 0.0
