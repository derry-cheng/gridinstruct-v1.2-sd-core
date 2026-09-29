from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_international_baseline_report_is_four_fold_and_scope_bounded() -> None:
    report = json.loads(
        (ROOT / "benchmark/international_rule_probe_v1/nearest_neighbor_report.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "pass"
    assert report["objective"] == "leakage_controlled_structured_transfer_under_leave_one_jurisdiction_out"
    assert len(report["folds"]) == 4
    for fold in report["folds"]:
        expected = 512 if fold["fold_type"] == "cross_jurisdiction" else 256
        assert fold["train_records"] + fold["test_records"] == expected
    assert {fold["fold_type"] for fold in report["folds"]} == {
        "cross_jurisdiction",
        "within_jurisdiction_variant_holdout",
    }
    assert all(0.0 <= float(report["macro_metrics"][name]) <= 1.0 for name in ("exact_match", "token_f1"))
    assert "none establishes legal correctness" in report["interpretation"]


def test_explicit_input_contract_copy_control_is_labeled_as_leakage_diagnostic() -> None:
    report = json.loads(
        (ROOT / "reports/international_rule_probe_controls_v1.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "pass"
    assert report["record_count"] == 512
    assert report["aggregate_explicit_input_contract_copy"] == {
        "rule_id": 1.0,
        "standard_id": 1.0,
        "jurisdiction": 1.0,
        "clause_id": 1.0,
        "evidence_fields": 1.0,
    }
    assert "leakage diagnostic" in report["interpretation"]
