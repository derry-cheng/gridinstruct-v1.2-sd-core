"""Unified GridInstruct Scientific Data experiment pipeline.

Runs the current release-candidate dataset through validation, official splits,
baselines, audit, release-doc refresh, and paper figures. Each experiment family
gets a dedicated folder with run scripts, logs, configs, results, checkpoints,
and figures where applicable.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tqdm.auto import tqdm

from gridinstruct_utils import ROOT, ensure_dirs, write_json


@dataclass
class Step:
    name: str
    experiment: str
    cmd: list[str]
    expected_outputs: list[str]


def exists_all(paths: list[str]) -> bool:
    return all((ROOT / path).exists() and (ROOT / path).stat().st_size > 0 for path in paths)


def write_run_script(path: Path, cmd: list[str]) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + " ".join(cmd) + "\n", encoding="utf-8")
    path.chmod(0o755)


def run_step(step: Step, run_root: Path, force: bool) -> dict:
    exp_dir = run_root / step.experiment
    log_dir = exp_dir / "logs"
    config_dir = exp_dir / "configs"
    result_dir = exp_dir / "results"
    ensure_dirs(log_dir, config_dir, result_dir)
    write_run_script(exp_dir / "run.sh", step.cmd)
    write_run_script(exp_dir / f"run_{step.name}.sh", step.cmd)
    write_json(config_dir / f"{step.name}.json", {"name": step.name, "command": step.cmd, "expected_outputs": step.expected_outputs})

    started_at = datetime.now(timezone.utc).isoformat()
    if not force and exists_all(step.expected_outputs):
        status = {
            "name": step.name,
            "experiment": step.experiment,
            "status": "skipped_existing",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "returncode": 0,
            "expected_outputs": step.expected_outputs,
        }
        write_json(result_dir / f"{step.name}_status.json", status)
        return status

    stdout_path = log_dir / f"{step.name}.stdout.log"
    stderr_path = log_dir / f"{step.name}.stderr.log"
    env = os.environ.copy()
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("HF_HUB_OFFLINE", "1")
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(step.cmd, cwd=ROOT, stdout=stdout, stderr=stderr, text=True, env=env)
    status = {
        "name": step.name,
        "experiment": step.experiment,
        "status": "pass" if completed.returncode == 0 else "fail",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "returncode": completed.returncode,
        "stdout_log": str(stdout_path.relative_to(ROOT)),
        "stderr_log": str(stderr_path.relative_to(ROOT)),
        "expected_outputs": step.expected_outputs,
    }
    write_json(result_dir / f"{step.name}_status.json", status)
    if completed.returncode != 0:
        raise RuntimeError(f"Step failed: {step.name}. See {stderr_path}")
    return status


def copy_result_files(run_root: Path, experiment: str, outputs: list[str]) -> None:
    result_dir = run_root / experiment / "results"
    ensure_dirs(result_dir)
    for rel in outputs:
        src = ROOT / rel
        if src.exists() and src.is_file():
            shutil.copy2(src, result_dir / src.name)


def build_steps(args: argparse.Namespace) -> list[Step]:
    py = sys.executable
    source_dataset = args.source_dataset
    harmonized_dataset = args.harmonized_dataset
    dataset = args.dataset
    tag = args.dataset_tag
    train = f"data/v1.2_paper_{tag}_train.jsonl"
    validation = f"data/v1.2_paper_{tag}_validation.jsonl"
    test = f"data/v1.2_paper_{tag}_test.jsonl"
    ood = f"data/v1.2_paper_{tag}_ood_test.jsonl"
    evaluation = f"benchmark/v1.2_paper_{tag}_evaluation_tasks.json"
    validation_report_prefix = f"reports/data_validation_v1.2_{tag}"
    validation_report = f"{validation_report_prefix}.json"
    data_quality_report = f"reports/data_quality_optimization_v1.2_{tag}.json"
    data_quality_report_md = f"reports/data_quality_optimization_v1.2_{tag}.md"
    harmonization_report = f"reports/regqa_target_harmonization_v1.2_{tag}.json"
    harmonization_report_md = f"reports/regqa_target_harmonization_v1.2_{tag}.md"
    canonicalization_report = f"reports/label_space_canonicalization_v1.2_{tag}.json"
    canonicalization_report_md = f"reports/label_space_canonicalization_v1.2_{tag}.md"
    score_quality_report = f"reports/model_score_quality_audit_v1.2_{tag}.json"
    score_quality_report_md = f"reports/model_score_quality_audit_v1.2_{tag}.md"
    review_report = "reports/llm_review_report_v1.2_paper_plus7_cumulative.json"
    tfidf_prefix = f"benchmark/v1.2_paper_{tag}_tfidf"
    tfidf_report = f"{tfidf_prefix}_report.json"

    steps = []
    if not args.skip_dataset_rebuild:
        steps.extend(
            [
                Step(
                    "harmonize_regqa_targets",
                    "01_dataset_integrity",
                    [
                        py,
                        "scripts/harmonize_regqa_outputs.py",
                        "--input",
                        source_dataset,
                        "--output",
                        harmonized_dataset,
                        "--report-json",
                        harmonization_report,
                        "--report-md",
                        harmonization_report_md,
                    ],
                    [harmonized_dataset, harmonization_report, harmonization_report_md],
                ),
                Step(
                    "canonicalize_label_space",
                    "01_dataset_integrity",
                    [
                        py,
                        "scripts/canonicalize_label_space.py",
                        "--input",
                        harmonized_dataset,
                        "--output",
                        dataset,
                        "--report-json",
                        canonicalization_report,
                        "--report-md",
                        canonicalization_report_md,
                    ],
                    [dataset, canonicalization_report, canonicalization_report_md],
                ),
            ]
        )

    steps.extend(
        [
            Step(
            "validate_dataset",
            "01_dataset_integrity",
            [py, "scripts/validate_dataset.py", "--input", dataset, "--report-prefix", validation_report_prefix],
            [validation_report, f"{validation_report_prefix}.md"],
            ),
            Step(
            "create_official_splits",
            "01_dataset_integrity",
            [
                py,
                "scripts/create_splits.py",
                "--input",
                dataset,
                "--train-output",
                train,
                "--validation-output",
                validation,
                "--test-output",
                test,
                "--ood-output",
                ood,
                "--evaluation-output",
                evaluation,
            ],
            [train, validation, test, ood, evaluation],
            ),
            Step(
            "analyze_data_quality",
            "01_dataset_integrity",
            [
                py,
                "scripts/analyze_data_quality.py",
                "--dataset",
                dataset,
                "--train",
                train,
                "--validation",
                validation,
                "--test",
                test,
                "--ood",
                ood,
                "--output-json",
                data_quality_report,
                "--output-md",
                data_quality_report_md,
            ],
            [data_quality_report, data_quality_report_md],
            ),
            Step(
            "run_tfidf",
            "03_tfidf_baseline",
            [
                py,
                "scripts/run_tfidf_task_baselines.py",
                "--train",
                train,
                "--validation",
                validation,
                "--test",
                test,
                "--ood",
                ood,
                "--output-prefix",
                tfidf_prefix,
            ],
            [
                tfidf_report,
                f"{tfidf_prefix}_validation_predictions.jsonl",
                f"{tfidf_prefix}_test_predictions.jsonl",
                f"{tfidf_prefix}_ood_predictions.jsonl",
            ],
            ),
        ]
    )

    transformer_tasks = [
        ("operation_ticket_check", []),
        ("regulation_compliance_check", []),
        ("dispatcher_intent_tool_call", []),
    ]
    for task, extra in transformer_tasks:
        prefix = f"benchmark/v1.2_paper_{tag}_transformer_{task}{'_rationale' if extra else ''}"
        model_name = args.compliance_transformer_model if task == "regulation_compliance_check" else args.transformer_model
        epochs = args.compliance_transformer_epochs if task == "regulation_compliance_check" else args.transformer_epochs
        batch_size = args.compliance_transformer_batch_size if task == "regulation_compliance_check" else args.transformer_batch_size
        max_length = args.compliance_transformer_max_length if task == "regulation_compliance_check" else args.transformer_max_length
        steps.append(
            Step(
                f"transformer_{task}",
                f"04_transformer_classification/{task}_{tag}",
                [
                    py,
                    "scripts/run_transformer_classifier_baseline.py",
                    "--task-type",
                    task,
                    "--train",
                    train,
                    "--validation",
                    validation,
                    "--test",
                    test,
                    "--model-name",
                    model_name,
                    "--epochs",
                    str(epochs),
                    "--batch-size",
                    str(batch_size),
                    "--max-length",
                    str(max_length),
                    "--output-prefix",
                    prefix,
                    "--experiment-dir",
                    f"experiments/04_transformer_classification/{task}_{tag}",
                    *extra,
                ],
                [
                    f"{prefix}_report.json",
                    f"{prefix}_report.md",
                    f"{prefix}_validation_predictions.jsonl",
                    f"{prefix}_test_predictions.jsonl",
                ],
            )
        )

    challenge_train = f"data/v1.2_paper_{tag}_challenge_train.jsonl"
    challenge_validation = f"data/v1.2_paper_{tag}_challenge_validation.jsonl"
    challenge_test = f"data/v1.2_paper_{tag}_challenge_test.jsonl"
    challenge_split_report = f"benchmark/v1.2_paper_{tag}_classification_challenge_splits.json"
    steps.append(
        Step(
            "create_classification_challenge_splits",
            "02_challenge_splits",
            [
                py,
                "scripts/create_classification_challenge_splits.py",
                "--input",
                dataset,
                "--train-output",
                challenge_train,
                "--validation-output",
                challenge_validation,
                "--test-output",
                challenge_test,
                "--report-json",
                challenge_split_report,
            ],
            [challenge_train, challenge_validation, challenge_test, challenge_split_report],
        )
    )
    for task, extra in transformer_tasks:
        prefix = f"benchmark/v1.2_paper_{tag}_challenge_transformer_{task}"
        steps.append(
            Step(
                f"challenge_transformer_{task}",
                f"04_transformer_classification/{task}_{tag}_challenge",
                [
                    py,
                    "scripts/run_transformer_classifier_baseline.py",
                    "--task-type",
                    task,
                    "--train",
                    challenge_train,
                    "--validation",
                    challenge_validation,
                    "--test",
                    challenge_test,
                    "--model-name",
                    args.challenge_transformer_model,
                    "--epochs",
                    str(args.challenge_transformer_epochs),
                    "--batch-size",
                    str(args.challenge_transformer_batch_size),
                    "--max-length",
                    str(args.challenge_transformer_max_length),
                    "--output-prefix",
                    prefix,
                    "--experiment-dir",
                    f"experiments/04_transformer_classification/{task}_{tag}_challenge",
                    *extra,
                ],
                [
                    f"{prefix}_report.json",
                    f"{prefix}_report.md",
                    f"{prefix}_validation_predictions.jsonl",
                    f"{prefix}_test_predictions.jsonl",
                ],
            )
        )

    if args.include_operation_boundary_holdout:
        boundary_train = f"data/v1.2_paper_{tag}_operation_boundary_holdout_train.jsonl"
        boundary_validation = f"data/v1.2_paper_{tag}_operation_boundary_holdout_validation.jsonl"
        boundary_test = f"data/v1.2_paper_{tag}_operation_boundary_holdout_test.jsonl"
        boundary_split_report = f"benchmark/v1.2_paper_{tag}_operation_boundary_holdout_splits.json"
        steps.append(
            Step(
                "create_operation_boundary_holdout_splits",
                "02_challenge_splits",
                [
                    py,
                    "scripts/create_operation_boundary_holdout_splits.py",
                    "--input",
                    dataset,
                    "--train-output",
                    boundary_train,
                    "--validation-output",
                    boundary_validation,
                    "--test-output",
                    boundary_test,
                    "--report-json",
                    boundary_split_report,
                ],
                [boundary_train, boundary_validation, boundary_test, boundary_split_report],
            )
        )
        for profile, suffix in [("full", ""), ("natural", "_natural")]:
            prefix = f"benchmark/v1.2_paper_{tag}_boundary_holdout{suffix}_transformer_operation_ticket_check"
            cmd = [
                py,
                "scripts/run_transformer_classifier_baseline.py",
                "--task-type",
                "operation_ticket_check",
                "--train",
                boundary_train,
                "--validation",
                boundary_validation,
                "--test",
                boundary_test,
                "--model-name",
                args.challenge_transformer_model,
                "--epochs",
                str(args.challenge_transformer_epochs),
                "--batch-size",
                str(args.challenge_transformer_batch_size),
                "--max-length",
                str(args.challenge_transformer_max_length),
                "--output-prefix",
                prefix,
                "--experiment-dir",
                f"experiments/04_transformer_classification/operation_ticket_check_{tag}_boundary_holdout{suffix}",
            ]
            if profile == "natural":
                cmd.extend(["--input-profile", "natural"])
            steps.append(
                Step(
                    f"boundary_holdout{suffix}_transformer_operation_ticket_check",
                    f"04_transformer_classification/operation_ticket_check_{tag}_boundary_holdout{suffix}",
                    cmd,
                    [
                        f"{prefix}_report.json",
                        f"{prefix}_report.md",
                        f"{prefix}_validation_predictions.jsonl",
                        f"{prefix}_test_predictions.jsonl",
                    ],
                )
            )

    seq2seq_tasks = [
        (
            "regulation_qa",
            "regqa_harmonized_e2",
            f"benchmark/v1.2_paper_{tag}_seq2seq_regqa_harmonized_e2",
            f"experiments/05_seq2seq_generation/regqa_harmonized_{tag}_e2",
            "512",
            "320",
            str(args.seq2seq_batch_size),
            str(args.regqa_seq2seq_epochs),
            str(args.regqa_seq2seq_patience),
            "fielded",
        ),
        (
            "auxiliary_decision",
            "auxiliary_decision",
            f"benchmark/v1.2_paper_{tag}_seq2seq_auxiliary_decision",
            f"experiments/05_seq2seq_generation/auxiliary_decision_{tag}",
            "288",
            "128",
            str(args.seq2seq_batch_size),
            str(args.seq2seq_epochs),
            str(args.seq2seq_patience),
            "compact",
        ),
        (
            "intelligent_data_query",
            "intelligent_data_query",
            f"benchmark/v1.2_paper_{tag}_seq2seq_intelligent_data_query",
            f"experiments/05_seq2seq_generation/intelligent_data_query_{tag}",
            "224",
            "80",
            str(args.seq2seq_batch_size),
            str(args.seq2seq_epochs),
            str(args.seq2seq_patience),
            "compact",
        ),
    ]
    for task, slug, prefix, experiment_dir, max_source, max_target, batch_size, epochs, patience, prompt_mode in seq2seq_tasks:
        target_mode = "actionable" if task == "intelligent_data_query" else "full"
        steps.append(
            Step(
                f"seq2seq_{slug}",
                f"05_seq2seq_generation/{Path(experiment_dir).name}",
                [
                    py,
                    "scripts/run_seq2seq_generation_baseline.py",
                    "--train",
                    train,
                    "--validation",
                    validation,
                    "--test",
                    test,
                    "--task-types",
                    task,
                    "--model-name",
                    args.seq2seq_model,
                    "--epochs",
                    epochs,
                    "--patience",
                    patience,
                    "--batch-size",
                    batch_size,
                    "--max-source-len",
                    max_source,
                    "--max-target-len",
                    max_target,
                    "--target-mode",
                    target_mode,
                    "--prompt-mode",
                    prompt_mode,
                    "--num-beams",
                    str(args.seq2seq_num_beams),
                    "--use-slow-tokenizer",
                    "--output-prefix",
                    prefix,
                    "--experiment-dir",
                    experiment_dir,
                ],
                [
                    f"{prefix}_report.json",
                    f"{prefix}_report.md",
                    f"{prefix}_validation_predictions.jsonl",
                    f"{prefix}_test_predictions.jsonl",
                ],
            )
        )

    audit = f"reports/project_milestone_audit_v1.2_paper_{tag}.json"
    audit_md = f"reports/project_milestone_audit_v1.2_paper_{tag}.md"
    steps.extend(
        [
            Step(
                "audit_model_score_quality",
                "06_sd_audit",
                [
                    py,
                    "scripts/audit_model_score_quality.py",
                    "--dataset",
                    dataset,
                    "--train",
                    train,
                    "--test",
                    test,
                    "--standard-report-glob",
                    f"benchmark/v1.2_paper_{tag}_transformer*_report.json",
                    "--challenge-report-glob",
                    f"benchmark/v1.2_paper_{tag}_challenge_transformer*_report.json",
                    "--output-json",
                    score_quality_report,
                    "--output-md",
                    score_quality_report_md,
                ],
                [score_quality_report, score_quality_report_md],
            ),
            Step(
                "run_project_audit",
                "06_sd_audit",
                [
                    py,
                    "scripts/run_project_milestone_audit.py",
                    "--dataset",
                    dataset,
                    "--validation-report",
                    validation_report,
                    "--data-quality-report",
                    data_quality_report,
                    "--review-report",
                    review_report,
                    "--evaluation-report",
                    evaluation,
                    "--tfidf-report",
                    tfidf_report,
                    "--transformer-report",
                    "reports/transformer_classifier_latest_report.json",
                    "--transformer-report-glob",
                    f"benchmark/v1.2_paper_{tag}_transformer*_report.json",
                    "--generative-report",
                    f"benchmark/v1.2_paper_{tag}_seq2seq_regqa_harmonized_e2_report.json",
                    "--generative-report-glob",
                    f"benchmark/v1.2_paper_{tag}_seq2seq_*_report.json",
                    "--dataset-metadata",
                    "metadata/dataset_metadata.json",
                    "--output-json",
                    audit,
                    "--output-md",
                    audit_md,
                    "--expected-version",
                    "1.2",
                    "--min-transformer-macro-f1",
                    str(args.min_transformer_macro_f1),
                    "--min-generative-token-f1",
                    str(args.min_generative_token_f1),
                ],
                [audit, audit_md],
            ),
            Step(
                "refresh_release_artifacts",
                "06_sd_audit",
                [
                    py,
                    "scripts/refresh_release_artifacts.py",
                    "--dataset",
                    dataset,
                    "--dataset-version",
                    "1.2",
                    "--train",
                    train,
                    "--validation",
                    validation,
                    "--test",
                    test,
                    "--ood",
                    ood,
                    "--evaluation-report",
                    evaluation,
                    "--validation-report",
                    validation_report,
                    "--review-report",
                    review_report,
                    "--audit-report",
                    audit,
                    "--tfidf-report",
                    tfidf_report,
                    "--transformer-report-glob",
                    f"benchmark/v1.2_paper_{tag}_transformer*_report.json",
                    "--generative-report",
                    f"benchmark/v1.2_paper_{tag}_seq2seq_regqa_harmonized_e2_report.json",
                    "--generative-report-glob",
                    f"benchmark/v1.2_paper_{tag}_seq2seq_*_report.json",
                ],
                ["metadata/dataset_metadata.json", "docs/TECHNICAL_VALIDATION.md", "docs/PAPER_OUTLINE_ALIGNMENT.md"],
            ),
            Step(
                "generate_figures",
                "07_visualizations",
                [
                    py,
                    "scripts/generate_sd_figures.py",
                    "--audit",
                    audit,
                    "--seq2seq-glob",
                    f"benchmark/v1.2_paper_{tag}_seq2seq_*_report.json",
                    "--challenge-transformer-glob",
                    f"benchmark/v1.2_paper_{tag}_challenge_transformer*_report.json",
                    "--mirror-dir",
                    f"figures/sd_{tag}",
                    "--summary-json",
                    f"experiments/07_visualizations/results/figure_manifest_{tag}.json",
                ],
                [f"experiments/07_visualizations/results/figure_manifest_{tag}.json"],
            ),
            Step(
                "write_final_assessment",
                "08_final_assessment",
                [
                    py,
                    "scripts/write_sd_final_assessment.py",
                    "--audit",
                    audit,
                    "--data-quality",
                    data_quality_report,
                    "--model-score-quality",
                    score_quality_report,
                    "--validation",
                    validation_report,
                    "--figure-dir",
                    f"figures/sd_{tag}",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dataset", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus7.jsonl")
    parser.add_argument("--harmonized-dataset", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus8.jsonl")
    parser.add_argument("--dataset", default="data/gridinstruct_v1.2_paper_candidate_actionable_plus9.jsonl")
    parser.add_argument("--dataset-tag", default="plus9")
    parser.add_argument("--run-root", default="experiments/sd_full_pipeline")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--transformer-model", default="uer/chinese_roberta_L-2_H-128")
    parser.add_argument("--transformer-epochs", type=int, default=4)
    parser.add_argument("--transformer-batch-size", type=int, default=32)
    parser.add_argument("--transformer-max-length", type=int, default=256)
    parser.add_argument("--compliance-transformer-model", default="hfl/chinese-macbert-base")
    parser.add_argument("--compliance-transformer-epochs", type=int, default=1)
    parser.add_argument("--compliance-transformer-batch-size", type=int, default=8)
    parser.add_argument("--compliance-transformer-max-length", type=int, default=384)
    parser.add_argument("--challenge-transformer-model", default="uer/chinese_roberta_L-2_H-128")
    parser.add_argument("--challenge-transformer-epochs", type=int, default=3)
    parser.add_argument("--challenge-transformer-batch-size", type=int, default=32)
    parser.add_argument("--challenge-transformer-max-length", type=int, default=256)
    parser.add_argument("--min-transformer-macro-f1", type=float, default=0.50)
    parser.add_argument("--min-generative-token-f1", type=float, default=0.60)
    parser.add_argument("--seq2seq-model", default="IDEA-CCNL/Randeng-T5-77M-MultiTask-Chinese")
    parser.add_argument("--seq2seq-epochs", type=int, default=1)
    parser.add_argument("--seq2seq-patience", type=int, default=1)
    parser.add_argument("--regqa-seq2seq-epochs", type=int, default=2)
    parser.add_argument("--regqa-seq2seq-patience", type=int, default=2)
    parser.add_argument("--seq2seq-batch-size", type=int, default=8)
    parser.add_argument("--seq2seq-num-beams", type=int, default=1)
    parser.add_argument(
        "--skip-dataset-rebuild",
        action="store_true",
        help="Use --dataset as the current release candidate instead of regenerating it from source/harmonized inputs.",
    )
    parser.add_argument(
        "--include-operation-boundary-holdout",
        action="store_true",
        help="Add the operation-ticket boundary holdout split plus full/natural input transformer checks.",
    )
    args = parser.parse_args()

    run_root = ROOT / args.run_root
    ensure_dirs(run_root)
    steps = build_steps(args)
    statuses = []
    for step in tqdm(steps, desc="SD full pipeline"):
        status = run_step(step, run_root, args.force)
        statuses.append(status)
        copy_result_files(run_root, step.experiment, step.expected_outputs)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "force": args.force,
        "steps_total": len(statuses),
        "steps_passed": sum(status["returncode"] == 0 for status in statuses),
        "statuses": statuses,
    }
    write_json(run_root / "results" / "sd_full_pipeline_manifest.json", manifest)


if __name__ == "__main__":
    main()
