"""Supervised schema-aware baseline for intelligent data query records."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import Pipeline

from gridinstruct_utils import ROOT, write_json
from query_contract import overload_query
from run_tfidf_task_baselines import token_f1


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_rows(path: Path, canonical_path: Path) -> list[dict[str, Any]]:
    """Load a full split or project an ID-only manifest onto the canonical table."""

    rows = read_jsonl(path)
    if not path.name.endswith("_ids.jsonl"):
        return rows
    canonical = {str(row["id"]): row for row in read_jsonl(canonical_path)}
    missing = [str(row["id"]) for row in rows if str(row["id"]) not in canonical]
    if missing:
        raise ValueError(f"split manifest IDs missing from canonical table: {missing[:3]}")
    return [canonical[str(row["id"])] for row in rows]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def filter_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("task_type") == "intelligent_data_query"]


def text_for(row: dict[str, Any]) -> str:
    input_obj = row.get("input") or {}
    fields = [
        row.get("instruction", ""),
        input_obj.get("question", ""),
        input_obj.get("analysis_focus", ""),
        input_obj.get("request_purpose", ""),
    ]
    return "\n".join(str(item) for item in fields if item)


def label_for(row: dict[str, Any]) -> str:
    query = row.get("structured_query") or {}
    filt = str(query.get("filter"))
    if filt == "loading_percent > 100":
        return f"{filt}::{query.get('equipment_scope') or 'legacy_line'}"
    return filt


def canonical_query(query_label: str, row: dict[str, Any]) -> dict[str, Any]:
    input_obj = row.get("input") or {}
    # The scenario identifier is an exposed input field in the public contract.
    # Never fall back to the gold target: a missing identifier must remain a
    # contract failure instead of silently converting leakage into accuracy.
    scenario_id = input_obj.get("scenario_id")
    if query_label.startswith("loading_percent > 100::"):
        scope = query_label.split("::", 1)[1]
        if scope == "legacy_line":
            query = {
                "filter": "loading_percent > 100",
                "scenario_id": scenario_id,
                "source": "simulation_outputs",
            }
        else:
            query = overload_query(str(scenario_id), equipment_scope=scope)
        return {"structured_query": query}
    return {
        "structured_query": {
            "filter": query_label,
            "scenario_id": scenario_id,
            "source": "simulation_outputs",
        }
    }


def evaluate(model: Pipeline, rows: list[dict[str, Any]], output_path: Path | None = None) -> dict[str, Any]:
    labels = [label_for(row) for row in rows]
    predictions = list(model.predict([text_for(row) for row in rows])) if rows else []
    output_rows: list[dict[str, Any]] = []
    exact = 0
    token_sum = 0.0
    field_sum = 0.0
    schema_exact = 0
    scenario_exact = 0
    valid_json = 0
    for row, label, pred_label in zip(rows, labels, predictions):
        pred_obj = canonical_query(pred_label, row)
        gold_obj = {"structured_query": row.get("structured_query") or {}}
        pred_text = json.dumps(pred_obj, ensure_ascii=False, sort_keys=True)
        gold_text = json.dumps(gold_obj, ensure_ascii=False, sort_keys=True)
        pred_serialized = json.dumps(pred_obj, ensure_ascii=False, sort_keys=True)
        try:
            parsed_pred = json.loads(pred_serialized)
            is_valid_json = isinstance(parsed_pred, dict) and isinstance(parsed_pred.get("structured_query"), dict)
        except json.JSONDecodeError:
            is_valid_json = False
        valid_json += int(is_valid_json)
        is_exact = pred_obj == gold_obj
        exact += int(is_exact)
        token_sum += token_f1(gold_text, pred_text)
        gold_query = gold_obj["structured_query"]
        pred_query = pred_obj["structured_query"]
        semantic_fields = sorted((set(gold_query) | set(pred_query)) - {"scenario_id"})
        schema_exact += int(all(gold_query.get(field) == pred_query.get(field) for field in semantic_fields))
        scenario_exact += int(gold_query.get("scenario_id") == pred_query.get("scenario_id"))
        fields = sorted(set(gold_query) | set(pred_query))
        field_sum += sum(gold_query.get(field) == pred_query.get(field) for field in fields) / len(fields)
        output_rows.append(
            {
                "id": row.get("id"),
                "instruction": row.get("instruction"),
                "gold_query_label": label,
                "predicted_query_label": pred_label,
                "gold": gold_obj,
                "prediction": pred_obj,
                "exact_match": is_exact,
                "semantic_schema_exact_excluding_scenario": bool(all(gold_query.get(field) == pred_query.get(field) for field in semantic_fields)),
                "scenario_id_exact": gold_query.get("scenario_id") == pred_query.get("scenario_id"),
                "prediction_is_json": is_valid_json,
            }
        )
    if output_path is not None:
        write_jsonl(output_path, output_rows)
    n = len(rows) or 1
    label_counts = dict(Counter(labels))
    majority_label = max(label_counts, key=label_counts.get) if label_counts else None
    majority_predictions = [majority_label] * len(labels) if majority_label is not None else []
    majority_macro_f1 = (
        f1_score(labels, majority_predictions, labels=sorted(label_counts), average="macro", zero_division=0)
        if labels
        else 0.0
    )
    return {
        "accuracy": accuracy_score(labels, predictions) if rows else 0.0,
        "macro_f1": f1_score(labels, predictions, average="macro") if rows else 0.0,
        "exact_match": exact / n,
        "token_f1": token_sum / n,
        "prediction_is_json": valid_json / n,
        "deterministic_serialization_validity": valid_json / n,
        "structured_query_exact": exact / n,
        "structured_query_semantic_exact_excluding_scenario": schema_exact / n,
        "scenario_id_exact": scenario_exact / n,
        "structured_query_field_accuracy": field_sum / n,
        "n": len(rows),
        "label_counts": label_counts,
        "majority_label": majority_label,
        "majority_accuracy": (sum(label == majority_label for label in labels) / len(labels)) if labels else 0.0,
        "majority_macro_f1": majority_macro_f1,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--validation", default="data/v1.2_sd_core_validation_en.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--ood", default="data/v1.2_sd_core_ood_test_en.jsonl")
    parser.add_argument("--canonical", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-prefix", default="benchmark/v1.2_sd_core_structured_query_filter")
    args = parser.parse_args()

    canonical_path = ROOT / args.canonical
    train_rows = filter_rows(load_rows(ROOT / args.train, canonical_path))
    validation_rows = filter_rows(load_rows(ROOT / args.validation, canonical_path))
    test_rows = filter_rows(load_rows(ROOT / args.test, canonical_path))
    ood_rows = filter_rows(load_rows(ROOT / args.ood, canonical_path)) if args.ood and (ROOT / args.ood).exists() else []
    model = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=50000)),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42)),
        ]
    )
    model.fit([text_for(row) for row in train_rows], [label_for(row) for row in train_rows])
    prefix = ROOT / args.output_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "objective": "schema_aware_supervised_structured_query_filter_and_equipment_scope_selection",
        "train": args.train,
        "validation": args.validation,
        "test": args.test,
        "ood": args.ood,
        "canonical": args.canonical,
        "train_records": len(train_rows),
        "validation_records": len(validation_rows),
        "test_records": len(test_rows),
        "ood_records": len(ood_rows),
        "code_sha256": file_sha256(Path(__file__).resolve()),
        "validation_evaluation": evaluate(model, validation_rows, Path(f"{prefix}_validation_predictions.jsonl")),
        "test_evaluation": evaluate(model, test_rows, Path(f"{prefix}_test_predictions.jsonl")),
        "ood_evaluation": evaluate(model, ood_rows, Path(f"{prefix}_ood_predictions.jsonl")) if ood_rows else None,
        "input_sha256": {
            "train": hashlib.sha256((ROOT / args.train).read_bytes()).hexdigest(),
            "validation": hashlib.sha256((ROOT / args.validation).read_bytes()).hexdigest(),
            "test": hashlib.sha256((ROOT / args.test).read_bytes()).hexdigest(),
            "ood": hashlib.sha256((ROOT / args.ood).read_bytes()).hexdigest() if args.ood and (ROOT / args.ood).exists() else None,
            "canonical": file_sha256(canonical_path),
        },
        "prediction_sha256": {
            split: file_sha256(Path(f"{prefix}_{split}_predictions.jsonl"))
            for split in ("validation", "test", "ood")
            if (prefix.parent / f"{prefix.name}_{split}_predictions.jsonl").exists()
        },
        "scenario_id_input_exposed": True,
        "note": "The model predicts only the structured_query filter and, for overload requests, the equipment scope. The scenario_id is copied from the exposed input field; no gold-target fallback is allowed. Full-contract exact match therefore measures both schema prediction and exposed identifier copying, while semantic_schema_exact_excluding_scenario isolates the predicted contract fields. The reported deterministic_serialization_validity is a format check because this baseline serializes a Python object before parsing it; it is excluded from model-performance interpretation. Majority-label diagnostics expose support imbalance.",
    }
    write_json(Path(f"{prefix}_report.json"), report)
    lines = [
        "# Structured Query Filter Baseline",
        "",
        f"- Train records: {len(train_rows)}",
        f"- Test exact match: {report['test_evaluation']['exact_match']:.4f}",
        f"- Test macro F1: {report['test_evaluation']['macro_f1']:.4f}",
        f"- Test deterministic serialization validity: {report['test_evaluation']['prediction_is_json']:.4f}",
    ]
    if report["ood_evaluation"]:
        lines.append(f"- OOD exact match: {report['ood_evaluation']['exact_match']:.4f}")
    Path(f"{prefix}_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
