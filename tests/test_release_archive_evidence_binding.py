from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_release_archive_replay import evidence_binding_errors  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_isolated_replay_rechecks_every_bound_artifact(tmp_path: Path) -> None:
    dataset = tmp_path / "data/current.jsonl"
    report = tmp_path / "reports/query.json"
    dataset.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    dataset.write_text("{}\n", encoding="utf-8")
    report.write_text('{"status":"pass"}\n', encoding="utf-8")
    evidence = {
        "current_inputs": {
            "dataset": {"path": "data/current.jsonl", "sha256": digest(dataset)}
        },
        "evidence": {
            "query": {"path": "reports/query.json", "sha256": digest(report)}
        },
    }

    assert evidence_binding_errors(tmp_path, evidence) == []

    report.write_text('{"status":"changed"}\n', encoding="utf-8")
    assert evidence_binding_errors(tmp_path, evidence) == [
        "bound_file_hash_mismatch:reports/query.json"
    ]

    report.unlink()
    assert evidence_binding_errors(tmp_path, evidence) == [
        "bound_file_missing:reports/query.json"
    ]
