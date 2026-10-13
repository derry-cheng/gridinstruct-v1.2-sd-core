#!/usr/bin/env python3
"""Audit physical-envelope categories for external-topology stress scenarios."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, write_json


EXTERNAL_NETWORKS = {"IEEE300", "ILLINOIS200", "PEGASE89", "PEGASE1354", "RTE1888", "RTE2848", "PEGASE2869"}
REQUIRED_ENVELOPE_COUNTS = {
    "normal-envelope": 13,
    "operational-stress": 13,
    "emergency-stress": 13,
    "extreme-stress": 13,
}


def norm_network(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "").upper()
    return text


def envelope_class(row: dict[str, Any]) -> str:
    if row.get("solver_status") != "converged":
        return "solver-not-converged"
    max_loading = float(row.get("max_branch_loading_percent") or 0.0)
    min_vm = float(row.get("min_bus_voltage_pu") or 1.0)
    max_vm = float(row.get("max_bus_voltage_pu") or 1.0)
    if max_loading <= 100.0 and 0.95 <= min_vm and max_vm <= 1.05:
        return "normal-envelope"
    if max_loading <= 120.0 and 0.90 <= min_vm and max_vm <= 1.10:
        return "operational-stress"
    if max_loading <= 300.0 and 0.80 <= min_vm and max_vm <= 1.20:
        return "emergency-stress"
    return "extreme-stress"


def formal_external_rows(dataset: Path) -> list[dict[str, Any]]:
    if not dataset.exists():
        return []
    rows = []
    for row in read_jsonl(dataset):
        network = norm_network(row.get("network_model"))
        metadata = row.get("metadata") or {}
        if network in EXTERNAL_NETWORKS and (
            metadata.get("external_topology_validation") or metadata.get("topology_expansion")
        ):
            rows.append(row)
    return rows


def make_figure(report: dict[str, Any], figure_path: Path) -> None:
    by_network = report["by_network"]
    networks = sorted(by_network)
    classes = ["normal-envelope", "operational-stress", "emergency-stress", "extreme-stress", "solver-not-converged"]
    colors = {
        "normal-envelope": "#2f7d32",
        "operational-stress": "#5f9ea0",
        "emergency-stress": "#d58a1f",
        "extreme-stress": "#b23a48",
        "solver-not-converged": "#7a7f87",
    }
    bottoms = [0] * len(networks)
    fig, ax = plt.subplots(figsize=(9.0, 4.8), dpi=220)
    for cls in classes:
        values = [by_network[n]["envelope_counts"].get(cls, 0) for n in networks]
        ax.bar(networks, values, bottom=bottoms, label=cls.replace("-", " ").title(), color=colors[cls])
        bottoms = [a + b for a, b in zip(bottoms, values)]
    ax.set_ylabel("Scenario count")
    ax.set_xlabel("External network")
    ax.set_title("External Topology Physical Envelope")
    ax.legend(frameon=False, ncol=2, fontsize=8)
    ax.grid(axis="y", color="#d9dde3", linewidth=0.8)
    ax.set_axisbelow(True)
    for tick in ax.get_xticklabels():
        tick.set_rotation(25)
        tick.set_horizontalalignment("right")
    fig.tight_layout()
    ensure_dirs(figure_path.parent)
    fig.savefig(figure_path)
    plt.close(fig)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# External Topology Physical Envelope Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Status: `{report['status']}`",
        f"- Scenario source: `{report['scenario_source']}`",
        f"- Scenario records: {report['scenario_count']}",
        f"- Converged records: {report['converged_count']}",
        f"- Formal external instruction records: {report['formal_external_instruction_records']}",
        f"- Formal external scenarios in release: {report['formal_external_scenarios']}",
        f"- Formal records linked to the current stress-source scenarios: {report['current_stress_source_formal_instruction_records']}",
        f"- Formal scenarios linked to the current stress-source scenarios: {report['current_stress_source_formal_scenarios']}",
        "",
        "## Envelope Classes",
        "",
        "| Class | Count | Meaning |",
        "| --- | ---: | --- |",
    ]
    meanings = {
        "normal-envelope": "Within 0.95-1.05 p.u. and at or below 100% branch loading.",
        "operational-stress": "Outside the normal envelope but within 0.90-1.10 p.u. and at or below 120% branch loading.",
        "emergency-stress": "Stronger stress within 0.80-1.20 p.u. and at or below 300% branch loading.",
        "extreme-stress": "Converged stress case outside the emergency-stress envelope.",
        "solver-not-converged": "Power-flow solver did not converge and the row is retained only as stress-audit evidence.",
    }
    for cls, count in report["envelope_counts"].items():
        lines.append(f"| {cls} | {count} | {meanings.get(cls, '')} |")
    lines.extend(["", "## Network Summary", "", "| Network | Scenarios | Converged | Dominant class | Formal instruction records |", "| --- | ---: | ---: | --- | ---: |"])
    for network, item in sorted(report["by_network"].items()):
        dominant = max(item["envelope_counts"], key=item["envelope_counts"].get)
        lines.append(
            f"| {network} | {item['scenario_count']} | {item['converged_count']} | {dominant} | {item['formal_instruction_records']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The external-topology block is explicitly separated into physical-envelope strata. Normal and operational-stress rows are closest to conventional operating envelopes, emergency-stress rows test security-boundary behaviour, and extreme-stress rows are retained as stress-test evidence rather than presented as typical field operating states.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="simulation_outputs/topology_stress/extended_topology_scenarios.json")
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--report-json", default="reports/external_topology_physical_envelope_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/external_topology_physical_envelope_v1.2_sd_core.md")
    parser.add_argument("--figure", default="figures/sd_core_publication/fig_external_topology_physical_envelope.png")
    args = parser.parse_args()

    scenario_path = ROOT / args.scenarios
    rows = read_json(scenario_path)
    envelope_counts = Counter(envelope_class(row) for row in rows)
    by_network: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[norm_network(row.get("network_model"))].append(row)

    formal_rows = formal_external_rows(ROOT / args.dataset)
    formal_by_network = Counter(norm_network(row.get("network_model")) for row in formal_rows)
    formal_scenarios_by_network: dict[str, set[str]] = defaultdict(set)
    source_scenario_ids = {str(row.get("scenario_id")) for row in rows if row.get("solver_status") == "converged"}
    source_formal_rows = [row for row in formal_rows if str(row.get("scenario_id")) in source_scenario_ids]
    for row in formal_rows:
        formal_scenarios_by_network[norm_network(row.get("network_model"))].add(str(row.get("scenario_id")))

    for network, items in grouped.items():
        item_counts = Counter(envelope_class(row) for row in items)
        by_network[network] = {
            "scenario_count": len(items),
            "converged_count": sum(row.get("solver_status") == "converged" for row in items),
            "envelope_counts": dict(item_counts),
            "min_voltage_min": min((float(row.get("min_bus_voltage_pu") or 1.0) for row in items if row.get("solver_status") == "converged"), default=None),
            "max_voltage_max": max((float(row.get("max_bus_voltage_pu") or 1.0) for row in items if row.get("solver_status") == "converged"), default=None),
            "max_loading_max": max((float(row.get("max_branch_loading_percent") or 0.0) for row in items if row.get("solver_status") == "converged"), default=None),
            "formal_instruction_records": formal_by_network.get(network, 0),
            "formal_scenarios": len(formal_scenarios_by_network.get(network, set())),
        }

    hard_gates = {
        "all_scenarios_classified": bool(rows) and sum(envelope_counts.values()) == len(rows),
        "all_scenarios_converged": envelope_counts.get("solver-not-converged", 0) == 0,
        "all_external_networks_present": set(grouped) == EXTERNAL_NETWORKS,
        "preregistered_envelope_minima_met": all(
            envelope_counts.get(name, 0) >= minimum
            for name, minimum in REQUIRED_ENVELOPE_COUNTS.items()
        ),
        "formal_external_rows_present": bool(formal_rows),
    }
    status = "pass" if all(hard_gates.values()) else "fail"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "scenario_source": args.scenarios,
        "dataset": args.dataset,
        "scenario_count": len(rows),
        "converged_count": sum(row.get("solver_status") == "converged" for row in rows),
        "envelope_counts": dict(envelope_counts),
        "required_envelope_counts": REQUIRED_ENVELOPE_COUNTS,
        "hard_gates": hard_gates,
        "by_network": by_network,
        "formal_external_instruction_records": len(formal_rows),
        "formal_external_scenarios": len({str(row.get("scenario_id")) for row in formal_rows}),
        "current_stress_source_formal_instruction_records": len(source_formal_rows),
        "current_stress_source_formal_scenarios": len({str(row.get("scenario_id")) for row in source_formal_rows}),
        "figure": args.figure,
        "claim_use": "Use this audit to separate typical operating-envelope evidence from emergency and extreme stress-test evidence for external topologies.",
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
