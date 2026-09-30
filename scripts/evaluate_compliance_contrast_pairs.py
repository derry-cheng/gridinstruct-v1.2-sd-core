#!/usr/bin/env python3
"""Evaluate state-changing and wording-changing pairs in held-out compliance rows."""

from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETS = {
    "official_test": (
        "data/v1.2_sd_core_test_en.jsonl",
        "benchmark/direct_english_tfidf_v1.2_sd_core_leakage_fixed_test_predictions.jsonl",
    ),
    "official_ood": (
        "data/v1.2_sd_core_ood_test_en.jsonl",
        "benchmark/direct_english_tfidf_v1.2_sd_core_leakage_fixed_ood_predictions.jsonl",
    ),
    "surface_test": (
        "data/v1.2_sd_core_instruction_surface_balanced_test_en.jsonl",
        "benchmark/instruction_surface_balanced_v1.2_sd_core_leakage_fixed/tfidf_test_predictions.jsonl",
    ),
}


def jsonl(path: str):
    with (ROOT / path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def evaluate(data_path: str, predictions_path: str) -> dict:
    predictions = {
        row["id"]: row["prediction"]
        for row in jsonl(predictions_path)
        if row.get("task_type") == "regulation_compliance_check"
    }
    by_instruction = defaultdict(list)
    by_state_action = defaultdict(list)
    used = 0
    for row in jsonl(data_path):
        if row.get("task_type") != "regulation_compliance_check":
            continue
        row_id = row["id"]
        if row_id not in predictions:
            raise ValueError(f"prediction missing for {row_id}")
        item = (row["scenario_id"], row["compliance_label"], predictions[row_id], row["instruction"])
        by_instruction[row["instruction"]].append(item)
        context = row["input"]
        by_state_action[
            (
                row["scenario_id"],
                context["proposed_action"],
                tuple(context["observed_issues"]),
                row["compliance_label"],
            )
        ].append(item)
        used += 1

    state_pairs = state_correct = state_groups = 0
    for group in by_instruction.values():
        pairs = [
            (left, right)
            for left, right in combinations(group, 2)
            if left[0] != right[0] and left[1] != right[1]
        ]
        if pairs:
            state_groups += 1
            state_pairs += len(pairs)
            state_correct += sum(
                left[2] == left[1] and right[2] == right[1]
                for left, right in pairs
            )

    wording_pairs = wording_correct = wording_consistent = wording_groups = 0
    for group in by_state_action.values():
        pairs = [(left, right) for left, right in combinations(group, 2) if left[3] != right[3]]
        if pairs:
            wording_groups += 1
            wording_pairs += len(pairs)
            wording_consistent += sum(left[2] == right[2] for left, right in pairs)
            wording_correct += sum(
                left[2] == left[1] and right[2] == right[1]
                for left, right in pairs
            )

    return {
        "compliance_rows": used,
        "state_change": {
            "definition": "identical instruction; distinct scenario; different gold label",
            "groups": state_groups,
            "pairs": state_pairs,
            "both_predictions_correct": state_correct,
            "both_correct_rate": round(state_correct / state_pairs, 6) if state_pairs else None,
        },
        "wording_change": {
            "definition": "same scenario, proposed action, observed issues, and gold label; distinct instruction; operator goal may vary",
            "groups": wording_groups,
            "pairs": wording_pairs,
            "prediction_agreement": wording_consistent,
            "agreement_rate": round(wording_consistent / wording_pairs, 6) if wording_pairs else None,
            "both_predictions_correct": wording_correct,
            "both_correct_rate": round(wording_correct / wording_pairs, 6) if wording_pairs else None,
        },
    }


def main() -> None:
    report = {
        "status": "pass",
        "model": "previously fitted TF-IDF linear-SVC predictions; no retraining",
        "pair_policy": "all unordered pairs within each exact-match grouping; pair counts are not independent samples",
        "splits": {name: evaluate(*paths) for name, paths in SETS.items()},
    }
    output = ROOT / "reports/compliance_contrast_pairs_v1.2_sd_core.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
