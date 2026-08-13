#!/usr/bin/env python3
"""Run the English derived-data pipeline for GridInstruct v1.2 SD core."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_step(name: str, cmd: list[str], log_dir: Path) -> dict:
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{name}.stdout.log"
    stderr_path = log_dir / f"{name}.stderr.log"
    started = datetime.now(timezone.utc)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=stdout, stderr=stderr, text=True, check=False)
    finished = datetime.now(timezone.utc)
    return {
        "name": name,
        "cmd": cmd,
        "returncode": proc.returncode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "stdout": str(stdout_path.relative_to(ROOT)),
        "stderr": str(stderr_path.relative_to(ROOT)),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default="/data/anaconda3/envs/power/bin/python")
    parser.add_argument("--input", default="data/gridinstruct_v1.2_sd_core.jsonl")
    parser.add_argument("--output", default="data/gridinstruct_v1.2_sd_core_en.jsonl")
    parser.add_argument("--cache", default="metadata/translation_cache_v1.jsonl")
    parser.add_argument("--experiment-dir", default="experiments/sd_core_full_pipeline/10_english_translation")
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=75)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-records", type=int, default=None)
    parser.add_argument("--skip-translation", action="store_true")
    args = parser.parse_args()

    py = args.python
    experiment_dir = ROOT / args.experiment_dir
    log_dir = experiment_dir / "logs"
    result_dir = experiment_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)

    steps: list[tuple[str, list[str]]] = []
    if not args.skip_translation:
        translate_cmd = [
            py,
            "scripts/translate_sd_core_to_english.py",
            "--input",
            args.input,
            "--output",
            args.output,
            "--cache",
            args.cache,
            "--report-json",
            "reports/english_translation_v1.2_sd_core.json",
            "--report-md",
            "reports/english_translation_v1.2_sd_core.md",
            "--batch-size",
            str(args.batch_size),
            "--workers",
            str(args.workers),
            "--timeout",
            str(args.timeout),
            "--max-retries",
            str(args.max_retries),
            "--shuffle-missing",
        ]
        if args.max_records is not None:
            translate_cmd.extend(["--max-records", str(args.max_records)])
        steps.append(("01_translate_full", translate_cmd))

    steps.extend(
        [
            (
                "01b_progress_audit",
                [
                    py,
                    "scripts/audit_english_translation_progress.py",
                    "--input",
                    args.input,
                    "--cache",
                    args.cache,
                    "--log",
                    f"{args.experiment_dir}/logs/01_translate_full.stderr.log",
                    "--output-json",
                    "reports/english_translation_progress_v1.2_sd_core.json",
                    "--output-md",
                    "reports/english_translation_progress_v1.2_sd_core.md",
                ],
            ),
            (
                "02_audit_full",
                [
                    py,
                    "scripts/audit_english_translation.py",
                    "--source",
                    args.input,
                    "--translated",
                    args.output,
                    "--report-json",
                    "reports/english_translation_audit_v1.2_sd_core.json",
                    "--report-md",
                    "reports/english_translation_audit_v1.2_sd_core.md",
                ],
            ),
            (
                "03_validate_full",
                [
                    py,
                    "scripts/validate_dataset.py",
                    "--input",
                    args.output,
                    "--report-prefix",
                    "reports/data_validation_v1.2_sd_core_en",
                ],
            ),
            (
                "04_materialize_splits",
                [
                    py,
                    "scripts/materialize_english_splits.py",
                    "--translated-full",
                    args.output,
                    "--report-json",
                    "reports/english_split_materialization_v1.2_sd_core.json",
                    "--report-md",
                    "reports/english_split_materialization_v1.2_sd_core.md",
                ],
            ),
            (
                "05_audit_splits",
                [
                    py,
                    "scripts/audit_english_splits.py",
                    "--report-json",
                    "reports/english_split_audit_v1.2_sd_core.json",
                    "--report-md",
                    "reports/english_split_audit_v1.2_sd_core.md",
                ],
            ),
        ]
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_dir": args.experiment_dir,
        "steps": [],
        "status": "running",
    }
    status_path = result_dir / "pipeline_status.json"
    write_json(status_path, report)

    for name, cmd in steps:
        step_report = run_step(name, cmd, log_dir)
        report["steps"].append(step_report)
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        report["status"] = "failed" if step_report["returncode"] else "running"
        write_json(status_path, report)
        if step_report["returncode"]:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise SystemExit(step_report["returncode"])

    report["status"] = "pass"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(status_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
