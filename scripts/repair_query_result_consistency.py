#!/usr/bin/env python3
"""Repair intelligent-data-query answers by re-executing stored structured queries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gridinstruct_utils import ROOT, read_json, read_jsonl, write_json
from query_contract import (
    QUERY_CONTRACT_VERSION,
    canonical,
    execute_query,
    normalize_query_mirrors,
    query_projection,
    truth_is_complete,
    upgrade_legacy_overload_contract,
    validate_query_contract,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def analyze_file(
    path: Path,
    scenarios: dict[str, dict[str, Any]],
    *,
    require_complete_truth: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    input_hash = sha256(path)
    rows = read_jsonl(path)
    changed = []
    query_rows = 0
    missing_scenarios = []
    incomplete_scenarios = []
    invalid_rows = []
    executed_query_rows = 0
    for idx, row in enumerate(rows):
        if row.get("task_type") != "intelligent_data_query":
            continue
        query_rows += 1
        try:
            migrated = normalize_query_mirrors(row)
        except ValueError as exc:
            invalid_rows.append({"id": row.get("id"), "reason": str(exc)})
            continue
        query = migrated["structured_query"]
        scenario_id = str(query["scenario_id"])
        scenario = scenarios.get(str(scenario_id)) if scenario_id else None
        if not scenario:
            missing_scenarios.append({"id": row.get("id"), "scenario_id": scenario_id})
            continue
        if require_complete_truth and not truth_is_complete(scenario):
            incomplete_scenarios.append({"id": row.get("id"), "scenario_id": scenario_id})
            continue
        if not truth_is_complete(scenario):
            metadata = dict(migrated.get("metadata") or {})
            metadata["query_truth_status"] = "solver_failed_state_bound"
            migrated["metadata"] = metadata
        migrated = upgrade_legacy_overload_contract(migrated, scenario)
        query = migrated["structured_query"]
        try:
            expected = execute_query(query, scenario)
        except ValueError as exc:
            invalid_rows.append({"id": row.get("id"), "reason": str(exc)})
            continue
        executed_query_rows += 1
        actual = migrated.get("query_result")
        output = migrated["output"]
        output_actual = output.get("query_result")
        contract_changed = canonical(migrated) != canonical(row)
        result_changed = canonical(actual) != canonical(expected) or canonical(output_actual) != canonical(expected)
        migrated["query_result"] = expected
        migrated["output"]["query_result"] = expected
        contract_errors = validate_query_contract(migrated, scenario)
        if contract_errors:
            invalid_rows.append({"id": row.get("id"), "reason": ";".join(contract_errors)})
            continue
        rows[idx] = migrated
        if contract_changed or result_changed:
            changed.append(
                {
                    "id": row.get("id"),
                    "filter": query.get("filter"),
                    "scenario_id": scenario_id,
                    "old_preview": canonical(actual)[:300],
                    "new_preview": canonical(expected)[:300],
                }
            )
    summary = {
        "path": str(path.relative_to(ROOT)),
        "input_sha256": input_hash,
        "output_sha256": input_hash,
        "records": len(rows),
        "query_rows": query_rows,
        "executed_query_rows": executed_query_rows,
        "changed": len(changed),
        "missing_scenario_count": len(missing_scenarios),
        "incomplete_truth_count": len(incomplete_scenarios),
        "missing_scenario_examples": missing_scenarios[:20],
        "incomplete_truth_examples": incomplete_scenarios[:20],
        "invalid_row_count": len(invalid_rows),
        "invalid_row_examples": invalid_rows[:20],
        "examples": changed[:20],
        "input_query_projection_sha256": object_sha256(query_projection(read_jsonl(path))),
        "output_query_projection_sha256": object_sha256(query_projection(rows)),
    }
    return summary, rows


def atomic_commit_validated(
    output_path: Path,
    rows: list[dict[str, Any]],
    scenarios: dict[str, dict[str, Any]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        reread = read_jsonl(temporary)
        errors = []
        for row in reread:
            if row.get("task_type") != "intelligent_data_query":
                continue
            scenario = scenarios.get(str((row.get("structured_query") or {}).get("scenario_id")))
            errors.extend((row.get("id"), reason) for reason in validate_query_contract(row, scenario))
        if errors or canonical(query_projection(reread)) != canonical(query_projection(rows)):
            raise ValueError(f"candidate reread query-contract validation failed: {errors[:10]}")
        os.replace(temporary, output_path)
        directory = os.open(output_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_md(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Query Result Consistency Repair",
        "",
        f"Generated: {payload['generated_at']}",
        f"Scenario source: `{payload['scenarios']}`",
        f"Files scanned: {payload['files_scanned']}",
        f"Rows repaired: {payload['rows_repaired']}",
        f"Query rows checked: {payload['query_rows_checked']}",
        f"Missing scenarios: {payload['missing_scenario_count']}",
        f"Incomplete scenario truth: {payload['incomplete_truth_count']}",
        "",
        "## File Summary",
        "",
    ]
    for item in payload["files"]:
        lines.append(f"- `{item['path']}`: {item['changed']}")
    examples = [ex for item in payload["files"] for ex in item.get("examples", [])]
    if examples:
        lines.extend(["", "## Example Repairs", ""])
        for ex in examples[:20]:
            lines.append(f"- `{ex['id']}` filter={ex['filter']} scenario={ex['scenario_id']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="simulation_outputs/contingency/scenarios_converged.json")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dataset", required=True)
    parser.add_argument("--allow-incomplete-truth", action="store_true")
    parser.add_argument("--output-json", default="reports/query_result_consistency_repair_v1.2_sd_core.json")
    parser.add_argument("--output-md", default="reports/query_result_consistency_repair_v1.2_sd_core.md")
    args = parser.parse_args()

    scenario_rows = read_json(ROOT / args.scenarios)
    if not isinstance(scenario_rows, list) or not scenario_rows:
        raise ValueError("scenario truth source must be a non-empty list")
    scenario_ids = [str(row.get("scenario_id") or "") for row in scenario_rows]
    if any(not value for value in scenario_ids) or len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("scenario truth source contains missing or duplicate scenario IDs")
    scenarios = {row["scenario_id"]: row for row in scenario_rows}
    input_path = ROOT / args.dataset
    output_path = ROOT / args.output_dataset
    if input_path.resolve() == output_path.resolve():
        raise ValueError("--dataset and --output-dataset must be different immutable stages")
    files = [input_path]
    summaries = []
    staged_rows: list[tuple[Path, Path, list[dict[str, Any]], dict[str, Any]]] = []
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
        summary, repaired_rows = analyze_file(
            path,
            scenarios,
            require_complete_truth=not args.allow_incomplete_truth,
        )
        summaries.append(summary)
        staged_rows.append((path, output_path, repaired_rows, summary))
    missing_scenario_count = sum(item["missing_scenario_count"] for item in summaries)
    incomplete_truth_count = sum(item["incomplete_truth_count"] for item in summaries)
    invalid_row_count = sum(item["invalid_row_count"] for item in summaries)
    query_rows_checked = sum(item["query_rows"] for item in summaries)
    executed_query_rows = sum(item["executed_query_rows"] for item in summaries)
    preflight_pass = bool(
        summaries
        and query_rows_checked > 0
        and missing_scenario_count == 0
        and incomplete_truth_count == 0
        and invalid_row_count == 0
        and executed_query_rows == query_rows_checked
    )
    if preflight_pass:
        for input_path, output_path, repaired_rows, summary in staged_rows:
            atomic_commit_validated(output_path, repaired_rows, scenarios)
            summary["output_path"] = str(output_path.relative_to(ROOT))
            summary["output_sha256"] = sha256(output_path)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenarios": args.scenarios,
        "files_scanned": len(summaries),
        "rows_repaired": sum(item["changed"] for item in summaries),
        "query_rows_checked": query_rows_checked,
        "query_rows_executed": executed_query_rows,
        "missing_scenario_count": missing_scenario_count,
        "incomplete_truth_count": incomplete_truth_count,
        "invalid_row_count": invalid_row_count,
        "transactional_preflight_pass": preflight_pass,
        "files_written_after_global_preflight": sum(
            1 for input_path, output_path, _, item in staged_rows if preflight_pass and (item["changed"] or output_path != input_path)
        ),
        "truth_completeness_required": not args.allow_incomplete_truth,
        "contract_schema_version": QUERY_CONTRACT_VERSION,
        "scenario_truth_sha256": sha256(ROOT / args.scenarios),
        "candidate_reread_validation_pass": preflight_pass,
        "atomic_commit_performed": preflight_pass,
        "status": "pass" if preflight_pass else "fail",
        "files": summaries,
    }
    write_json(ROOT / args.output_json, payload)
    write_md(ROOT / args.output_md, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
