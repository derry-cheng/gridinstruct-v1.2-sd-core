from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_opf_scenario_projection import build_projection  # noqa: E402


def scenario(system: str, scenario_id: str, *, bound: bool = True) -> dict:
    return {
        "scenario_id": scenario_id,
        "system": system,
        "network_model": system.upper(),
        "contingency_type": "n1_branch_outage",
        "solver_status": "converged",
        "physical_source": {"case_sha256": "abc"} if bound else None,
    }


def test_projection_is_source_bound_and_independent_of_external_rows() -> None:
    rows = [
        scenario("ieee118", "b"),
        scenario("pegase89", "external", bound=False),
        scenario("ieee14", "a"),
    ]

    selected, report = build_projection(
        rows,
        {"ieee14", "ieee118"},
        {"n1_branch_outage"},
    )

    assert report["status"] == "pass"
    assert [row["scenario_id"] for row in selected] == ["b", "a"]
    assert report["by_system"] == {"ieee14": 1, "ieee118": 1}


def test_projection_rejects_unbound_requested_system_row() -> None:
    rows = [
        scenario("ieee14", "a"),
        scenario("ieee118", "b", bound=False),
    ]

    with pytest.raises(ValueError, match="all_rows_have_physical_source"):
        build_projection(rows, {"ieee14", "ieee118"}, {"n1_branch_outage"})
