"""Create classification splits with held-out prompt/template surfaces.

The split is designed as a stress test for near-perfect classification scores:
records sharing the same task-specific surface key are assigned atomically to
one split, so validation and test surfaces are unseen during training.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

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


def label_for(row: dict[str, Any]) -> str:
    return norm(row.get(CLASSIFICATION_TASKS[row["task_type"]]))


def operation_surface(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    payload = row.get("input") or {}
    return "|".join(
        [
            f"variant:{norm(metadata.get('variant_index'))}",
            f"augmentation:{norm(metadata.get('augmentation_type'))}",
            f"boundary:{norm(metadata.get('plain_ticket_boundary_key'))}",
            f"implicit:{norm(metadata.get('implicit_case_key'))}",
            f"role:{norm(metadata.get('counterfactual_role'))}",
            f"context:{norm(payload.get('operation_context'))}",
        ]
    )


def compliance_surface(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    payload = row.get("input") or {}
    return "|".join(
        [
            f"variant:{norm(metadata.get('variant_index'))}",
            f"augmentation:{norm(metadata.get('augmentation_type'))}",
            f"action_category:{norm(metadata.get('action_category'))}",
            f"action_surface_variant:{norm(metadata.get('action_surface_variant'))}",
            f"counterfactual_variant:{norm(metadata.get('counterfactual_variant'))}",
            f"issue:{norm(metadata.get('issue_profile'))}",
            f"proposed_action:{norm(payload.get('proposed_action'))}",
        ]
    )


def dispatcher_surface(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    payload = row.get("input") or {}
    return "|".join(
        [
            f"variant:{norm(metadata.get('variant_index'))}",
            f"augmentation:{norm(metadata.get('augmentation_type'))}",
            f"counterfactual_intent:{norm(metadata.get('counterfactual_intent'))}",
            f"issue:{norm(metadata.get('issue_profile'))}",
            f"utterance:{norm(payload.get('utterance') or payload.get('user_utterance'))}",
            f"channel:{norm(payload.get('command_channel'))}",
        ]
    )


def surface_key(row: dict[str, Any]) -> str:
    task = row["task_type"]
    if task == "operation_ticket_check":
        body = operation_surface(row)
    elif task == "regulation_compliance_check":
        body = compliance_surface(row)
    elif task == "dispatcher_intent_tool_call":
        body = dispatcher_surface(row)
    else:
        body = row["id"]
    return f"{task}|{body}"


def split_task_rows(rows: list[dict[str, Any]], seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    groups_by_surface: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups_by_surface[surface_key(row)].append(row)
    groups = [
        {
            "surface": key,
            "rows": value,
            "labels": Counter(label_for(row) for row in value),
        }
        for key, value in groups_by_surface.items()
    ]
    rng.shuffle(groups)
    groups.sort(key=lambda group: len(group["rows"]), reverse=True)

    total = len(rows)
    targets = {"train": total * 0.70, "validation": total * 0.10, "test": total * 0.20}
    split_rows: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    split_groups: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    split_label_counts: dict[str, Counter[str]] = {name: Counter() for name in split_rows}
    labels = sorted({label_for(row) for row in rows})

    def add_group(split_name: str, group: dict[str, Any]) -> None:
        split_rows[split_name].extend(group["rows"])
        split_groups[split_name].append(group)
        split_label_counts[split_name].update(group["labels"])

    unused = groups[:]
    for split_name in ("validation", "test"):
        for label in labels:
            candidates = [group for group in unused if group["labels"].get(label, 0) > 0]
            if not candidates:
                continue
            candidate = min(candidates, key=lambda group: (len(group["rows"]), group["surface"]))
            add_group(split_name, candidate)
            unused.remove(candidate)

    for group in unused:
        current_sizes = {name: len(value) for name, value in split_rows.items()}
        if current_sizes["validation"] < targets["validation"]:
            add_group("validation", group)
        elif current_sizes["test"] < targets["test"]:
            add_group("test", group)
        else:
            add_group("train", group)

    for split_name in ("validation", "test"):
        missing = [label for label in labels if split_label_counts[split_name].get(label, 0) == 0]
        if missing:
            raise RuntimeError(f"{split_name} is missing labels for {rows[0]['task_type']}: {missing}")

    group_sets = {name: {group["surface"] for group in groups_} for name, groups_ in split_groups.items()}
    overlaps = {
        "train_validation": len(group_sets["train"] & group_sets["validation"]),
        "train_test": len(group_sets["train"] & group_sets["test"]),
        "validation_test": len(group_sets["validation"] & group_sets["test"]),
    }
    purity_weighted = 0.0
    for group in groups:
        n = len(group["rows"])
        purity_weighted += n * max(group["labels"].values()) / max(n, 1)
    diagnostics = {
        "task_type": rows[0]["task_type"],
        "records": total,
        "surface_groups": len(groups),
        "weighted_surface_label_purity": purity_weighted / max(total, 1),
        "split_counts": {name: len(value) for name, value in split_rows.items()},
        "label_counts": {name: dict(counter) for name, counter in split_label_counts.items()},
        "surface_group_overlap": overlaps,
    }
    return split_rows["train"], split_rows["validation"], split_rows["test"], diagnostics


def plot_split_counts(task_reports: list[dict[str, Any]], figure_dir: Path) -> list[str]:
    ensure_dirs(figure_dir)
    outputs: list[str] = []
    tasks = [row["task_type"] for row in task_reports]
    for name in ("train", "validation", "test"):
        values = [row["split_counts"][name] for row in task_reports]
        fig, ax = plt.subplots(figsize=(7.2, 3.0))
        bars = ax.bar(tasks, values, color="#4C78A8")
        ax.set_ylabel("Records")
        ax.set_title(f"Template-holdout {name} records")
        ax.tick_params(axis="x", rotation=25)
        ax.bar_label(bars, labels=[f"{value:,}" for value in values], fontsize=8, padding=2)
        path = figure_dir / f"fig_template_holdout_{name}_records.png"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        outputs.append(str(path.relative_to(ROOT)))
    return outputs


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Template-holdout Split Audit",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "This stress split assigns task-specific prompt/template surfaces atomically to train, validation, or test. Test surfaces are unseen during training.",
        "",
        "## Summary",
        "",
        f"- Status: {report['status']}",
        f"- Train records: {report['split_counts']['train']:,}",
        f"- Validation records: {report['split_counts']['validation']:,}",
        f"- Test records: {report['split_counts']['test']:,}",
        f"- Train/test surface overlap: {report['surface_overlap']['train_test']}",
        "",
        "## Task diagnostics",
        "",
    ]
    for row in report["tasks"]:
        lines.extend(
            [
                f"### {row['task_type']}",
                f"- Records: {row['records']:,}",
                f"- Surface groups: {row['surface_groups']:,}",
                f"- Weighted surface label purity: {row['weighted_surface_label_purity']:.4f}",
                f"- Split counts: {row['split_counts']}",
                f"- Surface overlap: {row['surface_group_overlap']}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--train-output", default="data/v1.2_sd_core_template_holdout_train.jsonl")
    parser.add_argument("--validation-output", default="data/v1.2_sd_core_template_holdout_validation.jsonl")
    parser.add_argument("--test-output", default="data/v1.2_sd_core_template_holdout_test.jsonl")
    parser.add_argument("--report-json", default="reports/template_holdout_split_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/template_holdout_split_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    parser.add_argument("--seed", type=int, default=2045)
    args = parser.parse_args()

    rows = [row for row in read_jsonl(ROOT / args.input) if row.get("task_type") in CLASSIFICATION_TASKS]
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[row["task_type"]].append(row)

    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    task_reports: list[dict[str, Any]] = []
    for index, task in enumerate(sorted(by_task)):
        task_train, task_validation, task_test, diagnostics = split_task_rows(by_task[task], args.seed + index)
        train.extend(task_train)
        validation.extend(task_validation)
        test.extend(task_test)
        task_reports.append(diagnostics)

    write_jsonl(ROOT / args.train_output, train)
    write_jsonl(ROOT / args.validation_output, validation)
    write_jsonl(ROOT / args.test_output, test)

    split_surfaces = {
        "train": {surface_key(row) for row in train},
        "validation": {surface_key(row) for row in validation},
        "test": {surface_key(row) for row in test},
    }
    overlap = {
        "train_validation": len(split_surfaces["train"] & split_surfaces["validation"]),
        "train_test": len(split_surfaces["train"] & split_surfaces["test"]),
        "validation_test": len(split_surfaces["validation"] & split_surfaces["test"]),
    }
    status = "pass" if all(value == 0 for value in overlap.values()) else "fail"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": args.input,
        "outputs": {
            "train": args.train_output,
            "validation": args.validation_output,
            "test": args.test_output,
        },
        "split_counts": {"train": len(train), "validation": len(validation), "test": len(test)},
        "surface_overlap": overlap,
        "tasks": task_reports,
        "figures": plot_split_counts(task_reports, ROOT / args.figure_dir),
        "interpretation": "Template-holdout classification split for testing whether high scores survive unseen prompt/template surfaces.",
    }
    write_json(ROOT / args.report_json, report)
    write_markdown(ROOT / args.report_md, report)
    if status != "pass":
        raise SystemExit("Template-holdout split has surface overlap.")


if __name__ == "__main__":
    main()
