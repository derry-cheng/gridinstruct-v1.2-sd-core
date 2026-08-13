from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_independent_solver_case_manifest import (  # noqa: E402
    validate_scenario_source_binding,
)


SOURCE = {
    "provider": "IEEE PES PGLib-OPF",
    "identifier": "pglib_opf_case14_ieee.m",
    "version": "abc123",
    "sha256": "a" * 64,
    "download_uri": "https://example.invalid/case14.m",
}


def test_missing_physical_source_cannot_be_relabelled_as_pglib() -> None:
    with pytest.raises(ValueError, match="no hash-bound physical_source"):
        validate_scenario_source_binding(
            {"scenario_id": "legacy", "network_model": "ieee14"},
            SOURCE,
            system="ieee14",
        )


def test_physical_source_requires_exact_commit_case_and_hash() -> None:
    scenario = {
        "scenario_id": "mismatch",
        "physical_source": {
            "repository_commit": "wrong",
            "case_file": SOURCE["identifier"],
            "case_sha256": SOURCE["sha256"],
        },
    }
    with pytest.raises(ValueError, match="physical_source mismatch"):
        validate_scenario_source_binding(scenario, SOURCE, system="ieee14")


def test_ieee300_hybrid_operating_point_is_not_native_parameter_replay() -> None:
    scenario = {
        "scenario_id": "ieee300",
        "physical_source": {
            "repository_commit": SOURCE["version"],
            "case_file": SOURCE["identifier"],
            "case_sha256": SOURCE["sha256"],
            "operating_point": {
                "provider": "pandapower.networks.case300",
                "pglib_topology_and_rating_precedence": True,
            },
        },
    }
    classification = validate_scenario_source_binding(
        scenario,
        SOURCE,
        system="ieee300",
    )
    assert classification["native_parameter_replay_eligible"] is False
    assert (
        classification["source_model_class"]
        == "hybrid_operating_point_pglib_topology_ratings"
    )
