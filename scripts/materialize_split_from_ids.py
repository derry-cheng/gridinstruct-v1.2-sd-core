#!/usr/bin/env python3
"""Materialize an ID-only split manifest into a temporary JSONL file.

The canonical dataset remains the only in-project copy.  This helper is used
for local benchmark runs and writes the requested split outside the project so
the release stays below the storage budget.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", required=True)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    ids = set()
    with Path(args.ids).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                ids.add(str(value["id"]))
    if not ids:
        raise SystemExit("ID manifest is empty")
    found = set()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with Path(args.canonical).open(encoding="utf-8") as source, output.open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("id")) in ids:
                target.write(json.dumps(row, ensure_ascii=False) + "\n")
                found.add(str(row["id"]))
    missing = ids - found
    if missing:
        raise SystemExit(f"manifest IDs missing from canonical input: {len(missing)}")
    print(json.dumps({"output": str(output), "records": len(found), "sha256": __import__('hashlib').sha256(output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
