"""Audit GridInstruct progress against internal data, model, and paper gates."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_json, read_jsonl, summarize_records, write_json


SCENARIO_TASKS = {
    "regulation_compliance_check",
    "auxiliary_decision",
    "dispatcher_intent_tool_call",
    "intelligent_data_query",
}
FLAT_TASKS = {"operation_ticket_check", "regulation_qa"}
TRANSFORMER_TASKS = {
    "operation_ticket_check",
    "regulation_compliance_check",
    "dispatcher_intent_tool_call",
}
GENERATIVE_TASKS = {
    "regulation_qa",
    "auxiliary_decision",
    "intelligent_data_query",
}
FORMAL_GENERATIVE_OBJECTIVES = {"pretrained_seq2seq_generation"}
FINALIZED_REVIEW_STATUSES = {
    "llm_dual_review_accepted",
    "llm_adjudicated_revised",
    "review_finalized",
}
RELEASE_FACING_FILES = [
    "docs/DATA_RECORDS.md",
    "docs/TECHNICAL_VALIDATION.md",
    "docs/SCIENTIFIC_DATA_DESCRIPTOR_DRAFT.md",
    "docs/PAPER_OUTLINE_ALIGNMENT.md",
    "docs/EXPERT_REVIEW_PROTOCOL.md",
    "docs/USAGE_NOTES.md",
    "docs/AVAILABILITY_AND_LIMITATIONS.md",
    "docs/ANNOTATION_POLICY.md",
    "docs/SCIENTIFIC_DATA_READINESS_CHECKLIST.md",
    "docs/DATA_DESCRIPTOR_AUDIT.md",
    "metadata/dataset_metadata.json",
    "metadata/annotation_policy.json",
    "metadata/expert_review_protocol.json",
    "metadata/paper_outline_alignment.json",
    "metadata/data_descriptor_audit.json",
    "metadata/split_integrity_report.json",
    "metadata/file_manifest.csv",
    "metadata/checksums_sha256.txt",
    "metadata/source_traceability.csv",
    "reports/release_metadata_snapshot.json",
]


def has_public_rule_source(rule: dict[str, Any]) -> bool:
    source_title = str(rule.get("source_title", "")).strip()
    source_url = str(rule.get("source_url", "")).strip()
    return bool(source_title and source_url and source_url.startswith(("http://", "https://")))


def safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return read_json(path)


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def scenario_overlap(eval_report: dict[str, Any] | None) -> dict[str, int]:
    if not eval_report:
        return {"train_test": -1, "train_validation": -1, "validation_test": -1}
    split_groups = (eval_report.get("overlap_report") or {}).get("split_group_key") or {}
    if split_groups:
        return {
            "train_test": int((split_groups.get("train__test") or {}).get("count", -1)),
            "train_validation": int((split_groups.get("train__validation") or {}).get("count", -1)),
            "validation_test": int((split_groups.get("validation__test") or {}).get("count", -1)),
        }
    train_test = len(eval_report.get("scenario_overlap_train_test", []))
    split_paths = eval_report.get("split_paths", {})
    train_path = ROOT / split_paths.get("train", "")
    validation_path = ROOT / split_paths.get("validation", "")
    test_path = ROOT / split_paths.get("test", "")
    train_rows = read_jsonl(train_path) if train_path.exists() else []
    validation_rows = read_jsonl(validation_path) if validation_path.exists() else []
    test_rows = read_jsonl(test_path) if test_path.exists() else []
    train_ids = {row.get("scenario_id") for row in train_rows if row.get("scenario_id")}
    validation_ids = {row.get("scenario_id") for row in validation_rows if row.get("scenario_id")}
    test_ids = {row.get("scenario_id") for row in test_rows if row.get("scenario_id")}
    return {
        "train_test": train_test,
        "train_validation": len(train_ids & validation_ids),
        "validation_test": len(validation_ids & test_ids),
    }


def per_task_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(row.get("task_type") for row in rows))


def review_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        Counter(str(row.get("metadata", {}).get("validation_status", "")).strip() or "missing" for row in rows)
    )


def finalized_review_by_task(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        status = str(row.get("metadata", {}).get("validation_status", "")).strip()
        if status in FINALIZED_REVIEW_STATUSES:
            counter[row.get("task_type")] += 1
    return dict(counter)


def collect_stale_release_files(expected_version: str, stale_tokens: list[str]) -> list[str]:
    stale = []
    for rel in RELEASE_FACING_FILES:
        text = load_text(ROOT / rel)
        if not text:
            continue
        if any(token in text for token in stale_tokens) and expected_version not in text:
            stale.append(rel)
    return stale


def summarize_model_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    summary = {
        "objective": report.get("objective"),
        "train": report.get("train"),
        "generated_at": report.get("generated_at"),
    }
    if "test_macro_score" in report:
        summary["test_macro_score"] = report.get("test_macro_score")
        summary["ood_macro_score"] = report.get("ood_macro_score")
    if "test_evaluation" in report:
        summary["test_evaluation"] = report.get("test_evaluation")
    if "best_validation" in report:
        summary["best_validation"] = report.get("best_validation")
    if "task_type" in report:
        summary["task_type"] = report.get("task_type")
    if "task_types" in report:
        summary["task_types"] = report.get("task_types")
    return summary


def summarize_generative_structured_metrics(report: dict[str, Any]) -> dict[str, Any]:
    metric_source = report.get("test_evaluation") or report.get("final_validation") or {}
    structured_keys = (
        "prediction_is_json",
        "structured_query_exact",
        "structured_query_field_accuracy",
        "tool_set_exact",
        "tool_set_jaccard",
    )
    return {key: metric_source[key] for key in structured_keys if key in metric_source}


def load_globbed_reports(pattern: str) -> list[dict[str, Any]]:
    reports = []
    for path in sorted(ROOT.glob(pattern)):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["_path"] = str(path.relative_to(ROOT))
            reports.append(payload)
    return reports


def is_formal_transformer_report(report: dict[str, Any]) -> bool:
    path = str(report.get("_path", ""))
    excluded = ("proxy_stress", "challenge", "tuned", "boundary_holdout")
    return bool(report.get("task_type")) and not any(token in path for token in excluded)


def pass_state(ok: bool) -> str:
    return "pass" if ok else "fail"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus9.jsonl")
    parser.add_argument("--validation-report", default="reports/data_validation_v1.2_plus9.json")
    parser.add_argument("--data-quality-report", default="reports/data_quality_optimization_v1.2_plus9.json")
    parser.add_argument("--review-report", default="reports/llm_review_report_v1.2_paper_plus7_cumulative.json")
    parser.add_argument("--evaluation-report", default="benchmark/v1.2_paper_plus9_evaluation_tasks.json")
    parser.add_argument("--tfidf-report", default="benchmark/v1.2_paper_plus9_tfidf_report.json")
    parser.add_argument("--transformer-report", default="reports/transformer_classifier_latest_report.json")
    parser.add_argument("--transformer-report-glob", default="benchmark/v1.2_paper_plus9_transformer*_report.json")
    parser.add_argument("--generative-report", default="benchmark/v1.2_paper_plus9_seq2seq_regqa_harmonized_e2_report.json")
    parser.add_argument("--generative-report-glob", default="benchmark/v1.2_paper_plus9_seq2seq_*_report.json")
    parser.add_argument("--structured-normalization-report", default="reports/structured_prediction_normalization_v1.2_sd_core.json")
    parser.add_argument("--split-independence-report", default="reports/split_independence_audit_v1.2_sd_core.json")
    parser.add_argument("--dataset-metadata", default="metadata/dataset_metadata.json")
    parser.add_argument("--rules", default="rules/regulation_rules.json")
    parser.add_argument("--output-json", default="reports/project_milestone_audit_v1.2_paper_plus9.json")
    parser.add_argument("--output-md", default="reports/project_milestone_audit_v1.2_paper_plus9.md")
    parser.add_argument("--expected-version", default="1.2")
    parser.add_argument("--min-total-records", type=int, default=60000)
    parser.add_argument("--min-flat-task-records", type=int, default=4000)
    parser.add_argument("--min-scenario-task-records", type=int, default=15000)
    parser.add_argument("--max-near-duplicate-rate", type=float, default=0.02)
    parser.add_argument("--min-finalized-review-per-task", type=int, default=50)
    parser.add_argument("--min-review-agreement-rate", type=float, default=0.75)
    parser.add_argument("--min-transformer-macro-f1", type=float, default=0.50)
    parser.add_argument("--min-generative-token-f1", type=float, default=0.60)
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.dataset)
    validation_report = safe_read_json(ROOT / args.validation_report) or {}
    data_quality_report = safe_read_json(ROOT / args.data_quality_report) or {}
    review_report = safe_read_json(ROOT / args.review_report) or {}
    evaluation_report = safe_read_json(ROOT / args.evaluation_report) or {}
    tfidf_report = safe_read_json(ROOT / args.tfidf_report)
    transformer_report = safe_read_json(ROOT / args.transformer_report)
    transformer_suite_reports = load_globbed_reports(args.transformer_report_glob)
    generative_report = safe_read_json(ROOT / args.generative_report)
    generative_suite_reports = load_globbed_reports(args.generative_report_glob) if args.generative_report_glob else []
    structured_normalization_report = safe_read_json(ROOT / args.structured_normalization_report) or {}
    split_independence_report = safe_read_json(ROOT / args.split_independence_report) or {}
    if not generative_suite_reports and generative_report:
        fallback_report = dict(generative_report)
        fallback_report["_path"] = args.generative_report
        generative_suite_reports = [fallback_report]
    dataset_metadata = safe_read_json(ROOT / args.dataset_metadata) or {}
    rules = safe_read_json(ROOT / args.rules) or []

    summary = summarize_records(rows)
    task_counts = per_task_counts(rows)
    status_counts = review_status_counts(rows)
    finalized_by_task = finalized_review_by_task(rows)
    overlap = scenario_overlap(evaluation_report)
    agreement = float(review_report.get("agreement", {}).get("overall_agreement_rate", 0.0))
    reviewed_records = int(review_report.get("reviewed_records", 0))
    augmentations = int(review_report.get("accepted_augmentations", 0))
    rules_with_sources = sum(has_public_rule_source(rule) for rule in rules if isinstance(rule, dict))

    flat_ok = all(task_counts.get(task, 0) >= args.min_flat_task_records for task in FLAT_TASKS)
    scenario_ok = all(task_counts.get(task, 0) >= args.min_scenario_task_records for task in SCENARIO_TASKS)
    data_volume_ok = len(rows) >= args.min_total_records and flat_ok and scenario_ok

    validation_ok = bool(validation_report.get("passed"))
    near_dup_ok = float(validation_report.get("near_duplicate_rate", 1.0)) <= args.max_near_duplicate_rate
    if split_independence_report:
        split_ok = split_independence_report.get("status") == "pass"
    else:
        split_ok = all(value == 0 for value in overlap.values())
    data_quality_gate_ok = data_quality_report.get("gates", {}).get("status") == "pass"
    data_quality_ok = validation_ok and near_dup_ok and split_ok and data_quality_gate_ok

    review_pipeline_present = reviewed_records > 0 and augmentations > 0
    review_depth_ok = all(
        finalized_by_task.get(task, 0) >= args.min_finalized_review_per_task
        for task in sorted(summary.get("by_task", {}))
    )
    review_agreement_ok = agreement >= args.min_review_agreement_rate

    tfidf_ok = bool(tfidf_report) and bool(tfidf_report.get("train"))
    formal_transformer_suite_reports = [report for report in transformer_suite_reports if is_formal_transformer_report(report)]
    transformer_covered_tasks = sorted(
        {
            str(report.get("task_type"))
            for report in formal_transformer_suite_reports
            if report.get("task_type")
        }
    )
    transformer_quality_by_task: dict[str, dict[str, Any]] = {}
    for report in formal_transformer_suite_reports:
        task_type = report.get("task_type")
        macro_f1 = (report.get("test_evaluation") or {}).get("macro_f1")
        if not task_type or macro_f1 is None:
            continue
        task_key = str(task_type)
        score = float(macro_f1)
        current = transformer_quality_by_task.get(task_key)
        if current is None or score > float(current["test_macro_f1"]):
            transformer_quality_by_task[task_key] = {
                "test_macro_f1": score,
                "path": report.get("_path"),
                "model_name": report.get("model_name") or report.get("config", {}).get("model_name"),
            }
    transformer_quality_ok = (
        set(transformer_quality_by_task) >= TRANSFORMER_TASKS
        and all(
            transformer_quality_by_task[task]["test_macro_f1"] >= args.min_transformer_macro_f1
            for task in TRANSFORMER_TASKS
        )
    )
    transformer_ok = set(transformer_covered_tasks) >= TRANSFORMER_TASKS and transformer_quality_ok
    generative_covered_tasks = sorted(
        {
            str(task_type)
            for report in generative_suite_reports
            for task_type in (
                report.get("task_types")
                if isinstance(report.get("task_types"), list)
                else [report.get("task_type")]
            )
            if task_type
        }
    )
    formal_generative_reports = [
        report
        for report in generative_suite_reports
        if report.get("objective") in FORMAL_GENERATIVE_OBJECTIVES
    ]
    formal_generative_reports_for_scoring = sorted(
        formal_generative_reports,
        key=lambda report: 1 if report.get("_path") == args.generative_report else 0,
    )
    formal_generative_covered_tasks = sorted(
        {
            str(task_type)
            for report in formal_generative_reports
            for task_type in (
                report.get("task_types")
                if isinstance(report.get("task_types"), list)
                else [report.get("task_type")]
            )
            if task_type
        }
    )
    generative_quality = {}
    generative_structured_quality: dict[str, dict[str, Any]] = {}
    for report in formal_generative_reports_for_scoring:
        metric_source = report.get("test_evaluation") or report.get("final_validation") or {}
        structured_metrics = summarize_generative_structured_metrics(report)
        for task_type in report.get("task_types") or ([report.get("task_type")] if report.get("task_type") else []):
            primary_f1 = metric_source.get("token_f1") if task_type == "regulation_qa" else metric_source.get("char_f1")
            if primary_f1 is not None:
                generative_quality[str(task_type)] = float(primary_f1)
            if structured_metrics:
                generative_structured_quality[str(task_type)] = structured_metrics
    generative_quality_ok = (
        set(generative_quality) >= GENERATIVE_TASKS
        and all(value >= args.min_generative_token_f1 for value in generative_quality.values())
    )
    generative_ok = (
        bool(formal_generative_reports)
        and set(formal_generative_covered_tasks) >= GENERATIVE_TASKS
        and generative_quality_ok
    )
    structured_generation_ok = structured_normalization_report.get("overall_status") == "pass"
    model_evidence_ok = tfidf_ok and transformer_ok and generative_ok and structured_generation_ok

    metadata_dataset = dataset_metadata.get("dataset", {}) if isinstance(dataset_metadata.get("dataset"), dict) else {}
    metadata_ok = (
        str(metadata_dataset.get("version")) == args.expected_version
        and int(metadata_dataset.get("record_count") or 0) == len(rows)
    )
    stale_release_files = collect_stale_release_files(
        args.expected_version,
        stale_tokens=["v0.1", '"version": "0.1"', '"record_count": 720', "GridInstruct v0.1"],
    )
    doi_values = []
    for key in ("data_doi", "archived_code_release_doi"):
        value = str(dataset_metadata.get("availability", {}).get(key, "")).strip() if isinstance(dataset_metadata.get("availability"), dict) else ""
        if value:
            doi_values.append(value)
    doi_ok = bool(doi_values) and all(value.lower() != "pending" for value in doi_values)
    rule_traceability_ok = rules_with_sources == len(rules) and len(rules) > 0

    paper_readiness_ok = (
        data_volume_ok
        and data_quality_ok
        and review_pipeline_present
        and review_depth_ok
        and review_agreement_ok
        and model_evidence_ok
        and metadata_ok
        and not stale_release_files
        and doi_ok
        and rule_traceability_ok
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "expected_version": args.expected_version,
        "thresholds": {
            "min_total_records": args.min_total_records,
            "min_flat_task_records": args.min_flat_task_records,
            "min_scenario_task_records": args.min_scenario_task_records,
            "max_near_duplicate_rate": args.max_near_duplicate_rate,
            "min_finalized_review_per_task": args.min_finalized_review_per_task,
            "min_review_agreement_rate": args.min_review_agreement_rate,
            "min_transformer_macro_f1": args.min_transformer_macro_f1,
            "min_generative_token_f1": args.min_generative_token_f1,
        },
        "data_scale": {
            "status": pass_state(data_volume_ok),
            "total_records": len(rows),
            "per_task": task_counts,
            "flat_tasks_meet_target": flat_ok,
            "scenario_tasks_meet_target": scenario_ok,
        },
        "data_quality": {
            "status": pass_state(data_quality_ok),
            "validation_passed": validation_ok,
            "near_duplicate_rate": validation_report.get("near_duplicate_rate"),
            "scenario_overlap": overlap,
            "scenario_overlap_scope": "diagnostic for standard train/validation/test scenario reuse; hard split gates are read from split_independence_report when present",
            "split_independence_report": {
                "path": args.split_independence_report,
                "status": split_independence_report.get("status"),
                "hard_gates": split_independence_report.get("hard_gates", {}),
                "ood_scenario_overlap_with_standard": split_independence_report.get("ood_scenario_overlap_with_standard"),
            },
            "data_quality_optimization": {
                "path": args.data_quality_report,
                "status": data_quality_report.get("gates", {}).get("status"),
                "required_missing_total": data_quality_report.get("gates", {}).get("required_missing_total"),
                "split_id_overlap_total": data_quality_report.get("gates", {}).get("split_id_overlap_total"),
                "split_prompt_input_overlap_total": data_quality_report.get("gates", {}).get("split_prompt_input_overlap_total"),
            },
            "validation_summary": {
                "schema_errors": len(validation_report.get("schema_errors", [])),
                "duplicate_ids": len(validation_report.get("duplicate_ids", [])),
                "invalid_rule_links": len(validation_report.get("invalid_rule_links", [])),
                "invalid_scenario_links": len(validation_report.get("invalid_scenario_links", [])),
                "semantic_errors": len(validation_report.get("semantic_errors", [])),
            },
        },
        "llm_review_and_augmentation": {
            "status": "pass" if review_pipeline_present else "fail",
            "reviewed_records": reviewed_records,
            "accepted_augmentations": augmentations,
            "agreement_rate": agreement,
            "finalized_review_by_task": finalized_by_task,
            "review_status_counts": status_counts,
            "paper_grade_review_depth": pass_state(review_depth_ok),
            "paper_grade_review_agreement": pass_state(review_agreement_ok),
        },
        "model_evidence": {
            "status": "pass" if model_evidence_ok else "partial",
            "tfidf": summarize_model_report(tfidf_report),
            "transformer_latest": summarize_model_report(transformer_report),
            "transformer_reports": [
                {
                    "path": report.get("_path"),
                    "task_type": report.get("task_type"),
                    "train": report.get("train"),
                    "generated_at": report.get("generated_at"),
                    "best_validation": report.get("best_validation"),
                    "test_evaluation": report.get("test_evaluation"),
                }
                for report in formal_transformer_suite_reports
            ],
            "generative_latest": summarize_model_report(generative_report),
            "generative_reports": [
                {
                    "path": report.get("_path"),
                    "task_types": (
                        report.get("task_types")
                        if isinstance(report.get("task_types"), list) and report.get("task_types")
                        else ([report.get("task_type")] if report.get("task_type") else [])
                    ),
                    "train": report.get("train"),
                    "model_name": report.get("model_name") or report.get("config", {}).get("model_name"),
                    "target_mode": report.get("config", {}).get("target_mode"),
                    "generated_at": report.get("generated_at"),
                    "best_validation": report.get("best_validation"),
                    "test_evaluation": report.get("test_evaluation"),
                    "structured_metrics": summarize_generative_structured_metrics(report),
                }
                for report in generative_suite_reports
            ],
            "has_v1.2_tfidf": tfidf_ok,
            "has_v1.2_transformer": transformer_ok,
            "transformer_covered_tasks": transformer_covered_tasks,
            "transformer_quality_by_task": transformer_quality_by_task,
            "transformer_quality_passed": transformer_quality_ok,
            "generative_covered_tasks": generative_covered_tasks,
            "formal_generative_covered_tasks": formal_generative_covered_tasks,
            "formal_generative_objectives": sorted(FORMAL_GENERATIVE_OBJECTIVES),
            "formal_generative_token_f1": generative_quality,
            "formal_generative_structured_quality": generative_structured_quality,
            "formal_generative_quality_passed": generative_quality_ok,
            "has_v1.2_generative": generative_ok,
            "structured_normalization_report": args.structured_normalization_report,
            "structured_generation_gate": structured_normalization_report.get("overall_status"),
            "structured_generation_tasks": structured_normalization_report.get("tasks", {}),
            "structured_generation_passed": structured_generation_ok,
        },
        "paper_readiness": {
            "status": pass_state(paper_readiness_ok),
            "dataset_metadata_consistent": metadata_ok,
            "stale_release_files": stale_release_files,
            "doi_ready": doi_ok,
            "rule_traceability_ready": rule_traceability_ok,
            "rules_with_public_source_links": rules_with_sources,
            "rule_count": len(rules),
        },
        "overall_status": "local_evidence_ready" if paper_readiness_ok else "local_evidence_not_ready",
        "status_scope": "automated local data, model, metadata, DOI-presence, and rule-traceability evidence only; final submission readiness is determined after evidence binding, archive replay, declarations, and independent human review",
        "next_actions": [],
    }

    next_actions = report["next_actions"]
    if not data_volume_ok:
        next_actions.append("继续扩充数据规模，优先补齐未达标任务的记录数。")
    if not data_quality_ok:
        next_actions.append("修复验证、近重复或 split 独立性硬门控问题，再把 v1.2 split 作为唯一官方口径。")
    if not review_pipeline_present:
        next_actions.append("继续执行 LLM review 与受控增广，确保每轮都有日志和可追溯输出。")
    if not review_depth_ok or not review_agreement_ok:
        next_actions.append("扩大专家审核样本，至少做到每个任务都有可发布级别的 finalized review 和一致性统计。")
    if not model_evidence_ok:
        next_actions.append("补齐 v1.2 官方 split 上的统一模型训练证据，并确保 Transformer、生成式基线和结构化语义门禁达到配置门槛。")
    if not metadata_ok or stale_release_files:
        next_actions.append("重生成 release-facing 元数据与文档，确保版本号、记录数和 split 引用全部对齐当前快照。")
    if not doi_ok:
        next_actions.append("补齐公开仓库、版本存档和 DOI。")
    if not rule_traceability_ok:
        next_actions.append("把规则表升级为条款级追溯表，补 source_url、standard_id、clause_id 等字段。")

    write_json(ROOT / args.output_json, report)

    md_lines = [
        "# Project Milestone Audit",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Dataset: `{args.dataset}`",
        f"- Overall status: `{report['overall_status']}`",
        "",
        "## Data Scale",
        "",
        f"- Status: `{report['data_scale']['status']}`",
        f"- Total records: {report['data_scale']['total_records']}",
        f"- Flat-task targets met: {report['data_scale']['flat_tasks_meet_target']}",
        f"- Scenario-task targets met: {report['data_scale']['scenario_tasks_meet_target']}",
        "",
        "## Data Quality",
        "",
        f"- Status: `{report['data_quality']['status']}`",
        f"- Validation passed: {report['data_quality']['validation_passed']}",
        f"- Near-duplicate rate: {report['data_quality']['near_duplicate_rate']}",
        f"- Train/test supervised-group overlap: {report['data_quality']['scenario_overlap']['train_test']}",
        f"- Train/validation supervised-group overlap: {report['data_quality']['scenario_overlap']['train_validation']}",
        f"- Validation/test supervised-group overlap: {report['data_quality']['scenario_overlap'].get('validation_test')}",
        f"- Data-quality optimization gate: `{report['data_quality']['data_quality_optimization']['status']}`",
        "",
        "## LLM Review And Augmentation",
        "",
        f"- Status: `{report['llm_review_and_augmentation']['status']}`",
        f"- Reviewed records: {report['llm_review_and_augmentation']['reviewed_records']}",
        f"- Accepted augmentations: {report['llm_review_and_augmentation']['accepted_augmentations']}",
        f"- Agreement rate: {report['llm_review_and_augmentation']['agreement_rate']:.4f}",
        f"- Paper-grade review depth: `{report['llm_review_and_augmentation']['paper_grade_review_depth']}`",
        f"- Paper-grade review agreement: `{report['llm_review_and_augmentation']['paper_grade_review_agreement']}`",
        "",
        "## Model Evidence",
        "",
        f"- Status: `{report['model_evidence']['status']}`",
        f"- v1.2 TF-IDF report present: {report['model_evidence']['has_v1.2_tfidf']}",
        f"- v1.2 Transformer report present: {report['model_evidence']['has_v1.2_transformer']}",
        f"- Transformer covered tasks: {', '.join(report['model_evidence']['transformer_covered_tasks']) or 'none'}",
        f"- Transformer quality passed: {report['model_evidence']['transformer_quality_passed']}",
        f"- v1.2 Formal generative report present: {report['model_evidence']['has_v1.2_generative']}",
        f"- Formal generative covered tasks: {', '.join(report['model_evidence']['formal_generative_covered_tasks']) or 'none'}",
        f"- Formal generative token-F1: {report['model_evidence']['formal_generative_token_f1']}",
        f"- Formal generative structured metrics: {report['model_evidence']['formal_generative_structured_quality']}",
        f"- Structured generation gate: {report['model_evidence']['structured_generation_gate']}",
        "",
        "## Paper Readiness",
        "",
        f"- Status: `{report['paper_readiness']['status']}`",
        f"- Dataset metadata consistent: {report['paper_readiness']['dataset_metadata_consistent']}",
        f"- DOI ready: {report['paper_readiness']['doi_ready']}",
        f"- Rule traceability ready: {report['paper_readiness']['rule_traceability_ready']}",
        f"- Stale release files: {len(report['paper_readiness']['stale_release_files'])}",
        "",
        "## Next Actions",
        "",
    ]
    if next_actions:
        md_lines.extend(f"- {item}" for item in next_actions)
    else:
        md_lines.append("- No blocking actions.")
    (ROOT / args.output_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
