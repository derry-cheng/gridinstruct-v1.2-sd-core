#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/Caskroom/miniconda/base/bin/python}"

cd "$ROOT_DIR"
"$PYTHON_BIN" scripts/audit_current_sd_release.py
"$PYTHON_BIN" scripts/create_near_neighbor_free_splits.py
"$PYTHON_BIN" scripts/build_opf_candidate_register.py
"$PYTHON_BIN" scripts/build_sd_remediation_manifest.py

echo "Local replay completed. Full split materialization and neural baselines are separate, resource-intensive steps; no public accession is created by this script."
