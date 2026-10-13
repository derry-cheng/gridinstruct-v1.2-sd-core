#!/usr/bin/env python3
"""Compare a pinned instruction model before and after released-data supervision."""

from __future__ import annotations

import argparse
import gc
import json
import random
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import numpy as np
import torch
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, f1_score
from sklearn.svm import LinearSVC
from peft import LoraConfig, PeftModel, get_peft_model, get_peft_model_state_dict, set_peft_model_state_dict
from transformers import AutoModelForCausalLM, AutoTokenizer

from gridinstruct_utils import ROOT, write_json, write_jsonl


LABELS = ("compliant", "compliant_with_monitoring", "non_compliant")
PROTOCOL = ROOT / "experiments/revision_20261001/protocol.json"
RESULTS = ROOT / "benchmark/local_instruction_transfer_20261001"


def load_rows(split: str) -> list[dict]:
    with (ROOT / f"data/v1.2_sd_core_strict_{split}.jsonl").open() as handle:
        return [row for line in handle if (row := json.loads(line))["task_type"] == "regulation_compliance_check"]


def select_rows(rows: list[dict], count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    selected = []
    for label in LABELS:
        groups = defaultdict(list)
        for row in rows:
            if row["compliance_label"] == label:
                groups[row["scenario_id"]].append(row)
        keys = sorted(groups)
        rng.shuffle(keys)
        for group in groups.values():
            group.sort(key=lambda row: row["id"])
            rng.shuffle(group)
        pool = []
        while len(pool) < count and any(groups.values()):
            for key in keys:
                if groups[key] and len(pool) < count:
                    pool.append(groups[key].pop())
        if len(pool) != count:
            raise ValueError(f"insufficient {label} records: {len(pool)} < {count}")
        selected.extend(pool)
    rng.shuffle(selected)
    return selected


def prompt(row: dict, context: bool = True) -> str:
    value = "Classify the proposed dispatch action. Return exactly one label: " + ", ".join(LABELS) + ".\n"
    value += "Request: " + row["instruction"]
    if context:
        value += "\nOperating context: " + json.dumps(row["input"], sort_keys=True)
    return value


def pair_probe(rows, seed):
    by_request, by_context = defaultdict(list), defaultdict(list)
    for row in sorted(rows, key=lambda item: item["id"]):
        by_request[row["instruction"]].append(row)
        by_context[(json.dumps(row["input"], sort_keys=True), row["compliance_label"])].append(row)
    rng = random.Random(seed)
    selected = {}
    for name, groups in (("state_change", by_request), ("request_variant", by_context)):
        eligible = []
        for group in groups.values():
            for left, right in combinations(group, 2):
                valid = (left["scenario_id"] != right["scenario_id"] and left["compliance_label"] != right["compliance_label"]) if name == "state_change" else left["instruction"] != right["instruction"]
                if valid:
                    eligible.append([left["id"], right["id"]])
                    break
        rng.shuffle(eligible)
        selected[name] = eligible[:32]
    ids = {item for pairs in selected.values() for pair in pairs for item in pair}
    return [row for row in rows if row["id"] in ids], selected


def paired_metrics(pairs, predictions):
    lookup = {row["id"]: row for row in predictions}
    result = {}
    for name, ids in pairs.items():
        correct = agreement = 0
        for left_id, right_id in ids:
            left, right = lookup[left_id], lookup[right_id]
            correct += left["prediction"] == left["gold"] and right["prediction"] == right["gold"]
            agreement += left["prediction"] == right["prediction"]
        result[name] = {"pairs": len(ids), "both_correct": correct,
                        "both_correct_rate": correct / len(ids) if ids else None,
                        "prediction_agreement": agreement / len(ids) if ids else None}
    return result


def encode(tokenizer, row: dict, label: str, max_tokens: int, context: bool = True) -> tuple[list[int], list[int]]:
    prefix = tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt(row, context)}], tokenize=True, add_generation_prompt=True
    )
    target = tokenizer.encode(label, add_special_tokens=False) + [tokenizer.eos_token_id]
    if len(prefix) + len(target) > max_tokens:
        raise ValueError(f"input truncation forbidden: {row['id']} needs {len(prefix) + len(target)} tokens")
    return prefix + target, [-100] * len(prefix) + target


def batch(examples, pad_id: int, device):
    width = max(len(item[0]) for item in examples)
    ids = torch.full((len(examples), width), pad_id, dtype=torch.long)
    mask = torch.zeros_like(ids)
    labels = torch.full_like(ids, -100)
    for i, (tokens, targets) in enumerate(examples):
        ids[i, :len(tokens)] = torch.tensor(tokens)
        mask[i, :len(tokens)] = 1
        labels[i, :len(tokens)] = torch.tensor(targets)
    return ids.to(device), mask.to(device), labels


