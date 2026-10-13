#!/usr/bin/env python3
"""Repair ambiguous dispatcher requests and refresh dependent materialisations.

The promoted table contains dispatcher records whose target intent or target
slot can differ while the rendered request is identical.  This utility
re-renders the typed request semantics, refreshes rule-link evidence, and
updates every canonical-derived split without dropping records.  It writes
temporary outputs outside the repository and commits them only with
``--commit``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from expand_rule_taxonomy_and_links import infer_secondary_rules
from gridinstruct_utils import ROOT
from regenerate_direct_english_core import render_row


PAIR_TARGETS = {
    "data/v1.2_sd_core_train.jsonl": "data/v1.2_sd_core_train_en.jsonl",
    "data/v1.2_sd_core_validation.jsonl": "data/v1.2_sd_core_validation_en.jsonl",
    "data/v1.2_sd_core_test.jsonl": "data/v1.2_sd_core_test_en.jsonl",
    "data/v1.2_sd_core_ood_test.jsonl": "data/v1.2_sd_core_ood_test_en.jsonl",
}
SINGLE_TARGETS = (
    "data/v1.2_sd_core_strict_train.jsonl",
    "data/v1.2_sd_core_strict_validation.jsonl",
    "data/v1.2_sd_core_strict_test.jsonl",
    "data/v1.2_sd_core_instruction_surface_balanced_train_en.jsonl",
    "data/v1.2_sd_core_instruction_surface_balanced_validation_en.jsonl",
    "data/v1.2_sd_core_instruction_surface_balanced_test_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_train_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_validation_en.jsonl",
    "data/v1.2_sd_core_proxyreduced_test_en.jsonl",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_ids(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                ids.append(str(value["id"]))
    return ids


def contract_key(row: dict[str, Any]) -> str:
    return json.dumps(
        {
            "task_type": row.get("task_type"),
            "instruction": row.get("instruction"),
            "input": row.get("input"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def duplicate_profile(path: Path, split_by_id: dict[str, str]) -> dict[str, int]:
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            groups[contract_key(row)].append(
                (
                    str(row.get("id")),
                    json.dumps(row.get("output"), ensure_ascii=False, sort_keys=True),
                )
            )
    duplicate = [group for group in groups.values() if len(group) > 1]
    conflicts = [group for group in duplicate if len({item[1] for item in group}) > 1]
    cross_split = [
        group
        for group in duplicate
        if len({split_by_id.get(item[0], "?") for item in group}) > 1
    ]
    return {
        "duplicate_groups": len(duplicate),
        "duplicate_rows": sum(len(group) for group in duplicate),
        "duplicate_extra_rows": sum(len(group) - 1 for group in duplicate),
        "conflicting_output_groups": len(conflicts),
        "cross_split_duplicate_groups": len(cross_split),
    }


def write_transformed(source: Path, destination: Path) -> dict[str, int]:
    rows = 0
    changed_links = 0
    with source.open("r", encoding="utf-8") as source_handle, destination.open("w", encoding="utf-8") as target:
        for index, line in enumerate(source_handle):
            if not line.strip():
                continue
            row = render_row(json.loads(line), index)
            links, basis = infer_secondary_rules(row)
            metadata = row.setdefault("metadata", {})
            metadata["rule_taxonomy_version"] = "v1.2-sd-core-expanded"
            metadata["rule_linkage_stage"] = "pre_target_input_evidence"
            metadata["rule_link_evidence_fields"] = basis
            if links != row.get("source_regulation_ids"):
                changed_links += 1
            row["source_regulation_ids"] = links
            target.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            rows += 1
    return {"records": rows, "changed_rule_link_rows": changed_links}


def materialize_subset(transformed_by_id: dict[str, str], source: Path, destination: Path) -> int:
    ids = read_ids(source)
    missing = [record_id for record_id in ids if record_id not in transformed_by_id]
    if missing:
        raise ValueError(f"{source}: {len(missing)} IDs are absent from the transformed canonical table")
    with destination.open("w", encoding="utf-8") as handle:
        for record_id in ids:
            handle.write(transformed_by_id[record_id])
    return len(ids)


def replace_file(staged: Path, destination: Path) -> None:
    mode = destination.stat().st_mode & 0o777 if destination.exists() else 0o600
    os.replace(staged, destination)
    os.chmod(destination, mode)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--english-output", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--work-dir", default="/tmp/gaiav2_dispatcher_request_repair")
    parser.add_argument("--report-json", default="reports/dispatcher_request_contract_repair_v1.2_sd_core.json")
    parser.add_argument("--report-md", default="reports/dispatcher_request_contract_repair_v1.2_sd_core.md")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    input_path = ROOT / args.input
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    staged_full = work_dir / "gridinstruct_v1.2_sd_core_repaired.jsonl"
    before_split_ids: dict[str, list[str]] = {}
    split_by_id: dict[str, str] = {}
    all_targets = [
        relative
        for relative in list(PAIR_TARGETS) + list(SINGLE_TARGETS)
        if (ROOT / relative).is_file() and not relative.endswith("_ids.jsonl")
    ]
    for relative in all_targets:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        ids = read_ids(path)
        before_split_ids[relative] = ids
        if relative in PAIR_TARGETS:
            split_name = Path(relative).stem
            for record_id in ids:
                split_by_id[record_id] = split_name

    with tempfile.NamedTemporaryFile("w", dir=work_dir, delete=False, prefix=".repaired.", suffix=".jsonl") as handle:
        staged_full = Path(handle.name)
    summary = write_transformed(input_path, staged_full)

    transformed_by_id: dict[str, str] = {}
    with staged_full.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                transformed_by_id[str(row["id"])] = line
    if len(transformed_by_id) != summary["records"]:
        raise ValueError("transformed canonical table contains duplicate or missing identifiers")

    staged_subsets: dict[str, Path] = {}
    for relative, ids in before_split_ids.items():
        with tempfile.NamedTemporaryFile("w", dir=work_dir, delete=False, prefix=".subset.", suffix=".jsonl") as handle:
            staged = Path(handle.name)
        materialize_subset(transformed_by_id, ROOT / relative, staged)
        staged_subsets[relative] = staged

    before_profile = duplicate_profile(input_path, split_by_id)
    after_profile = duplicate_profile(staged_full, split_by_id)
    report = {
        "status": "pass"
        if after_profile["conflicting_output_groups"] == 0
        and after_profile["cross_split_duplicate_groups"] == 0
        else "fail",
        "committed": bool(args.commit),
        "input": args.input,
        "english_output": args.english_output,
        "records": summary["records"],
        "changed_rule_link_rows": summary["changed_rule_link_rows"],
        "before_prompt_input_profile": before_profile,
        "after_prompt_input_profile": after_profile,
        "scope": "all canonical rows retained; dispatcher request semantics and rule-evidence receipts regenerated from typed fields",
    }
    if args.commit:
        replace_file(staged_full, input_path)
        english_path = ROOT / args.english_output
        if english_path.exists():
            english_path.unlink()
        os.link(input_path, english_path)
        for relative, staged in staged_subsets.items():
            destination = ROOT / relative
            replace_file(staged, destination)
            paired = PAIR_TARGETS.get(relative)
            if paired:
                paired_path = ROOT / paired
                if paired_path.exists():
                    paired_path.unlink()
                os.link(destination, paired_path)
        report["canonical_sha256"] = sha256(input_path)
        report["english_sha256"] = sha256(english_path)
    else:
        report["staged_full"] = str(staged_full)
    report_path = ROOT / args.report_json
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = ROOT / args.report_md
    md_path.write_text(
        "\n".join(
            [
                "# Dispatcher Request Contract Repair",
                "",
                f"- Status: `{report['status']}`",
                f"- Committed: `{report['committed']}`",
                f"- Records retained: {report['records']:,}",
                f"- Rows with refreshed rule links/evidence: {report['changed_rule_link_rows']:,}",
                f"- Prompt/input duplicate groups before/after: {before_profile['duplicate_groups']}/{after_profile['duplicate_groups']}",
                f"- Conflicting output groups before/after: {before_profile['conflicting_output_groups']}/{after_profile['conflicting_output_groups']}",
                f"- Cross-split duplicate groups before/after: {before_profile['cross_split_duplicate_groups']}/{after_profile['cross_split_duplicate_groups']}",
                "",
                "All canonical records are retained. The repair makes the typed dispatcher request and target slot observable in the request surface and refreshes rule-link evidence pointers.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
