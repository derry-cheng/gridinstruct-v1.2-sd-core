#!/usr/bin/env python3
"""Migrate legacy dataset scenario links onto authoritative physical truth."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from augment_compliance_counterfactual_actions import OUTPUT_BY_LABEL, rationale_for
from generate_instruction_data import (
    active_security_issues,
    auxiliary_instruction_for,
    auxiliary_response_bundle,
    compliance_instruction_for,
    intent_prompt_for,
    intent_slots_for,
    issue_profile,
    make_auxiliary,
    make_compliance,
    make_intent,
    make_query,
    scenario_summary,
    tool_plan_for,
)
from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, write_json, write_jsonl
from validate_dataset import expected_compliance_label


SCENARIO_ID_PATTERN = re.compile(
    r"_(base_power_flow|generator_perturbation|n1_branch_outage|n2_branch_outage|"
    r"n1_transformer_outage|n1_generator_outage|n1_bus_outage)_load(\d+)"
)
SEVERITY_TEXT = {"normal": "常规", "alert": "告警", "emergency": "紧急"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def normalize_network(value: object) -> str:
    return str(value or "").lower().replace(" ", "")


def selection_key(scenario_id: str, representative: dict[str, Any]) -> tuple[str, str, float, str]:
    match = SCENARIO_ID_PATTERN.search(scenario_id)
    if not match:
        raise ValueError(f"cannot parse legacy scenario identity: {scenario_id}")
    return (
        normalize_network(representative.get("network_model")),
        match.group(1),
        int(match.group(2)) / 100.0,
        str((representative.get("metadata") or {}).get("issue_profile") or ""),
    )


def authoritative_key(scenario: dict[str, Any]) -> tuple[str, str, float, str]:
    return (
        normalize_network(scenario.get("network_model") or scenario.get("system")),
        str(scenario.get("contingency_type") or ""),
        round(float(scenario.get("load_level") or 1.0), 6),
        issue_profile(scenario),
    )


def scenario_family_key(scenario: dict[str, Any]) -> tuple[str, str]:
    key = authoritative_key(scenario)
    return key[0], key[1]


def choose_replacements(
    groups: dict[str, list[dict[str, Any]]],
    scenarios: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    pools: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        pools[scenario_family_key(scenario)].append(scenario)
    for rows in pools.values():
        rows.sort(key=lambda row: str(row["scenario_id"]))

    group_context: dict[str, dict[str, Any]] = {}
    for old_id, group_rows in groups.items():
        representative = group_rows[0]
        key = selection_key(old_id, representative)
        family = (key[0], key[1])
        family_pool = pools.get(family, [])
        if not family_pool:
            raise ValueError(
                f"no authoritative scenario in the same network/contingency "
                f"family for {old_id}: {family}"
            )
        old_severity = str((representative.get("metadata") or {}).get("severity_level") or "")

        def rank(row: dict[str, Any]) -> tuple[int, bool, float, str]:
            candidate_key = authoritative_key(row)
            same_load = abs(candidate_key[2] - key[2]) <= 1e-9
            same_issue = candidate_key[3] == key[3]
            tier = (
                0
                if same_load and same_issue
                else 1
                if same_load
                else 2
                if same_issue
                else 3
            )
            return (
                tier,
                str(row.get("severity_level") or "") != old_severity,
                abs(candidate_key[2] - key[2]),
                str(row["scenario_id"]),
            )

        group_context[old_id] = {
            "representative": representative,
            "key": key,
            "family": family,
            "old_severity": old_severity,
            "rank": rank,
            "pool": family_pool,
        }

    replacements: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    remaining = set(groups)
    # Protect scarce exact matches before allowing flexible groups to consume them.
    # Within every tier, groups with fewer available candidates are assigned first.
    for tier in range(4):
        eligible: list[tuple[int, str, list[dict[str, Any]]]] = []
        for old_id in remaining:
            context = group_context[old_id]
            candidates = [
                row
                for row in context["pool"]
                if context["rank"](row)[0] == tier
            ]
            if candidates:
                eligible.append((len(candidates), old_id, candidates))
        for _, old_id, candidates in sorted(
            eligible,
            key=lambda item: (item[0], item[1]),
        ):
            if old_id not in remaining:
                continue
            rank = group_context[old_id]["rank"]
            unused_candidates = [
                row
                for row in candidates
                if str(row["scenario_id"]) not in used
            ]
            if not unused_candidates:
                continue
            unused_candidates.sort(key=rank)
            replacement = unused_candidates[0]
            replacements[old_id] = replacement
            used.add(str(replacement["scenario_id"]))
            remaining.remove(old_id)

    if remaining:
        unresolved = []
        for old_id in sorted(remaining):
            context = group_context[old_id]
            available = sum(
                str(row["scenario_id"]) not in used for row in context["pool"]
            )
            unresolved.append(
                {
                    "legacy_scenario_id": old_id,
                    "family": list(context["family"]),
                    "available_unused": available,
                }
            )
        raise ValueError(
            "insufficient unused authoritative scenarios in network/contingency "
            f"families: {unresolved[:10]}"
        )

    audit: dict[str, dict[str, Any]] = {}
    for old_id in sorted(groups):
        replacement = replacements[old_id]
        context = group_context[old_id]
        key = context["key"]
        old_severity = context["old_severity"]
        rank = context["rank"]
        new_id = str(replacement["scenario_id"])
        new_key = authoritative_key(replacement)
        tier_index = rank(replacement)[0]
        tier_name = (
            "exact_load_and_issue"
            if tier_index == 0
            else "exact_load"
            if tier_index == 1
            else "same_issue_nearest_load"
            if tier_index == 2
            else "nearest_load"
        )
        audit[old_id] = {
            "replacement_scenario_id": new_id,
            "selection_key": list(key),
            "replacement_key": list(new_key),
            "selection_tier": tier_name,
            "old_load_level": key[2],
            "new_load_level": new_key[2],
            "load_level_changed": abs(key[2] - new_key[2]) > 1e-9,
            "issue_profile_changed": key[3] != new_key[3],
            "old_severity": old_severity,
            "new_severity": str(replacement.get("severity_level") or ""),
            "severity_changed": old_severity != str(replacement.get("severity_level") or ""),
            "replacement_scenario_sha256": stable_hash(replacement),
            "record_count": len(groups[old_id]),
        }
    return replacements, audit


def replace_text(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace_text(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: replace_text(item, old, new) for key, item in value.items()}
    return value


BASE_ID_PATTERNS = {
    "regulation_compliance_check": re.compile(
        r"^gridinstruct_v01_compliance_(\d+)$"
    ),
    "dispatcher_intent_tool_call": re.compile(
        r"^gridinstruct_v01_intent_(\d+)$"
    ),
    "intelligent_data_query": re.compile(r"^gridinstruct_v01_query_(\d+)$"),
    "auxiliary_decision": re.compile(r"^gridinstruct_v01_aux_(\d+)$"),
}
TASK_GENERATORS = {
    "regulation_compliance_check": make_compliance,
    "dispatcher_intent_tool_call": make_intent,
    "intelligent_data_query": make_query,
    "auxiliary_decision": make_auxiliary,
}
INDEX_PATTERN = re.compile(
    r"gridinstruct_v01_(?:compliance|intent|query|aux)_(\d+)"
)
TOPOLOGY_PROTOTYPE_PATTERN = re.compile(
    r"^gridinstruct_v01_(?:compliance|intent|query|aux)_\d+$"
)


def generation_index(row: dict[str, Any]) -> int:
    metadata = row.get("metadata") or {}
    candidates = (
        row.get("id"),
        metadata.get("source_record_id"),
        metadata.get("construction_prototype_id"),
    )
    for candidate in candidates:
        match = INDEX_PATTERN.search(str(candidate or ""))
        if match:
            return int(match.group(1))
    for key in ("stable_generation_index", "scenario_index"):
        value = metadata.get(key)
        if value is not None:
            return int(value)
    raise ValueError(f"cannot recover generation index for {row.get('id')}")


def rebuild_base_generated_row(
    row: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any] | None:
    task = str(row.get("task_type") or "")
    pattern = BASE_ID_PATTERNS.get(task)
    match = pattern.fullmatch(str(row.get("id") or "")) if pattern else None
    if match is None:
        return None
    variant = int((row.get("metadata") or {}).get("variant_index") or 0)
    generated = TASK_GENERATORS[task](int(match.group(1)), scenario, variant)
    generated["id"] = row["id"]
    generated_metadata = dict(generated.get("metadata") or {})
    for key, value in (row.get("metadata") or {}).items():
        if key not in {
            "severity_level",
            "issue_profile",
            "template_family",
            "variant_index",
        }:
            generated_metadata[key] = copy.deepcopy(value)
    generated["metadata"] = generated_metadata
    return generated


def migrate_row(row: dict[str, Any], old_id: str, scenario: dict[str, Any]) -> dict[str, Any]:
    new_id = str(scenario["scenario_id"])
    regenerated = rebuild_base_generated_row(row, scenario)
    migrated = (
        regenerated
        if regenerated is not None
        else replace_text(copy.deepcopy(row), old_id, new_id)
    )
    migrated["scenario_id"] = new_id
    migrated["source_simulation_case_id"] = new_id
    migrated["network_model"] = (
        str(scenario.get("network_model") or scenario.get("system") or "").upper().replace(" ", "")
    )

    metadata = dict(migrated.get("metadata") or {})
    old_severity = str(metadata.get("severity_level") or "")
    new_severity = str(scenario.get("severity_level") or "")
    if old_severity != new_severity:
        old_text = SEVERITY_TEXT.get(old_severity, old_severity)
        new_text = SEVERITY_TEXT.get(new_severity, new_severity)
        if old_text and new_text:
            migrated = replace_text(migrated, old_text, new_text)
        metadata = dict(migrated.get("metadata") or {})
    metadata.update(
        {
            "severity_level": new_severity,
            "issue_profile": issue_profile(scenario),
            "scenario_link_migrated_by": "migrate_dataset_scenario_links.py",
            "migrated_from_scenario_id": old_id,
            "replacement_scenario_id": new_id,
            "replacement_scenario_sha256": stable_hash(scenario),
            "scenario_semantics_regenerated": True,
        }
    )
    migrated["metadata"] = metadata
    input_obj = dict(migrated.get("input") or {})
    variant = int(metadata.get("variant_index") or 0)
    if "grid_state_summary" in input_obj:
        input_obj["grid_state_summary"] = scenario_summary(scenario, variant)
    if "observed_issues" in input_obj:
        input_obj["observed_issues"] = active_security_issues(scenario)
    if "scenario_id" in input_obj:
        input_obj["scenario_id"] = new_id
    if "analysis_focus" in input_obj:
        input_obj["analysis_focus"] = issue_profile(scenario)
    migrated["input"] = input_obj

    task = str(migrated.get("task_type") or "")
    if task == "regulation_compliance_check":
        action = str(input_obj.get("proposed_action") or "")
        operator_goal = str(input_obj.get("operator_goal") or "先处理最严重越限，再复核其他风险")
        category = str(metadata.get("action_category") or "") or None
        label = expected_compliance_label(scenario, action, category)
        if label is None:
            raise ValueError(f"cannot recompute compliance label for {row.get('id')}")
        idx = generation_index(row)
        migrated["instruction"] = compliance_instruction_for(
            scenario,
            action,
            operator_goal,
            idx,
            variant,
        )
        migrated["compliance_label"] = label
        migrated["output"] = OUTPUT_BY_LABEL[label]
        migrated["rationale"] = rationale_for(migrated, action, label)
    elif task == "dispatcher_intent_tool_call":
        intent = str(migrated.get("intent") or (migrated.get("output") or {}).get("intent") or "")
        idx = generation_index(row)
        utterance = intent_prompt_for(intent, scenario, idx, variant)
        migrated["instruction"] = utterance
        input_obj["utterance"] = utterance
        input_obj["grid_state_summary"] = scenario_summary(scenario, variant)
        migrated["input"] = input_obj
        slots = intent_slots_for(scenario, intent)
        migrated["slots"] = slots
        output = dict(migrated.get("output") or {})
        output.update({"intent": intent, "slots": slots})
        migrated["output"] = output
        migrated["tool_plan"] = tool_plan_for(intent, new_id)
    elif task == "intelligent_data_query":
        structured = dict(migrated.get("structured_query") or {})
        structured["scenario_id"] = new_id
        migrated["structured_query"] = structured
        output = dict(migrated.get("output") or {})
        output["structured_query"] = structured
        migrated["output"] = output
    elif task == "auxiliary_decision":
        idx = generation_index(row)
        operator_goal = str(input_obj.get("operator_goal") or "先处理最严重越限，再复核其他风险")
        decision_window = str(input_obj.get("decision_window") or "值长复核")
        response_mode = str(input_obj.get("response_mode") or "可逆操作优先")
        chosen, rejected, rationale = auxiliary_response_bundle(
            scenario,
            issue_profile(scenario),
            operator_goal,
            decision_window,
            response_mode,
            idx,
            variant,
        )
        migrated["instruction"] = auxiliary_instruction_for(
            scenario,
            operator_goal,
            decision_window,
            response_mode,
            idx,
            variant,
        )
        input_obj.update(
            {
                "operator_goal": operator_goal,
                "decision_window": decision_window,
                "response_mode": response_mode,
                "issue_profile": issue_profile(scenario),
                "grid_state_summary": scenario_summary(scenario, variant),
            }
        )
        migrated["input"] = input_obj
        migrated["output"] = chosen
        migrated["chosen_response"] = chosen
        migrated["rejected_response"] = rejected
        migrated["rationale"] = rationale
        migrated["tool_plan"] = [
            {"tool": "run_power_flow", "args": {"scenario_id": new_id}},
            {"tool": "run_opf_redispatch", "args": {"objective": "remove_violations"}},
        ]
    else:
        raise ValueError(f"unsupported scenario-linked task during migration: {task}")
    return migrated


def migrate(
    rows: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    truth = {str(row["scenario_id"]): row for row in scenarios}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        scenario_id = str(row.get("scenario_id") or row.get("source_simulation_case_id") or "")
        if scenario_id and scenario_id not in truth:
            groups[scenario_id].append(row)
    replacements, mapping = choose_replacements(groups, scenarios)

    migrated_rows = []
    migrated_count = 0
    for row in rows:
        old_id = str(row.get("scenario_id") or row.get("source_simulation_case_id") or "")
        replacement = replacements.get(old_id)
        if replacement is None:
            migrated_rows.append(row)
        else:
            migrated_rows.append(migrate_row(row, old_id, replacement))
            migrated_count += 1

    # Canonicalize network identifiers for every release row so by_network
    # aggregates cannot split the same topology across spaced/unspaced labels.
    for row in migrated_rows:
        if row.get("network_model") not in (None, ""):
            row["network_model"] = str(row["network_model"]).upper().replace(" ", "")
        metadata = dict(row.get("metadata") or {})
        if metadata.get("network_family"):
            metadata["network_family"] = str(metadata["network_family"]).upper().replace(" ", "")
            row["metadata"] = metadata

    before_ids = [str(row.get("id") or "") for row in rows]
    after_ids = [str(row.get("id") or "") for row in migrated_rows]
    after_id_set = set(after_ids)
    reclassified_prototypes: list[dict[str, str]] = []
    for row in migrated_rows:
        metadata = dict(row.get("metadata") or {})
        source_id = str(metadata.get("source_record_id") or "")
        if (
            source_id
            and source_id not in after_id_set
            and str(metadata.get("created_by") or "")
            == "append_topology_instruction_records.py"
            and str(metadata.get("augmentation_type") or "")
            == "extended_topology_instruction_v1"
            and TOPOLOGY_PROTOTYPE_PATTERN.fullmatch(source_id)
        ):
            metadata.pop("source_record_id", None)
            metadata["construction_prototype_id"] = source_id
            metadata["source_link_reclassified_by"] = (
                "migrate_dataset_scenario_links.py"
            )
            row["metadata"] = metadata
            reclassified_prototypes.append(
                {
                    "record_id": str(row.get("id") or ""),
                    "construction_prototype_id": source_id,
                }
            )
    after_truth_ids = {
        str(row.get("scenario_id") or row.get("source_simulation_case_id") or "")
        for row in migrated_rows
        if row.get("scenario_id") or row.get("source_simulation_case_id")
    }
    dangling = sorted(after_truth_ids - set(truth))
    source_ids = {
        str((row.get("metadata") or {}).get("source_record_id") or "")
        for row in migrated_rows
        if (row.get("metadata") or {}).get("source_record_id")
    }
    missing_source_ids = sorted(source_ids - after_id_set)
    # Construction prototypes are allowed only as explicit construction_provenance fields;
    # they must never appear as unresolved source_record_id parents.
    prototype_ids = {
        str((row.get("metadata") or {}).get("construction_prototype_id") or "")
        for row in migrated_rows
        if (row.get("metadata") or {}).get("construction_prototype_id")
    }
    hard_gates = {
        "all_legacy_groups_mapped": len(mapping) == len(groups),
        "record_cardinality_preserved": len(rows) == len(migrated_rows),
        "record_ids_preserved": before_ids == after_ids and len(after_ids) == len(set(after_ids)),
        "task_distribution_preserved": Counter(row.get("task_type") for row in rows)
        == Counter(row.get("task_type") for row in migrated_rows),
        "all_scenario_links_authoritative": not dangling,
        "all_source_record_links_resolved": not missing_source_ids,
        "every_mapped_group_used": migrated_count == sum(len(group) for group in groups.values()),
        "authoritative_scenarios_not_reused": len(
            {
                item["replacement_scenario_id"]
                for item in mapping.values()
            }
        )
        == len(mapping),
        "all_migrated_rows_regenerated_from_authoritative_semantics": all(
            (row.get("metadata") or {}).get("scenario_semantics_regenerated") is True
            for row in migrated_rows
            if (row.get("metadata") or {}).get("scenario_link_migrated_by")
        ),
        "construction_prototypes_are_not_source_parents": not (prototype_ids & source_ids),
    }
    tier_counts = Counter(item["selection_tier"] for item in mapping.values())
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "fail",
        "records_before": len(rows),
        "records_after": len(migrated_rows),
        "migrated_record_count": migrated_count,
        "migrated_scenario_group_count": len(groups),
        "severity_changed_group_count": sum(item["severity_changed"] for item in mapping.values()),
        "load_level_changed_group_count": sum(
            item["load_level_changed"] for item in mapping.values()
        ),
        "issue_profile_changed_group_count": sum(
            item["issue_profile_changed"] for item in mapping.values()
        ),
        "selection_tier_counts": dict(tier_counts),
        "task_counts_migrated": dict(
            Counter(
                row.get("task_type")
                for row in migrated_rows
                if (row.get("metadata") or {}).get("scenario_link_migrated_by")
            )
        ),
        "hard_gates": hard_gates,
        "dangling_scenario_ids": dangling,
        "missing_source_record_ids": missing_source_ids,
        "construction_prototype_count": len(prototype_ids),
        "reclassified_construction_prototype_count": len(reclassified_prototypes),
        "reclassified_construction_prototypes": reclassified_prototypes,
        "scenario_mapping": mapping,
    }
    if report["status"] != "pass":
        raise ValueError(f"scenario-link migration gates failed: {hard_gates}")
    return migrated_rows, report


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dirs(path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    write_jsonl(temporary, rows)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--output-dataset", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    args = parser.parse_args()

    dataset_path = ROOT / args.dataset
    scenario_path = ROOT / args.scenarios
    rows = read_jsonl(dataset_path)
    scenarios = read_json(scenario_path)
    migrated_rows, report = migrate(rows, scenarios)
    report.update(
        {
            "dataset": args.dataset,
            "dataset_sha256": sha256(dataset_path),
            "scenarios": args.scenarios,
            "scenarios_sha256": sha256(scenario_path),
            "output_dataset": args.output_dataset,
            "output_projection_sha256": stable_hash(migrated_rows),
        }
    )
    atomic_jsonl(ROOT / args.output_dataset, migrated_rows)
    report["output_dataset_sha256"] = sha256(ROOT / args.output_dataset)
    write_json(ROOT / args.report_json, report)
    lines = [
        "# Dataset Scenario-Link Migration",
        "",
        f"Status: `{report['status']}`",
        f"Records: {report['records_before']} -> {report['records_after']}",
        f"Migrated records: {report['migrated_record_count']}",
        f"Migrated scenario groups: {report['migrated_scenario_group_count']}",
        f"Groups with corrected severity: {report['severity_changed_group_count']}",
        "",
        "## Hard gates",
        "",
        *[f"- `{key}`: {value}" for key, value in report["hard_gates"].items()],
    ]
    (ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "migrated_records": report["migrated_record_count"]}))


if __name__ == "__main__":
    main()
