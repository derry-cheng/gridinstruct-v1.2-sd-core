from __future__ import annotations

import json
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pandapower as pp
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_extended_topology_stress as stress  # noqa: E402
import build_external_matched_strata as matched  # noqa: E402
from power_evidence_common import (  # noqa: E402
    envelope_class,
    source_bound_rating_map_from_loaded_network,
)


def _scenario(status: str, contingency: str, line_index: int | None) -> dict:
    converged = status == "converged"
    return {
        "scenario_id": f"fake_{contingency}_{line_index}",
        "network_model": "fake",
        "contingency_type": contingency,
        "load_level": 1.0,
        "solver_status": status,
        "failure_class": None if converged else "numerical_nonconvergence",
        "max_branch_loading_percent": 50.0 if converged else None,
        "min_bus_voltage_pu": 1.0 if converged else None,
        "max_bus_voltage_pu": 1.0 if converged else None,
        "all_in_service_states_finite": converged,
        "additional_state_tables_complete": converged,
        "additional_element_results": {} if converged else None,
        "switch_states": [] if converged else None,
        "state_counts": {"buses": 1} if converged else {},
        "power_balance_residual_mw": 0.0 if converged else None,
    }


def test_failed_run_preserves_prior_publication_and_writes_attempt_ledger(tmp_path, monkeypatch) -> None:
    output = tmp_path / "published.json"
    output.write_text('[{"prior": true}]', encoding="utf-8")
    monkeypatch.setattr(stress, "ROOT", tmp_path)
    monkeypatch.setattr(
        stress,
        "SYSTEM_LOADERS",
        {"fake": lambda: SimpleNamespace(line=range(1), bus=range(1))},
    )
    monkeypatch.setattr(
        stress,
        "run_case",
        lambda system, load_level, contingency, line_idx, scenario_namespace=None: _scenario(
            "failed" if contingency == "base_power_flow" else "converged",
            contingency,
            line_idx,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_extended_topology_stress.py",
            "--systems",
            "fake",
            "--load-levels",
            "1.0",
            "--max-lines-per-system",
            "1",
            "--max-attempts-per-stratum",
            "1",
            "--output-json",
            "published.json",
            "--attempts-json",
            "attempts.json",
            "--report-json",
            "report.json",
            "--report-md",
            "report.md",
        ],
    )

    with pytest.raises(SystemExit):
        stress.main()

    assert json.loads(output.read_text(encoding="utf-8")) == [{"prior": True}]
    attempts = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert len(attempts) == 2
    assert attempts[0]["solver_status"] == "failed"
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["scenario_record_count"] == 1
    assert report["publication_transaction"]["output_written_by_this_run"] is False
    assert report["publication_transaction"]["output_sha256"] is None
    assert report["publication_transaction"]["prior_output_sha256"]


def test_published_external_loading_matches_matched_recomputation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    net = pp.create_empty_network(sn_mva=100.0)
    from_bus = pp.create_bus(net, vn_kv=110.0)
    to_bus = pp.create_bus(net, vn_kv=110.0)
    pp.create_ext_grid(net, bus=from_bus, vm_pu=1.0)
    pp.create_load(net, bus=to_bus, p_mw=35.0, q_mvar=8.0)
    pp.create_line_from_parameters(
        net,
        from_bus=from_bus,
        to_bus=to_bus,
        length_km=1.0,
        r_ohm_per_km=0.05,
        x_ohm_per_km=0.2,
        c_nf_per_km=0.0,
        max_i_ka=0.5,
    )
    net["pglib_provenance"] = {
        "repository_commit": "pinned-test-commit",
        "case_sha256": "a" * 64,
    }
    monkeypatch.setattr(
        stress,
        "load_pglib_network",
        lambda system, pglib_root: copy.deepcopy(net),
    )

    published = stress.run_case(
        "fake",
        1.0,
        "base_power_flow",
        None,
        pglib_root=tmp_path,
    )
    recomputed = matched.recompute_source_bound_loading(
        published,
        source_bound_rating_map_from_loaded_network(net),
        "pinned-test-commit",
    )

    assert published["thermal_rating_status"] == "source_bound"
    assert recomputed["thermal_rating_status"] == "source_bound"
    assert recomputed["max_branch_loading_percent"] == pytest.approx(
        published["max_branch_loading_percent"],
        abs=1e-9,
    )
    assert envelope_class(recomputed) == envelope_class(published)


def test_matched_rating_maps_use_the_pinned_loaded_index_space(
    tmp_path: Path,
    monkeypatch,
) -> None:
    net = pp.create_empty_network()
    from_bus = pp.create_bus(net, vn_kv=110.0)
    to_bus = pp.create_bus(net, vn_kv=110.0)
    pp.create_line_from_parameters(
        net,
        from_bus=from_bus,
        to_bus=to_bus,
        length_km=1.0,
        r_ohm_per_km=0.05,
        x_ohm_per_km=0.2,
        c_nf_per_km=0.0,
        max_i_ka=0.5,
        index=17,
    )
    calls = []
    monkeypatch.setattr(matched, "EXTERNAL_NETWORKS", ("fake",))
    monkeypatch.setattr(
        matched,
        "load_pglib_network",
        lambda system, root, expected_commit: (
            calls.append((system, root, expected_commit)) or copy.deepcopy(net)
        ),
    )

    maps, coverage = matched.source_bound_rating_maps(
        tmp_path,
        {"commit": "pinned-test-commit"},
        [],
    )

    assert calls == [("fake", tmp_path, "pinned-test-commit")]
    assert set(maps["fake"]) == {("line", 17)}
    assert coverage["fake"]["missing_ratings"] == []


def test_generation_and_voltage_linkage_respect_source_controls() -> None:
    net = SimpleNamespace(
        bus=pd.DataFrame(
            {
                "min_vm_pu": [0.95],
                "max_vm_pu": [1.05],
            }
        ),
        ext_grid=pd.DataFrame(
            {
                "bus": [0],
                "vm_pu": [1.02],
            }
        ),
        gen=pd.DataFrame(
            {
                "bus": [0],
                "p_mw": [100.0],
                "vm_pu": [1.04],
            }
        ),
    )

    stress.scale_generation(net, 0.8)
    stress.scale_generator_voltage_setpoints(net, 1.1)

    assert net.gen.at[0, "p_mw"] == pytest.approx(80.0)
    assert net.gen.at[0, "vm_pu"] == pytest.approx(1.05)
    assert net.ext_grid.at[0, "vm_pu"] == pytest.approx(1.05)
