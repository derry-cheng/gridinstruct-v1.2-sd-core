#!/usr/bin/env python3
"""Append formal large-topology instruction records to the SD-core release."""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generate_instruction_data import make_auxiliary, make_compliance, make_intent, make_query
from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, write_json, write_jsonl


EXPANSION_TAG = "extended_topology_instruction_v2"
NETWORK_NAME = {
    "ieee300": "IEEE300",
    "illinois200": "ILLINOIS200",
    "pegase89": "PEGASE89",
    "pegase1354": "PEGASE1354",
    "rte1888": "RTE1888",
    "rte2848": "RTE2848",
    "pegase2869": "PEGASE2869",
}


def normalize_scenario(row: dict[str, Any]) -> dict[str, Any]:
    scenario = dict(row)
    raw_network = str(scenario.get("network_model") or "")
    scenario["network_model"] = NETWORK_NAME.get(raw_network, raw_network.upper())
    scenario["system"] = str(row.get("network_model") or row.get("system") or "").lower()
    return scenario


def stable_scenario_key(scenario: dict[str, Any]) -> str:
    text = str(scenario["scenario_id"])
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()[:48]
    return f"{slug}_{digest}"


def expansion_id(task: str, scenario: dict[str, Any], variant: int, id_namespace: str) -> str:
    return f"gridinstruct_v12_{id_namespace}_{task}_{stable_scenario_key(scenario)}_v{variant:02d}"


def finalize_record(
    row: dict[str, Any],
    task_short: str,
    scenario: dict[str, Any],
    scenario_index: int,
    variant: int,
    *,
    expansion_tag: str,
    id_namespace: str,
    coverage_type: str,
) -> dict[str, Any]:
    row = dict(row)
    prototype_id = str(row["id"])
    new_id = expansion_id(task_short, scenario, variant, id_namespace)
    row["id"] = new_id
    row["network_model"] = str(row.get("network_model") or scenario["network_model"]).replace(" ", "")
    row["scenario_id"] = scenario["scenario_id"]
    row["source_simulation_case_id"] = scenario["scenario_id"]
    metadata = dict(row.get("metadata") or {})
    # Primary scenario-derived expansions are not augmentations of an in-release parent
    # record. Keep the builder prototype only as construction provenance; leave
    # source_record_id unset so release-level parent links remain resolvable.
    metadata.pop("source_record_id", None)
    metadata.update(
        {
            "augmentation_type": expansion_tag,
            "validation_status": "generated_pending_expert_review",
            "created_by": "append_topology_instruction_records.py",
            "construction_prototype_id": prototype_id,
            "source_group": f"{expansion_tag}:{scenario['scenario_id']}",
            "topology_expansion": coverage_type == "external",
            "external_topology_validation": coverage_type == "external",
            "equipment_contingency_validation": coverage_type == "equipment",
            "network_family": row["network_model"],
            "stable_generation_index": scenario_index,
            "stable_scenario_key": stable_scenario_key(scenario),
        }
    )
    row["metadata"] = metadata
    return row


def load_expansion_scenarios(
    path: Path,
    max_per_network: int | None,
    contingency_types: set[str] | None,
) -> list[dict[str, Any]]:
    raw = [normalize_scenario(row) for row in read_json(path) if row.get("solver_status") == "converged"]
    if contingency_types:
        raw = [row for row in raw if str(row.get("contingency_type")) in contingency_types]
    raw.sort(key=lambda row: (row.get("network_model"), row.get("scenario_id")))
    if max_per_network is None:
        return raw
    kept: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row in raw:
        network = str(row.get("network_model"))
        if counts[network] >= max_per_network:
            continue
        kept.append(row)
        counts[network] += 1
    return kept


