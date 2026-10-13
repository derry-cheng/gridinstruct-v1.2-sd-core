"""Train a local Transformer classification baseline for GridInstruct tasks.

This script targets label- and intent-oriented tasks where sequence
classification is a strong, dependency-light baseline. It is stronger evidence
than earlier lightweight probes, while still
remaining feasible on local MPS hardware.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from tqdm.auto import tqdm

from gridinstruct_utils import ROOT, ensure_dirs, read_jsonl, write_json, write_jsonl as atomic_write_jsonl


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


SUPPORTED_TASKS = {
    "operation_ticket_check": "compliance_label",
    "regulation_compliance_check": "compliance_label",
    "dispatcher_intent_tool_call": "intent",
}


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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


def local_hf_snapshot(model_name: str) -> str:
    path = Path(model_name)
    if path.exists():
        return model_name
    if "/" not in model_name:
        return model_name
    cache_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    model_cache = cache_home / "hub" / f"models--{model_name.replace('/', '--')}"
    ref_path = model_cache / "refs" / "main"
    if ref_path.exists():
        revision = ref_path.read_text(encoding="utf-8").strip()
        snapshot = model_cache / "snapshots" / revision
        if snapshot.exists():
            return str(snapshot)
    snapshots_dir = model_cache / "snapshots"
    if snapshots_dir.exists():
        snapshots = sorted(
            (p for p in snapshots_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if snapshots:
            return str(snapshots[0])
    return model_name


def select_device(prefer_mps: bool = True) -> torch.device:
    if prefer_mps and torch.backends.mps.is_available():
        return torch.device("mps")
    # The release protocol is local CPU/MPS only.  A CUDA device must never be
    # selected implicitly by a fallback path.
    return torch.device("cpu")


def safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def macro_f1(golds: list[str], preds: list[str]) -> float:
    labels = sorted(set(golds) | set(preds))
    f1s = []
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(golds, preds))
        fp = sum(g != label and p == label for g, p in zip(golds, preds))
        fn = sum(g == label and p != label for g, p in zip(golds, preds))
        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        f1s.append(safe_div(2 * precision * recall, precision + recall))
    return sum(f1s) / len(f1s) if f1s else 0.0


def balanced_accuracy(golds: list[str], preds: list[str]) -> float:
    labels = sorted(set(golds))
    recalls = []
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(golds, preds))
        fn = sum(g == label and p != label for g, p in zip(golds, preds))
        recalls.append(safe_div(tp, tp + fn))
    return sum(recalls) / len(recalls) if recalls else 0.0


def prediction_diagnostics(rows: list[dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    gold_counts = Counter(row["gold"] for row in rows)
    prediction_counts = Counter(row["prediction"] for row in rows)
    confusion = {
        gold: {prediction: 0 for prediction in labels}
        for gold in labels
    }
    for row in rows:
        confusion.setdefault(row["gold"], {prediction: 0 for prediction in labels})
        confusion[row["gold"]][row["prediction"]] = confusion[row["gold"]].get(row["prediction"], 0) + 1

    per_class: dict[str, dict[str, float | int]] = {}
    for label in labels:
        tp = confusion.get(label, {}).get(label, 0)
        fp = sum(confusion.get(gold, {}).get(label, 0) for gold in labels if gold != label)
        fn = sum(count for pred, count in confusion.get(label, {}).items() if pred != label)
        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        f1 = safe_div(2 * precision * recall, precision + recall)
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": gold_counts[label],
            "predicted": prediction_counts[label],
        }

    return {
        "gold_counts": dict(gold_counts),
        "prediction_counts": dict(prediction_counts),
        "error_count": sum(not row["correct"] for row in rows),
        "confusion_matrix": {
            "labels": labels,
            "by_gold": confusion,
            "matrix": [[confusion.get(gold, {}).get(prediction, 0) for prediction in labels] for gold in labels],
        },
        "per_class": per_class,
    }


def device_details(device: torch.device) -> dict[str, Any]:
    details: dict[str, Any] = {
        "device": str(device),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
    }
    if device.type == "cuda":
        details["cuda_device_name"] = torch.cuda.get_device_name()
        details["cuda_device_count"] = torch.cuda.device_count()
    return details


def natural_input_for(row: dict[str, Any]) -> dict[str, Any]:
    task_type = row["task_type"]
    payload = row.get("input") or {}
    if task_type == "operation_ticket_check":
        return {
            "ticket_text": payload.get("ticket_text"),
            "equipment_state": payload.get("equipment_state"),
            "operation_context": payload.get("operation_context"),
            "dispatch_permit_status": payload.get("dispatch_permit_status"),
            "monitoring_arrangement": payload.get("monitoring_arrangement"),
            "object_consistency": payload.get("object_consistency"),
            "key_checks": payload.get("key_checks"),
        }
    if task_type == "regulation_compliance_check":
        return {
            "scenario_id": payload.get("scenario_id") or row.get("scenario_id"),
            "grid_state_summary": payload.get("grid_state_summary"),
            "proposed_action": payload.get("proposed_action"),
            "operator_goal": payload.get("operator_goal"),
            "observed_issues": payload.get("observed_issues"),
            "rule_summary": payload.get("rule_summary"),
            "active_constraints": payload.get("active_constraints") or payload.get("observed_issues"),
        }
    if task_type == "dispatcher_intent_tool_call":
        return {
            "utterance": payload.get("utterance") or payload.get("user_utterance"),
            "grid_state_summary": payload.get("grid_state_summary") or payload.get("scenario_summary"),
            "command_channel": payload.get("command_channel"),
            "operator_request": payload.get("operator_request"),
        }
    return payload


def text_for(row: dict[str, Any], use_rationale: bool, input_profile: str) -> str:
    input_payload = natural_input_for(row) if input_profile == "natural" else row["input"]
    parts = [
        f"任务类型：{row['task_type']}",
        f"指令：{row['instruction']}",
        f"输入：{compact_json(input_payload)}",
    ]
    if use_rationale and row.get("rationale"):
        parts.append(f"解释：{row['rationale']}")
    return "\n".join(parts)


def label_for(row: dict[str, Any], task_type: str) -> str:
    field = SUPPORTED_TASKS[task_type]
    value = row.get(field)
    if not value:
        raise ValueError(f"Missing label field {field} for row {row['id']}")
    return str(value)


class ClassificationDataset(Dataset):
    def __init__(
        self,
        rows: list[dict[str, Any]],
        task_type: str,
        label_to_id: dict[str, int],
        use_rationale: bool,
        input_profile: str,
    ):
        self.items = []
        for row in rows:
            if row["task_type"] != task_type:
                continue
            label = label_for(row, task_type)
            self.items.append(
                {
                    "id": row["id"],
                    "text": text_for(row, use_rationale, input_profile),
                    "label_text": label,
                    "label_id": label_to_id[label],
                }
            )

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        return self.items[idx]


def collate_fn(tokenizer: Any, max_length: int):
    def _collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
        encoded = tokenizer(
            [item["text"] for item in batch],
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        )
        encoded["labels"] = torch.tensor([item["label_id"] for item in batch], dtype=torch.long)
        encoded["ids"] = [item["id"] for item in batch]
        encoded["label_texts"] = [item["label_text"] for item in batch]
        return encoded

    return _collate


def evaluate(
    model: Any,
    loader: DataLoader,
    id_to_label: dict[int, str],
    device: torch.device,
    desc: str = "eval",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    model.eval()
    golds: list[str] = []
    preds: list[str] = []
    rows: list[dict[str, Any]] = []
    with torch.no_grad():
        for batch in tqdm(loader, desc=desc, leave=False):
            labels = batch.pop("labels")
            ids = batch.pop("ids")
            label_texts = batch.pop("label_texts")
            inputs = {key: value.to(device) for key, value in batch.items()}
            logits = model(**inputs).logits
            pred_ids = logits.argmax(dim=-1).cpu().tolist()
            pred_labels = [id_to_label[idx] for idx in pred_ids]
            gold_labels = [id_to_label[idx] for idx in labels.tolist()]
            golds.extend(gold_labels)
            preds.extend(pred_labels)
            probs = torch.softmax(logits, dim=-1).cpu().tolist()
            for item_id, gold_label, pred_label, prob_vector in zip(ids, label_texts, pred_labels, probs):
                rows.append(
                    {
                        "id": item_id,
                        "gold": gold_label,
                        "prediction": pred_label,
                        "correct": gold_label == pred_label,
                        "probabilities": {id_to_label[idx]: prob for idx, prob in enumerate(prob_vector)},
                    }
                )
    accuracy = sum(g == p for g, p in zip(golds, preds)) / max(len(golds), 1)
    return (
        {
            "accuracy": accuracy,
            "macro_f1": macro_f1(golds, preds),
            "balanced_accuracy": balanced_accuracy(golds, preds),
            "n": len(golds),
        },
        rows,
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_jsonl(path, rows)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_dirs(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="data/v1.2_train.jsonl")
    parser.add_argument("--validation", default="data/v1.2_validation.jsonl")
    parser.add_argument("--test", default=None)
    parser.add_argument("--task-type", required=True, choices=sorted(SUPPORTED_TASKS))
    parser.add_argument("--model-name", default="bert-base-chinese")
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-prefix", default="benchmark/v1.2_transformer_classifier")
    parser.add_argument("--use-rationale", action="store_true")
    parser.add_argument("--input-profile", choices=["full", "natural"], default="full")
    parser.add_argument("--eval-only-model", default=None)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--experiment-dir", default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument(
        "--no-class-weights",
        action="store_true",
        help="Use plain cross entropy instead of inverse-frequency class weights.",
    )
    parser.add_argument(
        "--skip-latest-alias",
        action="store_true",
        help="Do not update reports/transformer_classifier_latest_report.* for sweep runs.",
    )
    args = parser.parse_args()

    started_at = time.time()
    set_seed(args.seed)
    train_rows_all = read_jsonl(ROOT / args.train)
    validation_rows_all = read_jsonl(ROOT / args.validation)
    test_rows_all = read_jsonl(ROOT / args.test) if args.test else []

    train_labels = [label_for(row, args.task_type) for row in train_rows_all if row["task_type"] == args.task_type]
    if not train_labels:
        raise ValueError(f"No train rows found for task type {args.task_type}.")
    label_list = sorted(set(train_labels))
    for split_name, split_rows in (("train", train_rows_all), ("validation", validation_rows_all), ("test", test_rows_all)):
        task_rows = [row for row in split_rows if row.get("task_type") == args.task_type]
        ids = [str(row.get("id") or "") for row in task_rows]
        if split_name != "test" or args.test:
            if not task_rows or any(not value for value in ids) or len(ids) != len(set(ids)):
                raise ValueError(f"{split_name} task rows must be non-empty with unique IDs")
        unseen = {label_for(row, args.task_type) for row in task_rows} - set(label_list)
        if unseen:
            raise ValueError(f"{split_name} contains labels absent from training: {sorted(unseen)}")
    label_to_id = {label: idx for idx, label in enumerate(label_list)}
    id_to_label = {idx: label for label, idx in label_to_id.items()}

    train_dataset = ClassificationDataset(
        train_rows_all, args.task_type, label_to_id, args.use_rationale, args.input_profile
    )
    validation_dataset = ClassificationDataset(
        validation_rows_all, args.task_type, label_to_id, args.use_rationale, args.input_profile
    )
    test_dataset = (
        ClassificationDataset(test_rows_all, args.task_type, label_to_id, args.use_rationale, args.input_profile)
        if args.test
        else None
    )

    if len(validation_dataset) == 0:
        raise ValueError(f"No validation rows found for task type {args.task_type}.")

    requested_model_name = args.model_name
    requested_model_source = args.eval_only_model or args.model_name
    model_source = requested_model_source
    load_model_name = args.model_name
    local_files_only = (
        args.local_files_only
        or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
        or os.environ.get("HF_HUB_OFFLINE") == "1"
    )
    if local_files_only:
        model_source = local_hf_snapshot(model_source)
        load_model_name = local_hf_snapshot(args.model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_source, local_files_only=local_files_only)
    if args.eval_only_model:
        model = AutoModelForSequenceClassification.from_pretrained(
            model_source,
            local_files_only=local_files_only
        )
    else:
        model = AutoModelForSequenceClassification.from_pretrained(
            load_model_name,
            num_labels=len(label_list),
            local_files_only=local_files_only
        )
    device = torch.device("cpu") if args.cpu else select_device()
    model.to(device)

    collate = collate_fn(tokenizer, args.max_length)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=collate)
    validation_loader = DataLoader(validation_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate)
    test_loader = (
        DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate)
        if test_dataset and len(test_dataset) > 0
        else None
    )
    prefix = ROOT / args.output_prefix
    experiment_dir = ROOT / args.experiment_dir if args.experiment_dir else ROOT / "experiments" / "04_transformer_classification" / args.task_type
    checkpoint_dir = experiment_dir / "checkpoints"
    log_dir = experiment_dir / "logs"
    ensure_dirs(prefix.parent, ROOT / "reports", checkpoint_dir, log_dir)
    training_log = log_dir / "training_log.jsonl"
    if training_log.exists() and not args.eval_only_model:
        training_log.unlink()

    history = []
    best_epoch = 0
    best_validation = {"accuracy": -1.0, "macro_f1": -1.0, "balanced_accuracy": -1.0, "n": 0}
    best_state = copy.deepcopy(model.state_dict())

    class_weights = []
    if args.eval_only_model:
        if training_log.exists():
            with training_log.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    validation = record.get("validation", {})
                    history.append(
                        {
                            "epoch": record.get("epoch"),
                            "train_loss": record.get("train_loss"),
                            "validation_accuracy": validation.get("accuracy"),
                            "validation_macro_f1": validation.get("macro_f1"),
                            "validation_balanced_accuracy": validation.get("balanced_accuracy"),
                        }
                    )
            if history:
                best_epoch = int(history[-1]["epoch"] or 0)
    else:
        if args.no_class_weights:
            loss_fn = nn.CrossEntropyLoss()
        else:
            class_counts = Counter(item["label_id"] for item in train_dataset.items)
            class_weights = [
                len(train_dataset) / (len(label_list) * class_counts[idx]) for idx in range(len(label_list))
            ]
            loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(class_weights, dtype=torch.float32, device=device))
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)

        for epoch in range(1, args.epochs + 1):
            model.train()
            losses = []
            progress = tqdm(train_loader, desc=f"train {args.task_type} epoch {epoch}/{args.epochs}", leave=False)
            for batch in progress:
                labels = batch.pop("labels").to(device)
                batch.pop("ids")
                batch.pop("label_texts")
                inputs = {key: value.to(device) for key, value in batch.items()}
                logits = model(**inputs).logits
                loss = loss_fn(logits, labels)
                if not bool(torch.isfinite(loss)):
                    raise RuntimeError(f"non-finite loss at epoch {epoch}")
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                loss_value = float(loss.detach().cpu())
                losses.append(loss_value)
                progress.set_postfix(loss=f"{loss_value:.4f}")
            val_metrics, _ = evaluate(model, validation_loader, id_to_label, device, desc=f"validate {args.task_type} epoch {epoch}")
            epoch_record = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "epoch": epoch,
                "train_loss": sum(losses) / max(len(losses), 1),
                "validation": val_metrics,
            }
            append_jsonl(training_log, epoch_record)
            history.append(
                {
                    "epoch": epoch,
                    "train_loss": sum(losses) / max(len(losses), 1),
                    "validation_accuracy": val_metrics["accuracy"],
                    "validation_macro_f1": val_metrics["macro_f1"],
                    "validation_balanced_accuracy": val_metrics["balanced_accuracy"],
                }
            )
            if (
                val_metrics["macro_f1"] > best_validation["macro_f1"]
                or (
                    val_metrics["macro_f1"] == best_validation["macro_f1"]
                    and val_metrics["accuracy"] > best_validation["accuracy"]
                )
            ):
                best_epoch = epoch
                best_validation = val_metrics
                best_state = copy.deepcopy(model.state_dict())
                torch.save(best_state, checkpoint_dir / "best_model_state.pt")
                model.save_pretrained(checkpoint_dir / "best_model")
                tokenizer.save_pretrained(checkpoint_dir / "best_model")

    model.load_state_dict(best_state)
    final_validation, validation_predictions = evaluate(model, validation_loader, id_to_label, device, desc=f"final validate {args.task_type}")
    validation_diagnostics = prediction_diagnostics(validation_predictions, label_list)
    if args.eval_only_model and best_validation["macro_f1"] < 0:
        best_validation = final_validation
    test_metrics: dict[str, Any] | None = None
    test_diagnostics: dict[str, Any] | None = None
    test_predictions: list[dict[str, Any]] = []
    if test_loader is not None:
        test_metrics, test_predictions = evaluate(model, test_loader, id_to_label, device, desc=f"test {args.task_type}")
        test_diagnostics = prediction_diagnostics(test_predictions, label_list)

    validation_prediction_path = Path(f"{prefix}_validation_predictions.jsonl")
    test_prediction_path = Path(f"{prefix}_test_predictions.jsonl")
    write_jsonl(validation_prediction_path, validation_predictions)
    write_jsonl(test_prediction_path, test_predictions)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "train": args.train,
        "validation": args.validation,
        "test": args.test,
        "task_type": args.task_type,
        "objective": "transformer_sequence_classification",
        "model_name": requested_model_name,
        "model_source": requested_model_source,
        "resolved_model_source": model_source,
        "resolved_model_name": load_model_name,
        "eval_only_model": args.eval_only_model,
        "run_kind": "heldout_evaluation" if args.test else "validation_only_probe",
        "device": str(device),
        "device_details": device_details(device),
        "determinism_contract": determinism_contract(),
        "train_records": len(train_dataset),
        "validation_records": len(validation_dataset),
        "test_records": len(test_dataset) if test_dataset is not None else 0,
        "input_sha256": {
            "train": sha256(ROOT / args.train),
            "validation": sha256(ROOT / args.validation),
            "test": sha256(ROOT / args.test) if args.test else None,
        },
        "prediction_sha256": {
            "validation": sha256(validation_prediction_path),
            "test": sha256(test_prediction_path),
        },
        "label_space": label_list,
        "config": {
            "output_prefix": args.output_prefix,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "weight_decay": args.weight_decay,
            "max_length": args.max_length,
            "seed": args.seed,
            "use_rationale": args.use_rationale,
            "input_profile": args.input_profile,
            "eval_only_model": args.eval_only_model,
            "local_files_only": local_files_only,
            "cpu": args.cpu,
            "class_weighting": "none" if args.no_class_weights else "inverse_frequency",
            "experiment_dir": str(experiment_dir.relative_to(ROOT)),
            "training_log": str(training_log.relative_to(ROOT)),
            "class_weights": {id_to_label[idx]: weight for idx, weight in enumerate(class_weights)},
            "skip_latest_alias": args.skip_latest_alias,
        },
        "history": history,
        "best_epoch": best_epoch,
        "best_validation": best_validation,
        "final_validation": final_validation,
        "validation_diagnostics": validation_diagnostics,
        "test_evaluation": test_metrics,
        "test_diagnostics": test_diagnostics,
        "runtime_seconds": time.time() - started_at,
        "note": (
            "Local Transformer classification baseline with held-out evaluation."
            if args.test
            else "Local Transformer classification baseline."
        ),
    }
    write_json(Path(f"{prefix}_report.json"), report)

    if not args.skip_latest_alias:
        alias_payload = copy.deepcopy(report)
        alias_payload["alias_name"] = "reports/transformer_classifier_latest_report.json"
        write_json(ROOT / "reports/transformer_classifier_latest_report.json", alias_payload)

    md_lines = [
        "# Transformer Classifier Baseline Report",
        "",
        f"- Task type: {args.task_type}",
        f"- Model: {args.model_name}",
        f"- Device: {report['device']}",
        f"- Objective: {report['objective']}",
        f"- Train records: {report['train_records']}",
        f"- Validation records: {report['validation_records']}",
        f"- Best epoch: {report['best_epoch']}",
        f"- Validation accuracy: {final_validation['accuracy']:.4f}",
        f"- Validation macro F1: {final_validation['macro_f1']:.4f}",
        f"- Validation balanced accuracy: {final_validation['balanced_accuracy']:.4f}",
        f"- Rationale input enabled: {args.use_rationale}",
        f"- Input profile: {args.input_profile}",
    ]
    if test_metrics:
        md_lines.extend(
            [
                f"- Test split: {args.test}",
                f"- Test records: {report['test_records']}",
                f"- Test accuracy: {test_metrics['accuracy']:.4f}",
                f"- Test macro F1: {test_metrics['macro_f1']:.4f}",
                f"- Test balanced accuracy: {test_metrics['balanced_accuracy']:.4f}",
            ]
        )
        if test_diagnostics and args.task_type == "operation_ticket_check":
            non_compliant = test_diagnostics["per_class"].get("non_compliant")
            monitoring = test_diagnostics["per_class"].get("compliant_with_monitoring")
            if non_compliant:
                md_lines.append(f"- Test non-compliant recall: {non_compliant['recall']:.4f}")
            if monitoring:
                md_lines.append(f"- Test monitoring recall: {monitoring['recall']:.4f}")
    md_lines.extend(["", report["note"]])
    Path(f"{prefix}_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    if not args.skip_latest_alias:
        (ROOT / "reports/transformer_classifier_latest_report.md").write_text(
            "\n".join(md_lines) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
