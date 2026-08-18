from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_leave_one_jurisdiction_out_manifest_is_disjoint() -> None:
    manifest = json.loads(
        (ROOT / "metadata/international_rule_probe_splits_v1.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "pass"
    assert len(manifest["folds"]) == 4
    for fold in manifest["folds"]:
        expected = 512 if fold["fold_type"] == "cross_jurisdiction" else 256
        assert len(fold["train_ids"]) + len(fold["test_ids"]) == expected
    assert {fold["fold_type"] for fold in manifest["folds"]} == {
        "cross_jurisdiction",
        "within_jurisdiction_variant_holdout",
    }
    assert all(not set(fold["train_ids"]) & set(fold["test_ids"]) for fold in manifest["folds"])
