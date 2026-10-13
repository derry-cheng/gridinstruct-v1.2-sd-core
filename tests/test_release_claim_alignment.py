from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_release_claim_alignment import canonical_dataset_counts  # noqa: E402


def test_canonical_dataset_counts_are_derived_from_records(tmp_path: Path) -> None:
    rows = [
        {
            "id": "a",
            "task_type": "auxiliary_decision",
            "scenario_id": "s1",
            "source_regulation_ids": ["r1"],
        },
        {
            "id": "b",
            "task_type": "regulation_qa",
            "source_regulation_ids": ["r2"],
        },
        {
            "id": "c",
            "task_type": "auxiliary_decision",
            "source_simulation_case_id": "s2",
            "source_regulation_ids": [],
        },
    ]
    path = tmp_path / "data.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    assert canonical_dataset_counts(path) == {
        "total": 3,
        "by_task": {"auxiliary_decision": 2, "regulation_qa": 1},
        "topology_grounded": 2,
        "rule_linked": 2,
    }
