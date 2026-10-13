#!/usr/bin/env python3
"""Keep exact record-content groups inside one official development split.

The official split already separates record identifiers.  This repair adds the
stronger, deterministic content-level condition needed by lexical baselines:
an exact normalized ``task/instruction/input/output`` signature cannot occur in
more than one of train, validation, and test.  OOD is left untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl


SPLITS = ("train", "validation", "test")
SPLIT_RANK = {name: index for index, name in enumerate(SPLITS)}


def text_for(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(row.get("task_type") or ""),
            str(row.get("instruction") or ""),
            json.dumps(row.get("input"), ensure_ascii=False, sort_keys=True),
            json.dumps(row.get("output"), ensure_ascii=False, sort_keys=True),
        ]
    )


def signature(row: dict[str, Any]) -> str:
    normalized = " ".join(text_for(row).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def cross_overlap(splits: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    signatures = {
        name: {signature(row) for row in rows}
        for name, rows in splits.items()
    }
    return {
        f"{left}_{right}": len(signatures[left] & signatures[right])
        for index, left in enumerate(SPLITS)
        for right in SPLITS[index + 1 :]
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--validation", default="data/v1.2_sd_core_validation_en.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--source-train", default="data/v1.2_sd_core_train.jsonl")
    parser.add_argument("--source-validation", default="data/v1.2_sd_core_validation.jsonl")
    parser.add_argument("--source-test", default="data/v1.2_sd_core_test.jsonl")
    parser.add_argument("--output-json", default="reports/official_exact_content_overlap_repair_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/official_exact_content_overlap_repair_v1.2_sd_core.md")
    args = parser.parse_args()

    english_paths = {
        "train": ROOT / args.train,
        "validation": ROOT / args.validation,
        "test": ROOT / args.test,
    }
    splits = {name: read_jsonl(path) for name, path in english_paths.items()}
    before_overlap = cross_overlap(splits)
    groups: dict[str, list[tuple[str, int, dict[str, Any]]]] = defaultdict(list)
    for split_name, rows in splits.items():
        for index, row in enumerate(rows):
            groups[signature(row)].append((split_name, index, row))

    assignments: dict[str, str] = {}
    moved_ids: list[dict[str, str]] = []
    groups_reassigned = 0
    for group_signature, members in groups.items():
        target = min((split for split, _, _ in members), key=SPLIT_RANK.get)
        if len({split for split, _, _ in members}) > 1:
            groups_reassigned += 1
        for split_name, _, row in members:
            assignments[str(row["id"])] = target
            if split_name != target:
                moved_ids.append({"id": str(row["id"]), "from": split_name, "to": target})

    repaired: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLITS}
    for split_name, rows in splits.items():
        for row in rows:
            repaired[assignments[str(row["id"])]].append(row)
    after_overlap = cross_overlap(repaired)
    if any(after_overlap.values()):
        raise RuntimeError(f"exact-content overlap remains after repair: {after_overlap}")

    input_hashes = {name: sha256(path) for name, path in english_paths.items()}
    output_hashes: dict[str, str] = {}
    for split_name, rows in repaired.items():
        english_path = english_paths[split_name]
        write_jsonl_atomic(english_path, rows)
        output_hashes[split_name] = sha256(english_path)

    # The source-language split paths are byte-identical release projections;
    # keep them synchronized with the repaired English projections.
    source_paths = {
        "train": ROOT / args.source_train,
        "validation": ROOT / args.source_validation,
        "test": ROOT / args.source_test,
    }
    source_hashes: dict[str, str] = {}
    for split_name, rows in repaired.items():
        write_jsonl_atomic(source_paths[split_name], rows)
        source_hashes[split_name] = sha256(source_paths[split_name])
        if source_hashes[split_name] != output_hashes[split_name]:
            raise RuntimeError(f"source/English projection mismatch for {split_name}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "contract": "official_exact_content_single_split_v1",
        "input_paths": {name: str(path.relative_to(ROOT)) for name, path in english_paths.items()},
        "source_paths": {name: str(path.relative_to(ROOT)) for name, path in source_paths.items()},
        "input_sha256": input_hashes,
        "output_sha256": output_hashes,
        "source_output_sha256": source_hashes,
        "before_cross_split_exact_signature_overlap": before_overlap,
        "after_cross_split_exact_signature_overlap": after_overlap,
        "groups_reassigned": groups_reassigned,
        "moved_record_count": len(moved_ids),
        "moved_records": sorted(moved_ids, key=lambda row: (row["to"], row["from"], row["id"])),
        "split_counts": {name: len(rows) for name, rows in repaired.items()},
        "ood_unchanged": True,
        "interpretation": "All canonical records are retained. Exact normalized task/instruction/input/output signatures are assigned to the earliest official development split; OOD is unchanged and no semantic similarity claim is made.",
    }
    output = ROOT / args.output_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# Official exact-content overlap repair",
        "",
        f"- Status: **{report['status']}**",
        f"- Reassigned groups: **{groups_reassigned}**",
        f"- Moved records: **{len(moved_ids)}**",
        "",
        "| Pair | Before | After |",
        "| --- | ---: | ---: |",
    ]
    for pair in before_overlap:
        md.append(f"| {pair} | {before_overlap[pair]} | {after_overlap[pair]} |")
    md.extend(
        [
            "",
            "The repair retains every record and leaves the OOD projection unchanged. The resulting exact-content condition is stronger than record-ID separation and is used only to prevent lexical-baseline leakage.",
        ]
    )
    (ROOT / args.output_md).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "moved_records": len(moved_ids), "after": after_overlap}, ensure_ascii=False))


if __name__ == "__main__":
    main()
