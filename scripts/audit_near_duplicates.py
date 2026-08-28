#!/usr/bin/env python3
"""Audit exact, template-normalized, MinHash, and semantic near duplicates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

from gridinstruct_utils import ROOT, read_jsonl, write_json


DEFAULT_SEMANTIC_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_SEMANTIC_MODEL_REVISION = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
DEFAULT_SEMANTIC_MODEL_PATH = (
    f"models/sentence-transformers/all-MiniLM-L6-v2/{DEFAULT_SEMANTIC_MODEL_REVISION}"
)


def text_for(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(row.get("task_type") or ""),
            str(row.get("instruction") or ""),
            json.dumps(row.get("input"), ensure_ascii=False, sort_keys=True),
            json.dumps(row.get("output"), ensure_ascii=False, sort_keys=True),
        ]
    )


def normalize_template(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\b(?:ieee|pegase|rte)\s*\d+\b", "{network}", text, flags=re.I)
    text = re.sub(r"\b[a-z]+\d+(?:_[a-z0-9]+)+\b", "{scenario}", text, flags=re.I)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "{number}", text)
    text = re.sub(r"[0-9a-f]{12,}", "{hash}", text, flags=re.I)
    return " ".join(text.split())


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def duplicate_summary(rows: list[dict[str, Any]], normalizer) -> dict[str, Any]:
    counts = Counter(digest(normalizer(text_for(row))) for row in rows)
    duplicate_records = sum(count - 1 for count in counts.values() if count > 1)
    return {
        "records": len(rows),
        "unique_signatures": len(counts),
        "duplicate_records_after_first": duplicate_records,
        "duplicate_rate": duplicate_records / max(len(rows), 1),
        "effective_unique_rate": len(counts) / max(len(rows), 1),
    }


def cross_signature_overlap(left: list[dict[str, Any]], right: list[dict[str, Any]], normalizer) -> dict[str, Any]:
    left_signatures = {digest(normalizer(text_for(row))) for row in left}
    matching = [row["id"] for row in right if digest(normalizer(text_for(row))) in left_signatures]
    return {
        "left_records": len(left),
        "right_records": len(right),
        "right_records_matching_left": len(matching),
        "right_match_rate": len(matching) / max(len(right), 1),
        "examples": matching[:50],
    }


def char_ngrams(text: str, n: int = 5) -> set[str]:
    compact = normalize_template(text)
    return {compact[idx : idx + n] for idx in range(max(len(compact) - n + 1, 1))}


@lru_cache(maxsize=200_000)
def minhash(text: str, num_perm: int) -> Any:
    from datasketch import MinHash

    value = MinHash(num_perm=num_perm)
    for gram in char_ngrams(text):
        value.update(gram.encode("utf-8"))
    return value


def minhash_cross(left: list[dict[str, Any]], right: list[dict[str, Any]], threshold: float, num_perm: int) -> dict[str, Any]:
    from datasketch import MinHashLSH

    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    for row in left:
        lsh.insert(str(row["id"]), minhash(text_for(row), num_perm))
    matches = []
    for row in right:
        neighbors = lsh.query(minhash(text_for(row), num_perm))
        if neighbors:
            matches.append({"id": row["id"], "neighbor_ids": sorted(neighbors)[:5]})
    return {
        "threshold": threshold,
        "num_perm": num_perm,
        "left_records": len(left),
        "right_records": len(right),
        "right_records_with_neighbor": len(matches),
        "right_match_rate": len(matches) / max(len(right), 1),
        "examples": matches[:50],
    }


def sample_rows(rows: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    if len(rows) <= count:
        return rows
    rng = random.Random(seed)
    return [rows[idx] for idx in sorted(rng.sample(range(len(rows)), count))]


def directory_sha256(path: Path) -> str:
    files = []
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest = hashlib.sha256()
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        files.append(
            {
                "path": str(item.relative_to(path)),
                "sha256": digest.hexdigest(),
                "size": item.stat().st_size,
            }
        )
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def semantic_backend_preflight(
    local_path: str,
    model_id: str,
    revision: str,
    expected_sha256: str | None,
    fallback: str,
) -> dict[str, Any]:
    path = Path(local_path)
    if not path.is_absolute():
        path = ROOT / path
    weight_files = [
        candidate
        for pattern in ("model.safetensors", "pytorch_model.bin")
        for candidate in path.rglob(pattern)
    ] if path.is_dir() else []
    actual_sha256 = directory_sha256(path) if path.is_dir() else None
    fallback_contract = {
        "backend": "sklearn_tfidf_nearest_neighbor",
        "analyzer": "char_wb",
        "ngram_range": [3, 5],
        "min_df": 1,
        "sublinear_tf": True,
        "norm": "l2",
        "dtype": "float32",
        "metric": "cosine",
        "n_jobs": 1,
    }
    fallback_contract_sha256 = hashlib.sha256(
        json.dumps(fallback_contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    errors = []
    if not path.is_dir():
        errors.append("local_model_directory_missing")
    if path.is_dir() and not weight_files:
        errors.append("local_model_weights_missing")
    if path.is_dir() and path.name != revision:
        revision_file = path / ".revision"
        recorded_revision = revision_file.read_text(encoding="utf-8").strip() if revision_file.is_file() else None
        if recorded_revision != revision:
            errors.append("local_model_revision_not_bound")
    if expected_sha256 and actual_sha256 != expected_sha256:
        errors.append("local_model_hash_mismatch")
    if importlib.util.find_spec("sentence_transformers") is None:
        errors.append("sentence_transformers_package_missing")
    use_transformer = not errors
    if errors and fallback == "fail":
        raise RuntimeError("semantic model preflight failed: " + ";".join(errors))
    return {
        "requested_model_id": model_id,
        "revision": revision,
        "local_path": str(path),
        "expected_sha256": expected_sha256,
        "actual_tree_sha256": actual_sha256,
        "weight_files": [str(item.relative_to(path)) for item in weight_files],
        "preflight_errors": errors,
        "backend": "sentence_transformer_local" if use_transformer else "deterministic_tfidf_char_ngrams",
        "fallback": fallback,
        "fallback_contract": fallback_contract,
        "fallback_contract_sha256": fallback_contract_sha256,
        "network_access_allowed": False,
    }


def deterministic_semantic_cross(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    left_text = [normalize_template(text_for(row)) for row in left]
    right_text = [normalize_template(text_for(row)) for row in right]
    vectorizer = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(3, 5),
        min_df=1,
        sublinear_tf=True,
        norm="l2",
        dtype=np.float32,
    )
    matrix = vectorizer.fit_transform(left_text + right_text)
    left_matrix = matrix[: len(left)]
    right_matrix = matrix[len(left) :]
    neighbors = NearestNeighbors(n_neighbors=1, metric="cosine", algorithm="brute", n_jobs=1)
    neighbors.fit(left_matrix)
    distances, indices = neighbors.kneighbors(right_matrix, return_distance=True)
    maxima = (1.0 - distances[:, 0]).tolist()
    argmax = indices[:, 0].tolist()
    matches = [
        {"id": right[idx]["id"], "neighbor_id": left[argmax[idx]]["id"], "cosine_similarity": maxima[idx]}
        for idx in range(len(right))
        if maxima[idx] >= threshold
    ]
    return {
        "model": "deterministic_tfidf_char_ngrams_3_5",
        "device": "cpu",
        "threshold": threshold,
        "left_sample_records": len(left),
        "right_sample_records": len(right),
        "right_records_with_neighbor": len(matches),
        "right_match_rate": len(matches) / max(len(right), 1),
        "examples": sorted(matches, key=lambda row: -row["cosine_similarity"])[:50],
    }


def semantic_cross(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    backend: dict[str, Any],
    threshold: float,
    batch_size: int,
) -> dict[str, Any]:
    if backend["backend"] != "sentence_transformer_local":
        return deterministic_semantic_cross(left, right, threshold)
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(backend["local_path"], device=device, local_files_only=True)
    left_embeddings = model.encode([normalize_template(text_for(row)) for row in left], batch_size=batch_size, convert_to_tensor=True, normalize_embeddings=True, show_progress_bar=True)
    right_embeddings = model.encode([normalize_template(text_for(row)) for row in right], batch_size=batch_size, convert_to_tensor=True, normalize_embeddings=True, show_progress_bar=True)
    maxima = []
    argmax = []
    for start in range(0, len(right), 512):
        similarities = right_embeddings[start : start + 512] @ left_embeddings.T
        values, indices = similarities.max(dim=1)
        maxima.extend(values.detach().cpu().tolist())
        argmax.extend(indices.detach().cpu().tolist())
    matches = [
        {"id": right[idx]["id"], "neighbor_id": left[argmax[idx]]["id"], "cosine_similarity": maxima[idx]}
        for idx in range(len(right))
        if maxima[idx] >= threshold
    ]
    return {
        "model": backend["requested_model_id"],
        "revision": backend["revision"],
        "model_tree_sha256": backend["actual_tree_sha256"],
        "local_path": backend["local_path"],
        "device": device,
        "threshold": threshold,
        "left_sample_records": len(left),
        "right_sample_records": len(right),
        "right_records_with_neighbor": len(matches),
        "right_match_rate": len(matches) / max(len(right), 1),
        "examples": sorted(matches, key=lambda row: -row["cosine_similarity"])[:50],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--ood", default="data/v1.2_sd_core_ood_test_en.jsonl")
    parser.add_argument("--strict-train", default="data/v1.2_sd_core_strict_train.jsonl")
    parser.add_argument("--strict-test", default="data/v1.2_sd_core_strict_test.jsonl")
    parser.add_argument("--minhash-threshold", type=float, default=0.85)
    parser.add_argument("--num-perm", type=int, default=64)
    parser.add_argument("--minhash-left-sample", type=int, default=2500)
    parser.add_argument("--minhash-right-sample", type=int, default=12000)
    parser.add_argument("--semantic-sample", type=int, default=5000)
    parser.add_argument("--semantic-threshold", type=float, default=0.95)
    # Keep the near-duplicate diagnostic on the same prespecified two-percent
    # contract used by the release row validator.  Cross-split exact matches
    # remain held to the stricter 0.1% leakage bound.
    parser.add_argument("--max-full-exact-duplicate-rate", type=float, default=0.02)
    parser.add_argument("--max-cross-exact-match-rate", type=float, default=0.001)
    parser.add_argument("--semantic-model", default=DEFAULT_SEMANTIC_MODEL_ID)
    parser.add_argument("--semantic-model-revision", default=DEFAULT_SEMANTIC_MODEL_REVISION)
    parser.add_argument(
        "--semantic-model-local-path",
        default=os.environ.get("SD_SEMANTIC_MODEL_PATH", DEFAULT_SEMANTIC_MODEL_PATH),
    )
    parser.add_argument("--semantic-model-sha256", default=os.environ.get("SD_SEMANTIC_MODEL_SHA256"))
    parser.add_argument(
        "--semantic-fallback",
        choices=("deterministic_tfidf", "fail"),
        default="deterministic_tfidf",
    )
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--output", default="reports/near_duplicate_audit_v1.2_sd_core.json")
    args = parser.parse_args()

    splits = {
        "full": read_jsonl(ROOT / args.full),
        "train": read_jsonl(ROOT / args.train),
        "test": read_jsonl(ROOT / args.test),
        "ood": read_jsonl(ROOT / args.ood),
        "strict_train": read_jsonl(ROOT / args.strict_train),
        "strict_test": read_jsonl(ROOT / args.strict_test),
    }
    cross_pairs = {
        "train_test": ("train", "test"),
        "train_ood": ("train", "ood"),
        "strict_train_strict_test": ("strict_train", "strict_test"),
    }
    semantic_backend = semantic_backend_preflight(
        args.semantic_model_local_path,
        args.semantic_model,
        args.semantic_model_revision,
        args.semantic_model_sha256,
        args.semantic_fallback,
    )
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "semantic_backend_preflight": semantic_backend,
        "exact_full": duplicate_summary(splits["full"], lambda text: " ".join(text.split())),
        "template_normalized_full": duplicate_summary(splits["full"], normalize_template),
        "cross_split": {},
    }
    for name, (left_name, right_name) in cross_pairs.items():
        left = splits[left_name]
        right = splits[right_name]
        minhash_left = sample_rows(left, args.minhash_left_sample, args.seed)
        minhash_right = sample_rows(right, args.minhash_right_sample, args.seed + len(name))
        semantic_left = sample_rows(left, args.semantic_sample, args.seed)
        semantic_right = sample_rows(right, args.semantic_sample, args.seed + len(name))
        report["cross_split"][name] = {
            "exact": cross_signature_overlap(left, right, lambda text: " ".join(text.split())),
            "template_normalized": cross_signature_overlap(left, right, normalize_template),
            "minhash_char5": minhash_cross(
                minhash_left, minhash_right, args.minhash_threshold, args.num_perm
            ),
            "semantic_sample": semantic_cross(
                semantic_left,
                semantic_right,
                semantic_backend,
                args.semantic_threshold,
                args.batch_size,
            ),
        }
    unique_id_gates = {
        name: len(rows) > 0
        and len([row.get("id") for row in rows]) == len({row.get("id") for row in rows})
        and all(row.get("id") for row in rows)
        for name, rows in splits.items()
    }
    hard_gates = {
        "all_splits_nonempty_unique_ids": all(unique_id_gates.values()),
        "official_train_test_id_disjoint": not ({row["id"] for row in splits["train"]} & {row["id"] for row in splits["test"]}),
        "official_train_ood_id_disjoint": not ({row["id"] for row in splits["train"]} & {row["id"] for row in splits["ood"]}),
        "full_exact_duplicate_rate_within_limit": report["exact_full"]["duplicate_rate"] <= args.max_full_exact_duplicate_rate,
        "cross_exact_match_rates_within_limit": all(
            item["exact"]["right_match_rate"] <= args.max_cross_exact_match_rate
            for item in report["cross_split"].values()
        ),
        "sampled_diagnostics_nonempty": all(
            item["minhash_char5"]["right_records"] > 0
            and item["semantic_sample"]["left_sample_records"] > 0
            and item["semantic_sample"]["right_sample_records"] > 0
            for item in report["cross_split"].values()
        ),
    }
    report["hard_gates"] = hard_gates
    report["threshold_policy"] = {
        "max_full_exact_duplicate_rate": args.max_full_exact_duplicate_rate,
        "max_cross_exact_match_rate": args.max_cross_exact_match_rate,
        "minhash_left_sample": args.minhash_left_sample,
        "minhash_right_sample": args.minhash_right_sample,
    }
    report["status"] = "pass" if all(hard_gates.values()) else "fail"
    report["interpretation"] = "Exact, template-normalized, MinHash character-5-gram, and embedding cosine diagnostics are reported separately; sampled diagnostics retain their sample sizes."
    write_json(ROOT / args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
