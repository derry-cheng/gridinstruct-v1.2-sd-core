from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_structured_query_filter_baseline as baseline  # noqa: E402


def test_structured_query_baseline_imports_available_token_f1() -> None:
    assert baseline.token_f1("a b", "a b") == 1.0
    assert baseline.token_f1("a b", "a") > 0.0
