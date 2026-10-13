#!/usr/bin/env python3
"""Build the acyclic metadata projection consumed by the milestone audit."""

from __future__ import annotations

import argparse
import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from build_release_bundle import build_archive_metadata
from gridinstruct_utils import ROOT, read_json, write_json


def record_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def build_projection(
    dataset: Path,
    *,
    dataset_path: str,
    version: str,
    archive: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "pass",
        "projection_scope": "pre-audit dataset identity and external-availability inputs",
        "dataset": {
            "version": version,
            "record_count": record_count(dataset),
            "dataset_path": dataset_path,
        },
        "availability": {
            "data_doi": archive.get("data_doi"),
            "code_repository_url": archive.get("public_repository_url"),
            "archived_code_release_doi": archive.get("archived_code_release_doi"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--dataset-version", default="1.2-sd-core")
    parser.add_argument("--archive-metadata", default="metadata/archive_metadata.json")
    parser.add_argument(
        "--output",
        default="reports/preaudit_dataset_metadata_v1.2_sd_core.json",
    )
    args = parser.parse_args()

    archive_path = ROOT / args.archive_metadata
    existing_archive = read_json(archive_path) if archive_path.is_file() else {}
    archive = build_archive_metadata(
        Namespace(
            public_repository_url=None,
            data_doi=None,
            archived_code_release_doi=None,
        ),
        existing_archive,
    )
    write_json(archive_path, archive)
    projection = build_projection(
        ROOT / args.dataset,
        dataset_path=args.dataset,
        version=args.dataset_version,
        archive=archive,
    )
    write_json(ROOT / args.output, projection)
    print(json.dumps(projection, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
