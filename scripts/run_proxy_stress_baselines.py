#!/usr/bin/env python3
"""Run proxy-group holdout TF-IDF baselines for classification tasks."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from create_classification_challenge_splits import CLASSIFICATION_TASKS, challenge_group_key, label_for
from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, stable_shuffle, write_json
from run_tfidf_task_baselines import classification_report


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def group_purity(rows: list[dict[str, Any]]) -> float:
    counts = Counter(label_for(row) for row in rows)
    return max(counts.values()) / len(rows) if rows else 0.0


def split_proxy_groups(rows: list[dict[str, Any]], seed: int, test_ratio: float) -> tuple[list[dict], list[dict], dict[str, Any]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[challenge_group_key(row)].append(row)
    groups = [{"key": key, "rows": value, "purity": group_purity(value), "size": len(value)} for key, value in grouped.items()]
    groups = stable_shuffle(groups, seed)
    groups.sort(key=lambda item: (item["purity"], item["size"]), reverse=True)
    target = max(1, int(len(rows) * test_ratio))
    test_groups = []
    test_rows: list[dict] = []
    for group in groups:
        if len(test_rows) >= target and len(test_groups) >= 2:
            break
        test_groups.append(group)
        test_rows.extend(group["rows"])
    test_keys = {group["key"] for group in test_groups}
    train_rows = [row for group in groups if group["key"] not in test_keys for row in group["rows"]]
    labels = {label_for(row) for row in rows}
    # Repair missing labels by moving smallest missing-label group from test to train or train to test.
    for split_name, split_rows, other_rows in [("test", test_rows, train_rows), ("train", train_rows, test_rows)]:
        missing = labels - {label_for(row) for row in split_rows}
        for label in sorted(missing):
            candidate = None
            candidate_source = other_rows
            for key, grow in sorted(grouped.items(), key=lambda item: len(item[1])):
                if any(row in candidate_source and label_for(row) == label for row in grow):
                    candidate = (key, grow)
                    break
            if not candidate:
                continue
            key, grow = candidate
            if split_name == "test":
                train_rows = [row for row in train_rows if challenge_group_key(row) != key]
                test_rows.extend(grow)
                test_keys.add(key)
            else:
                test_rows = [row for row in test_rows if challenge_group_key(row) != key]
                train_rows.extend(grow)
                test_keys.discard(key)
    train_keys = {challenge_group_key(row) for row in train_rows}
    test_keys = {challenge_group_key(row) for row in test_rows}
    summary = {
        "records": len(rows),
        "groups": len(groups),
        "train_records": len(train_rows),
        "test_records": len(test_rows),
        "train_groups": len(train_keys),
        "test_groups": len(test_keys),
        "group_overlap": len(train_keys & test_keys),
        "test_group_examples": sorted(test_keys)[:20],
        "label_counts": dict(Counter(label_for(row) for row in rows)),
        "train_label_counts": dict(Counter(label_for(row) for row in train_rows)),
        "test_label_counts": dict(Counter(label_for(row) for row in test_rows)),
        "mean_test_group_purity": sum(group_purity(grouped[key]) for key in test_keys) / max(len(test_keys), 1),
    }
    return train_rows, test_rows, summary


def standard_tfidf_by_task(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    payload = read_json(path)
    out = {}
    for task, metrics in (payload.get("test_metrics") or {}).items():
        if isinstance(metrics, dict):
            score = metrics.get("macro_f1") or metrics.get("accuracy") or metrics.get("token_f1")
            if score is not None:
                out[task] = float(score)
    return out


def english_path(source_path: Path) -> Path:
    return source_path.with_name(f"{source_path.stem}_en{source_path.suffix}")


def fixed_split_summary(
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    rows_by_split = {
        "train": train_rows,
        "validation": validation_rows,
        "test": test_rows,
    }
    groups_by_split = {
        name: {challenge_group_key(row) for row in rows}
        for name, rows in rows_by_split.items()
    }
    grouped_test: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in test_rows:
        grouped_test[challenge_group_key(row)].append(row)
    return {
        "train_records": len(train_rows),
        "validation_records": len(validation_rows),
        "test_records": len(test_rows),
        "train_groups": len(groups_by_split["train"]),
        "validation_groups": len(groups_by_split["validation"]),
        "test_groups": len(groups_by_split["test"]),
        "group_overlap": len(groups_by_split["train"] & groups_by_split["test"]),
        "group_overlap_all": {
            "train_validation": len(groups_by_split["train"] & groups_by_split["validation"]),
            "train_test": len(groups_by_split["train"] & groups_by_split["test"]),
            "validation_test": len(groups_by_split["validation"] & groups_by_split["test"]),
        },
        "train_label_counts": dict(Counter(label_for(row) for row in train_rows)),
        "validation_label_counts": dict(Counter(label_for(row) for row in validation_rows)),
        "test_label_counts": dict(Counter(label_for(row) for row in test_rows)),
        "mean_test_group_purity": sum(group_purity(rows) for rows in grouped_test.values())
        / max(len(grouped_test), 1),
    }


def load_fixed_split_contract(
    manifest_path: Path,
    canonical_path: Path,
    english_dataset_path: Path,
    split_language: str,
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, Any]]:
    manifest = read_json(manifest_path)
    errors = []
    if manifest.get("status") != "pass":
        errors.append("split_manifest_not_passing")
    canonical_hash = sha256(canonical_path)
    if manifest.get("dataset_sha256") != canonical_hash:
        errors.append("split_manifest_canonical_hash_mismatch")
    builder_relative = str(manifest.get("builder") or "")
    builder_path = ROOT / builder_relative
    builder_hash = sha256(builder_path) if builder_path.is_file() else None
    if not builder_hash or manifest.get("builder_sha256") != builder_hash:
        errors.append("split_manifest_builder_hash_mismatch")
    loaded: dict[str, dict[str, list[dict[str, Any]]]] = {}
    split_hashes: dict[str, dict[str, dict[str, str]]] = {}
    for task in CLASSIFICATION_TASKS:
        task_contract = (manifest.get("tasks") or {}).get(task)
        if not task_contract:
            errors.append(f"missing_task_contract:{task}")
            continue
        loaded[task] = {}
        split_hashes[task] = {}
        for split_name in ("train", "validation", "test"):
            source_relative = str(task_contract.get(split_name) or "")
            source_path = ROOT / source_relative
            if not source_path.is_file():
                errors.append(f"missing_source_split:{task}:{split_name}")
                continue
            source_hash = sha256(source_path)
            if (task_contract.get("sha256") or {}).get(split_name) != source_hash:
                errors.append(f"source_split_hash_mismatch:{task}:{split_name}")
            selected_path = english_path(source_path) if split_language == "en" else source_path
            if not selected_path.is_file():
                errors.append(f"missing_selected_split:{task}:{split_name}:{selected_path}")
                continue
            source_rows = read_jsonl(source_path)
            selected_rows = read_jsonl(selected_path)
            source_ids = [str(row.get("id") or "") for row in source_rows]
            selected_ids = [str(row.get("id") or "") for row in selected_rows]
            if source_ids != selected_ids:
                errors.append(f"selected_split_id_order_mismatch:{task}:{split_name}")
            expected_records = int((task_contract.get("records") or {}).get(split_name, -1))
            if len(source_rows) != expected_records or len(selected_rows) != expected_records:
                errors.append(f"selected_split_record_count_mismatch:{task}:{split_name}")
            loaded[task][split_name] = selected_rows
            split_hashes[task][split_name] = {
                "source_path": str(source_path.relative_to(ROOT)),
                "source_sha256": source_hash,
                "selected_path": str(selected_path.relative_to(ROOT)),
                "selected_sha256": sha256(selected_path),
            }
    if errors:
        raise ValueError("fixed proxy-stress split contract failed: " + ";".join(errors[:30]))
    binding = {
        "manifest": str(manifest_path.relative_to(ROOT)),
        "manifest_sha256": sha256(manifest_path),
        "manifest_status": manifest.get("status"),
        "canonical_dataset": str(canonical_path.relative_to(ROOT)),
        "canonical_dataset_sha256": canonical_hash,
        "english_dataset": str(english_dataset_path.relative_to(ROOT)),
        "english_dataset_sha256": sha256(english_dataset_path),
        "split_language": split_language,
        "builder_config": {
            "builder": manifest.get("builder"),
            "builder_sha256": builder_hash,
            "seed": manifest.get("seed"),
            "test_ratio": manifest.get("test_ratio"),
            "validation_ratio": manifest.get("validation_ratio"),
        },
        "splits": split_hashes,
    }
    return loaded, binding


def save_figure(results: dict[str, Any], path: Path) -> None:
    ensure_dirs(path.parent)
    tasks = list(results["task_results"])
    stress = [results["task_results"][task]["metrics"]["macro_f1"] for task in tasks]
    standard = [results["standard_tfidf_macro_f1"].get(task, 0.0) for task in tasks]
    x = range(len(tasks))
    fig, ax = plt.subplots(figsize=(8.2, 3.8), dpi=180)
    ax.bar([i - 0.18 for i in x], standard, width=0.36, label="Standard test", color="#4C78A8")
    ax.bar([i + 0.18 for i in x], stress, width=0.36, label="Proxy-group holdout", color="#F58518")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Macro-F1")
    ax.set_title("Proxy Stress Baselines")
    ax.set_xticks(list(x), [task.replace("_", "\n") for task in tasks], rotation=20, ha="right")
    ax.legend(frameon=False)
    ax.grid(axis="y", color="#D8DEE9", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def write_md(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Proxy Stress Baseline Report",
        "",
        f"Generated: {payload['generated_at']}",
        f"Dataset: `{payload['dataset']}`",
        "",
        "This audit holds out high-purity proxy groups and evaluates whether shallow text features remain strong under group shift.",
        "",
        "## Results",
        "",
        "| Task | Standard TF-IDF macro-F1 | Proxy-stress macro-F1 | Train records | Test records | Held-out groups |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for task, item in payload["task_results"].items():
        standard = payload["standard_tfidf_macro_f1"].get(task)
        standard_text = f"{standard:.4f}" if standard is not None else "NA"
        lines.append(
            f"| `{task}` | {standard_text} | {item['metrics']['macro_f1']:.4f} | "
            f"{item['split_summary']['train_records']} | {item['split_summary']['test_records']} | {item['split_summary']['test_groups']} |"
        )
    lines.extend(["", "## Diagnostics", ""])
    for task, item in payload["task_results"].items():
        s = item["split_summary"]
        lines.append(f"- {task}: group_overlap={s['group_overlap']}, mean_test_group_purity={s['mean_test_group_purity']:.4f}, test_labels={s['test_label_counts']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--english-dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--split-manifest", default="reports/proxy_stress_split_manifest_v1.2_sd_core.json")
    parser.add_argument("--split-language", choices=("en", "source"), default="en")
    parser.add_argument("--standard-tfidf", default="benchmark/v1.2_sd_core_tfidf_report.json")
    parser.add_argument("--output-json", default="benchmark/v1.2_sd_core_proxy_stress_report.json")
    parser.add_argument("--output-md", default="benchmark/v1.2_sd_core_proxy_stress_report.md")
    parser.add_argument("--figure-dir", default="figures/sd_core_proxy_stress")
    args = parser.parse_args()

    fixed_splits, split_contract = load_fixed_split_contract(
        ROOT / args.split_manifest,
        ROOT / args.canonical_dataset,
        ROOT / args.english_dataset,
        args.split_language,
    )
    standard = standard_tfidf_by_task(ROOT / args.standard_tfidf)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "dataset": args.english_dataset if args.split_language == "en" else args.canonical_dataset,
        "objective": "fixed_hash_bound_proxy_group_holdout_tfidf_stress_test",
        "standard_tfidf_macro_f1": standard,
        "standard_tfidf_report": args.standard_tfidf,
        "standard_tfidf_report_sha256": sha256(ROOT / args.standard_tfidf)
        if (ROOT / args.standard_tfidf).is_file()
        else None,
        "split_contract": split_contract,
        "task_results": {},
    }
    for task in CLASSIFICATION_TASKS:
        train_rows = fixed_splits[task]["train"]
        validation_rows = fixed_splits[task]["validation"]
        test_rows = fixed_splits[task]["test"]
        validation_metrics, _validation_predictions = classification_report(train_rows, validation_rows)
        metrics, predictions = classification_report(train_rows, test_rows)
        split_summary = fixed_split_summary(train_rows, validation_rows, test_rows)
        if any(split_summary["group_overlap_all"].values()):
            raise ValueError(f"fixed proxy-stress split has group overlap: {task}")
        payload["task_results"][task] = {
            "metrics": metrics,
            "validation_metrics": validation_metrics,
            "split_summary": split_summary,
            "error_count": sum(not row["correct"] for row in predictions),
            "prediction_examples": predictions[:20],
        }
    payload["status"] = "pass"
    write_json(ROOT / args.output_json, payload)
    write_md(ROOT / args.output_md, payload)
    save_figure(payload, ROOT / args.figure_dir / "proxy_stress_scores.png")


if __name__ == "__main__":
    main()