def target_log_probs(model, ids, mask, labels):
    # Project only supervised positions; this matches ordinary causal-LM loss.
    base_model = model.get_base_model() if isinstance(model, PeftModel) else model
    hidden = base_model.model(input_ids=ids, attention_mask=mask, use_cache=False).last_hidden_state[:, :-1]
    gold = labels[:, 1:]
    positions = gold != -100
    coordinates = positions.nonzero(as_tuple=False).to(ids.device)
    selected = hidden.float()[coordinates[:, 0], coordinates[:, 1]]
    logits = base_model.lm_head(selected.to(base_model.lm_head.weight.dtype)).float()
    losses = torch.nn.functional.cross_entropy(logits, gold[positions].to(ids.device), reduction="none")
    owners = coordinates[:, 0]
    totals = torch.zeros(ids.shape[0], device=ids.device).scatter_add_(0, owners, losses)
    return losses.mean(), -totals


@torch.no_grad()
def predict(model, tokenizer, rows, config, device, context=True):
    model.eval()
    predictions = []
    for start in range(0, len(rows), config["batch_size"]):
        part = rows[start:start + config["batch_size"]]
        examples = [encode(tokenizer, row, label, config["max_tokens"], context) for row in part for label in LABELS]
        ids, mask, gold = batch(examples, tokenizer.pad_token_id, device)
        _, scores = target_log_probs(model, ids, mask, gold)
        scores = scores.reshape(len(part), len(LABELS)).cpu().numpy()
        for row, values in zip(part, scores):
            predictions.append({"id": row["id"], "gold": row["compliance_label"],
                                "prediction": LABELS[int(values.argmax())], "label_log_probabilities": values.tolist()})
    return predictions


def metrics(rows, predictions):
    lookup = {item["id"]: item for item in predictions}
    gold = [lookup[row["id"]]["gold"] for row in rows]
    predicted = [lookup[row["id"]]["prediction"] for row in rows]
    by_request, by_context = defaultdict(list), defaultdict(list)
    for row in rows:
        by_request[row["instruction"]].append(row)
        by_context[json.dumps(row["input"], sort_keys=True)].append(row)
    result = {"n": len(rows), "accuracy": accuracy_score(gold, predicted),
              "macro_f1": f1_score(gold, predicted, labels=LABELS, average="macro", zero_division=0),
              "per_label_f1": dict(zip(LABELS, f1_score(gold, predicted, labels=LABELS, average=None, zero_division=0).tolist())),
              "scenario_count": len({row["scenario_id"] for row in rows})}
    for name, groups in (("state_change", by_request), ("request_variant", by_context)):
        pairs = []
        for group in groups.values():
            for left, right in combinations(group, 2):
                eligible = (left["scenario_id"] != right["scenario_id"] and left["compliance_label"] != right["compliance_label"]) if name == "state_change" else (left["instruction"] != right["instruction"] and left["compliance_label"] == right["compliance_label"])
                if eligible:
                    pairs.append((lookup[left["id"]], lookup[right["id"]]))
        correct = sum(left["gold"] == left["prediction"] and right["gold"] == right["prediction"] for left, right in pairs)
        result[name] = {"pairs": len(pairs), "both_correct": correct,
                        "both_correct_rate": correct / len(pairs) if pairs else None}
    return result


