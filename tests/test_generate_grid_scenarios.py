"""Regression tests for complete electrical-state serialization."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_grid_scenarios as scenarios  # noqa: E402


def test_case_loader_normalizes_current_transformer_contract() -> None:
    for system in scenarios.SYSTEM_LOADERS:
        net = scenarios.clone_net(system)
        for element in ("trafo", "trafo3w"):
            table = getattr(net, element, None)
            if table is not None and len(table):
                assert "tap_dependency_table" in table.columns
                assert table["tap_dependency_table"].eq(False).all()


def test_bus_outage_propagates_effective_energization() -> None:
    result = scenarios.run_scenario(
        system="ieee14",
        load_level=1.0,
        scenario_kind="n1_bus_outage",
        branch_idx=None,
        gen_factor=1.0,
        bus_idx=2,
    )

    assert result["solver_status"] == "converged"
    assert result["all_in_service_states_finite"] is True
    assert result["power_balance_residual_mw"] <= 1e-3

    connected_tables = (
        result["line_results"],
        result["transformer_results"],
        result["generator_results"],
        result["ext_grid_results"],
        result["load_results"],
    )
    connected_rows = [
        row
        for rows in connected_tables
        for row in rows
        if 2
        in {
            row.get("bus"),
            row.get("from_bus"),
            row.get("to_bus"),
            row.get("hv_bus"),
            row.get("lv_bus"),
        }
    ]
    assert connected_rows
    assert all(row["in_service"] is False for row in connected_rows)
    assert any(row["declared_in_service"] is True for row in connected_rows)

    isolated_generator = next(
        row for row in result["generator_results"] if row["bus"] == 2
    )
    assert isolated_generator["declared_in_service"] is True
    assert isolated_generator["in_service"] is False
    assert isolated_generator["vm_pu"] is None


def test_declared_branch_outage_is_not_effectively_energized() -> None:
    result = scenarios.run_scenario(
        system="ieee14",
        load_level=1.0,
        scenario_kind="n1_branch_outage",
        branch_idx=1,
        gen_factor=1.0,
    )

    assert result["solver_status"] == "converged"
    assert result["all_in_service_states_finite"] is True
    outaged_line = next(row for row in result["line_results"] if row["line"] == 1)
    assert outaged_line["declared_in_service"] is False
    assert outaged_line["in_service"] is False
    assert result["counts_in_security_denominator"] is True


def test_n1_denominator_uses_sorted_in_service_line_identities() -> None:
    net = scenarios.clone_net("ieee14")
    net.line.index = [100 + 3 * offset for offset in range(len(net.line))]
    net.line.at[103, "in_service"] = False

    assert scenarios.in_service_line_indices(net, 4) == [100, 106, 109, 112]
    affected = scenarios.apply_branch_outage(net, 106)
    assert affected
    assert bool(net.line.at[106, "in_service"]) is False


def test_base_state_keeps_connected_elements_energized() -> None:
    result = scenarios.run_scenario(
        system="ieee14",
        load_level=1.0,
        scenario_kind="base",
        branch_idx=None,
        gen_factor=1.0,
    )

    assert result["solver_status"] == "converged"
    assert result["all_in_service_states_finite"] is True
    for rows in (
        result["line_results"],
        result["transformer_results"],
        result["generator_results"],
        result["ext_grid_results"],
        result["load_results"],
    ):
        assert all(row["declared_in_service"] == row["in_service"] for row in rows)
