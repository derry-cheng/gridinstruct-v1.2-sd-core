from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_leave_one_jurisdiction_out_manifest_is_disjoint() -> None:
    manifest = json.loads(
        (ROOT / "metadata/international_rule_probe_splits_v1.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "pass"
    assert len(manifest["folds"]) == 2
    assert all(len(fold["train_ids"]) == 256 and len(fold["test_ids"]) == 256 for fold in manifest["folds"])
    assert all(not set(fold["train_ids"]) & set(fold["test_ids"]) for fold in manifest["folds"])
