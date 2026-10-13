"""Create official benchmark splits for a GridInstruct dataset snapshot."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, stable_shuffle, write_json, write_jsonl
from create_strict_source_group_splits import assign_components, build_components, flatten, provenance_keys, source_group


OOD_TASKS = {
    "regulation_compliance_check",
    "auxiliary_decision",
    "dispatcher_intent_tool_call",
    "intelligent_data_query",
}
OOD_SCENARIO_TAGS = ("load120", "load130", "n2_branch_outage")
OOD_NETWORKS = {
    "IEEE118",
    "IEEE300",
    "ILLINOIS200",
    "PEGASE89",
    "PEGASE1354",
    "RTE1888",
    "RTE2848",
    "PEGASE2869",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_ood_row(row: dict) -> bool:
    network = row.get("network_model")
    scenario = row.get("scenario_id") or ""
    task_type = row.get("task_type")
    return bool(task_type in OOD_TASKS and (network in OOD_NETWORKS or any(tag in scenario for tag in OOD_SCENARIO_TAGS)))


def split_records(rows: list[dict], seed: int) -> dict[str, list[dict]]:
    components = build_components(rows)
    ood_components = [component for component in components if any(is_ood_row(row) for row in component["rows"])]
    iid_components = [component for component in components if component not in ood_components]
    ood = []
    for component in ood_components:
        for source_row in component["rows"]:
            row = dict(source_row)
            metadata = dict(row.get("metadata") or {})
            metadata["ood_type"] = "topology_or_scenario_or_task_transfer_provenance_component"
            row["metadata"] = metadata
            ood.append(row)

    assigned = assign_components(iid_components, seed)
    iid_splits = flatten(assigned, seed)
    train = iid_splits["train"]
    validation = iid_splits["validation"]
    test = iid_splits["test"]

    return {
        "train": stable_shuffle(train, seed),
        "validation": stable_shuffle(validation, seed),
        "test": stable_shuffle(test, seed),
        "ood_test": stable_shuffle(ood, seed),
    }


def pairwise_overlaps(splits: dict[str, list[dict]], extractor) -> dict[str, int]:
    sets = {name: set(extractor(row) for row in values if extractor(row)) for name, values in splits.items()}
    names = list(sets)
    return {
        f"{left}__{right}": len(sets[left] & sets[right])
        for index, left in enumerate(names)
        for right in names[index + 1 :]
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-output", default="data/v1.2_train.jsonl")
    parser.add_argument("--validation-output", default="data/v1.2_validation.jsonl")
    parser.add_argument("--test-output", default="data/v1.2_test.jsonl")
    parser.add_argument("--ood-output", default="data/v1.2_ood_test.jsonl")
    parser.add_argument("--evaluation-output", default="benchmark/v1.2_evaluation_tasks.json")
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.input)
    input_ids = [str(row.get("id") or "") for row in rows]
    if not rows or any(not value for value in input_ids) or len(input_ids) != len(set(input_ids)):
        raise ValueError("input dataset must be non-empty with unique non-empty IDs")
    splits = split_records(rows, args.seed)
    ensure_dirs(ROOT / "data", ROOT / "benchmark")
    output_paths = {
        "train": ROOT / args.train_output,
        "validation": ROOT / args.validation_output,
        "test": ROOT / args.test_output,
        "ood_test": ROOT / args.ood_output,
    }
    scenario_by_split = {
        name: sorted({row.get("scenario_id") for row in split_rows if row.get("scenario_id")})
        for name, split_rows in splits.items()
    }
    train_scenarios = set(scenario_by_split["train"])
    test_scenarios = set(scenario_by_split["test"])
    leakage = sorted(train_scenarios & test_scenarios)

    split_limitations = {}
    all_tasks = sorted({row.get("task_type") for row in rows})
    for name, split_rows in splits.items():
        task_counts = Counter(row.get("task_type") for row in split_rows)
        split_limitations[name] = {
            "missing_tasks": [task for task in all_tasks if task_counts.get(task, 0) == 0],
            "low_count_tasks_below_5": [task for task, count in sorted(task_counts.items()) if count < 5],
        }

    split_ids = {name: {str(row["id"]) for row in values} for name, values in splits.items()}
    provenance_overlap = pairwise_overlaps(splits, lambda row: tuple(sorted(provenance_keys(row))))
    individual_provenance_overlap = {
        namespace: pairwise_overlaps(
            splits,
            lambda row, prefix=f"{namespace}::": next((key for key in provenance_keys(row) if key.startswith(prefix)), None),
        )
        for namespace in ("source_group", "source_record_id", "scenario_id", "source_simulation_case_id", "operation_group")
    }
    all_tasks = sorted({str(row.get("task_type")) for row in rows})
    iid_names = ("train", "validation", "test")
    classification_fields = {
        "operation_ticket_check": "compliance_label",
        "regulation_compliance_check": "compliance_label",
        "dispatcher_intent_tool_call": "intent",
    }
    expected_classification_label_spaces = {
        task: sorted(
            {
                str(row.get(field))
                for name in iid_names
                for row in splits[name]
                if row.get("task_type") == task and row.get(field) not in (None, "")
            }
        )
        for task, field in classification_fields.items()
    }
    classification_label_spaces_by_split = {
        name: {
            task: sorted(
                {
                    str(row.get(field))
                    for row in splits[name]
                    if row.get("task_type") == task and row.get(field) not in (None, "")
                }
            )
            for task, field in classification_fields.items()
        }
        for name in iid_names
    }
    hard_gates = {
        "all_splits_nonempty": all(bool(splits[name]) for name in splits),
        "ids_partition_input_exactly": set().union(*split_ids.values()) == set(input_ids)
        and sum(len(values) for values in split_ids.values()) == len(input_ids),
        "provenance_components_disjoint": all(value == 0 for value in provenance_overlap.values()),
        "all_individual_provenance_keys_disjoint": all(
            value == 0 for overlaps in individual_provenance_overlap.values() for value in overlaps.values()
        ),
        "all_tasks_present_in_iid_splits": all(
            all(any(row.get("task_type") == task for row in splits[name]) for task in all_tasks) for name in iid_names
        ),
        "classification_labels_present_in_iid_splits": all(
            all(
                any(row.get("task_type") == task and row.get(field) not in (None, "") for row in splits[name])
                for task, field in classification_fields.items()
            )
            for name in iid_names
        ),
        "classification_label_spaces_complete_in_iid_splits": all(
            classification_label_spaces_by_split[name][task]
            == expected_classification_label_spaces[task]
            for name in iid_names
            for task in classification_fields
        ),
    }
    status = "pass" if all(hard_gates.values()) else "fail"
    output_hashes = {}
    if status == "pass":
        for name, split_rows in splits.items():
            write_jsonl(output_paths[name], split_rows)
            output_hashes[name] = sha256(output_paths[name])

    write_json(
        ROOT / args.evaluation_output,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "source": args.input,
            "input_sha256": sha256(ROOT / args.input),
            "output_sha256": output_hashes,
            "split_paths": {name: str(path.relative_to(ROOT)) for name, path in output_paths.items()},
            "splits": {
                name: {
                    "records": len(split_rows),
                    "task_counts": dict(Counter(row.get("task_type") for row in split_rows)),
                }
                for name, split_rows in splits.items()
            },
            "scenario_overlap_train_test": leakage,
            "source_group_overlap_train_test": sorted(
                {source_group(row) for row in splits["train"] if source_group(row)}
                & {source_group(row) for row in splits["test"] if source_group(row)}
            ),
            "provenance_component_overlap": provenance_overlap,
            "individual_provenance_overlap": individual_provenance_overlap,
            "hard_gates": hard_gates,
            "expected_classification_label_spaces": expected_classification_label_spaces,
            "classification_label_spaces_by_split": classification_label_spaces_by_split,
            "authoritative_split_policy": "IID provenance-connected components over source group, source record, scenario, simulation case, and operation group; topology/scenario OOD held out first",
            "seed": args.seed,
            "ood_design": [
                "topology transfer to IEEE118",
                "external topology transfer to IEEE300, Illinois200, PEGASE89, PEGASE1354, RTE1888, RTE2848, and PEGASE2869",
                "scenario transfer to high-load (120% and 130%) and selected N-2 events",
                "held-out topology/scenario records for compliance, decision, tool-use and query tasks",
                "operation_ticket_check and regulation_qa have no OOD split because they are not scenario-grounded",
            ],
            "split_limitations": split_limitations,
        },
    )
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
