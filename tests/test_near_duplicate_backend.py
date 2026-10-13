from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_near_duplicates as audit  # noqa: E402


def test_missing_local_model_uses_deterministic_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    state = audit.semantic_backend_preflight(
        "models/missing",
        audit.DEFAULT_SEMANTIC_MODEL_ID,
        audit.DEFAULT_SEMANTIC_MODEL_REVISION,
        None,
        "deterministic_tfidf",
    )
    assert state["backend"] == "deterministic_tfidf_char_ngrams"
    assert state["revision"] == audit.DEFAULT_SEMANTIC_MODEL_REVISION
    rows = [
        {"id": "a", "task_type": "x", "instruction": "branch loading exceeds limit", "input": {}, "output": ""},
        {"id": "b", "task_type": "x", "instruction": "bus voltage is normal", "input": {}, "output": ""},
    ]
    report = audit.semantic_cross(rows, rows, state, threshold=0.95, batch_size=2)
    assert report["model"] == "deterministic_tfidf_char_ngrams_3_5"
    assert report["right_records_with_neighbor"] == 2
