"""Analyze regulation-QA seq2seq prediction quality by dataset evidence fields."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from gridinstruct_utils import ROOT, write_json


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def avg(values: list[float]) -> float:
    return mean(values) if values else 0.0


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def compact(text: Any, limit: int = 280) -> str:
    value = str(text).replace("\n", " ").strip()
    return value if len(value) <= limit else value[: limit - 1] + "..."


def contains_all(text: str, values: list[Any]) -> bool:
    if not values:
        return True
    haystack = text or ""
    return all(str(value) in haystack for value in values if value not in (None, ""))


def summarize_group(rows: list[dict[str, Any]], key: str, top_n: int = 12) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        groups[str(value)].append(float(row["token_f1"]))
    summary = [
        {
            key: value,
            "n": len(scores),
            "mean_token_f1": avg(scores),
            "p10_token_f1": quantile(scores, 0.10),
            "min_token_f1": min(scores),
        }
        for value, scores in groups.items()
    ]
    return sorted(summary, key=lambda item: (item["mean_token_f1"], -item["n"]))[:top_n]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", default="benchmark/v1.2_paper_plus9_seq2seq_regqa_harmonized_e2_test_predictions.jsonl")
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus9.jsonl")
    parser.add_argument("--output-json", default="reports/regqa_prediction_quality_v1.2_plus9_harmonized_e2.json")
    parser.add_argument("--output-md", default="reports/regqa_prediction_quality_v1.2_plus9_harmonized_e2.md")
    parser.add_argument("--low-f1-threshold", type=float, default=0.55)
    args = parser.parse_args()

    predictions = read_jsonl(ROOT / args.predictions)
    dataset_by_id = {row["id"]: row for row in read_jsonl(ROOT / args.dataset)}
    enriched = []
    for pred in predictions:
        row = dict(dataset_by_id.get(pred["id"], {}))
        input_obj = pred.get("input") if isinstance(pred.get("input"), dict) else row.get("input", {})
        metadata = pred.get("metadata") if isinstance(pred.get("metadata"), dict) else row.get("metadata", {})
        prediction_text = str(pred.get("prediction", ""))
        evidence_fields = input_obj.get("evidence_fields") or []
        applicable_tasks = input_obj.get("applicable_tasks") or []
        enriched.append(
            {
                "id": pred["id"],
                "token_f1": float(pred.get("token_f1", 0.0)),
                "exact_match": bool(pred.get("exact_match")),
                "rule_id": input_obj.get("rule_id"),
                "audience": input_obj.get("audience"),
                "usage_context": input_obj.get("usage_context"),
                "deliverable": input_obj.get("deliverable"),
                "focus_area": input_obj.get("focus_area"),
                "style_hint": input_obj.get("style_hint"),
                "validation_status": metadata.get("validation_status"),
                "created_by": metadata.get("created_by"),
                "variant_index": metadata.get("variant_index"),
                "evidence_fields": evidence_fields,
                "applicable_tasks": applicable_tasks,
                "mentions_rule_id": str(input_obj.get("rule_id")) in prediction_text if input_obj.get("rule_id") else False,
                "mentions_deliverable": str(input_obj.get("deliverable")) in prediction_text if input_obj.get("deliverable") else False,
                "mentions_focus_area": str(input_obj.get("focus_area")) in prediction_text if input_obj.get("focus_area") else False,
                "mentions_all_evidence_fields": contains_all(prediction_text, evidence_fields),
                "mentions_all_applicable_tasks": contains_all(prediction_text, applicable_tasks),
                "instruction": row.get("instruction") or pred.get("instruction"),
                "input": input_obj,
                "gold": pred.get("gold"),
                "prediction": pred.get("prediction"),
            }
        )

    scores = [row["token_f1"] for row in enriched]
    low_rows = [row for row in enriched if row["token_f1"] < args.low_f1_threshold]
    slot_rates = {
        "mentions_rule_id": avg([float(row["mentions_rule_id"]) for row in enriched]),
        "mentions_deliverable": avg([float(row["mentions_deliverable"]) for row in enriched]),
        "mentions_focus_area": avg([float(row["mentions_focus_area"]) for row in enriched]),
        "mentions_all_evidence_fields": avg([float(row["mentions_all_evidence_fields"]) for row in enriched]),
        "mentions_all_applicable_tasks": avg([float(row["mentions_all_applicable_tasks"]) for row in enriched]),
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "predictions": args.predictions,
        "dataset": args.dataset,
        "records": len(enriched),
        "overall": {
            "mean_token_f1": avg(scores),
            "min_token_f1": min(scores) if scores else 0.0,
            "p10_token_f1": quantile(scores, 0.10),
            "median_token_f1": quantile(scores, 0.50),
            "p90_token_f1": quantile(scores, 0.90),
            "low_f1_threshold": args.low_f1_threshold,
            "low_f1_count": len(low_rows),
            "low_f1_rate": len(low_rows) / len(enriched) if enriched else 0.0,
        },
        "slot_mention_rates": slot_rates,
        "weakest_groups": {
            "rule_id": summarize_group(enriched, "rule_id"),
            "deliverable": summarize_group(enriched, "deliverable"),
            "focus_area": summarize_group(enriched, "focus_area"),
            "audience": summarize_group(enriched, "audience"),
            "usage_context": summarize_group(enriched, "usage_context"),
            "validation_status": summarize_group(enriched, "validation_status"),
            "created_by": summarize_group(enriched, "created_by"),
            "variant_index": summarize_group(enriched, "variant_index"),
        },
        "low_f1_examples": [
            {
                "id": row["id"],
                "token_f1": row["token_f1"],
                "rule_id": row["rule_id"],
                "deliverable": row["deliverable"],
                "focus_area": row["focus_area"],
                "validation_status": row["validation_status"],
                "instruction": compact(row["instruction"]),
                "gold": compact(row["gold"]),
                "prediction": compact(row["prediction"]),
            }
            for row in sorted(enriched, key=lambda item: item["token_f1"])[:20]
        ],
    }
    write_json(ROOT / args.output_json, payload)

    lines = [
        "# Regulation-QA Prediction Quality Analysis",
        "",
        f"Generated: {payload['generated_at']}",
        f"Predictions: `{args.predictions}`",
        "",
        "## Overall",
        "",
        f"- Records: {payload['records']}",
        f"- Mean token F1: {payload['overall']['mean_token_f1']:.4f}",
        f"- P10 / median / P90: {payload['overall']['p10_token_f1']:.4f} / {payload['overall']['median_token_f1']:.4f} / {payload['overall']['p90_token_f1']:.4f}",
        f"- Low-F1 count (< {args.low_f1_threshold}): {payload['overall']['low_f1_count']} ({payload['overall']['low_f1_rate']:.2%})",
        "",
        "## Slot Mention Rates",
        "",
    ]
    for key, value in slot_rates.items():
        lines.append(f"- {key}: {value:.4f}")
    lines.extend(["", "## Weakest Groups", ""])
    for group_name, group_rows in payload["weakest_groups"].items():
        lines.append(f"### {group_name}")
        lines.append("| value | n | mean_token_f1 | p10_token_f1 | min_token_f1 |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for row in group_rows:
            value = row.get(group_name)
            lines.append(
                f"| {value} | {row['n']} | {row['mean_token_f1']:.4f} | "
                f"{row['p10_token_f1']:.4f} | {row['min_token_f1']:.4f} |"
            )
        lines.append("")
    lines.extend(["## Lowest-F1 Examples", ""])
    for row in payload["low_f1_examples"][:10]:
        lines.append(f"### {row['id']} ({row['token_f1']:.4f})")
        lines.append(f"- rule_id: {row['rule_id']}; deliverable: {row['deliverable']}; focus_area: {row['focus_area']}; status: {row['validation_status']}")
        lines.append(f"- instruction: {row['instruction']}")
        lines.append(f"- gold: {row['gold']}")
        lines.append(f"- prediction: {row['prediction']}")
        lines.append("")
    (ROOT / args.output_md).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
