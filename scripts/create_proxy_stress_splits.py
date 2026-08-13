#!/usr/bin/env python3
"""Create fixed proxy-group stress train/validation/test splits."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from create_classification_challenge_splits import CLASSIFICATION_TASKS, challenge_group_key, label_for
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, stable_shuffle, write_json, write_jsonl
from run_proxy_stress_baselines import split_proxy_groups


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_validation_groups(rows: list[dict[str, Any]], seed: int, validation_ratio: float) -> tuple[list[dict], list[dict], dict[str, Any]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[challenge_group_key(row)].append(row)
    groups = stable_shuffle([{"key": key, "rows": value, "size": len(value)} for key, value in grouped.items()], seed)
    target = max(1, int(len(rows) * validation_ratio))
    validation_groups = []
    validation_rows: list[dict] = []
    labels = {label_for(row) for row in rows}
    for group in groups:
        if len(validation_rows) >= target and labels <= {label_for(row) for row in validation_rows}:
            break
        validation_groups.append(group)
        validation_rows.extend(group["rows"])
    validation_keys = {group["key"] for group in validation_groups}
    train_rows = [row for key, grow in grouped.items() if key not in validation_keys for row in grow]
    summary = {
        "train_records": len(train_rows),
        "validation_records": len(validation_rows),
        "train_groups": len({challenge_group_key(row) for row in train_rows}),
        "validation_groups": len(validation_keys),
        "group_overlap": len({challenge_group_key(row) for row in train_rows} & validation_keys),
        "train_label_counts": dict(Counter(label_for(row) for row in train_rows)),
        "validation_label_counts": dict(Counter(label_for(row) for row in validation_rows)),
    }
    return train_rows, validation_rows, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--tasks", nargs="+", default=list(CLASSIFICATION_TASKS))
    parser.add_argument("--output-dir", default="data/proxy_stress")
    parser.add_argument("--report-json", default="reports/proxy_stress_split_manifest_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/proxy_stress_split_manifest_v1.2_sd_core.md")
    parser.add_argument("--seed", type=int, default=2041)
    parser.add_argument("--test-ratio", type=float, default=0.20)
    parser.add_argument("--validation-ratio", type=float, default=0.12)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.dataset)
    out_dir = ROOT / args.output_dir
    ensure_dirs(out_dir, ROOT / "reports")
    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "dataset": args.dataset,
        "dataset_sha256": sha256(ROOT / args.dataset),
        "output_dir": args.output_dir,
        "seed": args.seed,
        "test_ratio": args.test_ratio,
        "validation_ratio": args.validation_ratio,
        "builder": "scripts/create_proxy_stress_splits.py",
        "builder_sha256": sha256(ROOT / "scripts/create_proxy_stress_splits.py"),
        "tasks": {},
    }
    for offset, task in enumerate(args.tasks):
        task_rows = [row for row in rows if row.get("task_type") == task]
        if not task_rows:
            continue
        pool_rows, test_rows, stress_summary = split_proxy_groups(task_rows, args.seed + offset * 101, args.test_ratio)
        train_rows, validation_rows, validation_summary = split_validation_groups(pool_rows, args.seed + offset * 211, args.validation_ratio)
        task_dir = out_dir / task
        ensure_dirs(task_dir)
        train_path = task_dir / "train.jsonl"
        validation_path = task_dir / "validation.jsonl"
        test_path = task_dir / "test.jsonl"
        write_jsonl(train_path, train_rows)
        write_jsonl(validation_path, validation_rows)
        write_jsonl(test_path, test_rows)
        train_keys = {challenge_group_key(row) for row in train_rows}
        validation_keys = {challenge_group_key(row) for row in validation_rows}
        test_keys = {challenge_group_key(row) for row in test_rows}
        manifest["tasks"][task] = {
            "train": str(train_path.relative_to(ROOT)),
            "validation": str(validation_path.relative_to(ROOT)),
            "test": str(test_path.relative_to(ROOT)),
            "sha256": {
                "train": sha256(train_path),
                "validation": sha256(validation_path),
                "test": sha256(test_path),
            },
            "records": {"train": len(train_rows), "validation": len(validation_rows), "test": len(test_rows)},
            "group_counts": {"train": len(train_keys), "validation": len(validation_keys), "test": len(test_keys)},
            "group_overlap": {
                "train_validation": len(train_keys & validation_keys),
                "train_test": len(train_keys & test_keys),
                "validation_test": len(validation_keys & test_keys),
            },
            "label_counts": {
                "train": dict(Counter(label_for(row) for row in train_rows)),
                "validation": dict(Counter(label_for(row) for row in validation_rows)),
                "test": dict(Counter(label_for(row) for row in test_rows)),
            },
            "stress_summary": stress_summary,
            "validation_summary": validation_summary,
        }
    all_overlaps = [
        int(value)
        for item in manifest["tasks"].values()
        for value in item["group_overlap"].values()
    ]
    manifest["status"] = (
        "pass"
        if len(manifest["tasks"]) == len(args.tasks)
        and all(value == 0 for value in all_overlaps)
        else "fail"
    )
    write_json(ROOT / args.report_json, manifest)
    lines = [
        "# Proxy Stress Split Manifest",
        "",
        f"Generated: {manifest['generated_at']}",
        f"Dataset: `{manifest['dataset']}`",
        "",
        "| Task | Train | Validation | Test | Train/Test group overlap |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for task, item in manifest["tasks"].items():
        r = item["records"]
        lines.append(f"| `{task}` | {r['train']} | {r['validation']} | {r['test']} | {item['group_overlap']['train_test']} |")
    Path(ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2)[:5000])
    if manifest["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
