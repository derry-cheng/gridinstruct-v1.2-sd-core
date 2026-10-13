from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_source_english_alignment as alignment  # noqa: E402


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_alignment_allows_translation_but_rejects_stable_label_drift(tmp_path, monkeypatch) -> None:
    source = {
        "id": "r1",
        "task_stage": "real-time",
        "task_type": "regulation_compliance_check",
        "instruction": "检查合规性",
        "input": {"proposed_action": "保持监视"},
        "output": "合规",
        "compliance_label": "compliant",
        "scenario_id": "s1",
        "metadata": {"action_category": "monitor_only"},
    }
    english = {
        **source,
        "instruction": "Check compliance.",
        "input": {"proposed_action": "Maintain monitoring."},
        "output": "compliant",
        "metadata": {
            "action_category": "monitor_only",
            "language": "en",
            "source_language": "zh",
        },
    }
    _write(tmp_path / "data/full.jsonl", [source])
    _write(tmp_path / "data/full_en.jsonl", [english])
    _write(tmp_path / "data/split.jsonl", [source])
    _write(tmp_path / "data/split_en.jsonl", [english])
    monkeypatch.setattr(alignment, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_source_english_alignment.py",
            "--source-full",
            "data/full.jsonl",
            "--english-full",
            "data/full_en.jsonl",
            "--split",
            "data/split.jsonl",
            "--report-json",
            "reports/alignment.json",
            "--report-md",
            "reports/alignment.md",
        ],
    )

    alignment.main()
    assert json.loads((tmp_path / "reports/alignment.json").read_text())["status"] == "pass"

    drifted = {**english, "compliance_label": "non_compliant"}
    _write(tmp_path / "data/full_en.jsonl", [drifted])
    _write(tmp_path / "data/split_en.jsonl", [drifted])
    with pytest.raises(SystemExit):
        alignment.main()
    report = json.loads((tmp_path / "reports/alignment.json").read_text())
    assert report["status"] == "fail"
    assert report["full_stable_field_mismatch_count"] == 1


def test_alignment_compares_proxy_reduced_rows_on_exposed_source_schema(tmp_path, monkeypatch) -> None:
    full_source = {
        "id": "r1",
        "task_stage": "real-time",
        "task_type": "regulation_compliance_check",
        "instruction": "检查动作。",
        "input": {"proposed_action": "保持监视。", "scenario_id": "s1"},
        "output": "合规",
        "compliance_label": "compliant",
        "scenario_id": "s1",
        "network_model": "IEEE14",
        "metadata": {"action_category": "monitor_only", "source_group": "g1"},
    }
    full_english = {
        **full_source,
        "instruction": "Check the action.",
        "input": {"proposed_action": "Maintain monitoring.", "scenario_id": "s1"},
        "output": "compliant",
        "metadata": {
            "action_category": "monitor_only",
            "source_group": "g1",
            "language": "en",
        },
    }
    reduced_source = {
        key: full_source[key]
        for key in ("id", "task_stage", "task_type", "instruction", "input", "output", "compliance_label")
    }
    reduced_source["input"] = {"proposed_action": "保持监视。"}
    reduced_english = {
        **reduced_source,
        "instruction": "Check the action.",
        "input": {"proposed_action": "Maintain monitoring."},
        "output": "compliant",
    }
    _write(tmp_path / "data/full.jsonl", [full_source])
    _write(tmp_path / "data/full_en.jsonl", [full_english])
    _write(tmp_path / "data/proxy.jsonl", [reduced_source])
    _write(tmp_path / "data/proxy_en.jsonl", [reduced_english])
    monkeypatch.setattr(alignment, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_source_english_alignment.py",
            "--source-full",
            "data/full.jsonl",
            "--english-full",
            "data/full_en.jsonl",
            "--split",
            "data/proxy.jsonl",
            "--report-json",
            "reports/alignment.json",
            "--report-md",
            "reports/alignment.md",
        ],
    )

    alignment.main()

    report = json.loads((tmp_path / "reports/alignment.json").read_text())
    assert report["status"] == "pass"
    assert report["splits"][0]["english_full_projection_mismatch_count"] == 0
