#!/usr/bin/env python3
"""Create an exact-verified character-surface component split.

MinHash-LSH is used only as a broad candidate index. Every candidate pair is
then checked with exact character-5-gram Jaccard before unioning components.
The resulting split is an ID-only stress split and never rewrites the canonical
record table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from datasketch import MinHash, MinHashLSH

from create_group_aware_splits import assign_connected_groups, is_ood
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl


CLASSIFICATION_FIELDS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def surface_text(row: dict[str, Any]) -> str:
    instruction = str(row.get("instruction") or "")
    context = json.dumps(row.get("input") or {}, ensure_ascii=False, sort_keys=True)
    return "\n".join([str(row.get("task_type") or ""), instruction, context]).lower()


@lru_cache(maxsize=16384)
def char_ngrams(text: str, n: int = 5) -> frozenset[str]:
    compact = " ".join(text.split())
    return frozenset(compact[i : i + n] for i in range(max(len(compact) - n + 1, 1)))


def exact_jaccard(left: str, right: str) -> float:
    a = char_ngrams(left)
    b = char_ngrams(right)
    return len(a & b) / max(len(a | b), 1)


def minhash(text: str, num_perm: int) -> MinHash:
    value = MinHash(num_perm=num_perm, seed=1)
    value.update_batch(gram.encode("utf-8") for gram in char_ngrams(text))
    return value


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.size = [1] * size

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.size[left_root] < self.size[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.size[left_root] += self.size[right_root]


def component_groups(rows: list[dict[str, Any]], uf: UnionFind) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[uf.find(index)].append(row)
    groups = []
    for root, group_rows in sorted(grouped.items()):
        task_counts = Counter(str(row.get("task_type") or "unknown") for row in group_rows)
        target_counts = Counter()
        for row in group_rows:
            task = str(row.get("task_type") or "unknown")
            field = CLASSIFICATION_FIELDS.get(task)
            if field:
                target_counts[f"{task}::{row.get(field)}"] += 1
            else:
                target_counts[f"{task}::__task__"] += 1
        groups.append(
            {
                "group_key": f"exact_surface::{root}",
                "rows": group_rows,
                "size": len(group_rows),
                "task_counts": task_counts,
                "target_counts": target_counts,
            }
        )
    return groups


def value_sets(rows: list[dict[str, Any]], kind: str) -> set[str]:
    if kind == "record_id":
        return {str(row.get("id")) for row in rows if row.get("id")}
    if kind == "surface":
        return {surface_text(row) for row in rows}
    raise ValueError(kind)


def label_support(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result = {}
    for task, field in CLASSIFICATION_FIELDS.items():
        result[task] = dict(sorted(Counter(str(row.get(field)) for row in rows if row.get("task_type") == task).items()))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-prefix", default="data/v1.2_sd_core_exact_surface")
    parser.add_argument("--report", default="reports/exact_surface_component_split_v1.2_sd_core.json")
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--num-perm", type=int, default=128)
    parser.add_argument("--candidate-threshold", type=float, default=0.80)
    parser.add_argument("--exact-threshold", type=float, default=0.85)
    args = parser.parse_args()

    source = ROOT / args.input
    rows = read_jsonl(source)
    if not rows:
        raise SystemExit("canonical input is empty")
    ids = [str(row.get("id") or "") for row in rows]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise SystemExit("canonical input must have unique non-empty IDs")
    surfaces = [surface_text(row) for row in rows]
    uf = UnionFind(len(rows))

    exact_representative: dict[str, int] = {}
    exact_duplicate_count = 0
    for index, surface in enumerate(surfaces):
        previous = exact_representative.get(surface)
        if previous is None:
            exact_representative[surface] = index
        else:
            uf.union(index, previous)
            exact_duplicate_count += 1

    lsh = MinHashLSH(threshold=args.candidate_threshold, num_perm=args.num_perm)
    id_to_index = {value: index for index, value in enumerate(ids)}
    signatures = []
    for index, surface in enumerate(surfaces, 1):
        signature = minhash(surface, args.num_perm)
        signatures.append(signature)
        lsh.insert(ids[index - 1], signature)
        if index % 10000 == 0:
            print(f"[exact-surface] indexed {index}/{len(rows)}", flush=True)

    candidate_pairs = 0
    accepted_edges = 0
    max_verified_jaccard = 0.0
    for index, (row_id, surface, signature) in enumerate(zip(ids, surfaces, signatures, strict=True), 1):
        neighbours = lsh.query(signature)
        for neighbour_id in neighbours:
            other = id_to_index[neighbour_id]
            if other <= index - 1:
                continue
            candidate_pairs += 1
            score = exact_jaccard(surface, surfaces[other])
            max_verified_jaccard = max(max_verified_jaccard, score)
            if score >= args.exact_threshold:
                uf.union(index - 1, other)
                accepted_edges += 1
        if index % 10000 == 0:
            print(f"[exact-surface] verified {index}/{len(rows)} candidates={candidate_pairs}", flush=True)

    groups = component_groups(rows, uf)
    non_ood = [group for group in groups if not any(is_ood(row) for row in group["rows"])]
    ood = [group for group in groups if any(is_ood(row) for row in group["rows"])]
    assigned = assign_connected_groups(non_ood, args.seed)
    split_rows = {
        name: [row for group in assigned[name] for row in group["rows"]]
        for name in ("train", "validation", "test")
    }
    split_rows["ood_test"] = [row for group in ood for row in group["rows"]]
    for name, values in split_rows.items():
        values.sort(key=lambda row: str(row.get("id")))

    output_paths = {}
    prefix = ROOT / args.output_prefix
    ensure_dirs(prefix.parent, (ROOT / args.report).parent)
    for name, values in split_rows.items():
        path = prefix.with_name(prefix.name + f"_{name}_ids.jsonl")
        write_jsonl(path, [{"id": row["id"]} for row in values])
        output_paths[name] = str(path.relative_to(ROOT))

    component_assignments = defaultdict(set)
    for name, values in split_rows.items():
        for row in values:
            component_assignments[uf.find(id_to_index[str(row["id"])] )].add(name)
    component_violations = [
        {"component": str(root), "splits": sorted(splits)}
        for root, splits in component_assignments.items()
        if len(splits) != 1
    ]
    hard_gates = {
        "all_splits_nonempty": all(split_rows[name] for name in split_rows),
        "component_disjoint": not component_violations,
        "exact_surface_train_test_overlap_zero": not (value_sets(split_rows["train"], "surface") & value_sets(split_rows["test"], "surface")),
        "classification_labels_in_test": all(
            set(label_support(split_rows["test"])[task])
            for task in CLASSIFICATION_FIELDS
        ),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "fail",
        "input": args.input,
        "input_sha256": sha256(source),
        "records": len(rows),
        "split_policy": f"MinHash-LSH candidate retrieval at threshold {args.candidate_threshold:.2f} followed by exact character-5-gram Jaccard verification at threshold {args.exact_threshold:.2f}; every verified component is assigned to one split",
        "parameters": {"num_perm": args.num_perm, "candidate_threshold": args.candidate_threshold, "exact_threshold": args.exact_threshold, "seed": args.seed},
        "candidate_pairs": candidate_pairs,
        "accepted_exact_edges": accepted_edges,
        "exact_surface_duplicate_records": exact_duplicate_count,
        "max_verified_candidate_jaccard": max_verified_jaccard,
        "component_count": len(groups),
        "split_counts": {name: len(values) for name, values in split_rows.items()},
        "label_support": {name: label_support(values) for name, values in split_rows.items()},
        "output_paths": output_paths,
        "component_violations": component_violations,
        "hard_gates": hard_gates,
        "interpretation": "This is a lexical near-duplicate stress split. It is not a semantic equivalence test and does not establish physical-label validity.",
    }
    write_json(ROOT / args.report, report)
    print(json.dumps({"status": report["status"], "components": len(groups), "candidate_pairs": candidate_pairs, "accepted_edges": accepted_edges, "split_counts": report["split_counts"]}, ensure_ascii=False), flush=True)
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
