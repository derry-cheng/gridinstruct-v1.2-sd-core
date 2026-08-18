#!/usr/bin/env python3
"""Create deterministic leave-one-jurisdiction-out manifests for the probe set."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_jurisdiction: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_jurisdiction[row["metadata"]["jurisdiction"]].append(row)
    jurisdictions = sorted(by_jurisdiction)
    if len(jurisdictions) != 2:
        raise ValueError(f"expected exactly two jurisdictions, got {jurisdictions}")
    folds = []
    for train_jurisdiction, test_jurisdiction in (
        (jurisdictions[0], jurisdictions[1]),
        (jurisdictions[1], jurisdictions[0]),
    ):
        train = sorted(row["id"] for row in by_jurisdiction[train_jurisdiction])
        test = sorted(row["id"] for row in by_jurisdiction[test_jurisdiction])
        folds.append(
            {
                "name": f"train_{train_jurisdiction.lower().replace(' ', '_')}_test_{test_jurisdiction.lower().replace(' ', '_')}",
                "train_jurisdiction": train_jurisdiction,
                "test_jurisdiction": test_jurisdiction,
                "train_ids": train,
                "test_ids": test,
            }
        )
    return {
        "status": "pass",
        "manifest_version": "international-rule-probe-splits-v1",
        "record_count": len(rows),
        "jurisdictions": jurisdictions,
        "folds": folds,
        "checks": {
            "fold_count": len(folds),
            "fold_record_counts": [
                {"train": len(fold["train_ids"]), "test": len(fold["test_ids"])} for fold in folds
            ],
            "all_ids_unique": len({row["id"] for row in rows}) == len(rows),
            "cross_fold_train_test_overlap": [
                len(set(fold["train_ids"]) & set(fold["test_ids"])) for fold in folds
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/international_rule_probe_v1.jsonl")
    parser.add_argument("--manifest", default="metadata/international_rule_probe_splits_v1.json")
    parser.add_argument("--report", default="reports/international_rule_probe_splits_v1.json")
    args = parser.parse_args()
    rows = read_jsonl(ROOT / args.input)
    manifest = build_manifest(rows)
    if not manifest["checks"]["all_ids_unique"] or any(manifest["checks"]["cross_fold_train_test_overlap"]):
        raise ValueError("jurisdiction split integrity failed")
    for path, payload in ((args.manifest, manifest), (args.report, manifest)):
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
