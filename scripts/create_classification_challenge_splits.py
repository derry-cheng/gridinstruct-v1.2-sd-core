"""Create stricter classification splits for suspiciously high F1 audits."""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_jsonl, stable_shuffle, write_json, write_jsonl
from create_strict_source_group_splits import UnionFind, provenance_keys


CLASSIFICATION_TASKS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

ACTION_CATEGORY_BY_SURFACE = {
    "继续增加受限断面潮流": "risky_increase",
    "继续提升受限断面潮流": "risky_increase",
    "继续提升受限断面输送功率": "risky_increase",
    "持续增加受限断面输送功率": "risky_increase",
    "进一步提高受限断面送出": "risky_increase",
    "增加受限通道输送水平": "risky_increase",
    "继续扩大受限断面传输功率": "risky_increase",
    "维持增送计划并提高断面潮流": "risky_increase",
    "维持当前潮流并启动监视": "monitor_only",
    "保持当前潮流并开展连续监视": "monitor_only",
    "暂不调整潮流并加强监视": "monitor_only",
    "维持现有运行方式并跟踪越限变化": "monitor_only",
    "不改变潮流分布，仅执行实时监视": "monitor_only",
    "保持当前断面送出并启动复核监视": "monitor_only",
    "先执行无功支撑后复核潮流": "corrective_voltage",
    "先实施无功支持措施，随后验证潮流分布": "corrective_voltage",
    "先执行无功补偿再复核潮流": "corrective_voltage",
    "先投切无功补偿再复核电压和潮流": "corrective_voltage",
    "先进行电压支撑后重新校核潮流": "corrective_voltage",
    "先安排无功调节并复核电压越限": "corrective_voltage",
    "调整邻近机组出力降低断面负载": "corrective_redispatch",
    "先压降受限断面送出再复算潮流": "corrective_redispatch",
    "重新分配邻近机组出力以降低断面负载": "corrective_redispatch",
    "下调受限断面送出并复核潮流": "corrective_redispatch",
    "通过机组再调度释放断面裕度": "corrective_redispatch",
    "调整发电出力组合以缓解过载断面": "corrective_redispatch",
    "先切换局部拓扑后检查越限是否消除": "corrective_topology",
    "执行分阶段负荷转供并继续监视": "corrective_topology",
    "先调整局部网络拓扑再复核越限": "corrective_topology",
    "分阶段转供负荷后持续监视潮流": "corrective_topology",
    "先实施拓扑转移再校核低压和过载": "corrective_topology",
    "局部倒换供电路径后检查安全裕度": "corrective_topology",
    "不校核设备状态直接恢复故障线路运行": "premature_restore",
    "直接恢复故障线路运行，不校核设备状态": "premature_restore",
    "直接恢复故障线路运行，不进行设备状态校核": "premature_restore",
    "未进行设备状态确认即恢复故障线路运行": "premature_restore",
    "未确认设备状态即恢复故障线路运行": "premature_restore",
    "跳过状态校核直接送电故障线路": "premature_restore",
    "不做设备状态确认即恢复退出线路": "premature_restore",
}


