#!/usr/bin/env python3
"""Create a conservative inventory of superseded local artifacts before deletion."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/cleanup_inventory_v1.2_sd_core.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    candidates = sorted(
        [path for path in (ROOT / "data/stages").rglob("*") if path.is_file()]
        + [ROOT / "experiments/04_transformer_classification/operation_ticket_check/checkpoints/best_model_state.pt"]
        + [ROOT / "paper/scientific_data_latex/build_embedded/main_with_bbl.tex"]
    )
    records = []
    for path in candidates:
        records.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "reason": (
                    "superseded intermediate construction/probe stage; the current release is bound to canonical JSONL and current audits"
                    if "data/stages" in str(path)
                    else "obsolete transformer checkpoint not used by the current CPU evidence"
                    if "best_model_state.pt" in str(path)
                    else "stale embedded manuscript build; current manuscript is paper/scientific_data_latex/main.tex"
                ),
                "manifest_referenced": False,
            }
        )
    total = sum(item["bytes"] for item in records)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "inventory_only_before_deletion",
        "candidate_count": len(records),
        "candidate_bytes": total,
        "candidate_gib": round(total / (1024**3), 3),
        "policy": "Only explicitly listed files may be removed; current canonical tables, split files, audits, figures, manifests, and source diagrams are preserved.",
        "candidates": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
