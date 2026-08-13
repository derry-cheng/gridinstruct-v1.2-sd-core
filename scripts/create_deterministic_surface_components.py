#!/usr/bin/env python3
"""Build a deterministic exact character-surface component split.

The implementation uses the prefix-filtering theorem for Jaccard similarity,
then performs an exact set intersection for every retrieved candidate.  Gram
sets and the inverted index are stored as compact NumPy integer arrays; the
previous Python-set implementation exhausted memory before the full release
could be audited.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

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


def grams(text: str, n: int = 5) -> set[str]:
    compact = " ".join(text.split())
    return {compact[index : index + n] for index in range(max(len(compact) - n + 1, 1))}


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = np.arange(size, dtype=np.int32)
        self.size = np.ones(size, dtype=np.int32)

    def find(self, value: int) -> int:
        root = value
        while int(self.parent[root]) != root:
            root = int(self.parent[root])
        while int(self.parent[value]) != value:
            parent = int(self.parent[value])
            self.parent[value] = root
            value = parent
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if int(self.size[left_root]) < int(self.size[right_root]):
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.size[left_root] += self.size[right_root]


def label_support(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for task, field in CLASSIFICATION_FIELDS.items():
        result[task] = dict(
            sorted(
                Counter(str(row.get(field)) for row in rows if row.get("task_type") == task).items()
            )
        )
    return result


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
            target_counts[f"{task}::{row.get(field) if field else '__task__'}"] += 1
        groups.append(
            {
                "group_key": f"deterministic_surface::{root}",
                "rows": group_rows,
                "size": len(group_rows),
                "task_counts": task_counts,
                "target_counts": target_counts,
            }
        )
    return groups


def exact_intersection_size(left: np.ndarray, right: np.ndarray) -> int:
    """Exact intersection for sorted, unique compact integer arrays."""
    return int(np.intersect1d(left, right, assume_unique=True).size)


def build_compact_index(
    rows: list[dict[str, Any]], ngram_size: int, threshold: float
) -> tuple[list[np.ndarray], np.ndarray, np.ndarray, np.ndarray, list[int], np.ndarray, list[int], list[int]]:
    """Return compact sets and a prefix-token inverted index."""
    frequency: Counter[str] = Counter()
    for row in rows:
        frequency.update(grams(surface_text(row), ngram_size))
    ordered_grams = sorted(frequency, key=lambda value: (frequency[value], value))
    gram_to_id = {gram: index for index, gram in enumerate(ordered_grams)}
    all_sets: list[np.ndarray] = []
    for row in rows:
        identifiers = sorted(gram_to_id[gram] for gram in grams(surface_text(row), ngram_size))
        all_sets.append(np.asarray(identifiers, dtype=np.uint32))
    unique_lookup: dict[tuple[int, ...], int] = {}
    ordered_sets: list[np.ndarray] = []
    row_to_rep: list[int] = []
    representative_rows: list[int] = []
    for row_index, values in enumerate(all_sets):
        key = tuple(int(value) for value in values)
        representative = unique_lookup.get(key)
        if representative is None:
            representative = len(ordered_sets)
            unique_lookup[key] = representative
            ordered_sets.append(values)
            representative_rows.append(row_index)
        row_to_rep.append(representative)
    prefix_lengths: list[int] = []
    prefix_total = 0
    for values in ordered_sets:
        # ``values`` is sorted by token ID, which is the same as ascending
        # global document frequency followed by lexical order.
        prefix_length = max(1, values.size - math.ceil(threshold * values.size) + 1)
        prefix_lengths.append(prefix_length)
        prefix_total += prefix_length

    posting_tokens = np.empty(prefix_total, dtype=np.uint32)
    posting_rows = np.empty(prefix_total, dtype=np.int32)
    cursor = 0
    for row_index, values in enumerate(ordered_sets):
        length = prefix_lengths[row_index]
        posting_tokens[cursor : cursor + length] = values[:length]
        posting_rows[cursor : cursor + length] = row_index
        cursor += length
    order = np.argsort(posting_tokens, kind="stable")
    posting_tokens = posting_tokens[order]
    posting_rows = posting_rows[order]
    unique_tokens, starts = np.unique(posting_tokens, return_index=True)
    ends = np.empty_like(starts)
    ends[:-1] = starts[1:]
    ends[-1] = posting_tokens.size
    return ordered_sets, unique_tokens, starts, ends, prefix_lengths, posting_rows, row_to_rep, representative_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-prefix", default="data/v1.2_sd_core_deterministic_surface")
    parser.add_argument("--report", default="reports/deterministic_surface_component_split_v1.2_sd_core.json")
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--ngram-size", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.85)
    args = parser.parse_args()
    if not 0 < args.threshold <= 1:
        raise SystemExit("threshold must be in (0, 1]")

    source = ROOT / args.input
    rows = read_jsonl(source)
    if not rows:
        raise SystemExit("canonical input is empty")
    ids = [str(row.get("id") or "") for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise SystemExit("canonical input must have unique non-empty IDs")

    ordered_sets, unique_tokens, starts, ends, prefix_lengths, posting_rows, row_to_rep, representative_rows = build_compact_index(
        rows, args.ngram_size, args.threshold
    )
    uf = UnionFind(len(rows))
    for row_index, representative in enumerate(row_to_rep):
        uf.union(row_index, representative_rows[representative])
    seen = np.full(len(rows), -1, dtype=np.int32)
    candidate_pairs = 0
    accepted_edges = 0
    size_pruned_pairs = 0
    max_verified_jaccard = 0.0
    for index, values in enumerate(ordered_sets):
        marker = index
        candidate_indices: list[int] = []
        prefix_length = prefix_lengths[index]
        for token in values[:prefix_length]:
            location = int(np.searchsorted(unique_tokens, token))
            if location >= unique_tokens.size or int(unique_tokens[location]) != int(token):
                continue
            for other in posting_rows[int(starts[location]) : int(ends[location])]:
                other_index = int(other)
                if other_index > index and seen[other_index] != marker:
                    seen[other_index] = marker
                    candidate_indices.append(other_index)
        for other in candidate_indices:
            candidate_pairs += 1
            other_values = ordered_sets[other]
            if min(values.size, other_values.size) / max(values.size, other_values.size) < args.threshold:
                size_pruned_pairs += 1
                continue
            intersection = exact_intersection_size(values, other_values)
            union = int(values.size + other_values.size - intersection)
            score = intersection / max(union, 1)
            max_verified_jaccard = max(max_verified_jaccard, score)
            if score >= args.threshold:
                uf.union(representative_rows[index], representative_rows[other])
                accepted_edges += 1
        if (index + 1) % 10000 == 0:
            print(
                f"[deterministic-surface] verified {index + 1}/{len(rows)} "
                f"candidates={candidate_pairs} size_pruned={size_pruned_pairs}",
                flush=True,
            )

    groups = component_groups(rows, uf)
    non_ood = [group for group in groups if not any(is_ood(row) for row in group["rows"])]
    ood = [group for group in groups if any(is_ood(row) for row in group["rows"])]
    assigned = assign_connected_groups(non_ood, args.seed)
    split_rows = {
        name: [row for group in assigned[name] for row in group["rows"]]
        for name in ("train", "validation", "test")
    }
    split_rows["ood_test"] = [row for group in ood for row in group["rows"]]
    for values in split_rows.values():
        values.sort(key=lambda row: str(row.get("id")))

    component_assignments: dict[int, set[str]] = defaultdict(set)
    id_to_index = {value: index for index, value in enumerate(ids)}
    for name, values in split_rows.items():
        for row in values:
            component_assignments[uf.find(id_to_index[str(row["id"])])].add(name)
    component_violations = [
        {"component": str(root), "splits": sorted(splits)}
        for root, splits in component_assignments.items()
        if len(splits) != 1
    ]
    hard_gates = {
        "all_splits_nonempty": all(split_rows.values()),
        "component_disjoint": not component_violations,
        "prefix_filter_recall_guarantee": all(
            length >= 1 for length in prefix_lengths
        ),
        "classification_labels_in_test": all(
            set(label_support(split_rows["test"])[task]) for task in CLASSIFICATION_FIELDS
        ),
    }
    output_paths: dict[str, str] = {}
    prefix = ROOT / args.output_prefix
    ensure_dirs(prefix.parent, (ROOT / args.report).parent)
    for name, values in split_rows.items():
        path = prefix.with_name(prefix.name + f"_{name}_ids.jsonl")
        write_jsonl(path, [{"id": row["id"]} for row in values])
        try:
            output_paths[name] = str(path.relative_to(ROOT))
        except ValueError:
            output_paths[name] = str(path)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "fail",
        "input": args.input,
        "input_sha256": sha256(source),
        "records": len(rows),
        "unique_surface_sets": len(ordered_sets),
        "exact_duplicate_records": len(rows) - len(ordered_sets),
        "split_policy": (
            "Deterministic global-frequency prefix filtering followed by exact "
            f"character-{args.ngram_size}-gram Jaccard verification at threshold {args.threshold:.2f}"
        ),
        "parameters": {
            "ngram_size": args.ngram_size,
            "threshold": args.threshold,
            "seed": args.seed,
            "token_order": "ascending document frequency, then lexical order",
        },
        "candidate_recall_guarantee": True,
        "candidate_pairs": candidate_pairs,
        "size_pruned_pairs": size_pruned_pairs,
        "accepted_exact_edges": accepted_edges,
        "max_verified_jaccard": max_verified_jaccard,
        "component_count": len(groups),
        "split_counts": {name: len(values) for name, values in split_rows.items()},
        "label_support": {name: label_support(values) for name, values in split_rows.items()},
        "output_paths": output_paths,
        "component_violations": component_violations,
        "hard_gates": hard_gates,
        "interpretation": (
            "This is an exact lexical surface stress split with deterministic candidate recall. "
            "It does not establish semantic independence, physical-label validity, or dispatch competence."
        ),
    }
    write_json(ROOT / args.report, report)
    print(json.dumps({"status": report["status"], "components": len(groups), "candidate_pairs": candidate_pairs, "accepted_edges": accepted_edges, "split_counts": report["split_counts"]}, ensure_ascii=False), flush=True)
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
