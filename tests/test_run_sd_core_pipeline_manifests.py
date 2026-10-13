from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_sd_core_full_pipeline as pipeline  # noqa: E402


def test_run_step_rejects_input_drift_during_execution(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    monkeypatch.setattr(pipeline.platform, "platform", lambda: "fixture-platform")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "step.py").write_text(
        "print('v1')\n",
        encoding="utf-8",
    )
    step = pipeline.Step(
        "drifting",
        "audit",
        ["python", "scripts/step.py"],
        ["result.json"],
        input_paths=["scripts/step.py"],
    )

    def fake_run(*_args, **_kwargs):
        (tmp_path / "result.json").write_text("{}\n", encoding="utf-8")
        (tmp_path / "scripts" / "step.py").write_text(
            "print('v2')\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(step.cmd, 0)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    try:
        pipeline.run_step(
            step,
            tmp_path / "experiments" / "run",
            force=True,
            force_light=False,
        )
    except RuntimeError as exc:
        assert "changed during execution" in str(exc)
    else:
        raise AssertionError("run_step accepted outputs generated during input drift")

    status = json.loads(
        (tmp_path / "experiments/run/audit/results/drifting_status.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["returncode"] == 1
    assert status["input_drift_during_execution"] is True


def test_partial_invocations_preserve_full_manifest_and_aggregate_steps(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    run_root = tmp_path / "experiments" / "run"
    first_results = run_root / "00_physical_sources" / "results"
    second_results = run_root / "01_dataset_integrity" / "results"
    first_results.mkdir(parents=True)
    second_results.mkdir(parents=True)
    pipeline.write_json(
        first_results / "first_status.json",
        {"name": "first", "status": "pass", "returncode": 0},
    )
    full_manifest = run_root / "results" / "sd_core_full_pipeline_manifest.json"
    pipeline.write_run_manifests(
        run_root,
        {"status": "complete", "only_step": None, "start_at": None},
        full_run_requested=True,
    )
    original_full = full_manifest.read_bytes()

    pipeline.write_json(
        second_results / "second_status.json",
        {"name": "second", "status": "pass", "returncode": 0},
    )
    pipeline.write_run_manifests(
        run_root,
        {"status": "complete", "only_step": "second", "start_at": None},
        full_run_requested=False,
    )

    assert full_manifest.read_bytes() == original_full
    ledger = json.loads(
        (run_root / "results" / "sd_core_run_ledger.json").read_text(encoding="utf-8")
    )
    assert ledger["status"] == "pass"
    assert ledger["step_count"] == 2
    assert {row["name"] for row in ledger["steps"]} == {"first", "second"}
    assert ledger["invocation_count"] == 2
