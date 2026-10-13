#!/usr/bin/env python3
"""Audit the current direct-English instruction-surface split.

The split itself is produced by an exact MILP over atomic normalized
instruction groups.  This report only checks the resulting manifests and
records residual character similarity as a diagnostic; it does not interpret
lexical separation as semantic or physical independence.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT


SPLITS = ("train", "validation", "test", "ood_test")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_surface(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\b(?:ieee|pegase|rte)\s*\d+\b", "{network}", text, flags=re.I)
    text = re.sub(r"\b[a-z]+\d+(?:_[a-z0-9]+)+\b", "{scenario}", text, flags=re.I)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "{number}", text)
    text = re.sub(r"[0-9a-f]{12,}", "{hash}", text, flags=re.I)
    return " ".join(text.split())


def grams(text: str) -> set[str]:
    text = normalize_surface(text)
    return {text[i : i + 5] for i in range(max(1, len(text) - 4))}


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = __import__("argparse").ArgumentParser()
    parser.add_argument("--prefix", default="data/v1.2_sd_core_instruction_surface_balanced")
    parser.add_argument("--split-report", default="reports/instruction_surface_balanced_split_v1.2_sd_core.json")
    parser.add_argument("--output", default="reports/instruction_surface_balanced_surface_audit_v1.2_sd_core.json")
    args = parser.parse_args()

    split_paths = {
        split: ROOT / f"{args.prefix}_{split}_en.jsonl" for split in SPLITS
    }
    rows = {split: read_rows(path) for split, path in split_paths.items()}
    ids = {split: {str(row["id"]) for row in values} for split, values in rows.items()}
    duplicate_ids = {
        split: len(values) - len(ids[split]) for split, values in rows.items()
    }
    group_sets = {
        split: {
            (str(row.get("task_type")), normalize_surface(str(row.get("instruction") or "")))
            for row in values
        }
        for split, values in rows.items()
    }
    cross_groups = {
        f"{left}_{right}": len(group_sets[left] & group_sets[right])
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))
    }
    sample = {split: sorted(values, key=lambda row: str(row["id"]))[:200] for split, values in rows.items()}
    jaccard_max: dict[str, dict[str, Any]] = {}
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        by_task: dict[str, list[tuple[str, set[str]]]] = {}
        for row in sample[left]:
            by_task.setdefault(str(row.get("task_type")), []).append(
                (str(row["id"]), grams(str(row.get("instruction") or "")))
            )
        best = (0.0, None, None)
        for row in sample[right]:
            current = grams(str(row.get("instruction") or ""))
            for left_id, other in by_task.get(str(row.get("task_type")), []):
                value = len(current & other) / max(1, len(current | other))
                if value > best[0]:
                    best = (value, left_id, str(row["id"]))
        jaccard_max[f"{left}_{right}"] = {
            "left_sample_records": len(sample[left]),
            "right_sample_records": len(sample[right]),
            "max_jaccard": best[0],
            "left_id": best[1],
            "right_id": best[2],
        }

    split_report = ROOT / args.split_report
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(v == 0 for v in duplicate_ids.values()) and all(v == 0 for v in cross_groups.values()) else "fail",
        "split_report": str(split_report.relative_to(ROOT)),
        "split_report_sha256": sha256(split_report),
        "source_paths": {split: str(path.relative_to(ROOT)) for split, path in split_paths.items()},
        "source_sha256": {split: sha256(path) for split, path in split_paths.items()},
        "record_counts": {split: len(values) for split, values in rows.items()},
        "duplicate_id_counts": duplicate_ids,
        "normalized_surface_group_counts": {split: len(values) for split, values in group_sets.items()},
        "cross_split_normalized_surface_group_counts": cross_groups,
        "sampled_character_5gram_jaccard": jaccard_max,
        "interpretation": "Exact normalized-surface groups are isolated by the MILP split. Sampled character-5-gram similarity is reported as a lexical diagnostic and does not establish semantic, physical, or legal independence.",
    }
    out = ROOT / args.output
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "record_counts": report["record_counts"], "cross_groups": cross_groups}, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
