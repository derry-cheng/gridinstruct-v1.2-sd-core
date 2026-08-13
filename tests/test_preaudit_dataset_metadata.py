from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_preaudit_dataset_metadata import build_projection  # noqa: E402


def test_preaudit_projection_is_acyclic_and_uses_current_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "release_en.jsonl"
    dataset.write_text('{"id":"1"}\n{"id":"2"}\n', encoding="utf-8")
    archive = {
        "public_repository_url": "https://example.org/repository",
        "data_doi": "https://doi.org/10.1234/data",
        "archived_code_release_doi": "https://doi.org/10.1234/code",
    }

    projection = build_projection(
        dataset,
        dataset_path="data/release_en.jsonl",
        version="1.2-sd-core",
        archive=archive,
    )

    assert projection["dataset"] == {
        "version": "1.2-sd-core",
        "record_count": 2,
        "dataset_path": "data/release_en.jsonl",
    }
    assert projection["availability"]["data_doi"] == archive["data_doi"]
    assert "audit_reference" not in projection
