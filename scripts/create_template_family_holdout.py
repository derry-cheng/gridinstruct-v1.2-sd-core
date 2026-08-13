#!/usr/bin/env python3
"""Create a deterministic template-family holdout and CPU classification audit.

The split groups records by task, construction template, variant, augmentation,
and issue/action family.  Groups are allocated atomically, so a test template
family cannot be reconstructed from its training counterpart.  The raw
surface is only used for a bounded exact Jaccard diagnostic; no lexical
similarity score is promoted to semantic validation.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.svm import LinearSVC

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl


CLASSIFICATION_TASKS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def norm(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").strip().split()) or "none"


def group_key(row: dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    values = [
        row.get("task_type"),
        meta.get("template_family"),
        meta.get("variant_index"),
        meta.get("augmentation_type"),
        meta.get("issue_profile"),
        meta.get("action_category"),
        meta.get("action_surface_variant"),
        meta.get("counterfactual_variant"),
        meta.get("counterfactual_intent"),
        meta.get("plain_ticket_boundary_key"),
        meta.get("implicit_case_key"),
        meta.get("counterfactual_role"),
    ]
    return "|".join(norm(value) for value in values)


def make_text(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(row.get("task_type") or ""),
            str(row.get("task_stage") or ""),
            str(row.get("instruction") or ""),
            json.dumps(row.get("input") or {}, ensure_ascii=False, sort_keys=True),
        ]
    )


def surface(row: dict[str, Any]) -> str:
    return " ".join(make_text(row).lower().split())


def grams(value: str) -> set[str]:
    return {value[index : index + 5] for index in range(max(len(value) - 4, 1))}


def split_task(rows: list[dict[str, Any]], seed: int) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    label_field = CLASSIFICATION_TASKS[rows[0]["task_type"]]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[group_key(row)].append(row)
    group_rows = [
        {"key": key, "rows": values, "labels": Counter(str(row.get(label_field)) for row in values)}
        for key, values in groups.items()
    ]
    rng = random.Random(seed)
    rng.shuffle(group_rows)
    group_rows.sort(key=lambda item: (-len(item["rows"]), item["key"]))
    split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    used: dict[str, list[dict[str, Any]]] = {name: [] for name in split}
    label_sets = {name: Counter() for name in split}
    labels = sorted({str(row.get(label_field)) for row in rows})

    def add(name: str, item: dict[str, Any]) -> None:
        split[name].extend(item["rows"])
        used[name].append(item)
        label_sets[name].update(item["labels"])

    remaining = group_rows[:]
    for name in ("validation", "test"):
        for label in labels:
            candidates = [item for item in remaining if item["labels"].get(label, 0)]
            if candidates:
                item = min(candidates, key=lambda value: (len(value["rows"]), value["key"]))
                add(name, item)
                remaining.remove(item)

    targets = {"train": len(rows) * 0.70, "validation": len(rows) * 0.10, "test": len(rows) * 0.20}
    for item in remaining:
        name = min(split, key=lambda value: (len(split[value]) / max(targets[value], 1), value))
        add(name, item)
    for values in split.values():
        values.sort(key=lambda row: str(row.get("id")))
    overlap = {
        "train_validation": len({item["key"] for item in used["train"]} & {item["key"] for item in used["validation"]}),
        "train_test": len({item["key"] for item in used["train"]} & {item["key"] for item in used["test"]}),
        "validation_test": len({item["key"] for item in used["validation"]} & {item["key"] for item in used["test"]}),
    }
    return split, {
        "task_type": rows[0]["task_type"],
        "records": len(rows),
        "group_count": len(group_rows),
        "split_counts": {name: len(values) for name, values in split.items()},
        "group_overlap": overlap,
        "label_counts": {name: dict(counter) for name, counter in label_sets.items()},
    }


def classify(train: list[dict[str, Any]], test: list[dict[str, Any]], task: str) -> dict[str, Any]:
    field = CLASSIFICATION_TASKS[task]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1, max_features=60000)
    x_train = vectorizer.fit_transform(make_text(row) for row in train)
    x_test = vectorizer.transform(make_text(row) for row in test)
    y_train = [str(row.get(field)) for row in train]
    y_test = [str(row.get(field)) for row in test]
    model = LinearSVC(dual="auto")
    model.fit(x_train, y_train)
    pred = model.predict(x_test)
    return {
        "task_type": task,
        "n_test": len(test),
        "macro_f1": f1_score(y_test, pred, labels=sorted(set(y_train) | set(y_test)), average="macro", zero_division=0),
        "balanced_accuracy": balanced_accuracy_score(y_test, pred),
        "label_space": sorted(set(y_train) | set(y_test)),
    }


def sample_surface_diagnostic(split: dict[str, list[dict[str, Any]]], seed: int, sample_size: int) -> dict[str, Any]:
    rng = random.Random(seed)
    samples = {name: rng.sample(values, min(sample_size, len(values))) for name, values in split.items()}
    sets = {name: [grams(surface(row)) for row in values] for name, values in samples.items()}
    result: dict[str, Any] = {}
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        maximum = 0.0
        for first in sets[left]:
            for second in sets[right]:
                maximum = max(maximum, len(first & second) / max(len(first | second), 1))
        result[f"{left}_{right}"] = {"sample_size": [len(sets[left]), len(sets[right])], "max_jaccard": maximum}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--output-prefix", default="data/v1.2_sd_core_template_family_holdout")
    parser.add_argument("--report", default="reports/template_family_holdout_v1.2_sd_core.json")
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--surface-sample-size", type=int, default=200)
    args = parser.parse_args()
    started = time.perf_counter()
    rows = [row for row in read_jsonl(ROOT / args.input) if row.get("task_type") in CLASSIFICATION_TASKS]
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row["task_type"])].append(row)
    split = {"train": [], "validation": [], "test": []}
    task_reports = []
    baselines = []
    for index, task in enumerate(sorted(by_task)):
        task_split, task_report = split_task(by_task[task], args.seed + index)
        task_reports.append(task_report)
        for name in split:
            split[name].extend(task_split[name])
        baselines.append(classify(task_split["train"], task_split["test"], task))
    for values in split.values():
        values.sort(key=lambda row: str(row.get("id")))
    prefix = ROOT / args.output_prefix
    ensure_dirs(prefix.parent, (ROOT / args.report).parent)
    output_paths = {}
    for name, values in split.items():
        path = prefix.with_name(prefix.name + f"_{name}_ids.jsonl")
        write_jsonl(path, [{"id": row["id"]} for row in values])
        output_paths[name] = str(path.relative_to(ROOT))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(all(item == 0 for item in task["group_overlap"].values()) for task in task_reports) else "fail",
        "contract": "template_family_atomic_holdout_v1",
        "input": args.input,
        "records": len(rows),
        "elapsed_seconds_total": round(time.perf_counter() - started, 6),
        "split_counts": {name: len(values) for name, values in split.items()},
        "task_reports": task_reports,
        "baseline_metrics": baselines,
        "surface_diagnostic": sample_surface_diagnostic(split, args.seed, args.surface_sample_size),
        "output_paths": output_paths,
        "group_definition": "task_type + metadata.template_family + variant_index + augmentation/action/issue family fields",
        "interpretation": "Atomic template-family holdout and target classification diagnostic. The bounded character-Jaccard sample is descriptive and does not certify all-pair semantic independence.",
    }
    write_json(ROOT / args.report, report)
    print(json.dumps({"status": report["status"], "records": len(rows), "split_counts": report["split_counts"]}, ensure_ascii=False))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
