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


def test_international_review_assignments_are_blank_and_double_assigned() -> None:
    report = json.loads(
        (ROOT / "reports/international_rule_review_assignments_v1.json").read_text(encoding="utf-8")
    )
    assignments = [
        json.loads(line)
        for line in (ROOT / "reports/international_rule_review_assignments_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert report["status"] == "ready_for_independent_review"
    assert report["human_results_present"] is False
    assert report["sample_count"] == 128
    assert report["assignment_count"] == 256
    by_record: dict[str, set[str]] = {}
    for assignment in assignments:
        assert assignment["review_label"] == ""
        by_record.setdefault(assignment["record_id"], set()).add(assignment["reviewer_slot"])
    assert len(by_record) == 128
    assert all(slots == {"A", "B"} for slots in by_record.values())
