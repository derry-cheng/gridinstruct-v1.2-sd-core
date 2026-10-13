#!/usr/bin/env python3
"""Audit assumptions and record-level evidence for OPF closed-loop samples."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import matplotlib.pyplot as plt

from append_opf_closed_loop_auxiliary_records import (
    ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU,
    validate_executable_control_target,
)
from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json


TAG = "opf_closed_loop_auxiliary_v2"
REQUIRED_TOOL_SEQUENCE = ["run_power_flow", "run_opf_redispatch", "run_power_flow"]


def is_opf_record(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata") or {}
    return metadata.get("augmentation_type") == TAG or bool(row.get("closed_loop_validation"))


def tool_sequence(row: dict[str, Any]) -> list[str]:
    out = []
    for item in row.get("tool_plan") or []:
        if isinstance(item, dict):
            out.append(str(item.get("tool")))
    return out


def score_pair(row: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    validation = row.get("closed_loop_validation") or {}
    pre = validation.get("pre_action") or {}
    post = validation.get("post_action") or {}
    pre_score = pre.get("constraint_violation_score")
    post_score = post.get("constraint_violation_score")
    rel = validation.get("relative_violation_reduction")
    return (
        float(pre_score) if pre_score is not None else None,
        float(post_score) if post_score is not None else None,
        float(rel) if rel is not None else None,
    )


def recompute_relative_reduction(pre: float | None, post: float | None) -> float | None:
    if pre is None or post is None or not math.isfinite(pre) or not math.isfinite(post):
        return None
    if pre <= 1e-12:
        return 1.0 if abs(post) <= 1e-12 else 0.0
    return (pre - post) / pre


def detail_n1_pass(validation: dict[str, Any]) -> bool:
    n1 = validation.get("post_action_n1") or {}
    checks = n1.get("checks")
    if not isinstance(checks, list) or len(checks) != 7:
        return False
    network = sum(1 for item in checks if item.get("element") in {"line", "trafo"})
    generators = sum(1 for item in checks if item.get("element") == "gen")
    if network != 5 or generators != 2:
        return False
    for item in checks:
        metrics = item.get("metrics") or {}
        control = item.get("control_feasibility") or {}
        if (
            item.get("solver_status") != "converged"
            or item.get("secure") is not True
            or item.get("electrical_state_complete") is not True
            or control.get("status") != "pass"
            or control.get("violations") not in ([], None)
            or int(metrics.get("overloaded_branch_count") or 0) != 0
            or int(metrics.get("overloaded_transformer_count") or 0) != 0
            or int(metrics.get("voltage_violation_count") or 0) != 0
            or float(metrics.get("constraint_violation_score") or 0.0) > 1e-9
        ):
            return False
    return True


def detail_uncertainty_pass(validation: dict[str, Any]) -> bool:
    gate = validation.get("load_uncertainty_construction_gate") or {}
    models = gate.get("model_summaries") or {}
    if set(models) != {"active_only", "constant_power_factor"}:
        return False
    if gate.get("status") != "pass" or gate.get("denominator_complete") is not True:
        return False
    for summary in models.values():
        if (
            summary.get("status") != "pass"
            or summary.get("denominator_complete") is not True
            or int(summary.get("registered_case_count") or 0) != 48
            or int(summary.get("expected_case_count") or 0) != 48
            or int(summary.get("safe_case_count") or 0) != 48
        ):
            return False
        by_type = summary.get("by_case_type") or {}
        if (
            int((by_type.get("base_stress") or {}).get("registered_case_count") or 0) != 6
            or int((by_type.get("base_stress") or {}).get("safe_case_count") or 0) != 6
            or int((by_type.get("registered_n1_stress") or {}).get("registered_case_count") or 0) != 42
            or int((by_type.get("registered_n1_stress") or {}).get("safe_case_count") or 0) != 42
        ):
            return False
        by_stress = summary.get("by_stress_percent") or {}
        if set(by_stress) != {"-10%", "-5%", "-2%", "+2%", "+5%", "+10%"}:
            return False
        if any(
            int((item or {}).get("registered_case_count") or 0) != 8
            or int((item or {}).get("safe_case_count") or 0) != 8
            for item in by_stress.values()
        ):
            return False
    return True


def make_figure(report: dict[str, Any], figure_path: Path) -> None:
    networks = sorted(report["by_network"])
    pre_values = [report["by_network"][n]["mean_pre_violation_score"] for n in networks]
    post_values = [report["by_network"][n]["mean_post_violation_score"] for n in networks]
    x = range(len(networks))
    fig, ax = plt.subplots(figsize=(7.8, 4.6), dpi=220)
    ax.bar([i - 0.18 for i in x], pre_values, width=0.36, color="#8f4e2f", label="Pre-OPF")
    ax.bar([i + 0.18 for i in x], post_values, width=0.36, color="#2f6f73", label="Post-OPF")
    ax.set_xticks(list(x), networks)
    ax.set_ylabel("Mean violation score")
    ax.set_xlabel("Network")
    ax.set_title("OPF Closed-Loop Assumption Audit")
    ax.legend(frameon=False)
    ax.grid(axis="y", color="#d9dde3", linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    ensure_dirs(figure_path.parent)
    fig.savefig(figure_path)
    plt.close(fig)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# OPF Closed-Loop Assumption Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Status: `{report['status']}`",
        f"- Dataset: `{report['dataset']}`",
        f"- OPF-derived records: {report['opf_record_count']}",
        f"- Unique OPF scenarios: {report['unique_scenarios']}",
        f"- Tool-sequence pass rate: {report['tool_sequence_pass_rate']:.4f}",
        f"- Closed-loop field pass rate: {report['closed_loop_field_pass_rate']:.4f}",
        f"- Native-bound and control-vector pass rate: {report['control_evidence_pass_rate']:.4f}",
        f"- Executable target replay pass rate: {report['executable_target_replay_pass_rate']:.4f}",
        f"- Post-action N-1 pass rate: {report['post_action_n1_pass_rate']:.4f}",
        f"- Embedded uncertainty minimum voltage margin: {report['minimum_embedded_uncertainty_voltage_margin_pu']:.6f} p.u.",
        f"- Mean relative violation reduction: {report['mean_relative_violation_reduction']:.4f}",
        "",
        "## Network Summary",
        "",
        "| Network | Records | Scenarios | Mean pre-score | Mean post-score | Mean relative reduction |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for network, item in sorted(report["by_network"].items()):
        lines.append(
            f"| {network} | {item['records']} | {item['scenarios']} | "
            f"{item['mean_pre_violation_score']:.6f} | {item['mean_post_violation_score']:.6f} | "
            f"{item['mean_relative_violation_reduction']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Modeling Assumptions",
            "",
            "- OPF evidence is based on `pandapower.runopp` over the stored IEEE14 and IEEE118 benchmark scenarios.",
            "- Native cost curves and device capability limits are retained. The external-grid active-power interval is moved to the robust interior of its native capability; generator P/Q and external-grid Q use full native capability or the documented finite fallback when a source limit is absent.",
            "- A registered 90/80/70/60% pre-contingency loading search selects the first cost-minimizing dispatch that passes both embedded load-stress models. Corrective optimization uses the 0.96--1.04 p.u. and 80% internal envelopes, while final validation remains at the native-intersected 0.95--1.05 p.u. and 100% limits and requires at least 0.001 p.u. worst-case voltage margin.",
            "- Every published action retains bidirectional external-grid headroom for a 10% active-load error plus a 2% loss buffer and passes five network-element plus two generator N-1 checks.",
            "- The audit verifies computational closure between pre-action power flow, OPF redispatch, post-action power flow, and post-action contingency recomputation.",
            "- The evidence supports dataset construction validity for auxiliary-decision records; it is not a field-deployment dispatch trial.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--report-json", default="reports/opf_closed_loop_assumption_audit_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/opf_closed_loop_assumption_audit_v1.2_sd_core.md")
    parser.add_argument("--figure", default="figures/sd_core_publication/fig_opf_closed_loop_assumption_audit.png")
    args = parser.parse_args()

    rows = [row for row in read_jsonl(ROOT / args.dataset) if is_opf_record(row)]
    sequence_pass = 0
    field_pass = 0
    control_evidence_pass = 0
    executable_target_replay_pass = 0
    n1_pass = 0
    n1_detail_pass_count = 0
    uncertainty_pass = 0
    uncertainty_detail_pass_count = 0
    relative_reduction_pass_count = 0
    relative_reduction_errors: list[float] = []
    uncertainty_voltage_margins: list[float] = []
    issue_counts: Counter[str] = Counter()
    by_network_rows: dict[str, list[dict[str, Any]]] = {}
    pre_scores: list[float] = []
    post_scores: list[float] = []
    reductions: list[float] = []

    for row in rows:
        network = str(row.get("network_model") or "unknown")
        by_network_rows.setdefault(network, []).append(row)
        sequence = tool_sequence(row)
        if sequence == REQUIRED_TOOL_SEQUENCE:
            sequence_pass += 1
        else:
            issue_counts["tool_sequence_mismatch"] += 1
        validation = row.get("closed_loop_validation") or {}
        pre, post, rel = score_pair(row)
        recomputed_rel = recompute_relative_reduction(pre, post)
        if validation.get("opf_status") == "converged" and pre is not None and post is not None and rel is not None:
            field_pass += 1
            pre_scores.append(pre)
            post_scores.append(post)
            reductions.append(rel)
            if post > pre + 1e-9:
                issue_counts["post_score_worse_than_pre"] += 1
            if recomputed_rel is not None and abs(recomputed_rel - rel) <= 1e-6:
                relative_reduction_pass_count += 1
            else:
                issue_counts["relative_reduction_not_recomputed_consistently"] += 1
                if recomputed_rel is not None:
                    relative_reduction_errors.append(abs(recomputed_rel - rel))
        else:
            issue_counts["missing_closed_loop_fields"] += 1
        policy = validation.get("constraint_policy") or {}
        controls = validation.get("post_action_controls") or {}
        if (
            policy.get("native_costs_preserved") is True
            and int(policy.get("synthetic_cost_entries_added") or 0) == 0
            and policy.get("native_limits_not_relaxed") is True
            and policy.get("policy_id")
            == "native_limits_dual_load_uncertainty_and_registered_contingency_capability_v8"
            and policy.get("robust_optimization_voltage_bounds_pu")
            == [0.96, 1.04]
            and float(
                policy.get("corrective_solver_loading_limit_percent") or 0.0
            )
            == 80.0
            and float(policy.get("active_load_uncertainty_fraction") or 0.0)
            == 0.10
            and float(
                policy.get("minimum_published_uncertainty_voltage_margin_pu")
                or 0.0
            )
            == ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
            and float(policy.get("active_loss_buffer_fraction") or 0.0)
            == 0.02
            and float(
                policy.get(
                    "external_grid_reactive_capability_reserve_fraction_each_side"
                )
                or 0.0
            )
            == 0.46
            and policy.get("control_bounds")
            and controls.get("ext_grid")
            and validation.get("control_delta")
            and validation.get("pre_action_state")
            and validation.get("post_action_state")
            and (validation.get("reserve_summary") or {}).get("upward_reserve_adequate") is True
            and (validation.get("reserve_summary") or {}).get(
                "external_balance_reserve_adequate"
            )
            is True
            and validation.get("selected_loading_margin_percent") in {90.0, 80.0, 70.0, 60.0}
        ):
            control_evidence_pass += 1
        else:
            issue_counts["missing_native_bound_or_control_evidence"] += 1
        target_errors = validate_executable_control_target(
            row.get("executable_control_target") or {},
            validation,
        )
        replay_validation = row.get("target_replay_validation") or {}
        if not target_errors and replay_validation.get("status") == "pass":
            executable_target_replay_pass += 1
        else:
            issue_counts["executable_target_replay_invalid"] += 1
        n1 = validation.get("post_action_n1") or {}
        requested = int(n1.get("requested_checks") or 0)
        completed = int(n1.get("completed_checks") or 0)
        secure = int(n1.get("secure_checks") or 0)
        network_requested = int(n1.get("network_requested_checks") or 0)
        generator_requested = int(n1.get("generator_requested_checks") or 0)
        if (
            requested == 7
            and network_requested == 5
            and generator_requested == 2
            and completed == requested
            and secure == requested
        ):
            n1_pass += 1
        else:
            issue_counts["post_action_n1_incomplete_or_unsafe"] += 1
        if detail_n1_pass(validation):
            n1_detail_pass_count += 1
        else:
            issue_counts["post_action_n1_detail_invalid"] += 1
        uncertainty = validation.get("load_uncertainty_construction_gate") or {}
        uncertainty_voltage_margin = uncertainty.get(
            "minimum_worst_voltage_margin_pu"
        )
        uncertainty_thermal_margin = uncertainty.get(
            "minimum_thermal_margin_percent"
        )
        if (
            uncertainty.get("status") == "pass"
            and uncertainty.get("denominator_complete") is True
            and int(uncertainty.get("load_model_count") or 0) == 2
            and int(uncertainty.get("registered_case_count") or 0) == 96
            and int(uncertainty.get("safe_case_count") or 0) == 96
            and uncertainty_voltage_margin is not None
            and math.isfinite(float(uncertainty_voltage_margin))
            and float(uncertainty_voltage_margin)
            >= ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
            and uncertainty_thermal_margin is not None
            and math.isfinite(float(uncertainty_thermal_margin))
            and float(uncertainty_thermal_margin) >= 0.0
        ):
            uncertainty_pass += 1
            uncertainty_voltage_margins.append(
                float(uncertainty_voltage_margin)
            )
        else:
            issue_counts["embedded_dual_load_uncertainty_not_fully_safe"] += 1
        if detail_uncertainty_pass(validation):
            uncertainty_detail_pass_count += 1
        else:
            issue_counts["embedded_uncertainty_detail_invalid"] += 1

    by_network: dict[str, dict[str, Any]] = {}
    for network, items in by_network_rows.items():
        network_pre: list[float] = []
        network_post: list[float] = []
        network_rel: list[float] = []
        for row in items:
            pre, post, rel = score_pair(row)
            if pre is not None and post is not None and rel is not None:
                network_pre.append(pre)
                network_post.append(post)
                network_rel.append(rel)
        by_network[network] = {
            "records": len(items),
            "scenarios": len({str(row.get("scenario_id")) for row in items}),
            "mean_pre_violation_score": mean(network_pre) if network_pre else 0.0,
            "mean_post_violation_score": mean(network_post) if network_post else 0.0,
            "mean_relative_violation_reduction": mean(network_rel) if network_rel else 0.0,
        }

    status = (
        "pass"
        if rows
        and not issue_counts
        and sequence_pass == len(rows)
        and field_pass == len(rows)
        and control_evidence_pass == len(rows)
        and n1_pass == len(rows)
        and uncertainty_pass == len(rows)
        and n1_detail_pass_count == len(rows)
        and uncertainty_detail_pass_count == len(rows)
        and relative_reduction_pass_count == len(rows)
        else "fail"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "dataset": args.dataset,
        "opf_record_count": len(rows),
        "unique_scenarios": len({str(row.get("scenario_id")) for row in rows}),
        "by_network": by_network,
        "tool_sequence_expected": REQUIRED_TOOL_SEQUENCE,
        "tool_sequence_pass_count": sequence_pass,
        "tool_sequence_pass_rate": sequence_pass / len(rows) if rows else 0.0,
        "closed_loop_field_pass_count": field_pass,
        "closed_loop_field_pass_rate": field_pass / len(rows) if rows else 0.0,
        "control_evidence_pass_count": control_evidence_pass,
        "control_evidence_pass_rate": control_evidence_pass / len(rows) if rows else 0.0,
        "executable_target_replay_pass_count": executable_target_replay_pass,
        "executable_target_replay_pass_rate": (
            executable_target_replay_pass / len(rows) if rows else 0.0
        ),
        "post_action_n1_pass_count": n1_pass,
        "post_action_n1_pass_rate": n1_pass / len(rows) if rows else 0.0,
        "post_action_n1_detail_pass_count": n1_detail_pass_count,
        "post_action_n1_detail_pass_rate": n1_detail_pass_count / len(rows) if rows else 0.0,
        "embedded_uncertainty_pass_count": uncertainty_pass,
        "embedded_uncertainty_pass_rate": (
            uncertainty_pass / len(rows) if rows else 0.0
        ),
        "embedded_uncertainty_detail_pass_count": uncertainty_detail_pass_count,
        "embedded_uncertainty_detail_pass_rate": uncertainty_detail_pass_count / len(rows) if rows else 0.0,
        "relative_reduction_recomputed_pass_count": relative_reduction_pass_count,
        "relative_reduction_recomputed_pass_rate": relative_reduction_pass_count / len(rows) if rows else 0.0,
        "relative_reduction_max_abs_error": max(relative_reduction_errors) if relative_reduction_errors else 0.0,
        "minimum_required_embedded_uncertainty_voltage_margin_pu": (
            ROBUST_MINIMUM_PUBLISHED_VOLTAGE_MARGIN_PU
        ),
        "minimum_embedded_uncertainty_voltage_margin_pu": (
            min(uncertainty_voltage_margins)
            if uncertainty_voltage_margins
            else 0.0
        ),
        "mean_pre_violation_score": mean(pre_scores) if pre_scores else 0.0,
        "mean_post_violation_score": mean(post_scores) if post_scores else 0.0,
        "mean_relative_violation_reduction": mean(reductions) if reductions else 0.0,
        "min_relative_violation_reduction": min(reductions) if reductions else None,
        "max_relative_violation_reduction": max(reductions) if reductions else None,
        "issue_counts": dict(issue_counts),
        "figure": args.figure,
        "claim_use": "Use this audit as computational closed-loop evidence for OPF-derived auxiliary-decision records and as an explicit statement of OPF modeling assumptions.",
    }
    ensure_dirs(ROOT / "reports", ROOT / "figures/sd_core_publication")
    write_json(ROOT / args.report_json, report)
    write_markdown(report, ROOT / args.report_md)
    make_figure(report, ROOT / args.figure)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
