#!/usr/bin/env python3
"""Run and aggregate five-seed neural baselines on frozen English splits."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from importlib.metadata import PackageNotFoundError, version

from gridinstruct_utils import ROOT, write_json


CLASSIFICATION_TASKS = {
    "operation_ticket_check",
    "regulation_compliance_check",
    "dispatcher_intent_tool_call",
}
GENERATION_TASKS = {"regulation_qa", "intelligent_data_query", "auxiliary_decision"}
PRESPECIFIED_SEEDS = (13, 29, 42, 57, 71)


def environment_versions() -> dict[str, str | None]:
    packages = ("torch", "transformers", "scikit-learn", "pandapower", "lm-format-enforcer")
    values: dict[str, str | None] = {"python": platform.python_version()}
    for package in packages:
        try:
            values[package] = version(package)
        except PackageNotFoundError:
            values[package] = None
    return values


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_hashes(train: str, validation: str, test: str) -> dict[str, str]:
    return {
        "train": sha256(ROOT / train),
        "validation": sha256(ROOT / validation),
        "test": sha256(ROOT / test),
    }


def model_revision_fingerprint(model_name: str) -> dict[str, Any]:
    """Hash the exact local model snapshot used by the preregistered suite."""
    direct = Path(model_name).expanduser()
    snapshot: Path | None = direct.resolve() if direct.exists() else None
    if snapshot is None:
        cache_root = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{model_name.replace('/', '--')}"
        ref = cache_root / "refs" / "main"
        if ref.exists():
            revision = ref.read_text(encoding="utf-8").strip()
            candidate = cache_root / "snapshots" / revision
            if candidate.exists():
                snapshot = candidate.resolve()
        if snapshot is None and (cache_root / "snapshots").exists():
            candidates = sorted((cache_root / "snapshots").iterdir())
            if len(candidates) == 1:
                snapshot = candidates[0].resolve()
    if snapshot is None:
        return {"model_name": model_name, "snapshot": None, "sha256": None}

    required_names = {
        "config.json",
        "generation_config.json",
        "model.safetensors",
        "pytorch_model.bin",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "spiece.model",
        "vocab.txt",
    }
    members = []
    for path in sorted(item for item in snapshot.rglob("*") if item.is_file() and item.name in required_names):
        members.append(
            {
                "path": str(path.relative_to(snapshot)),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    payload = json.dumps(members, sort_keys=True, separators=(",", ":"))
    return {
        "model_name": model_name,
        "snapshot": str(snapshot),
        "member_count": len(members),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def finite_tree(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, dict):
        return all(finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(finite_tree(item) for item in value)
    return False


def load_prediction_ids(path: Path) -> list[str]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"invalid prediction row at {path}:{line_number}")
            rows.append(row)
    return [str(row["id"]) for row in rows]


def expected_test_ids(path: Path, task: str) -> set[str]:
    ids = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("task_type") == task:
                ids.add(str(row["id"]))
    if not ids:
        raise ValueError(f"no {task} rows found in {path}")
    return ids


def expected_test_gold(path: Path, task: str, family: str) -> dict[str, str]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if row.get("task_type") == task:
                    rows.append(row)
    if family == "classification":
        field = "intent" if task == "dispatcher_intent_tool_call" else "compliance_label"
        return {str(row["id"]): str(row[field]) for row in rows}
    from run_seq2seq_generation_baseline import target_for

    target_mode = "actionable" if task == "intelligent_data_query" else "full"
    return {str(row["id"]): target_for(row, target_mode).strip() for row in rows}


def prediction_gold_matches(path: Path, expected: dict[str, str]) -> bool:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("gold", "")).strip() != expected.get(str(row.get("id"))):
                return False
    return True


def expected_report_config(task: str, family: str, seed: int, args: argparse.Namespace) -> dict[str, Any]:
    if family == "classification":
        compliance = task == "regulation_compliance_check"
        return {
            "epochs": args.epochs if args.epochs is not None else (12 if compliance else 8),
            "batch_size": args.batch_size if args.batch_size is not None else 64,
            "learning_rate": 2e-5 if compliance else 5e-5,
            "max_length": args.max_length if args.max_length is not None else 256,
            "seed": seed,
            "input_profile": "full" if compliance else "natural",
            "class_weighting": "none" if compliance else "inverse_frequency",
        }
    return {
        "epochs": 10,
        "batch_size": 32,
        "learning_rate": 0.0005,
        "seed": seed,
        "max_source_len": 384,
        "max_target_len": 160 if task == "intelligent_data_query" else 320,
        "target_mode": "actionable" if task == "intelligent_data_query" else "full",
        "prompt_mode": "fielded" if task == "regulation_qa" else "compact",
        "num_beams": 1,
        "local_files_only": True,
        "json_constrained_decoding": task in {"intelligent_data_query", "auxiliary_decision"},
    }


def report_is_fresh(
    path: Path,
    predictions: Path,
    state_path: Path,
    hashes: dict[str, str],
    run_spec_sha256: str,
    task: str,
    family: str,
    seed: int,
    expected_ids: set[str],
    expected_gold: dict[str, str],
    model_snapshot: str,
    args: argparse.Namespace,
) -> bool:
    if not path.exists() or not predictions.exists() or not state_path.exists():
        return False
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        prediction_ids = load_prediction_ids(predictions)
    except (json.JSONDecodeError, OSError, ValueError):
        return False
    prediction_count = len(prediction_ids)
    unique_prediction_count = len(set(prediction_ids))
    expected_task = report.get("task_type") if family == "classification" else (report.get("task_types") or [None])[0]
    config = report.get("config") or {}
    expected_config = expected_report_config(task, family, seed, args)
    metrics = report.get("test_evaluation")
    if family == "classification":
        required_metrics = {"accuracy", "macro_f1", "balanced_accuracy"}
    elif task == "regulation_qa":
        required_metrics = {"exact_match", "token_f1", "selection_f1"}
    else:
        required_metrics = {
            "exact_match",
            "char_f1",
            "selection_f1",
            "prediction_is_json",
        }
    return bool(
        report.get("input_sha256") == hashes
        and Path(str(report.get("model_name"))).resolve() == Path(model_snapshot).resolve()
        and expected_task == task
        and all(config.get(key) == value for key, value in expected_config.items())
        and state.get("run_spec_sha256") == run_spec_sha256
        and state.get("report_sha256") == sha256(path)
        and state.get("prediction_sha256") == sha256(predictions)
        and prediction_count == report.get("test_records")
        and prediction_count > 0
        and unique_prediction_count == prediction_count
        and set(prediction_ids) == expected_ids
        and prediction_gold_matches(predictions, expected_gold)
        and isinstance(metrics, dict)
        and bool(metrics)
        and required_metrics.issubset(metrics)
        and int(metrics.get("n", -1)) == prediction_count
        and finite_tree(metrics)
    )


def run(cmd: list[str], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, text=True)
    if completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}); see {log.relative_to(ROOT)}")


def mean_std(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "mean": statistics.mean(values) if values else None,
        "sample_std": statistics.stdev(values) if len(values) > 1 else 0.0 if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def classification_cmd(args: argparse.Namespace, seed: int, prefix: str, experiment: str, model_snapshot: str) -> list[str]:
    compliance = args.task == "regulation_compliance_check"
    epochs = args.epochs if args.epochs is not None else (12 if compliance else 8)
    batch_size = args.batch_size if args.batch_size is not None else 64
    max_length = args.max_length if args.max_length is not None else 256
    cmd = [
        sys.executable,
        "scripts/run_transformer_classifier_baseline.py",
        "--task-type",
        args.task,
        "--train",
        args.train,
        "--validation",
        args.validation,
        "--test",
        args.test,
        "--model-name",
        model_snapshot,
        "--epochs",
        str(epochs),
        "--batch-size",
        str(batch_size),
        "--learning-rate",
        "2e-5" if compliance else "5e-5",
        "--max-length",
        str(max_length),
        "--seed",
        str(seed),
        "--input-profile",
        "full" if compliance else "natural",
        "--local-files-only",
        "--skip-latest-alias",
        "--output-prefix",
        prefix,
        "--experiment-dir",
        experiment,
    ]
    if compliance:
        cmd.append("--no-class-weights")
    return cmd


def generation_cmd(args: argparse.Namespace, seed: int, prefix: str, experiment: str, model_snapshot: str) -> list[str]:
    target_mode = "actionable" if args.task == "intelligent_data_query" else "full"
    prompt_mode = "fielded" if args.task == "regulation_qa" else "compact"
    max_target = "160" if args.task == "intelligent_data_query" else "320"
    cmd = [
        sys.executable,
        "scripts/run_seq2seq_generation_baseline.py",
        "--train",
        args.train,
        "--validation",
        args.validation,
        "--test",
        args.test,
        "--task-types",
        args.task,
        "--model-name",
        model_snapshot,
        "--epochs",
        "10",
        "--patience",
        "2",
        "--batch-size",
        "32",
        "--learning-rate",
        "0.0005",
        "--seed",
        str(seed),
        "--max-source-len",
        "384",
        "--max-target-len",
        max_target,
        "--target-mode",
        target_mode,
        "--prompt-mode",
        prompt_mode,
        "--num-beams",
        "1",
        "--local-files-only",
        "--output-prefix",
        prefix,
        "--experiment-dir",
        experiment,
    ]
    if args.task in {"intelligent_data_query", "auxiliary_decision"}:
        cmd.append("--json-constrained-decoding")
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(CLASSIFICATION_TASKS | GENERATION_TASKS))
    parser.add_argument("--train", default="data/v1.2_sd_core_train_en.jsonl")
    parser.add_argument("--validation", default="data/v1.2_sd_core_validation_en.jsonl")
    parser.add_argument("--test", default="data/v1.2_sd_core_test_en.jsonl")
    parser.add_argument("--seeds", nargs="+", type=int, default=list(PRESPECIFIED_SEEDS))
    parser.add_argument("--output-dir", default="benchmark/multiseed_v1.2_sd_core")
    parser.add_argument("--experiment-dir", default="experiments/multiseed_v1.2_sd_core")
    parser.add_argument("--epochs", type=int, default=None, help="Optional classification override; defaults preserve the original suite.")
    parser.add_argument("--batch-size", type=int, default=None, help="Optional classification batch-size override.")
    parser.add_argument("--max-length", type=int, default=None, help="Optional classification token-length override.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if tuple(args.seeds) != PRESPECIFIED_SEEDS or len(set(args.seeds)) != len(PRESPECIFIED_SEEDS):
        raise SystemExit(f"--seeds must exactly match preregistered unique seeds {list(PRESPECIFIED_SEEDS)}")

    family = "classification" if args.task in CLASSIFICATION_TASKS else "generation"
    hashes = current_hashes(args.train, args.validation, args.test)
    model_name = "distilbert-base-uncased" if family == "classification" else "t5-small"
    model_revision = model_revision_fingerprint(model_name)
    if not model_revision.get("sha256"):
        raise SystemExit(f"local model snapshot is unavailable for {model_name}")
    model_snapshot = str(model_revision["snapshot"])
    test_ids = expected_test_ids(ROOT / args.test, args.task)
    test_gold = expected_test_gold(ROOT / args.test, args.task, family)
    runs: list[dict[str, Any]] = []
    for seed in args.seeds:
        prefix = f"{args.output_dir}/{family}_{args.task}_seed{seed}"
        experiment = f"{args.experiment_dir}/{family}_{args.task}/seed{seed}"
        report_path = ROOT / f"{prefix}_report.json"
        predictions_path = ROOT / f"{prefix}_test_predictions.jsonl"
        state_path = ROOT / f"{prefix}_run_state.json"
        log_path = ROOT / experiment / "logs" / "suite.stdout.log"
        cmd = (
            classification_cmd(args, seed, prefix, experiment, model_snapshot)
            if family == "classification"
            else generation_cmd(args, seed, prefix, experiment, model_snapshot)
        )
        runner = ROOT / cmd[1]
        run_spec = {
            "task": args.task,
            "family": family,
            "seed": seed,
            "command": cmd,
            "input_sha256": hashes,
            "suite_script_sha256": sha256(Path(__file__).resolve()),
            "runner_script_sha256": sha256(runner),
            "model_revision": model_revision,
            "environment_versions": environment_versions(),
        }
        run_spec_sha256 = hashlib.sha256(
            json.dumps(run_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if args.force or not report_is_fresh(
            report_path,
            predictions_path,
            state_path,
            hashes,
            run_spec_sha256,
            args.task,
            family,
            seed,
            test_ids,
            test_gold,
            model_snapshot,
            args,
        ):
            run(cmd, log_path)
            if not report_is_fresh(
                report_path,
                predictions_path,
                state_path,
                hashes,
                run_spec_sha256,
                args.task,
                family,
                seed,
                test_ids,
                test_gold,
                model_snapshot,
                args,
            ):
                report = json.loads(report_path.read_text(encoding="utf-8"))
                prediction_ids = load_prediction_ids(predictions_path)
                prediction_count = len(prediction_ids)
                unique_prediction_count = len(set(prediction_ids))
                state = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "run_spec": run_spec,
                    "run_spec_sha256": run_spec_sha256,
                    "prediction_sha256": sha256(predictions_path),
                    "prediction_count": prediction_count,
                    "unique_prediction_count": unique_prediction_count,
                    "report_sha256": sha256(report_path),
                }
                write_json(state_path, state)
            if not report_is_fresh(
                report_path,
                predictions_path,
                state_path,
                hashes,
                run_spec_sha256,
                args.task,
                family,
                seed,
                test_ids,
                test_gold,
                model_snapshot,
                args,
            ):
                raise RuntimeError(f"freshness/integrity validation failed for {args.task} seed {seed}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        metric = report["test_evaluation"]
        runs.append(
            {
                "seed": seed,
                "report": str(report_path.relative_to(ROOT)),
                "test_predictions": f"{prefix}_test_predictions.jsonl",
                "prediction_sha256": sha256(predictions_path),
                "run_state": str(state_path.relative_to(ROOT)),
                "run_spec_sha256": run_spec_sha256,
                "metrics": metric,
                "input_sha256": report.get("input_sha256"),
            }
        )

    metric_keys = sorted(
        {
            key
            for row in runs
            for key, value in row["metrics"].items()
            if isinstance(value, (int, float)) and key != "n"
        }
    )
    aggregate = {
        key: mean_std([float(row["metrics"][key]) for row in runs if key in row["metrics"]])
        for key in metric_keys
    }
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if [row["seed"] for row in runs] == list(PRESPECIFIED_SEEDS) else "fail",
        "family": family,
        "task": args.task,
        "seeds": args.seeds,
        "split_paths": {"train": args.train, "validation": args.validation, "test": args.test},
        "input_sha256": hashes,
        "model_revision": model_revision,
        "classification_training_overrides": {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "max_length": args.max_length,
            "defaults_preserved_when_null": True,
        },
        "suite_script_sha256": sha256(Path(__file__).resolve()),
        "runs": runs,
        "aggregate": aggregate,
        "selection_policy": "all prespecified seeds retained; no best-seed selection",
    }
    output_path = ROOT / args.output_dir / f"{family}_{args.task}_five_seed_summary.json"
    write_json(output_path, output)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if output["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
