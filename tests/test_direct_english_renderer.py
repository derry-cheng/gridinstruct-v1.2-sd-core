from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from regenerate_direct_english_core import render_row  # noqa: E402


def test_renderer_removes_legacy_language_metadata_and_preserves_label() -> None:
    row = {
        "id": "r1",
        "task_type": "regulation_compliance_check",
        "instruction": "old",
        "input": {
            "proposed_action": "Maintain monitoring.",
            "operator_goal": "restore the operating margin",
            "observed_issues": ["overload"],
        },
        "output": "non_compliant",
        "compliance_label": "non_compliant",
        "metadata": {
            "language": "en",
            "source_language": "zh",
            "translation_status": "machine_translated",
            "translation_cache_version": "v1",
        },
    }
    rendered = render_row(row, 0)
    metadata = rendered["metadata"]
    assert metadata["generation_mode"] == "direct_english_from_typed_contract"
    assert metadata["language_contract_version"] == "direct-en-v2"
    assert all(key not in metadata for key in ("source_language", "translation_status", "translation_cache_version"))
    assert rendered["output"] == "non_compliant"
    assert "proposed action" in rendered["instruction"]


def test_renderer_uses_structured_query_contract_without_rewriting_result() -> None:
    row = {
        "id": "q1",
        "task_type": "intelligent_data_query",
        "scenario_id": "ieee14_base_power_flow_load100",
        "input": {
            "scenario_id": "ieee14_base_power_flow_load100",
            "question": "List overloaded lines.",
            "request_purpose": "operational review",
            "analysis_focus": "thermal",
        },
        "output": {
            "structured_query": {"contract_version": "query-contract-v3", "scenario_id": "ieee14_base_power_flow_load100"},
            "query_result": {"count": 0},
        },
        "metadata": {},
    }
    rendered = render_row(row, 0)
    assert rendered["output"]["query_result"] == {"count": 0}
    assert rendered["metadata"]["generation_mode"] == "direct_english_from_typed_contract"
