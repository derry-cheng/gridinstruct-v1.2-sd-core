#!/usr/bin/env python3
"""Recompute transfer metrics from the released labels and raw predictions."""

from __future__ import annotations

import argparse
import io
import json
import statistics
import tarfile
from contextlib import contextmanager
from pathlib import Path

from sklearn.metrics import accuracy_score, f1_score

from create_strict_source_group_splits import provenance_keys
from gridinstruct_utils import ROOT, write_json


PREFIX = "benchmark/local_instruction_transfer_20261001"
LABELS = ["compliant", "compliant_with_monitoring", "non_compliant"]


def validate(archive_path=None):
    archive = tarfile.open(archive_path, "r:gz") if archive_path else None

    @contextmanager
    def member(relative):
        if archive:
            with io.TextIOWrapper(archive.extractfile(relative), encoding="utf-8") as handle:
                yield handle
        else:
            with (ROOT / relative).open(encoding="utf-8") as handle:
                yield handle

    def read_json(relative):
        with member(relative) as handle:
            return json.load(handle)

    def read_predictions(name):
        with member(f"{PREFIX}/{name}") as handle:
            rows = [json.loads(line) for line in handle]
        result = {row["id"]: row for row in rows}
        if len(result) != len(rows):
            raise ValueError(f"duplicate prediction identifiers: {name}")
        return result

    def metric(predictions, ids):
        gold = [predictions[item]["gold"] for item in ids]
        values = [predictions[item]["prediction"] for item in ids]
        return {"macro_f1": f1_score(gold, values, labels=LABELS, average="macro", zero_division=0),
                "accuracy": accuracy_score(gold, values)}

    try:
        summary = read_json("reports/local_instruction_transfer_20261001.json")
        ids = read_json(f"{PREFIX}/sample_ids.json")
        base = read_predictions("base_predictions.jsonl")
        needed = set(base)
        sources, split_keys = {}, {}
        for split in ("train", "validation", "test"):
            selected = set(ids[split])
            found, keys = set(), set()
            with member(f"data/v1.2_sd_core_strict_{split}.jsonl") as handle:
                for line in handle:
                    row = json.loads(line)
                    if row["id"] in selected:
                        found.add(row["id"])
                        keys.update(provenance_keys(row))
                    if split == "test" and row["id"] in needed:
                        sources[row["id"]] = row
            if found != selected:
                raise ValueError(f"sample identifiers absent from {split}")
            split_keys[split] = keys
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
            if split_keys[left] & split_keys[right]:
                raise ValueError(f"provenance overlap: {left}/{right}")
        if set(sources) != needed:
            raise ValueError("evaluation identifier absent from strict test")
        computed = {"base": metric(base, ids["test"])}
        lexical = read_predictions("tfidf_predictions.jsonl")
        computed["tfidf"] = metric(lexical, ids["test"])
        if abs(computed["tfidf"]["macro_f1"] - summary["same_sample_tfidf"]["macro_f1"]) > 1e-12:
            raise ValueError("matched lexical baseline mismatch")
        runs = []
        report = read_json(f"{PREFIX}/report.json")
        for seed in summary["protocol"]["training_seeds"]:
            predictions = read_predictions(f"seed_{seed}_predictions.jsonl")
            if set(predictions) != needed:
                raise ValueError(f"evaluation denominator changed for seed {seed}")
            for row_id, item in predictions.items():
                if item["gold"] != sources[row_id]["compliance_label"]:
                    raise ValueError(f"reference label mismatch: {row_id}")
                if LABELS[max(range(3), key=lambda index: item["label_log_probabilities"][index])] != item["prediction"]:
                    raise ValueError(f"candidate scoring mismatch: {row_id}")
            value = metric(predictions, ids["test"])
            expected = next(row["tuned"] for row in report["runs"] if row["seed"] == seed)
            if abs(value["macro_f1"] - expected["macro_f1"]) > 1e-12:
                raise ValueError(f"stored metric mismatch: seed {seed}")
            computed[str(seed)] = value
            runs.append(value["macro_f1"])
        for row_id, item in base.items():
            if item["gold"] != sources[row_id]["compliance_label"]:
                raise ValueError(f"pretrained reference mismatch: {row_id}")
        mean = statistics.mean(runs)
        if abs(mean - summary["tuned_macro_f1_mean"]) > 1e-12:
            raise ValueError("aggregate macro-F1 mismatch")
        if abs(computed["base"]["macro_f1"] - summary["base"]["macro_f1"]) > 1e-12:
            raise ValueError("pretrained macro-F1 mismatch")
        with member("data/revision_20261001/request_variant_probe.jsonl") as handle:
            variants = [json.loads(line) for line in handle]
        source_ids = {row["source_record_id"] for row in variants}
        probe_sources = {}
        with member("data/v1.2_sd_core_strict_test.jsonl") as handle:
            for line in handle:
                row = json.loads(line)
                if row["id"] in source_ids:
                    probe_sources[row["id"]] = row
        if len(source_ids) != 32 or len(variants) != 64 or set(probe_sources) != source_ids:
            raise ValueError("controlled request denominator mismatch")
        probe_keys = {key for row in probe_sources.values() for key in provenance_keys(row)}
        if probe_keys & (split_keys["train"] | split_keys["validation"]):
            raise ValueError("controlled request source overlaps development provenance")
        groups = {}
        for row in variants:
            source = probe_sources[row["source_record_id"]]
            if row["input"] != source["input"] or row["compliance_label"] != source["compliance_label"]:
                raise ValueError("controlled request changed state or reference label")
            groups.setdefault(row["source_record_id"], []).append(row["id"])
        if any(len(group) != 2 for group in groups.values()):
            raise ValueError("controlled request pairing mismatch")
        for name in ["base"] + [f"seed_{seed}" for seed in summary["protocol"]["training_seeds"]]:
            predictions = read_predictions(f"{name}_request_variant_predictions.jsonl")
            if set(predictions) != {row["id"] for row in variants}:
                raise ValueError(f"controlled request prediction denominator mismatch: {name}")
            for row in variants:
                if predictions[row["id"]]["gold"] != row["compliance_label"]:
                    raise ValueError(f"controlled request reference mismatch: {name}")
            both = sum(all(predictions[item]["prediction"] == predictions[item]["gold"]
                           for item in pair) for pair in groups.values())
            expected = next(item["request_variant"] for item in summary["request_variants"]
                            if item["model"] == name)
            if both != expected["both_correct"]:
                raise ValueError(f"controlled request metric mismatch: {name}")
        return {"status": "pass", "test_records": len(ids["test"]), "raw_evaluation_records": len(needed),
                "test_scenarios": len({sources[item]["scenario_id"] for item in ids["test"]}),
                "provenance_overlap": 0, "reference_mismatches": 0,
                "controlled_request_sources": len(source_ids), "controlled_request_records": len(variants),
                "metrics": computed, "tuned_macro_f1_mean": mean}
    finally:
        if archive:
            archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", default="reports/local_instruction_transfer_replay_20261001.json")
    args = parser.parse_args()
    report = validate(args.archive)
    write_json(ROOT / args.output, report)
    print(json.dumps(report, indent=2))
