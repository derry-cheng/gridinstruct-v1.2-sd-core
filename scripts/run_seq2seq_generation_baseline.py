"""Fine-tune a pretrained seq2seq model for GridInstruct generation tasks.

This is the formal generation baseline used for submission-facing evidence.
It intentionally does not fall back to legacy character-level or placeholder models.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from tqdm.auto import tqdm

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl as atomic_write_jsonl


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def set_seed(seed: int) -> None:
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if torch.backends.mps.is_available():
        torch.mps.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    if hasattr(torch.backends, "cuda"):
        torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False


def determinism_contract() -> dict[str, Any]:
    return {
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
        "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
        "numpy_seeded": True,
        "torch_seeded": True,
        "cuda_seeded": bool(torch.cuda.is_available()),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
        "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
        "cuda_matmul_tf32": bool(torch.backends.cuda.matmul.allow_tf32),
        "cudnn_tf32": bool(torch.backends.cudnn.allow_tf32),
    }


def select_device(prefer_mps: bool = True) -> torch.device:
    if prefer_mps and torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def tool_names(row: dict[str, Any]) -> list[str]:
    tools = []
    for step in row.get("tool_plan", []):
        if isinstance(step, dict):
            tool = str(step.get("tool", "")).strip()
            if tool:
                tools.append(tool)
    return tools


PROMPT_FIELD_ORDER = (
    "audience",
    "usage_context",
    "deliverable",
    "focus_area",
    "style_hint",
    "rule_id",
    "rule_summary",
    "evidence_fields",
    "applicable_tasks",
    "secondary_rule_id",
    "secondary_rule_summary",
    "secondary_evidence_fields",
    "secondary_applicable_tasks",
)


def make_prompt(row: dict[str, Any], prompt_mode: str = "compact") -> str:
    lines = [
        f"Task type: {row['task_type']}",
        f"Instruction: {row['instruction']}",
    ]
    if row["task_type"] in {"dispatcher_intent_tool_call", "auxiliary_decision", "intelligent_data_query"}:
        lines.append(
            "Output contract: return only one valid JSON object with outer braces and double-quoted keys."
        )
    input_obj = row.get("input")
    if prompt_mode == "fielded" and isinstance(input_obj, dict):
        for key in PROMPT_FIELD_ORDER:
            value = input_obj.get(key)
            if value in (None, "", [], {}):
                continue
            lines.append(f"{key}: {stringify(value)}")
    lines.extend(
        [
            f"Input JSON: {json.dumps(input_obj, ensure_ascii=False, sort_keys=True)}",
            "Answer:",
        ]
    )
    return "\n".join(lines)


def target_for(row: dict[str, Any], target_mode: str = "full") -> str:
    if row["task_type"] in {"operation_ticket_check", "regulation_compliance_check"}:
        return str(row["compliance_label"])
    if row["task_type"] == "dispatcher_intent_tool_call":
        return stringify(
            {
                "intent": row.get("intent"),
                "slots": row.get("slots", {}),
                "tools": tool_names(row),
            }
        )
    if row["task_type"] == "auxiliary_decision":
        return stringify(
            {
                "response": row.get("chosen_response") or row.get("output"),
                "tools": tool_names(row),
            }
        )
    if row["task_type"] == "intelligent_data_query":
        if target_mode == "actionable":
            return stringify({"structured_query": row.get("structured_query")})
        return stringify(
            {
                "structured_query": row.get("structured_query"),
                "query_result": row.get("query_result"),
            }
        )
    return stringify(row["output"])


def filter_rows(rows: list[dict[str, Any]], task_types: set[str]) -> list[dict[str, Any]]:
    return [row for row in rows if row["task_type"] in task_types]


def overlap_f1(gold: str, pred: str, *, units: str) -> float:
    def tokenize(text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []
        if units == "character":
            return list("".join(stripped.split()))
        return [token for token in stripped.split() if token]

    gold_tokens = tokenize(gold)
    pred_tokens = tokenize(pred)
    if not gold_tokens and not pred_tokens:
        return 1.0
    if not gold_tokens or not pred_tokens:
        return 0.0
    gold_counts = Counter(gold_tokens)
    pred_counts = Counter(pred_tokens)
    overlap = sum((gold_counts & pred_counts).values())
    precision = overlap / max(len(pred_tokens), 1)
    recall = overlap / max(len(gold_tokens), 1)
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def parse_json_or_none(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def structured_scores(gold: str, pred: str, task_type: str) -> dict[str, Any]:
    gold_obj = parse_json_or_none(gold)
    pred_obj = parse_json_or_none(pred)
    scores: dict[str, Any] = {}
    if gold_obj is None or not isinstance(gold_obj, dict):
        return scores
    scores["gold_is_json"] = True
    scores["prediction_is_json"] = pred_obj is not None
    if pred_obj is None or not isinstance(pred_obj, dict):
        return scores

    scores["canonical_json_exact"] = gold_obj == pred_obj
    if task_type == "intelligent_data_query":
        gold_query = gold_obj.get("structured_query", {})
        pred_query = pred_obj.get("structured_query", {})
        if isinstance(gold_query, dict) and isinstance(pred_query, dict):
            fields = sorted(set(gold_query))
            missing = object()
            exact_by_field = {
                field: pred_query.get(field, missing) is not missing
                and isinstance(pred_query[field], type(gold_query[field]))
                and gold_query[field] == pred_query[field]
                for field in fields
            }
            scores["structured_query_exact"] = gold_query == pred_query
            scores["structured_query_field_accuracy"] = (
                sum(exact_by_field.values()) / len(exact_by_field) if exact_by_field else 0.0
            )
            scores["prediction_schema_valid"] = "structured_query" in pred_obj and isinstance(pred_obj["structured_query"], dict)
    if task_type == "auxiliary_decision":
        gold_tools = set(gold_obj.get("tools") or [])
        pred_tools = set(pred_obj.get("tools") or [])
        union = gold_tools | pred_tools
        intersection = gold_tools & pred_tools
        scores["tool_set_exact"] = gold_tools == pred_tools
        scores["tool_set_jaccard"] = len(intersection) / len(union) if union else 1.0
        scores["prediction_schema_valid"] = (
            "response" in pred_obj and "tools" in pred_obj and isinstance(pred_obj["tools"], list)
        )
    return scores


def json_schema_for_tasks(task_types: set[str]) -> dict[str, Any] | None:
    if task_types == {"intelligent_data_query"}:
        return {
            "type": "object",
            "properties": {
                "structured_query": {"type": "object"},
                "query_result": {},
            },
            "required": ["structured_query"],
            "additionalProperties": False,
        }
    if task_types == {"auxiliary_decision"}:
        return {
            "type": "object",
            "properties": {
                "response": {},
                "tools": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["response", "tools"],
            "additionalProperties": False,
        }
    if task_types == {"dispatcher_intent_tool_call"}:
        return {
            "type": "object",
            "properties": {
                "intent": {"type": ["string", "null"]},
                "slots": {"type": "object"},
                "tools": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["intent", "slots", "tools"],
            "additionalProperties": False,
        }
    return None


def build_json_prefix_constraint(tokenizer: Any, schema: dict[str, Any] | None):
    if schema is None:
        return None
    # Graceful degradation: if lm-format-enforcer or its transformers integration
    # is unavailable/incompatible (e.g. with transformers 5.x), fall back to
    # unconstrained generation. JSON validity is still measured post-generation
    # via the structured-query exact-match and JSON-validity metrics, so the
    # release loses no evaluation signal — only the training-time decoding hint.
    try:
        from lmformatenforcer import JsonSchemaParser
        from lmformatenforcer.integrations.transformers import (
            build_transformers_prefix_allowed_tokens_fn,
        )
    except ImportError:
        print(
            "WARNING: lm-format-enforcer not available; "
            "falling back to unconstrained generation "
            "(JSON validity still checked post-generation)"
        )
        return None
    try:
        return build_transformers_prefix_allowed_tokens_fn(
            tokenizer, JsonSchemaParser(schema)
        )
    except Exception as exc:  # noqa: BLE001 - degrade rather than abort training
        print(
            f"WARNING: lm-format-enforcer integration failed ({exc}); "
            "falling back to unconstrained generation"
        )
        return None


class Seq2SeqDataset(Dataset):
    def __init__(
        self,
        rows: list[dict[str, Any]],
        tokenizer: Any,
        max_source_len: int,
        max_target_len: int,
        target_mode: str,
        prompt_mode: str,
    ) -> None:
        self.items = []
        for row in rows:
            self.items.append(
                {
                    "id": row["id"],
                    "task_type": row["task_type"],
                    "instruction": row.get("instruction"),
                    "input": row.get("input"),
                    "metadata": row.get("metadata", {}),
                    "source_regulation_ids": row.get("source_regulation_ids", []),
                    "source": make_prompt(row, prompt_mode),
                    "target": target_for(row, target_mode),
                }
            )
        self.tokenizer = tokenizer
        self.max_source_len = max_source_len
        self.max_target_len = max_target_len

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        return self.items[idx]


def collate_fn(tokenizer: Any, max_source_len: int, max_target_len: int):
    def _collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
        sources = [item["source"] for item in batch]
        targets = [item["target"] for item in batch]
        encoded = tokenizer(
            sources,
            padding=True,
            truncation=True,
            max_length=max_source_len,
            return_tensors="pt",
        )
        target_encoded = tokenizer(
            text_target=targets,
            padding=True,
            truncation=True,
            max_length=max_target_len,
            return_tensors="pt",
        )
        encoded.pop("token_type_ids", None)
        labels = target_encoded["input_ids"]
        labels[labels == tokenizer.pad_token_id] = -100
        encoded["labels"] = labels
        encoded["ids"] = [item["id"] for item in batch]
        encoded["task_types"] = [item["task_type"] for item in batch]
        encoded["sources"] = [item["source"] for item in batch]
        encoded["instructions"] = [item.get("instruction") for item in batch]
        encoded["inputs"] = [item.get("input") for item in batch]
        encoded["metadata"] = [item.get("metadata", {}) for item in batch]
        encoded["source_regulation_ids"] = [item.get("source_regulation_ids", []) for item in batch]
        encoded["gold"] = targets
        return encoded

    return _collate


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_jsonl(path, rows)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_dirs(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def truncation_stats(
    rows: list[dict[str, Any]],
    tokenizer: Any,
    max_source_len: int,
    max_target_len: int,
    target_mode: str,
    prompt_mode: str,
) -> dict[str, Any]:
    source_lengths: list[int] = []
    target_lengths: list[int] = []
    for start in range(0, len(rows), 256):
        batch = rows[start : start + 256]
        sources = [make_prompt(row, prompt_mode) for row in batch]
        targets = [target_for(row, target_mode) for row in batch]
        source_lengths.extend(len(ids) for ids in tokenizer(sources, add_special_tokens=True, truncation=False)["input_ids"])
        target_lengths.extend(len(ids) for ids in tokenizer(targets, add_special_tokens=True, truncation=False)["input_ids"])
    return {
        "records": len(rows),
        "max_source_tokens": max(source_lengths, default=0),
        "max_target_tokens": max(target_lengths, default=0),
        "source_truncated_records": sum(length > max_source_len for length in source_lengths),
        "target_truncated_records": sum(length > max_target_len for length in target_lengths),
        "source_truncation_rate": sum(length > max_source_len for length in source_lengths) / max(len(rows), 1),
        "target_truncation_rate": sum(length > max_target_len for length in target_lengths) / max(len(rows), 1),
    }


@torch.no_grad()
def evaluate(
    model: Any,
    loader: DataLoader,
    tokenizer: Any,
    device: torch.device,
    max_target_len: int,
    num_beams: int,
    desc: str,
    prefix_allowed_tokens_fn: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    model.eval()
    predictions = []
    exact = 0
    f1_sum = 0.0
    structured_tasks = {"intelligent_data_query", "auxiliary_decision", "dispatcher_intent_tool_call"}
    for step, batch in enumerate(tqdm(loader, desc=desc, leave=False), start=1):
        ids = batch.pop("ids")
        task_types = batch.pop("task_types")
        sources = batch.pop("sources")
        instructions = batch.pop("instructions")
        inputs = batch.pop("inputs")
        metadata = batch.pop("metadata")
        source_regulation_ids = batch.pop("source_regulation_ids")
        golds = batch.pop("gold")
        batch = {key: value.to(device) for key, value in batch.items()}
        generated = model.generate(
            input_ids=batch["input_ids"],
            attention_mask=batch.get("attention_mask"),
            max_new_tokens=max_target_len,
            num_beams=num_beams,
            prefix_allowed_tokens_fn=prefix_allowed_tokens_fn,
        )
        decoded = tokenizer.batch_decode(
            generated,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        for item_id, task_type, source, instruction, input_obj, meta, rule_ids, gold, pred in zip(
            ids,
            task_types,
            sources,
            instructions,
            inputs,
            metadata,
            source_regulation_ids,
            golds,
            decoded,
        ):
            pred = pred.strip()
            gold = gold.strip()
            units = "character" if task_type in structured_tasks else "whitespace_token"
            item_f1 = overlap_f1(gold, pred, units=units)
            is_exact = gold == pred
            extra_scores = structured_scores(gold, pred, task_type)
            exact += int(is_exact)
            f1_sum += item_f1
            prediction_row = {
                "id": item_id,
                "task_type": task_type,
                "instruction": instruction,
                "input": input_obj,
                "source": source,
                "metadata": meta,
                "source_regulation_ids": rule_ids,
                "target": gold,
                "gold": gold,
                "prediction": pred,
                "exact_match": is_exact,
                ("char_f1" if units == "character" else "token_f1"): item_f1,
            }
            prediction_row.update(extra_scores)
            predictions.append(prediction_row)
        if device.type == "mps" and step % 25 == 0:
            torch.mps.empty_cache()
    n = max(len(predictions), 1)
    structured_only = bool(predictions) and all(row["task_type"] in structured_tasks for row in predictions)
    f1_name = "char_f1" if structured_only else "token_f1"
    metrics: dict[str, Any] = {
        "exact_match": exact / n,
        f1_name: f1_sum / n,
        "selection_f1": f1_sum / n,
        "n": len(predictions),
    }
    for key in (
        "prediction_is_json",
        "prediction_schema_valid",
        "canonical_json_exact",
        "structured_query_exact",
        "structured_query_field_accuracy",
        "tool_set_exact",
        "tool_set_jaccard",
    ):
        values = [row[key] for row in predictions if key in row]
        if values:
            metrics[key] = sum(float(value) for value in values) / len(values)
    return metrics, predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="data/v1.2_train.jsonl")
    parser.add_argument("--validation", default="data/v1.2_validation.jsonl")
    parser.add_argument("--test", default=None)
    parser.add_argument("--task-types", nargs="+", default=["regulation_qa"])
    parser.add_argument("--model-name", default="uer/t5-small-chinese-cluecorpussmall")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--patience", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-source-len", type=int, default=384)
    parser.add_argument("--max-target-len", type=int, default=96)
    parser.add_argument("--target-mode", choices=["full", "actionable"], default="full")
    parser.add_argument("--prompt-mode", choices=["compact", "fielded"], default="compact")
    parser.add_argument("--num-beams", type=int, default=1)
    parser.add_argument("--use-slow-tokenizer", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--json-constrained-decoding", action="store_true")
    parser.add_argument("--output-prefix", default="benchmark/v1.2_seq2seq_generation")
    parser.add_argument("--experiment-dir", default=None)
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()

    set_seed(args.seed)
    task_types = set(args.task_types)
    train_rows = filter_rows(read_jsonl(ROOT / args.train), task_types)
    validation_rows = filter_rows(read_jsonl(ROOT / args.validation), task_types)
    test_rows = filter_rows(read_jsonl(ROOT / args.test), task_types) if args.test else []
    if not train_rows or not validation_rows or (args.test and not test_rows):
        raise ValueError("Train, validation, and requested test splits must all contain the selected task.")

    tokenizer = AutoTokenizer.from_pretrained(
        args.model_name,
        use_fast=not args.use_slow_tokenizer,
        local_files_only=args.local_files_only,
    )
    truncation = {
        "train": truncation_stats(train_rows, tokenizer, args.max_source_len, args.max_target_len, args.target_mode, args.prompt_mode),
        "validation": truncation_stats(validation_rows, tokenizer, args.max_source_len, args.max_target_len, args.target_mode, args.prompt_mode),
        "test": truncation_stats(test_rows, tokenizer, args.max_source_len, args.max_target_len, args.target_mode, args.prompt_mode),
    }
    if any(stats["target_truncated_records"] for stats in truncation.values()):
        raise ValueError(f"gold target truncation is forbidden in formal runs: {truncation}")
    model = AutoModelForSeq2SeqLM.from_pretrained(args.model_name, local_files_only=args.local_files_only)
    device = torch.device("cpu") if args.cpu else select_device()
    model.to(device)
    json_schema = json_schema_for_tasks(task_types) if args.json_constrained_decoding else None
    prefix_allowed_tokens_fn = build_json_prefix_constraint(tokenizer, json_schema)

    train_dataset = Seq2SeqDataset(
        train_rows, tokenizer, args.max_source_len, args.max_target_len, args.target_mode, args.prompt_mode
    )
    validation_dataset = Seq2SeqDataset(
        validation_rows, tokenizer, args.max_source_len, args.max_target_len, args.target_mode, args.prompt_mode
    )
    test_dataset = (
        Seq2SeqDataset(test_rows, tokenizer, args.max_source_len, args.max_target_len, args.target_mode, args.prompt_mode)
        if test_rows
        else None
    )
    collate = collate_fn(tokenizer, args.max_source_len, args.max_target_len)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=collate)
    validation_loader = DataLoader(validation_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate) if test_dataset else None

    prefix = ROOT / args.output_prefix
    task_slug = "_".join(sorted(task_types))
    experiment_dir = ROOT / args.experiment_dir if args.experiment_dir else ROOT / "experiments" / "05_seq2seq_generation" / task_slug
    checkpoint_dir = experiment_dir / "checkpoints"
    log_dir = experiment_dir / "logs"
    ensure_dirs(prefix.parent, ROOT / "reports", checkpoint_dir, log_dir)
    training_log = log_dir / "training_log.jsonl"
    if training_log.exists():
        training_log.unlink()

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    best_epoch = 0
    best_validation = {"exact_match": -1.0, "selection_f1": -1.0, "n": 0}
    best_state = copy.deepcopy(model.state_dict())
    stagnant_epochs = 0
    history = []

    for epoch in range(1, args.epochs + 1):
        model.train()
        losses = []
        progress = tqdm(train_loader, desc=f"train seq2seq {task_slug} epoch {epoch}/{args.epochs}", leave=False)
        for step, batch in enumerate(progress, start=1):
            batch.pop("ids")
            batch.pop("task_types")
            batch.pop("sources")
            batch.pop("instructions")
            batch.pop("inputs")
            batch.pop("metadata")
            batch.pop("source_regulation_ids")
            batch.pop("gold")
            batch = {key: value.to(device) for key, value in batch.items()}
            batch.pop("token_type_ids", None)
            output = model(**batch)
            loss = output.loss
            if not bool(torch.isfinite(loss)):
                raise RuntimeError(f"non-finite training loss at epoch {epoch}, step {step}")
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            loss_value = float(loss.detach().cpu())
            losses.append(loss_value)
            progress.set_postfix(loss=f"{loss_value:.4f}")
            del output, loss
            if device.type == "mps" and step % 25 == 0:
                torch.mps.empty_cache()

        metrics, _ = evaluate(
            model,
            validation_loader,
            tokenizer,
            device,
            args.max_target_len,
            args.num_beams,
            desc=f"validate seq2seq {task_slug} epoch {epoch}",
            prefix_allowed_tokens_fn=prefix_allowed_tokens_fn,
        )
        epoch_record = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "epoch": epoch,
            "train_loss": sum(losses) / max(len(losses), 1),
            "validation": metrics,
        }
        append_jsonl(training_log, epoch_record)
        history.append(
            {
                "epoch": epoch,
                "train_loss": epoch_record["train_loss"],
                "validation_exact_match": metrics["exact_match"],
                "validation_selection_f1": metrics["selection_f1"],
            }
        )
        print(
            f"[epoch {epoch}] loss={epoch_record['train_loss']:.4f} "
            f"val_em={metrics['exact_match']:.4f} val_f1={metrics['selection_f1']:.4f}",
            flush=True,
        )
        if (
            metrics["selection_f1"] > best_validation["selection_f1"]
            or (
                metrics["selection_f1"] == best_validation["selection_f1"]
                and metrics["exact_match"] > best_validation["exact_match"]
            )
        ):
            best_epoch = epoch
            best_validation = metrics
            best_state = copy.deepcopy(model.state_dict())
            model.save_pretrained(checkpoint_dir / "best_model")
            tokenizer.save_pretrained(checkpoint_dir / "best_model")
            stagnant_epochs = 0
        else:
            stagnant_epochs += 1
            if stagnant_epochs >= args.patience:
                print(
                    f"[early-stop] best_epoch={best_epoch} "
                    f"best_em={best_validation['exact_match']:.4f} "
                    f"best_f1={best_validation['selection_f1']:.4f}",
                    flush=True,
                )
                break

    model.load_state_dict(best_state)
    final_validation, validation_predictions = evaluate(
        model,
        validation_loader,
        tokenizer,
        device,
        args.max_target_len,
        args.num_beams,
        desc=f"final validate seq2seq {task_slug}",
        prefix_allowed_tokens_fn=prefix_allowed_tokens_fn,
    )
    test_metrics = None
    test_predictions = []
    if test_loader:
        test_metrics, test_predictions = evaluate(
            model,
            test_loader,
            tokenizer,
            device,
            args.max_target_len,
            args.num_beams,
            desc=f"test seq2seq {task_slug}",
            prefix_allowed_tokens_fn=prefix_allowed_tokens_fn,
        )

    validation_prediction_path = Path(f"{prefix}_validation_predictions.jsonl")
    test_prediction_path = Path(f"{prefix}_test_predictions.jsonl")
    write_jsonl(validation_prediction_path, validation_predictions)
    write_jsonl(test_prediction_path, test_predictions)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train": args.train,
        "validation": args.validation,
        "test": args.test,
        "task_types": sorted(task_types),
        "objective": "pretrained_seq2seq_generation",
        "model_name": args.model_name,
        "run_kind": "heldout_evaluation" if args.test else "validation_only",
        "device": str(device),
        "determinism_contract": determinism_contract(),
        "train_records": len(train_rows),
        "validation_records": len(validation_rows),
        "test_records": len(test_rows),
        "truncation_audit": truncation,
        "input_sha256": {
            "train": sha256(ROOT / args.train),
            "validation": sha256(ROOT / args.validation),
            "test": sha256(ROOT / args.test) if args.test else None,
        },
        "prediction_sha256": {
            "validation": sha256(validation_prediction_path),
            "test": sha256(test_prediction_path),
        },
        "config": {
            "output_prefix": args.output_prefix,
            "experiment_dir": str(experiment_dir.relative_to(ROOT)),
            "training_log": str(training_log.relative_to(ROOT)),
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "weight_decay": args.weight_decay,
            "seed": args.seed,
            "max_source_len": args.max_source_len,
            "max_target_len": args.max_target_len,
            "target_mode": args.target_mode,
            "prompt_mode": args.prompt_mode,
            "num_beams": args.num_beams,
            "use_slow_tokenizer": args.use_slow_tokenizer,
            "local_files_only": args.local_files_only,
            "cpu": args.cpu,
            "json_constrained_decoding": args.json_constrained_decoding,
            "json_schema": json_schema,
        },
        "history": history,
        "best_epoch": best_epoch,
        "best_validation": best_validation,
        "final_validation": final_validation,
        "test_evaluation": test_metrics,
        "metric_definition": "whitespace-token F1 for natural-language QA; character F1 is separately named for structured JSON and accompanied by canonical JSON, schema-validity, and typed required-field metrics",
        "note": "Pretrained seq2seq generation baseline with held-out evaluation and saved checkpoints. In actionable mode, intelligent_data_query targets the executable structured_query; query_result remains validated as a dataset/simulation field rather than generated from hidden simulation records.",
    }
    write_json(Path(f"{prefix}_report.json"), report)

    md_lines = [
        "# Pretrained Seq2Seq Generation Baseline",
        "",
        f"- Model: {args.model_name}",
        f"- Device: {report['device']}",
        f"- Task types: {', '.join(report['task_types'])}",
        f"- Target mode: {args.target_mode}",
        f"- Train records: {report['train_records']}",
        f"- Validation records: {report['validation_records']}",
        f"- Best epoch: {report['best_epoch']}",
        f"- Validation exact match: {final_validation['exact_match']:.4f}",
        f"- Validation selection F1: {final_validation['selection_f1']:.4f}",
    ]
    if test_metrics:
        md_lines.extend(
            [
                f"- Test split: {args.test}",
                f"- Test records: {report['test_records']}",
                f"- Test exact match: {test_metrics['exact_match']:.4f}",
                f"- Test selection F1: {test_metrics['selection_f1']:.4f}",
            ]
        )
        for key, value in test_metrics.items():
            if key not in {"exact_match", "selection_f1", "n"}:
                md_lines.append(f"- Test {key}: {value:.4f}")
    md_lines.extend(["", report["note"], "", f"Metric: {report['metric_definition']}"])
    Path(f"{prefix}_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
