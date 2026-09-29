# GridInstruct v1.2-sd-core public data release

This package description was refreshed on 30 August 2026. The compact archive
contains the dereferenced data and validation members and is rebuilt from the current local files;
its exact member list and receipts are recorded in
`compact_archive_manifest_v1.2_sd_core.json`.

This directory contains the compact data-release description. The canonical 95,479-record JSONL table is stored once under `data/`; the archive includes the official and strict source-group English projections and the development projections for the instruction-surface split, while near-neighbour and large surface-stress partitions remain ID-only manifests. The converged scenario registry and bounded OPF evidence tables are included so the stated physical checks can be replayed after extraction. Submission manuscripts, figures, and reviewer assignment records are maintained outside Git. The current member list is `compact_archive_manifest_v1.2_sd_core.json`. A compact data-only archive is available through the public GitHub release; its SHA-256 companion is uploaded beside it. This route is public, but it is not a formal DOI-backed data accession.

The machine-readable release gate reports are under `../reports/`. Raw scenario and candidate ledgers, external reviews, the formal data DOI, and final author, CRediT, and funding metadata remain outside this code/data repository.

The replay boundary is deliberately split. Row-level audits and TF--IDF baselines are locally reproducible from the canonical table and split projections. The exact character-surface stress split is recorded in `reports/exact_surface_component_split_v1.2_sd_core.json`, with its target-hidden diagnostic in the adjacent report; both remain lexical diagnostics. The 160 fixed-control AC replays are evidence for the registered case set only; they do not stand in for the missing full-population scenario ledger. `reports/evidence_reconciliation_v1.2_sd_core.md` explains why the legacy construction receipt and current row-integrity receipt are retained separately.

The historical Zenodo route is not part of the active release. The current route is the compact GitHub data release above; it is not a formal DOI-backed data accession.

The compact package can be checked locally with `python3 scripts/validate_release_archive_replay.py --bundle release/GridInstruct_v1.2_sd_core_data_only.tar.gz`. The replay verifies archive safety, the 95,479-record English table, duplicate identifiers, and the selected direct-English and seed-stability receipts. Raw scenario truth and independent human-review ledgers remain outside its declared scope.

The former full-bundle manifest route is not part of the current reviewer package. The compact archive and its adjacent SHA-256 sidecar, together with the JSON member manifest, are the only current access artifacts; raw ledgers remain in the local evidence tree for bounded audit work. The full-bundle builder is retained as historical code and is not a current release path.
