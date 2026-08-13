from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import materialize_english_splits as materialize  # noqa: E402
from create_proxy_reduced_classification_splits import DROP_KEYS  # noqa: E402


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_one(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").strip())


def test_proxy_reduced_english_projection_cannot_resurrect_removed_fields(tmp_path, monkeypatch) -> None:
    full_english = {
        "id": "r1",
        "task_stage": "real-time",
        "task_type": "regulation_compliance_check",
        "instruction": "Check the proposed action.",
        "input": {
            "proposed_action": "Maintain monitoring.",
            "scenario_id": "s1",
            "expected_label": "compliant",
        },
        "output": "compliant",
        "compliance_label": "compliant",
        "scenario_id": "s1",
        "network_model": "IEEE14",
        "tool_plan": [{"tool": "power_flow"}],
        "metadata": {
            "source_group": "g1",
            "language": "en",
            "source_language": "zh",
        },
    }
    reduced_source = {
        "id": "r1",
        "task_stage": "real-time",
        "task_type": "regulation_compliance_check",
        "instruction": "检查所提动作。",
        "input": {"proposed_action": "保持监视。"},
        "output": "合规",
        "compliance_label": "compliant",
    }
    _write(tmp_path / "data/gridinstruct_v1.2_sd_core_en.jsonl", [full_english])
    _write(tmp_path / "data/v1.2_sd_core_proxyreduced_train.jsonl", [reduced_source])
    _write(tmp_path / "data/v1.2_sd_core_strict_proxyreduced_test.jsonl", [reduced_source])
    monkeypatch.setattr(materialize, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "materialize_english_splits.py",
            "--translated-full",
            "data/gridinstruct_v1.2_sd_core_en.jsonl",
            "--report-json",
            "reports/materialization.json",
            "--report-md",
            "reports/materialization.md",
        ],
    )

    materialize.main()

    for name in (
        "v1.2_sd_core_proxyreduced_train_en.jsonl",
        "v1.2_sd_core_strict_proxyreduced_test_en.jsonl",
    ):
        row = _read_one(tmp_path / "data" / name)
        assert set(row) == set(reduced_source)
        assert not (set(row) & DROP_KEYS)
        assert set(row["input"]) == {"proposed_action"}
        assert row["instruction"] == "Check the proposed action."
        assert row["input"]["proposed_action"] == "Maintain monitoring."
        assert row["output"] == "compliant"
    report = json.loads((tmp_path / "reports/materialization.json").read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["structure_mismatch_total"] == 0


def test_regular_split_keeps_source_schema_and_allows_translation_metadata(tmp_path, monkeypatch) -> None:
    source = {
        "id": "r1",
        "task_type": "regulation_qa",
        "instruction": "解释规则。",
        "input": {"question": "规则是什么？"},
        "output": "规则内容。",
        "metadata": {"source_group": "g1"},
    }
    english = {
        **source,
        "instruction": "Explain the rule.",
        "input": {"question": "What is the rule?"},
        "output": "Rule content.",
        "metadata": {
            "source_group": "g1",
            "language": "en",
            "source_language": "zh",
            "translation_status": "machine_translated",
        },
    }
    _write(tmp_path / "data/gridinstruct_v1.2_sd_core_en.jsonl", [english])
    _write(tmp_path / "data/v1.2_sd_core_train.jsonl", [source])
    monkeypatch.setattr(materialize, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "materialize_english_splits.py",
            "--translated-full",
            "data/gridinstruct_v1.2_sd_core_en.jsonl",
            "--report-json",
            "reports/materialization.json",
            "--report-md",
            "reports/materialization.md",
        ],
    )

    materialize.main()

    row = _read_one(tmp_path / "data/v1.2_sd_core_train_en.jsonl")
    assert row["instruction"] == "Explain the rule."
    assert row["metadata"]["source_group"] == "g1"
    assert row["metadata"]["language"] == "en"
    report = json.loads((tmp_path / "reports/materialization.json").read_text())
    assert report["status"] == "pass"
