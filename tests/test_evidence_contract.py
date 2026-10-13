from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evidence_contract import (  # noqa: E402
    dataset_scenario_migration_matches,
    power_evidence_gate_results,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_passing_power_evidence(root: Path) -> None:
    candidate_rows = [
        {"scenario_id": f"candidate-{index}"} for index in range(120)
    ]
    inputs = {
        "simulation_outputs/power_flow/scenarios_all.json": [],
        "simulation_outputs/power_flow/scenarios_converged_core.json": [],
        "simulation_outputs/contingency/scenarios_converged.json": [],
        "reports/simulation_report.json": {"status": "pass"},
        "metadata/pglib_opf_v23_07_case_registry.json": {
            "status": "pass",
            "commit": "pglib-commit",
            "license_sha256": "license-digest",
        },
        "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json": {
            "results": []
        },
        "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json": {
            "status": "pass"
        },
        "simulation_outputs/opf_closed_loop/ieee14_ieee118_source_scenarios_v1.json": [],
        "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json": candidate_rows,
    }
    for relative, payload in inputs.items():
        write_json(root / relative, payload)
    validator = root / "scripts/validate_opf_action_uncertainty_stress.py"
    validator.parent.mkdir(parents=True, exist_ok=True)
    validator.write_text("# fixed validator\n", encoding="utf-8")
    constant_power_factor_validator = (
        root
        / "scripts/validate_opf_action_constant_power_factor_stress.py"
    )
    constant_power_factor_validator.write_text(
        "# fixed constant-power-factor validator\n",
        encoding="utf-8",
    )
    generator = root / "scripts/generate_opf_secure_candidate_augmentation.py"
    generator.write_text("# fixed candidate generator\n", encoding="utf-8")
    opf_builder = root / "scripts/append_opf_closed_loop_auxiliary_records.py"
    opf_builder.write_text("# fixed robust OPF builder\n", encoding="utf-8")
    scenario_builder = root / "scripts/generate_grid_scenarios.py"
    scenario_builder.write_text("# fixed AC scenario builder\n", encoding="utf-8")
    candidate_path = (
        root
        / "simulation_outputs/opf_closed_loop/"
        "ieee14_secure_candidate_scenarios_v1.json"
    )
    candidate_screening = [
        {
            "scenario_id": row["scenario_id"],
            "status": "pass",
            "post_action_n1_summary": {
                "requested_checks": 7,
                "completed_checks": 7,
                "secure_checks": 7,
                "all_control_feasibility_checks_pass": True,
            },
            "active_load_uncertainty_construction_gate": {
                "status": "pass",
                "registered_case_count": 48,
                "safe_case_count": 48,
                "denominator_complete": True,
                "minimum_worst_voltage_margin_pu": 0.01,
                "minimum_thermal_margin_percent": 10.0,
            },
            "constant_power_factor_uncertainty_construction_gate": {
                "status": "pass",
                "registered_case_count": 48,
                "safe_case_count": 48,
                "denominator_complete": True,
                "minimum_worst_voltage_margin_pu": 0.01,
                "minimum_thermal_margin_percent": 10.0,
            },
            "load_uncertainty_construction_gate": {
                "status": "pass",
                "load_model_count": 2,
                "registered_case_count": 96,
                "safe_case_count": 96,
                "denominator_complete": True,
                "minimum_worst_voltage_margin_pu": 0.01,
                "minimum_thermal_margin_percent": 10.0,
            },
            "relative_violation_reduction": 1.0,
        }
        for row in candidate_rows
    ]
    write_json(
        root / "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
        {
            "contract": "ieee14-opf-robust-secure-candidate-augmentation-v4",
            "status": "pass",
            "registered_candidate_count": 35200,
            "registered_unique_scenario_count": 35200,
            "screened_pass_count": 120,
            "robust_gate_pass_count": 125,
            "robust_gate_pass_not_selected_count": 5,
            "target_passed_candidates": 120,
            "screening_error_count": 0,
            "load_levels": sorted(
                {round(0.60 + 0.01 * index, 3) for index in range(13)}
                | {round(0.620 + 0.001 * index, 3) for index in range(11)}
            ),
            "generator_factors": [
                round(0.50 + 0.01 * index, 2)
                for index in range(101)
                if index != 50
            ],
            "line_pairs": [[1, index] for index in range(2, 18)],
            "passed_line_pairs": [[1, 6], [1, 9]],
            "passed_diversity": {
                "status": "pass",
                "minimum_load_levels": 3,
                "minimum_generator_factors": 5,
                "minimum_line_pairs": 1,
                "observed_load_levels": 3,
                "observed_generator_factors": 100,
                "observed_line_pairs": 2,
            },
            "candidate_acceptance_gate": {
                "pre_contingency_loading_margins_percent": [90.0],
                "registered_n1_states_per_candidate": 7,
                "load_models": [
                    "active_only_with_source_reactive_load",
                    "uniform_constant_power_factor",
                ],
                "load_perturbations": [
                    -0.10,
                    -0.05,
                    -0.02,
                    0.02,
                    0.05,
                    0.10,
                ],
                "uncertainty_states_per_model_per_candidate": 48,
                "uncertainty_states_per_candidate": 96,
                "minimum_worst_voltage_margin_pu": 0.001,
                "minimum_thermal_margin_percent": 0.0,
                "denominator_policy": "all registered states must pass",
            },
            "hard_gates": {
                "target_passed_candidate_count_met": True,
                "deterministic_exact_target_selection_completed": True,
                "passed_candidate_diversity_met": True,
                "passed_candidate_denominators_complete": True,
                "all_passed_candidates_source_bound": True,
            },
            "implementation": [
                {
                    "path": "scripts/generate_opf_secure_candidate_augmentation.py",
                    "sha256": digest(generator),
                },
                {
                    "path": "scripts/append_opf_closed_loop_auxiliary_records.py",
                    "sha256": digest(opf_builder),
                },
                {
                    "path": "scripts/generate_grid_scenarios.py",
                    "sha256": digest(scenario_builder),
                },
            ],
            "output_scenarios": {
                "path": (
                    "simulation_outputs/opf_closed_loop/"
                    "ieee14_secure_candidate_scenarios_v1.json"
                ),
                "sha256": digest(candidate_path),
                "record_count": 120,
            },
            "output_scenario_ids": [
                row["scenario_id"] for row in candidate_rows
            ],
            "screening_outcomes": candidate_screening,
        },
    )

    core_input_paths = {
        "attempts": "simulation_outputs/power_flow/scenarios_all.json",
        "converged_core": "simulation_outputs/power_flow/scenarios_converged_core.json",
        "complete_truth": "simulation_outputs/contingency/scenarios_converged.json",
        "simulation_report": "reports/simulation_report.json",
        "case_registry": "metadata/pglib_opf_v23_07_case_registry.json",
    }
    write_json(
        root / "reports/core_n1_denominator_v1.2_sd_core.json",
        {
            "contract_version": "gridinstruct-core4-line-n1-denominator-v2",
            "status": "pass",
            "scope": {
                "systems": ["ieee14", "ieee30", "ieee57", "ieee118"],
                "contingency_type": "n1_branch_outage",
                "element_table": "line",
                "registered_system_load_strata": 28,
            },
            "inputs": {
                name: {"path": relative, "sha256": digest(root / relative)}
                for name, relative in core_input_paths.items()
            },
            "source_contract": {
                "repository_commit": "pglib-commit",
                "license_sha256": "license-digest",
            },
            "denominator": {
                "expected_attempt_count": 1896,
                "observed_attempt_count": 1896,
                "unique_observed_identity_count": 1896,
            },
            "outcomes": {
                "converged_attempt_count": 1772,
                "core_converged_truth_count": 1772,
                "complete_truth_core_n1_count": 1772,
            },
            "set_audit": {
                "missing_identity_count": 0,
                "extra_identity_count": 0,
                "duplicate_identity_count": 0,
                "duplicate_scenario_id_count": 0,
            },
            "hard_gates": {
                name: True
                for name in (
                    "scope_is_exactly_four_core_networks",
                    "registered_load_levels_are_formal",
                    "pglib_registry_and_loaded_sources_match",
                    "simulation_generation_report_passed",
                    "registered_load_levels_match_generation_report",
                    "expected_and_attempt_identity_sets_match",
                    "attempt_identity_keys_unique",
                    "attempt_scenario_ids_unique_and_nonempty",
                    "attempt_rows_are_contract_complete",
                    "converged_attempt_subset_matches_core_truth",
                    "complete_truth_subset_matches_core_truth",
                    "core_truth_ids_unique_and_nonempty",
                    "complete_truth_ids_unique_and_nonempty",
                    "failed_attempts_are_excluded_from_released_truth",
                    "all_input_hashes_present",
                )
            },
        },
    )

    manifest_cases = [
        {
            "case_id": f"case-{index}",
            "network_model": "ieee14" if index < 80 else "ieee118",
        }
        for index in range(160)
    ]
    manifest_path = (
        root / "simulation_outputs/independent_solver/case_manifest_v1.json"
    )
    write_json(
        manifest_path,
        {
            "contract_version": (
                "gridinstruct-independent-solver-case-manifest-v1"
            ),
            "inputs": {
                "scenarios": {
                    "path": (
                        "simulation_outputs/opf_closed_loop/"
                        "ieee14_ieee118_source_scenarios_v1.json"
                    ),
                    "sha256": digest(
                        root
                        / "simulation_outputs/opf_closed_loop/"
                        "ieee14_ieee118_source_scenarios_v1.json"
                    ),
                },
                "opf_results": {
                    "path": (
                        "simulation_outputs/opf_closed_loop/"
                        "auxiliary_opf_results.json"
                    ),
                    "sha256": digest(
                        root
                        / "simulation_outputs/opf_closed_loop/"
                        "auxiliary_opf_results.json"
                    ),
                },
                "additional_scenarios": [
                    {
                        "path": (
                            "simulation_outputs/opf_closed_loop/"
                            "ieee14_secure_candidate_scenarios_v1.json"
                        ),
                        "sha256": digest(
                            root
                            / "simulation_outputs/opf_closed_loop/"
                            "ieee14_secure_candidate_scenarios_v1.json"
                        ),
                    }
                ],
                "source_registry": {
                    "kind": "pglib-opf-fixed-commit",
                    "commit": "pglib-commit",
                    "license_sha256": "license-digest",
                },
            },
            "case_count": 160,
            "excluded_case_count": 0,
            "cases": manifest_cases,
        },
    )
    raw_path = (
        root / "reports/independent_solver_raw_evidence_v1.2_sd_core.json"
    )
    write_json(
        raw_path,
        {
            "contract_version": (
                "gridinstruct-independent-solver-evidence-v1"
            ),
            "case_manifest": {
                "path": (
                    "simulation_outputs/independent_solver/"
                    "case_manifest_v1.json"
                ),
                "sha256": digest(manifest_path),
            },
            "case_count": 160,
            "manifest_excluded_case_count": 0,
            "cases": manifest_cases,
        },
    )
    write_json(
        root / "reports/independent_solver_validation_v1.2_sd_core.json",
        {
            "status": "pass",
            "solver_evidence_class": "independent_solver",
            "case_count": 160,
            "passed_case_count": 160,
            "errors": [],
            "case_errors": [],
            "input": {
                "path": (
                    "reports/"
                    "independent_solver_raw_evidence_v1.2_sd_core.json"
                ),
                "sha256": digest(raw_path),
            },
            "case_manifest_binding": {
                "match": True,
                "case_ids_match": True,
                "actual_sha256": digest(manifest_path),
                "claimed_sha256": digest(manifest_path),
            },
        },
    )

    case_path = (
        root
        / "simulation_outputs/opf_closed_loop/"
        "opf_action_uncertainty_stress_cases_v1.json"
    )
    scenario_paths = [
        "simulation_outputs/opf_closed_loop/ieee14_ieee118_source_scenarios_v1.json",
        "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
    ]
    report_path = (
        root / "reports/opf_action_uncertainty_stress_v1.2_sd_core.json"
    )
    uncertainty_inputs = {
        "opf_results": {
            "sha256": digest(
                root
                / "simulation_outputs/opf_closed_loop/"
                "auxiliary_opf_results.json"
            )
        },
        "opf_commit": {
            "sha256": digest(
                root
                / "simulation_outputs/opf_closed_loop/"
                "opf_closed_loop_commit_v1.2_sd_core.json"
            )
        },
        "scenario_manifests": [
            {
                "path": relative,
                "sha256": digest(root / relative),
            }
            for relative in scenario_paths
        ],
    }
    write_json(
        case_path,
        {
            "contract_version": "opf-action-active-load-uncertainty-stress-v2",
            "input_artifacts": uncertainty_inputs,
            "registered_case_count": 7680,
            "cases": [
                {"case_id": f"stress-{index}"} for index in range(7680)
            ],
        },
    )
    write_json(
        report_path,
        {
            "contract_version": "opf-action-active-load-uncertainty-stress-v2",
            "status": "pass",
            "integrity_status": "pass",
            "robustness_status": "pass",
            "registered_denominator": {
                "published_action_count": 160,
                "stress_point_count_per_action": 6,
                "expected_base_case_count": 960,
                "expected_registered_n1_case_count": 6720,
                "expected_total_case_count": 7680,
                "observed_total_case_count": 7680,
                "complete": True,
            },
            "base_stress_summary": {
                "registered_case_count": 960,
                "converged_case_count": 960,
                "safe_case_count": 960,
                "convergence_rate": 1.0,
                "safety_rate_over_registered_denominator": 1.0,
                "minimum_worst_voltage_margin_pu": 0.01,
                "minimum_thermal_margin_percent": 10.0,
            },
            "registered_n1_stress_summary": {
                "registered_case_count": 6720,
                "converged_case_count": 6720,
                "safe_case_count": 6720,
                "convergence_rate": 1.0,
                "safety_rate_over_registered_denominator": 1.0,
                "minimum_worst_voltage_margin_pu": 0.01,
                "minimum_thermal_margin_percent": 10.0,
            },
            "hard_gates": {
                name: True
                for name in (
                    "input_contract_complete",
                    "registered_denominator_complete",
                    "base_stress_convergence_rate_is_one",
                    "base_stress_safety_rate_is_one",
                    "registered_n1_convergence_rate_is_one",
                    "registered_n1_safety_rate_is_one",
                    "worst_voltage_margin_meets_published_minimum",
                    "worst_thermal_margin_nonnegative",
                )
            },
            "input_artifacts": uncertainty_inputs,
            "implementation_sha256": digest(validator),
        },
    )
    write_json(
        root
        / "simulation_outputs/opf_closed_loop/"
        "opf_action_uncertainty_stress_commit_v1.json",
        {
            "status": "pass",
            "robustness_status": "pass",
            "inputs": uncertainty_inputs,
            "artifacts": {
                "cases": {
                    "path": (
                        "simulation_outputs/opf_closed_loop/"
                        "opf_action_uncertainty_stress_cases_v1.json"
                    ),
                    "sha256": digest(case_path),
                },
                "report": {
                    "path": (
                        "reports/"
                        "opf_action_uncertainty_stress_v1.2_sd_core.json"
                    ),
                    "sha256": digest(report_path),
                },
            },
        },
    )

    constant_power_factor_case_path = (
        root
        / "simulation_outputs/opf_closed_loop/"
        "opf_action_constant_power_factor_stress_cases_v1.json"
    )
    constant_power_factor_report_path = (
        root
        / "reports/"
        "opf_action_constant_power_factor_stress_v1.2_sd_core.json"
    )
    write_json(
        constant_power_factor_case_path,
        {
            "contract_version": (
                "opf-action-constant-power-factor-stress-v1"
            ),
            "input_artifacts": uncertainty_inputs,
            "registered_case_count": 7680,
            "cases": [
                {
                    "case_id": (
                        f"constant_power_factor::constant-pf-{index}"
                    )
                }
                for index in range(7680)
            ],
        },
    )
    constant_power_factor_summary = {
        "registered_case_count": 960,
        "converged_case_count": 960,
        "safe_case_count": 960,
        "convergence_rate": 1.0,
        "safety_rate_over_registered_denominator": 1.0,
        "minimum_worst_voltage_margin_pu": 0.01,
        "minimum_thermal_margin_percent": 10.0,
    }
    constant_power_factor_n1_summary = {
        **constant_power_factor_summary,
        "registered_case_count": 6720,
        "converged_case_count": 6720,
        "safe_case_count": 6720,
    }
    write_json(
        constant_power_factor_report_path,
        {
            "contract_version": (
                "opf-action-constant-power-factor-stress-v1"
            ),
            "status": "pass",
            "integrity_status": "pass",
            "robustness_status": "pass",
            "registered_denominator": {
                "published_action_count": 160,
                "stress_point_count_per_action": 6,
                "expected_base_case_count": 960,
                "expected_registered_n1_case_count": 6720,
                "expected_total_case_count": 7680,
                "observed_total_case_count": 7680,
                "complete": True,
            },
            "base_stress_summary": constant_power_factor_summary,
            "registered_n1_stress_summary": (
                constant_power_factor_n1_summary
            ),
            "hard_gates": {
                name: True
                for name in (
                    "input_contract_complete",
                    "registered_denominator_complete",
                    "base_stress_convergence_rate_is_one",
                    "base_stress_safety_rate_is_one",
                    "registered_n1_convergence_rate_is_one",
                    "registered_n1_safety_rate_is_one",
                    "worst_voltage_margin_meets_published_minimum",
                    "worst_thermal_margin_nonnegative",
                    "all_registered_device_limits_pass",
                    "all_incremental_active_balances_close",
                )
            },
            "input_artifacts": uncertainty_inputs,
            "implementation": [
                {
                    "path": (
                        "scripts/"
                        "validate_opf_action_constant_power_factor_stress.py"
                    ),
                    "sha256": digest(constant_power_factor_validator),
                },
                {
                    "path": (
                        "scripts/"
                        "validate_opf_action_uncertainty_stress.py"
                    ),
                    "sha256": digest(validator),
                },
            ],
        },
    )
    write_json(
        root
        / "simulation_outputs/opf_closed_loop/"
        "opf_action_constant_power_factor_stress_commit_v1.json",
        {
            "status": "pass",
            "robustness_status": "pass",
            "inputs": uncertainty_inputs,
            "artifacts": {
                "cases": {
                    "path": (
                        "simulation_outputs/opf_closed_loop/"
                        "opf_action_constant_power_factor_stress_cases_v1.json"
                    ),
                    "sha256": digest(constant_power_factor_case_path),
                },
                "report": {
                    "path": (
                        "reports/"
                        "opf_action_constant_power_factor_stress_v1.2_sd_core.json"
                    ),
                    "sha256": digest(constant_power_factor_report_path),
                },
            },
        },
    )


def passing_migration() -> dict:
    return {
        "status": "pass",
        "records_after": 1,
        "hard_gates": {
            "record_cardinality_preserved": True,
            "record_ids_preserved": True,
            "all_scenario_links_authoritative": True,
        },
    }


def test_scenario_migration_binding_accepts_authoritative_links() -> None:
    assert dataset_scenario_migration_matches(
        passing_migration(),
        [{"id": "a", "scenario_id": "s1"}],
        [{"scenario_id": "s1"}],
    )


def test_scenario_migration_binding_rejects_dangling_links() -> None:
    assert not dataset_scenario_migration_matches(
        passing_migration(),
        [{"id": "a", "scenario_id": "missing"}],
        [{"scenario_id": "s1"}],
    )


def test_scenario_migration_binding_rejects_failed_hard_gate() -> None:
    migration = passing_migration()
    migration["hard_gates"]["record_ids_preserved"] = False

    assert not dataset_scenario_migration_matches(
        migration,
        [{"id": "a", "scenario_id": "s1"}],
        [{"scenario_id": "s1"}],
    )


def test_power_evidence_contract_requires_all_exact_denominators(
    tmp_path: Path,
) -> None:
    write_passing_power_evidence(tmp_path)

    results = power_evidence_gate_results(tmp_path)

    assert all(result["passed"] for result in results.values())


def test_power_evidence_contract_fails_closed_on_core_denominator_drift(
    tmp_path: Path,
) -> None:
    write_passing_power_evidence(tmp_path)
    report_path = tmp_path / "reports/core_n1_denominator_v1.2_sd_core.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["denominator"]["observed_attempt_count"] = 1895
    write_json(report_path, report)

    result = power_evidence_gate_results(tmp_path)["core_n1_denominator"]

    assert result["passed"] is False
    assert "observed_attempt_count_not_1896" in result["errors"]


def test_power_evidence_contract_rejects_candidate_implementation_drift(
    tmp_path: Path,
) -> None:
    write_passing_power_evidence(tmp_path)
    (
        tmp_path / "scripts/generate_grid_scenarios.py"
    ).write_text("# changed AC scenario builder\n", encoding="utf-8")

    result = power_evidence_gate_results(tmp_path)[
        "opf_robust_candidate_register"
    ]

    assert result["passed"] is False
    assert "implementation_hashes_mismatch" in result["errors"]


def test_power_evidence_contract_rejects_constant_power_factor_drift(
    tmp_path: Path,
) -> None:
    write_passing_power_evidence(tmp_path)
    report_path = (
        tmp_path
        / "reports/"
        "opf_action_constant_power_factor_stress_v1.2_sd_core.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["registered_denominator"]["observed_total_case_count"] = 7679
    write_json(report_path, report)

    result = power_evidence_gate_results(tmp_path)[
        "opf_action_constant_power_factor_stress"
    ]

    assert result["passed"] is False
    assert "observed_total_case_count_mismatch" in result["errors"]
    assert "commit_report_hash_mismatch" in result["errors"]