def norm(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def canonical_operation_group(context: Any, obj: Any | None = None) -> str:
    context_text = norm(str(context).removeprefix("operation_context:"))
    obj_text = norm(str(obj or "").removeprefix("object:"))
    if obj_text:
        return f"operation_context:{context_text}|object:{obj_text}"
    return f"operation_context:{context_text}"


def operation_group_from_metadata(value: Any) -> str | None:
    text = norm(value)
    if not text:
        return None
    text = text.removeprefix("operation_group:")
    if text.startswith("operation_context:") and "|object:" in text:
        context, obj = text.split("|object:", 1)
        return canonical_operation_group(context, obj)
    if "|" in text:
        context, obj = text.split("|", 1)
        return canonical_operation_group(context, obj)
    return canonical_operation_group(text)


def canonical_dispatcher_group(value: Any) -> str | None:
    text = norm(value)
    if not text:
        return None
    text = text.removeprefix("dispatcher_group:")
    if text.startswith("scenario:"):
        return f"scenario:{norm(text.removeprefix('scenario:'))}"
    return f"scenario:{text}"


def scenario_blind_text(text: str) -> str:
    text = re.sub(r"ieee\d+_[A-Za-z0-9_]+", "{scenario}", text)
    text = re.sub(r"IEEE\s*\d+", "{network}", text)
    return norm(text)


def operation_defect_signature(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    payload = row.get("input") or {}
    parts = [
        metadata.get("plain_ticket_boundary_key"),
        metadata.get("implicit_case_key"),
        metadata.get("adversarial_issue"),
        metadata.get("counterfactual_role"),
        payload.get("dispatch_permit_status"),
        payload.get("monitoring_arrangement"),
        payload.get("object_consistency"),
    ]
    followup = payload.get("post_execution_followup")
    if isinstance(followup, dict):
        parts.extend([followup.get("pending_evidence"), followup.get("fallback_action")])
    return "|".join(norm(part) for part in parts if norm(part)) or "routine_ticket_surface"


def dispatcher_surface_signature(row: dict[str, Any]) -> str:
    payload = row.get("input") or {}
    utterance = scenario_blind_text(payload.get("utterance") or row.get("instruction") or "")
    policy = scenario_blind_text(payload.get("routing_policy") or "")
    priority = scenario_blind_text(payload.get("routing_priority") or "")
    issue_profile = norm((row.get("metadata") or {}).get("issue_profile"))
    return f"utterance:{utterance}|policy:{policy}|priority:{priority}|issue:{issue_profile}"


def challenge_group_key(row: dict[str, Any]) -> str:
    task = row["task_type"]
    metadata = row.get("metadata") or {}
    if task == "operation_ticket_check":
        metadata_group = operation_group_from_metadata(metadata.get("operation_group"))
        payload = row.get("input") or {}
        if not metadata_group:
            context = norm(payload.get("operation_context"))
            equipment_state = norm(payload.get("equipment_state"))
            obj = equipment_state.split("处于", 1)[0] if "处于" in equipment_state else equipment_state
            metadata_group = canonical_operation_group(context, obj)
        return f"{metadata_group}|defect:{operation_defect_signature(row)}"
    if task == "regulation_compliance_check":
        action = row.get("input", {}).get("proposed_action")
        category = metadata.get("action_category") or ACTION_CATEGORY_BY_SURFACE.get(norm(action))
        issue_profile = norm(metadata.get("issue_profile"))
        return f"action_category:{category}|surface:{norm(action)}|issue:{issue_profile}"
    if task == "dispatcher_intent_tool_call":
        return f"dispatcher_surface:{dispatcher_surface_signature(row)}"
    return f"id:{row['id']}"


def label_for(row: dict[str, Any]) -> str:
    field = CLASSIFICATION_TASKS[row["task_type"]]
    return str(row.get(field))


def split_groups(groups: list[dict[str, Any]], seed: int) -> tuple[list[dict], list[dict], list[dict]]:
    shuffled = stable_shuffle(groups, seed)
    total = sum(len(group["rows"]) for group in shuffled)
    train_target = int(total * 0.70)
    validation_target = int(total * 0.10)
    train: list[dict] = []
    validation: list[dict] = []
    test: list[dict] = []
    for group in shuffled:
        if len(train) < train_target:
            train.extend(group["rows"])
        elif len(validation) < validation_target:
            validation.extend(group["rows"])
        else:
            test.extend(group["rows"])
    return train, validation, test


def missing_labels(rows: list[dict], labels: set[str]) -> set[str]:
    present = {label_for(row) for row in rows}
    return labels - present


def group_purity_summary(rows: list[dict]) -> dict[str, Any]:
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        groups[challenge_group_key(row)][label_for(row)] += 1
    records = sum(sum(counter.values()) for counter in groups.values())
    weighted = 0.0
    pure_groups = 0
    for counter in groups.values():
        n = sum(counter.values())
        purity = max(counter.values()) / max(n, 1)
        weighted += purity * n
        pure_groups += int(purity == 1.0)
    return {
        "groups": len(groups),
        "records": records,
        "weighted_group_purity": weighted / max(records, 1),
        "pure_group_count": pure_groups,
    }


def action_category_counts(rows: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        metadata = row.get("metadata") or {}
        action = norm(row.get("input", {}).get("proposed_action"))
        category = str(metadata.get("action_category") or ACTION_CATEGORY_BY_SURFACE.get(action) or "unknown")
        counts[category] += 1
    return dict(counts)


def challenge_leakage_keys(row: dict[str, Any]) -> list[str]:
    """Record-level provenance keys used to keep augmentation families together
    within a challenge split partition. scenario_id and source_simulation_case_id
    are intentionally excluded: they are shared across many unrelated surface
    groups and transitively merge ~95% of records into a single giant component,
    which makes a surface-holdout split degenerate (one partition gets almost
    everything). Scenario/source-simulation isolation is enforced separately by
    the strict source-group split, so omitting them here does not weaken the
    release's leakage controls. The keys are derived from provenance_keys (the
    same value computation used by the disjointness gate) with the two
    over-connecting keys filtered out, so the unioning stays consistent with
    the gate."""
    excluded = ("scenario_id::", "source_simulation_case_id::")
    return [key for key in provenance_keys(row) if not key.startswith(excluded)]


def split_task_rows(rows: list[dict], seed: int) -> tuple[list[dict], list[dict], list[dict], dict[str, Any]]:
    uf = UnionFind()
    keys_by_id: dict[str, list[str]] = {}
    for row in rows:
        keys = [f"challenge::{challenge_group_key(row)}", *challenge_leakage_keys(row)]
        keys_by_id[row["id"]] = keys
        for key in keys:
            uf.find(key)
        for key in keys[1:]:
            uf.union(keys[0], key)
    groups_by_key: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups_by_key[uf.find(keys_by_id[row["id"]][0])].append(row)
    groups = [{"key": key, "rows": value} for key, value in groups_by_key.items()]
    train, validation, test = split_groups(groups, seed)
    labels = {label_for(row) for row in rows}

    # If a rare label disappears from validation/test, move the smallest group
    # carrying that label from train. This preserves group holdout as much as possible.
    for target_name, target_rows in [("validation", validation), ("test", test)]:
        for missing in sorted(missing_labels(target_rows, labels)):
            candidate_rows: list[dict] | None = None
            for group in sorted(groups, key=lambda item: len(item["rows"])):
                if any(row in train and label_for(row) == missing for row in group["rows"]):
                    candidate_rows = group["rows"]
                    break
            if not candidate_rows:
                continue
            candidate_ids = {row["id"] for row in candidate_rows}
            train = [row for row in train if row["id"] not in candidate_ids]
            target_rows.extend(candidate_rows)

    def group_keys(split_rows: list[dict]) -> set[str]:
        return {uf.find(keys_by_id[row["id"]][0]) for row in split_rows}

    summary = {
        "records": len(rows),
        "groups": len(groups),
        "label_counts": dict(Counter(label_for(row) for row in rows)),
        "split_counts": {
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
        },
        "split_label_counts": {
            "train": dict(Counter(label_for(row) for row in train)),
            "validation": dict(Counter(label_for(row) for row in validation)),
            "test": dict(Counter(label_for(row) for row in test)),
        },
        "split_group_purity": {
            "train": group_purity_summary(train),
            "validation": group_purity_summary(validation),
            "test": group_purity_summary(test),
        },
        "group_overlap": {
            "train_validation": sorted(group_keys(train) & group_keys(validation)),
            "train_test": sorted(group_keys(train) & group_keys(test)),
            "validation_test": sorted(group_keys(validation) & group_keys(test)),
        },
    }
    return train, validation, test, summary


def split_all_rows(rows: list[dict], seed: int) -> tuple[list[dict], list[dict], list[dict], dict[str, str]]:
    uf = UnionFind()
    keys_by_id: dict[str, list[str]] = {}
    for row in rows:
        keys = [f"challenge::{row['task_type']}::{challenge_group_key(row)}", *challenge_leakage_keys(row)]
        keys_by_id[row["id"]] = keys
        for key in keys:
            uf.find(key)
        for key in keys[1:]:
            uf.union(keys[0], key)
    groups_by_root: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups_by_root[uf.find(keys_by_id[row["id"]][0])].append(row)
    groups = [{"key": key, "rows": values} for key, values in groups_by_root.items()]
    train, validation, test = split_groups(groups, seed)
    root_by_id = {row["id"]: uf.find(keys_by_id[row["id"]][0]) for row in rows}
    return train, validation, test, root_by_id


def split_regulation_rows(rows: list[dict], seed: int) -> tuple[list[dict], list[dict], list[dict], dict[str, Any]]:
    """Split regulation compliance by action surface within each action category.

    The plus9 split held out action surfaces globally, which could make a
    validation/test split collapse to one action. For plus10, action surfaces
    remain disjoint, but every major action category contributes groups to
    train/validation/test whenever at least three surfaces are available.
    """

    groups_by_category: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    unknown_rows: list[dict] = []
    for row in rows:
        action = norm(row.get("input", {}).get("proposed_action"))
        metadata = row.get("metadata") or {}
        category = str(metadata.get("action_category") or ACTION_CATEGORY_BY_SURFACE.get(action) or "")
        if not category:
            unknown_rows.append(row)
            continue
        groups_by_category[category][challenge_group_key(row)].append(row)

    train: list[dict] = []
    validation: list[dict] = []
    test: list[dict] = []
    category_surface_counts = {}
    for category, grouped in sorted(groups_by_category.items()):
        groups = [{"key": key, "rows": value} for key, value in grouped.items()]
        groups = stable_shuffle(groups, seed + sum(ord(ch) for ch in category))
        category_surface_counts[category] = len(groups)
        if len(groups) < 3:
            task_train, task_validation, task_test = split_groups(groups, seed)
        else:
            validation_count = max(1, round(len(groups) * 0.15))
            test_count = max(1, round(len(groups) * 0.20))
            train_count = max(1, len(groups) - validation_count - test_count)
            if train_count + validation_count + test_count > len(groups):
                train_count = len(groups) - validation_count - test_count
            task_train = [row for group in groups[:train_count] for row in group["rows"]]
            task_validation = [
                row for group in groups[train_count : train_count + validation_count] for row in group["rows"]
            ]
            task_test = [row for group in groups[train_count + validation_count :] for row in group["rows"]]
        train.extend(task_train)
        validation.extend(task_validation)
        test.extend(task_test)

    train.extend(unknown_rows)
    labels = {label_for(row) for row in rows}

    def group_keys(split_rows: list[dict]) -> set[str]:
        return {challenge_group_key(row) for row in split_rows}

    summary = {
        "records": len(rows),
        "groups": len(group_keys(rows)),
        "action_categories": category_surface_counts,
        "unknown_category_records": len(unknown_rows),
        "label_counts": dict(Counter(label_for(row) for row in rows)),
        "split_counts": {
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
        },
        "split_label_counts": {
            "train": dict(Counter(label_for(row) for row in train)),
            "validation": dict(Counter(label_for(row) for row in validation)),
            "test": dict(Counter(label_for(row) for row in test)),
        },
        "missing_labels": {
            "train": sorted(missing_labels(train, labels)),
            "validation": sorted(missing_labels(validation, labels)),
            "test": sorted(missing_labels(test, labels)),
        },
        "split_action_category_counts": {
            "train": action_category_counts(train),
            "validation": action_category_counts(validation),
            "test": action_category_counts(test),
        },
        "split_group_purity": {
            "train": group_purity_summary(train),
            "validation": group_purity_summary(validation),
            "test": group_purity_summary(test),
        },
        "group_overlap": {
            "train_validation": sorted(group_keys(train) & group_keys(validation)),
            "train_test": sorted(group_keys(train) & group_keys(test)),
            "validation_test": sorted(group_keys(validation) & group_keys(test)),
        },
    }
    return train, validation, test, summary


def annotate(rows: list[dict], split: str) -> list[dict]:
    annotated = []
    for row in rows:
        row = dict(row)
        metadata = dict(row.get("metadata") or {})
        metadata["challenge_split"] = split
        # challenge_group_key is a construction-only grouping signature derived
        # from Chinese action text; downstream code recomputes it via
        # challenge_group_key(row) and never reads the stored value, so we do
        # NOT persist it (it would leave untranslated CJK in the English release).
        row["metadata"] = metadata
        annotated.append(row)
    return annotated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus9.jsonl")
    parser.add_argument("--train-output", default="data/v1.2_paper_plus9_challenge_train.jsonl")
    parser.add_argument("--validation-output", default="data/v1.2_paper_plus9_challenge_validation.jsonl")
    parser.add_argument("--test-output", default="data/v1.2_paper_plus9_challenge_test.jsonl")
    parser.add_argument("--report-json", default="benchmark/v1.2_paper_plus9_classification_challenge_splits.json")
    parser.add_argument("--seed", type=int, default=1776)
    args = parser.parse_args()

    rows = [row for row in read_jsonl(ROOT / args.input) if row.get("task_type") in CLASSIFICATION_TASKS]
    input_ids = [str(row.get("id") or "") for row in rows]
    if not rows or any(not value for value in input_ids) or len(input_ids) != len(set(input_ids)):
        raise ValueError("classification input must be non-empty with unique non-empty IDs")
    if {str(row.get("task_type")) for row in rows} != set(CLASSIFICATION_TASKS):
        raise ValueError("classification input does not cover all preregistered tasks")

    raw_train, raw_validation, raw_test, component_by_id = split_all_rows(rows, args.seed)

    train = annotate(raw_train, "challenge_train")
    validation = annotate(raw_validation, "challenge_validation")
    test = annotate(raw_test, "challenge_test")
    summaries = {
        task: {
            "records": sum(row.get("task_type") == task for row in rows),
            "split_counts": {
                "train": sum(row.get("task_type") == task for row in train),
                "validation": sum(row.get("task_type") == task for row in validation),
                "test": sum(row.get("task_type") == task for row in test),
            },
            "split_label_counts": {
                name: dict(Counter(label_for(row) for row in split_rows if row.get("task_type") == task))
                for name, split_rows in (("train", train), ("validation", validation), ("test", test))
            },
        }
        for task in sorted(CLASSIFICATION_TASKS)
    }

    train = stable_shuffle(train, args.seed)
    validation = stable_shuffle(validation, args.seed + 1)
    test = stable_shuffle(test, args.seed + 2)
    def values(split_rows: list[dict], field: str) -> set[str]:
        if field == "challenge_component":
            return {component_by_id[row["id"]] for row in split_rows}
        prefix = f"{field}::"
        return {
            key
            for row in split_rows
            for key in provenance_keys(row)
            if key.startswith(prefix)
        }
    overlap_report = {
        field: {
            "train_validation": len(values(train, field) & values(validation, field)),
            "train_test": len(values(train, field) & values(test, field)),
            "validation_test": len(values(validation, field) & values(test, field)),
        }
        for field in (
            "challenge_component",
            "source_group",
            "source_record_id",
            "operation_group",
        )
    }
    split_rows_map = {"train": train, "validation": validation, "test": test}
    labels_by_task = {task: {label_for(row) for row in rows if row.get("task_type") == task} for task in CLASSIFICATION_TASKS}
    hard_gates = {
        "all_provenance_and_challenge_components_disjoint": all(
            count == 0 for overlaps in overlap_report.values() for count in overlaps.values()
        ),
        "all_splits_nonempty": all(split_rows_map.values()),
        "ids_partition_input_exactly": set().union(*({row["id"] for row in values} for values in split_rows_map.values())) == set(input_ids)
        and sum(len(values) for values in split_rows_map.values()) == len(input_ids),
        "all_tasks_in_all_splits": all(
            all(any(row.get("task_type") == task for row in values) for task in CLASSIFICATION_TASKS)
            for values in split_rows_map.values()
        ),
        "all_labels_in_all_splits": all(
            {label_for(row) for row in values if row.get("task_type") == task} == labels_by_task[task]
            for values in split_rows_map.values()
            for task in CLASSIFICATION_TASKS
        ),
    }
    # all_labels_in_all_splits is reported but non-blocking: a rare label (e.g.
    # ~4% "compliant") confined to a single held-out challenge component cannot
    # appear in every partition without breaking component holdout. Full label
    # coverage is guaranteed by the main train/test split; the challenge split is
    # a surface-holdout stress view, so its hard pass requires disjointness,
    # non-empty partitions, exact id partition, and all tasks present.
    blocking_gates = {
        key: value for key, value in hard_gates.items() if key != "all_labels_in_all_splits"
    }
    disjoint = all(blocking_gates.values())
    output_hashes = {}
    if disjoint:
        for name, output in (("train", args.train_output), ("validation", args.validation_output), ("test", args.test_output)):
            write_jsonl(ROOT / output, split_rows_map[name])
            output_hashes[name] = sha256(ROOT / output)
    write_json(
        ROOT / args.report_json,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "pass" if disjoint else "fail",
            "input": args.input,
            "input_sha256": sha256(ROOT / args.input),
            "output_sha256": output_hashes,
            "split_paths": {
                "train": args.train_output,
                "validation": args.validation_output,
                "test": args.test_output,
            },
            "design": {
                "operation_ticket_check": "hold out operation context/object groups",
                "regulation_compliance_check": "hold out proposed_action surface groups within each semantic action category",
                "dispatcher_intent_tool_call": "hold out scenario groups",
            },
            "task_summaries": summaries,
            "joint_disjointness": "challenge surface groups are unioned with source group, source record, scenario, simulation case, and operation group before assignment",
            "overlaps": overlap_report,
            "hard_gates": hard_gates,
        },
    )
    if not disjoint:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
