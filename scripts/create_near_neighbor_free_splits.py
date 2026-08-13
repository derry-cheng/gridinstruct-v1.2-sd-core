#!/usr/bin/env python3
"""Build leakage-resistant splits using connected MinHash near-neighbour components.

The canonical English release is read once.  Rows connected at the selected
character-shingle MinHash threshold are assigned as one unit, so a near
duplicate cannot appear in two evaluation partitions.  The script never
rewrites the canonical dataset; it emits a separate benchmark split family and
an auditable manifest with hashes, component counts, threshold sensitivity and
label support.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datasketch import MinHash, MinHashLSH

from audit_near_duplicates import normalize_template, text_for
from create_group_aware_splits import is_ood
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl


CLASSIFICATION_FIELDS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def minhash_for(text: str, num_perm: int) -> MinHash:
    value = MinHash(num_perm=num_perm, seed=1)
    compact = normalize_template(text)
    grams = {compact[index : index + 5] for index in range(max(len(compact) - 4, 1))}
    value.update_batch(gram.encode("utf-8") for gram in grams)
    return value


def build_signatures(rows: list[dict[str, Any]], num_perm: int) -> tuple[list[str], list[MinHash]]:
    texts = [text_for(row) for row in rows]
    signatures = [normalize_template(text) for text in texts]
    minhashes = []
    for index, text in enumerate(texts, 1):
        minhashes.append(minhash_for(text, num_perm))
        if index % 10000 == 0:
            print(f"[progress] signatures {index}/{len(texts)}", flush=True)
    return signatures, minhashes


def neighbour_summary(
    ids: list[str],
    signatures: list[MinHash],
    threshold: float,
    collect_edges: bool,
    query_indices: list[int] | None = None,
) -> tuple[dict[str, Any], list[tuple[int, int]]]:
    lsh = MinHashLSH(threshold=threshold, num_perm=signatures[0].num_perm)
    for index, value in enumerate(signatures):
        lsh.insert(ids[index], value)
        if (index + 1) % 10000 == 0:
            print(f"[progress] LSH index threshold={threshold:.2f} {index + 1}/{len(signatures)}", flush=True)
    id_to_index = {value: index for index, value in enumerate(ids)}
    matches = 0
    edge_count = 0
    edges: list[tuple[int, int]] = []
    indices = query_indices if query_indices is not None else list(range(len(signatures)))
    for completed, index in enumerate(indices, 1):
        value = signatures[index]
        neighbours = [id_to_index[item] for item in lsh.query(value) if id_to_index[item] != index]
        if neighbours:
            matches += 1
            edge_count += len(neighbours)
            if collect_edges:
                edges.extend((index, neighbour) for neighbour in neighbours)
        if completed % 10000 == 0:
            print(f"[progress] LSH threshold={threshold:.2f} {completed}/{len(indices)}", flush=True)
    return (
        {
            "threshold": threshold,
            "num_perm": signatures[0].num_perm,
            "records": len(signatures),
            "query_records": len(indices),
            "records_with_neighbour": matches,
            "neighbour_rate": matches / max(len(indices), 1),
            "directed_edge_count": edge_count,
        },
        edges,
    )


def components(rows: list[dict[str, Any]], signatures: list[str], minhashes: list[MinHash], threshold: float) -> tuple[UnionFind, dict[str, Any]]:
    uf = UnionFind(len(rows))
    exact_representative: dict[str, int] = {}
    for index, signature in enumerate(signatures):
        previous = exact_representative.get(signature)
        if previous is not None:
            uf.union(index, previous)
        else:
            exact_representative[signature] = index

    summary, edges = neighbour_summary([str(row["id"]) for row in rows], minhashes, threshold, True)
    for left, right in edges:
        uf.union(left, right)
    summary["exact_template_signature_count"] = len(exact_representative)
    summary["exact_template_duplicate_records_after_first"] = len(rows) - len(exact_representative)
    return uf, summary


def component_rows(rows: list[dict[str, Any]], uf: UnionFind) -> dict[int, list[dict[str, Any]]]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[uf.find(index)].append(row)
    return groups


def assign_components(groups: dict[int, list[dict[str, Any]]], seed: int) -> dict[str, list[dict[str, Any]]]:
    non_ood = []
    ood = []
    for group in groups.values():
        (ood if any(is_ood(row) for row in group) else non_ood).append(group)
    non_ood.sort(key=lambda group: hashlib.sha256(
        (f"{seed}|" + "|".join(sorted(str(row["id"]) for row in group))).encode("utf-8")
    ).hexdigest())
    total = sum(len(group) for group in non_ood)
    targets = {"train": round(total * 0.80), "validation": round(total * 0.10)}
    targets["test"] = total - targets["train"] - targets["validation"]
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": [], "ood_test": []}
    counts = {name: 0 for name in ("train", "validation", "test")}
    for group in non_ood:
        available = [name for name in ("train", "validation", "test") if counts[name] < targets[name]]
        if not available:
            available = ["test"]
        best = max(available, key=lambda name: (targets[name] - counts[name], name == "train"))
        splits[best].extend(group)
        counts[best] += len(group)
    for group in ood:
        splits["ood_test"].extend(group)
    for name in splits:
        splits[name].sort(key=lambda row: str(row["id"]))
    return splits


def label_support(splits: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, dict[str, int]]]:
    result: dict[str, dict[str, dict[str, int]]] = {}
    for task, field in CLASSIFICATION_FIELDS.items():
        result[task] = {}
        for split_name in ("train", "validation", "test", "ood_test"):
            counts = Counter(str(row.get(field)) for row in splits[split_name] if row.get("task_type") == task)
            result[task][split_name] = dict(sorted(counts.items()))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-prefix", default="data/v1.2_sd_core_near_neighbor_free")
    parser.add_argument("--report", default="reports/near_neighbor_free_split_v1.2_sd_core.json")
    parser.add_argument("--seed", type=int, default=2036)
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--num-perm", type=int, default=64)
    parser.add_argument(
        "--sensitivity-query-records",
        type=int,
        default=0,
        help="Number of deterministic sensitivity queries; 0 audits all records (default).",
    )
    parser.add_argument("--materialize", action="store_true", help="write full JSONL copies; default writes ID manifests only")
    args = parser.parse_args()

    source = ROOT / args.input
    rows = read_jsonl(source)
    if not rows:
        raise SystemExit("canonical input is empty")
    ids = [str(row.get("id") or "") for row in rows]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise SystemExit("canonical input must have unique non-empty IDs")

    signatures, minhashes = build_signatures(rows, args.num_perm)
    sensitivity = {}
    if args.sensitivity_query_records <= 0 or args.sensitivity_query_records >= len(rows):
        sample_indices = list(range(len(rows)))
        sampling_note = "full 95,479-row query against the full 95,479-row index"
    else:
        sample_indices = sorted(
            sorted(range(len(rows)), key=lambda index: hashlib.sha256(f"{args.seed}|{ids[index]}".encode("utf-8")).hexdigest())[: args.sensitivity_query_records]
        )
        sampling_note = f"deterministic {len(sample_indices):,}-row query sample against the full {len(rows):,}-row index"
    for threshold in (0.80, 0.85, 0.90):
        sensitivity[str(threshold)] = neighbour_summary(ids, minhashes, threshold, False, sample_indices)[0]
        sensitivity[str(threshold)]["sampling"] = sampling_note
    uf, component_summary = components(rows, signatures, minhashes, args.threshold)
    groups = component_rows(rows, uf)
    splits = assign_components(groups, args.seed)

    prefix = ROOT / args.output_prefix
    ensure_dirs(prefix.parent, (ROOT / args.report).parent)
    output_paths = {}
    for name in ("train", "validation", "test", "ood_test"):
        if args.materialize:
            path = prefix.with_name(prefix.name + f"_{name}_en.jsonl")
            payload = splits[name]
        else:
            path = prefix.with_name(prefix.name + f"_{name}_ids.jsonl")
            payload = [{"id": row["id"]} for row in splits[name]]
        write_jsonl(path, payload)
        output_paths[name] = str(path.relative_to(ROOT))

    component_assignment = {}
    row_to_split = {}
    for name, split_rows in splits.items():
        for row in split_rows:
            row_to_split[str(row["id"])] = name
    cross_component_violations = []
    for root, group in groups.items():
        assignments = sorted({row_to_split[str(row["id"])] for row in group})
        component_assignment[str(root)] = {"size": len(group), "split": assignments}
        if len(assignments) != 1:
            cross_component_violations.append({"root": root, "splits": assignments})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not cross_component_violations else "fail",
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha256(source),
        "records": len(rows),
        "seed": args.seed,
        "split_policy": "all character-5-gram MinHash connected components at threshold 0.85 are assigned to exactly one split; components touching the preregistered OOD rule are kept in ood_test",
        "threshold": args.threshold,
        "num_perm": args.num_perm,
        "threshold_sensitivity": sensitivity,
        "component_summary": {
            **component_summary,
            "total_components": len(groups),
            "max_component_size": max(len(group) for group in groups.values()),
            "ood_components": sum(1 for group in groups.values() if any(is_ood(row) for row in group)),
        },
        "splits": {
            name: {
                "records": len(split_rows),
                "sha256": sha256(ROOT / output_paths[name]),
                "task_counts": dict(Counter(str(row.get("task_type")) for row in split_rows)),
            }
            for name, split_rows in splits.items()
        },
        "label_support": label_support(splits),
        "cross_component_violations": cross_component_violations,
        "component_assignment_sha256": hashlib.sha256(json.dumps(component_assignment, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "output_paths": output_paths,
    }
    write_json(ROOT / args.report, report)
    print(json.dumps({"status": report["status"], "records": len(rows), "components": len(groups), "splits": {name: len(value) for name, value in splits.items()}, "threshold_sensitivity": sensitivity}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
