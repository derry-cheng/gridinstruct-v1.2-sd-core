#!/usr/bin/env python3
"""Report external-only and scenario-specific OOD metrics without mixing strata."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sklearn.metrics import f1_score

from gridinstruct_utils import ROOT, read_jsonl, write_json


EXTERNAL_NETWORKS = {"IEEE300", "ILLINOIS200", "PEGASE89", "PEGASE1354", "RTE1888", "RTE2848", "PEGASE2869"}
CLASSIFICATION_TASKS = {"operation_ticket_check", "regulation_compliance_check", "dispatcher_intent_tool_call"}


def strata(row: dict[str, Any]) -> set[str]:
    network = str(row.get("network_model") or "")
    scenario = str(row.get("scenario_id") or "")
    values = set()
    if network in EXTERNAL_NETWORKS:
        values.update({"external_only", f"external_network::{network}"})
    if network == "IEEE118":
        values.add("ieee118_only")
    if any(token in scenario for token in ("load120", "load130")):
        values.add("high_load_only")
    if "n2_branch_outage" in scenario:
        values.add("n2_only")
    if any(token in scenario for token in ("n1_transformer_outage", "n1_generator_outage")):
        values.add("equipment_contingency_only")
    return values


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row["task_type"])].append(row)
    task_metrics = {}
    for task, task_rows in sorted(by_task.items()):
        if task in CLASSIFICATION_TASKS:
            task_metrics[task] = {
                "n": len(task_rows),
                "accuracy": sum(bool(row.get("correct")) for row in task_rows) / len(task_rows),
                "macro_f1": float(f1_score([row["gold"] for row in task_rows], [row["prediction"] for row in task_rows], average="macro")),
            }
        else:
            task_metrics[task] = {
                "n": len(task_rows),
                "exact_match": sum(bool(row.get("exact_match")) for row in task_rows) / len(task_rows),
                "token_f1": sum(float(row.get("token_f1") or 0.0) for row in task_rows) / len(task_rows),
            }
    return {"records": len(rows), "task_metrics": task_metrics}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ood", default="data/v1.2_sd_core_ood_test_en.jsonl")
    parser.add_argument("--predictions", default="benchmark/v1.2_sd_core_tfidf_ood_predictions.jsonl")
    parser.add_argument("--output", default="reports/ood_stratified_metrics_v1.2_sd_core.json")
    args = parser.parse_args()

    data = {row["id"]: row for row in read_jsonl(ROOT / args.ood)}
    predictions = read_jsonl(ROOT / args.predictions)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing = []
    prediction_ids = []
    for prediction in predictions:
        prediction_ids.append(str(prediction.get("id") or ""))
        source = data.get(prediction["id"])
        if source is None:
            missing.append(prediction["id"])
            continue
        enriched = {**prediction, "task_type": source["task_type"]}
        for name in strata(source):
            grouped[name].append(enriched)
    expected_ids = {str(value) for value in data}
    observed_ids = set(prediction_ids)
    duplicate_prediction_id_count = len(prediction_ids) - len(observed_ids)
    missing_expected_ids = sorted(expected_ids - observed_ids)
    unexpected_prediction_ids = sorted(observed_ids - expected_ids)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if (
            not missing
            and grouped.get("external_only")
            and not missing_expected_ids
            and not unexpected_prediction_ids
            and duplicate_prediction_id_count == 0
        ) else "fail",
        "ood": args.ood,
        "predictions": args.predictions,
        "missing_prediction_ids": missing[:50],
        "missing_prediction_id_count": len(missing),
        "expected_prediction_id_count": len(expected_ids),
        "observed_prediction_id_count": len(observed_ids),
        "missing_expected_id_count": len(missing_expected_ids),
        "unexpected_prediction_id_count": len(unexpected_prediction_ids),
        "duplicate_prediction_id_count": duplicate_prediction_id_count,
        "strata": {name: summarize(rows) for name, rows in sorted(grouped.items())},
        "interpretation": "external topology, IEEE118, high-load, N-2, and equipment-contingency results are reported separately; prediction IDs must exactly cover the OOD file",
    }
    write_json(ROOT / args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
