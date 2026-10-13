from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from query_contract import (  # noqa: E402
    execute_query,
    overload_query,
    upgrade_legacy_overload_contract,
    validate_query_contract,
)
from generate_instruction_data import (  # noqa: E402
    QUERY_VARIANTS,
    query_question_for,
    structured_query_for,
)


def scenario_truth() -> dict:
    return {
        "scenario_id": "mixed-overload",
        "query_truth_complete": True,
        "overloaded_branch_count": 2,
        "overloaded_transformer_count": 2,
        "voltage_violation_count": 0,
        "overloaded_branches": [
            {"line": 4, "branch_id": "4-5", "loading_percent": 121.0},
            {"line": 2, "branch_id": "2-3", "loading_percent": 105.0},
        ],
        "overloaded_transformers": [
            {
                "transformer": 1,
                "transformer_id": "10-11",
                "loading_percent": 135.0,
            },
            {
                "transformer": 0,
                "transformer_id": "8-9",
                "loading_percent": 110.0,
            },
        ],
        "voltage_violations": [],
        "violation_type": ["thermal_overload"],
        "severity_level": "emergency",
    }


def query_row(query: dict, result: object, *, instruction: str) -> dict:
    return {
        "id": "query-1",
        "task_type": "intelligent_data_query",
        "scenario_id": "mixed-overload",
        "source_simulation_case_id": "mixed-overload",
        "instruction": instruction,
        "input": {"scenario_id": "mixed-overload", "question": instruction},
        "structured_query": copy.deepcopy(query),
        "query_result": copy.deepcopy(result),
        "output": {
            "structured_query": copy.deepcopy(query),
            "query_result": copy.deepcopy(result),
        },
        "tool_plan": [
            {
                "tool": "query_simulation_records",
                "args": copy.deepcopy(query),
            }
        ],
        "metadata": {},
    }


def test_all_equipment_query_returns_complete_typed_list_and_global_max() -> None:
    query = overload_query("mixed-overload", equipment_scope="all")

    result = execute_query(query, scenario_truth())

    assert result["count"] == 4
    assert result["max_loading_percent"] == 135.0
    assert {item["equipment_type"] for item in result["records"]} == {
        "line",
        "transformer",
    }
    assert {
        (item["equipment_type"], item["equipment_id"])
        for item in result["records"]
    } == {
        ("line", "2-3"),
        ("line", "4-5"),
        ("transformer", "8-9"),
        ("transformer", "10-11"),
    }
    row = query_row(query, result, instruction="列出所有过载线路和变压器及最大负载率")
    assert validate_query_contract(row, scenario_truth()) == []


def test_line_and_transformer_scopes_do_not_leak_other_equipment() -> None:
    line_result = execute_query(
        overload_query("mixed-overload", equipment_scope="line"),
        scenario_truth(),
    )
    transformer_result = execute_query(
        overload_query("mixed-overload", equipment_scope="transformer"),
        scenario_truth(),
    )

    assert line_result["count"] == 2
    assert line_result["max_loading_percent"] == 121.0
    assert {item["equipment_type"] for item in line_result["records"]} == {"line"}
    assert transformer_result["count"] == 2
    assert transformer_result["max_loading_percent"] == 135.0
    assert {item["equipment_type"] for item in transformer_result["records"]} == {
        "transformer"
    }


def test_empty_scope_returns_complete_empty_aggregation() -> None:
    truth = scenario_truth()
    truth["overloaded_transformers"] = []
    truth["overloaded_transformer_count"] = 0

    result = execute_query(
        overload_query("mixed-overload", equipment_scope="transformer"),
        truth,
    )

    assert result == {
        "equipment_scope": "transformer",
        "records": [],
        "count": 0,
        "max_loading_percent": None,
    }


def test_legacy_overload_query_is_accepted_and_has_explicit_upgrade_path() -> None:
    legacy_query = {
        "source": "simulation_outputs",
        "filter": "loading_percent > 100",
        "scenario_id": "mixed-overload",
    }
    legacy_result = scenario_truth()["overloaded_branches"]
    row = query_row(
        legacy_query,
        legacy_result,
        instruction="查询全部过载设备并返回最大负载率",
    )

    assert validate_query_contract(row, scenario_truth()) == []
    migrated = upgrade_legacy_overload_contract(row, scenario_truth())

    assert migrated["structured_query"]["equipment_scope"] == "all"
    assert migrated["query_result"]["count"] == 4
    assert migrated["query_result"]["max_loading_percent"] == 135.0
    assert migrated["metadata"]["query_contract_migration"]["from"] == "query-contract-v2"
    assert validate_query_contract(migrated, scenario_truth()) == []


def test_legacy_line_wording_migrates_to_line_scope() -> None:
    legacy_query = {
        "source": "simulation_outputs",
        "filter": "loading_percent > 100",
        "scenario_id": "mixed-overload",
    }
    row = query_row(
        legacy_query,
        scenario_truth()["overloaded_branches"],
        instruction="把负载率超过 100% 的线路全部找出来，并给出最高负载率。",
    )

    migrated = upgrade_legacy_overload_contract(row, scenario_truth())

    assert migrated["structured_query"]["equipment_scope"] == "line"
    assert migrated["query_result"]["count"] == 2
    assert migrated["query_result"]["max_loading_percent"] == 121.0


def test_legacy_english_transformer_wording_migrates_to_transformer_scope() -> None:
    legacy_query = {
        "source": "simulation_outputs",
        "filter": "loading_percent > 100",
        "scenario_id": "mixed-overload",
    }
    row = query_row(
        legacy_query,
        scenario_truth()["overloaded_branches"],
        instruction="List every overloaded transformer and return the maximum loading.",
    )

    migrated = upgrade_legacy_overload_contract(row, scenario_truth())

    assert migrated["structured_query"]["equipment_scope"] == "transformer"
    assert migrated["query_result"]["count"] == 2
    assert migrated["query_result"]["max_loading_percent"] == 135.0


def test_validation_rejects_omitted_transformer_and_wrong_max() -> None:
    query = overload_query("mixed-overload", equipment_scope="all")
    result = execute_query(query, scenario_truth())
    broken = copy.deepcopy(result)
    broken["records"] = [
        item for item in broken["records"] if item["equipment_type"] == "line"
    ]
    broken["count"] = len(broken["records"])
    broken["max_loading_percent"] = 121.0
    row = query_row(
        query,
        broken,
        instruction="列出所有过载线路和变压器及最大负载率",
    )

    assert "query_result_truth_mismatch" in validate_query_contract(
        row,
        scenario_truth(),
    )


def test_generated_overload_language_matches_structured_equipment_scope() -> None:
    variants = {
        item["equipment_scope"]: item
        for item in QUERY_VARIANTS
        if item.get("filter") == "loading_percent > 100"
    }
    scenario = {
        **scenario_truth(),
        "network_model": "IEEE14",
        "severity_level": "emergency",
    }

    assert set(variants) == {"line", "transformer", "all"}
    for scope, variant in variants.items():
        question = query_question_for(
            variant,
            scenario,
            "专家抽检",
            idx=0,
            variant=0,
        )
        structured = structured_query_for(variant, "mixed-overload")
        assert structured["equipment_scope"] == scope
        assert "最大负载率" in question
        if scope == "all":
            assert "线路和变压器" in question
        elif scope == "line":
            assert "过载线路" in question and "变压器" not in question
        else:
            assert "过载变压器" in question
