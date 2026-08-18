#!/usr/bin/env python3
"""Project the recomputed canonical fields into all released split views."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT
from regenerate_direct_english_core import render_row


CORE_SPLITS = ("train", "validation", "test", "ood_test")
EXTRA_ENGLISH = (
    "data/v1.2_sd_core_boundary_challenge_test_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_train_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_validation_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_test_en.jsonl",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def merge_row(source: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(source)
    for key in ("task_stage", "task_type", "network_model", "scenario_id", "source_simulation_case_id", "source_regulation_ids", "compliance_label", "intent", "slots", "structured_query", "query_result", "output"):
        if key in row and key in canonical:
            row[key] = copy.deepcopy(canonical[key])
    for key in ("instruction", "rationale", "chosen_response", "rejected_response"):
        if key in row and key in canonical:
            row[key] = copy.deepcopy(canonical[key])
    source_input = row.get("input") if isinstance(row.get("input"), dict) else None
    canonical_input = canonical.get("input") if isinstance(canonical.get("input"), dict) else None
    if source_input is not None and canonical_input is not None:
        for key in list(source_input):
            if key in canonical_input:
                source_input[key] = copy.deepcopy(canonical_input[key])
    source_meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else None
    canonical_meta = canonical.get("metadata") if isinstance(canonical.get("metadata"), dict) else None
    if source_meta is not None and canonical_meta is not None:
        for key in list(source_meta):
            if key in canonical_meta:
                source_meta[key] = copy.deepcopy(canonical_meta[key])
        for key in ("language", "language_contract_version", "generation_mode", "direct_renderer"):
            source_meta[key] = canonical_meta[key]
        for key in ("source_language", "translation_status", "translation_cache_version"):
            source_meta.pop(key, None)
    return render_row(row, 0)


def main() -> None:
    canonical = {row["id"]: row for row in read_jsonl(ROOT / "data/gridinstruct_v1.2_sd_core.jsonl")}
    outputs = []
    for split in CORE_SPLITS:
        source_path = ROOT / f"data/v1.2_sd_core_{split}.jsonl"
        english_path = ROOT / f"data/v1.2_sd_core_{split}_en.jsonl"
        rows = [merge_row(row, canonical[row["id"]]) if row["id"] in canonical else render_row(row, 0) for row in read_jsonl(source_path)]
        write_jsonl(source_path, rows)
        write_jsonl(english_path, rows)
        outputs.append({"path": str(source_path.relative_to(ROOT)), "records": len(rows)})
    for relative_path in EXTRA_ENGLISH:
        path = ROOT / relative_path
        if not path.exists():
            continue
        rows = [merge_row(row, canonical[row["id"]]) if row["id"] in canonical else render_row(row, 0) for row in read_jsonl(path)]
        write_jsonl(path, rows)
        outputs.append({"path": relative_path, "records": len(rows)})
    report = {"status": "pass", "canonical_records": len(canonical), "outputs": outputs, "language_contract_version": "direct-en-v2"}
    report_path = ROOT / "reports/recomputed_core_split_projection_v1.2_sd_core.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
