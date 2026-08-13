#!/usr/bin/env python3
"""Audit exact surface isolation for the balanced component split.

The MinHash construction is a candidate detector.  This audit independently
checks exact normalized-surface collisions and computes exact character
5-gram Jaccard maxima from an inverted index.  OOD is evaluated on a fixed,
hash-ordered sample to keep the audit bounded; the sample and its hash are
recorded in the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.sparse import csr_matrix

from audit_near_duplicates import normalize_template, text_for
from gridinstruct_utils import ROOT, read_jsonl, write_json


SPLITS = ("train", "validation", "test", "ood_test")
TASKS = (
    "auxiliary_decision",
    "dispatcher_intent_tool_call",
    "intelligent_data_query",
    "operation_ticket_check",
    "regulation_compliance_check",
    "regulation_qa",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def grams(value: str) -> set[str]:
    compact = normalize_template(value)
    return {compact[i : i + 5] for i in range(max(len(compact) - 4, 1))}


def read_ids(path: Path) -> list[str]:
    values: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                values.append(str(json.loads(line)["id"]))
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate IDs in {path}")
    return values


def exact_max_jaccard(
    reference: list[tuple[str, set[str]]],
    queries: list[tuple[str, set[str]]],
    threshold: float,
) -> dict[str, Any]:
    # Build binary sparse matrices and evaluate every query--reference pair
    # in this deterministic sample.  The matrix product returns exact gram
    # intersections; no approximate candidate index is used here.
    vocabulary = {gram: index for index, gram in enumerate(sorted({gram for _, value in reference for gram in value} | {gram for _, value in queries for gram in value}))}

    def matrix(rows: list[tuple[str, set[str]]]) -> csr_matrix:
        row_indices: list[int] = []
        col_indices: list[int] = []
        for row_index, (_, values) in enumerate(rows):
            for gram in values:
                row_indices.append(row_index)
                col_indices.append(vocabulary[gram])
        data = np.ones(len(row_indices), dtype=np.int8)
        return csr_matrix((data, (row_indices, col_indices)), shape=(len(rows), len(vocabulary)), dtype=np.int32)

    reference_matrix = matrix(reference)
    query_matrix = matrix(queries)
    intersections = (query_matrix @ reference_matrix.T).toarray().astype(np.float64)
    query_sizes = np.asarray([len(value) for _, value in queries], dtype=np.float64)[:, None]
    reference_sizes = np.asarray([len(value) for _, value in reference], dtype=np.float64)[None, :]
    unions = query_sizes + reference_sizes - intersections
    scores = np.divide(intersections, unions, out=np.zeros_like(intersections), where=unions > 0)
    max_indices = scores.argmax(axis=1) if len(reference) else np.zeros(len(queries), dtype=int)
    row_maxima = scores.max(axis=1) if len(reference) else np.zeros(len(queries), dtype=float)
    max_value = float(row_maxima.max()) if len(row_maxima) else 0.0
    max_row = int(row_maxima.argmax()) if len(row_maxima) else None
    max_pair = (
        (queries[max_row][0], reference[int(max_indices[max_row])][0])
        if max_row is not None and len(reference)
        else None
    )
    rows_above = int(np.sum(row_maxima >= threshold))
    pairs_examined = len(reference) * len(queries)
    return {
        "reference_records": len(reference),
        "query_records": len(queries),
        "candidate_pairs_examined": pairs_examined,
        "candidate_retrieval": "exact sparse binary character-5-gram matrix product over every pair in the deterministic sample",
        "max_exact_character_5gram_jaccard": max_value,
        "max_pair": list(max_pair) if max_pair else None,
        "query_rows_at_or_above_threshold": rows_above,
        "threshold": threshold,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--split-prefix", default="data/v1.2_sd_core_near_neighbor_balanced")
    parser.add_argument("--output", default="reports/near_neighbor_balanced_surface_audit_v1.2_sd_core.json")
    parser.add_argument("--ood-sample", type=int, default=10000)
    parser.add_argument("--pair-sample", type=int, default=2000, help="deterministic query cap for exact Jaccard candidate verification")
    parser.add_argument("--reference-sample", type=int, default=3000, help="deterministic reference cap for exact Jaccard candidate verification")
    parser.add_argument("--threshold", type=float, default=0.85)
    args = parser.parse_args()

    source = ROOT / args.input
    rows = read_jsonl(source)
    by_id = {str(row["id"]): row for row in rows}
    split_ids: dict[str, list[str]] = {}
    for split in SPLITS:
        path = ROOT / f"{args.split_prefix}_{split}_ids.jsonl"
        split_ids[split] = read_ids(path)
        missing = sorted(set(split_ids[split]) - set(by_id))
        if missing:
            raise ValueError(f"{len(missing)} IDs in {path} are absent from the canonical input")
    all_ids = [value for values in split_ids.values() for value in values]
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != set(by_id):
        raise ValueError("balanced split manifests do not partition the canonical IDs exactly")

    surfaces = {
        record_id: normalize_template(text_for(by_id[record_id])) for record_id in by_id
    }
    def select_ids(values: list[str], cap: int) -> list[str]:
        if len(values) <= cap:
            return list(values)
        return sorted(
            values,
            key=lambda record_id: hashlib.sha256(f"balanced-pair-sample|{record_id}".encode("utf-8")).hexdigest(),
        )[:cap]

    pair_query_ids = {
        "validation": select_ids(split_ids["validation"], args.pair_sample),
        "test": select_ids(split_ids["test"], args.pair_sample),
    }
    pair_reference_ids = {
        "train": select_ids(split_ids["train"], args.reference_sample),
        "validation": select_ids(pair_query_ids["validation"], args.reference_sample),
    }
    canonical_ood = select_ids(split_ids["ood_test"], args.ood_sample)
    scored_ids = set(pair_reference_ids["train"]) | set(pair_reference_ids["validation"]) | set(pair_query_ids["validation"]) | set(pair_query_ids["test"]) | set(canonical_ood)
    surface_sets = {record_id: grams(text_for(by_id[record_id])) for record_id in scored_ids}
    exact_overlap: dict[str, int] = {}
    pair_reports: dict[str, dict[str, Any]] = {}
    ordered_pairs = (("train", "validation"), ("train", "test"), ("validation", "test"))
    for left, right in ordered_pairs:
        left_index = defaultdict(list)
        for record_id in split_ids[left]:
            left_index[surfaces[record_id]].append(record_id)
        collisions = sum(1 for record_id in split_ids[right] if surfaces[record_id] in left_index)
        exact_overlap[f"{left}__{right}"] = collisions
        reference_ids = pair_reference_ids[left]
        reference = [(record_id, surface_sets[record_id]) for record_id in reference_ids]
        queries = [(record_id, surface_sets[record_id]) for record_id in pair_query_ids[right]]
        pair_reports[f"{left}__{right}"] = exact_max_jaccard(reference, queries, args.threshold)
        reference = [(record_id, surface_sets[record_id]) for record_id in reference_ids]
    queries = [(record_id, surface_sets[record_id]) for record_id in canonical_ood]
    pair_reports["train__ood_sample"] = exact_max_jaccard(reference, queries, args.threshold)

    task_counts = {
        split: dict(sorted(Counter(str(by_id[record_id].get("task_type")) for record_id in ids).items()))
        for split, ids in split_ids.items()
    }
    label_counts: dict[str, dict[str, dict[str, int]]] = {}
    for task, field in {
        "operation_ticket_check": "compliance_label",
        "regulation_compliance_check": "compliance_label",
        "dispatcher_intent_tool_call": "intent",
    }.items():
        label_counts[task] = {
            split: dict(sorted(Counter(str(by_id[record_id].get(field)) for record_id in ids if by_id[record_id].get("task_type") == task).items()))
            for split, ids in split_ids.items()
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not any(exact_overlap.values()) and all(item["query_rows_at_or_above_threshold"] == 0 for item in pair_reports.values()) else "fail",
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha256(source),
        "split_prefix": args.split_prefix,
        "threshold": args.threshold,
        "records": len(rows),
        "task_counts": task_counts,
        "classification_label_counts": label_counts,
        "exact_normalized_surface_collisions": exact_overlap,
        "exact_character_5gram_jaccard_over_candidates": pair_reports,
        "ood_query_sample": {
            "sample_records": len(canonical_ood),
            "selection": "lowest SHA-256 of balanced-pair-sample|record_id",
            "selection_sha256": hashlib.sha256("\n".join(canonical_ood).encode("utf-8")).hexdigest(),
        },
        "pair_query_sample": {
            "train__validation": len(pair_query_ids["validation"]),
            "train__test": len(pair_query_ids["test"]),
            "train__ood": len(canonical_ood),
            "selection": "lowest SHA-256 of balanced-pair-sample|record_id",
        },
        "pair_reference_sample": {
            "records_per_pair": args.reference_sample,
            "selection": "lowest SHA-256 of balanced-pair-sample|record_id",
        },
        "interpretation": "Exact normalized-surface checks cover all cross-split rows. Exact character-5-gram Jaccard is evaluated on deterministic query samples for every pair retrieved by an explicitly reported MinHash-LSH candidate index; this lexical diagnostic does not establish semantic independence or physical-label validity.",
    }
    write_json(ROOT / args.output, report)
    print(json.dumps({"status": report["status"], "exact_overlap": exact_overlap, "pair_reports": pair_reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
