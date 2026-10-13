#!/usr/bin/env python3
"""Run a probability-producing TF-IDF baseline with ECE and Brier scores."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score

from gridinstruct_utils import ROOT, read_jsonl, write_json, write_jsonl


TASK_FIELDS = {
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


def text_for(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(row.get("task_type") or ""),
            str(row.get("instruction") or ""),
            json.dumps(row.get("input"), ensure_ascii=False, sort_keys=True),
        ]
    )


def ece(y_true: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    predicted = probabilities.argmax(axis=1)
    confidence = probabilities.max(axis=1)
    correct = (predicted == y_true).astype(float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    value = 0.0
    for index in range(bins):
        mask = (confidence > edges[index]) & (confidence <= edges[index + 1])
        if not np.any(mask):
            continue
        value += float(mask.mean()) * abs(float(correct[mask].mean()) - float(confidence[mask].mean()))
    return value


def multiclass_brier(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    one_hot = np.zeros_like(probabilities)
    one_hot[np.arange(len(y_true)), y_true] = 1.0
    return float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))


def evaluate(model: Any, vectorizer: TfidfVectorizer, rows: list[dict[str, Any]], field: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not rows:
        return {"n": 0, "labels": [], "macro_f1": None, "ece_10bin": None, "brier_multiclass": None, "probability_finite": True, "probability_nonnegative": True, "probability_rows_sum_to_one": True}, []
    texts = [text_for(row) for row in rows]
    labels = sorted({str(row.get(field)) for row in rows})
    label_to_id = {label: index for index, label in enumerate(labels)}
    y_true = np.asarray([label_to_id[str(row.get(field))] for row in rows], dtype=int)
    probabilities = model.predict_proba(vectorizer.transform(texts))
    predictions = probabilities.argmax(axis=1)
    report = {
        "n": len(rows),
        "labels": labels,
        "macro_f1": float(f1_score(y_true, predictions, labels=list(range(len(labels))), average="macro", zero_division=0)),
        "ece_10bin": ece(y_true, probabilities, bins=10),
        "brier_multiclass": multiclass_brier(y_true, probabilities),
        "probability_finite": bool(np.isfinite(probabilities).all()),
        "probability_nonnegative": bool((probabilities >= 0.0).all()),
        "probability_rows_sum_to_one": bool(np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-6)),
    }
    output = [
        {
            "id": row["id"],
            "task_type": row["task_type"],
            "gold": str(row.get(field)),
            "prediction": labels[int(prediction)],
            "confidence": float(probabilities[index].max()),
            "probabilities": {labels[j]: float(probabilities[index, j]) for j in range(len(labels))},
        }
        for index, (row, prediction) in enumerate(zip(rows, predictions))
    ]
    return report, output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--ood", required=True)
    parser.add_argument("--canonical", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-prefix", default="benchmark/instruction_surface_balanced_v1.2_sd_core/calibrated_tfidf")
    args = parser.parse_args()

    canonical_path = ROOT / args.canonical
    train = load_rows(ROOT / args.train, canonical_path)
    test = load_rows(ROOT / args.test, canonical_path)
    ood = load_rows(ROOT / args.ood, canonical_path)
    prefix = ROOT / args.output_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    metrics: dict[str, Any] = {}
    prediction_hashes: dict[str, str] = {}
    for task, field in TASK_FIELDS.items():
        train_rows = [row for row in train if row.get("task_type") == task]
        task_test = [row for row in test if row.get("task_type") == task]
        task_ood = [row for row in ood if row.get("task_type") == task]
        vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), max_features=60000)
        x_train = vectorizer.fit_transform(text_for(row) for row in train_rows)
        labels = [str(row.get(field)) for row in train_rows]
        model = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42)
        model.fit(x_train, labels)
        test_metrics, test_predictions = evaluate(model, vectorizer, task_test, field)
        ood_metrics, ood_predictions = evaluate(model, vectorizer, task_ood, field)
        metrics[task] = {"test": test_metrics, "ood": ood_metrics}
        for name, predictions in (("test", test_predictions), ("ood", ood_predictions)):
            path = prefix.with_name(f"{prefix.name}_{task}_{name}_predictions.jsonl")
            write_jsonl(path, predictions)
            prediction_hashes[f"{task}_{name}"] = sha256(path)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(
            item[scope][key]
            for item in metrics.values()
            for scope in ("test", "ood")
            for key in ("probability_finite", "probability_nonnegative", "probability_rows_sum_to_one")
        ) else "fail",
        "objective": "calibrated_probability_tfidf_classification",
        "train": args.train,
        "test": args.test,
        "ood": args.ood,
        "canonical": args.canonical,
        "train_records": len(train),
        "test_records": len(test),
        "ood_records": len(ood),
        "code_sha256": sha256(Path(__file__).resolve()),
        "input_sha256": {
            name: sha256(ROOT / value)
            for name, value in (("train", args.train), ("test", args.test), ("ood", args.ood))
        }
        | {"canonical": sha256(canonical_path)},
        "prediction_sha256": prediction_hashes,
        "metrics": metrics,
        "interpretation": "ECE and multiclass Brier scores quantify probability calibration for a CPU logistic TF-IDF reference; they do not imply physical-label correctness or dispatch competence.",
    }
    write_json(prefix.with_name(f"{prefix.name}_report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
