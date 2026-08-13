#!/usr/bin/env python3
"""Build a task-balanced split with exact normalized-instruction groups.

The group key is the task type plus the normalized user-facing instruction.
Every exact instruction surface is therefore atomic across train, validation,
test, and OOD.  Allocation uses the same deterministic MILP and explicit
task-count bounds as the component-balanced split.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from create_balanced_near_neighbor_splits import solve_assignment
from create_group_aware_splits import is_ood
from audit_near_duplicates import normalize_template
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl


SPLITS = ("train", "validation", "test")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def group_key(row: dict[str, Any]) -> str:
    instruction = normalize_template(str(row.get("instruction") or ""))
    return f"{row.get('task_type')}::{instruction}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-prefix", default="data/v1.2_sd_core_instruction_surface_balanced")
    parser.add_argument("--report", default="reports/instruction_surface_balanced_split_v1.2_sd_core.json")
    parser.add_argument("--seed", type=int, default=2036)
    parser.add_argument("--materialize", action="store_true")
    args = parser.parse_args()

    source = ROOT / args.input
    rows = read_jsonl(source)
    if not rows:
        raise SystemExit("canonical input is empty")
    groups_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups_by_key[group_key(row)].append(row)
    groups = list(groups_by_key.values())
    ood_groups = [group for group in groups if any(is_ood(row) for row in group)]
    non_ood_groups = [group for group in groups if not any(is_ood(row) for row in group)]
    tasks = sorted({str(row.get("task_type")) for row in rows})
    assignment = None
    solve_details = None
    for tolerance in (0.01, 0.02, 0.05, 0.10):
        try:
            assignment, solve_details = solve_assignment(non_ood_groups, tasks, args.seed, tolerance)
            break
        except RuntimeError:
            continue
    if assignment is None or solve_details is None:
        raise SystemExit("could not solve instruction-surface allocation")

    splits: dict[str, list[dict[str, Any]]] = {name: [] for name in (*SPLITS, "ood_test")}
    for group, split_index in zip(non_ood_groups, assignment):
        splits[SPLITS[int(split_index)]].extend(group)
    for group in ood_groups:
        splits["ood_test"].extend(group)
    for split_rows in splits.values():
        split_rows.sort(key=lambda row: str(row["id"]))

    prefix = ROOT / args.output_prefix
    ensure_dirs(prefix.parent, (ROOT / args.report).parent)
    output_paths: dict[str, str] = {}
    for name in (*SPLITS, "ood_test"):
        path = prefix.with_name(prefix.name + f"_{name}_{'en' if args.materialize else 'ids'}.jsonl")
        payload = splits[name] if args.materialize else [{"id": row["id"]} for row in splits[name]]
        write_jsonl(path, payload)
        output_paths[name] = str(path.relative_to(ROOT))

    split_by_id = {str(row["id"]): split for split, values in splits.items() for row in values}
    cross_group_violations = []
    for key, group in groups_by_key.items():
        assigned = sorted({split_by_id[str(row["id"])] for row in group})
        if len(assigned) != 1:
            cross_group_violations.append({"group_key": key, "splits": assigned})
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not cross_group_violations else "fail",
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha256(source),
        "seed": args.seed,
        "group_key": "task_type + normalize_template(instruction)",
        "total_groups": len(groups),
        "non_ood_groups": len(non_ood_groups),
        "ood_groups": len(ood_groups),
        "max_group_size": max(len(group) for group in groups),
        "allocation": solve_details,
        "splits": {
            name: {
                "records": len(values),
                "sha256": sha256(ROOT / output_paths[name]),
                "task_counts": dict(Counter(str(row.get("task_type")) for row in values)),
            }
            for name, values in splits.items()
        },
        "instruction_surface_counts": {
            name: len({group_key(row) for row in values}) for name, values in splits.items()
        },
        "cross_group_violations": cross_group_violations,
        "output_paths": output_paths,
        "interpretation": "This split prevents exact normalized user-instruction reuse across partitions. It is a lexical surface control and does not establish semantic independence or physical-label validity.",
        "code_sha256": sha256(Path(__file__).resolve()),
    }
    write_json(ROOT / args.report, report)
    print(json.dumps({"status": report["status"], "groups": len(groups), "splits": {name: len(values) for name, values in splits.items()}, "allocation": solve_details}, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
