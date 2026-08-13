from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_dataset_stage as promote  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_promotion_requires_passing_bound_transaction(tmp_path, monkeypatch) -> None:
    source = tmp_path / "stage.jsonl"
    target = tmp_path / "canonical.jsonl"
    gate = tmp_path / "gate.json"
    manifest = tmp_path / "manifest.json"
    source.write_text('{"id":"one"}\n', encoding="utf-8")
    gate.write_text(json.dumps({"status": "fail", "transaction": {"committed": False}}), encoding="utf-8")
    monkeypatch.setattr(promote, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "promote_dataset_stage.py",
            "--input",
            "stage.jsonl",
            "--output",
            "canonical.jsonl",
            "--manifest",
            "manifest.json",
            "--upstream-gate",
            "gate.json",
        ],
    )

    with pytest.raises(ValueError):
        promote.main()
    assert not target.exists()
    assert not manifest.exists()

    gate.write_text(
        json.dumps(
            {
                "status": "pass",
                "transaction": {
                    "committed": True,
                    "artifacts": {"stage.jsonl": _sha256(source)},
                },
            }
        ),
        encoding="utf-8",
    )
    promote.main()
    assert target.read_bytes() == source.read_bytes()
    state = json.loads(manifest.read_text(encoding="utf-8"))
    assert state["status"] == "pass"
    assert state["upstream_gate_sha256"] == _sha256(gate)

    verification = tmp_path / "verification.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "promote_dataset_stage.py",
            "--verify-only",
            "--output",
            "canonical.jsonl",
            "--manifest",
            "manifest.json",
            "--verification-report",
            "verification.json",
        ],
    )
    promote.main()
    verified = json.loads(verification.read_text(encoding="utf-8"))
    assert verified["canonical_dataset_immutable_since_promotion"] is True

    target.write_text('{"id":"mutated"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="mutated_after_promotion"):
        promote.main()
