#!/usr/bin/env python3
"""Run a reproducible five-seed stability check on the current English splits.

This is a stochastic linear reference, kept separate from the deterministic
LinearSVC headline baseline.  It measures whether the classification numbers
change materially with the random seed on the same frozen records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import balanced_accuracy_score, f1_score

from gridinstruct_utils import ROOT, read_jsonl, write_json


TASK_FIELDS = {
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


def normalize(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").split())


def text_for(row: dict[str, Any]) -> str:
    return "\n".join(
        (
            f"task_type: {row.get('task_type')}",
            f"instruction: {normalize(row.get('instruction'))}",
            f"input: {normalize(row.get('input'))}",
        )
    )


def evaluate(
    train_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    task: str,
    field: str,
    seed: int,
) -> dict[str, Any]:
    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(2, 4),
        min_df=1,
        max_features=50_000,
        sublinear_tf=True,
    )
    x_train = vectorizer.fit_transform(text_for(row) for row in train_rows)
    x_eval = vectorizer.transform(text_for(row) for row in eval_rows)
    y_train = [str(row[field]) for row in train_rows]
    y_eval = [str(row[field]) for row in eval_rows]
    labels = sorted(set(y_train) | set(y_eval))
    model = SGDClassifier(
        loss="modified_huber",
        alpha=1e-5,
        penalty="l2",
        max_iter=1_200,
        tol=1e-4,
        random_state=seed,
        class_weight="balanced",
        average=True,
    )
    model.fit(x_train, y_train)
    prediction = model.predict(x_eval)
    return {
        "seed": seed,
        "task_type": task,
        "n_train": len(train_rows),
        "n_eval": len(eval_rows),
        "macro_f1": float(
            f1_score(y_eval, prediction, labels=labels, average="macro", zero_division=0)
        ),
        "balanced_accuracy": float(balanced_accuracy_score(y_eval, prediction)),
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["evaluation"], row["task_type"])].append(row)
    output = []
    for (evaluation, task), items in sorted(grouped.items()):
        scores = [float(item["macro_f1"]) for item in items]
        balanced = [float(item["balanced_accuracy"]) for item in items]
        output.append(
            {
                "evaluation": evaluation,
                "task_type": task,
                "runs": len(items),
                "n_train": items[0]["n_train"],
                "n_eval": items[0]["n_eval"],
                "macro_f1_mean": statistics.mean(scores),
                "macro_f1_std": statistics.pstdev(scores),
                "macro_f1_min": min(scores),
                "macro_f1_max": max(scores),
                "balanced_accuracy_mean": statistics.mean(balanced),
                "balanced_accuracy_std": statistics.pstdev(balanced),
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 17, 23, 31, 43])
    parser.add_argument(
        "--output",
        default="reports/current_surface_seed_stability_v1.2_sd_core.json",
    )
    args = parser.parse_args()

    # The official split already has a deterministic, hash-bound baseline and
    # the current review concern is the instruction-surface geometry. Keeping
    # this audit focused avoids repeating a multi-minute matrix build over the
    # 61,479-record official OOD stratum.
    split_paths = {
        "surface_test": (
            "data/v1.2_sd_core_instruction_surface_balanced_train_en.jsonl",
            "data/v1.2_sd_core_instruction_surface_balanced_test_en.jsonl",
        ),
        "surface_ood": (
            "data/v1.2_sd_core_instruction_surface_balanced_train_en.jsonl",
            "data/v1.2_sd_core_instruction_surface_balanced_ood_test_en.jsonl",
        ),
    }
    rows: list[dict[str, Any]] = []
    input_hashes: dict[str, str] = {}
    for evaluation, (train_path, eval_path) in split_paths.items():
        train_file = ROOT / train_path
        eval_file = ROOT / eval_path
        if not train_file.exists() or not eval_file.exists():
            raise FileNotFoundError(f"missing split for {evaluation}: {train_path}, {eval_path}")
        input_hashes[train_path] = sha256(train_file)
        input_hashes[eval_path] = sha256(eval_file)
        train = read_jsonl(train_file)
        evaluation_rows = read_jsonl(eval_file)
        for task, field in TASK_FIELDS.items():
            task_train = [row for row in train if row.get("task_type") == task]
            task_eval = [row for row in evaluation_rows if row.get("task_type") == task]
            if not task_train or not task_eval:
                raise ValueError(f"empty task stratum {evaluation}/{task}")
            for seed in args.seeds:
                result = evaluate(task_train, task_eval, task, field, seed)
                result["evaluation"] = evaluation
                rows.append(result)

    summary = summarize(rows)
    max_std = max(item["macro_f1_std"] for item in summary)
    hard_gates = {
        "two_current_surface_geometries": len(summary) == 6,
        "five_seed_runs_per_stratum": len(args.seeds) >= 5
        and all(item["runs"] == len(args.seeds) for item in summary),
        "non_empty_denominators": all(item["n_eval"] > 0 for item in summary),
        "macro_f1_std_at_most_0.05": max_std <= 0.05,
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "warn",
        "objective": "current_split_five_seed_linear_tfidf_stability",
        "model": {
            "family": "sgd_modified_huber",
            "features": "character TF-IDF 2-4 grams, max_features=50000, sublinear_tf=true",
            "class_weight": "balanced",
            "random_seeds": args.seeds,
        },
        "input_sha256": input_hashes,
        "rows": rows,
        "summary": summary,
        "hard_gates": hard_gates,
        "interpretation": (
            "This stochastic reference measures seed stability on frozen current splits. "
            "It is a trainability diagnostic and does not establish semantic, physical, "
            "legal, or operational correctness."
        ),
    }
    output = ROOT / args.output
    write_json(output, report)
    markdown = output.with_suffix(".md")
    lines = [
        "# Current split five-seed stability audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Status: `{report['status']}`",
        "",
        "The stochastic linear reference is evaluated on the frozen instruction-surface test and OOD splits.",
        "",
        "| evaluation | task | runs | n | macro-F1 mean | std | min | max |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary:
        lines.append(
            f"| {item['evaluation']} | {item['task_type']} | {item['runs']} | "
            f"{item['n_eval']} | {item['macro_f1_mean']:.4f} | {item['macro_f1_std']:.4f} | "
            f"{item['macro_f1_min']:.4f} | {item['macro_f1_max']:.4f} |"
        )
    lines.extend(["", "The audit is a trainability diagnostic; it is not a physical or semantic validity certificate."])
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "strata": len(summary), "max_std": max_std}, ensure_ascii=False))


if __name__ == "__main__":
    main()
