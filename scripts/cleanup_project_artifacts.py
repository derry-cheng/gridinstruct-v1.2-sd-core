#!/usr/bin/env python3
"""Remove explicitly listed stale or reproducible heavyweight artifacts.

The cleanup is intentionally conservative: canonical data, ID manifests,
machine-readable reports, prediction files, logs, source code, and the paper
are retained.  Only transformer checkpoints and the superseded legacy
experiment directory are eligible for removal.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> None:
    legacy = ROOT / "experiments/04_transformer_classification"
    current = ROOT / "experiments/near_neighbor_free_multiseed_v1.2_sd_core"
    targets: list[Path] = []
    targets.extend(
        ROOT / name
        for name in (
            "main.aux",
            "main.fdb_latexmk",
            "main.fls",
            "main.log",
            ".pytest_cache",
            ".ruff_cache",
        )
    )
    targets.append(ROOT / "scripts/__pycache__")
    targets.extend(sorted(ROOT.rglob("__pycache__")))
    if legacy.exists():
        targets.append(legacy)
    if current.exists():
        targets.extend(sorted(current.glob("*/seed*/checkpoints")))
        targets.extend(sorted(current.glob("*/seed*/best_model_state.pt")))
    targets = list(dict.fromkeys(path for path in targets if path.exists()))
    before = sum(size_bytes(path) for path in targets)
    removed: list[str] = []
    for path in targets:
        relative = path.relative_to(ROOT)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(relative))
    after = sum(size_bytes(path) for path in targets if path.exists())
    output = ROOT / "reports/project_cleanup_2026-08-06.json"
    previous = None
    if output.exists():
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "removed_count": len(removed),
        "removed_paths": removed,
        "bytes_before": before,
        "bytes_after": after,
        "bytes_reclaimed": before - after,
        "prior_cleanup": previous.get("prior_cleanup", previous) if previous else None,
        "retained": [
            "canonical data and ID-only split manifests",
            "all benchmark reports, predictions, and training logs",
            "paper sources, figures, release manifest, and audit scripts",
        ],
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
