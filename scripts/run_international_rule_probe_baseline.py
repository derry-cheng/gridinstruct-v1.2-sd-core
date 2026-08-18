"""Run a jurisdiction-held-out retrieval diagnostic on the international rule probe.

This is deliberately a small, CPU-only diagnostic.  It fits character TF--IDF
on one jurisdiction, retrieves the nearest training record for the held-out
jurisdiction, and copies that record's answer.  The score is reported as a
lexical-transfer measurement; it is not a semantic compliance score and does
not replace independent expert review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer

from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json, write_jsonl


def normalize_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value).strip().split())


def make_text(row: dict[str, Any]) -> str:
    metadata = row.get("metadata", {})
    return "\n".join(
        [
            f"jurisdiction: {metadata.get('jurisdiction', row.get('input', {}).get('jurisdiction', ''))}",
            f"standard: {metadata.get('standard_id', row.get('input', {}).get('standard_id', ''))}",
            f"clause: {metadata.get('clause_id', row.get('input', {}).get('clause_id', ''))}",
            f"instruction: {row['instruction']}",
            f"input: {json.dumps(row['input'], ensure_ascii=False, sort_keys=True)}",
        ]
    )


def token_f1(gold: Any, prediction: Any) -> float:
    gold_tokens = normalize_text(gold).split()
    prediction_tokens = normalize_text(prediction).split()
    if not gold_tokens and not prediction_tokens:
        return 1.0
    if not gold_tokens or not prediction_tokens:
        return 0.0
    overlap = sum((Counter(gold_tokens) & Counter(prediction_tokens)).values())
    precision = overlap / len(prediction_tokens)
    recall = overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def exact_match(gold: Any, prediction: Any) -> bool:
    return normalize_text(gold) == normalize_text(prediction)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nearest_rows(
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    max_features: int,
) -> tuple[dict[str, float | int], list[dict[str, Any]]]:
    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(2, 4),
        min_df=1,
        max_features=max_features,
    )
    train_matrix = vectorizer.fit_transform(make_text(row) for row in train_rows)
    test_matrix = vectorizer.transform(make_text(row) for row in test_rows)
    similarities = test_matrix @ train_matrix.T
    top_indices = similarities.argmax(axis=1).A1.tolist()
    top_scores = similarities.max(axis=1).toarray().ravel().tolist()

    exact_values: list[float] = []
    f1_values: list[float] = []
    same_rule_values: list[float] = []
    same_standard_values: list[float] = []
    prediction_rows: list[dict[str, Any]] = []
    for row, index, similarity in zip(test_rows, top_indices, top_scores):
        neighbor = train_rows[index]
        gold = row["output"]
        prediction = neighbor["output"]
        gold_rule = row["input"]["rule_id"]
        neighbor_rule = neighbor["input"]["rule_id"]
        gold_standard = row["metadata"]["standard_id"]
        neighbor_standard = neighbor["metadata"]["standard_id"]
        exact_values.append(float(exact_match(gold, prediction)))
        f1_values.append(token_f1(gold, prediction))
        same_rule_values.append(float(gold_rule == neighbor_rule))
        same_standard_values.append(float(gold_standard == neighbor_standard))
        prediction_rows.append(
            {
                "id": row["id"],
                "fold": None,
                "test_jurisdiction": row["metadata"]["jurisdiction"],
                "train_neighbor_id": neighbor["id"],
                "gold_rule_id": gold_rule,
                "neighbor_rule_id": neighbor_rule,
                "gold_standard_id": gold_standard,
                "neighbor_standard_id": neighbor_standard,
                "gold": gold,
                "prediction": prediction,
                "exact_match": bool(exact_values[-1]),
                "token_f1": f1_values[-1],
                "nearest_similarity": float(similarity),
            }
        )

    denominator = max(len(test_rows), 1)
    metrics: dict[str, float | int] = {
        "n": len(test_rows),
        "exact_match": sum(exact_values) / denominator,
        "token_f1": sum(f1_values) / denominator,
        "avg_nearest_similarity": sum(float(value) for value in top_scores) / denominator,
        "same_rule_id_rate": sum(same_rule_values) / denominator,
        "same_standard_id_rate": sum(same_standard_values) / denominator,
    }
    return metrics, prediction_rows


def validate_fold(fold: dict[str, Any], rows_by_id: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train_rows = [rows_by_id[row_id] for row_id in fold["train_ids"]]
    test_rows = [rows_by_id[row_id] for row_id in fold["test_ids"]]
    if not train_rows or not test_rows:
        raise ValueError(f"empty fold: {fold['name']}")
    if set(fold["train_ids"]) & set(fold["test_ids"]):
        raise ValueError(f"train/test overlap in {fold['name']}")
    return train_rows, test_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/international_rule_probe_v1.jsonl")
    parser.add_argument("--splits", default="metadata/international_rule_probe_splits_v1.json")
    parser.add_argument("--output-prefix", default="benchmark/international_rule_probe_v1/nearest_neighbor")
    parser.add_argument("--max-features", type=int, default=60000)
    args = parser.parse_args()

    data_path = ROOT / args.data
    split_path = ROOT / args.splits
    rows = read_jsonl(data_path)
    split_manifest = read_json(split_path)
    rows_by_id = {str(row["id"]): row for row in rows}
    if len(rows_by_id) != len(rows):
        raise ValueError("international probe IDs must be unique")
    if split_manifest["record_count"] != len(rows):
        raise ValueError("split manifest record count does not match data")
    if set(rows_by_id) != {
        row_id
        for fold in split_manifest["folds"]
        for row_id in fold["train_ids"] + fold["test_ids"]
    }:
        raise ValueError("split manifest does not cover exactly the data IDs")

    all_predictions: list[dict[str, Any]] = []
    fold_reports: list[dict[str, Any]] = []
    for fold in split_manifest["folds"]:
        train_rows, test_rows = validate_fold(fold, rows_by_id)
        metrics, predictions = nearest_rows(train_rows, test_rows, args.max_features)
        for prediction in predictions:
            prediction["fold"] = fold["name"]
        fold_reports.append(
            {
                "name": fold["name"],
                "train_jurisdiction": fold["train_jurisdiction"],
                "test_jurisdiction": fold["test_jurisdiction"],
                "train_records": len(train_rows),
                "test_records": len(test_rows),
                "metrics": metrics,
                "prediction_count": len(predictions),
            }
        )
        all_predictions.extend(predictions)

    metric_names = ["exact_match", "token_f1", "avg_nearest_similarity", "same_rule_id_rate", "same_standard_id_rate"]
    macro_metrics = {
        name: sum(float(fold["metrics"][name]) for fold in fold_reports) / len(fold_reports)
        for name in metric_names
    }
    output_prefix = ROOT / args.output_prefix
    prediction_path = output_prefix.with_name(output_prefix.name + "_predictions.jsonl")
    report_path = output_prefix.with_name(output_prefix.name + "_report.json")
    markdown_path = output_prefix.with_name(output_prefix.name + "_report.md")
    write_jsonl(prediction_path, all_predictions)
    report = {
        "status": "pass",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_sha256": sha256(Path(__file__).resolve()),
        "data": args.data,
        "data_sha256": sha256(data_path),
        "split_manifest": args.splits,
        "split_manifest_sha256": sha256(split_path),
        "objective": "diagnostic_lexical_transfer_under_leave_one_jurisdiction_out",
        "model_family": "char_tfidf_nearest_neighbor_output_copy",
        "vectorizer": {"analyzer": "char", "ngram_range": [2, 4], "max_features": args.max_features},
        "folds": fold_reports,
        "macro_metrics": macro_metrics,
        "prediction_path": str(prediction_path.relative_to(ROOT)),
        "prediction_sha256": sha256(prediction_path),
        "interpretation": (
            "This retrieval baseline measures lexical transfer across the two held-out jurisdictions. "
            "Exact match and token-F1 compare copied answers to the typed-contract outputs; they do not "
            "establish semantic legal correctness, physical compliance, or expert agreement."
        ),
    }
    write_json(report_path, report)
    markdown = [
        "# International rule-probe retrieval diagnostic",
        "",
        "This CPU-only character TF--IDF nearest-neighbour baseline copies the answer from the closest training record.",
        "It is a lexical-transfer diagnostic, not a semantic compliance or expert-agreement result.",
        "",
        "| Fold | Train jurisdiction | Test jurisdiction | Test records | Exact match | Token-F1 | Mean similarity |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for fold in fold_reports:
        metrics = fold["metrics"]
        markdown.append(
            f"| {fold['name']} | {fold['train_jurisdiction']} | {fold['test_jurisdiction']} | "
            f"{fold['test_records']} | {metrics['exact_match']:.4f} | {metrics['token_f1']:.4f} | "
            f"{metrics['avg_nearest_similarity']:.4f} |"
        )
    markdown.extend(
        [
            "",
            f"Macro exact match: **{macro_metrics['exact_match']:.4f}**; macro token-F1: **{macro_metrics['token_f1']:.4f}**.",
            "The held-out folds contain different rule-card IDs, so same-rule and same-standard retrieval rates are reported only as diagnostics.",
            "No cross-jurisdiction semantic generalization claim is made without independent expert review.",
        ]
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
