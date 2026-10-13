#!/usr/bin/env python3
"""Evaluate saved adapters on fixed-state requests and summarize training transfer."""

from __future__ import annotations

import argparse
import gc
import json
import random
import statistics
from collections import Counter, defaultdict
from importlib.metadata import version

import numpy as np
import torch
from peft import PeftConfig, get_peft_model, set_peft_model_state_dict
from safetensors.torch import load_file
from sklearn.metrics import f1_score
from transformers import AutoModelForCausalLM, AutoTokenizer

from create_strict_source_group_splits import provenance_keys
from gridinstruct_utils import ROOT, write_json, write_jsonl
from run_local_instruction_transfer import LABELS, RESULTS, load_rows, paired_metrics, predict


def read_predictions(name):
    with (RESULTS / name).open() as handle:
        return {row["id"]: row for line in handle if (row := json.loads(line))}


def clustered_delta(rows, before, after, seed, draws=1000):
    groups = defaultdict(list)
    for row in rows:
        groups[row["scenario_id"]].append(row["id"])
    ids = list(groups)
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(draws):
        sample = [record for group in rng.choice(ids, len(ids), replace=True) for record in groups[group]]
        gold = [before[item]["gold"] for item in sample]
        base = [before[item]["prediction"] for item in sample]
        tuned = [after[item]["prediction"] for item in sample]
        deltas.append(f1_score(gold, tuned, labels=LABELS, average="macro", zero_division=0) -
                      f1_score(gold, base, labels=LABELS, average="macro", zero_division=0))
    return np.quantile(deltas, [0.025, 0.975]).tolist()


