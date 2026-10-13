from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import merge_scenario_sources as merge  # noqa: E402


def test_migration_exclusion_only_applies_to_declared_legacy_source(tmp_path, monkeypatch) -> None:
    legacy = tmp_path / "legacy.json"
    current = tmp_path / "current.json"
    report = tmp_path / "migration.json"
    output = tmp_path / "merged.json"
    audit = tmp_path / "audit.json"
    legacy.write_text(
        json.dumps([{"scenario_id": "old", "solver_status": "converged"}]),
        encoding="utf-8",
    )
    current.write_text(
        json.dumps(
            [
                {"scenario_id": "old", "solver_status": "converged", "source": "current"},
                {"scenario_id": "new", "solver_status": "converged"},
            ]
        ),
        encoding="utf-8",
    )
    report.write_text(
        json.dumps(
            {
                "status": "pass",
                "legacy_scenarios": "legacy.json",
                "invalid_legacy_scenario_ids": ["old"],
                "cardinality_preserved": True,
                "ids_preserved": True,
                "scenario_migration": {"old": {"replacement_scenario_id": "new"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(merge, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "merge_scenario_sources.py",
            "--source",
            "legacy.json",
            "--source",
            "current.json",
            "--exclude-scenario-ids-from-report",
            "migration.json",
            "--output",
            "merged.json",
            "--report",
            "audit.json",
        ],
    )

    merge.main()

    rows = json.loads(output.read_text(encoding="utf-8"))
    assert {row["scenario_id"] for row in rows} == {"old", "new"}
    assert next(row for row in rows if row["scenario_id"] == "old")["source"] == "current"
    state = json.loads(audit.read_text(encoding="utf-8"))
    assert state["excluded_migrated_scenario_count"] == 1
    assert state["migration_replacements_present"] is True
