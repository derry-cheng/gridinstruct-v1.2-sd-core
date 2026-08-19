"""Create leakage- and label-controlled splits for the SD-facing GridInstruct core release.

The split unit is a global connected component over scenario identifiers and
source-record identifiers. Assignment is greedy but label-aware, so validation
and test keep usable support for classification labels while preventing scenario
or augmentation-source leakage.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from create_classification_challenge_splits import CLASSIFICATION_TASKS, challenge_group_key
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, stable_shuffle, write_json, write_jsonl

OOD_TASKS = {
    "regulation_compliance_check",
    "auxiliary_decision",
    "dispatcher_intent_tool_call",
    "intelligent_data_query",
}
OOD_SCENARIO_TAGS = ("load120", "load130", "n2_branch_outage")
OOD_NETWORKS = {
    "ieee118",
    "ieee300",
    "illinois200",
    "pegase89",
    "pegase1354",
    "rte1888",
    "rte2848",
    "pegase2869",
}
RULE_ONLY_OOD_RULES = {"REG_QUERY_001"}
SPLIT_NAMES = ("train", "validation", "test")
SPLIT_RATIOS = {"train": 0.8, "validation": 0.1, "test": 0.1}
CLASSIFICATION_LABEL_FIELDS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root


def source_group(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("source_record_id") or row.get("id"))


def classification_group(row: dict[str, Any]) -> str:
    task = str(row.get("task_type"))
    if task not in CLASSIFICATION_TASKS:
        return "NA"
    return f"{task}::{challenge_group_key(row)}"


def grouping_keys(row: dict[str, Any]) -> list[str]:
    task = str(row.get("task_type") or "NA")
    cls_group = classification_group(row)
    if task in CLASSIFICATION_TASKS:
        if cls_group != "NA":
            return [f"task_classification::{cls_group}"]
        return [f"task_record::{task}::{row.get('id')}"]
    keys = [f"task_source::{task}::{source_group(row)}"]
    scenario = row.get("scenario_id") or ""
    if scenario:
        keys.append(f"task_scenario::{task}::{scenario}")
    return keys


def split_group_key(row: dict[str, Any]) -> str:
    return "|".join(grouping_keys(row))


def target_key(row: dict[str, Any]) -> str:
    task = str(row.get("task_type"))
    label_field = CLASSIFICATION_LABEL_FIELDS.get(task)
    if label_field:
        return f"{task}::{row.get(label_field)}"
    if task == "intelligent_data_query":
        query = row.get("structured_query") or {}
        query_label = query.get("filter") or query.get("select") or "query"
        return f"{task}::{query_label}"
    return f"{task}::__task__"


def ood_reason(row: dict[str, Any]) -> str | None:
    network = str(row.get("network_model") or "").replace(" ", "").lower()
    scenario = row.get("scenario_id") or ""
    task_type = row.get("task_type")
    if task_type in OOD_TASKS and (network in OOD_NETWORKS or any(tag in scenario for tag in OOD_SCENARIO_TAGS)):
        return "topology_or_scenario_transfer"
    if task_type == "operation_ticket_check":
        # Ticket records have no electrical network identifier.  Reserve a
        # deterministic construction-family holdout so the OOD split still
        # has a non-zero ticket denominator without mixing records across
        # operation-defect groups.
        key = classification_group(row)
        bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 10
        if bucket == 0:
            return "operation_ticket_construction_holdout"
    if task_type == "regulation_qa":
        rule_ids = set(str(item) for item in (row.get("source_regulation_ids") or []))
        if rule_ids & RULE_ONLY_OOD_RULES:
            return "rule_card_holdout"
    return None


def is_ood(row: dict[str, Any]) -> bool:
    """Compatibility predicate for the other split builders."""
    return ood_reason(row) is not None


def build_connected_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uf = UnionFind()
    row_keys: list[list[str]] = []
    for row in rows:
        keys = grouping_keys(row)
        row_keys.append(keys)
        for key in keys[1:]:
            uf.union(keys[0], key)
        uf.find(keys[0])

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row, keys in zip(rows, row_keys, strict=True):
        grouped[uf.find(keys[0])].append(row)

    groups: list[dict[str, Any]] = []
    for key, group_rows in grouped.items():
        groups.append(
            {
                "group_key": key,
                "rows": group_rows,
                "size": len(group_rows),
                "task_counts": Counter(str(row.get("task_type")) for row in group_rows),
                "target_counts": Counter(target_key(row) for row in group_rows),
            }
        )
    return groups


def distribution_loss(
    current_total: dict[str, int],
    current_task: dict[str, Counter],
    current_target: dict[str, Counter],
    desired_total: dict[str, float],
    desired_task: dict[str, dict[str, float]],
    desired_target: dict[str, dict[str, float]],
    task_totals: Counter,
    target_totals: Counter,
    total_iid: int,
) -> float:
    loss = 0.0
    for split_name in SPLIT_NAMES:
        total_scale = max(total_iid, 1)
        loss += 1.0 * ((current_total[split_name] - desired_total[split_name]) / total_scale) ** 2
        for task_type, task_total in task_totals.items():
            task_scale = max(task_total, 1)
            loss += 0.35 * ((current_task[split_name][task_type] - desired_task[split_name][task_type]) / task_scale) ** 2
        for key, target_total in target_totals.items():
            target_scale = max(target_total, 1)
            loss += 1.25 * ((current_target[split_name][key] - desired_target[split_name][key]) / target_scale) ** 2
    return loss


def assign_connected_groups(groups: list[dict[str, Any]], seed: int) -> dict[str, list[dict[str, Any]]]:
    total_iid = sum(group["size"] for group in groups)
    task_totals = Counter()
    target_totals = Counter()
    for group in groups:
        task_totals.update(group["task_counts"])
        target_totals.update(group["target_counts"])

    desired_total = {name: total_iid * ratio for name, ratio in SPLIT_RATIOS.items()}
    desired_task = {
        name: {task_type: task_total * SPLIT_RATIOS[name] for task_type, task_total in task_totals.items()}
        for name in SPLIT_NAMES
    }
    desired_target = {
        name: {key: target_total * SPLIT_RATIOS[name] for key, target_total in target_totals.items()}
        for name in SPLIT_NAMES
    }
    desired_total["test"] = total_iid - desired_total["train"] - desired_total["validation"]
    for task_type, task_total in task_totals.items():
        desired_task["test"][task_type] = task_total - desired_task["train"][task_type] - desired_task["validation"][task_type]
    for key, target_total in target_totals.items():
        desired_target["test"][key] = target_total - desired_target["train"][key] - desired_target["validation"][key]

    def preferred_split(group_key: str) -> str:
        bucket = stable_shuffle([f"{group_key}::{seed}::{i}" for i in range(1000)], seed)[0]
        value = sum(ord(ch) for ch in bucket) % 1000 / 1000.0
        if value < SPLIT_RATIOS["train"]:
            return "train"
        if value < SPLIT_RATIOS["train"] + SPLIT_RATIOS["validation"]:
            return "validation"
        return "test"

    def rare_target_rank(group: dict[str, Any]) -> tuple[float, int, str]:
        denominators = [target_totals[key] for key in group["target_counts"]]
        rare = min(denominators) if denominators else total_iid
        return (rare, -group["size"], group["group_key"])

    ordered = stable_shuffle(groups, seed)
    ordered.sort(key=rare_target_rank)

    splits: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLIT_NAMES}
    current_total = {name: 0 for name in SPLIT_NAMES}
    current_task = {name: Counter() for name in SPLIT_NAMES}
    current_target = {name: Counter() for name in SPLIT_NAMES}

    for group in ordered:
        pref = preferred_split(group["group_key"])
        best_split = "train"
        best_score: float | None = None
        for split_name in SPLIT_NAMES:
            total_deficit = (desired_total[split_name] - current_total[split_name]) / max(desired_total[split_name], 1.0)
            score = 0.25 * total_deficit
            for task_type, count in group["task_counts"].items():
                task_deficit = (desired_task[split_name][task_type] - current_task[split_name][task_type]) / max(
                    desired_task[split_name][task_type], 1.0
                )
                score += 0.20 * count / max(group["size"], 1) * task_deficit
            for key, count in group["target_counts"].items():
                target_deficit = (desired_target[split_name][key] - current_target[split_name][key]) / max(
                    desired_target[split_name][key], 1.0
                )
                score += 1.25 * count / max(group["size"], 1) * target_deficit
            projected_total = current_total[split_name] + group["size"]
            if projected_total > desired_total[split_name] * 1.08 and split_name != "train":
                score -= 2.0 * (projected_total - desired_total[split_name] * 1.08) / max(desired_total[split_name], 1.0)
            if projected_total > desired_total[split_name] * 1.03 and split_name == "train":
                score -= 0.75 * (projected_total - desired_total[split_name] * 1.03) / max(desired_total[split_name], 1.0)
            if split_name == pref:
                score += 0.001
            if best_score is None or score > best_score or (score == best_score and split_name == pref):
                best_score = score
                best_split = split_name
        splits[best_split].append(group)
        current_total[best_split] += group["size"]
        current_task[best_split].update(group["task_counts"])
        current_target[best_split].update(group["target_counts"])

    return splits


def flatten_group_splits(group_splits: dict[str, list[dict[str, Any]]], seed: int) -> dict[str, list[dict[str, Any]]]:
    return {
        name: stable_shuffle(
            [row for group in groups for row in group["rows"]],
            seed + index * 1009,
        )
        for index, (name, groups) in enumerate(group_splits.items())
    }


def classification_label(row: dict[str, Any], task: str) -> str:
    field = CLASSIFICATION_LABEL_FIELDS[task]
    return str(row.get(field))


def group_label_counts(group: dict[str, Any], task: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in group["rows"]:
        if row.get("task_type") == task:
            counts[classification_label(row, task)] += 1
    return counts


def labels_in_groups(groups: list[dict[str, Any]], task: str) -> set[str]:
    labels: set[str] = set()
    for group in groups:
        labels.update(group_label_counts(group, task))
    return labels


def label_counts_in_groups(groups: list[dict[str, Any]], task: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for group in groups:
        counts.update(group_label_counts(group, task))
    return counts


def move_connected_group(
    group_splits: dict[str, list[dict[str, Any]]],
    donor: str,
    target: str,
    group_key: str,
) -> bool:
    for index, group in enumerate(group_splits[donor]):
        if group["group_key"] == group_key:
            moving = group_splits[donor].pop(index)
            group_splits[target].append(moving)
            return True
    return False


def eval_label_floor(total: int) -> int:
    if total <= 0:
        return 0
    if total < 30:
        return 1
    return min(50, max(5, int(round(total * 0.03))))


def train_label_target(total: int) -> int:
    if total <= 0:
        return 0
    floor = eval_label_floor(total)
    return max(1, min(int(round(total * 0.60)), total - 2 * floor))


def repair_classification_label_coverage(
    group_splits: dict[str, list[dict[str, Any]]], seed: int
) -> dict[str, list[dict[str, Any]]]:
    for task in CLASSIFICATION_LABEL_FIELDS:
        all_labels = set().union(*(labels_in_groups(groups, task) for groups in group_splits.values()))
        if not all_labels:
            continue
        for target in SPLIT_NAMES:
            missing = sorted(all_labels - labels_in_groups(group_splits[target], task))
            for label in missing:
                candidates: list[tuple[int, int, str, str]] = []
                for donor in SPLIT_NAMES:
                    if donor == target:
                        continue
                    donor_counts = label_counts_in_groups(group_splits[donor], task)
                    for group in group_splits[donor]:
                        label_count = group_label_counts(group, task).get(label, 0)
                        if label_count == 0:
                            continue
                        if donor_counts[label] - label_count <= 0:
                            continue
                        candidates.append((group["size"], -label_count, donor, group["group_key"]))
                if not candidates:
                    continue
                _, _, donor, group_key = sorted(candidates)[0]
                move_connected_group(group_splits, donor, target, group_key)
    return group_splits


def rebalance_train_label_support(
    group_splits: dict[str, list[dict[str, Any]]], seed: int
) -> dict[str, list[dict[str, Any]]]:
    for task in CLASSIFICATION_LABEL_FIELDS:
        total_counts = Counter()
        for groups in group_splits.values():
            total_counts.update(label_counts_in_groups(groups, task))
        for label, total in sorted(total_counts.items()):
            target_count = train_label_target(total)
            guard = 0
            while label_counts_in_groups(group_splits["train"], task)[label] < target_count:
                needed = target_count - label_counts_in_groups(group_splits["train"], task)[label]
                candidates: list[tuple[int, int, int, str, str]] = []
                for donor in ("validation", "test"):
                    donor_counts = label_counts_in_groups(group_splits[donor], task)
                    floor = eval_label_floor(total)
                    for group in group_splits[donor]:
                        label_count = group_label_counts(group, task).get(label, 0)
                        if label_count == 0:
                            continue
                        if donor_counts[label] - label_count < floor:
                            continue
                        overshoot = max(label_count - needed, 0)
                        candidates.append((overshoot, -label_count, group["size"], donor, group["group_key"]))
                if not candidates:
                    break
                _, _, _, donor, group_key = sorted(candidates)[0]
                if not move_connected_group(group_splits, donor, "train", group_key):
                    break
                guard += 1
                if guard > 10000:
                    raise RuntimeError(f"label rebalance did not converge for {task}::{label}")
    return group_splits


def split_records(rows: list[dict[str, Any]], seed: int) -> dict[str, list[dict[str, Any]]]:
    ood: list[dict[str, Any]] = []
    iid: list[dict[str, Any]] = []
    for row in rows:
        reason = ood_reason(row)
        if reason:
            row = dict(row)
            row.setdefault("metadata", {})["ood_type"] = reason
            ood.append(row)
        else:
            iid.append(row)

    groups = build_connected_groups(iid)
    group_splits = assign_connected_groups(groups, seed)
    group_splits = repair_classification_label_coverage(group_splits, seed)
    group_splits = rebalance_train_label_support(group_splits, seed)
    group_splits = repair_classification_label_coverage(group_splits, seed + 17)
    splits = flatten_group_splits(group_splits, seed)
    splits["ood_test"] = stable_shuffle(ood, seed + 4001)
    return splits


def overlap_report(splits: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    fields = {
        "record_id": lambda row: str(row.get("id") or "NA"),
        "scenario_id": lambda row: str(row.get("scenario_id") or "NA"),
        "source_group": source_group,
        "task_scenario_id": lambda row: f"{row.get('task_type') or 'NA'}::{row.get('scenario_id') or 'NA'}",
        "task_source_group": lambda row: f"{row.get('task_type') or 'NA'}::{source_group(row)}",
        "split_group_key": split_group_key,
        "classification_group": classification_group,
    }
    out: dict[str, Any] = {}
    for field, fn in fields.items():
        sets = {name: {fn(row) for row in split if fn(row) != "NA"} for name, split in splits.items()}
        pairs: dict[str, Any] = {}
        names = list(sets)
        for i, left in enumerate(names):
            for right in names[i + 1 :]:
                inter = sets[left] & sets[right]
                pairs[f"{left}__{right}"] = {"count": len(inter), "examples": sorted(inter)[:10]}
        out[field] = pairs
    return out


def split_label_counts(split_rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = defaultdict(Counter)
    for row in split_rows:
        task = str(row.get("task_type"))
        label_field = CLASSIFICATION_LABEL_FIELDS.get(task)
        if label_field:
            out[task][str(row.get(label_field))] += 1
    return {task: dict(counter) for task, counter in sorted(out.items())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--seed", type=int, default=2036)
    parser.add_argument("--train-output", default="data/v1.2_sd_core_train.jsonl")
    parser.add_argument("--validation-output", default="data/v1.2_sd_core_validation.jsonl")
    parser.add_argument("--test-output", default="data/v1.2_sd_core_test.jsonl")
    parser.add_argument("--ood-output", default="data/v1.2_sd_core_ood_test.jsonl")
    parser.add_argument("--evaluation-output", default="benchmark/v1.2_sd_core_evaluation_tasks.json")
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    splits = split_records(rows, args.seed)
    output_paths = {
        "train": ROOT / args.train_output,
        "validation": ROOT / args.validation_output,
        "test": ROOT / args.test_output,
        "ood_test": ROOT / args.ood_output,
    }
    ensure_dirs(ROOT / "data", ROOT / "benchmark")
    for name, path in output_paths.items():
        write_jsonl(path, splits[name])

    all_tasks = sorted({row.get("task_type") for row in rows})
    split_limitations = {}
    for name, split_rows in splits.items():
        task_counts = Counter(row.get("task_type") for row in split_rows)
        split_limitations[name] = {
            "missing_tasks": [task for task in all_tasks if task_counts.get(task, 0) == 0],
            "low_count_tasks_below_5": [task for task, count in sorted(task_counts.items()) if count < 5],
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": args.input,
        "split_policy": "task-scoped connected components; classification tasks use whole semantic challenge groups as the supervised unit, while scenario-grounded non-classification tasks use source_record_id/original id plus scenario_id; label-aware assignment and whole-component movement for classification tasks; OOD by IEEE118/high-load/N-2 policy",
        "split_paths": {name: str(path.relative_to(ROOT)) for name, path in output_paths.items()},
        "splits": {
            name: {
                "records": len(split_rows),
                "task_counts": dict(Counter(row.get("task_type") for row in split_rows)),
                "classification_label_counts": split_label_counts(split_rows),
            }
            for name, split_rows in splits.items()
        },
        "overlap_report": overlap_report(splits),
        "ood_design": [
            "topology transfer to IEEE118",
            "scenario transfer to high-load (120% and 130%) and selected N-2 events",
            "operation-ticket construction-family holdout with deterministic group assignment",
            "regulation-QA rule-card holdout for REG_QUERY_001",
        ],
        "split_limitations": split_limitations,
    }
    write_json(ROOT / args.evaluation_output, report)
    print({"source": args.input, "splits": {name: len(split_rows) for name, split_rows in splits.items()}})


if __name__ == "__main__":
    main()
