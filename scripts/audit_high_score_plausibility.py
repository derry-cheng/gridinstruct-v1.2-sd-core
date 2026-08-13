"""Summarize evidence used to interpret near-perfect model scores."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from gridinstruct_utils import ROOT, ensure_dirs, read_json, write_json


TASKS = [
    "operation_ticket_check",
    "dispatcher_intent_tool_call",
    "regulation_compliance_check",
]


def load_optional(path: str) -> dict[str, Any]:
    target = ROOT / path
    return read_json(target) if target.exists() else {}


def task_score_from_tfidf(report: dict[str, Any], task: str) -> float | None:
    metrics = report.get("test_metrics", {}).get(task)
    if metrics:
        return metrics.get("macro_f1")
    return None


def profile_score(report: dict[str, Any], profile: str, task: str) -> float | None:
    metrics = report.get("profiles", {}).get(profile, {}).get("test_metrics", {}).get(task)
    if metrics:
        return metrics.get("macro_f1")
    return None


def transformer_score(path: str) -> float | None:
    target = ROOT / path
    if not target.exists():
        return None
    report = read_json(target)
    return report.get("test_evaluation", {}).get("macro_f1")


def high_score_flags(model_score_quality: dict[str, Any]) -> dict[str, list[str]]:
    flags: dict[str, list[str]] = {}
    for row in model_score_quality.get("model_audit", []):
        task = row.get("task_type")
        if task:
            flags[str(task)] = list(row.get("flags", []))
    return flags


def evidence_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    model_score_quality = load_optional(args.model_score_quality)
    template_report = load_optional(args.template_tfidf)
    boundary_report = load_optional(args.boundary_tfidf)
    rule_report = load_optional(args.rule_context_tfidf)
    proxy_reduced = load_optional(args.proxy_reduced_input)
    strict_evidence = load_optional(args.strict_evidence)
    flags = high_score_flags(model_score_quality)

    rows: list[dict[str, Any]] = []
    for task in TASKS:
        standard = transformer_score(f"benchmark/v1.2_sd_core_transformer_{task}_report.json")
        challenge = transformer_score(f"benchmark/v1.2_sd_core_challenge_transformer_{task}_report.json")
        template = task_score_from_tfidf(template_report, task)
        boundary = task_score_from_tfidf(boundary_report, task)
        rule_no_context = profile_score(rule_report, "natural_no_rule", task)
        rule_with_context = profile_score(rule_report, "natural_with_rule_context", task)
        proxy_rows = [
            row for row in proxy_reduced.get("rows", [])
            if row.get("task_type") == task and row.get("profile") == "proxy_reduced"
        ]
        proxy_standard = next((row.get("macro_f1") for row in proxy_rows if row.get("split") == "standard"), None)
        proxy_strict = next((row.get("macro_f1") for row in proxy_rows if row.get("split") == "strict_source_group"), None)
        rows.append(
            {
                "task_type": task,
                "standard_transformer_macro_f1": standard,
                "challenge_transformer_macro_f1": challenge,
                "template_holdout_tfidf_macro_f1": template,
                "dedicated_boundary_tfidf_macro_f1": boundary,
                "rule_context_no_rule_macro_f1": rule_no_context,
                "rule_context_with_rule_macro_f1": rule_with_context,
                "proxy_reduced_standard_macro_f1": proxy_standard,
                "proxy_reduced_strict_macro_f1": proxy_strict,
                "strict_evidence_available": strict_evidence.get("status") == "pass",
                "model_score_flags": flags.get(task, []),
            }
        )
    return rows


def plot(rows: list[dict[str, Any]], figure_dir: Path) -> list[str]:
    ensure_dirs(figure_dir)
    metrics = [
        ("standard_transformer_macro_f1", "Standard"),
        ("challenge_transformer_macro_f1", "Challenge"),
        ("template_holdout_tfidf_macro_f1", "Template\nholdout"),
        ("dedicated_boundary_tfidf_macro_f1", "Boundary\nset"),
        ("proxy_reduced_standard_macro_f1", "Proxy\nreduced"),
        ("rule_context_with_rule_macro_f1", "Rule\ncontext"),
    ]
    matrix = np.array(
        [
            [float(row.get(key) if row.get(key) is not None else np.nan) for key, _ in metrics]
            for row in rows
        ]
    )
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    image = ax.imshow(matrix, vmin=0, vmax=1, cmap="viridis", aspect="auto")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([row["task_type"] for row in rows])
    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels([label for _, label in metrics])
    ax.set_title("High-score interpretation evidence")
    for y in range(matrix.shape[0]):
        for x in range(matrix.shape[1]):
            value = matrix[y, x]
            label = "n/a" if np.isnan(value) else f"{value:.3f}"
            ax.text(x, y, label, ha="center", va="center", fontsize=8, color="white" if not np.isnan(value) and value < 0.65 else "black")
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04, label="Macro-F1")
    path = figure_dir / "fig_high_score_plausibility_matrix.png"
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return [str(path.relative_to(ROOT))]


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# High-score Plausibility Audit",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "This audit consolidates evidence for interpreting near-perfect classification scores. It does not convert high held-out scores into operational dispatch-reasoning claims.",
        "",
        f"Status: {report['status']}",
        "",
        "## Evidence by task",
        "",
    ]
    for row in report["rows"]:
        lines.append(f"### {row['task_type']}")
        for key in [
            "standard_transformer_macro_f1",
            "challenge_transformer_macro_f1",
            "template_holdout_tfidf_macro_f1",
            "dedicated_boundary_tfidf_macro_f1",
            "proxy_reduced_standard_macro_f1",
            "proxy_reduced_strict_macro_f1",
            "rule_context_with_rule_macro_f1",
        ]:
            value = row.get(key)
            lines.append(f"- {key}: {'not available' if value is None else f'{value:.4f}'}")
        lines.append(f"- score flags: {', '.join(row['model_score_flags']) if row['model_score_flags'] else 'none'}")
        lines.append("")
    lines.extend(["## Claim guidance", ""])
    for item in report["claim_guidance"]:
        lines.append(f"- {item}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-score-quality", default="reports/model_score_quality_audit_v1.2_sd_core.json")
    parser.add_argument("--template-tfidf", default="benchmark/v1.2_sd_core_template_holdout_tfidf_report.json")
    parser.add_argument("--boundary-tfidf", default="benchmark/v1.2_sd_core_boundary_challenge_tfidf_report.json")
    parser.add_argument("--rule-context-tfidf", default="benchmark/v1.2_sd_core_rule_context_tfidf_report.json")
    parser.add_argument("--proxy-reduced-input", default="reports/proxy_reduced_input_audit_v1.2_sd_core.json")
    parser.add_argument("--strict-evidence", default="reports/strict_split_evidence_v1.2_sd_core.json")
    parser.add_argument("--output-json", default="reports/high_score_plausibility_audit_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/high_score_plausibility_audit_v1.2_sd_core.md")
    parser.add_argument("--figure-dir", default="figures/sd_core")
    args = parser.parse_args()

    rows = evidence_rows(args)
    required_complete = all(row.get("template_holdout_tfidf_macro_f1") is not None for row in rows)
    status = "pass" if required_complete else "warn"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "rows": rows,
        "figures": plot(rows, ROOT / args.figure_dir),
        "claim_guidance": [
            "Use near-perfect standard split results as trainability and data-consistency evidence.",
            "Report template-holdout, challenge, dedicated boundary-set, proxy-reduced, strict source-group, calibration, and statistical interval evidence beside high scores.",
            "Use rule-context baselines as a provenance-usefulness check, not as a handcrafted operational rule system.",
        ],
    }
    write_json(ROOT / args.output_json, report)
    write_markdown(ROOT / args.output_md, report)


if __name__ == "__main__":
    main()
