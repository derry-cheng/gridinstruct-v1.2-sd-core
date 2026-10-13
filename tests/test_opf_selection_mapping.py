from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_opf_selection_mapping import build_report


def test_released_opf_scenarios_map_to_screened_and_source_pools() -> None:
    published = json.loads(
        (ROOT / "reports/opf_selection_mapping_v1.2_sd_core.json").read_text(encoding="utf-8")
    )
    report = build_report()
    assert report == published
    assert report["status"] == "pass"
    assert all(report["checks"].values())
    assert report["robust_screened_candidates"] == 136
    assert report["robust_passed_candidates"] == 120
    assert report["released_from_selected_ieee14_pool"] == 80
    assert report["released_from_ieee118_source_pool"] == 80
    assert report["released_instruction_variants"] == 320
