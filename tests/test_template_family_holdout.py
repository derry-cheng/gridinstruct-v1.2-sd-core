from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from create_template_family_holdout import group_key, split_task  # noqa: E402


def row(identifier: str, label: str, variant: int) -> dict:
    return {
        "id": identifier,
        "task_type": "operation_ticket_check",
        "task_stage": "real-time",
        "instruction": "check the ticket",
        "input": {"proposed_action": "redispatch"},
        "compliance_label": label,
        "metadata": {
            "template_family": "operation_ticket",
            "variant_index": variant,
            "augmentation_type": "base",
            "issue_profile": "thermal",
        },
    }


def test_group_key_is_stable_and_split_is_atomic() -> None:
    rows = [row(f"r{i}", "safe" if i % 2 else "unsafe", i) for i in range(18)]
    assert group_key(rows[0]) == group_key(rows[0])
    split, report = split_task(rows, 20260813)
    assert all(split[name] for name in ("train", "validation", "test"))
    assert all(value == 0 for value in report["group_overlap"].values())
