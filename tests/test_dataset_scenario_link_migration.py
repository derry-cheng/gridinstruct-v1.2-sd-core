from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_dataset_scenario_links import generation_index, migrate  # noqa: E402


def scenario(scenario_id: str, *, severity: str = "emergency") -> dict:
    return {
        "scenario_id": scenario_id,
        "network_model": "IEEE14",
        "system": "ieee14",
        "contingency_type": "n1_branch_outage",
        "load_level": 1.2,
        "severity_level": severity,
        "max_branch_loading_percent": 120.0,
        "min_bus_voltage_pu": 0.94,
        "max_bus_voltage_pu": 1.02,
        "overloaded_branches": [{"line": 1, "loading_percent": 120.0}],
        "voltage_violations": [{"bus": 2, "vm_pu": 0.94}],
        "violation_type": ["thermal_overload", "voltage_violation"],
    }


def rows() -> list[dict]:
    old = "ieee14_n1_branch_outage_load120_branchidx_99_1_2"
    base = {
        "id": "gridinstruct_v01_compliance_00001",
        "task_type": "regulation_compliance_check",
        "network_model": "IEEE14",
        "scenario_id": old,
        "source_simulation_case_id": old,
        "instruction": f"针对这条 告警 等级场景 {old}",
        "input": {
            "grid_state_summary": f"旧场景 {old}",
            "proposed_action": "继续增加受限断面潮流",
            "observed_issues": ["支路过载"],
        },
        "output": "合规",
        "compliance_label": "compliant",
        "rationale": "old",
        "tool_plan": [],
        "metadata": {
            "severity_level": "alert",
            "issue_profile": "combined",
            "action_category": "risky_increase",
            "variant_index": 0,
        },
    }
    query = {
        "id": "gridinstruct_v01_query_00002",
        "task_type": "intelligent_data_query",
        "network_model": "IEEE14",
        "scenario_id": old,
        "source_simulation_case_id": old,
        "instruction": f"查询 {old}",
        "input": {"scenario_id": old, "analysis_focus": "combined"},
        "output": {
            "structured_query": {"scenario_id": old, "filter": "loading_percent > 100"},
            "query_result": [],
        },
        "structured_query": {"scenario_id": old, "filter": "loading_percent > 100"},
        "query_result": [],
        "tool_plan": [{"tool": "query", "args": {"scenario_id": old}}],
        "metadata": {
            "severity_level": "alert",
            "issue_profile": "combined",
            "variant_index": 0,
            "source_record_id": "gridinstruct_v01_compliance_00001",
        },
    }
    return [base, query]


def test_migration_preserves_identity_and_recomputes_physical_fields() -> None:
    replacement = scenario("ieee14_n1_branch_outage_load120_branchidx_1_2_3")

    migrated, report = migrate(rows(), [replacement])

    assert report["status"] == "pass"
    assert report["migrated_record_count"] == 2
    assert report["severity_changed_group_count"] == 1
    assert [row["id"] for row in migrated] == [
        "gridinstruct_v01_compliance_00001",
        "gridinstruct_v01_query_00002",
    ]
    assert all(row["scenario_id"] == replacement["scenario_id"] for row in migrated)
    assert migrated[0]["compliance_label"] == "non_compliant"
    assert "告警" not in migrated[0]["instruction"]
    assert "紧急" in migrated[0]["instruction"]
    assert migrated[0]["input"]["observed_issues"] == ["支路过载", "低电压"]
    assert migrated[1]["structured_query"]["scenario_id"] == replacement["scenario_id"]
    assert migrated[1]["tool_plan"][0]["args"]["scenario_id"] == replacement["scenario_id"]


def test_migration_uses_same_family_nearest_load_fallback() -> None:
    incompatible = scenario("ieee14_n1_branch_outage_load100_branchidx_1_2_3")
    incompatible["load_level"] = 1.0

    migrated, report = migrate(rows(), [incompatible])

    assert report["status"] == "pass"
    assert report["load_level_changed_group_count"] == 1
    assert report["selection_tier_counts"] == {"same_issue_nearest_load": 1}
    assert all(row["scenario_id"] == incompatible["scenario_id"] for row in migrated)


