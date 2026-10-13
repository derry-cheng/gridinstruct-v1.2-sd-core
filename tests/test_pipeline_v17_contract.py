from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_sd_core_full_pipeline as pipeline  # noqa: E402


def test_v17_orders_recompute_promotion_splits_translation_and_alignment() -> None:
    steps = pipeline.build_steps("experiments/test_v17")
    names = [step.name for step in steps]

    assert pipeline.DEFAULT_RUN_ROOT == "experiments/sd_core_rebuild_v17_20260724"
    assert names.index("recompute_compliance_labels_before_promotion") < names.index(
        "promote_canonical_dataset"
    )
    assert names.index("recompute_complete_scenario_truth") < names.index(
        "build_opf_scenario_projection"
    )
    assert names.index("build_opf_scenario_projection") < names.index(
        "append_realistic_opf_closed_loop"
    )
    assert names.index("append_realistic_opf_closed_loop") < names.index(
        "validate_cross_solver_power_flow"
    )
    assert names.index("validate_cross_solver_power_flow") < names.index(
        "validate_lightsim_native_independent_solver"
    )
    assert names.index("validate_lightsim_native_independent_solver") < names.index(
        "validate_opf_action_uncertainty_stress"
    )
    assert names.index("validate_opf_action_uncertainty_stress") < names.index(
        "repair_query_result_consistency"
    )
    assert names.index("promote_canonical_dataset") < names.index(
        "create_official_splits_seed42"
    )
    assert names.index("create_official_splits_seed42") < names.index(
        "build_english_release_view"
    )
    assert names.index("build_english_release_view") < names.index(
        "verify_canonical_dataset_immutability"
    )
    assert names.index("materialize_english_after_strict_split") < names.index(
        "validate_source_english_core_alignment"
    )
    assert names.index(
        "materialize_english_after_template_holdout_split"
    ) < names.index("validate_source_english_all_split_alignment")
    assert names.index("create_proxy_stress_splits") < names.index(
        "materialize_english_after_proxy_stress_splits"
    )
    assert names.index("materialize_english_after_proxy_stress_splits") < names.index(
        "validate_source_english_proxy_stress_alignment"
    )
    assert names.index("validate_source_english_proxy_stress_alignment") < names.index(
        "run_proxy_stress_tfidf"
    )
    assert names.index("sync_manuscript_claims_from_evidence") < names.index(
        "build_sd_evidence_binding_manifest"
    )
    assert names.index("sync_manuscript_claims_from_evidence") < names.index(
        "audit_release_claim_alignment"
    )
    assert names.index("audit_release_claim_alignment") < names.index(
        "build_release_bundle"
    )
    assert names.index("sync_manuscript_claims_from_evidence") < names.index(
        "rebuild_scientific_data_latex_package"
    )
    assert names.index("generate_publication_sd_figures") < names.index(
        "rebuild_scientific_data_latex_package"
    )
    assert names.index("capture_formal_runtime_environment") < names.index(
        "build_reproducibility_manifests"
    )
    assert names.index("build_reproducibility_manifests") < names.index(
        "build_data_lineage_manifest"
    )
    assert names.index("rebuild_scientific_data_latex_package") < names.index(
        "build_release_bundle"
    )
    assert names.index("build_preaudit_dataset_metadata") < names.index(
        "run_project_milestone_audit"
    )
    assert names.index("run_project_milestone_audit") < names.index(
        "refresh_release_artifacts"
    )
    english_step = steps[names.index("build_english_release_view")]
    assert "experiments/test_v17/10_english_translation" in english_step.cmd


def test_release_metadata_binding_and_archive_inputs_are_current() -> None:
    steps = pipeline.build_steps("experiments/test_v17")
    by_name = {step.name: step for step in steps}

    milestone = by_name["run_project_milestone_audit"]
    assert "reports/preaudit_dataset_metadata_v1.2_sd_core.json" in milestone.cmd

    refresh = by_name["refresh_release_artifacts"]
    dataset_index = refresh.cmd.index("--dataset") + 1
    source_index = refresh.cmd.index("--source-dataset") + 1
    assert refresh.cmd[dataset_index] == pipeline.ENGLISH_DATASET
    assert refresh.cmd[source_index] == pipeline.DATASET

    binding = by_name["build_sd_evidence_binding_manifest"]
    assert pipeline.DATASET in (binding.input_paths or [])
    assert "metadata/data_lineage_manifest.json" in (binding.input_paths or [])
    assert "reports/independent_solver_validation_v1.2_sd_core.json" in (
        binding.input_paths or []
    )

    claim_sync = by_name["sync_manuscript_claims_from_evidence"]
    for report in (
        "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
        "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.json",
        "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
        "reports/independent_solver_validation_v1.2_sd_core.json",
    ):
        assert report in claim_sync.cmd
        assert report in (claim_sync.input_paths or [])

    claim_alignment = by_name["audit_release_claim_alignment"]
    for report in (
        "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
        "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.json",
        "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
        "reports/independent_solver_validation_v1.2_sd_core.json",
    ):
        assert report in (claim_alignment.input_paths or [])

    bundle = by_name["build_release_bundle"]
    assert "metadata/evidence_binding_manifest.json" in (bundle.input_paths or [])
    assert "reports/sd_visualization_completeness_v1.2_sd_core.json" in (
        bundle.input_paths or []
    )
    assert "metadata/archive_metadata.json" in (bundle.input_paths or [])
    assert (
        "release/GridInstruct_v1.2_sd_core_release_candidate.tar.gz.sha256"
        in bundle.expected_outputs
    )


