#!/usr/bin/env python3
"""Capture deterministic code and runtime manifests before lineage binding."""

from __future__ import annotations

from build_release_bundle import write_reproducibility_manifests


def main() -> None:
    write_reproducibility_manifests()


if __name__ == "__main__":
    main()
