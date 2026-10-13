from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from create_deterministic_surface_components import (  # noqa: E402
    build_compact_index,
    exact_intersection_size,
    grams,
    surface_text,
)


def test_exact_intersection_size_matches_numpy() -> None:
    left = np.asarray([1, 3, 8, 21], dtype=np.uint32)
    right = np.asarray([0, 3, 8, 9, 21], dtype=np.uint32)
    assert exact_intersection_size(left, right) == 3


def test_prefix_filter_retrieves_every_high_jaccard_pair() -> None:
    rows = [
        {"id": "a", "task_type": "x", "instruction": "alpha beta gamma", "input": {}},
        {"id": "b", "task_type": "x", "instruction": "alpha beta gamma delta", "input": {}},
        {"id": "c", "task_type": "x", "instruction": "unrelated wording", "input": {}},
        {"id": "d", "task_type": "x", "instruction": "alpha beta gamma", "input": {}},
    ]
    threshold = 0.85
    sets, tokens, starts, ends, prefix_lengths, posting_rows, _, _ = build_compact_index(
        rows, ngram_size=3, threshold=threshold
    )
    retrieved: set[tuple[int, int]] = set()
    for index, values in enumerate(sets):
        for token in values[: prefix_lengths[index]]:
            location = int(np.searchsorted(tokens, token))
            for other in posting_rows[int(starts[location]) : int(ends[location])]:
                if int(other) > index:
                    retrieved.add((index, int(other)))
    for left in range(len(sets)):
        for right in range(left + 1, len(sets)):
            inter = exact_intersection_size(sets[left], sets[right])
            union = sets[left].size + sets[right].size - inter
            score = inter / max(union, 1)
            if score >= threshold:
                assert (left, right) in retrieved


def test_surface_ngrams_are_deterministic() -> None:
    row = {"task_type": "x", "instruction": "A  B", "input": {"z": 1}}
    assert grams(surface_text(row), 5) == grams(surface_text(row), 5)