def test_resume_starts_at_first_non_current_step(tmp_path, monkeypatch) -> None:
    steps = [
        pipeline.Step("first", "x", ["true"], ["a"]),
        pipeline.Step("second", "x", ["true"], ["b"]),
        pipeline.Step("third", "x", ["true"], ["c"]),
    ]
    monkeypatch.setattr(
        pipeline,
        "step_completion_errors",
        lambda step, _run_root: [] if step.name == "first" else [f"stale:{step.name}"],
    )
    assert pipeline.resume_index(steps, tmp_path) == 1


def test_pipeline_has_no_downstream_input_cycles_or_shared_report_outputs() -> None:
    steps = pipeline.build_steps("experiments/test_v17")
    producers: dict[str, list[int]] = {}
    for index, step in enumerate(steps):
        for output in step.expected_outputs:
            producers.setdefault(output, []).append(index)

    duplicate_outputs = {
        output: indexes for output, indexes in producers.items() if len(indexes) > 1
    }
    assert duplicate_outputs == {}

    for index, step in enumerate(steps):
        for input_path in step.input_paths or []:
            if any(token in input_path for token in "*?["):
                continue
            assert not (
                input_path in producers and min(producers[input_path]) > index
            ), f"{step.name} fingerprints downstream output {input_path}"


def test_truth_merge_excludes_unbound_legacy_external_scenarios() -> None:
    steps = {step.name: step for step in pipeline.build_steps("experiments/test_v17")}
    merge = steps["merge_scenario_sources"]
    command = " ".join(merge.cmd)

    assert "simulation_outputs/power_flow/scenarios_converged_core.json" in command
    assert (
        "simulation_outputs/topology_stress/extended_topology_scenarios.json" in command
    )
    assert "legacy_topology_scenarios_v1.json" not in command
    assert "frozen_external_topology_migration_v2.json" not in command


def test_opf_steps_consume_the_source_bound_core_projection() -> None:
    steps = {step.name: step for step in pipeline.build_steps("experiments/test_v17")}
    candidate_step = steps["generate_ieee14_opf_secure_candidates"]
    assert "scripts/generate_opf_secure_candidate_augmentation.py" in (
        candidate_step.input_paths or []
    )
    assert "scripts/append_opf_closed_loop_auxiliary_records.py" in (
        candidate_step.input_paths or []
    )
    assert "scripts/generate_grid_scenarios.py" in (
        candidate_step.input_paths or []
    )
    for name in (
        "append_realistic_opf_closed_loop",
        "validate_cross_solver_power_flow",
        "build_native_independent_solver_case_manifest",
        "validate_opf_action_uncertainty_stress",
    ):
        command = " ".join(steps[name].cmd)
        assert pipeline.OPF_SOURCE_SCENARIOS in command
        assert "simulation_outputs/contingency/scenarios_converged.json" not in command


def test_uncertainty_stress_is_fail_closed_and_hash_bound() -> None:
    steps = {step.name: step for step in pipeline.build_steps("experiments/test_v17")}
    step = steps["validate_opf_action_uncertainty_stress"]
    command = " ".join(step.cmd)

    assert "--allow-diagnostic" not in command
    assert "opf_closed_loop_commit_v1.2_sd_core.json" in command
    assert (
        "simulation_outputs/opf_closed_loop/"
        "opf_action_uncertainty_stress_cases_v1.json"
    ) in step.expected_outputs
    assert (
        "reports/opf_action_uncertainty_stress_v1.2_sd_core.json"
        in step.expected_outputs
    )
    assert (
        "simulation_outputs/opf_closed_loop/"
        "opf_action_uncertainty_stress_commit_v1.json"
    ) in step.expected_outputs
