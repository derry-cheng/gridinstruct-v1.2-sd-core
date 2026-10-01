# GridInstruct v1.2-sd-core public data release

This package description was refreshed on 1 October 2026 for `v1.2-eai-20261001`. The compact archive
contains the dereferenced data and validation members and is rebuilt from the current local files;
its exact member list and receipts are recorded in
`compact_archive_manifest_v1.2_sd_core.json`.

This directory describes the compact data release. The canonical 95,479-record JSONL table is stored once under `data/`; the archive includes official and strict source-group projections and development projections for the instruction-surface split, while larger stress partitions remain ID-only manifests. It also includes the converged scenario registry, bounded OPF evidence, the candidate-to-release mapping, and held-out predictions for paired compliance diagnostics. The two new reports can be recomputed from their included inputs after extraction. Submission manuscripts, figures, and reviewer assignment records are maintained outside Git. The member list is `compact_archive_manifest_v1.2_sd_core.json`. Repository visibility and the download status of the GitHub release must be checked independently of local archive validation.

The machine-readable release reports are under `../reports/`. The archive also
includes raw compact-model predictions, three small trained adapters, fixed
experiment protocols, and the OPF supplement with two full stress ledgers.
Full population-level solver arrays, external human-review records, and final
author and funding declarations remain outside this code/data repository.
The active release uses GitHub and requires no Zenodo DOI.

The replay boundary is deliberately split. Row-level audits and TF--IDF baselines are locally reproducible from the canonical table and split projections. The exact character-surface stress split is recorded in `reports/exact_surface_component_split_v1.2_sd_core.json`, with its target-hidden diagnostic in the adjacent report; both remain lexical diagnostics. The 160 fixed-control AC replays are evidence for the registered case set only; they do not stand in for the missing full-population scenario ledger. `reports/evidence_reconciliation_v1.2_sd_core.md` explains why the legacy construction receipt and current row-integrity receipt are retained separately.

The historical Zenodo route is not part of the active release. The current route is the compact GitHub data release above; it is not a formal DOI-backed data accession.

The compact package can be checked locally with `python3 scripts/validate_release_archive_replay.py --bundle release/GridInstruct_v1.2_sd_core_data_only.tar.gz`. The replay verifies archive safety, the 95,479-record English table, duplicate identifiers, and the selected direct-English and seed-stability receipts. Raw scenario truth and independent human-review ledgers remain outside its declared scope.

The former full-bundle manifest route is not part of the current reviewer package. The compact archive and its adjacent SHA-256 sidecar, together with the JSON member manifest, are the only current access artifacts; raw ledgers remain in the local evidence tree for bounded audit work. The full-bundle builder is retained as historical code and is not a current release path.
