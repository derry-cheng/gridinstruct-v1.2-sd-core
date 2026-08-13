"""Audit distribution, duplication, leakage, and label-proxy risk."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def augmentation_type(row: dict[str, Any]) -> str:
    return str((row.get("metadata") or {}).get("augmentation_type") or "original")


def source_group(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("source_record_id") or row.get("id"))


def template_family(row: dict[str, Any]) -> str:
    return str((row.get("metadata") or {}).get("template_family") or "NA")


def action_category(row: dict[str, Any]) -> str:
    return str((row.get("metadata") or {}).get("action_category") or "NA")


def rule_key(row: dict[str, Any]) -> str:
    ids = row.get("source_regulation_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    return "+".join(str(item) for item in ids) if ids else "NA"


def scenario_id(row: dict[str, Any]) -> str:
    return str(row.get("scenario_id") or "NA")


def label_of(row: dict[str, Any]) -> str:
    task = str(row.get("task_type"))
    if task in {"operation_ticket_check", "regulation_compliance_check"}:
        return str(row.get("compliance_label") or row.get("output") or "NA")
    if task == "dispatcher_intent_tool_call":
        intent = row.get("intent")
        if not intent and isinstance(row.get("output"), dict):
            intent = row["output"].get("intent")
        return str(intent or "NA")
    if task == "intelligent_data_query":
        output = row.get("output") or {}
        query = output.get("structured_query") if isinstance(output, dict) else {}
        return str((query or {}).get("filter") or "structured_query")
    return str(row.get("task_type"))


def exact_key(row: dict[str, Any]) -> str:
    return json.dumps(
        {"task_type": row.get("task_type"), "instruction": row.get("instruction"), "input": row.get("input")},
        ensure_ascii=False,
        sort_keys=True,
    )


def counts(rows: list[dict[str, Any]], fn: Callable[[dict[str, Any]], str]) -> dict[str, int]:
    return dict(Counter(fn(row) for row in rows))


def group_size_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    c = Counter(source_group(row) for row in rows if augmentation_type(row) != "original")
    values = sorted(c.values())
    if not values:
        return {"groups": 0, "max": 0, "p95": 0, "histogram": {}}
    p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
    return {"groups": len(values), "max": max(values), "p95": p95, "histogram": dict(Counter(values))}


def duplicate_stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    c = Counter(exact_key(row) for row in rows)
    return {
        "duplicate_keys": sum(1 for value in c.values() if value > 1),
        "duplicate_rows": sum(value for value in c.values() if value > 1),
    }


def split_overlap(split_rows: dict[str, list[dict[str, Any]]], fn: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
    sets = {name: {fn(row) for row in rows if fn(row) != "NA"} for name, rows in split_rows.items()}
    out: dict[str, Any] = {}
    names = list(sets)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            inter = sets[left] & sets[right]
            out[f"{left}__{right}"] = {"count": len(inter), "examples": sorted(inter)[:10]}
    return out


def purity_by_group(rows: list[dict[str, Any]], group_fn: Callable[[dict[str, Any]], str], min_support: int = 20) -> dict[str, Any]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        key = group_fn(row)
        if key == "NA":
            continue
        grouped[key][label_of(row)] += 1
    group_reports = []
    total_weight = 0
    weighted_purity = 0.0
    pure_high_support = 0
    for key, c in grouped.items():
        support = sum(c.values())
        if support < min_support:
            continue
        top_label, top_count = c.most_common(1)[0]
        purity = top_count / support
        total_weight += support
        weighted_purity += purity * support
        if purity >= 0.98:
            pure_high_support += 1
        group_reports.append(
            {
                "group": key,
                "support": support,
                "top_label": top_label,
                "top_label_count": top_count,
                "purity": purity,
                "label_counts": dict(c),
            }
        )
    group_reports.sort(key=lambda item: (item["purity"], item["support"]), reverse=True)
    return {
        "groups_evaluated": len(group_reports),
        "weighted_mean_purity": weighted_purity / total_weight if total_weight else 0.0,
        "pure_groups_at_0_98": pure_high_support,
        "top_groups": group_reports[:20],
    }


def task_purity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for task in sorted({str(row.get("task_type")) for row in rows}):
        subset = [row for row in rows if str(row.get("task_type")) == task]
        checks = {
            "template_family": purity_by_group(subset, template_family),
            "rule_key": purity_by_group(subset, rule_key),
        }
        if task == "regulation_compliance_check":
            checks["action_category"] = purity_by_group(subset, action_category)
        report[task] = checks
    return report


def save_bar(mapping: dict[str, int], path: Path, title: str, ylabel: str, color: str = "#355C7D") -> None:
    items = sorted(mapping.items(), key=lambda item: item[1], reverse=True)
    labels = [key.replace("_", " ") for key, _ in items]
    values = [value for _, value in items]
    fig, ax = plt.subplots(figsize=(max(7.5, len(items) * 0.55), 4.8), dpi=180)
    ax.bar(range(len(values)), values, color=color)
    ax.set_xticks(range(len(values)), labels, rotation=30, ha="right")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", color="#D8DEE9", linewidth=0.8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def save_source_hist(histogram: dict[str, int], path: Path) -> None:
    items = sorted((int(k), v) for k, v in histogram.items())
    fig, ax = plt.subplots(figsize=(7.2, 4.5), dpi=180)
    ax.bar([str(k) for k, _ in items], [v for _, v in items], color="#6C5B7B")
    ax.set_xlabel("Augmented records per source group")
    ax.set_ylabel("Source groups")
    ax.set_title("Source-Group Expansion After Core Selection")
    ax.grid(axis="y", color="#D8DEE9", linewidth=0.8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def save_proxy_summary(purity: dict[str, Any], path: Path) -> None:
    rows = []
    for task, checks in purity.items():
        for name, item in checks.items():
            rows.append((f"{task}\n{name}", item["weighted_mean_purity"]))
    rows.sort(key=lambda item: item[1], reverse=True)
    fig, ax = plt.subplots(figsize=(max(8.5, len(rows) * 0.45), 5.2), dpi=180)
    ax.bar(range(len(rows)), [v for _, v in rows], color="#C06C84")
    ax.set_xticks(range(len(rows)), [k for k, _ in rows], rotation=45, ha="right", fontsize=7)
    ax.set_ylim(0, 1.02)
    ax.set_ylabel("Weighted label purity")
    ax.set_title("Label-Proxy Risk by Grouping Field")
    ax.grid(axis="y", color="#D8DEE9", linewidth=0.8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# SD Core Distribution And Leakage Risk Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Dataset: `{report['dataset']}`",
        "",
        "## Gate Summary",
        "",
        "| Gate | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for gate, item in report["gates"].items():
        lines.append(f"| `{gate}` | `{item['status']}` | {item['detail']} |")
    lines += [
        "",
        "## Dataset Summary",
        "",
        f"- Records: {report['records']}",
        f"- Exact duplicate rows: {report['duplicates']['duplicate_rows']}",
        f"- Augmented source-group max: {report['source_group_size']['max']}",
        f"- Augmented source-group p95: {report['source_group_size']['p95']}",
        "",
        "## Task Distribution",
        "",
        "| Task | Records |",
        "| --- | ---: |",
    ]
    for task, count in sorted(report["by_task"].items()):
        lines.append(f"| `{task}` | {count} |")
    lines += ["", "## Label-Proxy Risk", ""]
    for task, checks in report["purity"].items():
        lines.append(f"### {task}")
        lines.append("")
        lines.append("| Grouping field | Weighted purity | Pure groups >=0.98 |")
        lines.append("| --- | ---: | ---: |")
        for name, item in checks.items():
            lines.append(f"| `{name}` | {item['weighted_mean_purity']:.4f} | {item['pure_groups_at_0_98']} |")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--full-pool", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus15.jsonl")
    parser.add_argument("--train", default="data/v1.2_sd_core_train.jsonl")
    parser.add_argument("--validation", default="data/v1.2_sd_core_validation.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test.jsonl")
    parser.add_argument("--ood", default="data/v1.2_sd_core_ood_test.jsonl")
    parser.add_argument("--output-json", default="reports/sd_core_distribution_risk_audit.json")
    parser.add_argument("--output-md", default="reports/sd_core_distribution_risk_audit.md")
    parser.add_argument("--figure-dir", default="figures/sd_core_quality")
    args = parser.parse_args()

    rows = read_jsonl(ROOT / args.dataset)
    full_rows = read_jsonl(ROOT / args.full_pool) if args.full_pool else []
    split_rows = {
        "train": read_jsonl(ROOT / args.train),
        "validation": read_jsonl(ROOT / args.validation),
        "test": read_jsonl(ROOT / args.test),
        "ood": read_jsonl(ROOT / args.ood),
    }
    purity = task_purity(rows)
    source_stats = group_size_stats(rows)
    duplicates = duplicate_stats(rows)
    overlaps = {
        "record_id": split_overlap(split_rows, lambda row: str(row.get("id") or "NA")),
        "scenario_id": split_overlap(split_rows, scenario_id),
        "source_group": split_overlap(split_rows, source_group),
        "template_family": split_overlap(split_rows, template_family),
    }
    high_purity = []
    for task, checks in purity.items():
        for name, item in checks.items():
            if item["weighted_mean_purity"] >= 0.95 or item["pure_groups_at_0_98"] > 0:
                high_purity.append({"task": task, "field": name, **item})

    gates = {
        "exact_prompt_input_duplicates": {
            "status": "pass" if duplicates["duplicate_rows"] == 0 else "fail",
            "detail": f"duplicate_rows={duplicates['duplicate_rows']}",
        },
        "source_group_expansion": {
            "status": "pass" if source_stats["max"] <= 3 and source_stats["p95"] <= 3 else "warn",
            "detail": f"max={source_stats['max']}, p95={source_stats['p95']}",
        },
        "record_id_split_overlap": {
            "status": "pass" if all(item["count"] == 0 for item in overlaps["record_id"].values()) else "fail",
            "detail": json.dumps({k: v["count"] for k, v in overlaps["record_id"].items()}, ensure_ascii=False),
        },
        "scenario_split_overlap": {
            "status": "pass" if all(item["count"] == 0 for item in overlaps["scenario_id"].values()) else "warn",
            "detail": json.dumps({k: v["count"] for k, v in overlaps["scenario_id"].items()}, ensure_ascii=False),
        },
        "source_group_split_overlap": {
            "status": "pass" if all(item["count"] == 0 for item in overlaps["source_group"].values()) else "warn",
            "detail": json.dumps({k: v["count"] for k, v in overlaps["source_group"].items()}, ensure_ascii=False),
        },
        "label_proxy_purity": {
            "status": "warn" if high_purity else "pass",
            "detail": f"high_purity_groupings={len(high_purity)}",
        },
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "full_pool": args.full_pool,
        "records": len(rows),
        "full_pool_records": len(full_rows),
        "by_task": counts(rows, lambda row: str(row.get("task_type"))),
        "by_stage": counts(rows, lambda row: str(row.get("task_stage"))),
        "by_augmentation_type": counts(rows, augmentation_type),
        "full_pool_by_augmentation_type": counts(full_rows, augmentation_type) if full_rows else {},
        "duplicates": duplicates,
        "source_group_size": source_stats,
        "split_overlap": overlaps,
        "purity": purity,
        "high_purity_groupings": high_purity,
        "gates": gates,
        "passed": all(item["status"] != "fail" for item in gates.values()),
    }
    write_json(ROOT / args.output_json, report)
    write_md(ROOT / args.output_md, report)

    fig_dir = ROOT / args.figure_dir
    save_bar(report["by_task"], fig_dir / "task_distribution.png", "SD Core Task Distribution", "Records")
    save_bar(report["by_augmentation_type"], fig_dir / "augmentation_mix.png", "SD Core Augmentation Mix", "Records", "#2A9D8F")
    save_source_hist(report["source_group_size"]["histogram"], fig_dir / "source_group_expansion.png")
    save_proxy_summary(purity, fig_dir / "label_proxy_purity.png")
    write_json(
        fig_dir / "figure_manifest.json",
        {
            "figures": [
                "task_distribution.png",
                "augmentation_mix.png",
                "source_group_expansion.png",
                "label_proxy_purity.png",
            ],
            "source_report": args.output_json,
        },
    )
    print(json.dumps({"dataset": args.dataset, "records": len(rows), "passed": report["passed"], "gates": gates}, ensure_ascii=False))


if __name__ == "__main__":
    main()
