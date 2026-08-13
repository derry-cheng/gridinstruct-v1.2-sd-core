#!/usr/bin/env python3
"""Render the compact CSV file manifest from the current revision manifest."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVISION = ROOT / "metadata/current_revision_manifest_v1.2_sd_core.json"
OUTPUT = ROOT / "metadata/file_manifest.csv"


def main() -> None:
    manifest = json.loads(REVISION.read_text(encoding="utf-8"))
    rows: dict[str, dict] = {}
    for section in ("dataset", "required_artifacts", "evidence_reports", "metadata", "scripts"):
        for path, record in (manifest.get(section) or {}).items():
            if path == "metadata/file_manifest.csv":
                continue
            rows[path] = record
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bytes", "exists", "path", "records", "sha256"])
        for path in sorted(rows):
            record = rows[path]
            writer.writerow([record.get("bytes", ""), True, path, "", record.get("sha256", "")])
    print(json.dumps({"status": "pass", "path": str(OUTPUT.relative_to(ROOT)), "entries": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
