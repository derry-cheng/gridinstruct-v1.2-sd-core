#!/usr/bin/env python3
"""Migrate frozen external-topology records onto replayable current scenario truth."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandapower as pp

from append_topology_instruction_records import normalize_scenario
from generate_grid_scenarios import summarize_results
from generate_instruction_data import make_auxiliary, make_compliance, make_intent, make_query
from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json, write_jsonl
from recompute_scenario_truth import build_net, validate_complete


BUILDERS = {
    "regulation_compliance_check": make_compliance,
    "auxiliary_decision": make_auxiliary,
    "dispatcher_intent_tool_call": make_intent,
    "intelligent_data_query": make_query,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replayable(row: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        net = build_net(row)
        pp.runpp(
            net,
            algorithm="nr",
            init="auto",
            tolerance_mva=1e-6,
            max_iteration=50,
            enforce_q_lims=True,
            numba=False,
        )
        summary = summarize_results(net)
        candidate = {**row, **summary, "solver_status": "converged"}
        validate_complete(candidate)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:400]}"


def choose_replacements(
    invalid_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    available: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in current_rows:
        if row.get("solver_status") != "converged":
            continue
        key = (str(row.get("network_model") or "").lower(), str(row.get("contingency_type") or ""))
        available[key].append(row)
    for rows in available.values():
        rows.sort(key=lambda row: str(row.get("scenario_id") or ""))

    selected: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for legacy in sorted(invalid_rows, key=lambda row: str(row["scenario_id"])):
        key = (str(legacy.get("network_model") or "").lower(), str(legacy.get("contingency_type") or ""))
        candidates = [row for row in available.get(key, []) if str(row["scenario_id"]) not in used]
        candidates.sort(
            key=lambda row: (
                abs(float(row.get("load_level") or 1.0) - float(legacy.get("load_level") or 1.0)),
                str(row.get("scenario_id") or ""),
            )
        )
        if not candidates:
            raise ValueError(f"no unique replayable replacement for {legacy['scenario_id']}")
        replacement = candidates[0]
        selected[str(legacy["scenario_id"])] = replacement
        used.add(str(replacement["scenario_id"]))
    return selected


def rebuild_record(
    old: dict[str, Any],
    replacement: dict[str, Any],
    legacy_scenario_id: str,
) -> dict[str, Any]:
    task = str(old.get("task_type") or "")
    builder = BUILDERS.get(task)
    if builder is None:
        raise ValueError(f"unsupported frozen task for migration: {task}")
    variant = int((old.get("metadata") or {}).get("variant_index") or 0)
    stable_index = int(hashlib.sha256(f"{replacement['scenario_id']}:{variant}".encode()).hexdigest()[:10], 16)
    generated = builder(900000 + stable_index, normalize_scenario(replacement), variant)
    generated["id"] = old["id"]
    generated["network_model"] = str(replacement.get("network_model") or "").upper()
    generated["scenario_id"] = replacement["scenario_id"]
    generated["source_simulation_case_id"] = replacement["scenario_id"]
    metadata = dict(generated.get("metadata") or {})
    metadata.update(
        {
            "augmentation_type": "extended_topology_instruction_v1_migrated_v2",
            "created_by": "migrate_frozen_external_topology.py",
            "source_group": f"extended_topology_instruction_v1_migrated_v2:{replacement['scenario_id']}",
            "topology_expansion": True,
            "external_topology_validation": True,
            "network_family": generated["network_model"],
            "variant_index": variant,
            "migrated_from_scenario_id": legacy_scenario_id,
            "migration_reason": "legacy scenario failed complete-state replay in the formal pandapower 3.3.2 environment",
            "replacement_scenario_id": replacement["scenario_id"],
            "replacement_scenario_sha256": hashlib.sha256(
                json.dumps(replacement, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    )
    generated["metadata"] = metadata
    return generated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-scenarios", required=True)
    parser.add_argument("--current-scenarios", required=True)
    parser.add_argument("--frozen-records", required=True)
    parser.add_argument("--output-records", required=True)
    parser.add_argument("--checksum-manifest", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    args = parser.parse_args()

    legacy_path = ROOT / args.legacy_scenarios
    current_path = ROOT / args.current_scenarios
    frozen_path = ROOT / args.frozen_records
    output_path = ROOT / args.output_records
    legacy_rows = read_json(legacy_path)
    current_rows = read_json(current_path)
    frozen_rows = read_jsonl(frozen_path)

    replay_results = {}
    invalid_rows = []
    for row in legacy_rows:
        ok, error = replayable(row)
        replay_results[str(row["scenario_id"])] = {"replayable": ok, "error": error}
        if not ok:
            invalid_rows.append(row)
    replacements = choose_replacements(invalid_rows, current_rows)

    migrated = []
    migrated_count = 0
    for row in frozen_rows:
        legacy_id = str(row.get("scenario_id") or row.get("source_simulation_case_id") or "")
        replacement = replacements.get(legacy_id)
        if replacement is None:
            migrated.append(row)
        else:
            migrated.append(rebuild_record(row, replacement, legacy_id))
            migrated_count += 1

    ids = [str(row.get("id") or "") for row in migrated]
    if not migrated or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("migrated frozen records must retain unique non-empty IDs")
    expected_migrated = sum(
        1
        for row in frozen_rows
        if str(row.get("scenario_id") or row.get("source_simulation_case_id") or "") in replacements
    )
    if migrated_count != expected_migrated or len(migrated) != len(frozen_rows):
        raise ValueError("migration changed record cardinality or missed linked records")
    current_ids = {str(row.get("scenario_id") or "") for row in current_rows}
    if any(str(row["scenario_id"]) not in current_ids for row in replacements.values()):
        raise ValueError("replacement scenario missing from current formal source")

    write_jsonl(output_path, migrated)
    output_hash = sha256(output_path)
    write_json(ROOT / args.checksum_manifest, {args.output_records: output_hash})
    mapping = {
        legacy_id: {
            "replacement_scenario_id": replacement["scenario_id"],
            "network_model": replacement["network_model"],
            "contingency_type": replacement["contingency_type"],
            "legacy_load_level": next(
                float(row.get("load_level") or 1.0) for row in invalid_rows if row["scenario_id"] == legacy_id
            ),
            "replacement_load_level": float(replacement.get("load_level") or 1.0),
            "legacy_replay_error": replay_results[legacy_id]["error"],
        }
        for legacy_id, replacement in sorted(replacements.items())
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "legacy_scenarios": args.legacy_scenarios,
        "legacy_scenarios_sha256": sha256(legacy_path),
        "current_scenarios": args.current_scenarios,
        "current_scenarios_sha256": sha256(current_path),
        "frozen_records": args.frozen_records,
        "frozen_records_sha256": sha256(frozen_path),
        "output_records": args.output_records,
        "output_records_sha256": output_hash,
        "legacy_scenario_count": len(legacy_rows),
        "replayable_legacy_scenario_count": len(legacy_rows) - len(invalid_rows),
        "invalid_legacy_scenario_count": len(invalid_rows),
        "invalid_legacy_scenario_ids": sorted(replacements),
        "record_count_before": len(frozen_rows),
        "record_count_after": len(migrated),
        "migrated_record_count": migrated_count,
        "task_counts_after": dict(Counter(row.get("task_type") for row in migrated)),
        "scenario_migration": mapping,
        "cardinality_preserved": len(migrated) == len(frozen_rows),
        "ids_preserved": set(ids) == {str(row.get("id") or "") for row in frozen_rows},
    }
    write_json(ROOT / args.report_json, report)
    lines = [
        "# Frozen External Topology Migration",
        "",
        f"- Status: `{report['status']}`",
        f"- Legacy scenarios replayed: {report['legacy_scenario_count']}",
        f"- Replayable without migration: {report['replayable_legacy_scenario_count']}",
        f"- Migrated scenarios: {report['invalid_legacy_scenario_count']}",
        f"- Migrated records: {report['migrated_record_count']}",
        f"- Record cardinality preserved: {report['cardinality_preserved']}",
        "",
        "Every failed legacy replay is retained in the JSON audit with its error and deterministic replacement mapping.",
    ]
    (ROOT / args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
