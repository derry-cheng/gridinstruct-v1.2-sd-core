#!/usr/bin/env python3
"""Unified GridInstruct SD-core release and experiment pipeline.

This entry point rebuilds the SD-facing core snapshot, refreshes leakage-aware
splits, validates data quality, checks model evidence, regenerates English
figures, and writes the final SD assessment. Heavy model-training steps are part
of the pipeline and are skipped when their formal outputs already exist unless
--force is supplied.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.metadata
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tqdm.auto import tqdm

from evidence_contract import CURRENT_INPUTS, EVIDENCE_PATHS
from gridinstruct_utils import ROOT, ensure_dirs, write_json


PYTHON = sys.executable
DATASET = "data/gridinstruct_v1.2_sd_core.jsonl"
ENGLISH_DATASET = "data/gridinstruct_v1.2_sd_core_en.jsonl"
DEFAULT_RUN_ROOT = "experiments/sd_core_rebuild_v17_20260724"
STAGE_SELECTED = "data/stages/v1.2_sd_core/01_selected.jsonl"
STAGE_FROZEN = "data/stages/v1.2_sd_core/02_frozen_v1.jsonl"
STAGE_TOPOLOGY = "data/stages/v1.2_sd_core/03_topology_v2.jsonl"
STAGE_EQUIPMENT = "data/stages/v1.2_sd_core/04_equipment.jsonl"
STAGE_GROUNDED = "data/stages/v1.2_sd_core/04_equipment_grounded.jsonl"
STAGE_OPF = "data/stages/v1.2_sd_core/05_opf.jsonl"
STAGE_QUERY = "data/stages/v1.2_sd_core/06_query_repaired.jsonl"
STAGE_OPERATION = "data/stages/v1.2_sd_core/07_operation_state.jsonl"
STAGE_RULE = "data/stages/v1.2_sd_core/08_rule_linked.jsonl"
STAGE_COMPLIANCE = "data/stages/v1.2_sd_core/09_compliance_recomputed.jsonl"
OPF_SOURCE_SCENARIOS = (
    "simulation_outputs/opf_closed_loop/" "ieee14_ieee118_source_scenarios_v1.json"
)
V16_PLUS12 = "data/stages/augmentation_v16/12_dispatcher_counterfactuals.jsonl"
V16_PLUS13 = "data/stages/augmentation_v16/13_monitoring_counterfactuals.jsonl"
V16_PLUS14 = "data/stages/augmentation_v16/14_plain_ticket_adversarial.jsonl"
V16_PLUS15 = "data/gridinstruct_v1.2_paper_candidate_actionable_plus15_v16.jsonl"
TRAIN = "data/v1.2_sd_core_train.jsonl"
VALIDATION = "data/v1.2_sd_core_validation.jsonl"
TEST = "data/v1.2_sd_core_test.jsonl"
OOD = "data/v1.2_sd_core_ood_test.jsonl"
EVALUATION = "benchmark/v1.2_sd_core_evaluation_tasks.json"
REVIEW_REPORT = "reports/llm_review_report_v1.2_paper_cumulative_current.json"
STRICT_TRAIN = "data/v1.2_sd_core_strict_train.jsonl"
STRICT_VALIDATION = "data/v1.2_sd_core_strict_validation.jsonl"
STRICT_TEST = "data/v1.2_sd_core_strict_test.jsonl"
TEMPLATE_HOLDOUT_TRAIN = "data/v1.2_sd_core_template_holdout_train.jsonl"
TEMPLATE_HOLDOUT_VALIDATION = "data/v1.2_sd_core_template_holdout_validation.jsonl"
TEMPLATE_HOLDOUT_TEST = "data/v1.2_sd_core_template_holdout_test.jsonl"


def english_split(path: str) -> str:
    split_path = Path(path)
    if split_path.suffix == ".jsonl" and not split_path.stem.endswith("_en"):
        return str(split_path.with_name(f"{split_path.stem}_en{split_path.suffix}"))
    return path


@dataclass
class Step:
    name: str
    experiment: str
    cmd: list[str]
    expected_outputs: list[str]
    heavy: bool = False
    input_paths: list[str] | None = None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def controlled_code_tree() -> dict[str, Any]:
    files = [
        {"path": rel(path), "sha256": file_sha256(path), "size": path.stat().st_size}
        for path in sorted((ROOT / "scripts").glob("*.py"))
    ]
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    packages = {}
    for name in (
        "numpy",
        "pandas",
        "scipy",
        "scikit-learn",
        "torch",
        "transformers",
        "pandapower",
        "pypower",
        "lm-format-enforcer",
        "sentence-transformers",
        "datasketch",
        "lightsim2grid",
        "power-grid-model",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    cuda = {"available": False, "torch_cuda": None, "cudnn": None}
    try:
        import torch

        cuda = {
            "available": bool(torch.cuda.is_available()),
            "torch_cuda": torch.version.cuda,
            "cudnn": torch.backends.cudnn.version(),
        }
    except ImportError:
        pass
    environment = {
        key: os.environ.get(key)
        for key in (
            "CUDA_VISIBLE_DEVICES",
            "TRANSFORMERS_OFFLINE",
            "HF_HUB_OFFLINE",
            "CUBLAS_WORKSPACE_CONFIG",
            "PYTHONHASHSEED",
        )
    }
    return {
        "files": files,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "cuda": cuda,
        "environment": environment,
    }


def step_input_fingerprint(step: Step) -> dict[str, Any]:
    """Fingerprint command, scripts, and existing non-output file arguments."""
    expected = {(ROOT / item).resolve() for item in step.expected_outputs}
    candidates: set[Path] = set()
    tokens = list(step.cmd)
    if step.input_paths is not None:
        tokens = [
            token
            for token in step.cmd
            if isinstance(token, str) and token.endswith(".py")
        ] + step.input_paths
    for token in tokens:
        if not isinstance(token, str) or not token:
            continue
        expanded = (
            glob.glob(str(ROOT / token), recursive=True)
            if any(char in token for char in "*?[")
            else [str(ROOT / token)]
        )
        for item in expanded:
            path = Path(item)
            explicitly_input = (
                step.input_paths is not None and token in step.input_paths
            )
            resolved = path.resolve()
            if (
                path.exists()
                and path.is_file()
                and resolved.is_relative_to(ROOT)
                and (resolved not in expected or explicitly_input)
            ):
                candidates.add(resolved)
    files = [
        {"path": rel(path), "sha256": file_sha256(path), "size": path.stat().st_size}
        for path in sorted(candidates)
    ]
    payload = {
        "command": step.cmd,
        "inputs": files,
        "controlled_code_tree": controlled_code_tree(),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        **payload,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def exists_all(paths: list[str]) -> bool:
    return all(
        (ROOT / path).exists() and (ROOT / path).stat().st_size > 0 for path in paths
    )


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def write_run_script(path: Path, cmd: list[str]) -> None:
    path.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"cd {shlex.quote(str(ROOT))}\n"
        f"{shlex.join(cmd)}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def copy_result_files(run_root: Path, experiment: str, outputs: list[str]) -> None:
    result_dir = run_root / experiment / "results"
    ensure_dirs(result_dir)
    for item in outputs:
        src = ROOT / item
        if src.exists() and src.is_file():
            shutil.copy2(src, result_dir / src.name)


def run_step(
    step: Step, run_root: Path, force: bool, force_light: bool
) -> dict[str, Any]:
    exp_dir = run_root / step.experiment
    log_dir = exp_dir / "logs"
    config_dir = exp_dir / "configs"
    result_dir = exp_dir / "results"
    ensure_dirs(exp_dir, log_dir, config_dir, result_dir)
    write_run_script(exp_dir / "run.sh", step.cmd)
    write_run_script(exp_dir / f"run_{step.name}.sh", step.cmd)
    write_json(
        config_dir / f"{step.name}.json",
        {
            "name": step.name,
            "command": step.cmd,
            "expected_outputs": step.expected_outputs,
            "heavy": step.heavy,
        },
    )

    started_at = datetime.now(timezone.utc).isoformat()
    fingerprint = step_input_fingerprint(step)
    fingerprint_path = result_dir / f"{step.name}_input_fingerprint.json"
    prior_fingerprint = None
    prior_output_hashes: dict[str, str] = {}
    if fingerprint_path.exists():
        try:
            prior_state = json.loads(fingerprint_path.read_text(encoding="utf-8"))
            prior_fingerprint = prior_state.get("fingerprint")
            prior_output_hashes = prior_state.get("output_sha256") or {}
        except (json.JSONDecodeError, OSError):
            prior_fingerprint = None
    inputs_unchanged = prior_fingerprint == fingerprint["fingerprint"]
    outputs_unchanged = bool(prior_output_hashes) and all(
        (ROOT / item).is_file() and file_sha256(ROOT / item) == expected_hash
        for item, expected_hash in prior_output_hashes.items()
    )
    should_skip = (
        exists_all(step.expected_outputs)
        and inputs_unchanged
        and outputs_unchanged
        and not force
        and not (force_light and not step.heavy)
    )
    if should_skip:
        status = {
            "name": step.name,
            "experiment": step.experiment,
            "status": "skipped_existing",
            "heavy": step.heavy,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "returncode": 0,
            "expected_outputs": step.expected_outputs,
            "input_fingerprint": fingerprint["fingerprint"],
            "inputs_unchanged": True,
            "outputs_unchanged": True,
        }
        write_json(result_dir / f"{step.name}_status.json", status)
        copy_result_files(run_root, step.experiment, step.expected_outputs)
        return status

    stdout_path = log_dir / f"{step.name}.stdout.log"
    stderr_path = log_dir / f"{step.name}.stderr.log"
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("HF_HUB_OFFLINE", "1")
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            step.cmd, cwd=ROOT, stdout=stdout, stderr=stderr, text=True, env=env
        )
    missing = [item for item in step.expected_outputs if not (ROOT / item).exists()]
    post_fingerprint = step_input_fingerprint(step)
    input_drift_during_execution = (
        post_fingerprint["fingerprint"] != fingerprint["fingerprint"]
    )
    effective_returncode = (
        completed.returncode
        if completed.returncode != 0
        else (1 if missing or input_drift_during_execution else 0)
    )
    status = {
        "name": step.name,
        "experiment": step.experiment,
        "status": "pass" if effective_returncode == 0 else "fail",
        "heavy": step.heavy,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "returncode": effective_returncode,
        "stdout_log": rel(stdout_path),
        "stderr_log": rel(stderr_path),
        "expected_outputs": step.expected_outputs,
        "input_fingerprint": fingerprint["fingerprint"],
        "inputs_unchanged": inputs_unchanged,
        "outputs_unchanged": outputs_unchanged,
        "input_drift_during_execution": input_drift_during_execution,
        "missing_expected_outputs": missing,
    }
    write_json(result_dir / f"{step.name}_status.json", status)
    if completed.returncode != 0:
        raise RuntimeError(f"Step failed: {step.name}. See {rel(stderr_path)}")
    if missing:
        raise RuntimeError(
            f"Step {step.name} completed but expected outputs are missing: {missing}"
        )
    if input_drift_during_execution:
        raise RuntimeError(
            f"Step {step.name} inputs or controlled code changed during execution; "
            "the outputs are not accepted as reproducible evidence"
        )
    output_hashes = {
        item: file_sha256(ROOT / item)
        for item in step.expected_outputs
        if (ROOT / item).is_file()
    }
    write_json(
        fingerprint_path,
        {
            **fingerprint,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "output_sha256": output_hashes,
        },
    )
    copy_result_files(run_root, step.experiment, step.expected_outputs)
    return status


def step_completion_errors(step: Step, run_root: Path) -> list[str]:
    errors = []
    result_dir = run_root / step.experiment / "results"
    status_path = result_dir / f"{step.name}_status.json"
    fingerprint_path = result_dir / f"{step.name}_input_fingerprint.json"
    if not status_path.is_file() or not fingerprint_path.is_file():
        return [f"missing_completion_manifest:{step.name}"]
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        state = json.loads(fingerprint_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [f"invalid_completion_manifest:{step.name}"]
    current = step_input_fingerprint(step)
    if status.get("returncode") != 0:
        errors.append(f"failed_upstream_step:{step.name}")
    if state.get("fingerprint") != current["fingerprint"]:
        errors.append(f"stale_upstream_inputs:{step.name}")
    output_hashes = state.get("output_sha256") or {}
    expected_existing = {
        item for item in step.expected_outputs if (ROOT / item).is_file()
    }
    if (
        set(output_hashes) != expected_existing
        or set(output_hashes) != set(step.expected_outputs)
        or any(
            not (ROOT / item).is_file() or file_sha256(ROOT / item) != expected
            for item, expected in output_hashes.items()
        )
    ):
        errors.append(f"stale_or_incomplete_upstream_outputs:{step.name}")
    return errors


def validate_completed_prefix(prefix: list[Step], run_root: Path) -> None:
    errors = [
        error for step in prefix for error in step_completion_errors(step, run_root)
    ]
    if errors:
        raise RuntimeError(
            "--start-at upstream completion validation failed: " + ";".join(errors[:30])
        )


def resume_index(steps: list[Step], run_root: Path) -> int:
    """Return the first step without a passing, current, hash-bound completion."""
    for index, step in enumerate(steps):
        if step_completion_errors(step, run_root):
            return index
    return len(steps)


def transformer_step(
    name: str,
    experiment: str,
    task: str,
    train: str,
    validation: str,
    test: str,
    output_prefix: str,
    epochs: int,
    seed: int = 42,
    learning_rate: float = 5e-5,
    input_profile: str = "natural",
    no_class_weights: bool = False,
) -> Step:
    cmd = [
        PYTHON,
        "scripts/run_transformer_classifier_baseline.py",
        "--task-type",
        task,
        "--train",
        english_split(train),
        "--validation",
        english_split(validation),
        "--test",
        english_split(test),
        "--model-name",
        "distilbert-base-uncased",
        "--epochs",
        str(epochs),
        "--batch-size",
        "64",
        "--learning-rate",
        str(learning_rate),
        "--max-length",
        "256",
        "--seed",
        str(seed),
        "--input-profile",
        input_profile,
        "--local-files-only",
        "--skip-latest-alias",
        "--output-prefix",
        output_prefix,
        "--experiment-dir",
        experiment,
    ]
    if no_class_weights:
        cmd.append("--no-class-weights")
    return Step(
        name=name,
        experiment=experiment,
        cmd=cmd,
        expected_outputs=[
            f"{output_prefix}_report.json",
            f"{output_prefix}_report.md",
            f"{output_prefix}_validation_predictions.jsonl",
            f"{output_prefix}_test_predictions.jsonl",
        ],
        heavy=True,
    )


def seq2seq_step(
    name: str,
    experiment: str,
    task: str,
    output_prefix: str,
    max_target_len: int,
    target_mode: str,
    prompt_mode: str,
) -> Step:
    cmd = [
        PYTHON,
        "scripts/run_seq2seq_generation_baseline.py",
        "--train",
        english_split(TRAIN),
        "--validation",
        english_split(VALIDATION),
        "--test",
        english_split(TEST),
        "--task-types",
        task,
        "--model-name",
        "t5-small",
        "--epochs",
        "10",
        "--patience",
        "2",
        "--batch-size",
        "32",
        "--learning-rate",
        "0.0005",
        "--seed",
        "43",
        "--max-source-len",
        "384",
        "--max-target-len",
        str(max_target_len),
        "--target-mode",
        target_mode,
        "--prompt-mode",
        prompt_mode,
        "--num-beams",
        "1",
        "--output-prefix",
        output_prefix,
        "--experiment-dir",
        experiment,
    ]
    if task in {
        "intelligent_data_query",
        "auxiliary_decision",
        "dispatcher_intent_tool_call",
    }:
        cmd.append("--json-constrained-decoding")
    return Step(
        name=name,
        experiment=experiment,
        cmd=cmd,
        expected_outputs=[
            f"{output_prefix}_report.json",
            f"{output_prefix}_report.md",
            f"{output_prefix}_validation_predictions.jsonl",
            f"{output_prefix}_test_predictions.jsonl",
        ],
        heavy=True,
    )


def classification_figure_step(
    name: str, experiment: str, report: str, predictions: str, title: str
) -> Step:
    output_dir = f"{experiment}/figures"
    return Step(
        name=name,
        experiment=experiment,
        cmd=[
            PYTHON,
            "scripts/visualize_classification_results.py",
            "--report",
            report,
            "--predictions",
            predictions,
            "--output-dir",
            output_dir,
            "--title",
            title,
        ],
        expected_outputs=[
            f"{output_dir}/confusion_matrix.png",
            f"{output_dir}/per_class_metrics.png",
            f"{output_dir}/per_class_metrics.csv",
            f"{output_dir}/figure_manifest.json",
        ],
    )


def multiseed_step(task: str) -> Step:
    classification_tasks = {
        "operation_ticket_check",
        "regulation_compliance_check",
        "dispatcher_intent_tool_call",
    }
    family = "classification" if task in classification_tasks else "generation"
    prefix = f"benchmark/multiseed_v1.2_sd_core/{family}_{task}"
    expected = [f"{prefix}_five_seed_summary.json"]
    for seed in (13, 29, 42, 57, 71):
        expected.extend(
            [
                f"{prefix}_seed{seed}_report.json",
                f"{prefix}_seed{seed}_test_predictions.jsonl",
                f"{prefix}_seed{seed}_run_state.json",
            ]
        )
    return Step(
        name=f"multiseed_{task}",
        experiment=f"05_multiseed/{family}_{task}",
        cmd=[
            PYTHON,
            "scripts/run_current_english_multiseed_suite.py",
            "--task",
            task,
            "--train",
            english_split(TRAIN),
            "--validation",
            english_split(VALIDATION),
            "--test",
            english_split(TEST),
        ],
        expected_outputs=expected,
        heavy=True,
    )


def materialize_english_step(name: str) -> Step:
    phase = name.removeprefix("materialize_english_after_")
    report_json = f"reports/english_split_materialization_{phase}_v1.2_sd_core.json"
    report_md = f"reports/english_split_materialization_{phase}_v1.2_sd_core.md"
    return Step(
        name=name,
        experiment="01_dataset_integrity",
        cmd=[
            PYTHON,
            "scripts/materialize_english_splits.py",
            "--translated-full",
            "data/gridinstruct_v1.2_sd_core_en.jsonl",
            "--report-json",
            report_json,
            "--report-md",
            report_md,
        ],
        expected_outputs=[
            report_json,
            report_md,
        ],
        input_paths=[
            "data/gridinstruct_v1.2_sd_core_en.jsonl",
        ],
    )


def build_steps(run_root: str = DEFAULT_RUN_ROOT) -> list[Step]:
    challenge_train = "data/v1.2_sd_core_challenge_train.jsonl"
    challenge_validation = "data/v1.2_sd_core_challenge_validation.jsonl"
    challenge_test = "data/v1.2_sd_core_challenge_test.jsonl"
    proxy_stress_source_splits = [
        f"data/proxy_stress/{task}/{split}.jsonl"
        for task in (
            "regulation_compliance_check",
            "operation_ticket_check",
            "dispatcher_intent_tool_call",
        )
        for split in ("train", "validation", "test")
    ]
    proxy_stress_english_splits = [
        english_split(path) for path in proxy_stress_source_splits
    ]
    steps: list[Step] = [
        Step(
            "materialize_frozen_augmentation_contract",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/build_frozen_augmentation_contract.py",
                "--input",
                "data/gridinstruct_v1.2_paper_candidate_actionable_plus15.jsonl",
            ],
            [
                "data/frozen_sources/gridinstruct_v1.2_plus11_full.jsonl",
                "data/frozen_sources/operation_implicit_ticket_llm_v1.jsonl",
                "metadata/frozen_augmentation_contract_v1.2.json",
            ],
            input_paths=[
                "data/gridinstruct_v1.2_paper_candidate_actionable_plus15.jsonl",
            ],
        ),
        Step(
            "rebuild_plus12_dispatcher_counterfactuals",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/augment_operation_dispatcher_counterfactuals.py",
                "--input",
                "data/frozen_sources/gridinstruct_v1.2_plus11_full.jsonl",
                "--output",
                V16_PLUS12,
                "--report-json",
                "reports/v16_plus12_dispatcher_counterfactuals.json",
                "--report-md",
                "reports/v16_plus12_dispatcher_counterfactuals.md",
            ],
            [
                V16_PLUS12,
                "reports/v16_plus12_dispatcher_counterfactuals.json",
                "reports/v16_plus12_dispatcher_counterfactuals.md",
            ],
            input_paths=[
                "data/frozen_sources/gridinstruct_v1.2_plus11_full.jsonl",
                "metadata/frozen_augmentation_contract_v1.2.json",
            ],
        ),
        Step(
            "rebuild_plus13_monitoring_counterfactuals",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/augment_operation_monitoring_counterfactuals.py",
                "--input",
                V16_PLUS12,
                "--output",
                V16_PLUS13,
                "--report-json",
                "reports/v16_plus13_monitoring_counterfactuals.json",
                "--report-md",
                "reports/v16_plus13_monitoring_counterfactuals.md",
            ],
            [
                V16_PLUS13,
                "reports/v16_plus13_monitoring_counterfactuals.json",
                "reports/v16_plus13_monitoring_counterfactuals.md",
            ],
            input_paths=[V16_PLUS12],
        ),
        Step(
            "rebuild_plus14_plain_ticket_adversarial",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/augment_operation_plain_ticket_adversarial.py",
                "--input",
                V16_PLUS13,
                "--output",
                V16_PLUS14,
                "--report-json",
                "reports/v16_plus14_plain_ticket_adversarial.json",
                "--report-md",
                "reports/v16_plus14_plain_ticket_adversarial.md",
            ],
            [
                V16_PLUS14,
                "reports/v16_plus14_plain_ticket_adversarial.json",
                "reports/v16_plus14_plain_ticket_adversarial.md",
            ],
            input_paths=[V16_PLUS13],
        ),
        Step(
            "replay_frozen_plus15_implicit_rows",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/replay_frozen_implicit_ticket_rows.py",
                "--input",
                V16_PLUS14,
                "--frozen-rows",
                "data/frozen_sources/operation_implicit_ticket_llm_v1.jsonl",
                "--output",
                V16_PLUS15,
                "--report",
                "reports/v16_frozen_implicit_ticket_replay.json",
            ],
            [V16_PLUS15, "reports/v16_frozen_implicit_ticket_replay.json"],
            input_paths=[
                V16_PLUS14,
                "data/frozen_sources/operation_implicit_ticket_llm_v1.jsonl",
                "metadata/frozen_augmentation_contract_v1.2.json",
            ],
        ),
        Step(
            "generate_grid_scenarios",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/generate_grid_scenarios.py",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--seed",
                "42",
                "--scale-generation-with-load",
                "--system-load-levels",
                "ieee118:0.7,0.8,0.9,1.0,1.1,1.2",
                "--system-load-levels",
                "ieee30:0.7,0.75,0.8,0.9,1.0,1.1,1.2,1.3",
                "--max-n2-per-system",
                "72",
                "--equipment-outage-load-levels",
                "1.0",
                "1.2",
                "--all-output",
                "simulation_outputs/power_flow/scenarios_all.json",
                "--converged-output",
                "simulation_outputs/power_flow/scenarios_converged_core.json",
                "--report-output",
                "reports/simulation_report.json",
            ],
            [
                "simulation_outputs/power_flow/scenarios_all.json",
                "simulation_outputs/power_flow/scenarios_converged_core.json",
                "reports/simulation_report.json",
            ],
        ),
        Step(
            "generate_extended_topology_stress",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/generate_extended_topology_stress.py",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--systems",
                "ieee300",
                "illinois200",
                "pegase89",
                "pegase1354",
                "rte1888",
                "rte2848",
                "pegase2869",
                "--load-levels",
                "0.95",
                "1.0",
                "1.05",
                "--system-load-levels",
                "ieee300:0.95,0.955",
                "--system-load-levels",
                "illinois200:0.50,0.60,0.68,0.72,0.76,0.80,0.82,0.84,0.86,0.88,0.92,0.96,0.98,1.00",
                "--system-load-levels",
                "pegase89:0.75,0.85,0.95,1.0,1.05",
                "--system-load-levels",
                "pegase1354:0.85,0.95,1.0",
                "--system-load-levels",
                "rte1888:0.95,1.0",
                "--system-load-levels",
                "rte2848:0.95,1.0,1.05",
                "--system-load-levels",
                "pegase2869:0.90,0.95,1.0",
                "--max-lines-per-system",
                "12",
                "--max-attempts-per-stratum",
                "512",
                "--attempts-json",
                "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
                "--required-envelope-count",
                "normal-envelope:80",
                "--required-envelope-count",
                "operational-stress:40",
                "--required-envelope-count",
                "emergency-stress:20",
                "--required-envelope-count",
                "extreme-stress:13",
                "--seed",
                "20260710",
                "--scenario-namespace",
                "sdv2",
            ],
            [
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
                "reports/extended_topology_stress_audit_v1.2_sd_core.json",
                "reports/extended_topology_stress_audit_v1.2_sd_core.md",
            ],
        ),
        Step(
            "generate_external_normal_common_support",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/generate_extended_topology_stress.py",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--systems",
                "pegase1354",
                "--load-levels",
                "0.85",
                "--dispatch-policy",
                "normal_ac_opf",
                "--max-lines-per-system",
                "12",
                "--max-attempts-per-stratum",
                "64",
                "--required-envelope-count",
                "normal-envelope:13",
                "--seed",
                "20260723",
                "--scenario-namespace",
                "matched_normal_v2_pegase1354",
                "--output-json",
                "simulation_outputs/topology_stress/matched_normal_pool_pegase1354.json",
                "--attempts-json",
                "reports/matched_normal_attempts_pegase1354.json",
                "--report-json",
                "reports/matched_normal_audit_pegase1354.json",
                "--report-md",
                "reports/matched_normal_audit_pegase1354.md",
            ],
            [
                "simulation_outputs/topology_stress/matched_normal_pool_pegase1354.json",
                "reports/matched_normal_attempts_pegase1354.json",
                "reports/matched_normal_audit_pegase1354.json",
                "reports/matched_normal_audit_pegase1354.md",
            ],
            heavy=True,
            input_paths=[
                "metadata/pglib_opf_v23_07_case_registry.json",
                "third_party/pglib-opf-v23.07/LICENSE",
            ],
        ),
        Step(
            "audit_pglib_equipment_rating_provenance",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/audit_equipment_rating_provenance.py",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
            ],
            [
                "reports/equipment_rating_provenance_v1.2_sd_core.json",
                "reports/equipment_rating_provenance_v1.2_sd_core.md",
            ],
            input_paths=[
                "metadata/pglib_opf_v23_07_case_registry.json",
                "third_party/pglib-opf-v23.07/LICENSE",
            ],
        ),
        Step(
            "audit_all_network_transformer_contract",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/audit_transformer_contract_all_networks.py",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
            ],
            [
                "reports/pandapower_transformer_contract_all_networks_v1.2_sd_core.json",
            ],
            input_paths=[
                "metadata/pglib_opf_v23_07_case_registry.json",
                "third_party/pglib-opf-v23.07/LICENSE",
            ],
        ),
        Step(
            "audit_full_ieee14_ieee118_n1_denominator",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/audit_full_n1_enumeration.py",
                "--systems",
                "ieee14",
                "ieee118",
                "--load-levels",
                "0.5",
                "0.6",
                "0.7",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
            ],
            [
                "reports/full_n1_enumeration_v1.2_sd_core.json",
                "reports/full_n1_enumeration_v1.2_sd_core.md",
            ],
            heavy=True,
            input_paths=[
                "metadata/pglib_opf_v23_07_case_registry.json",
                "third_party/pglib-opf-v23.07/LICENSE",
            ],
        ),
        Step(
            "build_external_network_severity_matched_strata",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/build_external_matched_strata.py",
                "--scenarios",
                "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
                "--additional-scenarios",
                "simulation_outputs/topology_stress/matched_normal_pool_pegase1354.json",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--target-per-cell",
                "13",
            ],
            [
                "reports/external_network_severity_matched_strata_v1.2_sd_core.json",
                "reports/external_network_severity_matched_strata_v1.2_sd_core.md",
                "simulation_outputs/topology_stress/external_matched_strata_v1.json",
            ],
            heavy=True,
            input_paths=[
                "reports/extended_topology_stress_attempts_v1.2_sd_core.json",
                "simulation_outputs/topology_stress/matched_normal_pool_pegase1354.json",
                "metadata/pglib_opf_v23_07_case_registry.json",
            ],
        ),
        Step(
            "audit_explicit_high_rating_sensitivity",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/audit_explicit_high_rating_sensitivity.py",
                "--scenarios",
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
            ],
            [
                "reports/explicit_high_rating_sensitivity_v1.2_sd_core.json",
            ],
            input_paths=[
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                "metadata/pglib_opf_v23_07_case_registry.json",
            ],
        ),
        Step(
            "migrate_frozen_external_topology_v2",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/migrate_frozen_external_topology.py",
                "--legacy-scenarios",
                "simulation_outputs/topology_stress/legacy_topology_scenarios_v1.json",
                "--current-scenarios",
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                "--frozen-records",
                "data/frozen_sources/extended_topology_instruction_v1.jsonl",
                "--output-records",
                "data/frozen_sources/extended_topology_instruction_v2.jsonl",
                "--checksum-manifest",
                "metadata/frozen_source_checksums_v2.json",
                "--report-json",
                "reports/frozen_external_topology_migration_v2.json",
                "--report-md",
                "reports/frozen_external_topology_migration_v2.md",
            ],
            [
                "data/frozen_sources/extended_topology_instruction_v2.jsonl",
                "metadata/frozen_source_checksums_v2.json",
                "reports/frozen_external_topology_migration_v2.json",
                "reports/frozen_external_topology_migration_v2.md",
            ],
            heavy=True,
            input_paths=[
                "simulation_outputs/topology_stress/legacy_topology_scenarios_v1.json",
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                "data/frozen_sources/extended_topology_instruction_v1.jsonl",
            ],
        ),
        Step(
            "merge_scenario_sources",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/merge_scenario_sources.py",
                "--source",
                "simulation_outputs/power_flow/scenarios_converged_core.json",
                "--source",
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                "--output",
                "simulation_outputs/contingency/scenario_rebuild_manifest.json",
                "--report",
                "reports/scenario_source_merge_v1.2_sd_core.json",
            ],
            [
                "simulation_outputs/contingency/scenario_rebuild_manifest.json",
                "reports/scenario_source_merge_v1.2_sd_core.json",
            ],
            input_paths=[
                "simulation_outputs/power_flow/scenarios_converged_core.json",
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
            ],
        ),
        Step(
            "recompute_complete_scenario_truth",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/recompute_scenario_truth.py",
                "--input",
                "simulation_outputs/contingency/scenario_rebuild_manifest.json",
                "--output",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--report-json",
                "reports/scenario_truth_rebuild_v1.2_sd_core.json",
                "--report-md",
                "reports/scenario_truth_rebuild_v1.2_sd_core.md",
                "--attempts-json",
                "reports/scenario_truth_rebuild_attempts_v1.2_sd_core.json",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
            ],
            [
                "simulation_outputs/contingency/scenarios_converged.json",
                "reports/scenario_truth_rebuild_v1.2_sd_core.json",
                "reports/scenario_truth_rebuild_v1.2_sd_core.md",
                "reports/scenario_truth_rebuild_attempts_v1.2_sd_core.json",
            ],
        ),
        Step(
            "audit_core_n1_denominator",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/audit_core_n1_denominator.py",
                "--attempts",
                "simulation_outputs/power_flow/scenarios_all.json",
                "--converged-core",
                "simulation_outputs/power_flow/scenarios_converged_core.json",
                "--complete-truth",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--simulation-report",
                "reports/simulation_report.json",
                "--case-registry",
                "metadata/pglib_opf_v23_07_case_registry.json",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--output-json",
                "reports/core_n1_denominator_v1.2_sd_core.json",
                "--output-md",
                "reports/core_n1_denominator_v1.2_sd_core.md",
            ],
            [
                "reports/core_n1_denominator_v1.2_sd_core.json",
                "reports/core_n1_denominator_v1.2_sd_core.md",
            ],
            input_paths=[
                "simulation_outputs/power_flow/scenarios_all.json",
                "simulation_outputs/power_flow/scenarios_converged_core.json",
                "simulation_outputs/contingency/scenarios_converged.json",
                "reports/simulation_report.json",
                "metadata/pglib_opf_v23_07_case_registry.json",
                "third_party/pglib-opf-v23.07/LICENSE",
                "third_party/pglib-opf-v23.07/pglib_opf_case14_ieee.m",
                "third_party/pglib-opf-v23.07/pglib_opf_case30_ieee.m",
                "third_party/pglib-opf-v23.07/pglib_opf_case57_ieee.m",
                "third_party/pglib-opf-v23.07/pglib_opf_case118_ieee.m",
            ],
        ),
        Step(
            "build_opf_scenario_projection",
            "00_physical_sources",
            [
                PYTHON,
                "scripts/build_opf_scenario_projection.py",
                "--input",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--output",
                OPF_SOURCE_SCENARIOS,
                "--report",
                "reports/opf_scenario_projection_v1.2_sd_core.json",
            ],
            [
                OPF_SOURCE_SCENARIOS,
                "reports/opf_scenario_projection_v1.2_sd_core.json",
            ],
            input_paths=[
                "simulation_outputs/contingency/scenarios_converged.json",
            ],
        ),
        Step(
            "build_sd_core_snapshot",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/build_sd_core_snapshot.py",
                "--input",
                V16_PLUS15,
                "--output",
                STAGE_SELECTED,
                "--report-json",
                "reports/sd_core_selection_report.json",
                "--report-md",
                "reports/sd_core_selection_report.md",
                "--max-augmented-per-source",
                "3",
            ],
            [
                STAGE_SELECTED,
                "reports/sd_core_selection_report.json",
                "reports/sd_core_selection_report.md",
            ],
        ),
        Step(
            "append_frozen_external_topology_v2",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/append_frozen_records.py",
                "--dataset",
                STAGE_SELECTED,
                "--output-dataset",
                STAGE_FROZEN,
                "--source",
                "data/frozen_sources/extended_topology_instruction_v2.jsonl",
                "--checksum-manifest",
                "metadata/frozen_source_checksums_v2.json",
                "--report",
                "reports/frozen_external_topology_v2_append.json",
            ],
            [STAGE_FROZEN, "reports/frozen_external_topology_v2_append.json"],
            input_paths=[
                STAGE_SELECTED,
                "data/frozen_sources/extended_topology_instruction_v2.jsonl",
                "metadata/frozen_source_checksums_v2.json",
            ],
        ),
        Step(
            "append_external_topology_v2",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/append_topology_instruction_records.py",
                "--dataset",
                STAGE_FROZEN,
                "--output-dataset",
                STAGE_TOPOLOGY,
                "--scenarios",
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                "--main-scenarios",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--variants-per-scenario",
                "4",
                "--report-json",
                "reports/topology_instruction_expansion_v1.2_sd_core.json",
                "--report-md",
                "reports/topology_instruction_expansion_v1.2_sd_core.md",
            ],
            [
                STAGE_TOPOLOGY,
                "reports/topology_instruction_expansion_v1.2_sd_core.json",
                "reports/topology_instruction_expansion_v1.2_sd_core.md",
            ],
            input_paths=[
                STAGE_FROZEN,
                "simulation_outputs/topology_stress/extended_topology_scenarios.json",
            ],
        ),
        Step(
            "append_equipment_contingency_records",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/append_topology_instruction_records.py",
                "--dataset",
                STAGE_TOPOLOGY,
                "--output-dataset",
                STAGE_EQUIPMENT,
                "--scenarios",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--main-scenarios",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--contingency-types",
                "n1_transformer_outage",
                "n1_generator_outage",
                "n1_bus_outage",
                "--variants-per-scenario",
                "2",
                "--expansion-tag",
                "equipment_contingency_instruction_v1",
                "--id-namespace",
                "equipment1",
                "--coverage-type",
                "equipment",
                "--report-json",
                "reports/equipment_contingency_instruction_expansion_v1.2_sd_core.json",
                "--report-md",
                "reports/equipment_contingency_instruction_expansion_v1.2_sd_core.md",
            ],
            [
                STAGE_EQUIPMENT,
                "reports/equipment_contingency_instruction_expansion_v1.2_sd_core.json",
                "reports/equipment_contingency_instruction_expansion_v1.2_sd_core.md",
            ],
            input_paths=[
                STAGE_TOPOLOGY,
                "simulation_outputs/contingency/scenarios_converged.json",
            ],
        ),
        Step(
            "migrate_dataset_scenario_links",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/migrate_dataset_scenario_links.py",
                "--dataset",
                STAGE_EQUIPMENT,
                "--scenarios",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--output-dataset",
                STAGE_GROUNDED,
                "--report-json",
                "reports/dataset_scenario_link_migration_v1.2_sd_core.json",
                "--report-md",
                "reports/dataset_scenario_link_migration_v1.2_sd_core.md",
            ],
            [
                STAGE_GROUNDED,
                "reports/dataset_scenario_link_migration_v1.2_sd_core.json",
                "reports/dataset_scenario_link_migration_v1.2_sd_core.md",
            ],
            input_paths=[
                STAGE_EQUIPMENT,
                "simulation_outputs/contingency/scenarios_converged.json",
            ],
        ),
        Step(
            "generate_ieee14_opf_secure_candidates",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/generate_opf_secure_candidate_augmentation.py",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--target-passed-candidates",
                "120",
                "--min-passed-load-levels",
                "3",
                "--min-passed-generator-factors",
                "5",
                "--min-passed-line-pairs",
                "1",
                "--post-action-n1-checks",
                "5",
                "--post-action-generator-n1-checks",
                "2",
                "--output-scenarios",
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "--output-report",
                "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
            ],
            [
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
            ],
            heavy=True,
            input_paths=[
                "metadata/pglib_opf_v23_07_case_registry.json",
                "third_party/pglib-opf-v23.07/LICENSE",
                "scripts/generate_opf_secure_candidate_augmentation.py",
                "scripts/append_opf_closed_loop_auxiliary_records.py",
                "scripts/generate_grid_scenarios.py",
            ],
        ),
        Step(
            "append_realistic_opf_closed_loop",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/append_opf_closed_loop_auxiliary_records.py",
                "--dataset",
                STAGE_GROUNDED,
                "--output-dataset",
                STAGE_OPF,
                "--scenarios",
                OPF_SOURCE_SCENARIOS,
                "--additional-scenarios",
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "--systems",
                "ieee14",
                "ieee118",
                "--contingency-types",
                "base_power_flow",
                "generator_perturbation",
                "n1_branch_outage",
                "n2_branch_outage",
                "--variants-per-scenario",
                "2",
                "--target-scenarios-per-system",
                "80",
                "--post-action-n1-checks",
                "5",
                "--post-action-generator-n1-checks",
                "2",
                "--loading-margins",
                "90",
                "80",
                "70",
                "60",
                "--min-relative-reduction",
                "1.0",
            ],
            [
                STAGE_OPF,
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
                "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.json",
                "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.md",
            ],
            heavy=True,
            input_paths=[
                STAGE_GROUNDED,
                OPF_SOURCE_SCENARIOS,
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
            ],
        ),
        Step(
            "validate_cross_solver_power_flow",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/validate_cross_solver_power_flow.py",
                "--scenarios",
                OPF_SOURCE_SCENARIOS,
                "--additional-scenarios",
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "--opf-results",
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "--opf-commit",
                "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--output-json",
                "reports/cross_solver_power_flow_v1.2_sd_core.json",
                "--output-md",
                "reports/cross_solver_power_flow_v1.2_sd_core.md",
            ],
            [
                "reports/cross_solver_power_flow_v1.2_sd_core.json",
                "reports/cross_solver_power_flow_v1.2_sd_core.md",
            ],
            heavy=True,
            input_paths=[
                OPF_SOURCE_SCENARIOS,
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
                "metadata/pglib_opf_v23_07_case_registry.json",
                "third_party/pglib-opf-v23.07/*.m",
            ],
        ),
        Step(
            "build_native_independent_solver_case_manifest",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/build_independent_solver_case_manifest.py",
                "--scenarios",
                OPF_SOURCE_SCENARIOS,
                "--additional-scenarios",
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "--opf-results",
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--output",
                "simulation_outputs/independent_solver/case_manifest_v1.json",
            ],
            ["simulation_outputs/independent_solver/case_manifest_v1.json"],
            input_paths=[
                OPF_SOURCE_SCENARIOS,
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "metadata/pglib_opf_v23_07_case_registry.json",
            ],
        ),
        Step(
            "run_lightsim_native_independent_solver",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/run_lightsim_native_independent_solver.py",
                "--case-manifest",
                "simulation_outputs/independent_solver/case_manifest_v1.json",
                "--pglib-root",
                "third_party/pglib-opf-v23.07",
                "--output",
                "reports/independent_solver_raw_evidence_v1.2_sd_core.json",
            ],
            ["reports/independent_solver_raw_evidence_v1.2_sd_core.json"],
            heavy=True,
            input_paths=[
                "simulation_outputs/independent_solver/case_manifest_v1.json",
                "metadata/pglib_opf_v23_07_case_registry.json",
            ],
        ),
        Step(
            "validate_lightsim_native_independent_solver",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/validate_independent_solver_evidence.py",
                "--input",
                "reports/independent_solver_raw_evidence_v1.2_sd_core.json",
                "--case-manifest",
                "simulation_outputs/independent_solver/case_manifest_v1.json",
                "--output",
                "reports/independent_solver_validation_v1.2_sd_core.json",
            ],
            ["reports/independent_solver_validation_v1.2_sd_core.json"],
            input_paths=[
                "reports/independent_solver_raw_evidence_v1.2_sd_core.json",
                "simulation_outputs/independent_solver/case_manifest_v1.json",
            ],
        ),
        Step(
            "validate_opf_action_uncertainty_stress",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/validate_opf_action_uncertainty_stress.py",
                "--opf-results",
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "--opf-commit",
                "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
                "--scenarios",
                OPF_SOURCE_SCENARIOS,
                "--additional-scenarios",
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "--case-results-json",
                "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json",
                "--report-json",
                "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
                "--commit-json",
                "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_commit_v1.json",
            ],
            [
                "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json",
                "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
                "simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_commit_v1.json",
            ],
            heavy=True,
            input_paths=[
                OPF_SOURCE_SCENARIOS,
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
            ],
        ),
        Step(
            "validate_opf_action_constant_power_factor_stress",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/validate_opf_action_constant_power_factor_stress.py",
                "--opf-results",
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "--opf-commit",
                "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
                "--scenarios",
                OPF_SOURCE_SCENARIOS,
                "--additional-scenarios",
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "--case-results-json",
                (
                    "simulation_outputs/opf_closed_loop/"
                    "opf_action_constant_power_factor_stress_cases_v1.json"
                ),
                "--report-json",
                (
                    "reports/"
                    "opf_action_constant_power_factor_stress_v1.2_sd_core.json"
                ),
                "--commit-json",
                (
                    "simulation_outputs/opf_closed_loop/"
                    "opf_action_constant_power_factor_stress_commit_v1.json"
                ),
            ],
            [
                (
                    "simulation_outputs/opf_closed_loop/"
                    "opf_action_constant_power_factor_stress_cases_v1.json"
                ),
                (
                    "reports/"
                    "opf_action_constant_power_factor_stress_v1.2_sd_core.json"
                ),
                (
                    "simulation_outputs/opf_closed_loop/"
                    "opf_action_constant_power_factor_stress_commit_v1.json"
                ),
            ],
            heavy=True,
            input_paths=[
                OPF_SOURCE_SCENARIOS,
                "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
                "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
                "scripts/validate_opf_action_uncertainty_stress.py",
            ],
        ),
        Step(
            "repair_query_result_consistency",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/repair_query_result_consistency.py",
                "--dataset",
                STAGE_OPF,
                "--output-dataset",
                STAGE_QUERY,
                "--output-json",
                "reports/query_result_consistency_repair_v1.2_sd_core.json",
                "--output-md",
                "reports/query_result_consistency_repair_v1.2_sd_core.md",
            ],
            [
                STAGE_QUERY,
                "reports/query_result_consistency_repair_v1.2_sd_core.json",
                "reports/query_result_consistency_repair_v1.2_sd_core.md",
            ],
            input_paths=[
                STAGE_OPF,
                "simulation_outputs/contingency/scenarios_converged.json",
            ],
        ),
        Step(
            "validate_query_truth_independent",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/validate_query_truth_independent.py",
                "--dataset",
                STAGE_QUERY,
                "--scenarios",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--output-json",
                "reports/independent_query_truth_validation_v1.2_sd_core.json",
                "--output-md",
                "reports/independent_query_truth_validation_v1.2_sd_core.md",
            ],
            [
                "reports/independent_query_truth_validation_v1.2_sd_core.json",
                "reports/independent_query_truth_validation_v1.2_sd_core.md",
            ],
            heavy=True,
            input_paths=[
                STAGE_QUERY,
                "simulation_outputs/contingency/scenarios_converged.json",
            ],
        ),
        Step(
            "repair_operation_ticket_state_consistency",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/repair_operation_ticket_state_consistency.py",
                "--dataset",
                STAGE_QUERY,
                "--output-dataset",
                STAGE_OPERATION,
                "--report-json",
                "reports/operation_ticket_state_consistency_v1.2_sd_core.json",
                "--report-md",
                "reports/operation_ticket_state_consistency_v1.2_sd_core.md",
            ],
            [
                STAGE_OPERATION,
                "reports/operation_ticket_state_consistency_v1.2_sd_core.json",
                "reports/operation_ticket_state_consistency_v1.2_sd_core.md",
            ],
            input_paths=[STAGE_QUERY],
        ),
        Step(
            "expand_rule_taxonomy_and_links",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/expand_rule_taxonomy_and_links.py",
                "--canonical-dataset",
                STAGE_OPERATION,
                "--output-canonical-dataset",
                STAGE_RULE,
                "--rules",
                "rules/regulation_rules.json",
                "--report-json",
                "reports/rule_taxonomy_expansion_v1.2_sd_core.json",
                "--report-md",
                "reports/rule_taxonomy_expansion_v1.2_sd_core.md",
            ],
            [
                STAGE_RULE,
                "rules/regulation_rules.json",
                "metadata/rule_clause_matrix.csv",
                "metadata/rule_clause_matrix.json",
                "reports/rule_taxonomy_expansion_v1.2_sd_core.json",
                "reports/rule_taxonomy_expansion_v1.2_sd_core.md",
            ],
            input_paths=[STAGE_OPERATION],
        ),
        Step(
            "recompute_compliance_labels_before_promotion",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/recompute_compliance_labels_from_truth.py",
                "--dataset",
                STAGE_RULE,
                "--scenarios",
                "simulation_outputs/contingency/scenarios_converged.json",
                "--output-dataset",
                STAGE_COMPLIANCE,
                "--report-json",
                "reports/compliance_label_recompute_v1.2_sd_core.json",
            ],
            [STAGE_COMPLIANCE, "reports/compliance_label_recompute_v1.2_sd_core.json"],
            input_paths=[
                STAGE_RULE,
                "simulation_outputs/contingency/scenarios_converged.json",
            ],
        ),
        Step(
            "promote_canonical_dataset",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/promote_dataset_stage.py",
                "--input",
                STAGE_COMPLIANCE,
                "--output",
                DATASET,
                "--manifest",
                "metadata/canonical_dataset_promotion_manifest.json",
                "--upstream-gate",
                "reports/compliance_label_recompute_v1.2_sd_core.json",
            ],
            [DATASET, "metadata/canonical_dataset_promotion_manifest.json"],
            input_paths=[
                STAGE_COMPLIANCE,
                "reports/compliance_label_recompute_v1.2_sd_core.json",
            ],
        ),
        Step(
            "create_official_splits_seed42",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/create_splits.py",
                "--input",
                DATASET,
                "--seed",
                "42",
                "--train-output",
                TRAIN,
                "--validation-output",
                VALIDATION,
                "--test-output",
                TEST,
                "--ood-output",
                OOD,
                "--evaluation-output",
                EVALUATION,
            ],
            [TRAIN, VALIDATION, TEST, OOD, EVALUATION],
            input_paths=[DATASET],
        ),
        Step(
            "build_english_release_view",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/run_english_translation_pipeline.py",
                "--python",
                PYTHON,
                "--input",
                DATASET,
                "--output",
                ENGLISH_DATASET,
                "--cache",
                "metadata/translation_cache_v1.jsonl",
                "--experiment-dir",
                f"{run_root}/10_english_translation",
            ],
            [
                ENGLISH_DATASET,
                english_split(TRAIN),
                english_split(VALIDATION),
                english_split(TEST),
                english_split(OOD),
                "reports/english_translation_audit_v1.2_sd_core.json",
                "reports/english_split_materialization_v1.2_sd_core.json",
                f"{run_root}/10_english_translation/results/pipeline_status.json",
            ],
            heavy=True,
            input_paths=[
                DATASET,
                TRAIN,
                VALIDATION,
                TEST,
                OOD,
            ],
        ),
        Step(
            "verify_canonical_dataset_immutability",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/promote_dataset_stage.py",
                "--verify-only",
                "--output",
                DATASET,
                "--manifest",
                "metadata/canonical_dataset_promotion_manifest.json",
                "--verification-report",
                "reports/canonical_dataset_immutability_v1.2_sd_core.json",
            ],
            ["reports/canonical_dataset_immutability_v1.2_sd_core.json"],
            input_paths=[DATASET, "metadata/canonical_dataset_promotion_manifest.json"],
        ),
        Step(
            "validate_dataset",
            "01_dataset_integrity",
            [
                PYTHON,
                "scripts/validate_dataset.py",
                "--input",
                DATASET,
                "--report-prefix",
                "reports/data_validation_v1.2_sd_core",
            ],
            [
                "reports/data_validation_v1.2_sd_core.json",
                "reports/data_validation_v1.2_sd_core.md",
            ],
        ),
        Step(
            "audit_distribution_risk",
            "02_data_quality",
            [
                PYTHON,
                "scripts/audit_dataset_distribution_risk.py",
                "--dataset",
                DATASET,
                "--full-pool",
                V16_PLUS15,
                "--train",
                english_split(TRAIN),
                "--validation",
                english_split(VALIDATION),
                "--test",
                english_split(TEST),
                "--ood",
                english_split(OOD),
                "--output-json",
                "reports/sd_core_distribution_risk_audit.json",
                "--output-md",
                "reports/sd_core_distribution_risk_audit.md",
                "--figure-dir",
                "figures/sd_core_quality",
            ],
            [
                "reports/sd_core_distribution_risk_audit.json",
                "reports/sd_core_distribution_risk_audit.md",
                "figures/sd_core_quality/figure_manifest.json",
            ],
        ),
        Step(
            "analyze_data_quality",
            "02_data_quality",
            [
                PYTHON,
                "scripts/analyze_data_quality.py",
                "--dataset",
                DATASET,
                "--train",
                TRAIN,
                "--validation",
                VALIDATION,
                "--test",
                TEST,
                "--ood",
                OOD,
                "--output-json",
                "reports/data_quality_optimization_v1.2_sd_core.json",
                "--output-md",
                "reports/data_quality_optimization_v1.2_sd_core.md",
            ],
            [
                "reports/data_quality_optimization_v1.2_sd_core.json",
                "reports/data_quality_optimization_v1.2_sd_core.md",
            ],
        ),
        Step(
            "audit_label_split_support",
            "02_data_quality",
            [
                PYTHON,
                "scripts/audit_label_split_support.py",
                "--dataset",
                DATASET,
                "--metadata",
                "metadata/dataset_metadata.json",
                "--output-json",
                "reports/label_split_support_audit_v1.2_sd_core.json",
                "--output-md",
                "reports/label_split_support_audit_v1.2_sd_core.md",
                "--task-split-csv",
                "reports/label_split_support_task_by_split_v1.2_sd_core.csv",
                "--network-split-csv",
                "reports/label_split_support_network_by_split_v1.2_sd_core.csv",
                "--severity-split-csv",
                "reports/label_split_support_severity_by_split_v1.2_sd_core.csv",
                "--figure-dir",
                "figures/sd_core",
            ],
            [
                "reports/label_split_support_audit_v1.2_sd_core.json",
                "reports/label_split_support_audit_v1.2_sd_core.md",
                "reports/label_split_support_task_by_split_v1.2_sd_core.csv",
                "reports/label_split_support_network_by_split_v1.2_sd_core.csv",
                "reports/label_split_support_severity_by_split_v1.2_sd_core.csv",
                "figures/sd_core/fig_label_split_support.png",
                "figures/sd_core/fig_split_task_network_coverage.png",
            ],
        ),
        Step(
            "build_source_group_map",
            "02_data_quality",
            [
                PYTHON,
                "scripts/build_source_group_map.py",
                "--dataset",
                DATASET,
                "--output-csv",
                "metadata/source_group_map.csv",
                "--output-json",
                "reports/source_group_map_audit_v1.2_sd_core.json",
            ],
            [
                "metadata/source_group_map.csv",
                "reports/source_group_map_audit_v1.2_sd_core.json",
            ],
        ),
        Step(
            "create_strict_source_group_splits",
            "02_data_quality",
            [
                PYTHON,
                "scripts/create_strict_source_group_splits.py",
                "--input",
                DATASET,
                "--train-output",
                STRICT_TRAIN,
                "--validation-output",
                STRICT_VALIDATION,
                "--test-output",
                STRICT_TEST,
                "--report-json",
                "reports/strict_source_group_split_v1.2_sd_core.json",
                "--report-md",
                "reports/strict_source_group_split_v1.2_sd_core.md",
                "--figure-dir",
                "figures/sd_core",
            ],
            [
                STRICT_TRAIN,
                STRICT_VALIDATION,
                STRICT_TEST,
                "reports/strict_source_group_split_v1.2_sd_core.json",
                "reports/strict_source_group_split_v1.2_sd_core.md",
                "figures/sd_core/fig_strict_split_group_overlap.png",
            ],
        ),
        materialize_english_step("materialize_english_after_strict_split"),
        Step(
            "validate_source_english_core_alignment",
            "02_data_quality",
            [
                PYTHON,
                "scripts/validate_source_english_alignment.py",
                "--split",
                TRAIN,
                "--split",
                VALIDATION,
                "--split",
                TEST,
                "--split",
                OOD,
                "--split",
                STRICT_TRAIN,
                "--split",
                STRICT_VALIDATION,
                "--split",
                STRICT_TEST,
                "--report-json",
                "reports/source_english_core_alignment_v1.2_sd_core.json",
                "--report-md",
                "reports/source_english_core_alignment_v1.2_sd_core.md",
            ],
            [
                "reports/source_english_core_alignment_v1.2_sd_core.json",
                "reports/source_english_core_alignment_v1.2_sd_core.md",
            ],
            input_paths=[DATASET, ENGLISH_DATASET, "data/v1.2_sd_core*.jsonl"],
        ),
        Step(
            "audit_near_duplicates",
            "02_data_quality",
            [
                PYTHON,
                "scripts/audit_near_duplicates.py",
                "--semantic-model",
                "sentence-transformers/all-MiniLM-L6-v2",
                "--semantic-model-revision",
                "c9745ed1d9f207416be6d2e6f8de32d1f16199bf",
                "--semantic-model-local-path",
                "models/sentence-transformers/all-MiniLM-L6-v2/c9745ed1d9f207416be6d2e6f8de32d1f16199bf",
                "--semantic-fallback",
                "deterministic_tfidf",
                "--output",
                "reports/near_duplicate_audit_v1.2_sd_core.json",
            ],
            ["reports/near_duplicate_audit_v1.2_sd_core.json"],
            heavy=True,
            input_paths=[
                "data/gridinstruct_v1.2_sd_core_en.jsonl",
                english_split(TRAIN),
                english_split(TEST),
                english_split(OOD),
                english_split(STRICT_TEST),
            ],
        ),
        Step(
            "audit_generation_grounding",
            "02_data_quality",
            [
                PYTHON,
                "scripts/audit_generation_grounding.py",
                "--dataset",
                DATASET,
                "--output-json",
                "reports/generation_grounding_audit_v1.2_sd_core.json",
                "--output-md",
                "reports/generation_grounding_audit_v1.2_sd_core.md",
                "--figure-dir",
                "figures/sd_core_grounding",
            ],
            [
                "reports/generation_grounding_audit_v1.2_sd_core.json",
                "reports/generation_grounding_audit_v1.2_sd_core.md",
                "figures/sd_core_grounding/grounding_gate_matrix.png",
            ],
        ),
        Step(
            "sample_stratified_expert_review",
            "02_data_quality",
            [
                PYTHON,
                "scripts/sample_stratified_expert_review.py",
                "--dataset",
                DATASET,
                "--train",
                TRAIN,
                "--validation",
                VALIDATION,
                "--test",
                TEST,
                "--ood",
                OOD,
                "--output-dir",
                "review_packages/stratified_expert_review_v1.2_sd_core",
                "--report-json",
                "reports/expert_review_package_v1.2_sd_core.json",
                "--report-md",
                "reports/expert_review_package_v1.2_sd_core.md",
            ],
            [
                "review_packages/stratified_expert_review_v1.2_sd_core/sample_manifest.csv",
                "review_packages/stratified_expert_review_v1.2_sd_core/review_packet_blinded.jsonl",
                "review_packages/stratified_expert_review_v1.2_sd_core/human_review_log_template.csv",
                "reports/expert_review_package_v1.2_sd_core.json",
                "reports/expert_review_package_v1.2_sd_core.md",
            ],
        ),
        Step(
            "audit_expert_review_package_readiness",
            "02_data_quality",
            [
                PYTHON,
                "scripts/audit_expert_review_package_readiness.py",
                "--package-dir",
                "review_packages/stratified_expert_review_v1.2_sd_core",
                "--output-json",
                "reports/expert_review_readiness_audit_v1.2_sd_core.json",
                "--output-md",
                "reports/expert_review_readiness_audit_v1.2_sd_core.md",
                "--figure-dir",
                "figures/sd_core",
            ],
            [
                "reports/expert_review_readiness_audit_v1.2_sd_core.json",
                "reports/expert_review_readiness_audit_v1.2_sd_core.md",
                "figures/sd_core/fig_expert_review_sample_coverage.png",
                "figures/sd_core/fig_expert_review_label_family_coverage.png",
            ],
        ),
        Step(
            "audit_expert_review_execution",
            "02_data_quality",
            [
                PYTHON,
                "scripts/audit_expert_review_execution.py",
                "--package-dir",
                "review_packages/stratified_expert_review_v1.2_sd_core",
                "--output-json",
                "reports/expert_review_execution_check_v1.2_sd_core.json",
                "--output-md",
                "reports/expert_review_execution_check_v1.2_sd_core.md",
            ],
            [
                "reports/expert_review_execution_check_v1.2_sd_core.json",
                "reports/expert_review_execution_check_v1.2_sd_core.md",
            ],
        ),
        Step(
            "run_tfidf",
            "03_tfidf_baseline",
            [
                PYTHON,
                "scripts/run_tfidf_task_baselines.py",
                "--train",
                english_split(TRAIN),
                "--validation",
                english_split(VALIDATION),
                "--test",
                english_split(TEST),
                "--ood",
                english_split(OOD),
                "--output-prefix",
                "benchmark/v1.2_sd_core_tfidf",
            ],
            [
                "benchmark/v1.2_sd_core_tfidf_report.json",
                "benchmark/v1.2_sd_core_tfidf_validation_predictions.jsonl",
                "benchmark/v1.2_sd_core_tfidf_test_predictions.jsonl",
                "benchmark/v1.2_sd_core_tfidf_ood_predictions.jsonl",
            ],
        ),
        Step(
            "evaluate_ood_strata",
            "03_tfidf_baseline",
            [
                PYTHON,
                "scripts/evaluate_ood_strata.py",
                "--ood",
                english_split(OOD),
                "--predictions",
                "benchmark/v1.2_sd_core_tfidf_ood_predictions.jsonl",
                "--output",
                "reports/ood_stratified_metrics_v1.2_sd_core.json",
            ],
            ["reports/ood_stratified_metrics_v1.2_sd_core.json"],
        ),
        Step(
            "run_strict_tfidf",
            "03_tfidf_baseline",
            [
                PYTHON,
                "scripts/run_tfidf_task_baselines.py",
                "--train",
                english_split(STRICT_TRAIN),
                "--validation",
                english_split(STRICT_VALIDATION),
                "--test",
                english_split(STRICT_TEST),
                "--ood",
                "",
                "--output-prefix",
                "benchmark/v1.2_sd_core_strict_tfidf",
            ],
            [
                "benchmark/v1.2_sd_core_strict_tfidf_report.json",
                "benchmark/v1.2_sd_core_strict_tfidf_validation_predictions.jsonl",
                "benchmark/v1.2_sd_core_strict_tfidf_test_predictions.jsonl",
            ],
        ),
        Step(
            "create_classification_challenge_splits",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/create_classification_challenge_splits.py",
                "--input",
                DATASET,
                "--train-output",
                challenge_train,
                "--validation-output",
                challenge_validation,
                "--test-output",
                challenge_test,
                "--report-json",
                "benchmark/v1.2_sd_core_classification_challenge_splits.json",
                "--seed",
                "2037",
            ],
            [
                challenge_train,
                challenge_validation,
                challenge_test,
                "benchmark/v1.2_sd_core_classification_challenge_splits.json",
            ],
        ),
        materialize_english_step("materialize_english_after_challenge_split"),
        Step(
            "audit_split_independence",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/audit_split_independence.py",
                "--train",
                TRAIN,
                "--validation",
                VALIDATION,
                "--test",
                TEST,
                "--ood",
                OOD,
                "--challenge-train",
                challenge_train,
                "--challenge-validation",
                challenge_validation,
                "--challenge-test",
                challenge_test,
                "--output-json",
                "reports/split_independence_audit_v1.2_sd_core.json",
                "--output-md",
                "reports/split_independence_audit_v1.2_sd_core.md",
                "--figure-dir",
                "figures/sd_core",
            ],
            [
                "reports/split_independence_audit_v1.2_sd_core.json",
                "reports/split_independence_audit_v1.2_sd_core.md",
                "figures/sd_core/fig_split_independence_overlap.png",
            ],
        ),
        Step(
            "create_proxy_reduced_classification_splits",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/create_proxy_reduced_classification_splits.py",
                "--output-json",
                "reports/proxy_reduced_split_manifest_v1.2_sd_core.json",
            ],
            [
                "reports/proxy_reduced_split_manifest_v1.2_sd_core.json",
                "data/v1.2_sd_core_proxyreduced_train.jsonl",
                "data/v1.2_sd_core_challenge_proxyreduced_train.jsonl",
                "data/v1.2_sd_core_strict_proxyreduced_train.jsonl",
            ],
        ),
        materialize_english_step("materialize_english_after_proxy_reduced_splits"),
        Step(
            "run_proxyreduced_tfidf",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/run_tfidf_task_baselines.py",
                "--train",
                "data/v1.2_sd_core_proxyreduced_train_en.jsonl",
                "--validation",
                "data/v1.2_sd_core_proxyreduced_validation_en.jsonl",
                "--test",
                "data/v1.2_sd_core_proxyreduced_test_en.jsonl",
                "--ood",
                "",
                "--output-prefix",
                "benchmark/v1.2_sd_core_proxyreduced_tfidf",
            ],
            [
                "benchmark/v1.2_sd_core_proxyreduced_tfidf_report.json",
                "benchmark/v1.2_sd_core_proxyreduced_tfidf_validation_predictions.jsonl",
                "benchmark/v1.2_sd_core_proxyreduced_tfidf_test_predictions.jsonl",
            ],
        ),
        Step(
            "run_challenge_proxyreduced_tfidf",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/run_tfidf_task_baselines.py",
                "--train",
                "data/v1.2_sd_core_challenge_proxyreduced_train_en.jsonl",
                "--validation",
                "data/v1.2_sd_core_challenge_proxyreduced_validation_en.jsonl",
                "--test",
                "data/v1.2_sd_core_challenge_proxyreduced_test_en.jsonl",
                "--ood",
                "",
                "--output-prefix",
                "benchmark/v1.2_sd_core_challenge_proxyreduced_tfidf",
            ],
            [
                "benchmark/v1.2_sd_core_challenge_proxyreduced_tfidf_report.json",
                "benchmark/v1.2_sd_core_challenge_proxyreduced_tfidf_validation_predictions.jsonl",
                "benchmark/v1.2_sd_core_challenge_proxyreduced_tfidf_test_predictions.jsonl",
            ],
        ),
        Step(
            "run_strict_proxyreduced_tfidf",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/run_tfidf_task_baselines.py",
                "--train",
                "data/v1.2_sd_core_strict_proxyreduced_train_en.jsonl",
                "--validation",
                "data/v1.2_sd_core_strict_proxyreduced_validation_en.jsonl",
                "--test",
                "data/v1.2_sd_core_strict_proxyreduced_test_en.jsonl",
                "--ood",
                "",
                "--output-prefix",
                "benchmark/v1.2_sd_core_strict_proxyreduced_tfidf",
            ],
            [
                "benchmark/v1.2_sd_core_strict_proxyreduced_tfidf_report.json",
                "benchmark/v1.2_sd_core_strict_proxyreduced_tfidf_validation_predictions.jsonl",
                "benchmark/v1.2_sd_core_strict_proxyreduced_tfidf_test_predictions.jsonl",
            ],
        ),
        Step(
            "run_challenge_tfidf",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/run_tfidf_task_baselines.py",
                "--train",
                english_split(challenge_train),
                "--validation",
                english_split(challenge_validation),
                "--test",
                english_split(challenge_test),
                "--ood",
                "",
                "--output-prefix",
                "benchmark/v1.2_sd_core_challenge_tfidf",
            ],
            [
                "benchmark/v1.2_sd_core_challenge_tfidf_report.json",
                "benchmark/v1.2_sd_core_challenge_tfidf_validation_predictions.jsonl",
                "benchmark/v1.2_sd_core_challenge_tfidf_test_predictions.jsonl",
            ],
        ),
        Step(
            "create_template_holdout_splits",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/create_template_holdout_splits.py",
                "--input",
                DATASET,
                "--train-output",
                TEMPLATE_HOLDOUT_TRAIN,
                "--validation-output",
                TEMPLATE_HOLDOUT_VALIDATION,
                "--test-output",
                TEMPLATE_HOLDOUT_TEST,
                "--report-json",
                "reports/template_holdout_split_v1.2_sd_core.json",
                "--report-md",
                "reports/template_holdout_split_v1.2_sd_core.md",
                "--figure-dir",
                "figures/sd_core",
            ],
            [
                TEMPLATE_HOLDOUT_TRAIN,
                TEMPLATE_HOLDOUT_VALIDATION,
                TEMPLATE_HOLDOUT_TEST,
                "reports/template_holdout_split_v1.2_sd_core.json",
                "reports/template_holdout_split_v1.2_sd_core.md",
                "figures/sd_core/fig_template_holdout_train_records.png",
                "figures/sd_core/fig_template_holdout_validation_records.png",
                "figures/sd_core/fig_template_holdout_test_records.png",
            ],
        ),
        materialize_english_step("materialize_english_after_template_holdout_split"),
        Step(
            "validate_source_english_all_split_alignment",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/validate_source_english_alignment.py",
                "--report-json",
                "reports/source_english_alignment_v1.2_sd_core.json",
                "--report-md",
                "reports/source_english_alignment_v1.2_sd_core.md",
            ],
            [
                "reports/source_english_alignment_v1.2_sd_core.json",
                "reports/source_english_alignment_v1.2_sd_core.md",
            ],
            input_paths=[DATASET, ENGLISH_DATASET, "data/v1.2_sd_core*.jsonl"],
        ),
        Step(
            "run_template_holdout_tfidf",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/run_tfidf_task_baselines.py",
                "--train",
                english_split(TEMPLATE_HOLDOUT_TRAIN),
                "--validation",
                english_split(TEMPLATE_HOLDOUT_VALIDATION),
                "--test",
                english_split(TEMPLATE_HOLDOUT_TEST),
                "--ood",
                "",
                "--output-prefix",
                "benchmark/v1.2_sd_core_template_holdout_tfidf",
            ],
            [
                "benchmark/v1.2_sd_core_template_holdout_tfidf_report.json",
                "benchmark/v1.2_sd_core_template_holdout_tfidf_validation_predictions.jsonl",
                "benchmark/v1.2_sd_core_template_holdout_tfidf_test_predictions.jsonl",
            ],
        ),
        Step(
            "run_rule_context_tfidf",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/run_rule_context_tfidf_baseline.py",
                "--train",
                english_split(TRAIN),
                "--validation",
                english_split(VALIDATION),
                "--test",
                english_split(TEST),
                "--rules",
                "rules/regulation_rules.json",
                "--output-prefix",
                "benchmark/v1.2_sd_core_rule_context_tfidf",
                "--figure-dir",
                "figures/sd_core",
            ],
            [
                "benchmark/v1.2_sd_core_rule_context_tfidf_report.json",
                "benchmark/v1.2_sd_core_rule_context_tfidf_report.md",
                "benchmark/v1.2_sd_core_rule_context_tfidf_predictions.jsonl",
                "figures/sd_core/fig_rule_context_tfidf_macro_f1.png",
            ],
        ),
        Step(
            "run_shortcut_ablation_audit",
            "04_classification_challenge",
            [
                PYTHON,
                "scripts/run_shortcut_ablation_audit.py",
                "--train",
                english_split(TRAIN),
                "--test",
                english_split(TEST),
                "--challenge-train",
                english_split(challenge_train),
                "--challenge-test",
                english_split(challenge_test),
                "--output-json",
                "reports/shortcut_ablation_v1.2_sd_core.json",
                "--output-md",
                "reports/shortcut_ablation_v1.2_sd_core.md",
                "--output-csv",
                "benchmark/v1.2_sd_core_shortcut_ablation_results.csv",
                "--figure-dir",
                "figures/sd_core",
            ],
            [
                "reports/shortcut_ablation_v1.2_sd_core.json",
                "reports/shortcut_ablation_v1.2_sd_core.md",
                "benchmark/v1.2_sd_core_shortcut_ablation_results.csv",
                "figures/sd_core/fig_shortcut_ablation_standard.png",
                "figures/sd_core/fig_shortcut_ablation_challenge.png",
                "figures/sd_core/fig_shortcut_ablation_delta.png",
            ],
        ),
        Step(
            "create_proxy_stress_splits",
            "04_proxy_stress",
            [
                PYTHON,
                "scripts/create_proxy_stress_splits.py",
                "--dataset",
                DATASET,
                "--tasks",
                "regulation_compliance_check",
                "operation_ticket_check",
                "dispatcher_intent_tool_call",
            ],
            [
                "reports/proxy_stress_split_manifest_v1.2_sd_core.json",
                "reports/proxy_stress_split_manifest_v1.2_sd_core.md",
                *proxy_stress_source_splits,
            ],
            input_paths=[DATASET],
        ),
        Step(
            "materialize_english_after_proxy_stress_splits",
            "04_proxy_stress",
            [
                PYTHON,
                "scripts/materialize_english_splits.py",
                "--translated-full",
                ENGLISH_DATASET,
                *[
                    token
                    for path in proxy_stress_source_splits
                    for token in ("--split", path)
                ],
                "--report-json",
                "reports/proxy_stress_english_materialization_v1.2_sd_core.json",
                "--report-md",
                "reports/proxy_stress_english_materialization_v1.2_sd_core.md",
            ],
            [
                *proxy_stress_english_splits,
                "reports/proxy_stress_english_materialization_v1.2_sd_core.json",
                "reports/proxy_stress_english_materialization_v1.2_sd_core.md",
            ],
            input_paths=[ENGLISH_DATASET, *proxy_stress_source_splits],
        ),
        Step(
            "validate_source_english_proxy_stress_alignment",
            "04_proxy_stress",
            [
                PYTHON,
                "scripts/validate_source_english_alignment.py",
                *[
                    token
                    for path in proxy_stress_source_splits
                    for token in ("--split", path)
                ],
                "--report-json",
                "reports/source_english_proxy_stress_alignment_v1.2_sd_core.json",
                "--report-md",
                "reports/source_english_proxy_stress_alignment_v1.2_sd_core.md",
            ],
            [
                "reports/source_english_proxy_stress_alignment_v1.2_sd_core.json",
                "reports/source_english_proxy_stress_alignment_v1.2_sd_core.md",
            ],
            input_paths=[
                DATASET,
                ENGLISH_DATASET,
                *proxy_stress_source_splits,
                *proxy_stress_english_splits,
            ],
        ),
        Step(
            "run_proxy_stress_tfidf",
            "04_proxy_stress",
            [
                PYTHON,
                "scripts/run_proxy_stress_baselines.py",
                "--canonical-dataset",
                DATASET,
                "--english-dataset",
                ENGLISH_DATASET,
                "--split-manifest",
                "reports/proxy_stress_split_manifest_v1.2_sd_core.json",
                "--split-language",
                "en",
                "--output-json",
                "benchmark/v1.2_sd_core_proxy_stress_report.json",
                "--output-md",
                "benchmark/v1.2_sd_core_proxy_stress_report.md",
                "--figure-dir",
                "figures/sd_core_proxy_stress",
            ],
            [
                "benchmark/v1.2_sd_core_proxy_stress_report.json",
                "benchmark/v1.2_sd_core_proxy_stress_report.md",
                "figures/sd_core_proxy_stress/proxy_stress_scores.png",
            ],
            input_paths=[
                DATASET,
                ENGLISH_DATASET,
                "reports/proxy_stress_split_manifest_v1.2_sd_core.json",
                "benchmark/v1.2_sd_core_tfidf_report.json",
                *proxy_stress_source_splits,
                *proxy_stress_english_splits,
            ],
        ),
    ]

    for task in (
        "operation_ticket_check",
        "regulation_compliance_check",
        "dispatcher_intent_tool_call",
    ):
        if task == "regulation_compliance_check":
            experiment = "experiments/04_transformer_classification/sd_core_standard_full_no_w/regulation_compliance_check"
            standard_epochs = 12
            standard_seed = 13
            standard_learning_rate = 2e-5
            standard_input_profile = "full"
            standard_no_class_weights = True
        else:
            experiment = f"experiments/04_transformer_classification/sd_core_standard_natural/{task}"
            standard_epochs = 8
            standard_seed = 42
            standard_learning_rate = 5e-5
            standard_input_profile = "natural"
            standard_no_class_weights = False
        steps.append(
            transformer_step(
                f"transformer_standard_{task}",
                experiment,
                task,
                TRAIN,
                VALIDATION,
                TEST,
                f"benchmark/v1.2_sd_core_transformer_{task}",
                epochs=standard_epochs,
                seed=standard_seed,
                learning_rate=standard_learning_rate,
                input_profile=standard_input_profile,
                no_class_weights=standard_no_class_weights,
            )
        )
        steps.append(
            classification_figure_step(
                f"figures_standard_{task}",
                experiment,
                f"benchmark/v1.2_sd_core_transformer_{task}_report.json",
                f"benchmark/v1.2_sd_core_transformer_{task}_test_predictions.jsonl",
                f"SD Core Standard {task}",
            )
        )

    challenge_epochs = {
        "operation_ticket_check": 6,
        "regulation_compliance_check": 20,
        "dispatcher_intent_tool_call": 6,
    }
    for task in (
        "operation_ticket_check",
        "regulation_compliance_check",
        "dispatcher_intent_tool_call",
    ):
        if task == "regulation_compliance_check":
            experiment = "experiments/04_transformer_classification/sd_core_challenge_full/regulation_compliance_check"
        else:
            experiment = f"experiments/04_transformer_classification/sd_core_challenge_natural/{task}"
        steps.append(
            transformer_step(
                f"transformer_challenge_{task}",
                experiment,
                task,
                challenge_train,
                challenge_validation,
                challenge_test,
                f"benchmark/v1.2_sd_core_challenge_transformer_{task}",
                epochs=challenge_epochs[task],
                input_profile="full"
                if task == "regulation_compliance_check"
                else "natural",
            )
        )
        steps.append(
            classification_figure_step(
                f"figures_challenge_{task}",
                experiment,
                f"benchmark/v1.2_sd_core_challenge_transformer_{task}_report.json",
                f"benchmark/v1.2_sd_core_challenge_transformer_{task}_test_predictions.jsonl",
                f"SD Core Challenge {task}",
            )
        )

    tuned_runs = [
        (
            "challenge_tuned_full_seed42_lr5e5_e20_w",
            "experiments/04_transformer_classification/sd_core_challenge_regulation_tuning/full_seed42_lr5e5_e20_w",
            "benchmark/v1.2_sd_core_challenge_tuned_full_seed42_lr5e5_e20_w_transformer_regulation_compliance_check",
            42,
            5e-5,
            "full",
            False,
        ),
        (
            "challenge_tuned_natural_seed13_lr2e5_e20_no_w",
            "experiments/04_transformer_classification/sd_core_challenge_regulation_tuning/natural_seed13_lr2e5_e20_no_w",
            "benchmark/v1.2_sd_core_challenge_tuned_natural_seed13_lr2e5_e20_no_w_transformer_regulation_compliance_check",
            13,
            2e-5,
            "natural",
            True,
        ),
    ]
    for (
        name,
        experiment,
        output_prefix,
        seed,
        learning_rate,
        input_profile,
        no_class_weights,
    ) in tuned_runs:
        steps.append(
            transformer_step(
                f"transformer_{name}",
                experiment,
                "regulation_compliance_check",
                challenge_train,
                challenge_validation,
                challenge_test,
                output_prefix,
                epochs=20,
                seed=seed,
                learning_rate=learning_rate,
                input_profile=input_profile,
                no_class_weights=no_class_weights,
            )
        )
        steps.append(
            classification_figure_step(
                f"figures_{name}",
                experiment,
                f"{output_prefix}_report.json",
                f"{output_prefix}_test_predictions.jsonl",
                f"SD Core {name}",
            )
        )

    proxy_runs = [
        (
            "proxy_stress_natural_no_w_regulation",
            "experiments/04_transformer_classification/proxy_stress_regulation_natural_no_w",
            "benchmark/v1.2_sd_core_transformer_proxy_stress_natural_no_w_regulation_compliance_check",
            "natural",
            True,
            12,
        ),
        (
            "proxy_stress_full_no_w_regulation",
            "experiments/04_transformer_classification/proxy_stress_regulation_full_no_w",
            "benchmark/v1.2_sd_core_transformer_proxy_stress_full_no_w_regulation_compliance_check",
            "full",
            True,
            12,
        ),
        (
            "proxy_stress_full_w_regulation",
            "experiments/04_transformer_classification/proxy_stress_regulation_full_w",
            "benchmark/v1.2_sd_core_transformer_proxy_stress_full_w_regulation_compliance_check",
            "full",
            False,
            8,
        ),
    ]
    for (
        name,
        experiment,
        output_prefix,
        input_profile,
        no_class_weights,
        epochs,
    ) in proxy_runs:
        steps.append(
            transformer_step(
                f"transformer_{name}",
                experiment,
                "regulation_compliance_check",
                "data/proxy_stress/regulation_compliance_check/train.jsonl",
                "data/proxy_stress/regulation_compliance_check/validation.jsonl",
                "data/proxy_stress/regulation_compliance_check/test.jsonl",
                output_prefix,
                epochs=epochs,
                seed=13,
                learning_rate=2e-5,
                input_profile=input_profile,
                no_class_weights=no_class_weights,
            )
        )
        steps.append(
            classification_figure_step(
                f"figures_{name}",
                experiment,
                f"{output_prefix}_report.json",
                f"{output_prefix}_test_predictions.jsonl",
                f"SD Core {name}",
            )
        )

    steps.extend(
        [
            seq2seq_step(
                "seq2seq_regqa_highlr",
                "experiments/05_seq2seq_generation/sd_core_regqa_highlr",
                "regulation_qa",
                "benchmark/v1.2_sd_core_seq2seq_regqa",
                320,
                "full",
                "fielded",
            ),
            seq2seq_step(
                "seq2seq_intelligent_data_query_highlr",
                "experiments/05_seq2seq_generation/sd_core_intelligent_data_query_highlr",
                "intelligent_data_query",
                "benchmark/v1.2_sd_core_seq2seq_intelligent_data_query",
                160,
                "actionable",
                "compact",
            ),
            seq2seq_step(
                "seq2seq_auxiliary_decision_highlr",
                "experiments/05_seq2seq_generation/sd_core_auxiliary_decision_highlr",
                "auxiliary_decision",
                "benchmark/v1.2_sd_core_seq2seq_auxiliary_decision",
                320,
                "full",
                "compact",
            ),
            *[
                multiseed_step(task)
                for task in (
                    "operation_ticket_check",
                    "regulation_compliance_check",
                    "dispatcher_intent_tool_call",
                    "regulation_qa",
                    "intelligent_data_query",
                    "auxiliary_decision",
                )
            ],
            Step(
                "run_group_cluster_bootstrap",
                "05_multiseed",
                [
                    PYTHON,
                    "scripts/run_group_cluster_bootstrap.py",
                    "--summary-glob",
                    "benchmark/multiseed_v1.2_sd_core/*_five_seed_summary.json",
                    "--dataset",
                    "data/gridinstruct_v1.2_sd_core_en.jsonl",
                    "--iterations",
                    "1000",
                    "--seed",
                    "20260710",
                    "--output",
                    "reports/group_cluster_bootstrap_v1.2_sd_core.json",
                ],
                ["reports/group_cluster_bootstrap_v1.2_sd_core.json"],
                input_paths=[
                    "data/gridinstruct_v1.2_sd_core_en.jsonl",
                    "benchmark/multiseed_v1.2_sd_core/*_five_seed_summary.json",
                    "benchmark/multiseed_v1.2_sd_core/*_test_predictions.jsonl",
                ],
            ),
            Step(
                "audit_structured_prediction_normalization",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/audit_structured_prediction_normalization.py",
                    "--output-json",
                    "reports/structured_prediction_normalization_v1.2_sd_core.json",
                    "--output-md",
                    "reports/structured_prediction_normalization_v1.2_sd_core.md",
                ],
                [
                    "reports/structured_prediction_normalization_v1.2_sd_core.json",
                    "reports/structured_prediction_normalization_v1.2_sd_core.md",
                ],
            ),
            Step(
                "audit_generation_structure_coverage",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/audit_generation_structure_coverage.py",
                    "--normalization",
                    "reports/structured_prediction_normalization_v1.2_sd_core.json",
                    "--output-json",
                    "reports/generation_structure_coverage_audit_v1.2_sd_core.json",
                    "--output-md",
                    "reports/generation_structure_coverage_audit_v1.2_sd_core.md",
                    "--applicability-csv",
                    "benchmark/v1.2_sd_core_generation_metric_applicability.csv",
                    "--figure-dir",
                    "figures/sd_core_publication",
                ],
                [
                    "reports/generation_structure_coverage_audit_v1.2_sd_core.json",
                    "reports/generation_structure_coverage_audit_v1.2_sd_core.md",
                    "benchmark/v1.2_sd_core_generation_metric_applicability.csv",
                    "figures/sd_core/fig_generation_structure_coverage.png",
                ],
            ),
            Step(
                "audit_model_score_quality",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/audit_model_score_quality.py",
                    "--dataset",
                    DATASET,
                    "--train",
                    TRAIN,
                    "--test",
                    TEST,
                    "--standard-report-glob",
                    "benchmark/v1.2_sd_core_transformer_*_report.json",
                    "--challenge-report-glob",
                    "benchmark/v1.2_sd_core_challenge*transformer_*_report.json",
                    "--generative-report-glob",
                    "benchmark/v1.2_sd_core_seq2seq_*_report.json",
                    "--output-json",
                    "reports/model_score_quality_audit_v1.2_sd_core.json",
                    "--output-md",
                    "reports/model_score_quality_audit_v1.2_sd_core.md",
                ],
                [
                    "reports/model_score_quality_audit_v1.2_sd_core.json",
                    "reports/model_score_quality_audit_v1.2_sd_core.md",
                ],
            ),
            Step(
                "run_sd_statistical_quality_audit",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/run_sd_statistical_quality_audit.py",
                    "--output-json",
                    "reports/sd_statistical_quality_audit_v1.2_sd_core.json",
                    "--output-md",
                    "reports/sd_statistical_quality_audit_v1.2_sd_core.md",
                    "--figure-dir",
                    "figures/sd_core",
                    "--bootstrap",
                    "300",
                    "--max-bootstrap-size",
                    "5000",
                ],
                [
                    "reports/sd_statistical_quality_audit_v1.2_sd_core.json",
                    "reports/sd_statistical_quality_audit_v1.2_sd_core.md",
                    "figures/sd_core/fig_statistical_quality_intervals.png",
                    "figures/sd_core/fig_statistical_label_support.png",
                ],
            ),
            Step(
                "audit_calibration",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/audit_calibration.py",
                    "--output-json",
                    "reports/calibration_audit_v1.2_sd_core.json",
                    "--output-md",
                    "reports/calibration_audit_v1.2_sd_core.md",
                    "--figure-dir",
                    "figures/sd_core",
                ],
                [
                    "reports/calibration_audit_v1.2_sd_core.json",
                    "reports/calibration_audit_v1.2_sd_core.md",
                    "figures/sd_core/fig_calibration_reliability.png",
                ],
            ),
            Step(
                "run_proxy_reduced_input_audit",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/run_proxy_reduced_input_audit.py",
                    "--standard-train",
                    TRAIN,
                    "--standard-test",
                    TEST,
                    "--strict-train",
                    STRICT_TRAIN,
                    "--strict-test",
                    STRICT_TEST,
                    "--output-json",
                    "reports/proxy_reduced_input_audit_v1.2_sd_core.json",
                    "--output-md",
                    "reports/proxy_reduced_input_audit_v1.2_sd_core.md",
                    "--figure-dir",
                    "figures/sd_core",
                ],
                [
                    "reports/proxy_reduced_input_audit_v1.2_sd_core.json",
                    "reports/proxy_reduced_input_audit_v1.2_sd_core.md",
                    "figures/sd_core/fig_proxy_reduced_input_scores.png",
                ],
            ),
            Step(
                "summarize_strict_split_evidence",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/summarize_strict_split_evidence.py",
                    "--strict-split",
                    "reports/strict_source_group_split_v1.2_sd_core.json",
                    "--standard-tfidf",
                    "benchmark/v1.2_sd_core_tfidf_report.json",
                    "--strict-tfidf",
                    "benchmark/v1.2_sd_core_strict_tfidf_report.json",
                    "--proxy-reduced",
                    "reports/proxy_reduced_input_audit_v1.2_sd_core.json",
                    "--output-json",
                    "reports/strict_split_evidence_v1.2_sd_core.json",
                    "--output-md",
                    "reports/strict_split_evidence_v1.2_sd_core.md",
                    "--figure-dir",
                    "figures/sd_core",
                ],
                [
                    "reports/strict_split_evidence_v1.2_sd_core.json",
                    "reports/strict_split_evidence_v1.2_sd_core.md",
                    "figures/sd_core/fig_strict_split_scores.png",
                ],
            ),
            Step(
                "audit_high_score_plausibility",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/audit_high_score_plausibility.py",
                    "--model-score-quality",
                    "reports/model_score_quality_audit_v1.2_sd_core.json",
                    "--template-tfidf",
                    "benchmark/v1.2_sd_core_template_holdout_tfidf_report.json",
                    "--rule-context-tfidf",
                    "benchmark/v1.2_sd_core_rule_context_tfidf_report.json",
                    "--proxy-reduced-input",
                    "reports/proxy_reduced_input_audit_v1.2_sd_core.json",
                    "--strict-evidence",
                    "reports/strict_split_evidence_v1.2_sd_core.json",
                    "--output-json",
                    "reports/high_score_plausibility_audit_v1.2_sd_core.json",
                    "--output-md",
                    "reports/high_score_plausibility_audit_v1.2_sd_core.md",
                    "--figure-dir",
                    "figures/sd_core",
                ],
                [
                    "reports/high_score_plausibility_audit_v1.2_sd_core.json",
                    "reports/high_score_plausibility_audit_v1.2_sd_core.md",
                    "figures/sd_core/fig_high_score_plausibility_matrix.png",
                ],
            ),
            Step(
                "build_hard_boundary_benchmark",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/build_hard_boundary_benchmark.py",
                    "--dataset",
                    "data/gridinstruct_v1.2_sd_core_en.jsonl",
                    "--output-dir",
                    "data/hard_boundary_v1.2_sd_core",
                    "--report-json",
                    "reports/hard_boundary_benchmark_v1.2_sd_core.json",
                    "--report-md",
                    "reports/hard_boundary_benchmark_v1.2_sd_core.md",
                    "--figure",
                    "figures/sd_core/fig_hard_boundary_benchmark.png",
                ],
                [
                    "data/hard_boundary_v1.2_sd_core/operation_ticket_check_train.jsonl",
                    "data/hard_boundary_v1.2_sd_core/operation_ticket_check_test.jsonl",
                    "data/hard_boundary_v1.2_sd_core/dispatcher_intent_tool_call_train.jsonl",
                    "data/hard_boundary_v1.2_sd_core/dispatcher_intent_tool_call_test.jsonl",
                    "data/hard_boundary_v1.2_sd_core/regulation_compliance_check_train.jsonl",
                    "data/hard_boundary_v1.2_sd_core/regulation_compliance_check_test.jsonl",
                    "reports/hard_boundary_benchmark_v1.2_sd_core.json",
                    "reports/hard_boundary_benchmark_v1.2_sd_core.md",
                    "figures/sd_core/fig_hard_boundary_benchmark.png",
                ],
            ),
            Step(
                "run_linear_seed_stability_audit",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/run_linear_seed_stability_audit.py",
                    "--output-json",
                    "reports/linear_seed_stability_audit_v1.2_sd_core.json",
                    "--output-md",
                    "reports/linear_seed_stability_audit_v1.2_sd_core.md",
                    "--output-csv",
                    "benchmark/v1.2_sd_core_linear_seed_stability_results.csv",
                    "--figure-dir",
                    "figures/sd_core",
                ],
                [
                    "reports/linear_seed_stability_audit_v1.2_sd_core.json",
                    "reports/linear_seed_stability_audit_v1.2_sd_core.md",
                    "benchmark/v1.2_sd_core_linear_seed_stability_results.csv",
                    "figures/sd_core/fig_linear_seed_stability.png",
                ],
            ),
            Step(
                "audit_external_topology_physical_envelope",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/audit_external_topology_physical_envelope.py",
                    "--scenarios",
                    "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                    "--dataset",
                    "data/gridinstruct_v1.2_sd_core_en.jsonl",
                    "--report-json",
                    "reports/external_topology_physical_envelope_v1.2_sd_core.json",
                    "--report-md",
                    "reports/external_topology_physical_envelope_v1.2_sd_core.md",
                    "--figure",
                    "figures/sd_core_publication/fig_external_topology_physical_envelope.png",
                ],
                [
                    "reports/external_topology_physical_envelope_v1.2_sd_core.json",
                    "reports/external_topology_physical_envelope_v1.2_sd_core.md",
                    "figures/sd_core_publication/fig_external_topology_physical_envelope.png",
                ],
            ),
            Step(
                "audit_opf_closed_loop_assumptions",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/audit_opf_closed_loop_assumptions.py",
                    "--dataset",
                    "data/gridinstruct_v1.2_sd_core_en.jsonl",
                    "--report-json",
                    "reports/opf_closed_loop_assumption_audit_v1.2_sd_core.json",
                    "--report-md",
                    "reports/opf_closed_loop_assumption_audit_v1.2_sd_core.md",
                    "--figure",
                    "figures/sd_core_publication/fig_opf_closed_loop_assumption_audit.png",
                ],
                [
                    "reports/opf_closed_loop_assumption_audit_v1.2_sd_core.json",
                    "reports/opf_closed_loop_assumption_audit_v1.2_sd_core.md",
                    "figures/sd_core_publication/fig_opf_closed_loop_assumption_audit.png",
                ],
            ),
            Step(
                "build_preaudit_dataset_metadata",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/build_preaudit_dataset_metadata.py",
                    "--dataset",
                    ENGLISH_DATASET,
                    "--dataset-version",
                    "1.2-sd-core",
                    "--archive-metadata",
                    "metadata/archive_metadata.json",
                    "--output",
                    "reports/preaudit_dataset_metadata_v1.2_sd_core.json",
                ],
                [
                    "reports/preaudit_dataset_metadata_v1.2_sd_core.json",
                    "metadata/archive_metadata.json",
                ],
                input_paths=[ENGLISH_DATASET],
            ),
            Step(
                "run_project_milestone_audit",
                "06_sd_audit",
                [
                    PYTHON,
                    "scripts/run_project_milestone_audit.py",
                    "--dataset",
                    DATASET,
                    "--validation-report",
                    "reports/data_validation_v1.2_sd_core.json",
                    "--data-quality-report",
                    "reports/data_quality_optimization_v1.2_sd_core.json",
                    "--review-report",
                    REVIEW_REPORT,
                    "--evaluation-report",
                    EVALUATION,
                    "--tfidf-report",
                    "benchmark/v1.2_sd_core_tfidf_report.json",
                    "--transformer-report-glob",
                    "benchmark/v1.2_sd_core_transformer_*_report.json",
                    "--generative-report",
                    "benchmark/v1.2_sd_core_seq2seq_regqa_report.json",
                    "--generative-report-glob",
                    "benchmark/v1.2_sd_core_seq2seq_*_report.json",
                    "--structured-normalization-report",
                    "reports/structured_prediction_normalization_v1.2_sd_core.json",
                    "--split-independence-report",
                    "reports/split_independence_audit_v1.2_sd_core.json",
                    "--dataset-metadata",
                    "reports/preaudit_dataset_metadata_v1.2_sd_core.json",
                    "--output-json",
                    "reports/project_milestone_audit_v1.2_sd_core.json",
                    "--output-md",
                    "reports/project_milestone_audit_v1.2_sd_core.md",
                    "--expected-version",
                    "1.2-sd-core",
                    "--min-transformer-macro-f1",
                    "0.50",
                    "--min-generative-token-f1",
                    "0.60",
                ],
                [
                    "reports/project_milestone_audit_v1.2_sd_core.json",
                    "reports/project_milestone_audit_v1.2_sd_core.md",
                ],
            ),
            Step(
                "refresh_release_artifacts",
                "07_release_docs",
                [
                    PYTHON,
                    "scripts/refresh_release_artifacts.py",
                    "--dataset",
                    ENGLISH_DATASET,
                    "--source-dataset",
                    DATASET,
                    "--dataset-version",
                    "1.2-sd-core",
                    "--train",
                    english_split(TRAIN),
                    "--validation",
                    english_split(VALIDATION),
                    "--test",
                    english_split(TEST),
                    "--ood",
                    english_split(OOD),
                    "--evaluation-report",
                    EVALUATION,
                    "--validation-report",
                    "reports/data_validation_v1.2_sd_core.json",
                    "--review-report",
                    REVIEW_REPORT,
                    "--human-review-execution-report",
                    "reports/expert_review_execution_check_v1.2_sd_core.json",
                    "--audit-report",
                    "reports/project_milestone_audit_v1.2_sd_core.json",
                    "--tfidf-report",
                    "benchmark/v1.2_sd_core_tfidf_report.json",
                    "--transformer-report-glob",
                    "benchmark/v1.2_sd_core_transformer_*_report.json",
                    "--generative-report",
                    "benchmark/v1.2_sd_core_seq2seq_regqa_report.json",
                    "--generative-report-glob",
                    "benchmark/v1.2_sd_core_seq2seq_*_report.json",
                    "--external-topology-physical-envelope",
                    "reports/external_topology_physical_envelope_v1.2_sd_core.json",
                    "--opf-closed-loop-assumption",
                    "reports/opf_closed_loop_assumption_audit_v1.2_sd_core.json",
                    "--rule-taxonomy-expansion",
                    "reports/rule_taxonomy_expansion_v1.2_sd_core.json",
                    "--hard-boundary-benchmark",
                    "reports/hard_boundary_benchmark_v1.2_sd_core.json",
                ],
                [
                    "metadata/dataset_metadata.json",
                    "docs/TECHNICAL_VALIDATION.md",
                    "docs/PAPER_OUTLINE_ALIGNMENT.md",
                ],
                heavy=True,
            ),
            Step(
                "capture_formal_runtime_environment",
                "07_release_docs",
                [
                    PYTHON,
                    "scripts/capture_formal_runtime_environment.py",
                ],
                [
                    "metadata/formal_runtime_environment.json",
                ],
                input_paths=[
                    "environment.yml",
                    "scripts/capture_formal_runtime_environment.py",
                ],
            ),
            Step(
                "build_reproducibility_manifests",
                "07_release_docs",
                [
                    PYTHON,
                    "scripts/build_reproducibility_manifests.py",
                ],
                [
                    "metadata/environment_lock.json",
                    "metadata/runtime_environment.json",
                    "metadata/requirements-lock.txt",
                    "metadata/environment-freeze.txt",
                    "metadata/code_manifest.json",
                    "metadata/model_revision_manifest.json",
                ],
            ),
            Step(
                "build_data_lineage_manifest",
                "07_release_docs",
                [
                    PYTHON,
                    "scripts/build_data_lineage_manifest.py",
                ],
                [
                    "metadata/data_lineage_manifest.json",
                    "docs/DATA_GENERATION_LINEAGE.md",
                ],
                input_paths=[
                    "data/stages/v1.2_sd_core/*.jsonl",
                    "data/gridinstruct_v1.2_sd_core*.jsonl",
                    "data/v1.2_sd_core*.jsonl",
                    "data/proxy_stress/*/*.jsonl",
                    "simulation_outputs/power_flow/scenarios_all.json",
                    "simulation_outputs/contingency/scenarios_all.json",
                    "simulation_outputs/contingency/scenarios_converged.json",
                    "simulation_outputs/contingency/scenario_rebuild_manifest.json",
                    "simulation_outputs/topology_stress/extended_topology_scenarios.json",
                    "simulation_outputs/topology_stress/legacy_topology_scenarios_v1.json",
                    "data/frozen_sources/extended_topology_instruction_v1.jsonl",
                    "data/frozen_sources/extended_topology_instruction_v2.jsonl",
                    "metadata/frozen_source_checksums_v2.json",
                    "reports/frozen_external_topology_migration_v2.json",
                    "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json",
                    "simulation_outputs/opf_closed_loop/opf_closed_loop_commit_v1.2_sd_core.json",
                    "rules/regulation_rules.json",
                    "metadata/rule_clause_matrix.json",
                    "review_packages/stratified_expert_review_v1.2_sd_core/review_packet_blinded.jsonl",
                ],
            ),
            Step(
                "build_third_party_asset_inventory",
                "07_release_docs",
                [
                    PYTHON,
                    "scripts/build_third_party_asset_inventory.py",
                ],
                [
                    "metadata/third_party_asset_inventory.json",
                    "docs/THIRD_PARTY_ASSETS.md",
                ],
                input_paths=[
                    "metadata/pglib_opf_v23_07_case_registry.json",
                    "third_party/pglib-opf-v23.07/LICENSE",
                ],
            ),
            Step(
                "sync_manuscript_claims_from_evidence",
                "08_evidence_binding",
                [
                    PYTHON,
                    "scripts/sync_manuscript_claims_from_evidence.py",
                    "--dataset-metadata",
                    "metadata/dataset_metadata.json",
                    "--validation",
                    "reports/data_validation_v1.2_sd_core.json",
                    "--candidate-report",
                    "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
                    "--opf-report",
                    "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.json",
                    "--uncertainty-report",
                    "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
                    "--constant-power-factor-report",
                    (
                        "reports/"
                        "opf_action_constant_power_factor_stress_v1.2_sd_core.json"
                    ),
                    "--independent-solver-report",
                    "reports/independent_solver_validation_v1.2_sd_core.json",
                    "--report",
                    "reports/manuscript_claim_sync_v1.2_sd_core.json",
                ],
                [
                    "reports/manuscript_claim_sync_v1.2_sd_core.json",
                    "metadata/manuscript_claim_registry_v1.2_sd_core.json",
                    (
                        "paper/scientific_data_latex/generated/"
                        "gridinstruct_claims.tex"
                    ),
                    "paper/scientific_data/manuscript.md",
                ],
                input_paths=[
                    "metadata/dataset_metadata.json",
                    "reports/data_validation_v1.2_sd_core.json",
                    "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
                    "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.json",
                    "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
                    (
                        "reports/"
                        "opf_action_constant_power_factor_stress_v1.2_sd_core.json"
                    ),
                    "reports/independent_solver_validation_v1.2_sd_core.json",
                    "metadata/manuscript_claim_contract.json",
                ],
            ),
            Step(
                "build_sd_evidence_binding_manifest",
                "08_evidence_binding",
                [
                    PYTHON,
                    "scripts/build_sd_evidence_binding_manifest.py",
                ],
                ["metadata/evidence_binding_manifest.json"],
                input_paths=[
                    *CURRENT_INPUTS.values(),
                    *EVIDENCE_PATHS.values(),
                    # Retain the legacy artifact in the pipeline contract so
                    # historical isolated fixtures remain reproducible; the
                    # active evidence contract resolves the rebound report.
                    "reports/independent_solver_validation_v1.2_sd_core.json",
                    "benchmark/multiseed_v1.2_sd_core/*_five_seed_summary.json",
                ],
            ),
            Step(
                "audit_release_claim_alignment",
                "08_evidence_binding",
                [
                    PYTHON,
                    "scripts/audit_release_claim_alignment.py",
                ],
                [
                    "reports/release_claim_alignment_audit_v1.2_sd_core.json",
                    "reports/release_claim_alignment_audit_v1.2_sd_core.md",
                ],
                input_paths=[
                    "metadata/dataset_metadata.json",
                    "metadata/evidence_binding_manifest.json",
                    "paper/scientific_data/manuscript.md",
                    "paper/scientific_data_latex/main.tex",
                    "reports/extended_topology_stress_audit_v1.2_sd_core.json",
                    "reports/english_translation_audit_v1.2_sd_core.json",
                    "reports/english_split_audit_v1.2_sd_core.json",
                    "reports/expert_review_execution_check_v1.2_sd_core.json",
                    "reports/high_score_plausibility_audit_v1.2_sd_core.json",
                    "reports/split_independence_audit_v1.2_sd_core.json",
                    "reports/strict_source_group_split_v1.2_sd_core.json",
                    "reports/topology_instruction_expansion_v1.2_sd_core.json",
                    "reports/rule_coverage_scope_audit_v1.2_sd_core.json",
                    "reports/translation_glossary_audit_v1.2_sd_core.json",
                    "reports/action_level_validation_audit_v1.2_sd_core.json",
                    "reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json",
                    "reports/opf_closed_loop_auxiliary_audit_v1.2_sd_core.json",
                    "reports/opf_action_uncertainty_stress_v1.2_sd_core.json",
                    "reports/independent_solver_validation_v1.2_sd_core.json",
                ],
            ),
            Step(
                "generate_publication_sd_figures",
                "08_visualizations",
                [
                    PYTHON,
                    "scripts/generate_publication_sd_figures.py",
                ],
                [
                    "reports/sd_publication_visualization_refresh_v1.2_sd_core.json",
                    "reports/sd_publication_visualization_refresh_v1.2_sd_core.md",
                    "reports/language_position_audit_v1.2_sd_core.json",
                    "reports/sd_pre_release_quality_snapshot_v1.2_sd_core.json",
                    "figures/sd_core_publication/fig1_publication_dataset_composition.png",
                    "figures/sd_core_publication/fig2_publication_split_design.png",
                    "figures/sd_core_publication/fig3_publication_topology_coverage.png",
                    "figures/sd_core_publication/fig4_publication_model_validation.png",
                    "figures/sd_core_publication/fig5_publication_high_score_audit.png",
                    "figures/sd_core_publication/fig6_publication_quality_gates.png",
                    "figures/sd_core_publication/fig1_publication_dataset_composition.pdf",
                    "figures/sd_core_publication/fig2_publication_split_design.pdf",
                    "figures/sd_core_publication/fig3_publication_topology_coverage.pdf",
                    "figures/sd_core_publication/fig4_publication_model_validation.pdf",
                    "figures/sd_core_publication/fig5_publication_high_score_audit.pdf",
                    "figures/sd_core_publication/fig6_publication_quality_gates.pdf",
                ],
                input_paths=[
                    ENGLISH_DATASET,
                    "metadata/dataset_metadata.json",
                    "metadata/evidence_binding_manifest.json",
                    "reports/project_milestone_audit_v1.2_sd_core.json",
                    "reports/data_validation_v1.2_sd_core.json",
                    "reports/data_quality_optimization_v1.2_sd_core.json",
                    "reports/generation_grounding_audit_v1.2_sd_core.json",
                    "reports/query_result_consistency_repair_v1.2_sd_core.json",
                    "reports/source_group_map_audit_v1.2_sd_core.json",
                    "reports/split_independence_audit_v1.2_sd_core.json",
                    "reports/strict_source_group_split_v1.2_sd_core.json",
                    "reports/template_holdout_split_v1.2_sd_core.json",
                    "reports/model_score_quality_audit_v1.2_sd_core.json",
                    "reports/strict_split_evidence_v1.2_sd_core.json",
                    "reports/proxy_reduced_input_audit_v1.2_sd_core.json",
                    "reports/sd_statistical_quality_audit_v1.2_sd_core.json",
                    "reports/calibration_audit_v1.2_sd_core.json",
                    "reports/linear_seed_stability_audit_v1.2_sd_core.json",
                    "reports/expert_review_package_v1.2_sd_core.json",
                    "reports/expert_review_readiness_audit_v1.2_sd_core.json",
                    "reports/expert_review_execution_check_v1.2_sd_core.json",
                    "benchmark/v1.2_sd_core*_report.json",
                ],
            ),
            Step(
                "audit_sd_visualization_completeness",
                "08_visualizations",
                [
                    PYTHON,
                    "scripts/audit_sd_visualization_completeness.py",
                    "--figure-dir",
                    "figures/sd_core",
                    "--publication-figure-dir",
                    "figures/sd_core_publication",
                    "--figure-summary",
                    "reports/sd_core_figure_summary.json",
                    "--quality-snapshot",
                    "reports/sd_pre_release_quality_snapshot_v1.2_sd_core.json",
                    "--output-json",
                    "reports/sd_visualization_completeness_v1.2_sd_core.json",
                    "--output-md",
                    "reports/sd_visualization_completeness_v1.2_sd_core.md",
                    "--inventory-csv",
                    "figures/sd_core/figure_inventory_v1.2_sd_core.csv",
                ],
                [
                    "reports/sd_visualization_completeness_v1.2_sd_core.json",
                    "reports/sd_visualization_completeness_v1.2_sd_core.md",
                    "figures/sd_core/figure_inventory_v1.2_sd_core.csv",
                ],
            ),
            Step(
                "rebuild_scientific_data_latex_package",
                "09_final_assessment",
                [
                    PYTHON,
                    "paper/scientific_data_latex/rebuild_latex_report_package.py",
                ],
                [
                    "paper/scientific_data_latex/build/main.pdf",
                    "paper/scientific_data_latex/build/main.bbl",
                    "paper/scientific_data_latex/build_embedded/main_with_bbl.pdf",
                    (
                        "paper/scientific_data_latex/"
                        "GridInstruct_SD_scientific_data_official_template.pdf"
                    ),
                    (
                        "paper/scientific_data_latex/"
                        "GridInstruct_SD_scientific_data_latex_source.zip"
                    ),
                    "paper/scientific_data_latex/LATEX_BUILD_REPORT.json",
                    "paper/scientific_data_latex/LATEX_BUILD_REPORT.md",
                ],
                input_paths=[
                    "paper/scientific_data/manuscript.md",
                    "paper/scientific_data/references.bib",
                    "paper/scientific_data_latex/main.tex",
                    (
                        "paper/scientific_data_latex/generated/"
                        "gridinstruct_claims.tex"
                    ),
                    "paper/scientific_data_latex/build_from_markdown.py",
                    "paper/scientific_data_latex/make_main_with_bbl.py",
                    "paper/scientific_data_latex/rebuild_latex_report_package.py",
                    "paper/scientific_data_latex/sn-jnl.cls",
                    "paper/scientific_data_latex/*.bst",
                    "paper/scientific_data_latex/bst/*.bst",
                    "figures/sd_core_publication/*.png",
                ],
            ),
            Step(
                "build_release_bundle",
                "09_final_assessment",
                [
                    PYTHON,
                    "scripts/build_release_bundle.py",
                    "--bundle",
                    "release/GridInstruct_v1.2_sd_core_release_candidate.tar.gz",
                    "--manifest",
                    "release/archive_manifest_v1.2_sd_core.csv",
                    "--checksums",
                    "release/checksums_sha256.txt",
                    "--validation-json",
                    "reports/release_bundle_validation_v1.2_sd_core.json",
                    "--validation-md",
                    "reports/release_bundle_validation_v1.2_sd_core.md",
                    "--deposition-json",
                    "reports/deposition_checklist_v1.2_sd_core.json",
                    "--deposition-md",
                    "reports/deposition_checklist_v1.2_sd_core.md",
                ],
                [
                    "release/GridInstruct_v1.2_sd_core_release_candidate.tar.gz",
                    "release/archive_manifest_v1.2_sd_core.csv",
                    "release/checksums_sha256.txt",
                    "reports/release_bundle_validation_v1.2_sd_core.json",
                    "reports/release_bundle_validation_v1.2_sd_core.md",
                    "reports/deposition_checklist_v1.2_sd_core.json",
                    "reports/deposition_checklist_v1.2_sd_core.md",
                    "release/GridInstruct_v1.2_sd_core_release_candidate.tar.gz.sha256",
                ],
                input_paths=[
                    "metadata/dataset_metadata.json",
                    "metadata/archive_metadata.json",
                    "metadata/data_lineage_manifest.json",
                    "metadata/third_party_asset_inventory.json",
                    "metadata/evidence_binding_manifest.json",
                    "metadata/code_manifest.json",
                    "metadata/environment_lock.json",
                    "metadata/model_revision_manifest.json",
                    "reports/release_claim_alignment_audit_v1.2_sd_core.json",
                    "reports/sd_visualization_completeness_v1.2_sd_core.json",
                    "reports/sd_pre_release_quality_snapshot_v1.2_sd_core.json",
                    "paper/scientific_data/manuscript.md",
                    "paper/scientific_data_latex/LATEX_BUILD_REPORT.json",
                    (
                        "paper/scientific_data_latex/"
                        "GridInstruct_SD_scientific_data_official_template.pdf"
                    ),
                    (
                        "paper/scientific_data_latex/"
                        "GridInstruct_SD_scientific_data_latex_source.zip"
                    ),
                    "docs/*.md",
                    "figures/sd_core/*.png",
                    "figures/sd_core_publication/*.png",
                    "figures/sd_core_publication/*.pdf",
                ],
            ),
            Step(
                "validate_release_archive_replay",
                "09_final_assessment",
                [
                    PYTHON,
                    "scripts/validate_release_archive_replay.py",
                    "--bundle",
                    "release/GridInstruct_v1.2_sd_core_release_candidate.tar.gz",
                    "--output-json",
                    "reports/release_archive_replay_v1.2_sd_core.json",
                    "--output-md",
                    "reports/release_archive_replay_v1.2_sd_core.md",
                ],
                [
                    "reports/release_archive_replay_v1.2_sd_core.json",
                    "reports/release_archive_replay_v1.2_sd_core.md",
                ],
                input_paths=[
                    "release/GridInstruct_v1.2_sd_core_release_candidate.tar.gz"
                ],
            ),
            Step(
                "audit_scientific_data_submission",
                "09_final_assessment",
                [
                    PYTHON,
                    "scripts/audit_scientific_data_submission.py",
                    "--manuscript",
                    "paper/scientific_data/manuscript.md",
                    "--report-json",
                    "reports/scientific_data_submission_gate.json",
                    "--report-md",
                    "reports/scientific_data_submission_gate.md",
                ],
                [
                    "reports/scientific_data_submission_gate.json",
                    "reports/scientific_data_submission_gate.md",
                ],
                input_paths=[
                    "metadata/archive_metadata.json",
                    "metadata/evidence_binding_manifest.json",
                    "metadata/data_lineage_manifest.json",
                    "reports/release_bundle_validation_v1.2_sd_core.json",
                    "reports/release_archive_replay_v1.2_sd_core.json",
                    "reports/release_claim_alignment_audit_v1.2_sd_core.json",
                    "reports/expert_review_execution_check_v1.2_sd_core.json",
                    "paper/scientific_data/manuscript.md",
                    "paper/scientific_data_latex/references.bib",
                    "paper/scientific_data_latex/LATEX_BUILD_REPORT.json",
                    (
                        "paper/scientific_data_latex/"
                        "GridInstruct_SD_scientific_data_official_template.pdf"
                    ),
                    (
                        "paper/scientific_data_latex/"
                        "GridInstruct_SD_scientific_data_latex_source.zip"
                    ),
                ],
            ),
            Step(
                "write_final_assessment",
                "09_final_assessment",
                [
                    PYTHON,
                    "scripts/write_sd_final_assessment.py",
                    "--metadata",
                    "metadata/dataset_metadata.json",
                    "--audit",
                    "reports/project_milestone_audit_v1.2_sd_core.json",
                    "--review",
                    REVIEW_REPORT,
                    "--validation",
                    "reports/data_validation_v1.2_sd_core.json",
                    "--data-quality",
                    "reports/data_quality_optimization_v1.2_sd_core.json",
                    "--model-score-quality",
                    "reports/model_score_quality_audit_v1.2_sd_core.json",
                    "--query-repair",
                    "reports/query_result_consistency_repair_v1.2_sd_core.json",
                    "--query-independent",
                    "reports/independent_query_truth_validation_v1.2_sd_core.json",
                    "--scenario-truth",
                    "reports/scenario_truth_rebuild_v1.2_sd_core.json",
                    "--cross-solver",
                    "reports/cross_solver_power_flow_v1.2_sd_core.json",
                    "--group-bootstrap",
                    "reports/group_cluster_bootstrap_v1.2_sd_core.json",
                    "--near-duplicate",
                    "reports/near_duplicate_audit_v1.2_sd_core.json",
                    "--grounding-audit",
                    "reports/generation_grounding_audit_v1.2_sd_core.json",
                    "--proxy-split-manifest",
                    "reports/proxy_stress_split_manifest_v1.2_sd_core.json",
                    "--proxy-stress",
                    "benchmark/v1.2_sd_core_proxy_stress_report.json",
                    "--structured-normalization",
                    "reports/structured_prediction_normalization_v1.2_sd_core.json",
                    "--shortcut-ablation",
                    "reports/shortcut_ablation_v1.2_sd_core.json",
                    "--expert-review-package",
                    "reports/expert_review_package_v1.2_sd_core.json",
                    "--expert-review-readiness",
                    "reports/expert_review_readiness_audit_v1.2_sd_core.json",
                    "--statistical-quality",
                    "reports/sd_statistical_quality_audit_v1.2_sd_core.json",
                    "--calibration-audit",
                    "reports/calibration_audit_v1.2_sd_core.json",
                    "--split-independence",
                    "reports/split_independence_audit_v1.2_sd_core.json",
                    "--source-group-map",
                    "reports/source_group_map_audit_v1.2_sd_core.json",
                    "--strict-split",
                    "reports/strict_source_group_split_v1.2_sd_core.json",
                    "--strict-evidence",
                    "reports/strict_split_evidence_v1.2_sd_core.json",
                    "--proxy-reduced-input",
                    "reports/proxy_reduced_input_audit_v1.2_sd_core.json",
                    "--template-holdout",
                    "reports/template_holdout_split_v1.2_sd_core.json",
                    "--rule-context-baseline",
                    "benchmark/v1.2_sd_core_rule_context_tfidf_report.json",
                    "--high-score-plausibility",
                    "reports/high_score_plausibility_audit_v1.2_sd_core.json",
                    "--expert-review-execution",
                    "reports/expert_review_execution_check_v1.2_sd_core.json",
                    "--label-split-support",
                    "reports/label_split_support_audit_v1.2_sd_core.json",
                    "--linear-seed-stability",
                    "reports/linear_seed_stability_audit_v1.2_sd_core.json",
                    "--generation-structure-coverage",
                    "reports/generation_structure_coverage_audit_v1.2_sd_core.json",
                    "--visualization-completeness",
                    "reports/sd_visualization_completeness_v1.2_sd_core.json",
                    "--external-topology-physical-envelope",
                    "reports/external_topology_physical_envelope_v1.2_sd_core.json",
                    "--rule-taxonomy-expansion",
                    "reports/rule_taxonomy_expansion_v1.2_sd_core.json",
                    "--opf-closed-loop-assumption",
                    "reports/opf_closed_loop_assumption_audit_v1.2_sd_core.json",
                    "--hard-boundary-benchmark",
                    "reports/hard_boundary_benchmark_v1.2_sd_core.json",
                    "--release-bundle-validation",
                    "reports/release_bundle_validation_v1.2_sd_core.json",
                    "--deposition-checklist",
                    "reports/deposition_checklist_v1.2_sd_core.json",
                    "--evidence-binding",
                    "metadata/evidence_binding_manifest.json",
                    "--data-lineage",
                    "metadata/data_lineage_manifest.json",
                    "--release-archive-replay",
                    "reports/release_archive_replay_v1.2_sd_core.json",
                    "--scientific-data-submission-gate",
                    "reports/scientific_data_submission_gate.json",
                    "--third-party-assets",
                    "metadata/third_party_asset_inventory.json",
                    "--figure-dir",
                    "figures/sd_core",
                    "--output-md",
                    "reports/SD_FINAL_ASSESSMENT.md",
                    "--output-json",
                    "reports/SD_FINAL_ASSESSMENT.json",
                ],
                [
                    "reports/SD_FINAL_ASSESSMENT.md",
                    "reports/SD_FINAL_ASSESSMENT.json",
                    "reports/EXPERIMENT_AUDIT.md",
                    "reports/EXPERIMENT_AUDIT.json",
                    "reports/RESULT_TO_CLAIM.md",
                    "reports/RESULT_TO_CLAIM.json",
                ],
            ),
        ]
    )
    return steps


def write_run_manifests(
    run_root: Path,
    invocation: dict[str, Any],
    *,
    full_run_requested: bool,
) -> None:
    invocation_dir = run_root / "results" / "invocations"
    ensure_dirs(invocation_dir)
    scope = str(invocation.get("only_step") or invocation.get("start_at") or "full")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    invocation_path = invocation_dir / f"{stamp}_{os.getpid()}_{scope}.json"
    write_json(invocation_path, invocation)
    write_json(
        run_root / "results" / "sd_core_last_invocation_manifest.json", invocation
    )
    if full_run_requested:
        write_json(
            run_root / "results" / "sd_core_full_pipeline_manifest.json", invocation
        )

    statuses = []
    for status_path in sorted(run_root.rglob("*_status.json")):
        if "invocations" in status_path.parts:
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        status["status_manifest"] = rel(status_path)
        fingerprint_path = status_path.with_name(
            status_path.name.replace("_status.json", "_input_fingerprint.json")
        )
        status["fingerprint_manifest"] = (
            rel(fingerprint_path) if fingerprint_path.is_file() else None
        )
        statuses.append(status)
    by_name = {
        str(status.get("name")): status for status in statuses if status.get("name")
    }
    failed = sorted(
        name for name, status in by_name.items() if status.get("returncode") != 0
    )
    ledger = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_root": rel(run_root),
        "status": "fail" if failed else "pass" if by_name else "empty",
        "step_count": len(by_name),
        "passed_step_count": sum(
            status.get("returncode") == 0 for status in by_name.values()
        ),
        "failed_steps": failed,
        "steps": [by_name[name] for name in sorted(by_name)],
        "invocation_count": len(list(invocation_dir.glob("*.json"))),
        "last_invocation_manifest": rel(invocation_path),
    }
    write_json(run_root / "results" / "sd_core_run_ledger.json", ledger)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", default=DEFAULT_RUN_ROOT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rerun every step, including heavy model training.",
    )
    parser.add_argument(
        "--force-light",
        action="store_true",
        help="Refresh non-heavy data, audit, visualization, and reporting steps while reusing existing model training outputs.",
    )
    parser.add_argument(
        "--start-at",
        default=None,
        help="Start at a named pipeline step after checking the name exists.",
    )
    parser.add_argument(
        "--stop-after", default=None, help="Stop after a named pipeline step."
    )
    parser.add_argument(
        "--only-step",
        default=None,
        help="Run exactly one named step with its own current-input fingerprint.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue a v16 run at its first missing, failed, stale, or hash-mismatched step.",
    )
    args = parser.parse_args()

    run_root = ROOT / args.run_root
    ensure_dirs(run_root, run_root / "logs", run_root / "results")
    steps = build_steps(args.run_root)
    names = [step.name for step in steps]
    if args.resume and (
        args.only_step or args.start_at or args.force or args.force_light
    ):
        raise ValueError(
            "--resume cannot be combined with --only-step, --start-at, --force, or --force-light"
        )
    if args.only_step and (args.start_at or args.stop_after):
        raise ValueError(
            "--only-step cannot be combined with --start-at or --stop-after"
        )
    if args.only_step:
        if args.only_step not in names:
            raise ValueError(f"Unknown --only-step: {args.only_step}")
        steps = [steps[names.index(args.only_step)]]
        names = [args.only_step]
    if args.start_at:
        if args.start_at not in names:
            raise ValueError(f"Unknown --start-at step: {args.start_at}")
        start_index = names.index(args.start_at)
        validate_completed_prefix(steps[:start_index], run_root)
        steps = steps[names.index(args.start_at) :]
        names = [step.name for step in steps]
    resume_from = None
    if args.resume:
        index = resume_index(steps, run_root)
        resume_from = names[index] if index < len(names) else "already_complete"
        steps = steps[index:]
        names = names[index:]
    if args.stop_after:
        if args.stop_after not in names:
            raise ValueError(f"Unknown --stop-after step: {args.stop_after}")
        steps = steps[: names.index(args.stop_after) + 1]
    statuses: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    try:
        for step in tqdm(steps, desc="SD core full pipeline"):
            try:
                status = run_step(
                    step, run_root, force=args.force, force_light=args.force_light
                )
                statuses.append(status)
            except Exception as exc:
                status_path = (
                    run_root / step.experiment / "results" / f"{step.name}_status.json"
                )
                failed_status = (
                    json.loads(status_path.read_text(encoding="utf-8"))
                    if status_path.exists()
                    else {
                        "name": step.name,
                        "experiment": step.experiment,
                        "status": "fail",
                        "returncode": 1,
                    }
                )
                statuses.append(failed_status)
                failure = {
                    "step": step.name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                raise
    finally:
        full_run_requested = (
            args.start_at is None and args.stop_after is None and args.only_step is None
        )
        final_gate = None
        final_path = ROOT / "reports/SD_FINAL_ASSESSMENT.json"
        if full_run_requested and final_path.exists():
            try:
                final_gate = json.loads(final_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                final_gate = None
        manifest_status = (
            "complete"
            if failure is None
            and all(status.get("returncode") == 0 for status in statuses)
            and (
                not full_run_requested
                or bool((final_gate or {}).get("local_standard_met"))
            )
            else "partial_complete"
            if failure is None
            else "failed"
        )
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": manifest_status,
            "dataset": DATASET,
            "run_root": args.run_root,
            "force": args.force,
            "force_light": args.force_light,
            "resume": args.resume,
            "resume_from": resume_from,
            "start_at": args.start_at,
            "stop_after": args.stop_after,
            "only_step": args.only_step,
            "steps_requested": len(steps),
            "steps_recorded": len(statuses),
            "steps_passed": sum(status.get("returncode") == 0 for status in statuses),
            "steps_skipped_existing": sum(
                status.get("status") == "skipped_existing" for status in statuses
            ),
            "heavy_steps": sum(1 for status in statuses if status.get("heavy")),
            "failure": failure,
            "final_local_standard_met": (final_gate or {}).get("local_standard_met"),
            "statuses": statuses,
        }
        write_run_manifests(
            run_root,
            manifest,
            full_run_requested=full_run_requested,
        )


if __name__ == "__main__":
    main()
