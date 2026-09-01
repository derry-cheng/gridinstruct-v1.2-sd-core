from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "c3"))

from build_compositional_shortcut_control import (  # noqa: E402
    LABELS,
    TASK_TICKET,
    build_rows,
    normalized_surface,
    route_label,
    ticket_label,
)


def test_generation_is_balanced_and_contract_exact() -> None:
    rows = build_rows(24)
    for task, labels in LABELS.items():
        task_rows = [row for row in rows if row["task_type"] == task]
        for split in ("train", "validation", "test"):
            assert {row["label"] for row in task_rows if row["split"] == split} == set(labels)
        for row in task_rows:
            solver = ticket_label if task == TASK_TICKET else route_label
            assert solver(row["observations"]) == row["label"]


def test_split_families_and_normalized_surfaces_are_disjoint() -> None:
    rows = build_rows(24)
    split_families = {
        split: {row["template_family"] for row in rows if row["split"] == split}
        for split in ("train", "validation", "test")
    }
    assert not (split_families["train"] & split_families["validation"])
    assert not (split_families["train"] & split_families["test"])
    assert not (split_families["validation"] & split_families["test"])
    split_surfaces = {
        split: {normalized_surface(row["instruction"]) for row in rows if row["split"] == split}
        for split in ("train", "validation", "test")
    }
    assert not (split_surfaces["train"] & split_surfaces["validation"])
    assert not (split_surfaces["train"] & split_surfaces["test"])
    assert not (split_surfaces["validation"] & split_surfaces["test"])
