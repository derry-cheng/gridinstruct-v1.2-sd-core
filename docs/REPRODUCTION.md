# Reproduction Notes

GridInstruct v1.2-sd-core provides a bounded local reproduction route for the current compact snapshot:

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python scripts/run_target_hidden_classification.py \
  --output-json reports/target_hidden_classification_replay_20260812.json \
  --output-md reports/target_hidden_classification_replay_20260812.md
```

This command reproduces the 27-row CPU target-hidden diagnostic from the current split hashes. The lexical stress diagnostic is run with:

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python scripts/run_exact_surface_target_hidden.py
```

The historical full-rebuild entry point and remote environment references are retained only in archived run notes; they are not required for the current local evidence package. The compact snapshot does not contain the raw population-level scenario ledger or complete solver arrays, so those physical claims remain external gates.

For a deterministic lexical-stress implementation with exact candidate verification, run:

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python scripts/create_deterministic_surface_components.py \
  --input data/gridinstruct_v1.2_sd_core_en.jsonl \
  --output-prefix data/v1.2_sd_core_deterministic_surface \
  --report reports/deterministic_surface_component_split_v1.2_sd_core.json
```

The prefix-filter recall property is covered by
`tests/test_deterministic_surface_components.py`. The existing manuscript numbers from
the MinHash component reports remain explicitly candidate-index diagnostics until a
release-scale deterministic receipt is available.

The pandapower 3.3.2 case loader explicitly fills a missing `tap_dependency_table=False`
column only for transformer tables that omit the flag. The bundled cases have no
tap-dependent characteristic rows, so this selects the current constant-impedance path.
`tests/test_generate_grid_scenarios.py` checks the normalized input contract and the
declared/effective energization behavior; a five-case equivalence probe confirmed only
machine-precision changes relative to pandapower's deprecated fallback path.

## Runtime environment

The current local baseline environment is the macOS Conda base environment recorded by the
run receipts. It uses CPU or Apple Metal Performance Shaders (MPS) for the lightweight
baselines. `environment.yml` and historical Linux manifests document prior reconstruction
contexts; they are not evidence that the compact snapshot contains a complete remote rebuild.

The optional project environment can be created with:

```bash
conda env create -f environment.yml
conda activate gridinstruct-sd-core
```

Secrets and provider credentials are deliberately absent from the environment specification.
The current release renders English directly from typed scenario and rule contracts. The
release-facing renderer uses the stored variant index and deterministic templates; no external
language service is part of the reproducible path.

## Inputs

Physical inputs are the named benchmark constructors recorded in `metadata/third_party_asset_inventory.json`. Raw installed case files and intermediate construction JSONL stages are not part of this compact local package. The authoritative source and English release files are `data/gridinstruct_v1.2_sd_core.jsonl` and `data/gridinstruct_v1.2_sd_core_en.jsonl`; official, strict source-group, challenge, legacy template-surface, atomic template-family, proxy-reduced, and exact-surface ID-only manifests are under `data/`. Core rule cards are in `rules/regulation_rules.json`. The separate NERC/EU rule probe can be regenerated with `python3 scripts/generate_international_rule_probe.py` and its two leave-one-jurisdiction-out folds with `python3 scripts/create_international_rule_probe_splits.py`. The CPU lexical-transfer diagnostic is reproduced with `python scripts/run_international_rule_probe_baseline.py`; the two-reviewer assignment package is reproduced with `python scripts/create_international_rule_review_assignments.py`. The missing raw scenario and solver ledgers remain explicit external gates.

## Outputs

The current workflow writes reports under `reports/`, benchmark outputs under `benchmark/`, publication and audit figures under `figures/`, and the manuscript under `paper/scientific_data_latex/`. The closeout and reviewer-access manifests bind the current row-level audit, released-field OPF audit, CPU prediction files, exact-surface stress receipt, figures, Draw.io sources, metadata, and LaTeX build. The latest isolated release-archive replay passes payload-hash, evidence-binding, schema, and identifier checks; its validators explicitly defer raw scenario and query-truth checks that are outside the compact archive. Historical summary reports remain available as context; absent raw scenario and human-review artifacts are not inferred from them.

## Baselines

TF-IDF and lightweight linear audits run on CPU. Transformer and sequence-to-sequence baselines use the configured local model files and write prediction JSONL files, JSON/Markdown reports, and logs to their experiment folders. Checkpoint directories from the local training sweep are intentionally pruned after prediction hashes and metric reports are sealed; the pretrained model revision remains an external cache input recorded in each run manifest.

## Validation Gates

The current local gate is `reports/current_quality_snapshot_v1.2_sd_core.json`. It requires schema/link checks, official split-scope checks plus strict five-key isolation, released-field OPF closure, current CPU/MPS baselines, figure generation, and a successful LaTeX build. The retained native-source fixed-control replay passes within its 160-case manifest scope, and the latest isolated archive replay passes its compact-package checks. Submission readiness additionally requires the raw scenario and candidate ledgers, completed double human review and adjudication, final author/funding/competing-interest fields, a persistent data DOI, and confirmation of upstream benchmark-case attribution and redistribution terms. The workflow never converts a prepared review packet, a lexical-transfer diagnostic, or placeholder metadata into completed external evidence.
