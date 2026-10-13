from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_third_party_asset_inventory as target  # noqa: E402


FORMAL_SYSTEMS = {
    "ieee14",
    "ieee30",
    "ieee57",
    "ieee118",
    "ieee300",
    "illinois200",
    "pegase89",
    "pegase1354",
    "rte1888",
    "rte2848",
    "pegase2869",
}


@pytest.fixture
def synthetic_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    pglib_root = tmp_path / "pglib-opf"
    pglib_root.mkdir()

    license_text = "fixture PGLib license"
    license_path = pglib_root / "LICENSE"
    license_path.write_text(license_text, encoding="utf-8")
    license_sha256 = hashlib.sha256(license_path.read_bytes()).hexdigest()

    case_files = {system: f"fixture_{system}.m" for system in sorted(FORMAL_SYSTEMS)}
    case_hashes = {}
    for system, filename in case_files.items():
        path = pglib_root / filename
        path.write_text(f"% fixture {system}\n", encoding="utf-8")
        case_hashes[system] = hashlib.sha256(path.read_bytes()).hexdigest()

    commit = "fixture-commit"
    license_name = "CC-BY-4.0 for data; MIT for software"
    monkeypatch.setattr(target, "PGLIB_CASE_FILES", case_files)
    monkeypatch.setattr(target, "PGLIB_CASE_SHA256", case_hashes)
    monkeypatch.setattr(target, "PGLIB_EXPECTED_COMMIT", commit)
    monkeypatch.setattr(target, "PGLIB_LICENSE", license_name)
    monkeypatch.setattr(target, "PGLIB_LICENSE_SHA256", license_sha256)
    monkeypatch.setattr(target, "package_version", lambda _name: "fixture-version")

    registry = {
        "repository": "https://example.invalid/pglib-opf",
        "release": "v23.07",
        "commit": commit,
        "license": license_name,
        "license_sha256": license_sha256,
        "cases": {
            system: {"file": case_files[system], "sha256": case_hashes[system]}
            for system in case_files
        },
    }
    bridge = {
        "provider": "pandapower.networks.case300",
        "role": (
            "convergent operating point only; PGLib topology and RATE_A "
            "take precedence"
        ),
        "installed_case_file_sha256": "fixture-case300-sha256",
        "raw_case_file_in_release_archive": False,
    }
    return target.build_inventory(
        pglib_root=pglib_root,
        registry=registry,
        ieee300_bridge=bridge,
    )


def test_attribution_contract_covers_all_formal_systems() -> None:
    assert set(target.PGLIB_CASE_FILES) == FORMAL_SYSTEMS
    assert set(target.CASE_ATTRIBUTION_METADATA) == FORMAL_SYSTEMS
    for system, metadata in target.CASE_ATTRIBUTION_METADATA.items():
        record_name = metadata["header_attribution_record"]
        assert record_name in target.HEADER_ATTRIBUTION_RECORDS, system
        assert system in target.HEADER_ATTRIBUTION_RECORDS[record_name]["applies_to"]
        assert metadata["required_source_citation"].strip()
        assert metadata["case_header_change_notice"].strip()


def test_synthetic_inventory_preserves_case_level_contract(
    synthetic_inventory: dict,
) -> None:
    inventory = synthetic_inventory
    assert inventory["status"] == "pass"
    assert inventory["validation_errors"] == []
    assert inventory["audit_scope"]["scope_limit"] == target.AUDIT_SCOPE_LIMIT

    cases = {row["system"]: row for row in inventory["network_case_inputs"]}
    assert set(cases) == FORMAL_SYSTEMS
    for system, row in cases.items():
        assert row["header_attribution"].strip(), system
        assert row["header_attribution_record"].strip(), system
        assert row["required_source_citation"].strip(), system
        assert row["case_header_change_notice"].strip(), system
        assert row["derived_state_redistribution_status"] == "confirmed"
        assert row["required_attribution_status"] == "confirmed"
        assert row["raw_case_file_in_release_archive"] is False
        assert row["derived_synthetic_states_in_release_archive"] is True

    release_scope = inventory["release_scope"]
    assert release_scope["pglib_license_file_in_archive"] is True
    assert release_scope["raw_pglib_case_files_in_archive"] is False
    assert release_scope["archive_rebuild_required"] is True


def test_rte_restriction_and_ieee300_bridge_are_preserved(
    synthetic_inventory: dict,
) -> None:
    cases = {row["system"]: row for row in synthetic_inventory["network_case_inputs"]}
    for system in ("rte1888", "rte2848"):
        restriction = cases[system]["usage_restriction"].lower()
        assert "do not use for operation or planning" in restriction
        assert "french or european grids" in restriction

    bridge = cases["ieee300"]["operating_point_bridge"]
    assert bridge["provider"] == "pandapower.networks.case300"
    assert bridge["installed_case_file_sha256"] == "fixture-case300-sha256"
    assert bridge["raw_case_file_in_release_archive"] is False


def test_rendered_inventory_keeps_detailed_attribution(
    synthetic_inventory: dict,
) -> None:
    rendered = target.render_third_party_assets(synthetic_inventory)
    for system in FORMAL_SYSTEMS:
        assert f"`{system}`" in rendered
        row = next(
            item
            for item in synthetic_inventory["network_case_inputs"]
            if item["system"] == system
        )
        assert row["header_attribution"] in rendered
        assert row["required_source_citation"] in rendered
        assert row["case_header_change_notice"] in rendered

    assert "PGLib `LICENSE` included: `true`" in rendered
    assert "Raw PGLib case files included: `false`" in rendered
    assert "Final archive rebuild required" in rendered
    assert (
        "do not use for operation or planning of the French or European grids"
        in rendered
    )
