"""Refresh release-facing metadata and docs for the current GridInstruct snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_release_bundle import resolved_metadata_value
from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, summarize_records, write_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_records(path: Path) -> int | None:
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(payload, list):
            return len(payload)
    return None


def file_manifest(paths: list[str]) -> list[dict[str, Any]]:
    rows = []
    for rel in paths:
        path = ROOT / rel
        if not path.exists():
            rows.append({"path": rel, "exists": False})
            continue
        rows.append(
            {
                "path": rel,
                "exists": True,
                "bytes": path.stat().st_size,
                "records": count_records(path),
                "sha256": sha256(path),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    lines = [f"{row['sha256']}  {row['path']}" for row in rows if row.get("exists") and row.get("sha256")]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_version_label(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def source_traceability(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_task: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        task = row["task_type"]
        by_task[task]["records"] += 1
        by_task[task]["with_rules"] += int(bool(row.get("source_regulation_ids")))
        by_task[task]["with_simulation"] += int(bool(row.get("source_simulation_case_id")))
        by_task[task]["with_rationale"] += int(bool(row.get("rationale")))
    return {task: dict(counter) for task, counter in sorted(by_task.items())}


def source_traceability_rows(summary: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    rows = []
    for task, counts in summary.items():
        total = counts["records"] or 1
        rows.append(
            {
                "task_type": task,
                "records": counts["records"],
                "with_rules": counts["with_rules"],
                "with_simulation": counts["with_simulation"],
                "with_rationale": counts["with_rationale"],
                "rule_link_rate": round(counts["with_rules"] / total, 4),
                "simulation_link_rate": round(counts["with_simulation"] / total, 4),
                "rationale_rate": round(counts["with_rationale"] / total, 4),
            }
        )
    return rows


def split_integrity(split_paths: dict[str, str]) -> dict[str, Any]:
    splits = {}
    scenario_sets = {}
    id_sets = {}
    for split, rel in split_paths.items():
        rows = read_jsonl(ROOT / rel)
        splits[split] = summarize_records(rows)
        id_sets[split] = {row["id"] for row in rows}
        scenario_sets[split] = {row.get("scenario_id") for row in rows if row.get("scenario_id")}

    overlaps = {}
    split_names = list(split_paths)
    for index, left in enumerate(split_names):
        for right in split_names[index + 1 :]:
            overlaps[f"{left}__{right}"] = {
                "record_id_overlap": sorted(id_sets[left] & id_sets[right]),
                "scenario_id_overlap": sorted(scenario_sets[left] & scenario_sets[right]),
            }
    return {"splits": splits, "overlaps": overlaps}


def split_coverage_notes(split_summary: dict[str, Any], all_task_types: list[str]) -> list[str]:
    notes = []
    for split_name, stats in split_summary["splits"].items():
        task_counts = stats["by_task"]
        missing = [task for task in all_task_types if task_counts.get(task, 0) == 0]
        if missing:
            notes.append(f"{split_name} missing task types: {', '.join(missing)}")
    return notes


def rule_dictionary_rows(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for rule in rules:
        rows.append(
            {
                "rule_id": rule.get("rule_id"),
                "rule_type": rule.get("rule_type"),
                "standard_id": rule.get("standard_id", ""),
                "standard_title": rule.get("standard_title", ""),
                "clause_id": rule.get("clause_id", ""),
                "source_title": rule.get("source_title", ""),
                "source_url": rule.get("source_url", ""),
                "source_kind": rule.get("source_kind", ""),
                "issuing_body": rule.get("issuing_body", ""),
                "document_id": rule.get("document_id", ""),
                "document_version": rule.get("document_version", ""),
                "external_clause_locator": rule.get("external_clause_locator", ""),
                "access_date": rule.get("access_date", ""),
                "source_metadata_sha256": rule.get("source_metadata_sha256", ""),
                "summary": rule.get("summary"),
                "evidence_fields": ", ".join(rule.get("evidence_fields", [])),
                "applicable_tasks": ", ".join(rule.get("applicable_tasks", [])),
            }
        )
    return rows


def build_annotation_policy(version: str) -> dict[str, Any]:
    version_label = format_version_label(version)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": f"Rule-based label policy for GridInstruct {version_label} release snapshot",
        "active_issue_definition": {
            "thermal_overload": "scenario.overloaded_branches is non-empty or max_branch_loading_percent > 100.0",
            "low_voltage": "at least one voltage violation entry has vm_pu < 0.95",
            "high_voltage": "at least one voltage violation entry has vm_pu > 1.05",
        },
        "compliance_label_policy": {
            "non_compliant": "Active issue exists and the proposed action either increases constrained-flow loading, skips required checks, or restores equipment prematurely.",
            "compliant_with_monitoring": "Action is directionally corrective but still requires post-action review, continued monitoring, or staged execution checks.",
            "compliant": "No active issue exists and the proposed action is a non-escalating corrective or risk-reducing action.",
        },
        "action_templates": {
            "continue_increasing_flow": {
                "text": "继续增加受限断面潮流",
                "label_if_active_issue": "non_compliant",
                "label_if_no_active_issue": "compliant_with_monitoring",
            },
            "maintain_and_monitor": {
                "text": "维持当前潮流并启动监视",
                "label_if_active_issue": "non_compliant",
                "label_if_no_active_issue": "compliant_with_monitoring",
            },
            "reactive_support_then_review": {
                "text": "先执行无功支撑后复核潮流",
                "label_if_active_issue": "compliant_with_monitoring",
                "label_if_no_active_issue": "compliant",
            },
            "redispatch_reduce_loading": {
                "text": "调整邻近机组出力降低断面负载",
                "label_if_active_issue": "compliant_with_monitoring",
                "label_if_no_active_issue": "compliant",
            },
        },
        "limitations": [
            "This policy is task-oriented and summarizes label behavior at the dataset level rather than reproducing full operational rulebooks.",
            "validate_dataset.py checks explicit semantic contradictions, but it does not yet formalize every expert judgment nuance.",
        ],
    }


def build_expert_review_protocol(
    review_report: dict[str, Any] | None,
    human_execution: dict[str, Any],
    version: str,
) -> dict[str, Any]:
    version_label = format_version_label(version)
    agreement = review_report.get("agreement", {}) if review_report else {}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": f"Submission-facing protocol template for explanation, preference, and compliance-label review in GridInstruct {version_label}",
        "status": human_execution.get("status", "external_human_review_not_audited"),
        "review_units": [
            "regulation_compliance_check",
            "auxiliary_decision",
            "dispatcher_intent_tool_call",
            "intelligent_data_query",
            "operation_ticket_check",
            "regulation_qa",
        ],
        "review_workflow": {
            "minimum_reviewers_per_record": 2,
            "adjudication": "Escalate disagreements to an adjudication pass and record the adjudicated label.",
            "anonymity": "Publish reviewer roles and counts, not reviewer identities.",
            "sampling_plan": "Report the audited record count per task type and per label family in the release snapshot.",
        },
        "required_fields_to_capture": [
            "record_id",
            "task_type",
            "review_round",
            "reviewer_role",
            "review_decision",
            "disagreement_type",
            "adjudicated_label",
            "adjudication_note",
        ],
        "submission_statistics_expected": [
            "per-task agreement rate",
            "overall agreement rate",
            "number of adjudicated records",
            "label-disagreement breakdown",
        ],
        "current_statistics": {
            "human_review": {
                "selected_samples": human_execution.get("selected_samples", 0),
                "assignment_rows": human_execution.get("assignment_rows", 0),
                "filled_rows": human_execution.get("filled_human_review_rows", 0),
                "complete_rows": human_execution.get("complete_human_review_rows", 0),
                "samples_with_two_complete_reviews": human_execution.get("samples_with_two_complete_reviews", 0),
                "status": human_execution.get("status", "external_human_review_not_audited"),
            },
            "machine_assisted_consistency_screen": {
                "reviewed_records": review_report.get("reviewed_records", 0) if review_report else 0,
                "accepted_augmentations": review_report.get("accepted_augmentations", 0) if review_report else 0,
                "decision_agreement_rate": agreement.get("overall_agreement_rate", 0.0),
                "adjudicated_rows": agreement.get("adjudicated_rows", 0),
            },
        },
        "workspace_support_assets": [
            "templates/expert_review_log_template.csv",
            "templates/expert_review_sampling_plan_template.csv",
            "scripts/compute_expert_review_stats.py",
            "scripts/run_llm_review_pipeline.py",
        ],
    }


def build_paper_outline_alignment(
    metadata: dict[str, Any],
    audit: dict[str, Any],
    review_report: dict[str, Any] | None,
    human_execution: dict[str, Any],
) -> dict[str, Any]:
    dataset = metadata.get("dataset", {})
    paper = audit.get("paper_readiness", {})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": {
            "dataset_path": dataset.get("dataset_path"),
            "version": dataset.get("version"),
            "record_count": dataset.get("record_count"),
        },
        "alignment": [
            {
                "section": "Dataset scope and composition",
                "status": audit.get("data_scale", {}).get("status"),
                "notes": f"{dataset.get('record_count')} records across six task families and three dispatch stages.",
            },
            {
                "section": "Validation and split integrity",
                "status": audit.get("data_quality", {}).get("status"),
                "notes": "Schema validation, duplicate checks, scenario-link checks, and split overlap checks are recorded in release metadata.",
            },
            {
                "section": "Machine screen and external human review",
                "status": (
                    "pass"
                    if human_execution.get("status") == "external_human_review_completed"
                    else "external_pending"
                ),
                "notes": (
                    f"Machine-assisted consistency screen: {review_report.get('reviewed_records', 0)} records, "
                    f"decision agreement={review_report.get('agreement', {}).get('overall_agreement_rate', 0.0):.4f}; "
                    f"external human review: {human_execution.get('complete_human_review_rows', 0)}/"
                    f"{human_execution.get('assignment_rows', 0)} complete assignments."
                    if review_report
                    else f"No machine-screen report attached; external human review status={human_execution.get('status', 'unknown')}."
                ),
            },
            {
                "section": "Baseline evidence",
                "status": audit.get("model_evidence", {}).get("status"),
                "notes": "TF-IDF, Transformer, and pretrained seq2seq baselines are present for the current split set.",
            },
            {
                "section": "Archival traceability",
                "status": paper.get("status"),
                "notes": "Public DOI/repository identifiers still govern final submission readiness.",
            },
        ],
    }


def build_data_descriptor_audit(
    metadata: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": metadata.get("dataset", {}),
        "readiness": {
            "overall_status": audit.get("overall_status"),
            "data_scale": audit.get("data_scale", {}).get("status"),
            "data_quality": audit.get("data_quality", {}).get("status"),
            "review_status": audit.get("llm_review_and_augmentation", {}).get("status"),
            "model_evidence": audit.get("model_evidence", {}).get("status"),
            "paper_readiness": audit.get("paper_readiness", {}).get("status"),
        },
        "remaining_gaps": audit.get("next_actions", []),
        "notes": [
            "This artifact tracks the submission-facing state of the current release snapshot.",
            "It is an internal readiness audit, not a claim of external acceptance.",
        ],
    }


def write_descriptor_draft_doc(
    path: Path,
    metadata: dict[str, Any],
    simulation_report: dict[str, Any],
    audit: dict[str, Any],
    human_execution: dict[str, Any],
) -> None:
    dataset = metadata.get("dataset", {})
    summary = metadata.get("record_summary", {})
    networks = [
        network
        for network, count in sorted(summary.get("by_network", {}).items(), key=lambda item: str(item[0]))
        if count > 0
    ]
    network_text = ", ".join("rule-only" if str(network).lower() in {"null", "none"} else str(network) for network in networks)
    lines = [
        "# GridInstruct Scientific Data Descriptor Draft",
        "",
        f"This manuscript-aligned draft summarizes the current GridInstruct v{dataset.get('version')} release snapshot.",
        "",
        "## Title",
        "",
        "A topology-grounded instruction dataset for power dispatch assistance",
        "",
        "## Abstract Draft",
        "",
        (
            f"We describe GridInstruct v{dataset.get('version')}, a topology-grounded instruction dataset for research on "
            f"power-dispatch assistance. The current release contains {dataset.get('record_count')} English-derived instruction records "
            "spanning operation-ticket checking, regulation question answering, regulation compliance checking, auxiliary decision "
            "support, dispatcher intent/tool planning, and intelligent data queries. The dataset links natural-language tasks to "
            "structured regulation identifiers, reproducible IEEE benchmark simulation states, and task-specific outputs such as "
            "compliance labels, tool plans, structured queries, and preference pairs. The current release is built from IEEE 14, "
            f"IEEE 30, IEEE 57, IEEE 118, IEEE 300, PEGASE, and RTE benchmark systems with {simulation_report.get('converged_scenarios')} converged synthetic scenarios and additional topology-stress and OPF-closed-loop validation subsets. "
            "Technical validation covers schema integrity, identifier uniqueness, traceability, split integrity, and simulation convergence. "
            "GridInstruct is intended for benchmark construction, instruction-data research, and audit-friendly LLM evaluation rather than real-time operational deployment."
        ),
        "",
        "## Background And Summary",
        "",
        "GridInstruct targets the gap between power-system simulation datasets and instruction-tuning datasets. It organizes records around pre-event checking, real-time regulation/compliance/decision support, and post-event structured data queries.",
        "",
        "## Methods",
        "",
        "### Regulation Layer",
        "",
        "Rules are stored as structured identifiers and short task-oriented summaries rather than long copyrighted source text.",
        "",
        "### Topology And Simulation Layer",
        "",
        f"The current release uses the following network families when simulation provenance is applicable: {network_text}. Scenario generation produces base, generator-perturbation, N-1, selected N-2, topology-stress, and OPF-closed-loop synthetic states with convergence metadata and violation summaries.",
        "",
        "### Instruction Layer",
        "",
        f"The release contains six task families. Current per-task counts are {summary.get('by_task', {})}.",
        "",
        "### Human Review And Model Assistance",
        "",
        "Machine-assisted consistency screening and targeted augmentation are tracked separately from external human expert validation. The external package contains blinded assignments for two independent reviewers per sample and an adjudication schema. Completion may be claimed only when the execution audit reports two complete human reviews per sample and the required adjudications.",
        f"Current external-human execution status: `{human_execution.get('status', 'unknown')}`; complete assignments: {human_execution.get('complete_human_review_rows', 0)}/{human_execution.get('assignment_rows', 0)}.",
        "",
        "## Data Records",
        "",
        "Core release contents are documented in `docs/DATA_RECORDS.md`, `metadata/file_manifest.csv`, `metadata/checksums_sha256.txt`, and `examples/sample_records.jsonl`.",
        "",
        "## Technical Validation",
        "",
        f"- Schema and semantic validation: {audit.get('data_quality', {}).get('status', 'unknown')}",
        f"- Review and augmentation gate: {audit.get('llm_review_and_augmentation', {}).get('status', 'unknown')}",
        f"- Model evidence gate: {audit.get('model_evidence', {}).get('status', 'unknown')}",
        "",
        "## Usage Notes",
        "",
        "The release is intended for dataset construction, benchmark design, and model-evaluation research. It should not be framed as a certified operational tool or real-time dispatch assistant.",
        "",
        "## Data And Code Availability",
        "",
        "The current workspace contains the release candidate assets. Public repository URLs and DOI/accession identifiers must be inserted after archival deposition and before external submission.",
        "",
        "## Archive Status",
        "",
    ]
    for index, action in enumerate(audit.get("next_actions", []), start=1):
        if "v0.1 / 720 条旧口径" in action:
            action = "重生成 release-facing 元数据与文档，确保版本号、记录数和 split 引用全部对齐当前快照。"
        lines.append(f"{index}. {action}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_availability_doc(
    path: Path,
    metadata: dict[str, Any],
    audit: dict[str, Any],
    human_execution: dict[str, Any],
) -> None:
    dataset = metadata.get("dataset", {})
    lines = [
        "# Availability And Limitations",
        "",
        "## Data Availability",
        "",
        f"The current release snapshot is `{dataset.get('dataset_path')}`. The construction-source table is retained as `{dataset.get('source_dataset_path', 'not listed')}` for provenance and auditability. Before external submission, deposit the dataset in a stable public repository and add the repository name, DOI, version identifier, and access date.",
        "",
        "## Code Availability",
        "",
        "Before external submission, add the public code repository URL, release tag or commit SHA, code license, and archived code-release DOI when available.",
        "",
        "## License",
        "",
        "Local code/data licenses are declared in `LICENSE-CODE`, `LICENSE-DATA`, and `docs/LICENSES_AND_CITATION.md`. Release metadata should point those licenses at the final public archive identifiers.",
        "",
        "## Sensitive Or Restricted Content",
        "",
        "The dataset uses public IEEE benchmark systems and synthetic simulation states. It should not contain real grid operating records or secrets. Long copyrighted regulation text is not republished; records use rule identifiers and short task-oriented summaries.",
        "",
        "## Known Limitations",
        "",
        f"0. External human review status is `{human_execution.get('status', 'unknown')}` with {human_execution.get('complete_human_review_rows', 0)}/{human_execution.get('assignment_rows', 0)} complete assignments; machine-assisted screening is not counted as human expert evidence.",
    ]
    for index, action in enumerate(audit.get("next_actions", []), start=1):
        lines.append(f"{index}. {action}")
    lines.append(f"{len(audit.get('next_actions', [])) + 1}. IEEE benchmark systems and synthetic scenarios do not establish real-grid operational safety.")
    lines.append(
        f"{len(audit.get('next_actions', [])) + 2}. The current rule dictionary only covers {len(metadata.get('rule_dictionary', []))} publicly linked rule summaries and should not be described as comprehensive national or provincial regulation coverage."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_annotation_policy_doc(path: Path, annotation_policy: dict[str, Any]) -> None:
    policy = annotation_policy.get("compliance_label_policy", {})
    lines = [
        "# Annotation Policy",
        "",
        f"Generated: {annotation_policy.get('generated_at')}",
        "",
        f"This note records the current rule-based annotation policy for {annotation_policy.get('scope')}.",
        "",
        "## Compliance Labels",
        "",
        "| label | meaning |",
        "| --- | --- |",
    ]
    for label, meaning in policy.items():
        lines.append(f"| `{label}` | {meaning} |")
    lines.extend(["", "## Limitations", ""])
    for item in annotation_policy.get("limitations", []):
        lines.append(f"- {item}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_expert_review_protocol_doc(path: Path, protocol: dict[str, Any]) -> None:
    stats = protocol.get("current_statistics", {})
    human = stats.get("human_review", {})
    machine = stats.get("machine_assisted_consistency_screen", {})
    lines = [
        "# Expert Review Protocol",
        "",
        f"Generated: {protocol.get('generated_at')}",
        "",
        f"This document records the current review protocol for {protocol.get('scope')}.",
        "",
        "## Workflow",
        "",
        f"- Minimum reviewers per record: {protocol.get('review_workflow', {}).get('minimum_reviewers_per_record')}",
        f"- Adjudication: {protocol.get('review_workflow', {}).get('adjudication')}",
        f"- Sampling plan: {protocol.get('review_workflow', {}).get('sampling_plan')}",
        "",
        "## Current External Human Review",
        "",
        f"- Status: {human.get('status', 'unknown')}",
        f"- Selected samples: {human.get('selected_samples', 0)}",
        f"- Assignment rows: {human.get('assignment_rows', 0)}",
        f"- Complete rows: {human.get('complete_rows', 0)}",
        f"- Samples with two complete reviews: {human.get('samples_with_two_complete_reviews', 0)}",
        "",
        "## Machine-Assisted Consistency Screen",
        "",
        f"- Screened records: {machine.get('reviewed_records', 0)}",
        f"- Accepted augmentations: {machine.get('accepted_augmentations', 0)}",
        f"- Machine decision agreement rate: {machine.get('decision_agreement_rate', 0.0)}",
        f"- Machine adjudication rows: {machine.get('adjudicated_rows', 0)}",
        "",
        "## Captured Fields",
        "",
    ]
    for field in protocol.get("required_fields_to_capture", []):
        lines.append(f"- `{field}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_paper_outline_alignment_doc(path: Path, outline: dict[str, Any]) -> None:
    lines = [
        "# Paper Outline Alignment",
        "",
        f"Generated: {outline.get('generated_at')}",
        "",
        "This matrix aligns the current GridInstruct release snapshot with the Scientific Data-style paper outline.",
        "",
        "| Section | Status | Notes |",
        "| --- | --- | --- |",
    ]
    for row in outline.get("alignment", []):
        lines.append(f"| {row.get('section')} | {str(row.get('status', 'unknown')).upper()} | {row.get('notes')} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_usage_notes_doc(path: Path, dataset_path: str, split_paths: dict[str, str], version: str) -> None:
    version_label = format_version_label(version)
    lines = [
        "# Usage Notes",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Validate And Split",
        "",
        f"`python scripts/validate_dataset.py --input {dataset_path}`",
        f"`python scripts/create_splits.py --input {dataset_path}`",
        "",
        "## Official Splits",
        "",
    ]
    for name, rel in split_paths.items():
        lines.append(f"- `{name}`: `{rel}`")
    lines.extend(
        [
            "",
            "## Scope Note",
            "",
            f"`ood_test` in {version_label} is intended for topology/scenario transfer checks and does not include flat-task families such as `operation_ticket_check` and `regulation_qa`.",
            "",
            "## Operational Caveat",
            "",
            "GridInstruct is for research on data construction, benchmark design, and model evaluation. It is not a real-time dispatch tool and is not validated for safety-critical operational decisions.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sample_records(path: Path, rows: list[dict[str, Any]]) -> None:
    seen = set()
    samples = []
    for row in rows:
        task = row.get("task_type")
        if task in seen:
            continue
        seen.add(task)
        samples.append(row)
    ensure_dirs(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in samples:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def summarize_model_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    summary = {
        "generated_at": report.get("generated_at"),
        "train": report.get("train"),
        "objective": report.get("objective"),
    }
    for key in ("task_type", "task_types", "test_macro_score", "ood_macro_score", "best_validation", "test_evaluation"):
        if key in report:
            summary[key] = report.get(key)
    return summary


def task_tuple(report: dict[str, Any]) -> tuple[str, ...]:
    task_types = report.get("task_types")
    if isinstance(task_types, list) and task_types:
        return tuple(str(task) for task in task_types)
    task_type = report.get("task_type")
    return (str(task_type),) if task_type else ()


def canonical_generative_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    seen_formal_tasks: set[tuple[str, ...]] = set()

    def rank(report: dict[str, Any]) -> tuple[int, int, str]:
        hint = str(report.get("config", {}).get("output_prefix") or report.get("output_prefix") or report.get("train") or "")
        version_rank = 0 if "plus9" in hint else (1 if "plus8" in hint else 2)
        tasks = task_tuple(report)
        if tasks == ("regulation_qa",):
            if "seq2seq_regqa_harmonized" in hint:
                return (0, version_rank, hint)
            if hint.endswith("seq2seq_regqa"):
                return (1, version_rank, hint)
            return (2, version_rank, hint)
        return (0, version_rank, hint)

    for report in sorted(reports, key=rank):
        tasks = task_tuple(report)
        if report.get("objective") == "pretrained_seq2seq_generation" and tasks:
            if tasks in seen_formal_tasks:
                continue
            seen_formal_tasks.add(tasks)
        canonical.append(report)
    return canonical


def write_data_records_doc(
    path: Path,
    version: str,
    dataset_path: str,
    split_paths: dict[str, str],
    manifest: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    summary: dict[str, Any],
    split_summary: dict[str, Any],
    coverage_notes: list[str],
) -> None:
    version_label = format_version_label(version)
    core_paths = {
        dataset_path,
        *split_paths.values(),
        "metadata/schema.json",
        "metadata/task_taxonomy.json",
        "metadata/data_dictionary.csv",
        "rules/regulation_rules.json",
    }
    lines = [
        "# Data Records",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"GridInstruct {version_label} contains {summary['total_records']} instruction records. Records are stored as UTF-8 JSON Lines and validated against `metadata/schema.json`.",
        "",
        "## Core Files",
        "",
        "| path | records | bytes | sha256 |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in manifest:
        if row.get("path") not in core_paths or not row.get("exists"):
            continue
        lines.append(f"| {row['path']} | {row.get('records')} | {row.get('bytes')} | {row.get('sha256')} |")

    lines.extend(
        [
            "",
            "## Rule Dictionary",
            "",
            "| rule_id | standard_id | clause_id | rule_type | evidence_fields | applicable_tasks |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for rule in rules:
        lines.append(
            f"| {rule.get('rule_id')} | {rule.get('standard_id', '')} | {rule.get('clause_id', '')} | "
            f"{rule.get('rule_type')} | {', '.join(rule.get('evidence_fields', []))} | "
            f"{', '.join(rule.get('applicable_tasks', []))} |"
        )

    lines.extend(["", "## Task Distribution", "", "| task_type | records |", "| --- | ---: |"])
    for task_type, count in sorted(summary["by_task"].items()):
        lines.append(f"| {task_type} | {count} |")

    lines.extend(["", "## Split Distribution", "", "| split | records |", "| --- | ---: |"])
    for split_name, stats in split_summary["splits"].items():
        lines.append(f"| {split_name} | {stats['total_records']} |")

    lines.extend(
        [
            "",
            "## Split Coverage Notes",
            "",
        ]
    )
    if coverage_notes:
        for note in coverage_notes:
            lines.append(f"- {note}")
    else:
        lines.append("- All official evaluation splits cover their intended task families.")

    lines.extend(
        [
            "",
            "## Record Schema",
            "",
            "Every instruction record includes `id`, `task_stage`, `task_type`, `instruction`, `input`, `output`, `source_regulation_ids`, and `metadata`.",
            "Topology-grounded tasks additionally link to `network_model`, `scenario_id`, and `source_simulation_case_id` when applicable.",
            "`metadata/data_dictionary.csv` defines the released fields.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_technical_validation_doc(
    path: Path,
    summary: dict[str, Any],
    validation_report: dict[str, Any],
    simulation_report: dict[str, Any],
    trace_rows: list[dict[str, Any]],
    tfidf_report: dict[str, Any] | None,
    transformer_reports: list[dict[str, Any]],
    generative_reports: list[dict[str, Any]],
    review_report: dict[str, Any] | None,
    external_topology_physical_envelope: dict[str, Any] | None = None,
    opf_closed_loop_assumption: dict[str, Any] | None = None,
    rule_taxonomy_expansion: dict[str, Any] | None = None,
    hard_boundary_benchmark: dict[str, Any] | None = None,
) -> None:
    lines = [
        "# Technical Validation",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Dataset Integrity",
        "",
        "| Check | Result |",
        "| --- | ---: |",
        f"| Total records | {summary['total_records']} |",
        f"| Validation passed | {str(validation_report.get('passed', False)).lower()} |",
        f"| Near-duplicate rate | {validation_report.get('near_duplicate_rate', 'n/a')} |",
        f"| Schema errors | {len(validation_report.get('schema_errors', []))} |",
        f"| Duplicate ids | {len(validation_report.get('duplicate_ids', []))} |",
        f"| Invalid rule links | {len(validation_report.get('invalid_rule_links', []))} |",
        f"| Invalid scenario links | {len(validation_report.get('invalid_scenario_links', []))} |",
        f"| Semantic errors | {len(validation_report.get('semantic_errors', []))} |",
        "",
        "## Simulation Validation",
        "",
        "| Check | Result |",
        "| --- | ---: |",
        f"| Systems | {', '.join(simulation_report.get('systems', []))} |",
        f"| Total scenarios | {simulation_report.get('total_scenarios')} |",
        f"| Converged scenarios | {simulation_report.get('converged_scenarios')} |",
        f"| Convergence rate | {simulation_report.get('convergence_rate')} |",
        "",
        "## Source Traceability",
        "",
        "| task_type | records | rule_link_rate | simulation_link_rate | rationale_rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in trace_rows:
        lines.append(
            f"| {row['task_type']} | {row['records']} | {row['rule_link_rate']} | "
            f"{row['simulation_link_rate']} | {row['rationale_rate']} |"
        )

    if rule_taxonomy_expansion:
        lines.extend(["", "## Expanded Rule Taxonomy", ""])
        lines.append(f"- Status: `{rule_taxonomy_expansion.get('status')}`.")
        lines.append(f"- Rule cards before expansion: {rule_taxonomy_expansion.get('rule_count_before')}.")
        lines.append(f"- Rule cards after expansion: {rule_taxonomy_expansion.get('rule_count_after')}.")
        lines.append(f"- Canonical records changed: {rule_taxonomy_expansion.get('canonical_changed_records')}.")
        for rule_id, count in sorted((rule_taxonomy_expansion.get("new_rule_link_counts") or {}).items()):
            lines.append(f"- {rule_id}: {count} linked records.")

    lines.extend(["", "## Model Evidence", ""])
    if tfidf_report:
        lines.append(
            f"- TF-IDF baseline: test macro score {tfidf_report.get('test_macro_score')}, "
            f"OOD macro score {tfidf_report.get('ood_macro_score')}."
        )
    for report in transformer_reports:
        lines.append(
            f"- Transformer `{report.get('task_type')}`: test macro F1 "
            f"{report.get('test_evaluation', {}).get('macro_f1')}."
        )
    for report in generative_reports:
        task_types = report.get("task_types")
        if not isinstance(task_types, list) or not task_types:
            task_type = report.get("task_type")
            task_types = [task_type] if task_type else []
        objective = report.get("objective", "unknown")
        label = "Pretrained seq2seq generation" if objective == "pretrained_seq2seq_generation" else f"Non-formal generation ({objective})"
        lines.append(
            f"- {label} (`{', '.join(task_types)}`): "
            f"test token F1 {report.get('test_evaluation', {}).get('token_f1')}."
        )

    if external_topology_physical_envelope:
        lines.extend(["", "## External Topology Physical Envelope", ""])
        lines.append(f"- Status: `{external_topology_physical_envelope.get('status')}`.")
        lines.append(f"- Stress-source scenarios: {external_topology_physical_envelope.get('scenario_count')}.")
        lines.append(f"- Converged stress-source scenarios: {external_topology_physical_envelope.get('converged_count')}.")
        lines.append(
            f"- Formal external instruction records in release: {external_topology_physical_envelope.get('formal_external_instruction_records')}."
        )
        lines.append(
            f"- Formal external scenarios in release: {external_topology_physical_envelope.get('formal_external_scenarios')}."
        )
        for cls, count in sorted((external_topology_physical_envelope.get("envelope_counts") or {}).items()):
            lines.append(f"- {cls}: {count}.")

    if opf_closed_loop_assumption:
        lines.extend(["", "## OPF Closed-Loop Assumption Audit", ""])
        lines.append(f"- Status: `{opf_closed_loop_assumption.get('status')}`.")
        lines.append(f"- OPF-derived records: {opf_closed_loop_assumption.get('opf_record_count')}.")
        lines.append(f"- Unique OPF scenarios: {opf_closed_loop_assumption.get('unique_scenarios')}.")
        lines.append(f"- Tool-sequence pass rate: {opf_closed_loop_assumption.get('tool_sequence_pass_rate')}.")
        lines.append(f"- Closed-loop field pass rate: {opf_closed_loop_assumption.get('closed_loop_field_pass_rate')}.")

    if hard_boundary_benchmark:
        lines.extend(["", "## Hard Boundary Benchmark", ""])
        lines.append(f"- Status: `{hard_boundary_benchmark.get('status')}`.")
        lines.append(f"- Model: `{hard_boundary_benchmark.get('model')}`.")
        for task, metrics in sorted((hard_boundary_benchmark.get("tasks") or {}).items()):
            lines.append(
                f"- {task}: train {metrics.get('train_records')}, test {metrics.get('test_records')}, "
                f"macro-F1 {metrics.get('macro_f1')}, surface purity {metrics.get('surface_purity', {}).get('weighted_surface_label_purity')}."
            )

    lines.extend(["", "## Review Pipeline", ""])
    if review_report:
        lines.append(
            f"- Reviewed records: {review_report.get('reviewed_records')}; "
            f"accepted augmentations: {review_report.get('accepted_augmentations')}; "
            f"agreement rate: {review_report.get('agreement', {}).get('overall_agreement_rate')}."
        )
    else:
        lines.append("- No LLM review report was provided for this refresh run.")

    lines.extend(
        [
            "",
            "## Limits",
            "",
            "This release demonstrates schema integrity, traceability, split integrity, simulation linkage, and local trainability.",
            "Stable public archival identifiers must be added before external submission; independent domain review can further strengthen reuse confidence.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_checklist_doc(
    path: Path,
    audit: dict[str, Any],
    version: str,
    human_execution: dict[str, Any],
) -> None:
    version_label = format_version_label(version)
    review_status = (
        "PASS"
        if human_execution.get("status") == "external_human_review_completed"
        else "EXTERNAL PENDING"
    )
    lines = [
        "# Scientific Data Readiness Checklist",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"This checklist summarizes GridInstruct {version_label} against the current internal paper gate.",
        "",
        "| Area | Status | Evidence |",
        "| --- | --- | --- |",
        f"| Data scale | {audit.get('data_scale', {}).get('status', 'unknown').upper()} | {audit.get('data_scale', {}).get('total_records', 'n/a')} records across six tasks |",
        f"| Data quality | {audit.get('data_quality', {}).get('status', 'unknown').upper()} | validator pass={audit.get('data_quality', {}).get('validation_passed', 'n/a')}, near_duplicate_rate={audit.get('data_quality', {}).get('near_duplicate_rate', 'n/a')} |",
        f"| Machine-assisted consistency screen | RECORDED | screened={audit.get('llm_review_and_augmentation', {}).get('reviewed_records', 'n/a')}, machine decision agreement={audit.get('llm_review_and_augmentation', {}).get('agreement_rate', 'n/a')} |",
        f"| External human expert review | {review_status} | complete assignments={human_execution.get('complete_human_review_rows', 0)}/{human_execution.get('assignment_rows', 0)}, two-review samples={human_execution.get('samples_with_two_complete_reviews', 0)} |",
        f"| Model evidence | {audit.get('model_evidence', {}).get('status', 'unknown').upper()} | TF-IDF + transformer suite + pretrained seq2seq generation available |",
        f"| Paper readiness | {audit.get('paper_readiness', {}).get('status', 'unknown').upper()} | overall_status={audit.get('overall_status', 'unknown')} |",
        "",
        "## Next Actions",
        "",
    ]
    for action in audit.get("next_actions", []):
        lines.append(f"- {action}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_descriptor_audit_doc(
    path: Path,
    audit: dict[str, Any],
    metadata: dict[str, Any],
    human_execution: dict[str, Any],
) -> None:
    dataset = metadata.get("dataset", {})
    review_status = (
        "READY"
        if human_execution.get("status") == "external_human_review_completed"
        else "EXTERNAL PENDING"
    )
    lines = [
        "# Data Descriptor Audit",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "| Item | Artifact | Status | Notes |",
        "| --- | --- | --- | --- |",
        f"| Dataset snapshot | {dataset.get('dataset_path', 'n/a')} | READY | version={dataset.get('version')} records={dataset.get('record_count')} |",
        f"| Validation report | {metadata.get('validation', {}).get('path', 'n/a')} | {audit.get('data_quality', {}).get('status', 'unknown').upper()} | schema and semantic checks recorded |",
        f"| Machine screen | {metadata.get('review_pipeline', {}).get('machine_screen_report_path', 'n/a')} | RECORDED | machine-assisted consistency evidence only |",
        f"| External human review | {metadata.get('review_pipeline', {}).get('human_execution_report_path', 'n/a')} | {review_status} | {human_execution.get('complete_human_review_rows', 0)}/{human_execution.get('assignment_rows', 0)} complete assignments |",
        f"| Baseline evidence | benchmark/ | {audit.get('model_evidence', {}).get('status', 'unknown').upper()} | task-level TF-IDF, transformer, and pretrained seq2seq reports |",
        f"| Release metadata | metadata/dataset_metadata.json | {'READY' if audit.get('paper_readiness', {}).get('dataset_metadata_consistent') else 'NEEDS UPDATE'} | release-facing fields refreshed for the current snapshot |",
        f"| DOI / archival ids | metadata/dataset_metadata.json | {'READY' if audit.get('paper_readiness', {}).get('doi_ready') else 'PENDING'} | public archival identifiers are still required |",
        "",
        "## Remaining Gaps",
        "",
    ]
    for action in audit.get("next_actions", []):
        lines.append(f"- {action}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--source-dataset", default=None)
    parser.add_argument("--dataset-version", default="1.2")
    parser.add_argument("--train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--validation", default="data/v1.2_sd_core_validation_en.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--ood", default="data/v1.2_sd_core_ood_test_en.jsonl")
    parser.add_argument("--evaluation-report", default="benchmark/v1.2_evaluation_tasks.json")
    parser.add_argument("--validation-report", default="reports/data_validation_v1.2_sd_core.json")
    parser.add_argument("--review-report", default="reports/llm_review_report_v1.2_paper_plus7_cumulative.json")
    parser.add_argument("--human-review-execution-report", default="reports/expert_review_execution_check_v1.2_sd_core.json")
    parser.add_argument("--audit-report", default="reports/project_milestone_audit_v1.2_sd_core.json")
    parser.add_argument("--tfidf-report", default="benchmark/v1.2_sd_core_tfidf_report.json")
    parser.add_argument("--transformer-report-glob", default="benchmark/v1.2_sd_core_transformer*_report.json")
    parser.add_argument("--generative-report", default="benchmark/v1.2_sd_core_seq2seq_regqa_report.json")
    parser.add_argument("--generative-report-glob", default="benchmark/v1.2_sd_core_seq2seq_*_report.json")
    parser.add_argument("--external-topology-physical-envelope", default="reports/external_topology_physical_envelope_v1.2_sd_core.json")
    parser.add_argument("--opf-closed-loop-assumption", default="reports/opf_closed_loop_assumption_audit_v1.2_sd_core.json")
    parser.add_argument("--rule-taxonomy-expansion", default="reports/rule_taxonomy_expansion_v1.2_sd_core.json")
    parser.add_argument("--hard-boundary-benchmark", default="reports/hard_boundary_benchmark_v1.2_sd_core.json")
    parser.add_argument("--data-doi", default=None)
    parser.add_argument("--archived-code-release-doi", default=None)
    parser.add_argument("--repository-url", default=None)
    args = parser.parse_args()

    version = args.dataset_version
    version_label = format_version_label(version)
    dataset_path = args.dataset
    source_dataset_path = args.source_dataset
    split_paths = {
        "train": args.train,
        "validation": args.validation,
        "test": args.test,
        "ood_test": args.ood,
    }

    rows = read_jsonl(ROOT / dataset_path)
    summary = summarize_records(rows)
    validation_report = read_json(ROOT / args.validation_report)
    simulation_report = read_json(ROOT / "reports/simulation_report.json")
    review_report = read_json(ROOT / args.review_report) if (ROOT / args.review_report).exists() else None
    human_execution = (
        read_json(ROOT / args.human_review_execution_report)
        if (ROOT / args.human_review_execution_report).exists()
        else {}
    )
    audit_report = read_json(ROOT / args.audit_report) if (ROOT / args.audit_report).exists() else {}
    tfidf_report = read_json(ROOT / args.tfidf_report) if (ROOT / args.tfidf_report).exists() else None
    rules = read_json(ROOT / "rules/regulation_rules.json")
    archive_metadata_path = ROOT / "metadata/archive_metadata.json"
    archive_metadata = read_json(archive_metadata_path) if archive_metadata_path.exists() else {}
    repository_url = resolved_metadata_value(
        args.repository_url, archive_metadata.get("public_repository_url")
    )
    data_doi = resolved_metadata_value(args.data_doi, archive_metadata.get("data_doi"))
    archived_code_release_doi = resolved_metadata_value(
        args.archived_code_release_doi,
        archive_metadata.get("archived_code_release_doi"),
    )

    transformer_glob = args.transformer_report_glob or f"benchmark/v{version}_transformer*_report.json"
    generative_glob = args.generative_report_glob

    transformer_reports = []
    for path in sorted(ROOT.glob(transformer_glob)):
        transformer_reports.append(read_json(path))
    generative_reports = []
    seen_generative_paths: set[Path] = set()
    explicit_generative_path = ROOT / args.generative_report
    if explicit_generative_path.exists():
        generative_reports.append(read_json(explicit_generative_path))
        seen_generative_paths.add(explicit_generative_path.resolve())
    if generative_glob:
        for path in sorted(ROOT.glob(generative_glob)):
            resolved = path.resolve()
            if resolved in seen_generative_paths:
                continue
            generative_reports.append(read_json(path))
            seen_generative_paths.add(resolved)
    generative_reports = canonical_generative_reports(generative_reports)
    generative_report = generative_reports[0] if generative_reports else None
    external_topology_physical_envelope = (
        read_json(ROOT / args.external_topology_physical_envelope)
        if (ROOT / args.external_topology_physical_envelope).exists()
        else None
    )
    opf_closed_loop_assumption = (
        read_json(ROOT / args.opf_closed_loop_assumption)
        if (ROOT / args.opf_closed_loop_assumption).exists()
        else None
    )
    rule_taxonomy_expansion = (
        read_json(ROOT / args.rule_taxonomy_expansion)
        if (ROOT / args.rule_taxonomy_expansion).exists()
        else None
    )
    hard_boundary_benchmark = (
        read_json(ROOT / args.hard_boundary_benchmark)
        if (ROOT / args.hard_boundary_benchmark).exists()
        else None
    )

    annotation_policy = build_annotation_policy(version)
    expert_review_protocol = build_expert_review_protocol(review_report, human_execution, version)

    split_summary = split_integrity(split_paths)
    coverage_notes = split_coverage_notes(split_summary, sorted(summary["by_task"]))
    trace_summary = source_traceability(rows)
    trace_rows = source_traceability_rows(trace_summary)
    rule_rows = rule_dictionary_rows(rules)
    licenses = read_json(ROOT / "metadata/release_licenses.json")

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "title": f"GridInstruct {version_label}",
            "description": "Topology-grounded instruction dataset for power dispatch assistance and benchmark evaluation.",
            "version": version,
            "record_count": len(rows),
            "language": "English-derived natural-language fields with stable structured identifiers",
            "source_language": "mixed bilingual construction sources; English release validated separately",
            "source_dataset_path": source_dataset_path,
            "language_audit": "reports/english_translation_audit_v1.2_sd_core.json",
            "release_status": "internal paper-grade snapshot; public archival identifiers pending",
            "dataset_path": dataset_path,
            "split_paths": split_paths,
        },
        "record_summary": summary,
        "source_traceability": trace_summary,
        "split_integrity": split_summary,
        "validation": {
            "path": args.validation_report,
            "passed": validation_report.get("passed"),
            "near_duplicate_rate": validation_report.get("near_duplicate_rate"),
            "schema_errors": len(validation_report.get("schema_errors", [])),
            "duplicate_ids": len(validation_report.get("duplicate_ids", [])),
            "invalid_rule_links": len(validation_report.get("invalid_rule_links", [])),
            "invalid_scenario_links": len(validation_report.get("invalid_scenario_links", [])),
            "semantic_errors": len(validation_report.get("semantic_errors", [])),
        },
        "simulation": simulation_report,
        "review_pipeline": {
            "machine_screen_report_path": args.review_report,
            "machine_screened_records": review_report.get("reviewed_records") if review_report else 0,
            "machine_accepted_augmentations": review_report.get("accepted_augmentations") if review_report else 0,
            "machine_decision_agreement_rate": review_report.get("agreement", {}).get("overall_agreement_rate") if review_report else 0.0,
            "human_execution_report_path": args.human_review_execution_report,
            "human_execution_status": human_execution.get("status", "not_audited"),
            "human_complete_assignments": human_execution.get("complete_human_review_rows", 0),
        },
        "model_evidence": {
            "tfidf": summarize_model_report(tfidf_report),
            "transformer_reports": [summarize_model_report(report) for report in transformer_reports],
            "generative_latest": summarize_model_report(generative_report),
            "generative_reports": [summarize_model_report(report) for report in generative_reports],
            "hard_boundary_benchmark": hard_boundary_benchmark,
        },
        "availability": {
            "data_doi": data_doi,
            "code_repository_url": repository_url,
            "archived_code_release_doi": archived_code_release_doi,
            "data_license": licenses.get("data_license", {}).get("spdx", ""),
            "code_license": licenses.get("code_license", {}).get("spdx", ""),
        },
        "rule_dictionary": rule_rows,
        "rule_taxonomy_expansion": rule_taxonomy_expansion,
        "audit_reference": audit_report,
    }
    paper_outline_alignment = build_paper_outline_alignment(metadata, audit_report, review_report, human_execution)
    data_descriptor_audit = build_data_descriptor_audit(metadata, audit_report)

    write_json(ROOT / "metadata/annotation_policy.json", annotation_policy)
    write_json(ROOT / "metadata/expert_review_protocol.json", expert_review_protocol)
    write_json(ROOT / "metadata/paper_outline_alignment.json", paper_outline_alignment)
    write_json(ROOT / "metadata/data_descriptor_audit.json", data_descriptor_audit)
    write_json(ROOT / "metadata/split_integrity_report.json", split_summary)
    write_sample_records(ROOT / "examples/sample_records.jsonl", rows)

    manifest_paths = [
        dataset_path,
        args.train,
        args.validation,
        args.test,
        args.ood,
        "metadata/schema.json",
        "metadata/task_taxonomy.json",
        "metadata/data_dictionary.csv",
        "metadata/annotation_policy.json",
        "metadata/runtime_environment.json",
        "metadata/expert_review_protocol.json",
        "metadata/paper_outline_alignment.json",
        "metadata/data_descriptor_audit.json",
        "metadata/split_integrity_report.json",
        "metadata/release_licenses.json",
        "rules/regulation_rules.json",
        "examples/sample_records.jsonl",
        args.evaluation_report,
        args.tfidf_report,
        "reports/simulation_report.json",
        args.validation_report,
        args.audit_report,
        args.external_topology_physical_envelope,
        args.opf_closed_loop_assumption,
        args.rule_taxonomy_expansion,
        args.hard_boundary_benchmark,
        "figures/sd_core/fig_hard_boundary_benchmark.png",
    ] + [str(path.relative_to(ROOT)) for path in sorted(ROOT.glob(transformer_glob))]
    if source_dataset_path:
        manifest_paths.append(source_dataset_path)
    if generative_glob:
        manifest_paths.extend(str(path.relative_to(ROOT)) for path in sorted(ROOT.glob(generative_glob)))
    else:
        manifest_paths.append(args.generative_report)
    manifest = file_manifest(manifest_paths)
    metadata["file_manifest"] = manifest

    write_json(ROOT / "metadata/dataset_metadata.json", metadata)
    write_csv(ROOT / "metadata/file_manifest.csv", manifest)
    write_checksums(ROOT / "metadata/checksums_sha256.txt", manifest)
    write_csv(ROOT / "metadata/source_traceability.csv", trace_rows)
    write_csv(ROOT / "metadata/rule_dictionary.csv", rule_rows)
    write_json(ROOT / "reports/release_metadata_snapshot.json", metadata)

    write_data_records_doc(
        ROOT / "docs/DATA_RECORDS.md",
        version,
        dataset_path,
        split_paths,
        manifest,
        rules,
        summary,
        split_summary,
        coverage_notes,
    )
    write_technical_validation_doc(
        ROOT / "docs/TECHNICAL_VALIDATION.md",
        summary,
        validation_report,
        simulation_report,
        trace_rows,
        tfidf_report,
        transformer_reports,
        generative_reports,
        review_report,
        external_topology_physical_envelope,
        opf_closed_loop_assumption,
        rule_taxonomy_expansion,
        hard_boundary_benchmark,
    )
    write_checklist_doc(ROOT / "docs/SCIENTIFIC_DATA_READINESS_CHECKLIST.md", audit_report, version, human_execution)
    write_descriptor_audit_doc(ROOT / "docs/DATA_DESCRIPTOR_AUDIT.md", audit_report, metadata, human_execution)
    write_descriptor_draft_doc(
        ROOT / "docs/SCIENTIFIC_DATA_DESCRIPTOR_DRAFT.md",
        metadata,
        simulation_report,
        audit_report,
        human_execution,
    )
    write_availability_doc(ROOT / "docs/AVAILABILITY_AND_LIMITATIONS.md", metadata, audit_report, human_execution)
    write_annotation_policy_doc(ROOT / "docs/ANNOTATION_POLICY.md", annotation_policy)
    write_expert_review_protocol_doc(ROOT / "docs/EXPERT_REVIEW_PROTOCOL.md", expert_review_protocol)
    write_paper_outline_alignment_doc(ROOT / "docs/PAPER_OUTLINE_ALIGNMENT.md", paper_outline_alignment)
    write_usage_notes_doc(ROOT / "docs/USAGE_NOTES.md", dataset_path, split_paths, version)


if __name__ == "__main__":
    main()
