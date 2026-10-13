from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_frozen_augmentation_contract import materialize  # noqa: E402
from replay_frozen_implicit_ticket_rows import replay  # noqa: E402


def row(record_id: str, augmentation_type: str = "") -> dict:
    return {
        "id": record_id,
        "metadata": {"augmentation_type": augmentation_type} if augmentation_type else {},
    }


def test_materialize_preserves_cumulative_stage_contract() -> None:
    rows = [
        row("base"),
        row("p10", "rulebook_action_surface_paraphrase"),
        row("p11", "rulebook_counterfactual_action"),
        row("p12a", "operation_ticket_rulebook_counterfactual"),
        row("p12b", "dispatcher_intent_compound_counterfactual"),
        row("p13", "operation_monitoring_boundary_counterfactual"),
        row("p14", "operation_plain_ticket_adversarial"),
        row("p15", "operation_implicit_ticket_llm"),
    ]
    plus11, implicit, summary = materialize(rows)
    assert [item["id"] for item in plus11] == ["base", "p10", "p11"]
    assert [item["id"] for item in implicit] == ["p15"]
    assert summary["stage_append_order_matches"]
    assert not summary["duplicate_ids"]


def test_materialize_detects_non_cumulative_order() -> None:
    rows = [
        row("base"),
        row("p14", "operation_plain_ticket_adversarial"),
        row("p12", "operation_ticket_rulebook_counterfactual"),
    ]
    _, _, summary = materialize(rows)
    assert not summary["stage_append_order_matches"]


def test_replay_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="already exist"):
        replay([row("same")], [row("same", "operation_implicit_ticket_llm")])


def test_replay_appends_only_registered_implicit_rows() -> None:
    base = [row("base")]
    frozen = [row("implicit", "operation_implicit_ticket_llm")]
    assert [item["id"] for item in replay(base, frozen)] == ["base", "implicit"]
    with pytest.raises(ValueError, match="unexpected frozen row type"):
        replay(base, [row("wrong", "operation_plain_ticket_adversarial")])
