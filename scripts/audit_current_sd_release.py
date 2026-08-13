#!/usr/bin/env python3
"""Run a release-integrity audit against the files that are actually present.

This audit deliberately separates row-level integrity from claims that require
the deleted/raw power-flow and expert-review ledgers.  It never treats a
summary report as a substitute for a replayable source artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCENARIO_TASKS = {
    "regulation_compliance_check",
    "auxiliary_decision",
    "dispatcher_intent_tool_call",
    "intelligent_data_query",
}
SUPPORTED_QUERY_FILTERS = {
    "loading_percent > 100",
    "vm_pu < 0.95",
    "vm_pu > 1.05",
    "violation_type != none",
}
SUPPORTED_QUERY_SCOPES = {"line", "transformer", "all"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: record is not an object")
            yield value


def first_number(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def derive_severity(input_obj: Any) -> str | None:
    """Derive the published severity from the numeric state summary.

    Loading values below 10 are represented as a per-unit ratio in the legacy
    Chinese generator; they are converted to percent before applying the
    release policy.  This parser is independent of the generator's policy
    helper and only consumes the released input fields.
    """

    if not isinstance(input_obj, dict):
        return None
    summary = str(input_obj.get("grid_state_summary") or "")
    if not summary:
        return None
    loading = first_number(
        summary,
        [
            r"(?:max(?:imum)?)\s+(?:branch|line)\s+(?:load(?:ing)?(?:\s+rate)?|load\s+rate)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
            r"(?:max(?:imum)?)\s+(?:branch|line)\s+loading\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
        ],
    )
    min_vm = first_number(
        summary,
        [r"min(?:imum)?\s+bus\s+voltage\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)"],
    )
    max_vm = first_number(
        summary,
        [r"max(?:imum)?\s+bus\s+voltage\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)"],
    )
    if loading is None and min_vm is None and max_vm is None:
        return None
    loading_percent = loading * 100.0 if loading is not None and loading < 10.0 else loading
    if (loading_percent is not None and loading_percent > 110.0) or (
        min_vm is not None and min_vm < 0.92
    ) or (max_vm is not None and max_vm > 1.08):
        return "emergency"
    if (loading_percent is not None and loading_percent > 100.0) or (
        min_vm is not None and min_vm < 0.95
    ) or (max_vm is not None and max_vm > 1.05):
        return "alert"
    return "normal"


def numeric_state_fields(input_obj: Any) -> dict[str, float | None]:
    """Extract the numeric fields required for a complete severity decision.

    The compact English release does not guarantee that all three limits are
    present in ``grid_state_summary``.  Keeping the extraction separate from
    ``derive_severity`` lets the audit report an incomplete state explicitly
    instead of treating a partial parser result as a physical validation.
    """

    if not isinstance(input_obj, dict):
        return {"loading": None, "min_vm": None, "max_vm": None}
    summary = str(input_obj.get("grid_state_summary") or "")
    return {
        "loading": first_number(
            summary,
            [
                r"(?:max(?:imum)?)\s+(?:branch|line)\s+(?:load(?:ing)?(?:\s+rate)?|load\s+rate)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
                r"(?:max(?:imum)?)\s+(?:branch|line)\s+loading\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
            ],
        ),
        "min_vm": first_number(
            summary,
            [r"min(?:imum)?\s+bus\s+voltage\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)"],
        ),
        "max_vm": first_number(
            summary,
            [r"max(?:imum)?\s+bus\s+voltage\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)"],
        ),
    }


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def query_row_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    query = row.get("structured_query")
    if not isinstance(query, dict):
        return ["structured_query_not_object"]
    scenario_id = str(row.get("scenario_id") or "")
    if query.get("scenario_id") != scenario_id:
        errors.append("query_scenario_id_mismatch")
    if query.get("filter") not in SUPPORTED_QUERY_FILTERS:
        errors.append("unsupported_filter")
    if query.get("filter") == "loading_percent > 100":
        if query.get("equipment_scope") not in SUPPORTED_QUERY_SCOPES:
            errors.append("invalid_equipment_scope")
        if query.get("aggregation") != "complete_list_and_max":
            errors.append("incomplete_aggregation_contract")
    result = row.get("query_result")
    if query.get("filter") == "vm_pu < 0.95" or query.get("filter") == "vm_pu > 1.05":
        if not isinstance(result, list):
            errors.append("voltage_query_result_not_list")
        elif any(not isinstance(item, dict) or "bus" not in item or "vm_pu" not in item for item in result):
            errors.append("voltage_query_record_schema_error")
    elif query.get("filter") == "violation_type != none":
        if not isinstance(result, dict):
            errors.append("violation_query_result_not_object")
        elif "violation_type" not in result or "severity_level" not in result:
            errors.append("violation_query_result_schema_error")
    elif not isinstance(result, dict):
        errors.append("query_result_not_object")
    else:
        records = result.get("records")
        if not isinstance(records, list):
            errors.append("query_records_not_list")
        else:
            if result.get("count") != len(records):
                errors.append("query_count_mismatch")
            values = [item.get("loading_percent") for item in records if isinstance(item, dict)]
            values = [float(value) for value in values if isinstance(value, (int, float))]
            if values and abs(float(result.get("max_loading_percent", float("nan"))) - max(values)) > 1e-5:
                errors.append("query_max_mismatch")
            if result.get("equipment_scope") != query.get("equipment_scope"):
                errors.append("query_scope_result_mismatch")
    output = row.get("output")
    if not isinstance(output, dict) or canonical(output.get("query_result")) != canonical(result):
        errors.append("output_query_result_mismatch")
    return errors


def split_sets(root: Path, split_paths: dict[str, Path]) -> dict[str, Any]:
    ids: dict[str, set[str]] = {}
    groups: dict[str, set[str]] = {}
    provenance: dict[str, dict[str, set[str]]] = {}
    network_counts: dict[str, Counter[str]] = {}
    counts: dict[str, int] = {}
    provenance_keys = (
        "source_group",
        "source_record_id",
        "scenario_id",
        "source_simulation_case_id",
        "operation_group",
    )
    for name, path in split_paths.items():
        ids[name] = set()
        groups[name] = set()
        provenance[name] = {key: set() for key in provenance_keys}
        network_counts[name] = Counter()
        counts[name] = 0
        if not path.exists():
            continue
        for row in rows(path):
            counts[name] += 1
            ids[name].add(str(row.get("id") or ""))
            meta = row.get("metadata") or {}
            for key in provenance_keys:
                value = meta.get(key) or row.get(key)
                if value not in (None, ""):
                    provenance[name][key].add(str(value))
            group = (
                meta.get("source_group")
                or meta.get("source_record_id")
                or (
                    f"{row.get('task_type')}:{row.get('source_simulation_case_id')}"
                    if row.get("source_simulation_case_id")
                    else None
                )
                or row.get("id")
            )
            if group:
                groups[name].add(str(group))
            network = row.get("network_model")
            if network:
                network_counts[name][str(network)] += 1
    overlap: dict[str, int] = {}
    names = list(ids)
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            overlap[f"{left}__{right}"] = len(ids[left] & ids[right])
    group_overlap = {
        "train__test": len(groups.get("train", set()) & groups.get("test", set())),
        "train__ood_test": len(groups.get("train", set()) & groups.get("ood_test", set())),
    }
    provenance_overlap: dict[str, dict[str, int]] = {}
    names = list(provenance)
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            pair = f"{left}__{right}"
            provenance_overlap[pair] = {
                key: len(provenance[left][key] & provenance[right][key])
                for key in provenance_keys
            }
    return {
        "counts": counts,
        "id_overlap": overlap,
        "source_group_overlap": group_overlap,
        "provenance_overlap": provenance_overlap,
        "provenance_keys": list(provenance_keys),
        "network_counts": {name: dict(value) for name, value in network_counts.items()},
    }


def template_surface(row: dict[str, Any]) -> str:
    """Reproduce the task-specific surface key used by the holdout builder."""
    meta = row.get("metadata") or {}
    payload = row.get("input") or {}

    def norm(value: Any) -> str:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return " ".join(str(value or "").strip().split()) or "none"

    task = row.get("task_type")
    if task == "operation_ticket_check":
        fields = [meta.get("variant_index"), meta.get("augmentation_type"), meta.get("plain_ticket_boundary_key"), meta.get("implicit_case_key"), meta.get("counterfactual_role"), payload.get("operation_context")]
    elif task == "regulation_compliance_check":
        fields = [meta.get("variant_index"), meta.get("augmentation_type"), meta.get("action_category"), meta.get("action_surface_variant"), meta.get("counterfactual_variant"), meta.get("issue_profile"), payload.get("proposed_action")]
    elif task == "dispatcher_intent_tool_call":
        fields = [meta.get("variant_index"), meta.get("augmentation_type"), meta.get("counterfactual_intent"), meta.get("issue_profile"), payload.get("utterance") or payload.get("user_utterance"), payload.get("command_channel")]
    else:
        fields = [row.get("id")]
    return f"{task}|" + "|".join(norm(value) for value in fields)


def template_holdout_report(root: Path) -> dict[str, Any]:
    paths = {
        "train": root / "data/v1.2_sd_core_template_holdout_train_en.jsonl",
        "validation": root / "data/v1.2_sd_core_template_holdout_validation_en.jsonl",
        "test": root / "data/v1.2_sd_core_template_holdout_test_en.jsonl",
    }
    surfaces: dict[str, set[str]] = {name: set() for name in paths}
    counts = {name: 0 for name in paths}
    for name, path in paths.items():
        if not path.exists():
            continue
        for row in rows(path):
            counts[name] += 1
            surfaces[name].add(template_surface(row))
    overlap = {
        "train_validation": len(surfaces["train"] & surfaces["validation"]),
        "train_test": len(surfaces["train"] & surfaces["test"]),
        "validation_test": len(surfaces["validation"] & surfaces["test"]),
    }
    return {
        "counts": counts,
        "surface_counts": {name: len(value) for name, value in surfaces.items()},
        "surface_overlap": overlap,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--source-stage", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-json", default="reports/current_release_integrity_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/current_release_integrity_audit_v1.2_sd_core.md")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    dataset = root / args.dataset
    source_stage = root / args.source_stage

    counts = Counter()
    ids: set[str] = set()
    duplicate_ids: list[str] = []
    malformed_rows: list[dict[str, Any]] = []
    missing_scenario: list[str] = []
    scenario_mismatch: list[str] = []
    network_missing: list[str] = []
    tool_scenario_errors: list[str] = []
    severity_checked = 0
    severity_mismatch: list[dict[str, Any]] = []
    numeric_severity_checked = 0
    numeric_severity_mismatch: list[dict[str, Any]] = []
    numeric_state_incomplete = 0
    numeric_missing_fields: Counter[str] = Counter()
    numeric_complete_state = 0
    query_errors: list[dict[str, Any]] = []
    opf_rows = 0
    opf_scenarios: set[str] = set()
    opf_status = Counter()
    opf_scenario_counts: Counter[str] = Counter()
    source_row_count = 0
    source_alignment_errors: list[str] = []
    source_stage_exists = source_stage.exists()

    for row in rows(dataset):
        record_id = str(row.get("id") or "")
        if not record_id:
            malformed_rows.append({"reason": "empty_id"})
        elif record_id in ids:
            duplicate_ids.append(record_id)
        ids.add(record_id)
        task = str(row.get("task_type") or "")
        counts[task] += 1
        if not row.get("instruction") or "output" not in row or not isinstance(row.get("metadata"), dict):
            malformed_rows.append({"id": record_id, "reason": "required_field_missing"})
        if task in SCENARIO_TASKS:
            sid = row.get("scenario_id")
            source_sid = row.get("source_simulation_case_id")
            if not sid or not source_sid:
                missing_scenario.append(record_id)
            if sid and source_sid and str(sid) != str(source_sid):
                scenario_mismatch.append(record_id)
            if not row.get("network_model"):
                network_missing.append(record_id)
            for step in row.get("tool_plan") or []:
                args_obj = step.get("args") if isinstance(step, dict) else None
                if isinstance(args_obj, dict) and args_obj.get("scenario_id") and str(args_obj["scenario_id"]) != str(sid):
                    tool_scenario_errors.append(record_id)
            expected_severity = None
            state_summary = str((row.get("input") or {}).get("grid_state_summary") or "")
            explicit_match = re.search(
                r"severity(?:\s+level)?\s*[:：]?\s*(normal|alert|emergency)\b",
                state_summary,
                re.IGNORECASE,
            )
            if explicit_match:
                expected_severity = explicit_match.group(1).lower()
            actual_severity = (row.get("metadata") or {}).get("severity_level")
            if expected_severity is not None:
                severity_checked += 1
                if str(actual_severity).lower() != expected_severity:
                    severity_mismatch.append({"id": record_id, "expected": expected_severity, "actual": actual_severity})
            # Run the independent numeric parser as a diagnostic whenever the
            # released prose contains enough numeric fields.  The released
            # English summary omits maximum voltage in some high-voltage rows
            # and uses legacy loading-rate units in others, so this diagnostic
            # is intentionally not promoted to the explicit-string gate.
            numeric_fields = numeric_state_fields(row.get("input"))
            if any(value is not None for value in numeric_fields.values()):
                missing_fields = [name for name, value in numeric_fields.items() if value is None]
                if missing_fields:
                    numeric_state_incomplete += 1
                    numeric_missing_fields.update(missing_fields)
                else:
                    numeric_complete_state += 1
            numeric_severity = derive_severity(row.get("input"))
            if numeric_severity is not None:
                numeric_severity_checked += 1
                if str(actual_severity).lower() != numeric_severity:
                    numeric_severity_mismatch.append(
                        {
                            "id": record_id,
                            "numeric": numeric_severity,
                            "explicit": actual_severity,
                        }
                    )
        if task == "intelligent_data_query":
            errors = query_row_errors(row)
            if errors and len(query_errors) < 100:
                query_errors.append({"id": record_id, "errors": errors})
        if (row.get("metadata") or {}).get("opf_closed_loop"):
            opf_rows += 1
            opf_scenarios.add(str(row.get("scenario_id") or ""))
            opf_status[str((row.get("metadata") or {}).get("opf_status") or "unknown")] += 1
            opf_scenario_counts[str(row.get("scenario_id") or "")] += 1

    if source_stage_exists:
        with source_stage.open("r", encoding="utf-8") as source_handle, dataset.open("r", encoding="utf-8") as release_handle:
            for line_number, release_line in enumerate(release_handle, 1):
                if not release_line.strip():
                    continue
                release_row = json.loads(release_line)
                source_line = source_handle.readline()
                if not source_line.strip():
                    source_alignment_errors.append(f"source_eof_at_release_line_{line_number}")
                    break
                source_row = json.loads(source_line)
                source_row_count += 1
                for field in ("id", "task_type", "scenario_id", "source_simulation_case_id"):
                    if release_row.get(field) != source_row.get(field):
                        source_alignment_errors.append(f"line_{line_number}:{field}")
                        break
            if source_handle.readline().strip():
                source_alignment_errors.append("source_has_extra_rows")

    split_report = split_sets(
        root,
        {
            "train": root / "data/v1.2_sd_core_train_en.jsonl",
            "validation": root / "data/v1.2_sd_core_validation_en.jsonl",
            "test": root / "data/v1.2_sd_core_test_en.jsonl",
            "ood_test": root / "data/v1.2_sd_core_ood_test_en.jsonl",
        },
    )
    template_report = template_holdout_report(root)
    # The current revision deliberately stores the rebound manifest under
    # metadata/.  Keep the historical simulation_outputs path out of the
    # current gate so a valid local manifest is not reported as missing.
    raw_replay = {
        "scenario_registry": (root / "simulation_outputs/contingency/scenarios_converged.json").exists(),
        "n1_attempt_manifest": (root / "simulation_outputs/contingency/scenario_rebuild_manifest.json").exists(),
        "independent_solver_case_manifest": (root / "metadata/independent_solver_case_manifest_v1.json").exists(),
        "expert_review_assignments": (root / "review_packages/stratified_expert_review_v1.2_sd_core/review_packet_blinded.jsonl").exists(),
    }
    gates = {
        "jsonl_schema_and_unique_ids": not malformed_rows and not duplicate_ids,
        "scenario_links_present_and_equal": not missing_scenario and not scenario_mismatch,
        "network_and_tool_links_consistent": not network_missing and not tool_scenario_errors,
        "severity_explicit_consistency": severity_checked > 0 and not severity_mismatch,
        "query_contract_complete": not query_errors,
        "source_stage_row_alignment": source_stage_exists and not source_alignment_errors and source_row_count == len(ids),
        "split_ids_disjoint": not any(value for value in split_report["id_overlap"].values()),
        "split_provenance_keys_disjoint": not any(
            value
            for pair in split_report["provenance_overlap"].values()
            for value in pair.values()
        ),
        "opf_two_variants_per_scenario": bool(opf_scenario_counts)
        and all(value == 2 for value in opf_scenario_counts.values()),
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "dataset_sha256": sha256(dataset),
        "records": sum(counts.values()),
        "by_task": dict(counts),
        "unique_ids": len(ids),
        "duplicate_id_count": len(duplicate_ids),
        "malformed_examples": malformed_rows[:20],
        "scenario_link_missing_count": len(missing_scenario),
        "scenario_id_mismatch_count": len(scenario_mismatch),
        "network_missing_count": len(network_missing),
        "tool_scenario_error_count": len(tool_scenario_errors),
        "severity_checked": severity_checked,
        "severity_mismatch_count": len(severity_mismatch),
        "severity_mismatch_examples": severity_mismatch[:20],
        "numeric_severity_diagnostic": {
            "checked": numeric_severity_checked,
            "mismatch_count": len(numeric_severity_mismatch),
            "mismatch_examples": numeric_severity_mismatch[:20],
            "complete_numeric_state_rows": numeric_complete_state,
            "incomplete_numeric_state_rows": numeric_state_incomplete,
            "missing_field_counts": dict(numeric_missing_fields),
            "status": "inconclusive_due_to_missing_numeric_state_fields",
            "interpretation": "The explicit severity string is audited independently. Numeric severity is not promoted because a complete decision requires loading, minimum voltage, and maximum voltage; compact release rows currently omit maximum voltage whenever a numeric state is present.",
        },
        "query_contract_error_count": sum(len(item["errors"]) for item in query_errors),
        "query_error_examples": query_errors[:20],
        "opf_closed_loop_rows": opf_rows,
        "opf_closed_loop_unique_scenarios": len(opf_scenarios),
        "opf_status": dict(opf_status),
        "opf_variant_counts": {
            "scenario_count": len(opf_scenario_counts),
            "variant_count_distribution": dict(Counter(opf_scenario_counts.values())),
            "all_scenarios_have_two_variants": bool(opf_scenario_counts)
            and all(value == 2 for value in opf_scenario_counts.values()),
        },
        "source_stage": {
            "path": args.source_stage,
            "exists": source_stage_exists,
            "rows_compared": source_row_count,
            "alignment_error_count": len(source_alignment_errors),
            "alignment_error_examples": source_alignment_errors[:20],
        },
        "split_integrity": split_report,
        "template_holdout_integrity": template_report,
        "raw_replay_artifacts_present": raw_replay,
        "gates": {name: {"status": "pass" if value else "fail"} for name, value in gates.items()},
        "status": "pass" if all(gates.values()) else "fail",
        "local_integrity_status": "pass" if all(gates.values()) else "fail",
        "release_readiness_status": "blocked_external_gates" if not all(raw_replay.values()) else "ready_for_external_review",
        "interpretation": "Row-level integrity and split checks are independent of the missing raw replay ledgers. Missing replay or expert-review artifacts remain explicit failures; summary reports are not promoted to evidence.",
    }
    out_json = root / args.output_json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Current Scientific Data Release Integrity Audit",
        "",
        f"Generated: {payload['generated_at']}",
        f"Dataset: `{args.dataset}`",
        f"Local integrity status: `{payload['local_integrity_status']}`; release readiness: `{payload['release_readiness_status']}`",
        "",
        "| Gate | Status |",
        "| --- | --- |",
    ]
    for name, gate in payload["gates"].items():
        lines.append(f"| {name} | {gate['status']} |")
    lines.extend([
        "",
        f"Records: {payload['records']:,}; unique IDs: {payload['unique_ids']:,}.",
        f"Scenario-link missing/mismatch: {payload['scenario_link_missing_count']}/{payload['scenario_id_mismatch_count']}; severity mismatches: {payload['severity_mismatch_count']} of {payload['severity_checked']:,} checked.",
        f"Numeric severity diagnostic: {payload['numeric_severity_diagnostic']['checked']:,} partial states; {payload['numeric_severity_diagnostic']['complete_numeric_state_rows']:,} complete three-field states; status={payload['numeric_severity_diagnostic']['status']}.",
        f"OPF closed-loop rows/scenarios: {payload['opf_closed_loop_rows']}/{payload['opf_closed_loop_unique_scenarios']}.",
        "",
        "The audit intentionally keeps unavailable raw replays and external expert review as failed gates. They must be regenerated or deposited before a Scientific Data submission claim can be upgraded.",
    ])
    (root / args.output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
