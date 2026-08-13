#!/usr/bin/env python3
"""Seed translation cache by deterministic template rematerialization.

New Chinese strings that share the same soft skeleton as a validated English
cache entry (scenario IDs / network names / numeric literals as typed slots)
are rematerialized by substituting those slots into the cached English text.
This is the same class of operation as scenario-id rematerialization for the
overlap corpus: no free-form paraphrase, no fuzzy matching.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from translate_sd_core_to_english import (
    append_cache,
    contains_cjk,
    iter_cjk_strings,
    load_cache,
    stable_hash,
)

SCENARIO_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b", re.I)
NET_RE = re.compile(r"\b(?:IEEE|PEGASE|RTE|GSO|ACTIVSG|ILLINOIS)\s*\d+\b", re.I)
NUM_RE = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?")


def soft_skel(text: str) -> tuple[str, list[str]]:
    toks: list[str] = []

    def sub(pattern: re.Pattern[str], kind: str, value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            toks.append(match.group(0))
            return "{%s}" % kind

        return pattern.sub(repl, value)

    value = sub(SCENARIO_RE, "S", text)
    value = sub(NET_RE, "NET", value)
    value = sub(NUM_RE, "N", value)
    return value, toks


def apply_slot_map(source_tokens: list[str], english: str, new_tokens: list[str]) -> str | None:
    if len(source_tokens) != len(new_tokens):
        return None
    out = english
    markers: list[tuple[str, str, bool]] = []
    for index, old in enumerate(source_tokens):
        mark = f"@@T{index}@@"
        if old not in out:
            markers.append((mark, new_tokens[index], False))
            continue
        out = out.replace(old, mark)
        markers.append((mark, new_tokens[index], True))
    for mark, new, found in markers:
        if found:
            out = out.replace(mark, new)
    if contains_cjk(out):
        return None
    return out


def collect_missing_unique(source_path: Path, previous_english_path: Path, cache: dict[str, dict[str, str]]) -> dict[str, str]:
    previous_ids: set[str] = set()
    with previous_english_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                previous_ids.add(str(json.loads(line)["id"]))
    unique: dict[str, str] = {}
    with source_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row["id"]) in previous_ids:
                continue
            for _, text in iter_cjk_strings(row):
                unique.setdefault(stable_hash(text), text)
    return {key: text for key, text in unique.items() if key not in cache}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--previous-english", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--cache", default="metadata/translation_cache_v1.jsonl")
    parser.add_argument("--report-json", default="reports/translation_cache_template_rematerialization_v1.2_sd_core.json")
    args = parser.parse_args()

    cache_path = Path(args.cache)
    cache = load_cache(cache_path)
    by_skel: dict[str, list[tuple[list[str], str, str]]] = defaultdict(list)
    for key, row in cache.items():
        source = row.get("source") or ""
        translation = row.get("translation") or ""
        if not source or not translation or contains_cjk(translation):
            continue
        skeleton, tokens = soft_skel(source)
        by_skel[skeleton].append((tokens, translation, key))

    # Deterministic candidate preference: lexicographic cache key.
    for skeleton, candidates in by_skel.items():
        candidates.sort(key=lambda item: item[2])

    missing = collect_missing_unique(Path(args.source), Path(args.previous_english), cache)
    seeded: dict[str, dict[str, str]] = {}
    unresolved = 0
    now = datetime.now(timezone.utc).isoformat()
    for key, text in missing.items():
        skeleton, tokens = soft_skel(text)
        candidates = by_skel.get(skeleton) or []
        rematerialized = None
        parent_key = None
        for source_tokens, english, parent in candidates:
            rematerialized = apply_slot_map(source_tokens, english, tokens)
            if rematerialized is not None:
                parent_key = parent
                break
        if rematerialized is None:
            unresolved += 1
            continue
        seeded[key] = {
            "source": text,
            "translation": rematerialized,
            "provider": "template_rematerialization",
            "model": "validated_cache_slot_substitution",
            "key_index": "0",
            "translated_at": now,
            "parent_cache_key": parent_key or "",
        }

    if seeded:
        append_cache(cache_path, seeded)

    report: dict[str, Any] = {
        "generated_at": now,
        "status": "pass",
        "cache": args.cache,
        "missing_before": len(missing),
        "seeded": len(seeded),
        "unresolved": unresolved,
        "method": "soft_skeleton_slot_substitution_from_validated_cache",
    }
    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
