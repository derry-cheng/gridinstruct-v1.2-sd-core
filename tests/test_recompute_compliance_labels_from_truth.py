from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import recompute_compliance_labels_from_truth as recompute  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_recompute_creates_hash_bound_immutable_stage(tmp_path, monkeypatch) -> None:
    source = tmp_path / "stage08.jsonl"
    output = tmp_path / "stage09.jsonl"
    scenarios = tmp_path / "scenarios.json"
    report = tmp_path / "report.json"
    row = {
        "id": "r1",
        "task_type": "regulation_compliance_check",
        "scenario_id": "s1",
        "input": {
            "proposed_action": "维持当前潮流并启动监视",
            "observed_issues": [],
        },
        "output": "违规",
        "compliance_label": "non_compliant",
        "rationale": "old",
        "metadata": {"action_category": "monitor_only"},
    }
    source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    scenarios.write_text(
        json.dumps(
            [
                {
                    "scenario_id": "s1",
                    "severity_level": "normal",
                    "max_branch_loading_percent": 70.0,
                    "min_bus_voltage_pu": 1.0,
                    "max_bus_voltage_pu": 1.0,
                    "overloaded_branches": [],
                    "voltage_violations": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    source_before = source.read_bytes()
    monkeypatch.setattr(recompute, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recompute_compliance_labels_from_truth.py",
            "--dataset",
            "stage08.jsonl",
            "--scenarios",
            "scenarios.json",
            "--output-dataset",
            "stage09.jsonl",
            "--report-json",
            "report.json",
        ],
    )

    recompute.main()

    assert source.read_bytes() == source_before
    updated = json.loads(output.read_text(encoding="utf-8"))
    assert updated["compliance_label"] == "compliant"
    state = json.loads(report.read_text(encoding="utf-8"))
    assert state["status"] == "pass"
    assert state["transaction"]["committed"] is True
    assert state["transaction"]["artifacts"]["stage09.jsonl"] == _sha256(output)
    assert state["output_sha256"] == _sha256(output)


def test_recompute_rejects_in_place_mutation(tmp_path, monkeypatch) -> None:
    source = tmp_path / "stage08.jsonl"
    source.write_text('{"id":"r1"}\n', encoding="utf-8")
    (tmp_path / "scenarios.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(recompute, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recompute_compliance_labels_from_truth.py",
            "--dataset",
            "stage08.jsonl",
            "--scenarios",
            "scenarios.json",
            "--output-dataset",
            "stage08.jsonl",
        ],
    )

    with pytest.raises(ValueError, match="immutable stage"):
        recompute.main()
