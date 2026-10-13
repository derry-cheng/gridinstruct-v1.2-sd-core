"""Recompute seq2seq prediction metrics after metric-definition updates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gridinstruct_utils import ROOT, read_json, write_json
from run_seq2seq_generation_baseline import token_f1


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def recompute(path: Path) -> dict:
    rows = read_jsonl(path)
    exact = 0
    f1_sum = 0.0
    for row in rows:
        gold = str(row.get("gold", "")).strip()
        pred = str(row.get("prediction", "")).strip()
        item_f1 = token_f1(gold, pred)
        is_exact = gold == pred
        row["exact_match"] = is_exact
        row["token_f1"] = item_f1
        exact += int(is_exact)
        f1_sum += item_f1
    write_jsonl(path, rows)
    n = max(len(rows), 1)
    return {"exact_match": exact / n, "token_f1": f1_sum / n, "n": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--validation-predictions", required=True)
    parser.add_argument("--test-predictions", default=None)
    args = parser.parse_args()

    report_path = ROOT / args.report
    report = read_json(report_path)
    report["metric_definition"] = "whitespace-normalized character/token F1 for natural-language and structured generation outputs"
    report["final_validation"] = recompute(ROOT / args.validation_predictions)
    report["best_validation"] = report["final_validation"]
    if args.test_predictions:
        test_path = ROOT / args.test_predictions
        if test_path.exists():
            report["test_evaluation"] = recompute(test_path)
    write_json(report_path, report)

    md_path = report_path.with_suffix(".md")
    if md_path.exists():
        task_types = ", ".join(report.get("task_types", []))
        lines = [
            "# Pretrained Seq2Seq Generation Baseline",
            "",
            f"- Model: {report.get('model_name')}",
            f"- Device: {report.get('device')}",
            f"- Task types: {task_types}",
            f"- Train records: {report.get('train_records')}",
            f"- Validation records: {report.get('validation_records')}",
            f"- Best epoch: {report.get('best_epoch')}",
            f"- Validation exact match: {report['final_validation']['exact_match']:.4f}",
            f"- Validation token F1: {report['final_validation']['token_f1']:.4f}",
        ]
        if report.get("test_evaluation"):
            lines.extend(
                [
                    f"- Test split: {report.get('test')}",
                    f"- Test records: {report.get('test_records')}",
                    f"- Test exact match: {report['test_evaluation']['exact_match']:.4f}",
                    f"- Test token F1: {report['test_evaluation']['token_f1']:.4f}",
                ]
            )
        lines.extend(["", str(report.get("note", "")), "", f"Metric: {report['metric_definition']}"])
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
