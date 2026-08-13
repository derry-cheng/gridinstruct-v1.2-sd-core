#!/usr/bin/env python3
"""Bind SD evidence reports to the current data, split, scenario, model, and code files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from gridinstruct_utils import read_json, read_jsonl
from query_contract import canonical, query_projection


CURRENT_INPUTS = {
    "dataset_cn": "data/gridinstruct_v1.2_sd_core.jsonl",
    "dataset_en": "data/gridinstruct_v1.2_sd_core_en.jsonl",
    "train_cn": "data/v1.2_sd_core_train.jsonl",
    "validation_cn": "data/v1.2_sd_core_validation.jsonl",
    "test_cn": "data/v1.2_sd_core_test.jsonl",
    "ood_cn": "data/v1.2_sd_core_ood_test.jsonl",
    "train_en": "data/v1.2_sd_core_train_en.jsonl",
    "validation_en": "data/v1.2_sd_core_validation_en.jsonl",
    "test_en": "data/v1.2_sd_core_test_en.jsonl",
    "ood_en": "data/v1.2_sd_core_ood_test_en.jsonl",
    "scenario_truth": "simulation_outputs/contingency/scenarios_converged.json",
    "core_scenario_truth": "simulation_outputs/power_flow/scenarios_converged_core.json",
    "scenario_rebuild_manifest": "simulation_outputs/contingency/scenario_rebuild_manifest.json",
    "external_topology_truth": "simulation_outputs/topology_stress/extended_topology_scenarios.json",
    "external_topology_attempts": "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
    "external_normal_common_support": "simulation_outputs/topology_stress/matched_normal_pool_pegase1354.json",
    "external_normal_common_support_audit": "reports/matched_normal_audit_pegase1354.json",
    "v16_candidate_pool": "data/gridinstruct_v1.2_paper_candidate_actionable_plus15_v16.jsonl",
    "frozen_augmentation_contract": "metadata/frozen_augmentation_contract_v1.2.json",
    "frozen_plus11": "data/frozen_sources/gridinstruct_v1.2_plus11_full.jsonl",
    "frozen_implicit_ticket_rows": "data/frozen_sources/operation_implicit_ticket_llm_v1.jsonl",
    "pglib_case_registry": "metadata/pglib_opf_v23_07_case_registry.json",
    "equipment_rating_provenance": "reports/equipment_rating_provenance_v1.2_sd_core.json",
    "core_n1_attempts": "simulation_outputs/power_flow/scenarios_all.json",
    "core_n1_generation_report": "reports/simulation_report.json",
    "core_n1_denominator": "reports/core_n1_denominator_v1.2_sd_core.json",
    "transformer_contract": "reports/pandapower_transformer_contract_all_networks_v1.2_sd_core.json",
    "external_matched_strata": "reports/external_network_severity_matched_strata_v1.2_sd_core.json",
    "explicit_high_rating_sensitivity": "reports/explicit_high_rating_sensitivity_v1.2_sd_core.json",
    "independent_solver_manifest": "metadata/independent_solver_case_manifest_v1.json",
    "independent_solver_raw": "reports/independent_solver_raw_evidence_v1.2_sd_core_rebound.json",
    "independent_solver_validation": "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
    "frozen_topology_v1": "data/frozen_sources/extended_topology_instruction_v1.jsonl",
    "frozen_topology_v2": "data/frozen_sources/extended_topology_instruction_v2.jsonl",
    "frozen_topology_migration": "reports/frozen_external_topology_migration_v2.json",
    "frozen_topology_checksums_v2": "metadata/frozen_source_checksums_v2.json",
    "scenario_truth_attempts": "reports/scenario_truth_rebuild_attempts_v1.2_sd_core.json",
    "dataset_scenario_link_migration": "reports/dataset_scenario_link_migration_v1.2_sd_core.json",
    "opf_results": "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
    "opf_commit": "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
    "opf_source_scenarios": "simulation_outputs/opf_closed_loop/ieee14_ieee118_source_scenarios_v1.json",
    "opf_additional_scenarios": "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
    "opf_additional_scenarios_report": "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    "opf_uncertainty_cases": "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json",
    "opf_uncertainty_commit": "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_commit_v1.json",
    "opf_uncertainty_validator": "scripts/validate_opf_action_uncertainty_stress.py",
    "opf_constant_power_factor_cases": (
        "simulation_outputs/opf_closed_loop/"
        "opf_action_constant_power_factor_stress_cases_v1.json"
    ),
    "opf_constant_power_factor_commit": (
        "simulation_outputs/opf_closed_loop/"
        "opf_action_constant_power_factor_stress_commit_v1.json"
    ),
    "opf_constant_power_factor_validator": (
        "scripts/validate_opf_action_constant_power_factor_stress.py"
    ),
    "rule_dictionary": "rules/regulation_rules.json",
    "rule_clause_matrix": "metadata/rule_clause_matrix.json",
    "dataset_metadata": "metadata/dataset_metadata.json",
    "compliance_recompute": "reports/compliance_label_recompute_v1.2_sd_core.json",
    "canonical_promotion": "metadata/canonical_dataset_promotion_manifest.json",
    "canonical_immutability": "reports/canonical_dataset_immutability_v1.2_sd_core.json",
    "source_english_alignment": "reports/source_english_alignment_v1.2_sd_core.json",
    "proxy_stress_split_manifest": "reports/proxy_stress_split_manifest_v1.2_sd_core.json",
    "proxy_stress_english_alignment": "reports/source_english_proxy_stress_alignment_v1.2_sd_core.json",
    "proxy_stress_baseline": "benchmark/v1.2_sd_core_proxy_stress_report.json",
    "data_lineage_manifest": "metadata/data_lineage_manifest.json",
    "third_party_asset_inventory": "metadata/third_party_asset_inventory.json",
    "code_manifest": "metadata/code_manifest.json",
    "environment_lock": "metadata/environment_lock.json",
    "historical_runtime_environment": "metadata/runtime_environment.json",
    "formal_runtime_environment": "metadata/formal_runtime_environment.json",
    "formal_environment_specification": "environment.yml",
    "requirements_lock": "metadata/requirements-lock.txt",
    "environment_freeze": "metadata/environment-freeze.txt",
    "model_revision_manifest": "metadata/model_revision_manifest.json",
}

EVIDENCE_PATHS = {
    "query_repair": "reports/query_result_consistency_repair_v1.2_sd_core.json",
    "query_independent": "reports/independent_query_truth_validation_v1.2_sd_core.json",
    "cross_solver": "reports/cross_solver_power_flow_v1.2_sd_core.json",
    "equipment_rating_provenance": "reports/equipment_rating_provenance_v1.2_sd_core.json",
    "core_n1_denominator": "reports/core_n1_denominator_v1.2_sd_core.json",
    "transformer_contract": "reports/pandapower_transformer_contract_all_networks_v1.2_sd_core.json",
    "external_normal_common_support": "reports/matched_normal_audit_pegase1354.json",
    "external_matched_strata": "reports/external_network_severity_matched_strata_v1.2_sd_core.json",
    "explicit_high_rating_sensitivity": "reports/explicit_high_rating_sensitivity_v1.2_sd_core.json",
    # The current release uses the manifest-rebound native-source replay.
    # Keep the legacy fallback below for isolated historical fixtures, but
    # make the active evidence path explicit here.
    "native_independent_solver": "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
    "opf_robust_candidate_register": "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
    "opf_action_uncertainty_stress": "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
    "opf_action_constant_power_factor_stress": (
        "reports/opf_action_constant_power_factor_stress_v1.2_sd_core.json"
    ),
    "bootstrap": "reports/group_cluster_bootstrap_v1.2_sd_core.json",
    "human_review": "reports/expert_review_execution_check_v1.2_sd_core.json",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def projection_sha256(path: Path) -> str:
    payload = canonical(query_projection(read_jsonl(path))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evidence_entry(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        return {"path": relative, "exists": False, "sha256": None, "status": None}
    payload = read_json(path)
    return {
        "path": relative,
        "exists": True,
        "sha256": file_sha256(path),
        "status": payload.get("status") or payload.get("overall_status"),
    }


def _read_json_object(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _hash_or_none(root: Path, relative: str) -> str | None:
    path = root / relative
    return file_sha256(path) if path.is_file() else None


def _artifact_hash(payload: dict[str, Any], key: str) -> str | None:
    entry = payload.get(key) or {}
    return str(entry.get("sha256") or "") or None


def _gate_contract_errors(
    payload: dict[str, Any],
    required_names: set[str],
) -> list[str]:
    gates = payload.get("hard_gates")
    if not isinstance(gates, dict):
        return ["hard_gates_missing"]
    errors = [
        f"hard_gate_missing:{name}"
        for name in sorted(required_names - set(gates))
    ]
    errors.extend(
        f"hard_gate_failed:{name}"
        for name in sorted(required_names & set(gates))
        if gates.get(name) is not True
    )
    return errors


def _integer(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: Any, default: float = -1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _constant_power_factor_stress_errors(root: Path) -> list[str]:
    report_path = EVIDENCE_PATHS[
        "opf_action_constant_power_factor_stress"
    ]
    cases_path = CURRENT_INPUTS["opf_constant_power_factor_cases"]
    commit_path = CURRENT_INPUTS["opf_constant_power_factor_commit"]
    report = _read_json_object(root, report_path)
    cases_payload = _read_json_object(root, cases_path)
    commit = _read_json_object(root, commit_path)
    report_hash = _hash_or_none(root, report_path)
    cases_hash = _hash_or_none(root, cases_path)
    errors: list[str] = []
    if not report:
        errors.append("missing_report")
    if not cases_payload:
        errors.append("missing_case_payload")
    if not commit:
        errors.append("missing_commit")
    if report:
        if report.get("contract_version") != (
            "opf-action-constant-power-factor-stress-v1"
        ):
            errors.append("contract_version_mismatch")
        for field in ("status", "integrity_status", "robustness_status"):
            if report.get(field) != "pass":
                errors.append(f"{field}_not_pass")
        denominator = report.get("registered_denominator") or {}
        exact_counts = {
            "published_action_count": 160,
            "stress_point_count_per_action": 6,
            "expected_base_case_count": 960,
            "expected_registered_n1_case_count": 6720,
            "expected_total_case_count": 7680,
            "observed_total_case_count": 7680,
        }
        for field, expected in exact_counts.items():
            if _integer(denominator.get(field)) != expected:
                errors.append(f"{field}_mismatch")
        if denominator.get("complete") is not True:
            errors.append("registered_denominator_incomplete")
        for label, summary, expected_count in (
            ("base", report.get("base_stress_summary") or {}, 960),
            ("n1", report.get("registered_n1_stress_summary") or {}, 6720),
        ):
            if _integer(summary.get("registered_case_count")) != expected_count:
                errors.append(f"{label}_registered_count_mismatch")
            if _integer(summary.get("converged_case_count")) != expected_count:
                errors.append(f"{label}_converged_count_mismatch")
            if _integer(summary.get("safe_case_count")) != expected_count:
                errors.append(f"{label}_safe_count_mismatch")
            if _number(summary.get("convergence_rate")) != 1.0:
                errors.append(f"{label}_convergence_rate_not_one")
            if (
                _number(summary.get("safety_rate_over_registered_denominator"))
                != 1.0
            ):
                errors.append(f"{label}_safety_rate_not_one")
            if _number(summary.get("minimum_worst_voltage_margin_pu")) < 0.001:
                errors.append(f"{label}_minimum_voltage_margin_below_0_001")
            if _number(summary.get("minimum_thermal_margin_percent")) < 0.0:
                errors.append(f"{label}_minimum_thermal_margin_negative")
        errors.extend(
            _gate_contract_errors(
                report,
                {
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
                },
            )
        )
        report_inputs = report.get("input_artifacts") or {}
        for name, relative in (
            ("opf_results", CURRENT_INPUTS["opf_results"]),
            ("opf_commit", CURRENT_INPUTS["opf_commit"]),
        ):
            expected_hash = _hash_or_none(root, relative)
            if (
                expected_hash is None
                or _artifact_hash(report_inputs, name) != expected_hash
            ):
                errors.append(f"report_input_hash_mismatch:{name}")
        observed_scenarios = {
            str(entry.get("path") or ""): str(entry.get("sha256") or "")
            for entry in (report_inputs.get("scenario_manifests") or [])
        }
        expected_scenarios = {
            CURRENT_INPUTS["opf_source_scenarios"]: _hash_or_none(
                root,
                CURRENT_INPUTS["opf_source_scenarios"],
            ),
            CURRENT_INPUTS["opf_additional_scenarios"]: _hash_or_none(
                root,
                CURRENT_INPUTS["opf_additional_scenarios"],
            ),
        }
        if (
            any(value is None for value in expected_scenarios.values())
            or observed_scenarios != expected_scenarios
        ):
            errors.append("report_scenario_hashes_mismatch")
        observed_implementation = {
            str(entry.get("path") or ""): str(entry.get("sha256") or "")
            for entry in (report.get("implementation") or [])
        }
        expected_implementation = {
            CURRENT_INPUTS["opf_constant_power_factor_validator"]: _hash_or_none(
                root,
                CURRENT_INPUTS["opf_constant_power_factor_validator"],
            ),
            CURRENT_INPUTS["opf_uncertainty_validator"]: _hash_or_none(
                root,
                CURRENT_INPUTS["opf_uncertainty_validator"],
            ),
        }
        if (
            any(value is None for value in expected_implementation.values())
            or observed_implementation != expected_implementation
        ):
            errors.append("implementation_hashes_mismatch")
    if cases_payload:
        cases = cases_payload.get("cases") or []
        if cases_payload.get("contract_version") != (
            "opf-action-constant-power-factor-stress-v1"
        ):
            errors.append("case_payload_contract_version_mismatch")
        if (
            _integer(cases_payload.get("registered_case_count")) != 7680
            or len(cases) != 7680
        ):
            errors.append("case_payload_count_not_7680")
        case_ids = [str(row.get("case_id") or "") for row in cases]
        if "" in case_ids or len(set(case_ids)) != 7680:
            errors.append("case_ids_not_unique")
        if any(
            not case_id.startswith("constant_power_factor::")
            for case_id in case_ids
        ):
            errors.append("case_ids_lack_load_model_namespace")
        active_cases_payload = _read_json_object(
            root,
            CURRENT_INPUTS["opf_uncertainty_cases"],
        )
        active_case_ids = {
            str(row.get("case_id") or "")
            for row in (active_cases_payload.get("cases") or [])
        }
        if active_case_ids & set(case_ids):
            errors.append("case_ids_overlap_active_load_model")
        if report and cases_payload.get("input_artifacts") != report.get(
            "input_artifacts"
        ):
            errors.append("case_payload_input_binding_mismatch")
    if commit:
        artifacts = commit.get("artifacts") or {}
        if commit.get("status") != "pass":
            errors.append("commit_status_not_pass")
        if commit.get("robustness_status") != "pass":
            errors.append("commit_robustness_status_not_pass")
        if _artifact_hash(artifacts, "cases") != cases_hash:
            errors.append("commit_cases_hash_mismatch")
        if _artifact_hash(artifacts, "report") != report_hash:
            errors.append("commit_report_hash_mismatch")
        if (artifacts.get("cases") or {}).get("path") != cases_path:
            errors.append("commit_cases_path_mismatch")
        if (artifacts.get("report") or {}).get("path") != report_path:
            errors.append("commit_report_path_mismatch")
        if report and commit.get("inputs") != report.get("input_artifacts"):
            errors.append("commit_input_binding_mismatch")
    return errors


def power_evidence_gate_results(root: Path) -> dict[str, dict[str, Any]]:
    """Evaluate the claim-bearing power evidence families fail-closed."""

    candidate_path = CURRENT_INPUTS["opf_additional_scenarios"]
    candidate_report_path = CURRENT_INPUTS["opf_additional_scenarios_report"]
    candidate_file = root / candidate_path
    candidate_report = _read_json_object(root, candidate_report_path)
    candidate_rows_payload = (
        read_json(candidate_file) if candidate_file.is_file() else []
    )
    candidate_rows = (
        candidate_rows_payload if isinstance(candidate_rows_payload, list) else []
    )
    candidate_hash = _hash_or_none(root, candidate_path)
    candidate_errors: list[str] = []
    if not candidate_report:
        candidate_errors.append("missing_report")
    if candidate_hash is None:
        candidate_errors.append("missing_candidate_manifest")
    if len(candidate_rows) != 120:
        candidate_errors.append("candidate_manifest_count_not_120")
    candidate_ids = [str(row.get("scenario_id") or "") for row in candidate_rows]
    if "" in candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
        candidate_errors.append("candidate_manifest_ids_not_unique")
    if candidate_report:
        if candidate_report.get("contract") != (
            "ieee14-opf-robust-secure-candidate-augmentation-v4"
        ):
            candidate_errors.append("contract_version_mismatch")
        if candidate_report.get("status") != "pass":
            candidate_errors.append("status_not_pass")
        exact_counts = {
            "registered_candidate_count": 35200,
            "registered_unique_scenario_count": 35200,
            "screened_pass_count": 120,
            "target_passed_candidates": 120,
            "screening_error_count": 0,
        }
        for field, expected in exact_counts.items():
            if _integer(candidate_report.get(field)) != expected:
                candidate_errors.append(f"{field}_mismatch")
        if len(candidate_report.get("load_levels") or []) != 22:
            candidate_errors.append("load_level_denominator_not_22")
        if len(candidate_report.get("generator_factors") or []) != 100:
            candidate_errors.append("generator_factor_denominator_not_100")
        if len(candidate_report.get("line_pairs") or []) != 16:
            candidate_errors.append("line_pair_denominator_not_16")
        passed_diversity = candidate_report.get("passed_diversity") or {}
        if _integer(passed_diversity.get("minimum_load_levels")) != 3:
            candidate_errors.append("minimum_passed_load_levels_not_3")
        if _integer(
            passed_diversity.get("minimum_generator_factors")
        ) != 5:
            candidate_errors.append(
                "minimum_passed_generator_factors_not_5"
            )
        if _integer(passed_diversity.get("minimum_line_pairs")) != 1:
            candidate_errors.append("minimum_passed_line_pairs_not_1")
        if _integer(passed_diversity.get("observed_load_levels")) < 3:
            candidate_errors.append("observed_passed_load_levels_below_3")
        if _integer(
            passed_diversity.get("observed_generator_factors")
        ) < 5:
            candidate_errors.append(
                "observed_passed_generator_factors_below_5"
            )
        if _integer(passed_diversity.get("observed_line_pairs")) < 1:
            candidate_errors.append("observed_passed_line_pairs_below_1")
        if len(candidate_report.get("passed_line_pairs") or []) < 1:
            candidate_errors.append("passed_line_pair_list_below_1")
        acceptance_gate = candidate_report.get("candidate_acceptance_gate") or {}
        if acceptance_gate.get("pre_contingency_loading_margins_percent") != [
            90.0
        ]:
            candidate_errors.append("candidate_loading_margin_grid_mismatch")
        if _integer(
            acceptance_gate.get("registered_n1_states_per_candidate")
        ) != 7:
            candidate_errors.append("registered_n1_count_not_7")
        if acceptance_gate.get("load_models") != [
            "active_only_with_source_reactive_load",
            "uniform_constant_power_factor",
        ]:
            candidate_errors.append("candidate_load_model_grid_mismatch")
        if acceptance_gate.get("load_perturbations") != [
            -0.1,
            -0.05,
            -0.02,
            0.02,
            0.05,
            0.1,
        ]:
            candidate_errors.append("load_perturbation_grid_mismatch")
        if _integer(
            acceptance_gate.get("uncertainty_states_per_model_per_candidate")
        ) != 48:
            candidate_errors.append("per_model_uncertainty_denominator_not_48")
        if _integer(acceptance_gate.get("uncertainty_states_per_candidate")) != 96:
            candidate_errors.append("dual_model_uncertainty_denominator_not_96")
        if _number(
            acceptance_gate.get("minimum_worst_voltage_margin_pu")
        ) != 0.001:
            candidate_errors.append(
                "minimum_worst_voltage_margin_requirement_mismatch"
            )
        if _number(
            acceptance_gate.get("minimum_thermal_margin_percent")
        ) != 0.0:
            candidate_errors.append(
                "minimum_thermal_margin_requirement_mismatch"
            )
        if acceptance_gate.get("denominator_policy") != (
            "all registered states must pass"
        ):
            candidate_errors.append("denominator_policy_mismatch")
        robust_gate_pass_count = _integer(
            candidate_report.get("robust_gate_pass_count")
        )
        robust_gate_pass_not_selected_count = _integer(
            candidate_report.get("robust_gate_pass_not_selected_count")
        )
        if robust_gate_pass_count < 120:
            candidate_errors.append("robust_gate_pass_count_below_target")
        if (
            robust_gate_pass_not_selected_count
            != robust_gate_pass_count - 120
        ):
            candidate_errors.append(
                "robust_gate_pass_not_selected_count_mismatch"
            )
        candidate_errors.extend(
            _gate_contract_errors(
                candidate_report,
                {
                    "target_passed_candidate_count_met",
                    "deterministic_exact_target_selection_completed",
                    "passed_candidate_diversity_met",
                    "passed_candidate_denominators_complete",
                    "all_passed_candidates_source_bound",
                },
            )
        )
        expected_implementation = {
            relative: _hash_or_none(root, relative)
            for relative in (
                "scripts/generate_opf_secure_candidate_augmentation.py",
                "scripts/append_opf_closed_loop_auxiliary_records.py",
                "scripts/generate_grid_scenarios.py",
            )
        }
        observed_implementation = {
            str(entry.get("path") or ""): str(entry.get("sha256") or "")
            for entry in (candidate_report.get("implementation") or [])
        }
        if (
            any(value is None for value in expected_implementation.values())
            or observed_implementation != expected_implementation
        ):
            candidate_errors.append("implementation_hashes_mismatch")
        output_binding = candidate_report.get("output_scenarios") or {}
        if (
            output_binding.get("path") != candidate_path
            or output_binding.get("sha256") != candidate_hash
            or _integer(output_binding.get("record_count")) != 120
        ):
            candidate_errors.append("candidate_manifest_binding_mismatch")
        output_ids = [
            str(value) for value in (candidate_report.get("output_scenario_ids") or [])
        ]
        if output_ids != candidate_ids:
            candidate_errors.append("candidate_manifest_id_order_mismatch")
        passed_screening = [
            row
            for row in (candidate_report.get("screening_outcomes") or [])
            if row.get("status") == "pass"
        ]
        if len(passed_screening) != 120:
            candidate_errors.append("passed_screening_count_not_120")
        elif [str(row.get("scenario_id") or "") for row in passed_screening] != (
            candidate_ids
        ):
            candidate_errors.append("passed_screening_id_order_mismatch")
        for index, row in enumerate(passed_screening):
            n1 = row.get("post_action_n1_summary") or {}
            uncertainty = row.get("load_uncertainty_construction_gate") or {}
            if (
                _integer(n1.get("requested_checks")) != 7
                or _integer(n1.get("completed_checks")) != 7
                or _integer(n1.get("secure_checks")) != 7
                or n1.get("all_control_feasibility_checks_pass") is not True
            ):
                candidate_errors.append(f"candidate_n1_gate_failed:{index}")
            if (
                uncertainty.get("status") != "pass"
                or _integer(uncertainty.get("load_model_count")) != 2
                or _integer(uncertainty.get("registered_case_count")) != 96
                or _integer(uncertainty.get("safe_case_count")) != 96
                or uncertainty.get("denominator_complete") is not True
                or _number(
                    uncertainty.get("minimum_worst_voltage_margin_pu")
                )
                < 0.001
                or _number(
                    uncertainty.get("minimum_thermal_margin_percent")
                )
                < 0.0
            ):
                candidate_errors.append(
                    f"candidate_uncertainty_gate_failed:{index}"
                )
            if _number(row.get("relative_violation_reduction")) < 1.0:
                candidate_errors.append(
                    f"candidate_violation_reduction_below_one:{index}"
                )

    native_manifest_path = CURRENT_INPUTS["independent_solver_manifest"]
    native_raw_path = CURRENT_INPUTS["independent_solver_raw"]
    native_validation_path = CURRENT_INPUTS["independent_solver_validation"]
    # Keep the contract compatible with older isolated fixtures while the
    # current release uses the rebound manifest under metadata/.
    legacy_native_paths = {
        "manifest": "simulation_outputs/independent_solver/case_manifest_v1.json",
        "raw": "reports/independent_solver_raw_evidence_v1.2_sd_core.json",
        "validation": "reports/independent_solver_validation_v1.2_sd_core.json",
    }
    if not (root / native_manifest_path).is_file() and (
        root / legacy_native_paths["manifest"]
    ).is_file():
        native_manifest_path = legacy_native_paths["manifest"]
        native_raw_path = legacy_native_paths["raw"]
        native_validation_path = legacy_native_paths["validation"]
    native_manifest = _read_json_object(root, native_manifest_path)
    native_raw = _read_json_object(root, native_raw_path)
    native_validation = _read_json_object(root, native_validation_path)
    native_manifest_hash = _hash_or_none(root, native_manifest_path)
    native_raw_hash = _hash_or_none(root, native_raw_path)
    opf_results_hash = _hash_or_none(root, CURRENT_INPUTS["opf_results"])
    native_rebound = bool(
        native_manifest
        and native_manifest.get("raw_scenario_arrays_present") is False
        and native_manifest.get("replay_reproduction_status")
        == "manifest_rebound_raw_scenario_arrays_external"
    )

    native_errors: list[str] = []
    if native_manifest_hash is None:
        native_errors.append("manifest_file_missing")
    if native_raw_hash is None:
        native_errors.append("raw_file_missing")
    if opf_results_hash is None and not native_rebound:
        native_errors.append("opf_results_missing")
    for name, payload in (
        ("manifest", native_manifest),
        ("raw", native_raw),
        ("validation", native_validation),
    ):
        if not payload:
            native_errors.append(f"missing_{name}")
    if native_validation:
        binding = native_validation.get("case_manifest_binding") or {}
        if native_validation.get("status") != "pass":
            native_errors.append("validation_status_not_pass")
        if native_validation.get("solver_evidence_class") != "independent_solver":
            native_errors.append("solver_not_independent")
        if _integer(native_validation.get("case_count")) != 160:
            native_errors.append("validation_case_count_not_160")
        if _integer(native_validation.get("passed_case_count")) != 160:
            native_errors.append("validation_passed_case_count_not_160")
        if native_validation.get("errors") != []:
            native_errors.append("validation_errors_present")
        if native_validation.get("case_errors") != []:
            native_errors.append("validation_case_errors_present")
        if binding.get("match") is not True:
            native_errors.append("manifest_digest_binding_failed")
        if binding.get("case_ids_match") is not True:
            native_errors.append("manifest_case_id_binding_failed")
        if _artifact_hash(native_validation, "input") != native_raw_hash:
            native_errors.append("validation_raw_hash_mismatch")
        if (native_validation.get("input") or {}).get("path") != native_raw_path:
            native_errors.append("validation_raw_path_mismatch")
        if str(binding.get("actual_sha256") or "") != str(native_manifest_hash or ""):
            native_errors.append("validation_manifest_actual_hash_mismatch")
        if str(binding.get("claimed_sha256") or "") != str(native_manifest_hash or ""):
            native_errors.append("validation_manifest_claimed_hash_mismatch")
    if native_rebound and native_raw:
        dataset_binding = native_raw.get("dataset_binding") or {}
        expected_dataset_hash = _hash_or_none(root, CURRENT_INPUTS["dataset_en"])
        if dataset_binding.get("path") != CURRENT_INPUTS["dataset_en"]:
            native_errors.append("rebound_dataset_path_mismatch")
        if str(dataset_binding.get("sha256") or "") != str(expected_dataset_hash or ""):
            native_errors.append("rebound_dataset_hash_mismatch")
    if native_raw:
        raw_cases = native_raw.get("cases") or []
        raw_ids = [str(row.get("case_id") or "") for row in raw_cases]
        networks = {
            str(row.get("network_model") or "").lower().replace(" ", "")
            for row in raw_cases
        }
        if native_raw.get("contract_version") != (
            "gridinstruct-independent-solver-evidence-v1"
        ):
            native_errors.append("raw_contract_version_mismatch")
        if _integer(native_raw.get("case_count")) != 160 or len(raw_cases) != 160:
            native_errors.append("raw_case_count_not_160")
        if _integer(native_raw.get("manifest_excluded_case_count")) != 0:
            native_errors.append("raw_manifest_exclusions_present")
        if _artifact_hash(native_raw, "case_manifest") != native_manifest_hash:
            native_errors.append("raw_manifest_hash_mismatch")
        if (native_raw.get("case_manifest") or {}).get("path") != native_manifest_path:
            native_errors.append("raw_manifest_path_mismatch")
        if networks != {"ieee14", "ieee118"}:
            native_errors.append("raw_network_scope_mismatch")
        if "" in raw_ids or len(set(raw_ids)) != 160:
            native_errors.append("raw_case_ids_not_unique")
    if native_manifest:
        manifest_cases = native_manifest.get("cases") or []
        manifest_ids = [str(row.get("case_id") or "") for row in manifest_cases]
        if native_manifest.get("contract_version") != (
            "gridinstruct-independent-solver-case-manifest-v1"
        ):
            native_errors.append("manifest_contract_version_mismatch")
        if (
            _integer(native_manifest.get("case_count")) != 160
            or len(manifest_cases) != 160
        ):
            native_errors.append("manifest_case_count_not_160")
        if _integer(native_manifest.get("excluded_case_count")) != 0:
            native_errors.append("manifest_exclusions_present")
        manifest_inputs = native_manifest.get("inputs") or {}
        if not native_rebound:
            if (
                _artifact_hash(manifest_inputs, "opf_results")
                != opf_results_hash
            ):
                native_errors.append("manifest_opf_results_hash_mismatch")
            if _artifact_hash(manifest_inputs, "scenarios") != _hash_or_none(
                root, CURRENT_INPUTS["opf_source_scenarios"]
            ):
                native_errors.append("manifest_source_scenarios_hash_mismatch")
            additional_scenarios = manifest_inputs.get("additional_scenarios") or []
            expected_additional = {
                CURRENT_INPUTS["opf_additional_scenarios"]: _hash_or_none(
                    root, CURRENT_INPUTS["opf_additional_scenarios"]
                )
            }
            observed_additional = {
                str(entry.get("path") or ""): str(entry.get("sha256") or "")
                for entry in additional_scenarios
            }
            if observed_additional != expected_additional:
                native_errors.append("manifest_additional_scenarios_hash_mismatch")
            registry = _read_json_object(root, CURRENT_INPUTS["pglib_case_registry"])
            source_registry = manifest_inputs.get("source_registry") or {}
            if (
                source_registry.get("kind") != "pglib-opf-fixed-commit"
                or source_registry.get("commit") != registry.get("commit")
                or source_registry.get("license_sha256")
                != registry.get("license_sha256")
            ):
                native_errors.append("manifest_source_registry_binding_mismatch")
        if "" in manifest_ids or len(set(manifest_ids)) != 160:
            native_errors.append("manifest_case_ids_not_unique")
        if native_raw:
            raw_ids = {
                str(row.get("case_id") or "") for row in (native_raw.get("cases") or [])
            }
            if set(manifest_ids) != raw_ids:
                native_errors.append("raw_manifest_case_id_set_mismatch")

    core_report_path = EVIDENCE_PATHS["core_n1_denominator"]
    core_report = _read_json_object(root, core_report_path)
    core_errors: list[str] = []
    if not core_report:
        core_errors.append("missing_report")
    else:
        denominator = core_report.get("denominator") or {}
        outcomes = core_report.get("outcomes") or {}
        set_audit = core_report.get("set_audit") or {}
        inputs = core_report.get("inputs") or {}
        if core_report.get("contract_version") != (
            "gridinstruct-core4-line-n1-denominator-v2"
        ):
            core_errors.append("contract_version_mismatch")
        if core_report.get("status") != "pass":
            core_errors.append("status_not_pass")
        scope = core_report.get("scope") or {}
        if set(scope.get("systems") or []) != {
            "ieee14",
            "ieee30",
            "ieee57",
            "ieee118",
        }:
            core_errors.append("network_scope_mismatch")
        if scope.get("contingency_type") != "n1_branch_outage":
            core_errors.append("contingency_scope_mismatch")
        if scope.get("element_table") != "line":
            core_errors.append("element_scope_mismatch")
        if _integer(scope.get("registered_system_load_strata")) != 28:
            core_errors.append("load_strata_count_not_28")
        if _integer(denominator.get("expected_attempt_count")) != 1896:
            core_errors.append("expected_attempt_count_not_1896")
        if _integer(denominator.get("observed_attempt_count")) != 1896:
            core_errors.append("observed_attempt_count_not_1896")
        if _integer(denominator.get("unique_observed_identity_count")) != 1896:
            core_errors.append("unique_attempt_count_not_1896")
        if _integer(outcomes.get("converged_attempt_count")) != 1772:
            core_errors.append("converged_attempt_count_not_1772")
        if _integer(outcomes.get("core_converged_truth_count")) != 1772:
            core_errors.append("core_truth_count_not_1772")
        if _integer(outcomes.get("complete_truth_core_n1_count")) != 1772:
            core_errors.append("complete_truth_count_not_1772")
        for field in (
            "missing_identity_count",
            "extra_identity_count",
            "duplicate_identity_count",
            "duplicate_scenario_id_count",
        ):
            if _integer(set_audit.get(field)) != 0:
                core_errors.append(f"{field}_not_zero")
        core_errors.extend(
            _gate_contract_errors(
                core_report,
                {
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
                },
            )
        )
        expected_hashes = {
            "attempts": _hash_or_none(root, CURRENT_INPUTS["core_n1_attempts"]),
            "converged_core": _hash_or_none(root, CURRENT_INPUTS["core_scenario_truth"]),
            "complete_truth": _hash_or_none(root, CURRENT_INPUTS["scenario_truth"]),
            "simulation_report": _hash_or_none(
                root, CURRENT_INPUTS["core_n1_generation_report"]
            ),
            "case_registry": _hash_or_none(root, CURRENT_INPUTS["pglib_case_registry"]),
        }
        for name, expected_hash in expected_hashes.items():
            if expected_hash is None or _artifact_hash(inputs, name) != expected_hash:
                core_errors.append(f"input_hash_mismatch:{name}")
        registry = _read_json_object(root, CURRENT_INPUTS["pglib_case_registry"])
        source_contract = core_report.get("source_contract") or {}
        if (
            source_contract.get("repository_commit") != registry.get("commit")
            or source_contract.get("license_sha256")
            != registry.get("license_sha256")
        ):
            core_errors.append("source_contract_registry_mismatch")

    uncertainty_report_path = EVIDENCE_PATHS["opf_action_uncertainty_stress"]
    uncertainty_cases_path = CURRENT_INPUTS["opf_uncertainty_cases"]
    uncertainty_commit_path = CURRENT_INPUTS["opf_uncertainty_commit"]
    uncertainty_report = _read_json_object(root, uncertainty_report_path)
    uncertainty_cases = _read_json_object(root, uncertainty_cases_path)
    uncertainty_commit = _read_json_object(root, uncertainty_commit_path)
    uncertainty_report_hash = _hash_or_none(root, uncertainty_report_path)
    uncertainty_cases_hash = _hash_or_none(root, uncertainty_cases_path)
    uncertainty_errors: list[str] = []
    for name, payload in (
        ("report", uncertainty_report),
        ("cases", uncertainty_cases),
        ("commit", uncertainty_commit),
    ):
        if not payload:
            uncertainty_errors.append(f"missing_{name}")
    if uncertainty_report:
        denominator = uncertainty_report.get("registered_denominator") or {}
        base = uncertainty_report.get("base_stress_summary") or {}
        n1 = uncertainty_report.get("registered_n1_stress_summary") or {}
        if uncertainty_report.get("contract_version") != (
            "opf-action-active-load-uncertainty-stress-v2"
        ):
            uncertainty_errors.append("report_contract_version_mismatch")
        if uncertainty_report.get("status") != "pass":
            uncertainty_errors.append("report_status_not_pass")
        if uncertainty_report.get("integrity_status") != "pass":
            uncertainty_errors.append("integrity_status_not_pass")
        if uncertainty_report.get("robustness_status") != "pass":
            uncertainty_errors.append("robustness_status_not_pass")
        exact_counts = {
            "published_action_count": 160,
            "stress_point_count_per_action": 6,
            "expected_base_case_count": 960,
            "expected_registered_n1_case_count": 6720,
            "expected_total_case_count": 7680,
            "observed_total_case_count": 7680,
        }
        for field, expected in exact_counts.items():
            if _integer(denominator.get(field)) != expected:
                uncertainty_errors.append(f"{field}_mismatch")
        if denominator.get("complete") is not True:
            uncertainty_errors.append("registered_denominator_incomplete")
        for label, summary, expected_count in (
            ("base", base, 960),
            ("n1", n1, 6720),
        ):
            if _integer(summary.get("registered_case_count")) != expected_count:
                uncertainty_errors.append(f"{label}_registered_count_mismatch")
            if _integer(summary.get("converged_case_count")) != expected_count:
                uncertainty_errors.append(f"{label}_converged_count_mismatch")
            if _integer(summary.get("safe_case_count")) != expected_count:
                uncertainty_errors.append(f"{label}_safe_count_mismatch")
            if _number(summary.get("convergence_rate")) != 1.0:
                uncertainty_errors.append(f"{label}_convergence_rate_not_one")
            if (
                _number(summary.get("safety_rate_over_registered_denominator"))
                != 1.0
            ):
                uncertainty_errors.append(f"{label}_safety_rate_not_one")
            if (
                _number(summary.get("minimum_worst_voltage_margin_pu"))
                < 0.001
            ):
                uncertainty_errors.append(
                    f"{label}_minimum_voltage_margin_below_0_001"
                )
            if _number(summary.get("minimum_thermal_margin_percent")) < 0.0:
                uncertainty_errors.append(
                    f"{label}_minimum_thermal_margin_negative"
                )
        uncertainty_errors.extend(
            _gate_contract_errors(
                uncertainty_report,
                {
                    "input_contract_complete",
                    "registered_denominator_complete",
                    "base_stress_convergence_rate_is_one",
                    "base_stress_safety_rate_is_one",
                    "registered_n1_convergence_rate_is_one",
                    "registered_n1_safety_rate_is_one",
                    "worst_voltage_margin_meets_published_minimum",
                    "worst_thermal_margin_nonnegative",
                },
            )
        )
        report_inputs = uncertainty_report.get("input_artifacts") or {}
        expected_inputs = {
            "opf_results": _hash_or_none(root, CURRENT_INPUTS["opf_results"]),
            "opf_commit": _hash_or_none(root, CURRENT_INPUTS["opf_commit"]),
        }
        for name, expected_hash in expected_inputs.items():
            if expected_hash is None or _artifact_hash(report_inputs, name) != expected_hash:
                uncertainty_errors.append(f"report_input_hash_mismatch:{name}")
        observed_scenarios = {
            str(entry.get("path") or ""): str(entry.get("sha256") or "")
            for entry in (report_inputs.get("scenario_manifests") or [])
        }
        expected_scenarios = {
            CURRENT_INPUTS["opf_source_scenarios"]: _hash_or_none(
                root, CURRENT_INPUTS["opf_source_scenarios"]
            ),
            CURRENT_INPUTS["opf_additional_scenarios"]: _hash_or_none(
                root, CURRENT_INPUTS["opf_additional_scenarios"]
            ),
        }
        if any(value is None for value in expected_scenarios.values()):
            uncertainty_errors.append("expected_scenario_file_missing")
        elif observed_scenarios != expected_scenarios:
            uncertainty_errors.append("report_scenario_hashes_mismatch")
        implementation_hash = _hash_or_none(
            root, CURRENT_INPUTS["opf_uncertainty_validator"]
        )
        if (
            implementation_hash is None
            or uncertainty_report.get("implementation_sha256")
            != implementation_hash
        ):
            uncertainty_errors.append("implementation_hash_mismatch")
    if uncertainty_cases:
        cases = uncertainty_cases.get("cases") or []
        if uncertainty_cases.get("contract_version") != (
            "opf-action-active-load-uncertainty-stress-v2"
        ):
            uncertainty_errors.append("case_payload_contract_version_mismatch")
        if (
            _integer(uncertainty_cases.get("registered_case_count")) != 7680
            or len(cases) != 7680
        ):
            uncertainty_errors.append("case_payload_count_not_7680")
        case_ids = [str(row.get("case_id") or "") for row in cases]
        if "" in case_ids or len(set(case_ids)) != 7680:
            uncertainty_errors.append("uncertainty_case_ids_not_unique")
        if uncertainty_report and uncertainty_cases.get(
            "input_artifacts"
        ) != uncertainty_report.get("input_artifacts"):
            uncertainty_errors.append("case_payload_input_binding_mismatch")
    if uncertainty_commit:
        artifacts = uncertainty_commit.get("artifacts") or {}
        if uncertainty_commit.get("status") != "pass":
            uncertainty_errors.append("commit_status_not_pass")
        if uncertainty_commit.get("robustness_status") != "pass":
            uncertainty_errors.append("commit_robustness_status_not_pass")
        if _artifact_hash(artifacts, "cases") != uncertainty_cases_hash:
            uncertainty_errors.append("commit_cases_hash_mismatch")
        if _artifact_hash(artifacts, "report") != uncertainty_report_hash:
            uncertainty_errors.append("commit_report_hash_mismatch")
        if (artifacts.get("cases") or {}).get("path") != uncertainty_cases_path:
            uncertainty_errors.append("commit_cases_path_mismatch")
        if (artifacts.get("report") or {}).get("path") != uncertainty_report_path:
            uncertainty_errors.append("commit_report_path_mismatch")
        if uncertainty_report and uncertainty_commit.get(
            "inputs"
        ) != uncertainty_report.get("input_artifacts"):
            uncertainty_errors.append("commit_input_binding_mismatch")

    constant_power_factor_errors = _constant_power_factor_stress_errors(root)
    return {
        "opf_robust_candidate_register": {
            "passed": not candidate_errors,
            "path": candidate_report_path,
            "errors": candidate_errors,
        },
        "core_n1_denominator": {
            "passed": not core_errors,
            "path": core_report_path,
            "errors": core_errors,
        },
        "native_independent_solver": {
            "passed": not native_errors,
            "path": native_validation_path,
            "errors": native_errors,
        },
        "opf_action_uncertainty_stress": {
            "passed": not uncertainty_errors,
            "path": uncertainty_report_path,
            "errors": uncertainty_errors,
        },
        "opf_action_constant_power_factor_stress": {
            "passed": not constant_power_factor_errors,
            "path": EVIDENCE_PATHS[
                "opf_action_constant_power_factor_stress"
            ],
            "errors": constant_power_factor_errors,
        },
    }


def dataset_scenario_migration_matches(
    migration: dict[str, Any],
    dataset_rows: list[dict[str, Any]],
    scenario_rows: list[dict[str, Any]],
) -> bool:
    truth_ids = {str(row.get("scenario_id") or "") for row in scenario_rows}
    linked_ids = {
        str(row.get("scenario_id") or row.get("source_simulation_case_id") or "")
        for row in dataset_rows
        if row.get("scenario_id") or row.get("source_simulation_case_id")
    }
    hard_gates = migration.get("hard_gates") or {}
    return bool(
        migration.get("status") == "pass"
        and hard_gates
        and all(hard_gates.values())
        and 0 <= int(migration.get("records_after", -1)) <= len(dataset_rows)
        and not (linked_ids - truth_ids)
    )


def build_evidence_binding_manifest(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    current_inputs = {}
    for name, relative in CURRENT_INPUTS.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"missing_current_input:{name}:{relative}")
            current_inputs[name] = {"path": relative, "sha256": None}
        else:
            current_inputs[name] = {"path": relative, "sha256": file_sha256(path)}

    query_projection_hash = (
        projection_sha256(root / CURRENT_INPUTS["dataset_cn"])
        if current_inputs["dataset_cn"]["sha256"]
        else None
    )
    evidence = {name: evidence_entry(root, path) for name, path in EVIDENCE_PATHS.items()}
    for name, entry in evidence.items():
        if not entry["exists"]:
            errors.append(f"missing_evidence:{name}")
    for name in (
        "equipment_rating_provenance",
        "transformer_contract",
        "external_normal_common_support",
        "external_matched_strata",
        "explicit_high_rating_sensitivity",
    ):
        if evidence[name]["exists"] and evidence[name]["status"] != "pass":
            errors.append(f"power_evidence_failed:{name}")
    power_evidence_gates = power_evidence_gate_results(root)
    for name, result in power_evidence_gates.items():
        if not result["passed"]:
            errors.extend(
                f"power_evidence_binding_mismatch:{name}:{error}"
                for error in result["errors"]
            )

    if current_inputs["compliance_recompute"]["sha256"]:
        payload = read_json(root / CURRENT_INPUTS["compliance_recompute"])
        transaction = payload.get("transaction") or {}
        artifacts = transaction.get("artifacts") or {}
        final_stage = str(payload.get("output_dataset") or "")
        if (
            payload.get("status") != "pass"
            or transaction.get("committed") is not True
            or payload.get("output_sha256") != current_inputs["dataset_cn"]["sha256"]
            or artifacts.get(final_stage) != current_inputs["dataset_cn"]["sha256"]
        ):
            errors.append("compliance_recompute_binding_mismatch")
    if current_inputs["canonical_promotion"]["sha256"]:
        payload = read_json(root / CURRENT_INPUTS["canonical_promotion"])
        if (
            payload.get("status") != "pass"
            or payload.get("atomic_promotion") is not True
            or payload.get("input_sha256") != current_inputs["dataset_cn"]["sha256"]
            or payload.get("output_sha256") != current_inputs["dataset_cn"]["sha256"]
        ):
            errors.append("canonical_promotion_binding_mismatch")
    if current_inputs["canonical_immutability"]["sha256"]:
        payload = read_json(root / CURRENT_INPUTS["canonical_immutability"])
        if (
            payload.get("status") != "pass"
            or payload.get("canonical_dataset_immutable_since_promotion") is not True
            or payload.get("output_sha256") != current_inputs["dataset_cn"]["sha256"]
        ):
            errors.append("canonical_immutability_binding_mismatch")
    if current_inputs["source_english_alignment"]["sha256"]:
        payload = read_json(root / CURRENT_INPUTS["source_english_alignment"])
        if (
            payload.get("status") != "pass"
            or payload.get("source_full_sha256") != current_inputs["dataset_cn"]["sha256"]
            or payload.get("english_full_sha256") != current_inputs["dataset_en"]["sha256"]
            or int(payload.get("full_stable_field_mismatch_count", -1)) != 0
            or int(payload.get("full_classification_output_mismatch_count", -1)) != 0
        ):
            errors.append("source_english_alignment_binding_mismatch")
    if current_inputs["proxy_stress_split_manifest"]["sha256"]:
        payload = read_json(root / CURRENT_INPUTS["proxy_stress_split_manifest"])
        if (
            payload.get("status") != "pass"
            or payload.get("dataset_sha256") != current_inputs["dataset_cn"]["sha256"]
            or len(payload.get("tasks") or {}) != 3
        ):
            errors.append("proxy_stress_split_manifest_binding_mismatch")
    if current_inputs["proxy_stress_english_alignment"]["sha256"]:
        payload = read_json(root / CURRENT_INPUTS["proxy_stress_english_alignment"])
        if (
            payload.get("status") != "pass"
            or payload.get("source_full_sha256") != current_inputs["dataset_cn"]["sha256"]
            or payload.get("english_full_sha256") != current_inputs["dataset_en"]["sha256"]
        ):
            errors.append("proxy_stress_english_alignment_binding_mismatch")
    if current_inputs["proxy_stress_baseline"]["sha256"]:
        payload = read_json(root / CURRENT_INPUTS["proxy_stress_baseline"])
        contract = payload.get("split_contract") or {}
        if (
            payload.get("status") != "pass"
            or contract.get("manifest_sha256") != current_inputs["proxy_stress_split_manifest"]["sha256"]
            or contract.get("canonical_dataset_sha256") != current_inputs["dataset_cn"]["sha256"]
            or contract.get("english_dataset_sha256") != current_inputs["dataset_en"]["sha256"]
            or contract.get("split_language") != "en"
        ):
            errors.append("proxy_stress_baseline_binding_mismatch")

    if evidence["query_repair"]["exists"]:
        payload = read_json(root / EVIDENCE_PATHS["query_repair"])
        files = payload.get("files") or []
        if (
            payload.get("status") != "pass"
            or not files
            or files[0].get("output_query_projection_sha256") != query_projection_hash
            or payload.get("scenario_truth_sha256") != current_inputs["scenario_truth"]["sha256"]
        ):
            errors.append("query_repair_binding_mismatch")
    if evidence["query_independent"]["exists"]:
        payload = read_json(root / EVIDENCE_PATHS["query_independent"])
        if (
            payload.get("status") != "pass"
            or payload.get("query_projection_sha256") != query_projection_hash
            or payload.get("scenario_truth_sha256") != current_inputs["scenario_truth"]["sha256"]
        ):
            errors.append("query_independent_binding_mismatch")
    if evidence["cross_solver"]["exists"]:
        payload = read_json(root / EVIDENCE_PATHS["cross_solver"])
        if (
            payload.get("status") != "pass"
            or payload.get("scenario_truth_sha256") != current_inputs["scenario_truth"]["sha256"]
            or payload.get("opf_results_sha256") != current_inputs["opf_results"]["sha256"]
        ):
            errors.append("cross_solver_binding_mismatch")
    if current_inputs["frozen_topology_migration"]["sha256"]:
        migration = read_json(root / CURRENT_INPUTS["frozen_topology_migration"])
        checksum_manifest = read_json(root / CURRENT_INPUTS["frozen_topology_checksums_v2"])
        if (
            migration.get("status") != "pass"
            or migration.get("cardinality_preserved") is not True
            or migration.get("ids_preserved") is not True
            or migration.get("frozen_records_sha256") != current_inputs["frozen_topology_v1"]["sha256"]
            or migration.get("output_records_sha256") != current_inputs["frozen_topology_v2"]["sha256"]
            or checksum_manifest.get(CURRENT_INPUTS["frozen_topology_v2"])
            != current_inputs["frozen_topology_v2"]["sha256"]
        ):
            errors.append("frozen_topology_migration_binding_mismatch")
    if current_inputs["dataset_scenario_link_migration"]["sha256"]:
        migration = read_json(root / CURRENT_INPUTS["dataset_scenario_link_migration"])
        dataset_rows = read_jsonl(root / CURRENT_INPUTS["dataset_cn"])
        scenario_rows = read_json(root / CURRENT_INPUTS["scenario_truth"])
        if not dataset_scenario_migration_matches(migration, dataset_rows, scenario_rows):
            errors.append("dataset_scenario_link_migration_binding_mismatch")
    if current_inputs["scenario_truth_attempts"]["sha256"]:
        truth_report_path = root / "reports/scenario_truth_rebuild_v1.2_sd_core.json"
        truth_report = read_json(truth_report_path) if truth_report_path.is_file() else {}
        if (
            truth_report.get("status") != "pass"
            or truth_report.get("attempts_sha256") != current_inputs["scenario_truth_attempts"]["sha256"]
        ):
            errors.append("scenario_truth_attempt_binding_mismatch")
    if current_inputs["opf_commit"]["sha256"]:
        commit = read_json(root / CURRENT_INPUTS["opf_commit"])
        committed = commit.get("artifacts") or {}
        if (
            commit.get("status") != "pass"
            or (committed.get("results") or {}).get("sha256") != current_inputs["opf_results"]["sha256"]
        ):
            errors.append("opf_commit_binding_mismatch")
    if evidence["bootstrap"]["exists"]:
        payload = read_json(root / EVIDENCE_PATHS["bootstrap"])
        if (
            payload.get("status") != "pass"
            or payload.get("dataset_sha256") != current_inputs["dataset_en"]["sha256"]
            or int(payload.get("run_count", 0)) != 30
        ):
            errors.append("bootstrap_binding_mismatch")

    split_hashes = {
        "train": current_inputs["train_en"]["sha256"],
        "validation": current_inputs["validation_en"]["sha256"],
        "test": current_inputs["test_en"]["sha256"],
    }
    multiseed = []
    summary_dir = root / "benchmark/multiseed_v1.2_sd_core"
    for path in sorted(summary_dir.glob("*_five_seed_summary.json")):
        payload = read_json(path)
        row = {
            "task": payload.get("task"),
            "path": str(path.relative_to(root)),
            "sha256": file_sha256(path),
            "status": payload.get("status"),
            "input_sha256": payload.get("input_sha256"),
            "model_revision": payload.get("model_revision"),
            "runs": payload.get("runs"),
        }
        multiseed.append(row)
    expected_tasks = {
        "operation_ticket_check",
        "regulation_compliance_check",
        "dispatcher_intent_tool_call",
        "regulation_qa",
        "intelligent_data_query",
        "auxiliary_decision",
    }
    if (
        {str(row["task"]) for row in multiseed} != expected_tasks
        or any(
            row["status"] != "pass"
            or row["input_sha256"] != split_hashes
            or not (row.get("model_revision") or {}).get("sha256")
            or [run.get("seed") for run in (row.get("runs") or [])] != [13, 29, 42, 57, 71]
            for row in multiseed
        )
    ):
        errors.append("multiseed_binding_mismatch")
    evidence["multiseed"] = {"status": "pass" if "multiseed_binding_mismatch" not in errors else "fail", "summaries": multiseed}

    for manifest_name in (
        "code_manifest",
        "environment_lock",
        "historical_runtime_environment",
        "formal_runtime_environment",
        "model_revision_manifest",
    ):
        relative = CURRENT_INPUTS[manifest_name]
        if (root / relative).is_file():
            payload = read_json(root / relative)
            if manifest_name == "model_revision_manifest" and payload.get("status") != "pass":
                errors.append("model_revision_manifest_failed")
    if current_inputs["data_lineage_manifest"]["sha256"]:
        lineage = read_json(root / CURRENT_INPUTS["data_lineage_manifest"])
        if lineage.get("status") != "pass" or lineage.get("validation_errors"):
            errors.append("data_lineage_manifest_failed")
    if current_inputs["third_party_asset_inventory"]["sha256"]:
        third_party = read_json(root / CURRENT_INPUTS["third_party_asset_inventory"])
        if third_party.get("local_inventory_complete") is not True or third_party.get("validation_errors"):
            errors.append("third_party_asset_inventory_failed")

    return {
        "schema_version": "1.0",
        "status": "pass" if not errors else "fail",
        "current_inputs": current_inputs,
        "query_contract_projection_sha256": query_projection_hash,
        "evidence": evidence,
        "power_evidence_gates": power_evidence_gates,
        "invalid_evidence_count": len(errors),
        "errors": errors,
    }
