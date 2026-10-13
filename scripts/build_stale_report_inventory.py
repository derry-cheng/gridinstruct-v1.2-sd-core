#!/usr/bin/env python3
"""Inventory superseded report snapshots that contain pre-current-release claims."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/stale_report_cleanup_inventory_v1.2_sd_core.json"
STALE = [
    "reports/SD_FINAL_ASSESSMENT.json",
    "reports/SD_FINAL_ASSESSMENT.md",
    "reports/project_milestone_audit_v1.2_sd_core.json",
    "reports/project_milestone_audit_v1.2_sd_core.md",
    "reports/release_metadata_snapshot.json",
    "reports/manuscript_claim_sync_v1.2_sd_core.json",
    "reports/release_claim_alignment_audit_v1.2_sd_core.json",
    "reports/release_claim_alignment_audit_v1.2_sd_core.md",
    "reports/english_rematerialization_progress_v1.2_sd_core.json",
    "reports/english_rematerialization_v1.2_sd_core.json",
    "reports/english_split_audit_v1.2_sd_core.json",
    "reports/english_split_audit_v1.2_sd_core.md",
    "reports/english_split_materialization_v1.2_sd_core.json",
    "reports/english_split_materialization_v1.2_sd_core.md",
    "reports/english_split_materialization_challenge_split_v1.2_sd_core.json",
    "reports/english_split_materialization_challenge_split_v1.2_sd_core.md",
    "reports/english_split_materialization_proxy_reduced_splits_v1.2_sd_core.json",
    "reports/english_split_materialization_proxy_reduced_splits_v1.2_sd_core.md",
    "reports/english_split_materialization_strict_split_v1.2_sd_core.json",
    "reports/english_split_materialization_strict_split_v1.2_sd_core.md",
    "reports/english_split_materialization_template_holdout_split_v1.2_sd_core.json",
    "reports/english_split_materialization_template_holdout_split_v1.2_sd_core.md",
    "reports/equipment_contingency_instruction_expansion_v1.2_sd_core.json",
    "reports/equipment_contingency_instruction_expansion_v1.2_sd_core.md",
    "reports/topology_instruction_expansion_v1.2_sd_core.json",
    "reports/topology_instruction_expansion_v1.2_sd_core.md",
    "reports/frozen_external_topology_v1_append.json",
    "reports/frozen_external_topology_v1_append.md",
    "reports/frozen_external_topology_v2_append.json",
    "reports/frozen_external_topology_v2_append.md",
    "reports/dataset_scenario_link_migration_v1.2_sd_core.json",
    "reports/dataset_scenario_link_migration_v1.2_sd_core.md",
    "reports/operation_ticket_state_consistency_v1.2_sd_core.json",
    "reports/operation_ticket_state_consistency_v1.2_sd_core.md",
    "reports/query_result_consistency_migration_v1.2_sd_core.json",
    "reports/query_result_consistency_migration_v1.2_sd_core.md",
    "reports/query_result_consistency_repair_v1.2_sd_core.json",
    "reports/query_result_consistency_repair_v1.2_sd_core.md",
    "reports/rule_taxonomy_expansion_v1.2_sd_core.json",
    "reports/rule_taxonomy_expansion_v1.2_sd_core.md",
    "reports/scenario_truth_rebuild_attempts_v1.2_sd_core.json",
    "reports/scenario_truth_rebuild_v1.2_sd_core.json",
    "reports/scenario_truth_rebuild_v1.2_sd_core.md",
    "reports/sd_core_selection_report.json",
    "reports/sd_core_selection_report.md",
    "reports/v16_frozen_implicit_ticket_replay.json",
    "reports/v16_plus12_dispatcher_counterfactuals.json",
    "reports/v16_plus12_dispatcher_counterfactuals.md",
    "reports/v16_plus13_monitoring_counterfactuals.json",
    "reports/v16_plus13_monitoring_counterfactuals.md",
    "reports/v16_plus14_plain_ticket_adversarial.json",
    "reports/v16_plus14_plain_ticket_adversarial.md",
    "reports/independent_solver_preflight_v16_raw.json",
    "reports/independent_solver_preflight_v16_ten_native_models_raw.json",
    "reports/independent_solver_preflight_v16_ten_native_models_validation.json",
    "reports/independent_solver_preflight_v16_validation.json",
    "reports/compliance_label_recompute_v1.2_sd_core.json",
    "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.json",
    "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.md",
    "reports/data_validation_v1.2_sd_core.json.pre_v15_20260721",
    "reports/sd_readiness_report.json",
    "reports/PROJECT_DATA_LINEAGE_AND_DUAL_REVIEW_20260723.md",
    "reports/full_n1_enumeration_probe_low.json",
    "reports/release_bundle_validation_v1.2_sd_core.json",
    "reports/sd_core_distribution_risk_audit.json",
]

STALE.extend(
    str(path.relative_to(ROOT))
    for directory in (ROOT / "reports/probes", ROOT / "reports/review_chunks")
    if directory.is_dir()
    for path in directory.iterdir()
    if path.is_file()
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    entries = []
    missing = []
    for relative in STALE:
        path = ROOT / relative
        if not path.is_file():
            missing.append(relative)
            continue
        entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "inventory_only_before_deletion",
        "reason": "These snapshots encode pre-95,479-row claims or refer to deleted intermediate stages; current evidence is in the revision manifest and current audit reports.",
        "entries": entries,
        "missing_expected": missing,
        "candidate_bytes": sum(item["bytes"] for item in entries),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
