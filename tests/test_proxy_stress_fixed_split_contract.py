from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_proxy_stress_baselines as baseline  # noqa: E402


TASKS = (
    "operation_ticket_check",
    "regulation_compliance_check",
    "dispatcher_intent_tool_call",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _row(task: str, split: str) -> dict:
    row = {
        "id": f"{task}-{split}",
        "task_type": task,
        "task_stage": "real-time",
        "instruction": f"{task} {split}",
        "input": {},
        "output": "compliant",
        "metadata": {"issue_profile": split},
    }
    if task == "operation_ticket_check":
        row["compliance_label"] = "compliant"
        row["input"] = {
            "operation_context": split,
            "equipment_state": f"{split}设备处于运行",
        }
    elif task == "regulation_compliance_check":
        row["compliance_label"] = "compliant"
        row["input"] = {"proposed_action": f"monitor-{split}"}
        row["metadata"]["action_category"] = f"category-{split}"
    else:
        row["intent"] = "query"
        row["input"] = {"utterance": f"utterance-{split}"}
    return row


def test_main_consumes_hash_bound_fixed_english_splits(tmp_path, monkeypatch) -> None:
    canonical = tmp_path / "data/canonical.jsonl"
    english_full = tmp_path / "data/canonical_en.jsonl"
    builder = tmp_path / "scripts/create_proxy_stress_splits.py"
    _write(canonical, [{"id": "canonical"}])
    _write(english_full, [{"id": "canonical"}])
    builder.parent.mkdir(parents=True)
    builder.write_text("# deterministic builder\n", encoding="utf-8")
    manifest = {
        "status": "pass",
        "dataset": "data/canonical.jsonl",
        "dataset_sha256": _sha(canonical),
        "seed": 2041,
        "test_ratio": 0.2,
        "validation_ratio": 0.12,
        "builder": "scripts/create_proxy_stress_splits.py",
        "builder_sha256": _sha(builder),
        "tasks": {},
    }
    consumed_ids = []
    for task in TASKS:
        contract = {"records": {}, "sha256": {}}
        for split in ("train", "validation", "test"):
            source_path = tmp_path / f"data/proxy_stress/{task}/{split}.jsonl"
            english_path = source_path.with_name(f"{split}_en.jsonl")
            source_row = _row(task, split)
            english_row = {**source_row, "instruction": f"English {task} {split}"}
            _write(source_path, [source_row])
            _write(english_path, [english_row])
            contract[split] = str(source_path.relative_to(tmp_path))
            contract["records"][split] = 1
            contract["sha256"][split] = _sha(source_path)
        manifest["tasks"][task] = contract
    manifest_path = tmp_path / "reports/proxy_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_classification(train_rows, eval_rows):
        consumed_ids.extend(row["id"] for row in train_rows + eval_rows)
        predictions = [
            {"id": row["id"], "correct": True}
            for row in eval_rows
        ]
        return {"macro_f1": 1.0, "accuracy": 1.0, "n": len(eval_rows)}, predictions

    monkeypatch.setattr(baseline, "ROOT", tmp_path)
    monkeypatch.setattr(baseline, "classification_report", fake_classification)
    monkeypatch.setattr(
        baseline,
        "split_proxy_groups",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime resplit")),
    )
    monkeypatch.setattr(baseline, "save_figure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(baseline, "write_md", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_proxy_stress_baselines.py",
            "--canonical-dataset",
            "data/canonical.jsonl",
            "--english-dataset",
            "data/canonical_en.jsonl",
            "--split-manifest",
            "reports/proxy_manifest.json",
            "--split-language",
            "en",
            "--output-json",
            "reports/output.json",
            "--output-md",
            "reports/output.md",
            "--figure-dir",
            "figures",
        ],
    )

    baseline.main()

    report = json.loads((tmp_path / "reports/output.json").read_text())
    assert report["status"] == "pass"
    assert report["split_contract"]["split_language"] == "en"
    assert report["split_contract"]["canonical_dataset_sha256"] == _sha(canonical)
    assert all(f"{task}-train" in consumed_ids for task in TASKS)
    assert all(f"{task}-validation" in consumed_ids for task in TASKS)
    assert all(f"{task}-test" in consumed_ids for task in TASKS)
