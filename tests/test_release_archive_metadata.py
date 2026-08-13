from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_release_bundle as release_bundle  # noqa: E402
from build_release_bundle import (  # noqa: E402
    ROOT as RELEASE_ROOT,
    build_archive_metadata,
    deposition_status,
    evidence_bound_paths,
    resolved_metadata_value,
    should_include,
    successful_archive_status,
    write_reproducibility_manifests,
)


def args(**overrides: str | None) -> argparse.Namespace:
    values = {
        "public_repository_url": None,
        "data_doi": None,
        "archived_code_release_doi": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_archive_metadata_preserves_existing_external_identifiers() -> None:
    existing = {
        "public_repository_url": "https://example.org/repository",
        "data_doi": "https://doi.org/10.1234/data",
        "archived_code_release_doi": "https://doi.org/10.1234/code",
    }

    metadata = build_archive_metadata(args(), existing)

    for key, value in existing.items():
        assert metadata[key] == value
    assert "third_party_case_redistribution_confirmation" not in metadata


def test_explicit_archive_metadata_overrides_existing_values() -> None:
    metadata = build_archive_metadata(
        args(data_doi="https://doi.org/10.1234/new"),
        {"data_doi": "https://doi.org/10.1234/old"},
    )

    assert metadata["data_doi"] == "https://doi.org/10.1234/new"


def test_formal_environment_specification_is_included_in_release() -> None:
    assert should_include(RELEASE_ROOT / "environment.yml") is True
    assert should_include(RELEASE_ROOT / "metadata/formal_runtime_environment.json") is True
    assert should_include(RELEASE_ROOT / "third_party/pglib-opf-v23.07/LICENSE") is True


def test_release_uses_v16_replay_pool_instead_of_legacy_plus15() -> None:
    assert should_include(
        RELEASE_ROOT / "data/gridinstruct_v1.2_paper_candidate_actionable_plus15_v16.jsonl"
    )
    assert not should_include(
        RELEASE_ROOT / "data/gridinstruct_v1.2_paper_candidate_actionable_plus15.jsonl"
    )


def test_fully_ready_and_external_pending_archives_are_success_states() -> None:
    assert successful_archive_status(
        "package_ready_external_identifiers_pending"
    )
    assert successful_archive_status("package_ready_for_deposition")
    assert not successful_archive_status("reproducibility_or_evidence_binding_failed")


def test_claim_bearing_power_evidence_is_included_in_release() -> None:
    for relative in (
        "reports/core_n1_denominator_v1.2_sd_core.json",
        "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
        "reports/independent_solver_raw_evidence_v1.2_sd_core.json",
        "reports/independent_solver_validation_v1.2_sd_core.json",
        "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
        "simulation_outputs/independent_solver/case_manifest_v1.json",
        (
            "simulation_outputs/opf_closed_loop/"
            "ieee14_secure_candidate_scenarios_v1.json"
        ),
        (
            "simulation_outputs/opf_closed_loop/"
            "opf_action_uncertainty_stress_cases_v1.json"
        ),
    ):
        assert should_include(RELEASE_ROOT / relative)


def test_deposition_status_distinguishes_local_failure_from_external_inputs() -> None:
    assert deposition_status(
        "reproducibility_or_evidence_binding_failed",
        ["native_independent_solver_pass"],
        [],
    ) == "local_package_failed"
    assert deposition_status(
        "package_ready_external_identifiers_pending",
        [],
        ["data_doi_inserted"],
    ) == "local_package_ready_external_deposition_pending"
    assert deposition_status(
        "package_ready_for_deposition",
        [],
        [],
    ) == "local_package_ready_for_deposition"


def test_evidence_bound_paths_include_current_inputs_and_multiseed(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "metadata/evidence_binding_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "current_inputs": {
                    "dataset": {"path": "data/current.jsonl", "sha256": "a"}
                },
                "evidence": {
                    "query": {"path": "reports/query.json", "sha256": "b"},
                    "multiseed": {
                        "summaries": [
                            {"path": "benchmark/seed_summary.json", "sha256": "c"}
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(release_bundle, "ROOT", tmp_path)

    assert evidence_bound_paths() == {
        "data/current.jsonl",
        "reports/query.json",
        "benchmark/seed_summary.json",
    }


def test_reproducibility_manifest_preserves_historical_and_formal_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    historical = metadata / "runtime_environment.json"
    formal = metadata / "formal_runtime_environment.json"
    historical.write_text(json.dumps({"role": "historical"}), encoding="utf-8")
    formal.write_text(json.dumps({"role": "formal"}), encoding="utf-8")
    historical_bytes = historical.read_bytes()
    formal_bytes = formal.read_bytes()
    monkeypatch.setattr(release_bundle, "ROOT", tmp_path)

    write_reproducibility_manifests()

    assert historical.read_bytes() == historical_bytes
    assert formal.read_bytes() == formal_bytes
    assert (metadata / "environment_lock.json").is_file()
    environment_lock = json.loads(
        (metadata / "environment_lock.json").read_text(encoding="utf-8")
    )
    assert {
        "lightsim2grid",
        "lm-format-enforcer",
        "power-grid-model",
    } <= set(environment_lock["packages"])


def test_placeholder_values_remain_pending() -> None:
    assert resolved_metadata_value(None, "pending") == "pending"
    assert resolved_metadata_value("", "TBD") == "pending"
    assert resolved_metadata_value(None, None) == "pending"
