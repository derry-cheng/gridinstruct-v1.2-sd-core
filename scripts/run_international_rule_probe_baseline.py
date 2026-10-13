"""Run leakage-controlled jurisdiction transfer diagnostics on the rule probe.

This CPU-only baseline fits character TF--IDF on one jurisdiction, excludes
explicit rule, standard, clause, and jurisdiction identifiers from the input,
and transfers the nearest typed rule contract to the held-out jurisdiction.
The copied-answer scores remain diagnostics; structured field scores are the
primary transfer measurements and do not replace independent expert review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json, write_jsonl


class CharTfidf:
    """Small dependency-free character TF--IDF index for the 512-row probe."""

    def __init__(self, max_features: int = 60000) -> None:
        self.max_features = max_features
        self.idf: dict[str, float] = {}

    @staticmethod
    def grams(value: str) -> set[str]:
        padded = f"  {value.lower()}  "
        return {
            padded[index : index + width]
            for width in (2, 3, 4)
            for index in range(max(0, len(padded) - width + 1))
        }

    def fit(self, texts: list[str]) -> "CharTfidf":
        from collections import Counter
        import math

        df = Counter()
        for value in texts:
            df.update(self.grams(value))
        if len(df) > self.max_features:
            keep = {gram for gram, _ in df.most_common(self.max_features)}
        else:
            keep = set(df)
        self.idf = {
            gram: math.log((1 + len(texts)) / (1 + df[gram])) + 1.0
            for gram in keep
        }
        return self

    def vector(self, value: str) -> dict[str, float]:
        import math
        from collections import Counter

        counts = Counter(gram for gram in self.grams(value) if gram in self.idf)
        vec = {gram: float(count) * self.idf[gram] for gram, count in counts.items()}
        norm = math.sqrt(sum(item * item for item in vec.values()))
        return {gram: item / norm for gram, item in vec.items()} if norm else {}

    @staticmethod
    def cosine(left: dict[str, float], right: dict[str, float]) -> float:
        if len(left) > len(right):
            left, right = right, left
        return sum(value * right.get(key, 0.0) for key, value in left.items())


def normalize_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value).strip().split())


def make_text(row: dict[str, Any]) -> str:
    inp = row.get("input", {})
    # Identifiers and jurisdiction names are excluded so the baseline cannot
    # win by copying a label embedded in the prompt surface.
    scrubbed_question = normalize_text(inp.get("question", ""))
    for token in (
        str(inp.get("rule_id", "")),
        str(inp.get("standard_id", "")),
        str(inp.get("clause_id", "")),
        str(inp.get("jurisdiction", "")),
    ):
        if token:
            scrubbed_question = scrubbed_question.replace(token, "RULE_ID")
    return "\n".join(
        [
            f"instruction: {scrubbed_question}",
            f"summary: {normalize_text(inp.get('rule_summary', ''))}",
            f"evidence: {','.join(sorted(str(item) for item in (inp.get('evidence_fields') or [])))}",
            f"deliverable: {normalize_text(inp.get('deliverable', ''))}",
            f"focus: {normalize_text(inp.get('focus_area', ''))}",
        ]
    )


def set_f1(gold: Any, prediction: Any) -> float:
    gold_set = {str(item) for item in (gold or [])}
    prediction_set = {str(item) for item in (prediction or [])}
    if not gold_set and not prediction_set:
        return 1.0
    if not gold_set or not prediction_set:
        return 0.0
    overlap = len(gold_set & prediction_set)
    precision = overlap / len(prediction_set)
    recall = overlap / len(gold_set)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


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


def format_report_path(path: Path) -> str:
    """Return a stable project-relative path or preserve an external staging path."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def nearest_rows(
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    max_features: int,
) -> tuple[dict[str, float | int], list[dict[str, Any]]]:
    index = CharTfidf(max_features=max_features).fit([make_text(row) for row in train_rows])
    train_vectors = [index.vector(make_text(row)) for row in train_rows]
    top_indices: list[int] = []
    top_scores: list[float] = []
    for row in test_rows:
        test_vector = index.vector(make_text(row))
        scores = [index.cosine(test_vector, candidate) for candidate in train_vectors]
        best = max(range(len(scores)), key=lambda item: (scores[item], -item))
        top_indices.append(best)
        top_scores.append(scores[best])

    exact_values: list[float] = []
    f1_values: list[float] = []
    rule_values: list[float] = []
    standard_values: list[float] = []
    jurisdiction_values: list[float] = []
    clause_values: list[float] = []
    evidence_values: list[float] = []
    prediction_rows: list[dict[str, Any]] = []
    for row, index, similarity in zip(test_rows, top_indices, top_scores):
        neighbor = train_rows[index]
        gold = row["output"]
        prediction = neighbor["output"]
        gold_contract = row.get("target_contract") or {}
        neighbor_contract = neighbor.get("target_contract") or {}
        gold_rule = gold_contract.get("rule_id", row["input"]["rule_id"])
        neighbor_rule = neighbor_contract.get("rule_id", neighbor["input"]["rule_id"])
        gold_standard = gold_contract.get("standard_id", row["metadata"]["standard_id"])
        neighbor_standard = neighbor_contract.get("standard_id", neighbor["metadata"]["standard_id"])
        gold_jurisdiction = gold_contract.get("jurisdiction", row["metadata"]["jurisdiction"])
        neighbor_jurisdiction = neighbor_contract.get("jurisdiction", neighbor["metadata"]["jurisdiction"])
        gold_clause = gold_contract.get("clause_id", row["input"]["clause_id"])
        neighbor_clause = neighbor_contract.get("clause_id", neighbor["input"]["clause_id"])
        gold_evidence = gold_contract.get("evidence_fields", row["input"].get("evidence_fields"))
        neighbor_evidence = neighbor_contract.get("evidence_fields", neighbor["input"].get("evidence_fields"))
        exact_values.append(float(exact_match(gold, prediction)))
        f1_values.append(token_f1(gold, prediction))
        rule_values.append(float(gold_rule == neighbor_rule))
        standard_values.append(float(gold_standard == neighbor_standard))
        jurisdiction_values.append(float(gold_jurisdiction == neighbor_jurisdiction))
        clause_values.append(float(gold_clause == neighbor_clause))
        evidence_values.append(set_f1(gold_evidence, neighbor_evidence))
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
                "gold_jurisdiction": gold_jurisdiction,
                "neighbor_jurisdiction": neighbor_jurisdiction,
                "gold_clause_id": gold_clause,
                "neighbor_clause_id": neighbor_clause,
                "gold_evidence_fields": gold_evidence,
                "neighbor_evidence_fields": neighbor_evidence,
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
        "rule_id_accuracy": sum(rule_values) / denominator,
        "standard_id_accuracy": sum(standard_values) / denominator,
        "jurisdiction_accuracy": sum(jurisdiction_values) / denominator,
        "clause_exact": sum(clause_values) / denominator,
        "evidence_field_f1": sum(evidence_values) / denominator,
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
                "fold_type": fold.get("fold_type", "unspecified"),
                "train_jurisdiction": fold["train_jurisdiction"],
                "test_jurisdiction": fold["test_jurisdiction"],
                "train_records": len(train_rows),
                "test_records": len(test_rows),
                "metrics": metrics,
                "prediction_count": len(predictions),
            }
        )
        all_predictions.extend(predictions)

    metric_names = [
        "exact_match", "token_f1", "avg_nearest_similarity", "rule_id_accuracy",
        "standard_id_accuracy", "jurisdiction_accuracy", "clause_exact", "evidence_field_f1",
    ]
    macro_metrics = {
        name: sum(float(fold["metrics"][name]) for fold in fold_reports) / len(fold_reports)
        for name in metric_names
    }
    grouped_macro_metrics = {}
    for fold_type in sorted({str(fold.get("fold_type", "unspecified")) for fold in fold_reports}):
        group = [fold for fold in fold_reports if str(fold.get("fold_type", "unspecified")) == fold_type]
        grouped_macro_metrics[fold_type] = {
            name: sum(float(fold["metrics"][name]) for fold in group) / len(group)
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
        "objective": "leakage_controlled_structured_transfer_under_leave_one_jurisdiction_out",
        "model_family": "char_tfidf_nearest_neighbor_typed_contract_transfer",
        "vectorizer": {"analyzer": "char", "ngram_range": [2, 4], "max_features": args.max_features, "implementation": "dependency_free"},
        "folds": fold_reports,
        "macro_metrics": macro_metrics,
        "grouped_macro_metrics": grouped_macro_metrics,
        "prediction_path": format_report_path(prediction_path),
        "prediction_sha256": sha256(prediction_path),
        "interpretation": (
            "The baseline excludes explicit rule, standard, clause, and jurisdiction identifiers from its "
            "input. Structured field metrics are the primary transfer diagnostics. Exact match and token-F1 "
            "compare copied answers to the typed-contract outputs; none establishes legal correctness, "
            "physical compliance, or expert agreement."
        ),
    }
    write_json(report_path, report)
    markdown = [
        "# International rule-probe retrieval diagnostic",
        "",
        "This CPU-only character TF--IDF nearest-neighbour baseline transfers the typed rule contract from the closest training record.",
        "Explicit rule, standard, clause, and jurisdiction identifiers are excluded from its input.",
        "",
        "| Fold | Type | Train jurisdiction | Test jurisdiction | N | Rule acc. | Standard acc. | Evidence F1 | Answer exact |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for fold in fold_reports:
        metrics = fold["metrics"]
        markdown.append(
            f"| {fold['name']} | {fold.get('fold_type', 'unspecified')} | {fold['train_jurisdiction']} | {fold['test_jurisdiction']} | "
            f"{fold['test_records']} | {metrics['rule_id_accuracy']:.4f} | "
            f"{metrics['standard_id_accuracy']:.4f} | {metrics['evidence_field_f1']:.4f} | "
            f"{metrics['exact_match']:.4f} |"
        )
    markdown.extend(
        [
            "",
            f"Overall macro rule-card accuracy: **{macro_metrics['rule_id_accuracy']:.4f}**; "
            f"overall macro standard accuracy: **{macro_metrics['standard_id_accuracy']:.4f}**; "
            f"overall macro evidence-field F1: **{macro_metrics['evidence_field_f1']:.4f}**.",
            f"Cross-jurisdiction rule-card accuracy: **{grouped_macro_metrics.get('cross_jurisdiction', {}).get('rule_id_accuracy', 0.0):.4f}**; "
            f"within-jurisdiction variant-holdout rule-card accuracy: **{grouped_macro_metrics.get('within_jurisdiction_variant_holdout', {}).get('rule_id_accuracy', 0.0):.4f}**.",
            f"Copied-answer exact match is a secondary diagnostic (**{macro_metrics['exact_match']:.4f}**).",
            "No cross-jurisdiction semantic generalization claim is made without independent expert review.",
        ]
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
