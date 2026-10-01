#!/usr/bin/env python3
"""Refresh the existing release manifests after code and report changes."""

import hashlib
import json
import subprocess
from pathlib import Path

from gridinstruct_utils import ROOT, write_json


def record(relative):
    path = ROOT / relative
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": relative, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def main():
    listed = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    files = sorted({name for name in listed if name.endswith(".py") and
                    name.startswith(("scripts/", "tests/"))})
    write_json(ROOT / "metadata/code_manifest.json",
               {"schema_version": "1.0", "file_count": len(files),
                "files": [record(name) for name in files]})
    path = ROOT / "metadata/dataset_metadata.json"
    metadata = json.loads(path.read_text())
    for item in metadata["file_manifest"]:
        if (ROOT / item["path"]).is_file():
            item.update(record(item["path"]))
            item["exists"] = True
    write_json(path, metadata)
    print(json.dumps({"status": "pass", "code_files": len(files),
                      "existing_dataset_entries": len(metadata["file_manifest"])}))


if __name__ == "__main__":
    main()
