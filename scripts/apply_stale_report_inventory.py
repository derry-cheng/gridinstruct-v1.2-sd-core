#!/usr/bin/env python3
"""Delete only hash-validated stale report snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "reports/stale_report_cleanup_inventory_v1.2_sd_core.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    removed = []
    for item in inventory["entries"]:
        path = ROOT / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise RuntimeError(f"stale report changed or missing: {item['path']}")
        path.unlink()
        removed.append(item["path"])
    inventory["status"] = "deleted_after_hash_validation"
    inventory["removed_count"] = len(removed)
    inventory["removed_paths"] = removed
    INVENTORY.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": inventory["status"], "removed_count": len(removed), "removed_bytes": inventory["candidate_bytes"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
