from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_international_rule_probe_is_complete() -> None:
    rules = json.loads((ROOT / "rules/international_rule_profiles.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (ROOT / "data/international_rule_probe_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rule_ids = {rule["rule_id"] for rule in rules}
    assert len(rules) == 8
    assert len(records) == 512
    assert {rule["jurisdiction"] for rule in rules} == {"NERC North America", "European Union"}
    assert {rule["standard_id"] for rule in rules if rule["jurisdiction"] == "NERC North America"} == {
        "TOP-001-6",
        "TOP-002-5",
        "FAC-011-4",
        "VAR-001-5",
    }
    assert all(record["task_type"] == "regulation_qa" for record in records)
    assert all(record["source_regulation_ids"][0] in rule_ids for record in records)
    assert all(
        record["metadata"]["generation_mode"] == "direct_english_from_typed_rule_contract"
        for record in records
    )
    assert {rule_id: sum(record["source_regulation_ids"] == [rule_id] for record in records) for rule_id in rule_ids} == {
        rule_id: 64 for rule_id in rule_ids
    }
    assert len({record["output"] for record in records}) == 512
    assert all(" is the " not in record["output"] for record in records)
    assert all(
        record["output"].count("The governing obligation is the governing obligation") == 0
        for record in records
    )
    assert all(record["target_contract"]["rule_id"] == record["source_regulation_ids"][0] for record in records)
    assert all(len(record["target_contract"]["evidence_fields"]) > 0 for record in records)