def verify_scenarios_present(main_path: Path, scenarios: list[dict[str, Any]]) -> int:
    rows = read_json(main_path)
    ids = [str(row.get("scenario_id") or "") for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("main scenario truth contains missing or duplicate IDs")
    missing = sorted({str(row["scenario_id"]) for row in scenarios} - set(ids))
    if missing:
        raise ValueError(f"instruction scenarios missing from complete main truth: {missing[:20]}")
    return len(scenarios)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Topology Instruction Expansion",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        f"- Source dataset before: {report['source_records_before']}",
        f"- Source dataset after: {report['source_records_after']}",
        f"- Candidate new instruction records: {report['candidate_new_records']}",
        f"- New instruction records appended: {report['new_records_appended']}",
        f"- New scenarios merged: {report['new_scenarios_merged']}",
        "",
        "## Network Coverage",
        "",
        "| Network | Scenarios | Candidate instruction records |",
        "| --- | ---: | ---: |",
    ]
    for network in sorted(report["scenario_counts_by_network"]):
        lines.append(
            f"| {network} | {report['scenario_counts_by_network'][network]} | {report['new_record_counts_by_network'].get(network, 0)} |"
        )
    lines.extend(["", "## Task Counts", ""])
    for task, count in sorted(report["new_record_counts_by_task"].items()):
        lines.append(f"- {task}: {count}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output-dataset", default=None)
    parser.add_argument("--scenarios", default="simulation_outputs/topology_stress/extended_topology_scenarios.json")
    parser.add_argument("--main-scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--variants-per-scenario", type=int, default=4)
    parser.add_argument("--max-scenarios-per-network", type=int, default=None)
    parser.add_argument("--include-existing-scenarios", action="store_true")
    parser.add_argument("--contingency-types", nargs="+", default=None)
    parser.add_argument("--expansion-tag", default=EXPANSION_TAG)
    parser.add_argument("--id-namespace", default="topology2")
    parser.add_argument("--coverage-type", choices=["external", "equipment"], default="external")
    parser.add_argument("--report-json", default="reports/topology_instruction_expansion_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/topology_instruction_expansion_v1.2_sd_core.md")
    args = parser.parse_args()

    dataset_path = ROOT / args.dataset
    output_dataset_path = ROOT / (args.output_dataset or args.dataset)
    original_rows = read_jsonl(dataset_path)
    original_ids = [str(row.get("id") or "") for row in original_rows]
    if any(not value for value in original_ids) or len(original_ids) != len(set(original_ids)):
        raise ValueError("dataset contains missing or duplicate IDs")
    prior_tag_rows = [
        row for row in original_rows if (row.get("metadata") or {}).get("augmentation_type") == args.expansion_tag
    ]
    dataset_rows = [
        row for row in original_rows if (row.get("metadata") or {}).get("augmentation_type") != args.expansion_tag
    ]
    scenarios = load_expansion_scenarios(
        ROOT / args.scenarios,
        args.max_scenarios_per_network,
        set(args.contingency_types or []),
    )
    if not scenarios:
        raise ValueError("no converged scenarios matched the requested expansion")

    before_count = len(original_rows)
    new_rows: list[dict[str, Any]] = []
    task_builders = [
        ("compliance", make_compliance),
        ("aux", make_auxiliary),
        ("intent", make_intent),
        ("query", make_query),
    ]
    for scenario in scenarios:
        for variant in range(args.variants_per_scenario):
            stable_index = int(
                hashlib.sha256(
                    f"{args.expansion_tag}:{scenario['scenario_id']}:{variant}".encode("utf-8")
                ).hexdigest()[:10],
                16,
            )
            base_idx = 900000 + stable_index
            for task_short, builder in task_builders:
                row = builder(base_idx, scenario, variant)
                new_rows.append(
                    finalize_record(
                        row,
                        task_short,
                        scenario,
                        stable_index,
                        variant,
                        expansion_tag=args.expansion_tag,
                        id_namespace=args.id_namespace,
                        coverage_type=args.coverage_type,
                    )
                )

    new_ids = [str(row["id"]) for row in new_rows]
    if len(new_ids) != len(set(new_ids)):
        raise ValueError("generated topology records contain duplicate deterministic IDs")
    existing_by_id = {str(row["id"]): row for row in dataset_rows}
    conflicts = [row["id"] for row in new_rows if row["id"] in existing_by_id]
    if conflicts:
        raise ValueError(f"topology expansion IDs conflict with non-expansion rows: {conflicts[:20]}")
    verified_scenarios = verify_scenarios_present(ROOT / args.main_scenarios, scenarios)
    output_rows = dataset_rows + new_rows
    write_jsonl(output_dataset_path, output_rows)
    appended = len(new_rows)
    after_count = len(output_rows)

    new_task_counts = Counter(row["task_type"] for row in new_rows)
    new_network_counts = Counter(row["network_model"] for row in new_rows)
    scenario_network_counts = Counter(row["network_model"] for row in scenarios)
    status = "pass" if appended == len(new_rows) and after_count == len(dataset_rows) + len(new_rows) else "fail"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "dataset": args.dataset,
        "output_dataset": str(output_dataset_path.relative_to(ROOT)),
        "scenario_source": args.scenarios,
        "main_scenarios": args.main_scenarios,
        "variants_per_scenario": args.variants_per_scenario,
        "source_records_before": before_count,
        "source_records_after": after_count,
        "candidate_new_records": len(new_rows),
        "new_records_appended": appended,
        "new_scenarios_merged": 0,
        "complete_truth_scenarios_verified": verified_scenarios,
        "previous_same_tag_records_replaced": len(prior_tag_rows),
        "include_existing_scenarios_argument": args.include_existing_scenarios,
        "scenario_counts_by_network": dict(scenario_network_counts),
        "new_record_counts_by_network": dict(new_network_counts),
        "new_record_counts_by_task": dict(new_task_counts),
        "expansion_tag": args.expansion_tag,
        "id_namespace": args.id_namespace,
        "coverage_type": args.coverage_type,
        "contingency_types": args.contingency_types,
    }
    ensure_dirs(ROOT / "reports")
    write_json(ROOT / args.report_json, report)
    write_markdown(report, ROOT / args.report_md)
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
