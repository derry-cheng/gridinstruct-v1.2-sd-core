"""Run GridInstruct API baselines on test and OOD splits.

This script supports zero-shot and few-shot prompting against the configured
LLM API endpoint. Evaluation is always computed against dataset ground truth,
never another model's output.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, stable_shuffle, write_json
from llm_client import chat_completion


LABEL_TASKS = {"operation_ticket_check", "regulation_compliance_check"}
INTENT_TASKS = {"dispatcher_intent_tool_call"}
QUERY_TASKS = {"intelligent_data_query"}


def normalize_text(text: object) -> str:
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False, sort_keys=True)
    text = str(text).lower().strip()
    return re.sub(r"\s+", " ", text)


def safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def normalize_label_value(value: object) -> str:
    return "__missing__" if value is None else str(value)


def macro_f1(golds: list[str], preds: list[str]) -> float:
    gold_values = [normalize_label_value(value) for value in golds]
    pred_values = [normalize_label_value(value) for value in preds]
    labels = sorted(set(gold_values) | set(pred_values))
    f1s = []
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(gold_values, pred_values))
        fp = sum(g != label and p == label for g, p in zip(gold_values, pred_values))
        fn = sum(g == label and p != label for g, p in zip(gold_values, pred_values))
        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        f1s.append(safe_div(2 * precision * recall, precision + recall))
    return sum(f1s) / len(f1s) if f1s else 0.0


def balanced_accuracy(golds: list[str], preds: list[str]) -> float:
    labels = sorted(set(golds))
    recalls = []
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(golds, preds))
        fn = sum(g == label and p != label for g, p in zip(golds, preds))
        recalls.append(safe_div(tp, tp + fn))
    return sum(recalls) / len(recalls) if recalls else 0.0


def token_f1(gold: object, pred: object) -> float:
    gold_tokens = normalize_text(gold).split()
    pred_tokens = normalize_text(pred).split()
    if not gold_tokens and not pred_tokens:
        return 1.0
    if not gold_tokens or not pred_tokens:
        return 0.0
    gold_counts = Counter(gold_tokens)
    pred_counts = Counter(pred_tokens)
    overlap = sum((gold_counts & pred_counts).values())
    precision = safe_div(overlap, len(pred_tokens))
    recall = safe_div(overlap, len(gold_tokens))
    return safe_div(2 * precision * recall, precision + recall)


def slot_f1(gold_slots: dict[str, Any], pred_slots: dict[str, Any]) -> float:
    gold_items = {(key, normalize_text(value)) for key, value in gold_slots.items()}
    pred_items = {(key, normalize_text(value)) for key, value in pred_slots.items()}
    tp = len(gold_items & pred_items)
    precision = safe_div(tp, len(pred_items))
    recall = safe_div(tp, len(gold_items))
    return safe_div(2 * precision * recall, precision + recall)


def parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def canonical_label(text: str) -> str | None:
    text_n = normalize_text(text)
    if "合规但需继续监视" in text or "compliant_with_monitoring" in text_n:
        return "compliant_with_monitoring"
    if "违规" in text or "non_compliant" in text_n:
        return "non_compliant"
    if "合规" in text or "compliant" in text_n:
        return "compliant"
    return None


def expected_label(row: dict[str, Any]) -> str | None:
    if row["task_type"] in LABEL_TASKS:
        return row.get("compliance_label")
    if row["task_type"] in INTENT_TASKS:
        return row.get("intent")
    return None


def render_io(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def task_instruction(row: dict[str, Any]) -> str:
    task = row["task_type"]
    if task in LABEL_TASKS:
        return "只输出一个标签，不要解释：合规、违规、合规但需继续监视。"
    if task == "dispatcher_intent_tool_call":
        return (
            "输出 JSON，对应格式为 "
            '{"intent":"...", "slots":{"priority":"...", "scenario_id":"..."}, "tools":["...", "..."]}。'
        )
    if task == "intelligent_data_query":
        return (
            '输出 JSON：{"structured_query":{"source":"simulation_outputs","filter":"...",'
            '"scenario_id":"..."}}。过载查询还必须输出 contract_version、'
            "equipment_scope（line、transformer 或 all）和 aggregation。"
        )
    if task == "auxiliary_decision":
        return '输出 JSON：{"response":"...", "tools":["...", "..."]}。'
    return "请简洁回答，不要输出多余说明。"


def exemplar_block(row: dict[str, Any]) -> str:
    task = row["task_type"]
    if task in LABEL_TASKS:
        answer = row["output"]
    elif task == "dispatcher_intent_tool_call":
        answer = {
            "intent": row.get("intent"),
            "slots": row.get("slots", {}),
            "tools": [step["tool"] for step in row.get("tool_plan", [])],
        }
    elif task == "intelligent_data_query":
        answer = {"structured_query": row.get("structured_query", {})}
    elif task == "auxiliary_decision":
        answer = {
            "response": row.get("output"),
            "tools": [step["tool"] for step in row.get("tool_plan", [])],
        }
    else:
        answer = row["output"]
    return (
        f"任务类型：{row['task_type']}\n"
        f"指令：{row['instruction']}\n"
        f"输入：{render_io(row['input'])}\n"
        f"标准回答：{render_io(answer)}"
    )


def prompt_for(row: dict[str, Any], mode: str, exemplars: list[dict[str, Any]]) -> str:
    sections = [
        "你是电力调度数据集 benchmark 的被评测模型。",
        task_instruction(row),
    ]
    if mode == "few_shot" and exemplars:
        sections.append("下面是同任务示例：")
        sections.extend(exemplar_block(example) for example in exemplars)
    sections.append(
        "\n".join(
            [
                "现在请回答当前样本：",
                f"任务类型：{row['task_type']}",
                f"指令：{row['instruction']}",
                f"输入：{render_io(row['input'])}",
                "回答：",
            ]
        )
    )
    return "\n\n".join(sections)


def select_exemplars(
    train_rows_by_task: dict[str, list[dict[str, Any]]],
    row: dict[str, Any],
    few_shot_k: int,
) -> list[dict[str, Any]]:
    examples = []
    for candidate in train_rows_by_task.get(row["task_type"], []):
        if candidate["id"] == row["id"]:
            continue
        examples.append(candidate)
        if len(examples) >= few_shot_k:
            break
    return examples


def predicted_tool_names(pred_text: str, parsed: dict[str, Any] | None) -> list[str]:
    if isinstance(parsed, dict) and isinstance(parsed.get("tools"), list):
        return [str(item) for item in parsed["tools"]]
    tool_names = []
    for tool in [
        "get_violations",
        "suggest_corrective_actions",
        "run_power_flow",
        "run_opf_redispatch",
        "query_simulation_records",
    ]:
        if tool in pred_text:
            tool_names.append(tool)
    return tool_names


def score_row(row: dict[str, Any], pred_text: str) -> dict[str, Any]:
    task = row["task_type"]
    parsed = parse_json_object(pred_text)
    pred_text_n = normalize_text(pred_text)

    if task in LABEL_TASKS:
        label = None
        if isinstance(parsed, dict):
            label = canonical_label(str(parsed.get("label", "")))
        label = label or canonical_label(pred_text)
        gold = row.get("compliance_label")
        return {
            "primary_metric": "accuracy",
            "primary_value": float(label == gold),
            "gold": {"compliance_label": gold},
            "prediction_structured": {"compliance_label": label},
            "correct": label == gold,
        }

    if task == "dispatcher_intent_tool_call":
        pred_intent = None
        pred_slots: dict[str, Any] = {}
        if isinstance(parsed, dict):
            pred_intent = parsed.get("intent")
            if isinstance(parsed.get("slots"), dict):
                pred_slots = parsed["slots"]
        pred_intent = pred_intent or next(
            (intent for intent in [row.get("intent")] if intent and intent.lower() in pred_text_n),
            None,
        )
        slot_score = slot_f1(row.get("slots", {}), pred_slots)
        gold_tools = [step["tool"] for step in row.get("tool_plan", [])]
        pred_tools = predicted_tool_names(pred_text, parsed)
        tool_acc = float(pred_tools == gold_tools)
        return {
            "primary_metric": "intent_accuracy",
            "primary_value": float(pred_intent == row.get("intent")),
            "slot_f1": slot_score,
            "tool_selection_accuracy": tool_acc,
            "gold": {
                "intent": row.get("intent"),
                "slots": row.get("slots", {}),
                "tools": gold_tools,
            },
            "prediction_structured": {
                "intent": pred_intent,
                "slots": pred_slots,
                "tools": pred_tools,
            },
            "correct": pred_intent == row.get("intent"),
        }

    if task == "intelligent_data_query":
        structured_query = {}
        if isinstance(parsed, dict) and isinstance(parsed.get("structured_query"), dict):
            structured_query = parsed["structured_query"]
        else:
            structured_query = {
                "source": "simulation_outputs" if "simulation_outputs" in pred_text_n else None,
                "filter": row.get("structured_query", {}).get("filter")
                if normalize_text(row.get("structured_query", {}).get("filter", "")) in pred_text_n
                else None,
                "scenario_id": row.get("scenario_id") if row.get("scenario_id", "") in pred_text else None,
            }
        gold = row.get("structured_query", {})
        filter_match = normalize_text(structured_query.get("filter", "")) == normalize_text(gold.get("filter", ""))
        scope_match = structured_query.get("equipment_scope") == gold.get("equipment_scope")
        exact = structured_query == gold
        return {
            "primary_metric": "structured_query_accuracy",
            "primary_value": float(exact),
            "filter_exact_match": float(filter_match),
            "equipment_scope_exact_match": float(scope_match),
            "gold": {"structured_query": gold},
            "prediction_structured": {"structured_query": structured_query},
            "correct": exact,
        }

    if task == "auxiliary_decision":
        gold_tools = [step["tool"] for step in row.get("tool_plan", [])]
        pred_tools = predicted_tool_names(pred_text, parsed)
        contains_reference = normalize_text(row.get("output", "")) in pred_text_n
        return {
            "primary_metric": "tool_selection_accuracy",
            "primary_value": float(pred_tools == gold_tools),
            "contains_reference": float(contains_reference),
            "gold": {"response": row.get("output"), "tools": gold_tools},
            "prediction_structured": {"response": pred_text, "tools": pred_tools},
            "correct": pred_tools == gold_tools,
        }

    gold_text = row.get("output", "")
    exact = normalize_text(gold_text) == pred_text_n
    f1 = token_f1(gold_text, pred_text)
    return {
        "primary_metric": "token_f1",
        "primary_value": f1,
        "exact_match": float(exact),
        "gold": gold_text,
        "prediction_structured": pred_text,
        "correct": exact,
    }


def summarize_predictions(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        grouped[(row["mode"], row["split"], row["task_type"])].append(row)

    results = []
    for (mode, split, task_type), rows in sorted(grouped.items()):
        primary_metric = rows[0]["metric_name"]
        primary_values = [float(row["metric_value"]) for row in rows]
        result = {
            "mode": mode,
            "split": split,
            "task_type": task_type,
            "primary_metric": primary_metric,
            "primary_value": sum(primary_values) / len(primary_values),
            "n": len(rows),
        }

        if task_type in LABEL_TASKS:
            golds = [row["gold"]["compliance_label"] for row in rows]
            preds = [row["prediction_structured"].get("compliance_label") for row in rows]
            result["macro_f1"] = macro_f1(golds, preds)
            result["balanced_accuracy"] = balanced_accuracy(golds, preds)
        elif task_type == "dispatcher_intent_tool_call":
            result["slot_f1"] = sum(float(row.get("slot_f1", 0.0)) for row in rows) / len(rows)
            result["tool_selection_accuracy"] = sum(float(row.get("tool_selection_accuracy", 0.0)) for row in rows) / len(rows)
        elif task_type == "intelligent_data_query":
            result["filter_exact_match"] = sum(float(row.get("filter_exact_match", 0.0)) for row in rows) / len(rows)
        elif task_type == "auxiliary_decision":
            result["contains_reference"] = sum(float(row.get("contains_reference", 0.0)) for row in rows) / len(rows)
        else:
            result["exact_match"] = sum(float(row.get("exact_match", 0.0)) for row in rows) / len(rows)

        results.append(result)
    return results


def limit_rows(
    rows: list[dict[str, Any]],
    limit: int,
    limit_per_task: int,
    seed: int,
) -> list[dict[str, Any]]:
    if limit_per_task > 0:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["task_type"]].append(row)
        selected = []
        for offset, task_type in enumerate(sorted(grouped)):
            shuffled = stable_shuffle(grouped[task_type], seed + offset)
            selected.extend(shuffled[:limit_per_task])
        return stable_shuffle(selected, seed + 999)
    if limit > 0:
        return stable_shuffle(rows, seed)[:limit]
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--splits", nargs="+", default=["data/test.jsonl"])
    parser.add_argument("--train-split", default="data/train.jsonl")
    parser.add_argument("--modes", nargs="+", default=["zero_shot"])
    parser.add_argument("--few-shot-k", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--limit-per-task", type=int, default=0)
    parser.add_argument("--task-types", nargs="*", default=[])
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--request-timeout", type=float, default=45.0)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--output-prefix", default="benchmark/api_baseline")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    ensure_dirs(ROOT / "benchmark", ROOT / "reports")
    task_filter = set(args.task_types)
    train_rows = read_jsonl(ROOT / args.train_split)
    train_rows_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train_rows:
        train_rows_by_task[row["task_type"]].append(row)

    details: list[dict[str, Any]] = []
    model_counts = Counter()
    run_counts = Counter()
    total_requests = 0

    pending_rows: list[tuple[str, str, dict[str, Any]]] = []

    for split in args.splits:
        split_rows = read_jsonl(ROOT / split)
        if task_filter:
            split_rows = [row for row in split_rows if row["task_type"] in task_filter]
        split_rows = limit_rows(split_rows, args.limit, args.limit_per_task, args.seed)
        for mode in args.modes:
            for row in split_rows:
                pending_rows.append((split, mode, row))

    total_requests = len(pending_rows)
    completed = 0

    for split, mode, row in pending_rows:
        exemplars = select_exemplars(train_rows_by_task, row, args.few_shot_k) if mode == "few_shot" else []
        prompt = prompt_for(row, mode, exemplars)
        error_text = None
        try:
            prediction, model_used = chat_completion(
                [{"role": "user", "content": prompt}],
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                timeout=args.request_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - benchmark should continue past provider failures.
            prediction = ""
            model_used = "llm_call_failed"
            error_text = str(exc)
        score = score_row(row, prediction)
        model_counts[model_used] += 1
        run_counts[(mode, split, model_used)] += 1
        details.append(
            {
                "id": row["id"],
                "split": split,
                "mode": mode,
                "task_type": row["task_type"],
                "model": model_used,
                "prediction_raw": prediction,
                "gold": score["gold"],
                "prediction_structured": score["prediction_structured"],
                "metric_name": score["primary_metric"],
                "metric_value": score["primary_value"],
                "correct": score["correct"],
                "slot_f1": score.get("slot_f1"),
                "tool_selection_accuracy": score.get("tool_selection_accuracy"),
                "filter_exact_match": score.get("filter_exact_match"),
                "exact_match": score.get("exact_match"),
                "contains_reference": score.get("contains_reference"),
                "error": error_text,
            }
        )
        completed += 1
        if args.progress_every > 0 and (completed % args.progress_every == 0 or completed == total_requests):
            print(f"[progress] {completed}/{total_requests} requests complete", flush=True)

    results = summarize_predictions(details)
    prefix = ROOT / args.output_prefix
    predictions_path = Path(f"{prefix}_predictions.jsonl")
    csv_path = Path(f"{prefix}_results.csv")
    write_jsonl(predictions_path, details)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "mode",
            "split",
            "task_type",
            "primary_metric",
            "primary_value",
            "macro_f1",
            "balanced_accuracy",
            "slot_f1",
            "tool_selection_accuracy",
            "filter_exact_match",
            "exact_match",
            "contains_reference",
            "n",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    legacy_csv_path = ROOT / "benchmark/baseline_results.csv"
    if csv_path != legacy_csv_path:
        legacy_csv_path.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "split": ",".join(args.splits),
        "splits": args.splits,
        "train_split": args.train_split,
        "modes": args.modes,
        "few_shot_k": args.few_shot_k,
        "limit": args.limit,
        "limit_per_task": args.limit_per_task,
        "seed": args.seed,
        "model_counts": dict(model_counts),
        "run_counts": [
            {"mode": mode, "split": split, "model": model, "n": n}
            for (mode, split, model), n in sorted(run_counts.items())
        ],
        "predictions_path": str(predictions_path.relative_to(ROOT)),
        "results_path": str(csv_path.relative_to(ROOT)),
        "results": results,
    }
    write_json(Path(f"{prefix}_report.json"), report)
    write_json(ROOT / "reports/baseline_report.json", report)


if __name__ == "__main__":
    main()
