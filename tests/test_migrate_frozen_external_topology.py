from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_frozen_external_topology import choose_replacements  # noqa: E402


def test_replacements_preserve_network_contingency_and_use_unique_nearest_load() -> None:
    invalid = [
        {
            "scenario_id": "legacy_a",
            "network_model": "IEEE300",
            "contingency_type": "n1_branch_outage",
            "load_level": 1.0,
        },
        {
            "scenario_id": "legacy_b",
            "network_model": "IEEE300",
            "contingency_type": "n1_branch_outage",
            "load_level": 1.0,
        },
    ]
    current = [
        {
            "scenario_id": "current_far",
            "network_model": "ieee300",
            "contingency_type": "n1_branch_outage",
            "load_level": 0.8,
            "solver_status": "converged",
        },
        {
            "scenario_id": "current_near_a",
            "network_model": "ieee300",
            "contingency_type": "n1_branch_outage",
            "load_level": 0.95,
            "solver_status": "converged",
        },
        {
            "scenario_id": "current_near_b",
            "network_model": "ieee300",
            "contingency_type": "n1_branch_outage",
            "load_level": 0.955,
            "solver_status": "converged",
        },
        {
            "scenario_id": "wrong_type",
            "network_model": "ieee300",
            "contingency_type": "base_power_flow",
            "load_level": 1.0,
            "solver_status": "converged",
        },
    ]

    mapping = choose_replacements(invalid, current)

    selected = {row["scenario_id"] for row in mapping.values()}
    assert selected == {"current_near_a", "current_near_b"}
    assert len(selected) == len(mapping)
