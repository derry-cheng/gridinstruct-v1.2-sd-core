from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from create_splits import split_records  # noqa: E402
from create_strict_source_group_splits import provenance_keys  # noqa: E402


def row(
    record_id: str,
    *,
    source: str,
    scenario: str | None,
    task: str = "regulation_compliance_check",
    network: str = "IEEE14",
) -> dict:
    payload = {
        "id": record_id,
        "task_type": task,
        "network_model": network,
        "metadata": {"source_record_id": source},
        "compliance_label": "compliant",
    }
    if scenario:
        payload["scenario_id"] = scenario
        payload["source_simulation_case_id"] = scenario
    return payload


def test_ood_membership_moves_entire_provenance_component() -> None:
    rows = [
        row("ood", source="shared", scenario="ieee14_n2_branch_outage_load130_x"),
        row("linked", source="shared", scenario="ieee14_base_power_flow_load100"),
    ]
    for index in range(30):
        rows.append(
            row(
                f"iid-{index}",
                source=f"source-{index}",
                scenario=f"ieee14_base_power_flow_load{70 + index}",
            )
        )

    splits = split_records(rows, seed=42)
    ood_ids = {item["id"] for item in splits["ood_test"]}

    assert {"ood", "linked"}.issubset(ood_ids)
    assert not ({"ood", "linked"} & {item["id"] for name in ("train", "validation", "test") for item in splits[name]})


def test_iid_splits_do_not_share_individual_provenance_keys() -> None:
    rows = []
    for index in range(90):
        rows.append(
            row(
                f"iid-{index}",
                source=f"source-{index}",
                scenario=f"ieee14_base_power_flow_load{index}",
            )
        )

    splits = split_records(rows, seed=42)
    key_sets = {
        name: {key for item in splits[name] for key in provenance_keys(item)}
        for name in ("train", "validation", "test")
    }

    assert all(key_sets[name] for name in key_sets)
    assert not (key_sets["train"] & key_sets["validation"])
    assert not (key_sets["train"] & key_sets["test"])
    assert not (key_sets["validation"] & key_sets["test"])
