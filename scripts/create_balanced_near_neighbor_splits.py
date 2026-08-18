#!/usr/bin/env python3
"""Create a deterministic, label-supported split over lexical-neighbour components.

The existing near-neighbour audit isolates MinHash components but allocates them
with a size-only rule.  This script keeps the same component graph and solves a
small mixed-integer allocation problem over the non-OOD components.  Task-level
record bounds are explicit, so a clean evaluation split cannot silently become
single-label or near-empty for a task family.  OOD components remain fixed.

Only ID manifests are written by default; the canonical JSONL table is never
copied or rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import LinearConstraint, milp
from scipy.sparse import lil_matrix

from create_near_neighbor_free_splits import (
    CLASSIFICATION_FIELDS,
    build_signatures,
    components,
    component_rows,
)
from create_group_aware_splits import is_ood
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl


SPLITS = ("train", "validation", "test")
FRACTIONS = {"train": 0.60, "validation": 0.20, "test": 0.20}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_task_counts(group: list[dict[str, Any]], tasks: list[str]) -> list[int]:
    counts = Counter(str(row.get("task_type")) for row in group)
    return [int(counts.get(task, 0)) for task in tasks]


def stable_cost(group: list[dict[str, Any]], seed: int) -> float:
    key = "|".join(sorted(str(row["id"]) for row in group))
    value = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).hexdigest()
    # Tiny deterministic tie-breaker; task-support constraints dominate.
    return int(value[:12], 16) / float(16**12) * 1e-9


def solve_assignment(
    groups: list[list[dict[str, Any]]],
    tasks: list[str],
    seed: int,
    tolerance_ratio: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    n_groups = len(groups)
    n_tasks = len(tasks)
    n_variables = n_groups * len(SPLITS)
    costs = np.zeros(n_variables, dtype=float)
    for group_index, group in enumerate(groups):
        tie = stable_cost(group, seed)
        for split_index in range(len(SPLITS)):
            costs[group_index * len(SPLITS) + split_index] = tie + split_index * 1e-12

    rows = n_groups + len(SPLITS) * n_tasks
    matrix = lil_matrix((rows, n_variables), dtype=float)
    lower = np.full(rows, -np.inf, dtype=float)
    upper = np.full(rows, np.inf, dtype=float)

    # Every component is assigned to exactly one non-OOD split.
    for group_index in range(n_groups):
        row_index = group_index
        for split_index in range(len(SPLITS)):
            matrix[row_index, group_index * len(SPLITS) + split_index] = 1.0
        lower[row_index] = 1.0
        upper[row_index] = 1.0

    feature_matrix = np.asarray([row_task_counts(group, tasks) for group in groups], dtype=float)
    total_by_task = feature_matrix.sum(axis=0)
    target_by_split = {
        split: np.rint(total_by_task * FRACTIONS[split]).astype(int)
        for split in SPLITS
    }

    for split_index, split in enumerate(SPLITS):
        for task_index, task in enumerate(tasks):
            row_index = n_groups + split_index * n_tasks + task_index
            for group_index in range(n_groups):
                value = feature_matrix[group_index, task_index]
                if value:
                    matrix[row_index, group_index * len(SPLITS) + split_index] = value
            target = int(target_by_split[split][task_index])
            tolerance = max(3, int(np.ceil(target * tolerance_ratio)))
            lower[row_index] = max(0, target - tolerance)
            upper[row_index] = target + tolerance

    result = milp(
        c=costs,
        integrality=np.ones(n_variables, dtype=np.int8),
        bounds=(np.zeros(n_variables), np.ones(n_variables)),
        constraints=LinearConstraint(matrix.tocsr(), lower, upper),
        options={"presolve": True, "time_limit": 300.0, "mip_rel_gap": 0.0},
    )
    if not result.success or result.x is None:
        raise RuntimeError(f"MILP failed at tolerance={tolerance_ratio}: {result.message}")

    assignment = np.asarray(result.x).reshape(n_groups, len(SPLITS)).argmax(axis=1)
    actual = {
        split: feature_matrix[assignment == split_index].sum(axis=0).astype(int).tolist()
        for split_index, split in enumerate(SPLITS)
    }
    return assignment, {
        "status": result.message,
        "objective": float(result.fun),
        "tolerance_ratio": tolerance_ratio,
        "target_by_split": {
            split: target_by_split[split].astype(int).tolist() for split in SPLITS
        },
        "actual_by_split": actual,
        "tasks": tasks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-prefix", default="data/v1.2_sd_core_near_neighbor_balanced")
    parser.add_argument("--report", default="reports/near_neighbor_balanced_split_v1.2_sd_core.json")
    parser.add_argument("--seed", type=int, default=2036)
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--num-perm", type=int, default=64)
    parser.add_argument("--materialize", action="store_true")
    args = parser.parse_args()

    source = ROOT / args.input
    rows = read_jsonl(source)
    if not rows:
        raise SystemExit("canonical input is empty")
    print(f"[progress] loaded records {len(rows):,}", flush=True)
    signatures, minhashes = build_signatures(rows, args.num_perm)
    uf, component_summary = components(rows, signatures, minhashes, args.threshold)
    groups = component_rows(rows, uf)
    ood_groups = [group for group in groups.values() if any(is_ood(row) for row in group)]
    non_ood_groups = [group for group in groups.values() if not any(is_ood(row) for row in group)]
    tasks = sorted({str(row.get("task_type")) for row in rows})
    print(
        f"[progress] components={len(groups):,}, non_ood={len(non_ood_groups):,}, ood={len(ood_groups):,}",
        flush=True,
    )

    assignment = None
    solve_details = None
    # Preserve an exact MILP allocation over lexical components.  Direct
    # English rendering can make the tight task-count bands infeasible, so
    # relax the count bands deterministically before declaring the split
    # unsatisfiable.
    for tolerance in (0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00):
        try:
            assignment, solve_details = solve_assignment(non_ood_groups, tasks, args.seed, tolerance)
            break
        except RuntimeError as exc:
            print(f"[progress] retry allocation: {exc}", flush=True)
    if assignment is None or solve_details is None:
        raise SystemExit("could not solve balanced component allocation")

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
        if args.materialize:
            path = prefix.with_name(prefix.name + f"_{name}_en.jsonl")
            payload = splits[name]
        else:
            path = prefix.with_name(prefix.name + f"_{name}_ids.jsonl")
            payload = [{"id": row["id"]} for row in splits[name]]
        write_jsonl(path, payload)
        output_paths[name] = str(path.relative_to(ROOT))

    row_to_split = {
        str(row["id"]): split
        for split, split_rows in splits.items()
        for row in split_rows
    }
    cross_component_violations = []
    for root, group in groups.items():
        assignments = sorted({row_to_split[str(row["id"])] for row in group})
        if len(assignments) != 1:
            cross_component_violations.append({"root": root, "splits": assignments})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not cross_component_violations else "fail",
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha256(source),
        "records": len(rows),
        "seed": args.seed,
        "split_policy": "all detected character-5-gram MinHash components remain atomic; preregistered OOD components stay in ood_test; non-OOD components are allocated by a deterministic MILP with explicit task-support bounds",
        "threshold": args.threshold,
        "num_perm": args.num_perm,
        "component_summary": {
            **component_summary,
            "total_components": len(groups),
            "max_component_size": max(len(group) for group in groups.values()),
            "ood_components": len(ood_groups),
            "non_ood_components": len(non_ood_groups),
        },
        "allocation": solve_details,
        "splits": {
            name: {
                "records": len(split_rows),
                "sha256": sha256(ROOT / output_paths[name]),
                "task_counts": dict(Counter(str(row.get("task_type")) for row in split_rows)),
            }
            for name, split_rows in splits.items()
        },
        "classification_support": {
            task: {
                split: dict(sorted(Counter(str(row.get(field)) for row in split_rows if row.get("task_type") == task).items()))
                for split, split_rows in splits.items()
            }
            for task, field in CLASSIFICATION_FIELDS.items()
        },
        "cross_component_violations": cross_component_violations,
        "output_paths": output_paths,
        "code_sha256": sha256(Path(__file__).resolve()),
    }
    write_json(ROOT / args.report, report)
    print(json.dumps({
        "status": report["status"],
        "records": len(rows),
        "components": len(groups),
        "splits": {name: len(value) for name, value in splits.items()},
        "allocation": solve_details,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
