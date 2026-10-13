"""Audit suspiciously high model scores and label leakage risks."""

from __future__ import annotations

import argparse
import glob
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from create_classification_challenge_splits import challenge_group_key
from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json


CLASSIFICATION_TASKS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def label_for(row: dict[str, Any]) -> str:
    return str(row.get(CLASSIFICATION_TASKS[row["task_type"]]))


def group_purity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        groups[challenge_group_key(row)][label_for(row)] += 1
    total = sum(sum(counter.values()) for counter in groups.values()) or 1
    pure_records = 0
    weighted_purity = 0.0
    pure_group_count = 0
    for counter in groups.values():
        n = sum(counter.values())
        purity = max(counter.values()) / n
        weighted_purity += purity * n
        if purity == 1.0:
            pure_group_count += 1
            pure_records += n
    return {
        "groups": len(groups),
        "records": total,
        "weighted_group_purity": weighted_purity / total,
        "pure_group_count": pure_group_count,
        "pure_group_record_rate": pure_records / total,
    }


def split_group_overlap(train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]]) -> dict[str, Any]:
    train_groups = {challenge_group_key(row) for row in train_rows}
    test_groups = {challenge_group_key(row) for row in test_rows}
    overlap = train_groups & test_groups
    covered_test = sum(1 for row in test_rows if challenge_group_key(row) in overlap)
    return {
        "train_groups": len(train_groups),
        "test_groups": len(test_groups),
        "overlap_groups": len(overlap),
        "test_records_with_train_group_rate": covered_test / max(len(test_rows), 1),
    }


def read_report(path: str | Path) -> dict[str, Any] | None:
    path = ROOT / path if isinstance(path, str) else path
    if not path.exists():
        return None
    try:
        payload = read_json(path)
    except Exception:
        return None
    if isinstance(payload, dict):
        payload["_path"] = str(path.relative_to(ROOT))
        return payload
    return None


def report_score(report: dict[str, Any]) -> float | None:
    if "test_evaluation" in report and isinstance(report.get("test_evaluation"), dict):
        value = report["test_evaluation"].get("macro_f1")
        if value is not None:
            return float(value)
    if "test_macro_score" in report:
        return float(report["test_macro_score"])
    return None


def generation_scores(report: dict[str, Any]) -> dict[str, float | None]:
    test_eval = report.get("test_evaluation") if isinstance(report.get("test_evaluation"), dict) else {}
    return {
        "exact_match": float(test_eval["exact_match"]) if test_eval.get("exact_match") is not None else None,
        "token_f1": float(test_eval["token_f1"]) if test_eval.get("token_f1") is not None else None,
        "char_f1": float(test_eval["char_f1"]) if test_eval.get("char_f1") is not None else None,
        "selection_f1": float(test_eval["selection_f1"]) if test_eval.get("selection_f1") is not None else None,
        "prediction_is_json": (
            float(test_eval["prediction_is_json"]) if test_eval.get("prediction_is_json") is not None else None
        ),
        "structured_query_exact": (
            float(test_eval["structured_query_exact"])
            if test_eval.get("structured_query_exact") is not None
            else None
        ),
        "tool_set_exact": float(test_eval["tool_set_exact"]) if test_eval.get("tool_set_exact") is not None else None,
    }


def normalize_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return str(value)
    return " ".join(str(value or "").strip().split())


def input_proxy_diagnostics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_values: dict[str, Counter[str]] = defaultdict(Counter)
    field_label_counts: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for row in rows:
        label = label_for(row)
        for field, value in (row.get("input") or {}).items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                key = normalize_value(item)
                if not key:
                    continue
                field_values[field][key] += 1
                field_label_counts[field][key][label] += 1

    diagnostics = []
    total_rows = len(rows) or 1
    for field, value_counts in sorted(field_values.items()):
        covered = sum(value_counts.values())
        pure_records = 0
        repeated_records = 0
        pure_repeated_records = 0
        weighted_purity = 0.0
        pure_values = 0
        for value, count in value_counts.items():
            label_counter = field_label_counts[field][value]
            purity = max(label_counter.values()) / count
            weighted_purity += purity * count
            if count >= 3:
                repeated_records += count
                if purity == 1.0:
                    pure_repeated_records += count
            if purity == 1.0:
                pure_values += 1
                pure_records += count
        diagnostics.append(
            {
                "field": field,
                "distinct_values": len(value_counts),
                "record_coverage_rate": covered / total_rows,
                "mean_records_per_value": covered / max(len(value_counts), 1),
                "repeated_value_record_rate": repeated_records / max(covered, 1),
                "weighted_value_label_purity": weighted_purity / max(covered, 1),
                "pure_value_count": pure_values,
                "pure_value_record_rate": pure_records / max(covered, 1),
                "pure_repeated_value_record_rate": pure_repeated_records / max(repeated_records, 1),
            }
        )
    return diagnostics


