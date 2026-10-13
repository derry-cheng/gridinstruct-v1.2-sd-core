"""Train fast TF-IDF baselines for all GridInstruct task families.

Classification tasks use character-ngram TF-IDF with a linear SVM.
Generation-style tasks use same-task nearest-neighbor retrieval over TF-IDF
features and copy the nearest training output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.svm import LinearSVC
from tqdm.auto import tqdm

from gridinstruct_utils import ROOT, read_jsonl, write_json, write_jsonl as atomic_write_jsonl


CLASSIFICATION_TASKS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}
GENERATION_TASKS = {
    "auxiliary_decision",
    "intelligent_data_query",
    "regulation_qa",
}


def normalize_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value).strip().split())


def make_text(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"任务类型：{row['task_type']}",
            f"阶段：{row['task_stage']}",
            f"指令：{row['instruction']}",
            f"输入：{json.dumps(row['input'], ensure_ascii=False, sort_keys=True)}",
        ]
    )


def output_text(row: dict[str, Any]) -> str:
    return normalize_text(row["output"])


def token_f1(gold: Any, pred: Any) -> float:
    gold_tokens = normalize_text(gold).split()
    pred_tokens = normalize_text(pred).split()
    if not gold_tokens and not pred_tokens:
        return 1.0
    if not gold_tokens or not pred_tokens:
        return 0.0
    gold_counts = Counter(gold_tokens)
    pred_counts = Counter(pred_tokens)
    overlap = sum((gold_counts & pred_counts).values())
    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def exact_match(gold: Any, pred: Any) -> float:
    return float(normalize_text(gold) == normalize_text(pred))


def cosine_top1(train_matrix, eval_matrix, batch_size: int, desc: str) -> tuple[list[int], list[float]]:
    top_indices: list[int] = []
    top_scores: list[float] = []
    total = eval_matrix.shape[0]
    for start in tqdm(range(0, total, batch_size), desc=desc, leave=False):
        end = min(start + batch_size, total)
        sims = eval_matrix[start:end] @ train_matrix.T
        top_indices.extend(sims.argmax(axis=1).A1.tolist())
        top_scores.extend(sims.max(axis=1).toarray().ravel().tolist())
    return top_indices, top_scores


def per_task(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["task_type"]].append(row)
    return grouped


def classification_report(train_rows: list[dict[str, Any]], eval_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task = eval_rows[0]["task_type"]
    label_field = CLASSIFICATION_TASKS[task]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1, max_features=60000)
    x_train = vectorizer.fit_transform(make_text(row) for row in train_rows)
    y_train = [row[label_field] for row in train_rows]
    x_eval = vectorizer.transform(make_text(row) for row in eval_rows)
    y_eval = [row[label_field] for row in eval_rows]

    clf = LinearSVC(dual="auto")
    if len(set(y_train)) < 2:
        raise ValueError(f"{task} training split has fewer than two labels")
    clf.fit(x_train, y_train)
    y_pred = clf.predict(x_eval).tolist()

    predictions = []
    for row, pred in zip(eval_rows, y_pred):
        predictions.append(
            {
                "id": row["id"],
                "task_type": task,
                "gold": row[label_field],
                "prediction": pred,
                "correct": pred == row[label_field],
            }
        )

    metrics = {
        "task_type": task,
        "model_family": "tfidf_linear_svc",
        "n": len(eval_rows),
        "accuracy": accuracy_score(y_eval, y_pred),
        "macro_f1": f1_score(y_eval, y_pred, labels=sorted(set(y_train) | set(y_eval)), average="macro", zero_division=0),
        "balanced_accuracy": balanced_accuracy_score(y_eval, y_pred),
        "label_space": sorted(set(y_train) | set(y_eval)),
    }
    return metrics, predictions


def generation_report(
    train_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    nearest_batch_size: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task = eval_rows[0]["task_type"]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1, max_features=60000)
    train_texts = [make_text(row) for row in train_rows]
    eval_texts = [make_text(row) for row in eval_rows]
    x_train = vectorizer.fit_transform(train_texts)
    x_eval = vectorizer.transform(eval_texts)
    top1, top_scores = cosine_top1(
        x_train,
        x_eval,
        batch_size=max(nearest_batch_size, 1),
        desc=f"{task} nearest",
    )

    predictions = []
    exact_total = 0.0
    token_f1_total = 0.0
    similarity_total = 0.0
    for row, neighbor_idx, sim in zip(eval_rows, top1, top_scores):
        neighbor = train_rows[neighbor_idx]
        pred = neighbor["output"]
        pred_text = normalize_text(pred)
        gold_text = normalize_text(row["output"])
        em = exact_match(gold_text, pred_text)
        tf1 = token_f1(gold_text, pred_text)
        exact_total += em
        token_f1_total += tf1
        similarity_total += sim
        predictions.append(
            {
                "id": row["id"],
                "task_type": task,
                "gold": row["output"],
                "prediction": pred,
                "exact_match": bool(em),
                "token_f1": tf1,
                "nearest_train_id": neighbor["id"],
                "nearest_similarity": sim,
            }
        )

    n = max(len(eval_rows), 1)
    metrics = {
        "task_type": task,
        "model_family": "tfidf_nearest_neighbor",
        "n": len(eval_rows),
        "exact_match": exact_total / n,
        "token_f1": token_f1_total / n,
        "avg_nearest_similarity": similarity_total / n,
    }
    return metrics, predictions


def run_split(
    train_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    nearest_batch_size: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grouped_train = per_task(train_rows)
    grouped_eval = per_task(eval_rows)
    if not eval_rows:
        raise ValueError("evaluation split is empty")
    unknown = sorted(set(grouped_eval) - CLASSIFICATION_TASKS.keys() - GENERATION_TASKS)
    if unknown:
        raise ValueError(f"unsupported task types in evaluation split: {unknown}")
    metrics: dict[str, Any] = {}
    predictions: list[dict[str, Any]] = []
    for task, rows in grouped_eval.items():
        train_task_rows = grouped_train.get(task, [])
        if not train_task_rows:
            raise ValueError(f"missing training rows for evaluation task {task}")
        if task in CLASSIFICATION_TASKS:
            task_metrics, task_predictions = classification_report(train_task_rows, rows)
        elif task in GENERATION_TASKS:
            task_metrics, task_predictions = generation_report(train_task_rows, rows, nearest_batch_size)
        metrics[task] = task_metrics
        predictions.extend(task_predictions)
    expected_ids = {str(row["id"]) for row in eval_rows}
    prediction_ids = [str(row["id"]) for row in predictions]
    if len(prediction_ids) != len(set(prediction_ids)) or set(prediction_ids) != expected_ids:
        raise ValueError("prediction IDs do not exactly cover the evaluation split")
    return metrics, predictions


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_jsonl(path, rows)


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


def metric_family_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    classification = [row for row in metrics.values() if "macro_f1" in row]
    generation = [row for row in metrics.values() if "token_f1" in row]
    return {
        "classification_task_macro_f1": (
            sum(float(row["macro_f1"]) for row in classification) / len(classification)
            if classification
            else None
        ),
        "classification_task_macro_accuracy": (
            sum(float(row["accuracy"]) for row in classification) / len(classification)
            if classification
            else None
        ),
        "generation_task_macro_exact_match": (
            sum(float(row["exact_match"]) for row in generation) / len(generation)
            if generation
            else None
        ),
        "generation_task_macro_token_f1": (
            sum(float(row["token_f1"]) for row in generation) / len(generation)
            if generation
            else None
        ),
        "aggregation_policy": "metrics are aggregated only within comparable task families",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--validation", default="data/v1.2_sd_core_validation_en.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--ood", default="data/v1.2_sd_core_ood_test_en.jsonl")
    parser.add_argument("--canonical", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument(
        "--output-prefix",
        default="benchmark/direct_english_tfidf_v1.2_sd_core_leakage_fixed",
    )
    parser.add_argument("--nearest-batch-size", type=int, default=2048)
    args = parser.parse_args()

    canonical_path = ROOT / args.canonical
    train_rows = load_rows(ROOT / args.train, canonical_path)
    validation_rows = load_rows(ROOT / args.validation, canonical_path)
    test_rows = load_rows(ROOT / args.test, canonical_path)
    ood_rows = load_rows(ROOT / args.ood, canonical_path) if args.ood else []
    for name, rows in (("train", train_rows), ("validation", validation_rows), ("test", test_rows)):
        ids = [str(row.get("id") or "") for row in rows]
        if not rows or any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{name} split must be non-empty with unique non-empty IDs")
    if args.ood and not ood_rows:
        raise ValueError("requested OOD split is empty")

    validation_metrics, validation_predictions = run_split(train_rows, validation_rows, args.nearest_batch_size)
    test_metrics, test_predictions = run_split(train_rows, test_rows, args.nearest_batch_size)
    ood_metrics, ood_predictions = (
        run_split(train_rows, ood_rows, args.nearest_batch_size) if ood_rows else ({}, [])
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_sha256": sha256(Path(__file__).resolve()),
        "train": args.train,
        "validation": args.validation,
        "test": args.test,
        "ood": args.ood,
        "canonical": args.canonical,
        "objective": "task_specific_tfidf_baselines",
        "status": "pass",
        "train_records": len(train_rows),
        "validation_records": len(validation_rows),
        "test_records": len(test_rows),
        "ood_records": len(ood_rows),
        "input_sha256": {
            "train": sha256(ROOT / args.train),
            "validation": sha256(ROOT / args.validation),
            "test": sha256(ROOT / args.test),
            "ood": sha256(ROOT / args.ood) if args.ood else None,
            "canonical": sha256(canonical_path),
        },
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "ood_metrics": ood_metrics,
        "validation_metric_families": metric_family_summary(validation_metrics),
        "test_metric_families": metric_family_summary(test_metrics),
        "ood_metric_families": metric_family_summary(ood_metrics),
        "deprecated_cross_task_macro_score": None,
        "note": "Classification tasks use macro-F1 as the primary metric. Generation tasks report exact match and token-F1 separately; incompatible metrics are not averaged together.",
    }

    output_prefix = ROOT / args.output_prefix
    validation_path = output_prefix.with_name(output_prefix.name + "_validation_predictions.jsonl")
    test_path = output_prefix.with_name(output_prefix.name + "_test_predictions.jsonl")
    ood_path = output_prefix.with_name(output_prefix.name + "_ood_predictions.jsonl")
    write_jsonl(validation_path, validation_predictions)
    write_jsonl(test_path, test_predictions)
    if args.ood:
        write_jsonl(ood_path, ood_predictions)
    report["prediction_sha256"] = {
        "validation": sha256(validation_path),
        "test": sha256(test_path),
        "ood": sha256(ood_path) if args.ood else None,
    }
    write_json(output_prefix.with_name(output_prefix.name + "_report.json"), report)


if __name__ == "__main__":
    main()
