from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import recompute_scenario_truth as target  # noqa: E402


def physical_source(system: str = "ieee14") -> dict[str, str]:
    return {
        "contract_version": "gridinstruct-pglib-network-source-v1",
        "system": system,
        "repository": "repo",
        "repository_commit": "commit",
        "license_sha256": "license",
        "case_file": f"{system}.m",
        "case_sha256": "case",
    }


def test_normalize_system_uses_physical_source_when_legacy_row_omits_system() -> None:
    assert target.normalize_system(
        {"network_model": "unparseable display name", "physical_source": physical_source()}
    ) == "ieee14"


def test_hash_bound_row_requires_explicit_pglib_root() -> None:
    with pytest.raises(ValueError, match="--pglib-root was not provided"):
        target.build_net(
            {
                "scenario_id": "bound",
                "system": "ieee14",
                "contingency_type": "base_power_flow",
                "physical_source": physical_source(),
            }
        )


def test_hash_bound_row_uses_pglib_loader_and_replays_generation_scaling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Column:
        def __init__(self) -> None:
            self.multiplier = 1.0

        def __imul__(self, factor: float):
            self.multiplier *= factor
            return self

    class Table:
        def __init__(self) -> None:
            self.p_mw = Column()

        def __len__(self) -> int:
            return 1

        def __getitem__(self, key: str):
            assert key == "p_mw"
            return self.p_mw

        def __setitem__(self, key: str, value: Column) -> None:
            assert key == "p_mw"
            self.p_mw = value

    class Net:
        def __init__(self) -> None:
            self.gen = Table()

    net = Net()
    source = physical_source()
    loaded: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        target,
        "load_pglib_network",
        lambda system, root: loaded.append((system, root)) or net,
    )
    monkeypatch.setattr(target, "network_provenance", lambda _: copy.deepcopy(source))
    monkeypatch.setattr(target, "scale_loads", lambda *_: None)
    monkeypatch.setattr(
        target,
        "scale_generation",
        lambda loaded_net, factor: loaded_net.gen.__setitem__(
            "p_mw", loaded_net.gen["p_mw"].__imul__(factor)
        ),
    )

    result = target.build_net(
        {
            "scenario_id": "bound",
            "system": "ieee14",
            "contingency_type": "base_power_flow",
            "physical_source": source,
            "base_generation_scaling_factor": 1.2,
        },
        pglib_root=Path("/pglib"),
    )

    assert result is net
    assert loaded == [("ieee14", Path("/pglib"))]
    assert net.gen.p_mw.multiplier == pytest.approx(1.2)


def test_physical_source_hash_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyTable:
        def __len__(self) -> int:
            return 0

    class Net:
        gen = EmptyTable()

    source = physical_source()
    actual = copy.deepcopy(source)
    actual["case_sha256"] = "different"
    monkeypatch.setattr(target, "load_pglib_network", lambda *_: Net())
    monkeypatch.setattr(target, "network_provenance", lambda _: actual)

    with pytest.raises(ValueError, match="physical_source mismatch"):
        target.build_net(
            {
                "scenario_id": "mismatch",
                "system": "ieee14",
                "contingency_type": "base_power_flow",
                "physical_source": source,
            },
            pglib_root=Path("/pglib"),
        )
