#!/usr/bin/env python3
"""Delete only hash-validated entries from cleanup_inventory_v1.2_sd_core.json."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "reports/cleanup_inventory_v1.2_sd_core.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    removed = []
    for item in inventory["candidates"]:
        path = ROOT / item["path"]
        if not path.is_file():
            raise FileNotFoundError(item["path"])
        if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise RuntimeError(f"cleanup target changed: {item['path']}")
        path.unlink()
        removed.append(item["path"])
    for directory in sorted({path.parent for path in (ROOT / "data/stages").rglob("*") if path.is_dir()}, reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    for directory in [
        ROOT / "data/stages",
        ROOT / "experiments/04_transformer_classification/operation_ticket_check/checkpoints",
        ROOT / "paper/scientific_data_latex/build_embedded",
    ]:
        try:
            directory.rmdir()
        except OSError:
            pass
    inventory["status"] = "deleted_after_hash_validation"
    inventory["removed_count"] = len(removed)
    inventory["removed_paths"] = removed
    INVENTORY.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": inventory["status"], "removed_count": len(removed), "removed_bytes": inventory["candidate_bytes"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
