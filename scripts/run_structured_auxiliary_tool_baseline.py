"""Supervised schema-aware baseline for auxiliary-decision tool plans."""

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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
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
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def filter_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("task_type") == "auxiliary_decision"]


def tool_sequence(row: dict[str, Any]) -> list[str]:
    tools: list[str] = []
    for step in row.get("tool_plan") or []:
        if isinstance(step, dict):
            tool = str(step.get("tool") or "").strip()
            if tool:
                tools.append(tool)
    return tools


def label_for(row: dict[str, Any]) -> str:
    return json.dumps(tool_sequence(row), ensure_ascii=False, sort_keys=True)


def text_for(row: dict[str, Any]) -> str:
    input_obj = row.get("input") or {}
    fields = [
        row.get("instruction", ""),
        input_obj.get("analysis_focus", ""),
        input_obj.get("request_purpose", ""),
        input_obj.get("scenario_id", ""),
        input_obj.get("severity", ""),
        input_obj.get("risk_level", ""),
        json.dumps(input_obj.get("violation_summary") or {}, ensure_ascii=False, sort_keys=True),
        json.dumps(input_obj.get("pre_action_metrics") or {}, ensure_ascii=False, sort_keys=True),
        json.dumps(row.get("source_regulation_ids") or [], ensure_ascii=False, sort_keys=True),
    ]
    return "\n".join(str(item) for item in fields if item)


def decode_label(label: str) -> list[str]:
    value = json.loads(label)
    return [str(item) for item in value]


def evaluate(model: Pipeline, rows: list[dict[str, Any]], output_path: Path | None = None) -> dict[str, Any]:
    labels = [label_for(row) for row in rows]
    predictions = list(model.predict([text_for(row) for row in rows])) if rows else []
    output_rows: list[dict[str, Any]] = []
    exact = 0
    set_exact = 0
    jaccard_sum = 0.0
    valid_json = 0
    for row, gold_label, pred_label in zip(rows, labels, predictions):
        gold_tools = decode_label(gold_label)
        pred_tools = decode_label(pred_label)
        gold_set = set(gold_tools)
        pred_set = set(pred_tools)
        union = gold_set | pred_set
        inter = gold_set & pred_set
        is_exact = gold_tools == pred_tools
        exact += int(is_exact)
        is_set_exact = gold_set == pred_set
        set_exact += int(is_set_exact)
        prediction_payload = json.dumps({"tools": pred_tools}, ensure_ascii=False, sort_keys=True)
        try:
            parsed_prediction = json.loads(prediction_payload)
            is_valid_json = isinstance(parsed_prediction, dict) and isinstance(parsed_prediction.get("tools"), list)
        except json.JSONDecodeError:
            is_valid_json = False
        valid_json += int(is_valid_json)
        jaccard = len(inter) / len(union) if union else 1.0
        jaccard_sum += jaccard
        output_rows.append(
            {
                "id": row.get("id"),
                "task_type": "auxiliary_decision",
                "instruction": row.get("instruction"),
                "gold": {"tools": gold_tools},
                "prediction": {"tools": pred_tools},
                "prediction_is_json": is_valid_json,
                "tool_sequence_exact": is_exact,
                "tool_set_exact": is_set_exact,
                "tool_set_jaccard": jaccard,
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
        "tool_sequence_exact": exact / n,
        "tool_set_exact": set_exact / n,
        "tool_set_jaccard": jaccard_sum / n,
        "prediction_is_json": valid_json / n,
        "deterministic_serialization_validity": valid_json / n,
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
    parser.add_argument("--output-prefix", default="benchmark/v1.2_sd_core_structured_auxiliary_tool")
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
        "objective": "schema_aware_supervised_auxiliary_tool_sequence_prediction",
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
        "note": "The model predicts the ordered auxiliary tool sequence from the released input contract. Sequence exactness and set exactness are reported separately. The reported deterministic_serialization_validity is a format check because this baseline serializes a Python object before parsing it; it is excluded from model-performance interpretation. Majority-label diagnostics expose support imbalance. This is a contract-prediction baseline, not an executable-control test.",
    }
    write_json(Path(f"{prefix}_report.json"), report)
    lines = [
        "# Structured Auxiliary Tool Baseline",
        "",
        f"- Train records: {len(train_rows)}",
        f"- Test tool-sequence exact: {report['test_evaluation']['tool_sequence_exact']:.4f}",
        f"- Test macro F1: {report['test_evaluation']['macro_f1']:.4f}",
        f"- Test deterministic serialization validity: {report['test_evaluation']['prediction_is_json']:.4f}",
    ]
    if report["ood_evaluation"]:
        lines.append(f"- OOD tool-sequence exact: {report['ood_evaluation']['tool_sequence_exact']:.4f}")
    Path(f"{prefix}_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
