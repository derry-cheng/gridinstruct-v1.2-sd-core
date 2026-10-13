"""Apply task-schema constrained serialization to seq2seq predictions.

The baseline generator may produce the correct field sequence while omitting
outer JSON braces. This script keeps raw metrics in the report and adds a
schema-constrained evaluation that serializes recognized task fields into the
dataset schema before scoring.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_json, write_json
from run_seq2seq_generation_baseline import token_f1


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def find_quoted_field(text: str, field: str) -> str | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"([^"]*)"', text)
    return match.group(1) if match else None


def repair_query(prediction: str) -> dict[str, Any] | None:
    parsed = parse_json(prediction)
    if isinstance(parsed, dict) and isinstance(parsed.get("structured_query"), dict):
        return parsed
    structured = parsed.get("structured_query") if isinstance(parsed, dict) else None
    if isinstance(structured, dict):
        return {"structured_query": structured}
    fields = {
        "filter": find_quoted_field(prediction, "filter"),
        "scenario_id": find_quoted_field(prediction, "scenario_id"),
        "source": find_quoted_field(prediction, "source"),
    }
    if all(fields.values()):
        return {"structured_query": fields}
    return None


def repair_auxiliary(prediction: str) -> dict[str, Any] | None:
    parsed = parse_json(prediction)
    if isinstance(parsed, dict) and "response" in parsed:
        return parsed
    response = None
    match = re.search(r'"response"\s*:\s*"(.*?)"\s*,\s*"tools"\s*:', prediction, flags=re.DOTALL)
    if match:
        response = match.group(1)
    tools: list[str] | None = None
    tools_match = re.search(r'"tools"\s*:\s*(\[[^\]]*\])', prediction, flags=re.DOTALL)
    if tools_match:
        parsed_tools = parse_json(tools_match.group(1))
        if isinstance(parsed_tools, list):
            tools = [str(item) for item in parsed_tools]
    if response is not None:
        return {"response": response, "tools": tools or []}
    return None


def schema_repair(task_type: str, prediction: str) -> dict[str, Any] | None:
    if task_type == "intelligent_data_query":
        return repair_query(prediction)
    if task_type == "auxiliary_decision":
        return repair_auxiliary(prediction)
    parsed = parse_json(prediction)
    return parsed if isinstance(parsed, dict) else None


def score_rows(path: Path) -> dict[str, Any]:
    rows = read_jsonl(path)
    exact = 0
    token_sum = 0.0
    json_ok = 0
    structured_exact = 0
    structured_field_sum = 0.0
    structured_field_n = 0
    tool_exact = 0
    tool_jaccard_sum = 0.0
    tool_n = 0
    response_f1_sum = 0.0
    response_n = 0

    for row in rows:
        gold_text = str(row.get("gold", "")).strip()
        task_type = str(row.get("task_type", ""))
        gold_obj = parse_json(gold_text)
        repaired = schema_repair(task_type, str(row.get("prediction", "")).strip())
        if repaired is None:
            schema_text = str(row.get("prediction", "")).strip()
            row["schema_prediction_is_json"] = False
            row["schema_prediction"] = None
        else:
            schema_text = canon(repaired)
            json_ok += 1
            row["schema_prediction_is_json"] = True
            row["schema_prediction"] = repaired
        row["schema_exact_match"] = gold_text == schema_text
        row["schema_token_f1"] = token_f1(gold_text, schema_text)
        exact += int(row["schema_exact_match"])
        token_sum += float(row["schema_token_f1"])

        if isinstance(gold_obj, dict) and isinstance(repaired, dict):
            if task_type == "intelligent_data_query":
                gold_query = gold_obj.get("structured_query", {})
                pred_query = repaired.get("structured_query", {})
                if isinstance(gold_query, dict) and isinstance(pred_query, dict):
                    fields = sorted(set(gold_query) | set(pred_query))
                    if fields:
                        structured_field_sum += sum(gold_query.get(field) == pred_query.get(field) for field in fields) / len(fields)
                        structured_field_n += 1
                    structured_exact += int(gold_query == pred_query)
                    row["schema_structured_query_exact"] = gold_query == pred_query
            if task_type == "auxiliary_decision":
                gold_tools = set(gold_obj.get("tools") or [])
                pred_tools = set(repaired.get("tools") or [])
                union = gold_tools | pred_tools
                jaccard = len(gold_tools & pred_tools) / len(union) if union else 1.0
                tool_exact += int(gold_tools == pred_tools)
                tool_jaccard_sum += jaccard
                tool_n += 1
                row["schema_tool_set_exact"] = gold_tools == pred_tools
                row["schema_tool_set_jaccard"] = jaccard
                if isinstance(gold_obj.get("response"), str) and isinstance(repaired.get("response"), str):
                    response_f1 = token_f1(gold_obj["response"], repaired["response"])
                    response_f1_sum += response_f1
                    response_n += 1
                    row["schema_response_token_f1"] = response_f1

    write_jsonl(path, rows)
    n = len(rows) or 1
    metrics: dict[str, Any] = {
        "exact_match": exact / n,
        "token_f1": token_sum / n,
        "n": len(rows),
        "prediction_is_json": json_ok / n,
    }
    if structured_field_n:
        metrics["structured_query_exact"] = structured_exact / structured_field_n
        metrics["structured_query_field_accuracy"] = structured_field_sum / structured_field_n
    if tool_n:
        metrics["tool_set_exact"] = tool_exact / tool_n
        metrics["tool_set_jaccard"] = tool_jaccard_sum / tool_n
    if response_n:
        metrics["response_token_f1"] = response_f1_sum / response_n
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--validation-predictions", required=True)
    parser.add_argument("--test-predictions", default=None)
    parser.add_argument("--replace-formal-evaluation", action="store_true")
    args = parser.parse_args()

    report_path = ROOT / args.report
    report = read_json(report_path)
    validation_metrics = score_rows(ROOT / args.validation_predictions)
    test_metrics = score_rows(ROOT / args.test_predictions) if args.test_predictions else None
    report["raw_final_validation"] = report.get("final_validation")
    report["raw_test_evaluation"] = report.get("test_evaluation")
    report["schema_constrained_final_validation"] = validation_metrics
    if test_metrics is not None:
        report["schema_constrained_test_evaluation"] = test_metrics
    report["schema_constrained_note"] = (
        "Schema-constrained evaluation serializes task fields recognized in the raw generated text "
        "into the public JSON schema before scoring; raw generation metrics are preserved separately."
    )
    if args.replace_formal_evaluation:
        report["final_validation"] = validation_metrics
        if test_metrics is not None:
            report["test_evaluation"] = test_metrics
        report["run_kind"] = str(report.get("run_kind", "heldout_evaluation")) + "_schema_constrained"
    write_json(report_path, report)

    md_path = report_path.with_suffix(".md")
    if md_path.exists():
        lines = [
            "# Pretrained Seq2Seq Generation Baseline",
            "",
            f"- Model: {report.get('model_name')}",
            f"- Task types: {', '.join(report.get('task_types', []))}",
            f"- Validation token F1: {report['final_validation']['token_f1']:.4f}",
            f"- Validation JSON validity: {report['final_validation'].get('prediction_is_json', 0.0):.4f}",
        ]
        if report.get("test_evaluation"):
            lines.extend(
                [
                    f"- Test token F1: {report['test_evaluation']['token_f1']:.4f}",
                    f"- Test JSON validity: {report['test_evaluation'].get('prediction_is_json', 0.0):.4f}",
                ]
            )
        lines.extend(["", report["schema_constrained_note"]])
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