def task_reports(pattern: str) -> list[dict[str, Any]]:
    reports = []
    for path in glob.glob(str(ROOT / pattern)):
        payload = read_report(Path(path))
        if payload:
            reports.append(payload)
    return sorted(reports, key=lambda item: item.get("_path", ""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--standard-report-glob", default="benchmark/v1.2_sd_core_transformer*_report.json")
    parser.add_argument("--challenge-report-glob", default="benchmark/v1.2_sd_core_challenge_transformer*_report.json")
    parser.add_argument("--strict-report-glob", default="benchmark/v1.2_sd_core_strict_transformer*_report.json")
    parser.add_argument("--generative-report-glob", default="benchmark/v1.2_sd_core_seq2seq*_report.json")
    parser.add_argument("--output-json", default="reports/model_score_quality_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/model_score_quality_audit_v1.2_sd_core.md")
    args = parser.parse_args()

    dataset_rows = read_jsonl(ROOT / args.dataset)
    train_rows_all = read_jsonl(ROOT / args.train) if (ROOT / args.train).exists() else []
    test_rows_all = read_jsonl(ROOT / args.test) if (ROOT / args.test).exists() else []
    by_task = {task: [row for row in dataset_rows if row.get("task_type") == task] for task in CLASSIFICATION_TASKS}
    train_by_task = {task: [row for row in train_rows_all if row.get("task_type") == task] for task in CLASSIFICATION_TASKS}
    test_by_task = {task: [row for row in test_rows_all if row.get("task_type") == task] for task in CLASSIFICATION_TASKS}

    label_audit = {}
    for task, rows in by_task.items():
        counts = Counter(label_for(row) for row in rows)
        proxy_fields = input_proxy_diagnostics(rows)
        label_audit[task] = {
            "label_counts": dict(counts),
            "rare_labels_below_5": {label: count for label, count in counts.items() if count < 5},
            "group_purity": group_purity(rows),
            "standard_split_group_overlap": split_group_overlap(train_by_task[task], test_by_task[task]),
            "input_proxy_fields": [
                item
                for item in proxy_fields
                if item["record_coverage_rate"] >= 0.5 and item["weighted_value_label_purity"] >= 0.95
                and item["repeated_value_record_rate"] >= 0.5
                and item["pure_repeated_value_record_rate"] >= 0.95
            ],
        }

    standard_reports = [
        report
        for report in task_reports(args.standard_report_glob)
        if 'proxy_stress' not in str(report.get('_path', '')) and 'challenge' not in str(report.get('_path', ''))
    ]
    challenge_reports = [
        report
        for report in task_reports(args.challenge_report_glob)
        if "challenge_tuned" not in str(report.get("_path", ""))
        and "proxy_stress" not in str(report.get("_path", ""))
        and "boundary_holdout" not in str(report.get("_path", ""))
    ]
    strict_reports = (
        [
            report
            for report in task_reports(args.strict_report_glob)
            if "proxy_stress" not in str(report.get("_path", ""))
            and "challenge" not in str(report.get("_path", ""))
        ]
        if args.strict_report_glob
        else []
    )
    generative_reports = task_reports(args.generative_report_glob)
    challenge_by_task: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for report in challenge_reports:
        challenge_by_task[report.get("task_type")].append(report)
    strict_by_task: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for report in strict_reports:
        strict_by_task[report.get("task_type")].append(report)

    model_audit = []
    for report in standard_reports:
        task = report.get("task_type")
        score = report_score(report)
        task_challenge_reports = challenge_by_task.get(task, [])
        challenge_scores = [
            {
                "path": challenge_report.get("_path"),
                "test_macro_f1": report_score(challenge_report),
            }
            for challenge_report in task_challenge_reports
        ]
        numeric_challenge_scores = [
            item["test_macro_f1"] for item in challenge_scores if item["test_macro_f1"] is not None
        ]
        challenge_score = min(numeric_challenge_scores) if numeric_challenge_scores else None
        challenge_best_score = max(numeric_challenge_scores) if numeric_challenge_scores else None
        strict_scores = [
            {
                "path": strict_report.get("_path"),
                "test_macro_f1": report_score(strict_report),
            }
            for strict_report in strict_by_task.get(task, [])
        ]
        numeric_strict_scores = [item["test_macro_f1"] for item in strict_scores if item["test_macro_f1"] is not None]
        strict_score = max(numeric_strict_scores) if numeric_strict_scores else None
        flags = []
        if score is not None and score >= 0.995:
            flags.append("near_perfect_standard_score")
        if challenge_score is not None and challenge_score >= 0.995:
            flags.append("near_perfect_challenge_score")
        if report.get("config", {}).get("use_rationale"):
            flags.append("rationale_input_can_leak_gold_reasoning")
        overlap_rate = label_audit.get(str(task), {}).get("standard_split_group_overlap", {}).get(
            "test_records_with_train_group_rate", 0.0
        )
        if overlap_rate >= 0.75:
            flags.append("standard_test_reuses_train_template_or_action_groups")
        group_purity_value = label_audit.get(str(task), {}).get("group_purity", {}).get("weighted_group_purity", 0.0)
        if group_purity_value >= 0.95:
            flags.append("challenge_groups_are_nearly_label_pure")
        if label_audit.get(str(task), {}).get("input_proxy_fields"):
            flags.append("input_fields_have_high_label_purity")
        if challenge_score is None:
            flags.append("missing_challenge_score")
        if strict_score is None:
            flags.append("missing_strict_source_group_score")
        elif score is not None and challenge_best_score is not None and score - challenge_best_score >= 0.05:
            flags.append("challenge_score_drop")
        model_audit.append(
            {
                "path": report.get("_path"),
                "task_type": task,
                "standard_test_macro_f1": score,
                "challenge_report": challenge_scores[0]["path"] if challenge_scores else None,
                "challenge_reports": challenge_scores,
                "challenge_test_macro_f1": challenge_score,
                "challenge_best_macro_f1": challenge_best_score,
                "strict_reports": strict_scores,
                "strict_test_macro_f1": strict_score,
                "flags": flags,
            }
        )

    generative_audit = []
    for report in generative_reports:
        scores = generation_scores(report)
        flags = []
        if scores["selection_f1"] is not None and scores["selection_f1"] >= 0.995:
            flags.append("near_perfect_generation_selection_f1")
        if scores["exact_match"] is not None and scores["exact_match"] >= 0.995:
            flags.append("near_perfect_generation_exact_match")
        if (
            scores["structured_query_exact"] is not None
            and scores["structured_query_exact"] >= 0.995
            and scores["exact_match"] is not None
            and scores["exact_match"] >= 0.995
        ):
            flags.append("deterministic_structured_query_task")
        generative_audit.append(
            {
                "path": report.get("_path"),
                "task_types": report.get("task_types"),
                "scores": scores,
                "flags": flags,
            }
        )

    overall_flags = sorted(
        {flag for item in model_audit for flag in item["flags"]}
        | {flag for item in generative_audit for flag in item["flags"]}
    )
    strict_reports_present = set(
        str(item.get("task_type")) for item in model_audit if item.get("strict_test_macro_f1") is not None
    ) >= set(CLASSIFICATION_TASKS)
    strict_reports_pass = all(
        item.get("strict_test_macro_f1") is not None and item.get("strict_test_macro_f1") >= 0.50
        for item in model_audit
    )
    standard_overlap_clean = all(
        audit.get("standard_split_group_overlap", {}).get("test_records_with_train_group_rate", 1.0) <= 0.05
        for audit in label_audit.values()
    )
    # A high strict-source score cannot erase standard-split surface reuse.
    # Keep overlap as an explicit warning and report strict results as a
    # complementary diagnostic rather than a gate that upgrades it.
    hard_gates = {
        "standard_reports_present_for_all_classification_tasks": set(
            str(item.get("task_type")) for item in model_audit if item.get("standard_test_macro_f1") is not None
        ) >= set(CLASSIFICATION_TASKS),
        "challenge_reports_present_for_all_classification_tasks": set(
            str(item.get("task_type")) for item in model_audit if item.get("challenge_best_macro_f1") is not None
        ) >= set(CLASSIFICATION_TASKS),
        "standard_split_group_overlap_clean": standard_overlap_clean,
        "challenge_best_macro_f1_at_least_0_50": all(
            item.get("challenge_best_macro_f1") is not None and item.get("challenge_best_macro_f1") >= 0.50
            for item in model_audit
        ),
        "strict_source_group_reports_present_for_all_classification_tasks": strict_reports_present,
        "strict_source_group_macro_f1_at_least_0_50": strict_reports_pass,
        "no_rationale_input_leakage_in_formal_reports": not any(
            "rationale_input_can_leak_gold_reasoning" in item.get("flags", []) for item in model_audit
        ),
    }
    verdict = "pass" if all(hard_gates.values()) else "warn"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "verdict": verdict,
        "label_audit": label_audit,
        "model_audit": model_audit,
        "generative_audit": generative_audit,
        "overall_flags": overall_flags,
        "hard_gates": hard_gates,
        "claim_guidance": [
            "Treat near-perfect standard split classifier scores as trainability checks, not as claims of solved dispatch reasoning.",
            "Report challenge-split scores alongside standard split scores when discussing classification models.",
            "Treat deterministic structured-query generation scores separately from open-ended language generation scores.",
            "Avoid using gold rationales as classifier inputs in formal baseline claims.",
        ],
    }
    write_json(ROOT / args.output_json, report)

    lines = [
        "# Model Score Quality Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Dataset: `{args.dataset}`",
        f"Verdict: `{verdict}`",
        "",
        "## Model Flags",
        "",
    ]
    for item in model_audit:
        lines.append(
            f"- `{item['path']}`: standard={item['standard_test_macro_f1']}, "
            f"challenge_min={item['challenge_test_macro_f1']}, challenge_best={item.get('challenge_best_macro_f1')}, "
            f"strict={item.get('strict_test_macro_f1')}, "
            f"flags={', '.join(item['flags']) if item['flags'] else 'none'}"
        )
    lines.extend(["", "## Hard Gates", ""])
    for name, ok in hard_gates.items():
        lines.append(f"- {name}: {'pass' if ok else 'fail'}")
    lines.extend(["", "## Generative Model Flags", ""])
    for item in generative_audit:
        lines.append(
            f"- `{item['path']}`: scores={item['scores']}, "
            f"flags={', '.join(item['flags']) if item['flags'] else 'none'}"
        )
    lines.extend(["", "## Label And Split Diagnostics", ""])
    for task, audit in label_audit.items():
        purity = audit["group_purity"]
        overlap = audit["standard_split_group_overlap"]
        lines.append(
            f"- {task}: labels={audit['label_counts']}; rare={audit['rare_labels_below_5'] or 'none'}; "
            f"group_purity={purity['weighted_group_purity']:.4f}; "
            f"standard_test_group_overlap={overlap['test_records_with_train_group_rate']:.4f}; "
            f"proxy_fields={len(audit['input_proxy_fields'])}"
        )
        for field in audit["input_proxy_fields"][:5]:
            lines.append(
                f"  - proxy field `{field['field']}`: coverage={field['record_coverage_rate']:.4f}, "
                f"repeated_value_rate={field['repeated_value_record_rate']:.4f}, "
                f"value_label_purity={field['weighted_value_label_purity']:.4f}, "
                f"pure_repeated_value_rate={field['pure_repeated_value_record_rate']:.4f}"
            )
    lines.extend(["", "## Claim Guidance", ""])
    lines.extend(f"- {item}" for item in report["claim_guidance"])
    (ROOT / args.output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
