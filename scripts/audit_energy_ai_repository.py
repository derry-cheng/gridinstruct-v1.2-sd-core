#!/usr/bin/env python3
"""Audit the GitHub-facing data and code boundary for the Energy & AI manuscript.

The report deliberately distinguishes local repository integrity from external
publication requirements. It does not treat a historical archive identifier as
evidence that the current data release is available.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    data_path = ROOT / "data/gridinstruct_v1.2_sd_core_en.jsonl"
    manifest_path = ROOT / "metadata/code_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_files = [ROOT / item["path"] for item in manifest.get("files", [])]
    missing_code = [str(path.relative_to(ROOT)) for path in manifest_files if not path.is_file()]
    compile_run = subprocess.run(
        ["/opt/homebrew/Caskroom/miniconda/base/bin/python", "-m", "compileall", "-q", "scripts"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    pytest_run = subprocess.run(
        ["/opt/homebrew/Caskroom/miniconda/base/bin/python", "-m", "pytest", "-q"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**__import__("os").environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": "https://github.com/derry-cheng/gridinstruct-v1.2-sd-core",
        "data": {
            "path": str(data_path.relative_to(ROOT)),
            "present": data_path.is_file(),
            "bytes": data_path.stat().st_size if data_path.is_file() else None,
            "sha256": sha256(data_path) if data_path.is_file() else None,
        },
        "code_manifest": {
            "declared_files": len(manifest_files),
            "missing_files": missing_code,
            "all_declared_files_present": not missing_code,
        },
        "syntax_check": {"returncode": compile_run.returncode, "stderr": compile_run.stderr[-2000:]},
        "regression_suite": {
            "returncode": pytest_run.returncode,
            "summary": pytest_run.stdout.strip().splitlines()[-1] if pytest_run.stdout.strip() else "",
            "stderr": pytest_run.stderr[-2000:],
        },
        "release_boundary": {
            "data_and_code_published_by_github": True,
            "zenodo_required": False,
            "raw_population_solver_ledgers": "external_gate",
            "completed_human_review": "external_gate",
        },
        "status": "pass" if data_path.is_file() and not missing_code and compile_run.returncode == 0 and pytest_run.returncode == 0 else "fail",
    }
    out_json = ROOT / "reports/energy_ai_repository_audit_20260929.json"
    out_md = ROOT / "reports/energy_ai_repository_audit_20260929.md"
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_md.write_text(
        "# Energy & AI repository audit\n\n"
        f"- Status: **{report['status']}**\n"
        f"- Canonical data SHA-256: `{report['data']['sha256']}`\n"
        f"- Declared code files present: {report['code_manifest']['all_declared_files_present']}\n"
        f"- Syntax check return code: {compile_run.returncode}\n"
        f"- Regression suite: {report['regression_suite']['summary']}\n\n"
        "The GitHub repository is the release boundary for this Energy & AI version. "
        "Zenodo is not required. Raw population-level solver ledgers and completed human review "
        "remain explicitly external evidence gates.\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