def run(model_dir: str, smoke: bool = False):
    config = json.loads(PROTOCOL.read_text())
    torch.set_num_threads(4)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    rows = {split: select_rows(load_rows(split), config[f"{split}_per_label"], config["selection_seed"])
            for split in ("train", "validation", "test")}
    probe_rows, pairs = pair_probe(load_rows("test"), config["selection_seed"])
    evaluation_rows = list({row["id"]: row for row in rows["test"] + probe_rows}.values())
    for left, right in combinations(rows, 2):
        for field in ("id", "scenario_id", "source_simulation_case_id"):
            overlap = {row.get(field) for row in rows[left]} & {row.get(field) for row in rows[right]}
            if overlap - {None}:
                raise ValueError(f"{field} overlap between {left} and {right}")
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    encoded = [encode(tokenizer, row, row["compliance_label"], config["max_tokens"]) for row in rows["train"]]
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_json(RESULTS / "sample_ids.json", {key: [row["id"] for row in values] for key, values in rows.items()})
    write_json(RESULTS / "pair_ids.json", pairs)
    dtype = torch.float16 if device.type == "mps" else torch.float32
    def load_model(adapt=False):
        value = AutoModelForCausalLM.from_pretrained(model_dir, local_files_only=True, attn_implementation="eager", torch_dtype=dtype).to(device)
        if adapt:
            value = get_peft_model(value, LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05,
                                  target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM"))
            for parameter in value.parameters():
                if parameter.requires_grad:
                    parameter.data = parameter.data.float()
        return value
    model = load_model(adapt=smoke)
    started = time.monotonic()
    if smoke:
        model.eval()
        ids, mask, labels = batch(encoded[:2], tokenizer.pad_token_id, device)
        with torch.no_grad():
            usual = model(input_ids=ids, attention_mask=mask, labels=labels.to(device)).loss
            optimized, _ = target_log_probs(model, ids, mask, labels)
        if not torch.allclose(usual, optimized, rtol=1e-5, atol=1e-5):
            raise ValueError(f"loss mismatch: {usual.item()} versus {optimized.item()}")
        optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"])
        for step in range(5):
            model.train()
            optimizer.zero_grad(set_to_none=True)
            ids, mask, labels = batch(encoded[step * 8:(step + 1) * 8], tokenizer.pad_token_id, device)
            loss, _ = target_log_probs(model, ids, mask, labels)
            loss.backward()
            optimizer.step()
            print(json.dumps({"phase": "smoke", "step": step + 1, "elapsed_seconds": time.monotonic() - started}), flush=True)
        print(json.dumps({"status": "smoke_pass", "loss_equivalence": True, "five_steps_seconds": time.monotonic() - started, "device": str(device)}), flush=True)
        return
    base = predict(model, tokenizer, evaluation_rows, config, device)
    write_jsonl(RESULTS / "base_predictions.jsonl", base)
    report = {"status": "running", "protocol": config, "device": str(device), "base": metrics(rows["test"], base), "base_pairs": paired_metrics(pairs, base), "runs": []}
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), max_features=60000)
    classifier = LinearSVC(dual=True)
    classifier.fit(vectorizer.fit_transform(prompt(row) for row in rows["train"]), [row["compliance_label"] for row in rows["train"]])
    lexical = [{"id": row["id"], "gold": row["compliance_label"], "prediction": value}
               for row, value in zip(evaluation_rows, classifier.predict(vectorizer.transform(prompt(row) for row in evaluation_rows)))]
    report["same_sample_tfidf"] = metrics(rows["test"], lexical)
    report["same_sample_tfidf_pairs"] = paired_metrics(pairs, lexical)
    write_jsonl(RESULTS / "tfidf_predictions.jsonl", lexical)
    del model
    gc.collect()
    if device.type == "mps":
        torch.mps.empty_cache()
    for seed_index, seed in enumerate(config["training_seeds"]):
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if device.type == "mps":
            torch.mps.manual_seed(seed)
        model = load_model(adapt=True)
        optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"])
        best_score, best_state, history = None, None, []
        rng = random.Random(seed)
        for epoch in range(config["epochs"]):
            order = list(range(len(encoded)))
            rng.shuffle(order)
            model.train()
            for start in range(0, len(order), config["batch_size"]):
                optimizer.zero_grad(set_to_none=True)
                examples = [encoded[i] for i in order[start:start + config["batch_size"]]]
                ids, mask, labels = batch(examples, tokenizer.pad_token_id, device)
                loss, _ = target_log_probs(model, ids, mask, labels)
                if not torch.isfinite(loss):
                    raise ValueError("non-finite training loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                if start % 256 == 0:
                    fraction = (seed_index * config["epochs"] + epoch + start / len(order)) / (len(config["training_seeds"]) * config["epochs"])
                    print(json.dumps({"phase": "train", "percent": round(100 * fraction, 1), "seed": seed, "epoch": epoch + 1, "examples": start, "loss": loss.item(), "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
            validation = predict(model, tokenizer, rows["validation"], config, device)
            validation_f1 = metrics(rows["validation"], validation)["macro_f1"]
            target_tokens = sum(len(tokenizer.encode(item["gold"], add_special_tokens=False)) + 1 for item in validation)
            validation_loss = -sum(item["label_log_probabilities"][LABELS.index(item["gold"])] for item in validation) / target_tokens
            score = (validation_f1, -validation_loss)
            history.append({"epoch": epoch + 1, "validation_macro_f1": validation_f1, "validation_target_loss": validation_loss})
            if best_score is None or score > best_score:
                best_score = score
                best_state = {key: value.detach().cpu().clone() for key, value in get_peft_model_state_dict(model).items()}
        set_peft_model_state_dict(model, best_state)
        model.peft_config["default"].base_model_name_or_path = config["model"]
        model.peft_config["default"].revision = config["model_revision"]
        model.save_pretrained(RESULTS / "checkpoints" / f"seed_{seed}")
        tuned = predict(model, tokenizer, evaluation_rows, config, device)
        hidden = predict(model, tokenizer, evaluation_rows, config, device, context=False)
        write_jsonl(RESULTS / f"seed_{seed}_predictions.jsonl", tuned)
        write_jsonl(RESULTS / f"seed_{seed}_instruction_only_predictions.jsonl", hidden)
        report["runs"].append({"seed": seed, "history": history, "tuned": metrics(rows["test"], tuned), "instruction_only": metrics(rows["test"], hidden),
                                "tuned_pairs": paired_metrics(pairs, tuned), "instruction_only_pairs": paired_metrics(pairs, hidden)})
        report["elapsed_seconds"] = time.monotonic() - started
        write_json(RESULTS / "report.json", report)
        print(json.dumps({"phase": "seed_complete", "seed": seed, "metrics": report["runs"][-1]}), flush=True)
        del model, optimizer, best_state
        gc.collect()
        if device.type == "mps":
            torch.mps.empty_cache()
    report["status"] = "complete"
    write_json(RESULTS / "report.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default="tmp/local_instruction_model")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    run(str(ROOT / args.model_dir), args.smoke)
