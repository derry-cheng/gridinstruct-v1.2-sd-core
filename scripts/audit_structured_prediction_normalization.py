#!/usr/bin/env python3
"""Audit structured prediction files with strict field-level metrics."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, write_json

TOKEN_REPLACEMENTS = {
    "structured _ query": "structured_query",
    "scenario _ id": "scenario_id",
    "simulation _ outputs": "simulation_outputs",
    "violation _ type": "violation_type",
    "loading _ percent": "loading_percent",
    "vm _ pu": "vm_pu",
    "run _ power _ flow": "run_power_flow",
    "run _ opf _ redispatch": "run_opf_redispatch",
}
FILTER_ALIASES = {
    "loading_percent>100": "loading_percent > 100",
    "vm_pu<0.95": "vm_pu < 0.95",
    "violation_type!=none": "violation_type != none",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def compact_token(value: Any) -> str:
    text = str(value or "").strip()
    for old, new in TOKEN_REPLACEMENTS.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+_\s+", "_", text)
    text = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[A-Za-z0-9])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", text)
    text = text.replace("! =", "!=")
    return text.strip()


def normalize_filter(value: Any) -> str:
    text = compact_token(value)
    compact = re.sub(r"\s+", "", text)
    return FILTER_ALIASES.get(compact, text)


def value_to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


def compact_structural_text(value: Any) -> str:
    text = value_to_text(value).strip()
    for old, new in TOKEN_REPLACEMENTS.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+([{}\[\]:,])", r"\1", text)
    text = re.sub(r"([{}\[\]:,])\s+", r"\1", text)
    text = re.sub(r"\s+_\s+", "_", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", text)
    text = text.replace("! =", "!=")
    return text


def parse_json(value: Any) -> tuple[Any | None, bool]:
    if isinstance(value, (dict, list)):
        return value, False
    raw = value_to_text(value).strip()
    candidates = [raw, compact_structural_text(raw)]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate), False
        except Exception:
            pass
        start_positions = [idx for idx in (candidate.find("{"), candidate.find("[")) if idx >= 0]
        for start in sorted(start_positions):
            try:
                obj, end = decoder.raw_decode(candidate[start:])
                trailing = candidate[start + end :].strip()
                return obj, bool(trailing)
            except Exception:
                continue
    return None, False


def canonicalize_obj(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            clean_key = compact_token(key)
            out[clean_key] = canonicalize_obj(item, clean_key)
        return out
    if isinstance(value, list):
        return [canonicalize_obj(item, parent_key) for item in value]
    if isinstance(value, str):
        if parent_key == "filter":
            return normalize_filter(value)
        return compact_token(value)
    return value


def as_set(value: Any) -> set[str]:
    return {compact_token(item) for item in (value or [])}


def evaluate_rows(rows: list[dict[str, Any]], default_task: str) -> dict[str, Any]:
    counters = Counter()
    examples = []
    for row in rows:
        task = row.get("task_type") or default_task
        gold_raw, gold_trailing = parse_json(row.get("target") or row.get("gold") or "")
        raw_pred = row.get("prediction") if "prediction" in row else row.get("prediction_structured")
        pred_raw, pred_trailing = parse_json(raw_pred)
        gold = canonicalize_obj(gold_raw) if gold_raw is not None else None
        pred = canonicalize_obj(pred_raw) if pred_raw is not None else None
        normalized_text = compact_structural_text(raw_pred)
        counters["records"] += 1
        counters["gold_trailing_garbage"] += int(gold_trailing)
        counters["prediction_trailing_garbage"] += int(pred_trailing)
        prediction_is_json = row.get("prediction_is_json")
        if prediction_is_json is None:
            prediction_is_json = pred_raw is not None
        counters["raw_prediction_is_json"] += int(bool(prediction_is_json))
        counters["normalized_prediction_is_json"] += int(pred is not None)
        if gold is not None and pred == gold:
            counters["normalized_exact"] += 1
        if isinstance(gold, dict) and isinstance(pred, dict):
            if task == "intelligent_data_query":
                gq = gold.get("structured_query") or {}
                pq = pred.get("structured_query") or {}
                fields = sorted(set(gq) | set(pq))
                if gq == pq:
                    counters["structured_query_exact"] += 1
                counters["structured_query_records"] += 1
                counters["structured_query_fields_total"] += len(fields)
                counters["structured_query_fields_correct"] += sum(gq.get(field) == pq.get(field) for field in fields)
                counters["scenario_id_exact"] += int(gq.get("scenario_id") == pq.get("scenario_id"))
                counters["filter_exact"] += int(gq.get("filter") == pq.get("filter"))
                counters["source_exact"] += int(gq.get("source") == pq.get("source"))
            if task == "auxiliary_decision":
                gt = as_set(gold.get("tools"))
                pt = as_set(pred.get("tools"))
                counters["tool_records"] += 1
                counters["tool_set_exact"] += int(gt == pt)
                union = gt | pt
                inter = gt & pt
                counters["tool_set_jaccard_sum"] += (len(inter) / len(union)) if union else 1.0
        if len(examples) < 20 and pred is not None:
            examples.append(
                {
                    "id": row.get("id"),
                    "task_type": task,
                    "raw_prediction": value_to_text(raw_pred)[:300],
                    "normalized_prediction": normalized_text[:300],
                    "canonical_prediction_preview": json.dumps(pred, ensure_ascii=False, sort_keys=True)[:300],
                }
            )
    n = max(counters["records"], 1)
    sq_n = max(counters["structured_query_records"], 1)
    tool_n = max(counters["tool_records"], 1)
    return {
        "records": counters["records"],
        "raw_prediction_is_json": counters["raw_prediction_is_json"] / n,
        "normalized_prediction_is_json": counters["normalized_prediction_is_json"] / n,
        "prediction_trailing_garbage_rate": counters["prediction_trailing_garbage"] / n,
        "normalized_exact": counters["normalized_exact"] / n,
        "structured_query_exact": counters["structured_query_exact"] / sq_n,
        "structured_query_field_accuracy": counters["structured_query_fields_correct"] / max(counters["structured_query_fields_total"], 1),
        "scenario_id_exact": counters["scenario_id_exact"] / sq_n,
        "filter_exact": counters["filter_exact"] / sq_n,
        "source_exact": counters["source_exact"] / sq_n,
        "tool_set_exact": counters["tool_set_exact"] / tool_n,
        "tool_set_jaccard": counters["tool_set_jaccard_sum"] / tool_n,
        "examples": examples,
    }


def evaluate_gate(task: str, item: dict[str, Any]) -> dict[str, Any]:
    if task == "intelligent_data_query":
        checks = {
            "normalized_json_rate_min_0_99": item.get("normalized_prediction_is_json", 0.0) >= 0.99,
            "field_accuracy_min_0_85": item.get("structured_query_field_accuracy", 0.0) >= 0.85,
            "scenario_id_exact_min_0_85": item.get("scenario_id_exact", 0.0) >= 0.85,
            "filter_exact_min_0_60": item.get("filter_exact", 0.0) >= 0.60,
        }
    elif task == "auxiliary_decision":
        checks = {
            "normalized_json_rate_min_0_99": item.get("normalized_prediction_is_json", 0.0) >= 0.99,
            "tool_set_exact_min_0_95": item.get("tool_set_exact", 0.0) >= 0.95,
            "tool_set_jaccard_min_0_95": item.get("tool_set_jaccard", 0.0) >= 0.95,
        }
    else:
        checks = {"records_present": item.get("records", 0) > 0}
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "failed_checks": [name for name, ok in checks.items() if not ok],
    }


def write_md(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Structured Prediction Normalization Audit",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "This report evaluates schema-aware structured prediction files and recomputes strict field-level metrics after normalizing tokenizer artifacts.",
        "",
        "| Task | Status | Records | Raw JSON rate | Normalized JSON rate | Trailing garbage | Normalized exact | Scenario exact | Filter exact | Tool exact |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for task, item in payload["tasks"].items():
        lines.append(
            f"| `{task}` | {item.get('gate', {}).get('status', 'unknown')} | {item['records']} | {item['raw_prediction_is_json']:.4f} | {item['normalized_prediction_is_json']:.4f} | "
            f"{item['prediction_trailing_garbage_rate']:.4f} | {item['normalized_exact']:.4f} | {item['scenario_id_exact']:.4f} | "
            f"{item['filter_exact']:.4f} | {item['tool_set_exact']:.4f} |"
        )
    lines.extend(["", "## Gates", ""])
    for task, item in payload["tasks"].items():
        gate = item.get("gate", {})
        failed = ", ".join(gate.get("failed_checks", [])) or "all checks passed"
        lines.append(f"- `{task}`: {gate.get('status', 'unknown')} ({failed})")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-predictions", default="benchmark/v1.2_sd_core_structured_query_filter_test_predictions.jsonl")
    parser.add_argument("--aux-predictions", default="benchmark/v1.2_sd_core_structured_auxiliary_tool_test_predictions.jsonl")
    parser.add_argument("--output-json", default="reports/structured_prediction_normalization_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/structured_prediction_normalization_v1.2_sd_core.md")
    args = parser.parse_args()
    tasks = {
        "intelligent_data_query": evaluate_rows(
            read_jsonl(ROOT / args.query_predictions),
            "intelligent_data_query",
        ),
        "auxiliary_decision": evaluate_rows(
            read_jsonl(ROOT / args.aux_predictions),
            "auxiliary_decision",
        ),
    }
    for task, item in tasks.items():
        item["gate"] = evaluate_gate(task, item)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": "pass" if all(item["gate"]["status"] == "pass" for item in tasks.values()) else "fail",
        "tasks": tasks,
        "input_files": {
            "intelligent_data_query": args.query_predictions,
            "auxiliary_decision": args.aux_predictions,
        },
    }
    write_json(ROOT / args.output_json, payload)
    write_md(ROOT / args.output_md, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:5000])


if __name__ == "__main__":
    main()