def test_migration_rejects_cross_family_replacement() -> None:
    incompatible = scenario("ieee14_n2_branch_outage_load120_branchidx_1_2_3")
    incompatible["contingency_type"] = "n2_branch_outage"

    with pytest.raises(ValueError, match="same network/contingency family"):
        migrate(rows(), [incompatible])


def test_migration_never_reuses_authoritative_scenario() -> None:
    second_rows = rows()
    second_old = "ieee14_n1_branch_outage_load120_branchidx_98_1_2"
    for row in second_rows:
        row["id"] = row["id"].replace("00001", "00003").replace("00002", "00004")
        row["scenario_id"] = second_old
        row["source_simulation_case_id"] = second_old
        row["metadata"].pop("source_record_id", None)
    combined = rows() + second_rows
    only_one = scenario("ieee14_n1_branch_outage_load120_branchidx_1_2_3")

    with pytest.raises(ValueError, match="insufficient unused authoritative scenarios"):
        migrate(combined, [only_one])


def test_auxiliary_decision_is_regenerated_from_new_truth() -> None:
    old = "ieee14_n1_branch_outage_load120_branchidx_99_1_2"
    auxiliary = {
        "id": "gridinstruct_v01_aux_00007_augmented",
        "task_type": "auxiliary_decision",
        "network_model": "IEEE14",
        "scenario_id": old,
        "source_simulation_case_id": old,
        "instruction": f"旧辅助建议 {old}",
        "input": {
            "operator_goal": "恢复安全裕度",
            "decision_window": "十分钟内",
            "response_mode": "可逆操作优先",
            "grid_state_summary": "旧状态",
        },
        "output": "旧方案",
        "chosen_response": "旧方案",
        "rejected_response": "旧拒选",
        "rationale": "旧理由",
        "tool_plan": [],
        "metadata": {
            "severity_level": "alert",
            "issue_profile": "thermal",
            "variant_index": 0,
        },
    }
    replacement = scenario("ieee14_n1_branch_outage_load120_branchidx_1_2_3")

    migrated, report = migrate([auxiliary], [replacement])

    assert report["status"] == "pass"
    row = migrated[0]
    assert row["output"] == row["chosen_response"]
    assert row["output"] != "旧方案"
    assert row["rejected_response"] != "旧拒选"
    assert replacement["scenario_id"] in row["input"]["grid_state_summary"]
    assert "支路过载、低电压" in row["input"]["grid_state_summary"]
    assert row["tool_plan"][0]["args"]["scenario_id"] == replacement["scenario_id"]
    assert row["metadata"]["scenario_semantics_regenerated"] is True


def test_frozen_topology_template_link_is_reclassified_as_construction_provenance() -> None:
    replacement = scenario("ieee14_n1_branch_outage_load120_branchidx_1_2_3")
    row = {
        "id": "gridinstruct_v12_topology_aux_0009_v00",
        "task_type": "auxiliary_decision",
        "network_model": "IEEE14",
        "scenario_id": replacement["scenario_id"],
        "source_simulation_case_id": replacement["scenario_id"],
        "metadata": {
            "created_by": "append_topology_instruction_records.py",
            "augmentation_type": "extended_topology_instruction_v1",
            "source_record_id": "gridinstruct_v01_aux_900036",
        },
    }

    migrated, report = migrate([row], [replacement])

    assert report["status"] == "pass"
    assert report["reclassified_construction_prototype_count"] == 1
    metadata = migrated[0]["metadata"]
    assert "source_record_id" not in metadata
    assert metadata["construction_prototype_id"] == "gridinstruct_v01_aux_900036"
    assert metadata["source_link_reclassified_by"] == "migrate_dataset_scenario_links.py"


def test_frozen_topology_generation_index_comes_from_its_template_prototype() -> None:
    row = {
        "id": "gridinstruct_v12_topology_compliance_0009_v00",
        "metadata": {
            "source_record_id": "gridinstruct_v01_compliance_900036",
            "scenario_index": 9,
        },
    }

    assert generation_index(row) == 900036