def main(model_dir):
    config = json.loads((ROOT / "experiments/revision_20261001/protocol.json").read_text())
    variant_config = json.loads((ROOT / "experiments/revision_20261001/request_variant_protocol.json").read_text())
    report = json.loads((RESULTS / "report.json").read_text())
    if report["status"] != "complete":
        raise ValueError("training must finish before the saved-model diagnostic")
    all_test = load_rows("test")
    ids = json.loads((RESULTS / "sample_ids.json").read_text())
    samples = {split: [row for row in load_rows(split) if row["id"] in set(ids[split])]
               for split in ("train", "validation", "test")}
    keys = {split: {key for row in values for key in provenance_keys(row)} for split, values in samples.items()}
    overlap = {f"{left}--{right}": sorted(keys[left] & keys[right])
               for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))}
    if any(overlap.values()):
        raise ValueError("five-key provenance overlap in the selected samples")
    rng = random.Random(config["selection_seed"])
    pools = {label: sorted([row for row in all_test if row["compliance_label"] == label], key=lambda row: row["id"]) for label in LABELS}
    for pool in pools.values():
        rng.shuffle(pool)
    selected, seen = [], set()
    while len(selected) < 32:
        for label in LABELS:
            while pools[label] and pools[label][-1]["scenario_id"] in seen:
                pools[label].pop()
            if pools[label] and len(selected) < 32:
                row = pools[label].pop()
                selected.append(row)
                seen.add(row["scenario_id"])
    variants, pairs = [], []
    for source in selected:
        pair = []
        for i, instruction in enumerate(variant_config["instructions"]):
            row = {"id": source["id"] + f"__request_probe_{i}", "source_record_id": source["id"],
                   "task_type": source["task_type"], "scenario_id": source["scenario_id"],
                   "instruction": instruction, "input": source["input"], "compliance_label": source["compliance_label"]}
            variants.append(row)
            pair.append(row["id"])
        pairs.append(pair)
    write_jsonl(ROOT / "data/revision_20261001/request_variant_probe.jsonl", variants)
    keys["test"].update(key for row in selected for key in provenance_keys(row))
    overlap = {f"{left}--{right}": sorted(keys[left] & keys[right])
               for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))}
    if any(overlap.values()):
        raise ValueError("five-key provenance overlap in the controlled request probe")
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    device = torch.device(report["device"])
    dtype = torch.float16 if device.type == "mps" else torch.float32
    torch.set_num_threads(4)
    variant_results, reload_checks = [], []
    test_lookup = {row["id"]: row for row in all_test}
    evaluation_rows = [test_lookup[row_id] for row_id in read_predictions("base_predictions.jsonl")]
    for seed in [None] + config["training_seeds"]:
        model = AutoModelForCausalLM.from_pretrained(model_dir, local_files_only=True, torch_dtype=dtype, attn_implementation="eager").to(device)
        if seed is not None:
            checkpoint = RESULTS / "checkpoints" / f"seed_{seed}"
            model = get_peft_model(model, PeftConfig.from_pretrained(checkpoint))
            for name, parameter in model.named_parameters():
                if "lora_" in name:
                    parameter.data = parameter.data.float()
            weights = load_file(str(checkpoint / "adapter_model.safetensors"))
            set_peft_model_state_dict(model, weights)
            original = read_predictions(f"seed_{seed}_predictions.jsonl")
            reloaded = predict(model, tokenizer, evaluation_rows, config, device)
            changes = sum(row["prediction"] != original[row["id"]]["prediction"] for row in reloaded)
            maximum_difference = max(abs(left - right) for row in reloaded
                                     for left, right in zip(row["label_log_probabilities"], original[row["id"]]["label_log_probabilities"]))
            if changes:
                raise ValueError(f"saved adapter changed {changes} predictions for seed {seed}")
            hidden = predict(model, tokenizer, evaluation_rows, config, device, context=False)
            write_jsonl(RESULTS / f"seed_{seed}_predictions.jsonl", reloaded)
            write_jsonl(RESULTS / f"seed_{seed}_instruction_only_predictions.jsonl", hidden)
            reload_checks.append({"seed": seed, "records": len(reloaded), "changed_labels": changes,
                                  "maximum_label_log_probability_difference": maximum_difference})
            print(json.dumps({"phase": "adapter_reload", "seed": seed, "changed_labels": changes}), flush=True)
        predictions = predict(model, tokenizer, variants, config, device)
        name = "base" if seed is None else f"seed_{seed}"
        write_jsonl(RESULTS / f"{name}_request_variant_predictions.jsonl", predictions)
        variant_results.append({"model": name, **paired_metrics({"request_variant": pairs}, predictions)})
        del model
        gc.collect()
        if device.type == "mps":
            torch.mps.empty_cache()
    base = read_predictions("base_predictions.jsonl")
    intervals = {str(seed): clustered_delta(samples["test"], base, read_predictions(f"seed_{seed}_predictions.jsonl"), seed)
                 for seed in config["training_seeds"]}
    summary = {"status": "complete", "scope": config["scope"], "protocol": config,
               "runtime_versions": {name: version(name) for name in ("torch", "transformers", "peft", "accelerate", "scikit-learn")},
               "sample_provenance_overlap": overlap,
               "saved_adapter_replay": reload_checks,
               "target_provenance": "released synthetic compliance-contract labels; no prediction-derived reference",
               "test_record_review_status": dict(Counter((row.get("metadata") or {}).get("validation_status", "unspecified") for row in samples["test"])),
               "test_records": len(samples["test"]), "test_scenarios": len({row["scenario_id"] for row in samples["test"]}),
               "base": report["base"], "same_sample_tfidf": report["same_sample_tfidf"],
               "tuned_macro_f1_mean": statistics.mean(row["tuned"]["macro_f1"] for row in report["runs"]),
               "tuned_macro_f1_sd": statistics.stdev(row["tuned"]["macro_f1"] for row in report["runs"]),
               "instruction_only_macro_f1_mean": statistics.mean(row["instruction_only"]["macro_f1"] for row in report["runs"]),
               "scenario_cluster_bootstrap_delta_95_intervals": intervals,
               "bootstrap_policy": "1000 paired resamples of entire scenario clusters within the fixed label-balanced test sample; seeds remain separate",
               "state_change_pairs": {"base": report["base_pairs"]["state_change"],
                                      "tuned": [row["tuned_pairs"]["state_change"] for row in report["runs"]]},
               "request_variant_protocol": variant_config, "request_variants": variant_results}
    summary["request_probe_source_records"] = len(selected)
    summary["request_probe_rendered_records"] = len(variants)
    summary["request_probe_source_review_status"] = dict(Counter((row.get("metadata") or {}).get("validation_status", "unspecified") for row in selected))
    summary["request_probe_source_provenance_overlap"] = overlap
    summary["sample_provenance_scope"] = "selected training and validation records versus primary test and controlled request-probe source records"
    summary["macro_f1_gain"] = summary["tuned_macro_f1_mean"] - summary["base"]["macro_f1"]
    write_json(ROOT / "reports/local_instruction_transfer_20261001.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default="tmp/local_instruction_model")
    args = parser.parse_args()
    main(str(ROOT / args.model_dir))
