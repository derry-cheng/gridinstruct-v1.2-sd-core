#!/usr/bin/env python3
"""Build reproducibility manifests and bind all final evidence to current inputs."""

from __future__ import annotations

import json

from evidence_contract import build_evidence_binding_manifest
from gridinstruct_utils import ROOT, write_json


def main() -> None:
    manifest = build_evidence_binding_manifest(ROOT)
    write_json(ROOT / "metadata/evidence_binding_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if manifest["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
