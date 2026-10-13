#!/usr/bin/env python3
"""Capture the current formal reconstruction runtime from environment.yml."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from gridinstruct_utils import ROOT, ensure_dirs, write_json


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def direct_pip_requirements(environment: dict[str, Any]) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for dependency in environment.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        for specification in dependency.get("pip") or []:
            parts = str(specification).split("==", 1)
            if len(parts) != 2 or not all(part.strip() for part in parts):
                raise ValueError(
                    f"direct pip dependency is not exactly pinned: {specification}"
                )
            name, expected_version = (part.strip() for part in parts)
            if name in requirements:
                raise ValueError(f"duplicate direct pip dependency: {name}")
            requirements[name] = expected_version
    if not requirements:
        raise ValueError("environment.yml has no direct pip packages")
    return requirements


def direct_pip_packages(environment: dict[str, Any]) -> list[str]:
    return list(direct_pip_requirements(environment))


def direct_runtime_requirements(environment: dict[str, Any]) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for dependency in environment.get("dependencies") or []:
        if not isinstance(dependency, str):
            continue
        parts = dependency.split("=", 1)
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError(
                f"direct runtime dependency is not exactly pinned: {dependency}"
            )
        name, expected_version = (part.strip() for part in parts)
        requirements[name] = expected_version
    if not {"python", "pip"} <= set(requirements):
        raise ValueError("environment.yml must pin python and pip")
    return requirements


def gpu_runtime() -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "device_count": 0,
        "visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
    }
    try:
        import torch

        available = bool(torch.cuda.is_available())
        count = int(torch.cuda.device_count()) if available else 0
        result.update(
            {
                "available": available,
                "device_count": count,
                "torch_cuda_version": torch.version.cuda,
                "device_names": [
                    torch.cuda.get_device_name(index) for index in range(count)
                ],
            }
        )
    except Exception as exc:  # pragma: no cover - host-specific diagnostic
        result["probe_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", default="environment.yml")
    parser.add_argument(
        "--output",
        default="metadata/formal_runtime_environment.json",
    )
    args = parser.parse_args()

    environment_path = (ROOT / args.environment).resolve()
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    runtime_requirements = direct_runtime_requirements(environment)
    requirements = direct_pip_requirements(environment)
    package_names = list(requirements)
    versions: dict[str, str | None] = {}
    missing: list[str] = []
    for name in package_names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
            missing.append(name)
    mismatched = {
        name: {
            "expected": requirements[name],
            "actual": versions[name],
        }
        for name in package_names
        if versions[name] is not None and versions[name] != requirements[name]
    }
    runtime_versions = {
        "python": platform.python_version(),
        "pip": importlib.metadata.version("pip"),
    }
    runtime_mismatched = {
        name: {
            "expected": runtime_requirements[name],
            "actual": runtime_versions[name],
        }
        for name in runtime_requirements
        if runtime_versions.get(name) != runtime_requirements[name]
    }

    output = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "role": "formal_v17_reconstruction_runtime",
        "status": (
            "pass"
            if not missing and not mismatched and not runtime_mismatched
            else "fail"
        ),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "platform_string": platform.platform(),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "expected_runtime_versions": runtime_requirements,
        "runtime_version_mismatches": runtime_mismatched,
        "gpu_runtime": gpu_runtime(),
        "direct_package_versions": versions,
        "expected_direct_package_versions": requirements,
        "missing_direct_packages": missing,
        "mismatched_direct_packages": mismatched,
        "environment_specification": {
            "path": str(environment_path.relative_to(ROOT)),
            "sha256": sha256(environment_path),
        },
        "implementation": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "notes": [
            "This file is regenerated inside the formal Linux reconstruction runtime.",
            "The complete transitive package snapshot is stored separately in metadata/environment-freeze.txt.",
            "Credentials, tokens, and proxy values are intentionally excluded.",
        ],
    }
    output_path = (ROOT / args.output).resolve()
    ensure_dirs(output_path.parent)
    write_json(output_path, output)
    if missing or mismatched or runtime_mismatched:
        problems = []
        if missing:
            problems.append("missing=" + ",".join(missing))
        if mismatched:
            problems.append(
                "mismatched="
                + ",".join(
                    f"{name}:{row['actual']}!={row['expected']}"
                    for name, row in mismatched.items()
                )
            )
        if runtime_mismatched:
            problems.append(
                "runtime_mismatched="
                + ",".join(
                    f"{name}:{row['actual']}!={row['expected']}"
                    for name, row in runtime_mismatched.items()
                )
            )
        raise RuntimeError(
            "formal runtime does not match environment.yml: " + "; ".join(problems)
        )


if __name__ == "__main__":
    main()
