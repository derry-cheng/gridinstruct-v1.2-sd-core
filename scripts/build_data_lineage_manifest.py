#!/usr/bin/env python3
"""Build a machine-verifiable map from physical sources to every released data view."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, write_json


COMPONENTS = [
    {
        "name": "v16_candidate_pool_reconstruction",
        "generator": "scripts/build_frozen_augmentation_contract.py + deterministic plus12/13/14 augmenters + scripts/replay_frozen_implicit_ticket_rows.py",
        "inputs": [
            "data/gridinstruct_v1.2_paper_candidate_actionable_plus15.jsonl",
            "data/frozen_sources/gridinstruct_v1.2_plus11_full.jsonl",
            "data/frozen_sources/operation_implicit_ticket_llm_v1.jsonl",
            "metadata/frozen_augmentation_contract_v1.2.json",
        ],
        "outputs": [
            "data/gridinstruct_v1.2_paper_candidate_actionable_plus15_v16.jsonl",
            "reports/v16_frozen_implicit_ticket_replay.json",
        ],
        "method": "reviewed plus11 state, deterministic plus12-through-plus14 transformations, and content-addressed replay of the 681 accepted implicit-ticket rows",
    },
    {
        "name": "core_physical_scenarios",
        "generator": "scripts/generate_grid_scenarios.py",
        "inputs": ["metadata/pglib_opf_v23_07_case_registry.json"],
        "outputs": [
            "simulation_outputs/power_flow/scenarios_all.json",
            "simulation_outputs/power_flow/scenarios_converged_core.json",
        ],
        "method": "hash-pinned PGLib-OPF v23.07 IEEE14/30/57/118 cases, deterministic scaling and contingencies, and complete AC electrical states",
    },
    {
        "name": "external_topology_scenarios",
        "generator": "scripts/generate_extended_topology_stress.py",
        "inputs": ["metadata/pglib_opf_v23_07_case_registry.json"],
        "outputs": [
            "simulation_outputs/topology_stress/extended_topology_scenarios.json",
            "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
            "reports/extended_topology_stress_audit_v1.2_sd_core.json",
        ],
        "method": "hash-pinned PGLib-OPF topology and RATE_A limits, network-specific preregistered loading envelopes, and deterministic candidate search; IEEE300 uses a bijectively mapped convergent public operating point with PGLib topology and ratings retained",
    },
    {
        "name": "external_normal_common_support",
        "generator": "scripts/generate_extended_topology_stress.py --dispatch-policy normal_ac_opf",
        "inputs": ["metadata/pglib_opf_v23_07_case_registry.json"],
        "outputs": [
            "simulation_outputs/topology_stress/matched_normal_pool_pegase1354.json",
            "reports/matched_normal_attempts_pegase1354.json",
            "reports/matched_normal_audit_pegase1354.json",
        ],
        "method": "PGLib-bound PEGASE1354 normal-envelope construction with AC OPF, voltage bounds 0.95-1.05 p.u., conservative thermal constraints, one base state, twelve accepted N-1 states, and a complete classified attempt ledger",
    },
    {
        "name": "power_domain_source_and_security_evidence",
        "generator": "scripts/audit_equipment_rating_provenance.py + scripts/audit_full_n1_enumeration.py + scripts/audit_transformer_contract_all_networks.py + scripts/build_external_matched_strata.py + scripts/audit_explicit_high_rating_sensitivity.py",
        "inputs": [
            "metadata/pglib_opf_v23_07_case_registry.json",
            "simulation_outputs/topology_stress/extended_topology_scenarios.json",
            "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
            "simulation_outputs/topology_stress/matched_normal_pool_pegase1354.json",
        ],
        "outputs": [
            "reports/equipment_rating_provenance_v1.2_sd_core.json",
            "reports/full_n1_enumeration_v1.2_sd_core.json",
            "reports/pandapower_transformer_contract_all_networks_v1.2_sd_core.json",
            "reports/external_network_severity_matched_strata_v1.2_sd_core.json",
            "reports/explicit_high_rating_sensitivity_v1.2_sd_core.json",
        ],
        "method": "source-bound ratings for all eleven networks, complete IEEE14/118 N-1 denominators, transformer/tap/phase regression, transparent 7x4 support diagnostics plus exact matching inside the fully observed network-by-severity common-support block, and conservative sensitivity for seven source-explicit IEEE300 high limits",
    },
    {
        "name": "frozen_external_migration",
        "generator": "scripts/migrate_frozen_external_topology.py",
        "inputs": [
            "simulation_outputs/topology_stress/legacy_topology_scenarios_v1.json",
            "simulation_outputs/topology_stress/extended_topology_scenarios.json",
            "data/frozen_sources/extended_topology_instruction_v1.jsonl",
        ],
        "outputs": [
            "data/frozen_sources/extended_topology_instruction_v2.jsonl",
            "metadata/frozen_source_checksums_v2.json",
            "reports/frozen_external_topology_migration_v2.json",
        ],
        "method": "complete-state replay of every legacy scenario, deterministic same-network and same-contingency replacement for non-replayable states, regenerated physical supervision, stable record IDs, and cardinality-preserving migration audit",
    },
    {
        "name": "scenario_source_merge",
        "generator": "scripts/merge_scenario_sources.py",
        "inputs": [
            "simulation_outputs/power_flow/scenarios_converged_core.json",
            "simulation_outputs/topology_stress/legacy_topology_scenarios_v1.json",
            "simulation_outputs/topology_stress/extended_topology_scenarios.json",
            "reports/frozen_external_topology_migration_v2.json",
        ],
        "outputs": ["simulation_outputs/contingency/scenario_rebuild_manifest.json"],
        "method": "exact duplicate collapse, conflict rejection, and migration-report-bound exclusion of non-replayable legacy states after their records are cardinality-preservingly regenerated onto current truth",
    },
    {
        "name": "authoritative_scenario_truth",
        "generator": "scripts/recompute_scenario_truth.py",
        "inputs": ["simulation_outputs/contingency/scenario_rebuild_manifest.json"],
        "outputs": [
            "simulation_outputs/contingency/scenarios_converged.json",
            "reports/scenario_truth_rebuild_attempts_v1.2_sd_core.json",
        ],
        "method": "independent AC replay of every merged scenario with complete truth reconstruction and a per-scenario pass/fail ledger bound by input-row hashes",
    },
    {
        "name": "selected_snapshot",
        "generator": "scripts/build_sd_core_snapshot.py",
        "inputs": ["data/gridinstruct_v1.2_paper_candidate_actionable_plus15_v16.jsonl"],
        "outputs": ["data/stages/v1.2_sd_core/01_selected.jsonl"],
        "method": "deterministic task/source quota selection from the hash-bound, fully replayed v16 plus15 candidate pool",
    },
    {
        "name": "migrated_frozen_external_append",
        "generator": "scripts/append_frozen_records.py",
        "inputs": [
            "data/stages/v1.2_sd_core/01_selected.jsonl",
            "data/frozen_sources/extended_topology_instruction_v2.jsonl",
            "metadata/frozen_source_checksums_v2.json",
        ],
        "outputs": ["data/stages/v1.2_sd_core/02_frozen_v1.jsonl"],
        "method": "checksum-pinned append of the cardinality-preserving v2 migrated frozen source with stable IDs and conflict rejection; the immutable v1 source remains retained as migration evidence",
    },
    {
        "name": "new_external_topology_instructions",
        "generator": "scripts/append_topology_instruction_records.py",
        "inputs": [
            "data/stages/v1.2_sd_core/02_frozen_v1.jsonl",
            "simulation_outputs/topology_stress/extended_topology_scenarios.json",
        ],
        "outputs": ["data/stages/v1.2_sd_core/03_topology_v2.jsonl"],
        "method": "stable scenario-keyed instruction variants over converged external topology states",
    },
    {
        "name": "equipment_contingency_instructions",
        "generator": "scripts/append_topology_instruction_records.py",
        "inputs": [
            "data/stages/v1.2_sd_core/03_topology_v2.jsonl",
            "simulation_outputs/contingency/scenarios_converged.json",
        ],
        "outputs": ["data/stages/v1.2_sd_core/04_equipment.jsonl"],
        "method": "transformer, generator, and bus N-1 instruction variants linked to complete physical truth",
    },
    {
        "name": "dataset_scenario_link_migration",
        "generator": "scripts/migrate_dataset_scenario_links.py",
        "inputs": [
            "data/stages/v1.2_sd_core/04_equipment.jsonl",
            "simulation_outputs/contingency/scenarios_converged.json",
        ],
        "outputs": [
            "data/stages/v1.2_sd_core/04_equipment_grounded.jsonl",
            "reports/dataset_scenario_link_migration_v1.2_sd_core.json",
        ],
        "method": "cardinality- and ID-preserving group migration of legacy scenario links to unique authoritative states with matching network, contingency, loading, and issue profile; severity and physically derived supervision are recomputed from current truth",
    },
    {
        "name": "opf_closed_loop_instructions",
        "generator": "scripts/append_opf_closed_loop_auxiliary_records.py",
        "inputs": [
            "data/stages/v1.2_sd_core/04_equipment_grounded.jsonl",
            "simulation_outputs/contingency/scenarios_converged.json",
        ],
        "outputs": [
            "data/stages/v1.2_sd_core/05_opf.jsonl",
            "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
            "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
        ],
        "method": "native-bound AC OPF, loss-aware generator-outage reserve, bounded corrective redispatch, and registered post-action N-1 replay; commit marker is written last",
    },
    {
        "name": "native_independent_solver_evidence",
        "generator": "scripts/build_independent_solver_case_manifest.py + scripts/run_lightsim_native_independent_solver.py + scripts/validate_independent_solver_evidence.py",
        "inputs": [
            "simulation_outputs/contingency/scenarios_converged.json",
            "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
            "metadata/pglib_opf_v23_07_case_registry.json",
        ],
        "outputs": [
            "metadata/independent_solver_case_manifest_v1.json",
            "reports/independent_solver_raw_evidence_v1.2_sd_core_rebound.json",
            "reports/independent_solver_validation_v1.2_sd_core_rebound.json",
        ],
        "method": "direct MATPOWER text parsing into the independent LightSim2Grid C++ data model, followed by topology, voltage, branch-flow, thermal-label, voltage-label, and power-balance agreement gates",
    },
    {
        "name": "query_truth_transaction",
        "generator": "scripts/repair_query_result_consistency.py",
        "inputs": [
            "data/stages/v1.2_sd_core/05_opf.jsonl",
            "simulation_outputs/contingency/scenarios_converged.json",
        ],
        "outputs": ["data/stages/v1.2_sd_core/06_query_repaired.jsonl"],
        "method": "strict query/source/tool/result contract with full preflight and atomic replacement",
    },
    {
        "name": "operation_state_repair",
        "generator": "scripts/repair_operation_ticket_state_consistency.py",
        "inputs": ["data/stages/v1.2_sd_core/06_query_repaired.jsonl"],
        "outputs": ["data/stages/v1.2_sd_core/07_operation_state.jsonl"],
        "method": "deterministic operation-ticket state and action consistency repair",
    },
    {
        "name": "rule_evidence_links",
        "generator": "scripts/expand_rule_taxonomy_and_links.py",
        "inputs": [
            "data/stages/v1.2_sd_core/07_operation_state.jsonl",
            "rules/regulation_rules.json",
        ],
        "outputs": [
            "data/stages/v1.2_sd_core/08_rule_linked.jsonl",
            "metadata/rule_clause_matrix.json",
        ],
        "method": "pre-target exact JSON-pointer evidence links to the current provenance-complete rule-card registry",
    },
    {
        "name": "compliance_truth_recomputation",
        "generator": "scripts/recompute_compliance_labels_from_truth.py",
        "inputs": [
            "data/stages/v1.2_sd_core/08_rule_linked.jsonl",
            "simulation_outputs/contingency/scenarios_converged.json",
        ],
        "outputs": [
            "data/stages/v1.2_sd_core/09_compliance_recomputed.jsonl",
            "reports/compliance_label_recompute_v1.2_sd_core.json",
        ],
        "method": "immutable pre-promotion recomputation of every regulation-compliance label and rationale from authoritative scenario truth; the committed report binds the output-stage hash",
    },
    {
        "name": "canonical_release_promotion",
        "generator": "scripts/promote_dataset_stage.py",
        "inputs": [
            "data/stages/v1.2_sd_core/09_compliance_recomputed.jsonl",
            "reports/compliance_label_recompute_v1.2_sd_core.json",
        ],
        "outputs": [
            "data/gridinstruct_v1.2_sd_core.jsonl",
            "metadata/canonical_dataset_promotion_manifest.json",
            "reports/canonical_dataset_immutability_v1.2_sd_core.json",
        ],
        "method": "transaction-bound atomic promotion of the final immutable construction stage followed by a hash gate that detects any post-promotion canonical mutation",
    },
    {
        "name": "english_release_materialization",
        "generator": "scripts/run_english_translation_pipeline.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core.jsonl"],
        "outputs": ["data/gridinstruct_v1.2_sd_core_en.jsonl"],
        "method": "field-scoped English materialization with immutable IDs, labels, rule links, scenario links, and structured targets",
    },
    {
        "name": "official_source_splits",
        "generator": "scripts/create_splits.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core.jsonl"],
        "outputs": [
            "data/v1.2_sd_core_train.jsonl",
            "data/v1.2_sd_core_validation.jsonl",
            "data/v1.2_sd_core_test.jsonl",
            "data/v1.2_sd_core_ood_test.jsonl",
        ],
        "method": "single-seed provenance-component split with task and full classification-label coverage gates",
    },
    {
        "name": "official_english_splits",
        "generator": "scripts/materialize_english_splits.py",
        "inputs": [
            "data/gridinstruct_v1.2_sd_core_en.jsonl",
            "data/v1.2_sd_core_train.jsonl",
            "data/v1.2_sd_core_validation.jsonl",
            "data/v1.2_sd_core_test.jsonl",
            "data/v1.2_sd_core_ood_test.jsonl",
        ],
        "outputs": [
            "data/v1.2_sd_core_train_en.jsonl",
            "data/v1.2_sd_core_validation_en.jsonl",
            "data/v1.2_sd_core_test_en.jsonl",
            "data/v1.2_sd_core_ood_test_en.jsonl",
        ],
        "method": "ID-exact projection from each source split into the validated English release",
    },
    {
        "name": "source_english_alignment_gate",
        "generator": "scripts/validate_source_english_alignment.py",
        "inputs": [
            "data/gridinstruct_v1.2_sd_core.jsonl",
            "data/gridinstruct_v1.2_sd_core_en.jsonl",
        ],
        "outputs": [
            "reports/source_english_core_alignment_v1.2_sd_core.json",
            "reports/source_english_alignment_v1.2_sd_core.json",
        ],
        "method": "record-order, ID, label, structured-anchor, provenance, and stable-metadata equality gates over the full source/English release and every materialized split",
    },
    {
        "name": "strict_source_group_splits",
        "generator": "scripts/create_strict_source_group_splits.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core.jsonl"],
        "outputs": [
            "data/v1.2_sd_core_strict_train.jsonl",
            "data/v1.2_sd_core_strict_validation.jsonl",
            "data/v1.2_sd_core_strict_test.jsonl",
        ],
        "method": "connected-component holdout over source group, scenario, simulation case, source record, and operation group",
    },
    {
        "name": "classification_challenge_splits",
        "generator": "scripts/create_classification_challenge_splits.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core.jsonl"],
        "outputs": [
            "data/v1.2_sd_core_challenge_train.jsonl",
            "data/v1.2_sd_core_challenge_validation.jsonl",
            "data/v1.2_sd_core_challenge_test.jsonl",
        ],
        "method": "classification-only global provenance-component holdout with task/label support gates",
    },
    {
        "name": "template_holdout_splits",
        "generator": "scripts/create_template_holdout_splits.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core.jsonl"],
        "outputs": [
            "data/v1.2_sd_core_template_holdout_train.jsonl",
            "data/v1.2_sd_core_template_holdout_validation.jsonl",
            "data/v1.2_sd_core_template_holdout_test.jsonl",
        ],
        "method": "surface-template-family holdout for shortcut sensitivity",
    },
    {
        "name": "stress_split_english_views",
        "generator": "scripts/materialize_english_splits.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core_en.jsonl"],
        "outputs": [
            "data/v1.2_sd_core_strict_train_en.jsonl",
            "data/v1.2_sd_core_strict_validation_en.jsonl",
            "data/v1.2_sd_core_strict_test_en.jsonl",
            "data/v1.2_sd_core_challenge_train_en.jsonl",
            "data/v1.2_sd_core_challenge_validation_en.jsonl",
            "data/v1.2_sd_core_challenge_test_en.jsonl",
            "data/v1.2_sd_core_template_holdout_train_en.jsonl",
            "data/v1.2_sd_core_template_holdout_validation_en.jsonl",
            "data/v1.2_sd_core_template_holdout_test_en.jsonl",
        ],
        "method": "ID-exact English projection of strict, challenge, and template-holdout source splits",
    },
    {
        "name": "proxy_reduced_splits",
        "generator": "scripts/create_proxy_reduced_classification_splits.py",
        "inputs": [
            "data/v1.2_sd_core_train.jsonl",
            "data/v1.2_sd_core_challenge_train.jsonl",
            "data/v1.2_sd_core_strict_train.jsonl",
        ],
        "outputs": [
            "data/v1.2_sd_core_proxyreduced_train.jsonl",
            "data/v1.2_sd_core_proxyreduced_validation.jsonl",
            "data/v1.2_sd_core_proxyreduced_test.jsonl",
            "data/v1.2_sd_core_challenge_proxyreduced_train.jsonl",
            "data/v1.2_sd_core_challenge_proxyreduced_validation.jsonl",
            "data/v1.2_sd_core_challenge_proxyreduced_test.jsonl",
            "data/v1.2_sd_core_strict_proxyreduced_train.jsonl",
            "data/v1.2_sd_core_strict_proxyreduced_validation.jsonl",
            "data/v1.2_sd_core_strict_proxyreduced_test.jsonl",
        ],
        "method": "registered removal of high-purity proxy fields while preserving IDs and gold labels",
    },
    {
        "name": "proxy_reduced_english_views",
        "generator": "scripts/materialize_english_splits.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core_en.jsonl"],
        "outputs": [
            "data/v1.2_sd_core_proxyreduced_train_en.jsonl",
            "data/v1.2_sd_core_proxyreduced_validation_en.jsonl",
            "data/v1.2_sd_core_proxyreduced_test_en.jsonl",
            "data/v1.2_sd_core_challenge_proxyreduced_train_en.jsonl",
            "data/v1.2_sd_core_challenge_proxyreduced_validation_en.jsonl",
            "data/v1.2_sd_core_challenge_proxyreduced_test_en.jsonl",
            "data/v1.2_sd_core_strict_proxyreduced_train_en.jsonl",
            "data/v1.2_sd_core_strict_proxyreduced_validation_en.jsonl",
            "data/v1.2_sd_core_strict_proxyreduced_test_en.jsonl",
        ],
        "method": "ID-exact English projection of all proxy-reduced source splits",
    },
    {
        "name": "proxy_stress_splits",
        "generator": "scripts/create_proxy_stress_splits.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core.jsonl"],
        "outputs": [
            "reports/proxy_stress_split_manifest_v1.2_sd_core.json",
            "data/proxy_stress/regulation_compliance_check/train.jsonl",
            "data/proxy_stress/regulation_compliance_check/validation.jsonl",
            "data/proxy_stress/regulation_compliance_check/test.jsonl",
            "data/proxy_stress/operation_ticket_check/train.jsonl",
            "data/proxy_stress/operation_ticket_check/validation.jsonl",
            "data/proxy_stress/operation_ticket_check/test.jsonl",
            "data/proxy_stress/dispatcher_intent_tool_call/train.jsonl",
            "data/proxy_stress/dispatcher_intent_tool_call/validation.jsonl",
            "data/proxy_stress/dispatcher_intent_tool_call/test.jsonl",
        ],
        "method": "task-specific high-purity proxy-group holdout for classification stress testing with source-split hashes, canonical hash, seed, and builder hash committed before evaluation",
    },
    {
        "name": "proxy_stress_english_views",
        "generator": "scripts/materialize_english_splits.py",
        "inputs": [
            "data/gridinstruct_v1.2_sd_core_en.jsonl",
            "reports/proxy_stress_split_manifest_v1.2_sd_core.json",
        ],
        "outputs": [
            "data/proxy_stress/regulation_compliance_check/train_en.jsonl",
            "data/proxy_stress/regulation_compliance_check/validation_en.jsonl",
            "data/proxy_stress/regulation_compliance_check/test_en.jsonl",
            "data/proxy_stress/operation_ticket_check/train_en.jsonl",
            "data/proxy_stress/operation_ticket_check/validation_en.jsonl",
            "data/proxy_stress/operation_ticket_check/test_en.jsonl",
            "data/proxy_stress/dispatcher_intent_tool_call/train_en.jsonl",
            "data/proxy_stress/dispatcher_intent_tool_call/validation_en.jsonl",
            "data/proxy_stress/dispatcher_intent_tool_call/test_en.jsonl",
            "reports/source_english_proxy_stress_alignment_v1.2_sd_core.json",
        ],
        "method": "source-schema-preserving English projection of the nine fixed proxy-stress splits followed by per-ID stable-field, label, and structure gates",
    },
    {
        "name": "formal_runtime_environment",
        "generator": "scripts/capture_formal_runtime_environment.py",
        "inputs": ["environment.yml"],
        "outputs": ["metadata/formal_runtime_environment.json"],
        "method": "capture of the active formal Linux runtime, direct pinned package versions, GPU visibility, environment specification hash, and capture implementation hash",
    },
    {
        "name": "reproducibility_manifests",
        "generator": "scripts/build_reproducibility_manifests.py",
        "inputs": ["metadata/formal_runtime_environment.json"],
        "outputs": [
            "metadata/environment_lock.json",
            "metadata/runtime_environment.json",
            "metadata/requirements-lock.txt",
            "metadata/environment-freeze.txt",
            "metadata/code_manifest.json",
        ],
        "method": "deterministic capture of the formal pipeline Python, platform, package versions, reproducibility environment flags, and complete script-tree hashes",
    },
    {
        "name": "blinded_expert_review_sample",
        "generator": "scripts/sample_stratified_expert_review.py",
        "inputs": ["data/gridinstruct_v1.2_sd_core.jsonl"],
        "outputs": ["review_packages/stratified_expert_review_v1.2_sd_core/review_packet_blinded.jsonl"],
        "method": "stratified 800-record sample with 1,600 independent reviewer assignments and a separate adjudication template",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_file(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    row: dict[str, Any] = {"path": relative, "exists": path.is_file()}
    if not path.is_file():
        return row
    row.update({"size_bytes": path.stat().st_size, "sha256": sha256(path)})
    if path.suffix == ".jsonl":
        identifier_field = "sample_id" if relative.startswith("review_packages/") else "id"
        record_count = 0
        identifiers: set[str] = set()
        missing_identifiers = 0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                record_count += 1
                identifier = str(record.get(identifier_field) or "")
                if identifier:
                    identifiers.add(identifier)
                else:
                    missing_identifiers += 1
        row.update(
            {
                "record_count": record_count,
                "identifier_field": identifier_field,
                "id_count": record_count - missing_identifiers,
                "unique_id_count": len(identifiers),
                "ids_complete_and_unique": record_count > 0
                and missing_identifiers == 0
                and record_count == len(identifiers),
            }
        )
    elif path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            row["top_level_type"] = type(payload).__name__
            if isinstance(payload, list):
                row["record_count"] = len(payload)
        except json.JSONDecodeError:
            row["json_parse_error"] = True
    return row


def main() -> None:
    entries = []
    errors = []
    for component in COMPONENTS:
        generator = inspect_file(component["generator"])
        inputs = [inspect_file(path) for path in component["inputs"]]
        outputs = [inspect_file(path) for path in component["outputs"]]
        entry = {**component, "generator_artifact": generator, "input_artifacts": inputs, "output_artifacts": outputs}
        entries.append(entry)
        if not generator["exists"]:
            errors.append(f"missing_generator:{component['name']}:{component['generator']}")
        for artifact in inputs + outputs:
            if not artifact["exists"]:
                errors.append(f"missing_artifact:{component['name']}:{artifact['path']}")
            if artifact.get("ids_complete_and_unique") is False:
                errors.append(f"invalid_record_ids:{component['name']}:{artifact['path']}")
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not errors else "fail",
        "component_count": len(entries),
        "components": entries,
        "validation_errors": errors,
    }
    output_json = ROOT / "metadata/data_lineage_manifest.json"
    output_md = ROOT / "docs/DATA_GENERATION_LINEAGE.md"
    write_json(output_json, manifest)
    lines = [
        "# GridInstruct Data Generation Lineage",
        "",
        f"Generated: {manifest['generated_at']}",
        f"Status: `{manifest['status']}`",
        "",
        "Every listed hash is computed from the current artifact in the release workspace.",
        "",
        "| Component | Generator | Method | Outputs |",
        "| --- | --- | --- | --- |",
    ]
    for entry in entries:
        outputs = "; ".join(
            f"`{row['path']}` ({row.get('record_count', row.get('size_bytes', 0))})"
            for row in entry["output_artifacts"]
        )
        lines.append(
            f"| {entry['name']} | `{entry['generator']}` | {entry['method']} | {outputs} |"
        )
    if errors:
        lines.extend(["", "## Validation errors", "", *[f"- `{error}`" for error in errors]])
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "components": len(entries), "errors": len(errors)}))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
