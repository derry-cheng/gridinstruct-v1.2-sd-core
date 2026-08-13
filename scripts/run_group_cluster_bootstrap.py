#!/usr/bin/env python3
"""Compute scenario/source-group cluster bootstrap intervals for neural runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sklearn.metrics import f1_score

from gridinstruct_utils import ROOT, read_jsonl, write_json
from run_current_english_multiseed_suite import expected_test_gold


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provenance_tokens(row: dict[str, Any]) -> list[str]:
    metadata = row.get("metadata") or {}
    values = {
        "source_group": metadata.get("source_group"),
        "scenario": row.get("scenario_id") or metadata.get("scenario_id"),
        "simulation_case": row.get("source_simulation_case_id") or metadata.get("source_simulation_case_id"),
        "source_record": metadata.get("source_record_id") or row.get("source_record_id"),
        "operation_group": metadata.get("operation_group") or row.get("operation_group"),
    }
    tokens = []
    for namespace, value in values.items():
        if value not in (None, ""):
            tokens.append(f"{namespace}:{value}")
    return tokens or [f"record:{row['id']}"]


class UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}
        self.rank = {value: 0 for value in values}

    def find(self, value: str) -> str:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def build_cluster_map(rows: list[dict[str, Any]]) -> tuple[dict[str, str], int]:
    ids = [str(row["id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("dataset contains duplicate IDs")
    union_find = UnionFind(ids)
    first_by_token: dict[str, str] = {}
    for row in rows:
        record_id = str(row["id"])
        for token in provenance_tokens(row):
            first = first_by_token.setdefault(token, record_id)
            union_find.union(record_id, first)
    members: dict[str, list[str]] = defaultdict(list)
    for record_id in ids:
        members[union_find.find(record_id)].append(record_id)
    component_id: dict[str, str] = {}
    for component_members in members.values():
        digest = hashlib.sha256("\n".join(sorted(component_members)).encode("utf-8")).hexdigest()[:20]
        for record_id in component_members:
            component_id[record_id] = f"component:{digest}"
    return component_id, len(members)


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def bootstrap(
    rows: list[dict[str, Any]],
    metric: Callable[[list[dict[str, Any]]], float],
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    if iterations <= 0 or not rows:
        raise ValueError("bootstrap requires positive iterations and non-empty rows")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["cluster_id"]].append(row)
    keys = sorted(groups)
    if len(keys) < 2:
        raise ValueError("cluster bootstrap requires at least two provenance components")
    rng = random.Random(seed)
    values = []
    for _ in range(iterations):
        sample = []
        for key in rng.choices(keys, k=len(keys)):
            sample.extend(groups[key])
        values.append(metric(sample))
    point = metric(rows)
    return {
        "point_estimate": point,
        "ci95_lower": percentile(values, 0.025),
        "ci95_upper": percentile(values, 0.975),
        "bootstrap_iterations": iterations,
        "cluster_count": len(keys),
        "row_count": len(rows),
    }


def sampled_cluster_metric(
    rows: list[dict[str, Any]],
    metric: Callable[[list[dict[str, Any]]], float],
    rng: random.Random,
) -> float:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["cluster_id"]].append(row)
    keys = sorted(groups)
    sample = []
    for key in rng.choices(keys, k=len(keys)):
        sample.extend(groups[key])
    return metric(sample)


def two_level_bootstrap(
    runs: list[tuple[list[dict[str, Any]], Callable[[list[dict[str, Any]]], float]]],
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    if len(runs) != 5:
        raise ValueError("two-level bootstrap requires exactly five preregistered runs")
    rng = random.Random(seed)
    values = []
    for _ in range(iterations):
        sampled_runs = rng.choices(runs, k=len(runs))
        values.append(sum(sampled_cluster_metric(rows, metric, rng) for rows, metric in sampled_runs) / len(sampled_runs))
    point = sum(metric(rows) for rows, metric in runs) / len(runs)
    return {
        "point_estimate_across_seeds": point,
        "ci95_lower": percentile(values, 0.025),
        "ci95_upper": percentile(values, 0.975),
        "bootstrap_iterations": iterations,
        "seed_run_count": len(runs),
        "resampling_policy": "sample seed runs with replacement, then provenance clusters within each sampled run",
    }


def classification_macro_f1(labels: list[str]) -> Callable[[list[dict[str, Any]]], float]:
    def _metric(rows: list[dict[str, Any]]) -> float:
        return float(
            f1_score(
                [row["gold"] for row in rows],
                [row["prediction"] for row in rows],
                labels=labels,
                average="macro",
                zero_division=0,
            )
        )

    return _metric


def finite_tree(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, dict):
        return all(finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(finite_tree(item) for item in value)
    return False


def mean_field(field: str):
    def _metric(rows: list[dict[str, Any]]) -> float:
        return sum(float(row[field]) for row in rows) / max(len(rows), 1)

    return _metric


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-glob", default="benchmark/multiseed_v1.2_sd_core/*_five_seed_summary.json")
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument(
        "--expected-tasks",
        nargs="+",
        default=[
            "operation_ticket_check",
            "regulation_compliance_check",
            "dispatcher_intent_tool_call",
            "regulation_qa",
            "intelligent_data_query",
            "auxiliary_decision",
        ],
    )
    parser.add_argument("--output", default="reports/group_cluster_bootstrap_v1.2_sd_core.json")
    args = parser.parse_args()

    if args.iterations <= 0:
        raise SystemExit("--iterations must be positive")
    dataset_rows = read_jsonl(ROOT / args.dataset)
    cluster_map, dataset_component_count = build_cluster_map(dataset_rows)
    dataset = {row["id"]: row for row in dataset_rows}
    summaries = sorted(ROOT.glob(args.summary_glob))
    summaries_by_task: dict[str, tuple[Path, dict[str, Any]]] = {}
    validation_errors: list[str] = []
    for summary_path in summaries:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        task = str(summary.get("task") or "")
        if not task or task in summaries_by_task:
            validation_errors.append(f"duplicate_or_missing_summary_task:{summary_path}")
            continue
        summaries_by_task[task] = (summary_path, summary)
    if set(summaries_by_task) != set(args.expected_tasks):
        validation_errors.append(
            f"summary_task_set_mismatch:expected={sorted(args.expected_tasks)}:actual={sorted(summaries_by_task)}"
        )

    run_results = []
    missing_ids = []
    two_level_inputs: dict[tuple[str, str], list[tuple[list[dict[str, Any]], Callable[[list[dict[str, Any]]], float]]]] = defaultdict(list)
    for task in sorted(summaries_by_task):
        summary_path, summary = summaries_by_task[task]
        family = summary["family"]
        seeds = [int(run.get("seed")) for run in summary.get("runs", [])]
        if summary.get("status") != "pass" or seeds != [13, 29, 42, 57, 71] or len(set(seeds)) != 5:
            validation_errors.append(f"invalid_multiseed_summary:{summary_path}")
            continue
        split_path = ROOT / str((summary.get("split_paths") or {}).get("test") or "")
        if not split_path.is_file():
            validation_errors.append(f"missing_test_split:{summary_path}")
            continue
        expected_ids = {
            str(row["id"])
            for row in read_jsonl(split_path)
            if row.get("task_type") == task
        }
        if not expected_ids:
            validation_errors.append(f"empty_expected_test_task:{task}")
            continue
        split_paths = summary.get("split_paths") or {}
        current_split_hashes = {
            name: sha256(ROOT / str(split_paths.get(name) or ""))
            for name in ("train", "validation", "test")
            if (ROOT / str(split_paths.get(name) or "")).is_file()
        }
        if set(current_split_hashes) != {"train", "validation", "test"} or summary.get(
            "input_sha256"
        ) != current_split_hashes:
            validation_errors.append(f"summary_split_hash_mismatch:{task}")
            continue
        expected_gold = expected_test_gold(split_path, task, family)
        if set(expected_gold) != expected_ids:
            validation_errors.append(f"expected_gold_id_mismatch:{task}")
            continue
        for run in summary["runs"]:
            prediction_path = ROOT / run["test_predictions"]
            report_path = ROOT / str(run.get("report") or "")
            state_path = ROOT / str(run.get("run_state") or "")
            if not report_path.is_file() or not state_path.is_file():
                validation_errors.append(f"missing_report_or_state:{task}:seed{run['seed']}")
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (
                run.get("input_sha256") != current_split_hashes
                or state.get("report_sha256") != sha256(report_path)
                or state.get("prediction_sha256") != sha256(prediction_path)
                or state.get("run_spec_sha256") != run.get("run_spec_sha256")
                or (state.get("run_spec") or {}).get("input_sha256") != current_split_hashes
            ):
                validation_errors.append(f"run_artifact_binding_mismatch:{task}:seed{run['seed']}")
                continue
            if run.get("prediction_sha256") != sha256(prediction_path):
                validation_errors.append(f"prediction_hash_mismatch:{task}:seed{run['seed']}")
                continue
            predictions = read_jsonl(prediction_path)
            prediction_ids = [str(row.get("id")) for row in predictions]
            if len(prediction_ids) != len(set(prediction_ids)) or set(prediction_ids) != expected_ids:
                validation_errors.append(f"prediction_id_set_mismatch:{task}:seed{run['seed']}")
                continue
            if any(
                str(row.get("gold", "")).strip() != expected_gold.get(str(row.get("id")))
                for row in predictions
            ):
                validation_errors.append(f"prediction_gold_mismatch:{task}:seed{run['seed']}")
                continue
            enriched = []
            for prediction in predictions:
                source = dataset.get(prediction["id"])
                if source is None:
                    missing_ids.append(prediction["id"])
                    continue
                enriched.append({**prediction, "cluster_id": cluster_map[source["id"]]})
            metrics = {}
            if family == "classification":
                fixed_label_space = sorted(set(expected_gold.values()))
                metrics["macro_f1"] = bootstrap(
                    enriched,
                    classification_macro_f1(fixed_label_space),
                    args.iterations,
                    args.seed + int(run["seed"]),
                )
                metrics["macro_f1"]["fixed_label_space"] = fixed_label_space
                two_level_inputs[(task, "macro_f1")].append((enriched, classification_macro_f1(fixed_label_space)))
            else:
                for field in (
                    "exact_match",
                    "token_f1",
                    "char_f1",
                    "prediction_is_json",
                    "prediction_schema_valid",
                    "canonical_json_exact",
                    "structured_query_exact",
                    "tool_set_exact",
                ):
                    if any(field in row for row in enriched):
                        field_rows = [row for row in enriched if field in row]
                        metrics[field] = bootstrap(
                            field_rows,
                            mean_field(field),
                            args.iterations,
                            args.seed + int(run["seed"]),
                        )
                        two_level_inputs[(task, field)].append((field_rows, mean_field(field)))
                required = {"exact_match", "token_f1"} if task == "regulation_qa" else {
                    "exact_match",
                    "char_f1",
                    "prediction_is_json",
                }
                if not required.issubset(metrics):
                    validation_errors.append(f"missing_generation_metrics:{task}:seed{run['seed']}")
                    continue
            if not metrics or not finite_tree(metrics):
                validation_errors.append(f"nonfinite_or_empty_bootstrap:{task}:seed{run['seed']}")
                continue
            run_results.append(
                {
                    "family": family,
                    "task": summary["task"],
                    "seed": run["seed"],
                    "prediction_path": run["test_predictions"],
                    "prediction_sha256": run["prediction_sha256"],
                    "report_path": run["report"],
                    "report_sha256": sha256(report_path),
                    "run_state_path": run["run_state"],
                    "run_state_sha256": sha256(state_path),
                    "current_split_sha256": current_split_hashes,
                    "metrics": metrics,
                }
            )

    two_level = {
        f"{task}:{metric}": two_level_bootstrap(runs, args.iterations, args.seed + index * 1009)
        for index, ((task, metric), runs) in enumerate(sorted(two_level_inputs.items()))
        if len(runs) == 5
    }
    if any(len(runs) != 5 for runs in two_level_inputs.values()):
        validation_errors.append("two_level_bootstrap_missing_seed_runs")
    if not two_level or not finite_tree(two_level):
        validation_errors.append("two_level_bootstrap_empty_or_nonfinite")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not validation_errors and not missing_ids and len(run_results) == 30 else "fail",
        "dataset": args.dataset,
        "dataset_sha256": sha256(ROOT / args.dataset),
        "summary_artifacts": [
            {
                "task": task,
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256(path),
                "input_sha256": summary.get("input_sha256"),
            }
            for task, (path, summary) in sorted(summaries_by_task.items())
        ],
        "summary_glob": args.summary_glob,
        "summary_count": len(summaries),
        "run_count": len(run_results),
        "bootstrap_iterations": args.iterations,
        "cluster_policy": "union-find connected components over split provenance keys: source group, scenario, simulation case, source record, and operation group; template family excluded from the primary clustering",
        "dataset_component_count": dataset_component_count,
        "missing_prediction_id_count": len(set(missing_ids)),
        "missing_prediction_id_examples": sorted(set(missing_ids))[:50],
        "validation_errors": validation_errors,
        "two_level_seed_cluster_bootstrap": two_level,
        "runs": run_results,
    }
    write_json(ROOT / args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
