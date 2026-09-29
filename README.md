# GridInstruct

GridInstruct is a topology- and rule-grounded instruction dataset for research on power-grid
dispatch assistance. It connects natural-language requests to reproducible AC operating
states, traceable rule cards, structured queries and tool actions, and task-specific
evaluation evidence. The release covers six registered task families and several
provenance-controlled evaluation regimes.

The current snapshot contains the promoted 95,479-row core table, a separate 512-record
international rule probe, split definitions, validation evidence, jurisdiction-held-out
retrieval diagnostics, independent OPF and network-envelope diagnostics, and the compact
reproduction assets. The GitHub repository is the code and data access point; Zenodo is not
required for the current submission route. The population screening receipt is included, while
raw solver arrays and submission-only material remain outside this repository. Author metadata
and any external review records are maintained with the submission package rather than mixed
with the reproducibility release.
The compact reviewer map is
`release/reviewer_access_manifest_2026-08-13.json`.
The dated remediation ledger is `reports/sd_closeout_status_2026-08-13.json`; it records the
C1--C5 scope without treating external inputs as completed.

## Repository layout

| Path | Purpose |
| --- | --- |
| `scripts/` | Physical construction, dataset stages, validation, baselines, figures, packaging, and the unified pipeline |
| `tests/` | Fast regression checks for the physical-state and input-version contracts |
| `data/` | Canonical tables and official split files after promotion |
| `metadata/` | Schema, dictionaries, lineage, third-party inventory, file manifest, checksums, and evidence bindings |
| `rules/` | Task-oriented rule cards with source metadata |
| `benchmark/` | Task definitions, predictions, per-seed metrics, and aggregate results |
| `reports/` | Physical, query, rule, OPF, split, model, statistical, review, and archive audits |
| `docs/` | Data records, technical validation, reproduction, expert review, availability, licence, and reuse notes |
| `release/` | Compact release boundary, reviewer-access manifest, and deposition checklist |

Submission manuscripts, publication figures, and reviewer packages are deliberately kept
outside Git. The local working copy may contain these materials for editing, but they are
ignored and are not part of the public history. Reproduction code, canonical data, validation
receipts, and release metadata remain versioned under the directories above.

All commands in this repository use the local snapshot. No remote workspace, GPU job, or
untracked checkpoint is required for the published local audits.

## Reproduction boundaries

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python \
  scripts/run_target_hidden_classification.py \
  --output-json reports/target_hidden_classification_replay_20260812.json \
  --output-md reports/target_hidden_classification_replay_20260812.md
```

This command reproduces the 27-row CPU target-hidden classification diagnostic from the
current split hashes. Full population-level physical reconstruction is deliberately not
claimed from the compact snapshot because its raw scenario ledger and solver arrays are
external release items. See `docs/REPRODUCTION.md` and
`reports/evidence_reconciliation_v1.2_sd_core.md` for the exact boundary.

The lexical stress diagnostic can be reproduced from the ID-only component manifests:

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python scripts/run_exact_surface_target_hidden.py
```

It evaluates three closed-label tasks under full-contract, target-hidden, and
instruction-only input profiles. The component split and baseline are intended to expose
lexical dependence; they do not establish semantic independence or physical-label validity.

The repository also contains a deterministic prefix-filter implementation for exact
character-n-gram Jaccard verification:

```bash
/opt/homebrew/Caskroom/miniconda/base/bin/python \
  scripts/create_deterministic_surface_components.py \
  --input data/gridinstruct_v1.2_sd_core_en.jsonl \
  --output-prefix data/v1.2_sd_core_deterministic_surface \
  --report reports/deterministic_surface_component_split_v1.2_sd_core.json
```

Its unit tests certify the prefix-recall property on a finite construction and the
implementation uses compact integer arrays. A full release-scale run is intentionally
not represented as a completed paper result until its runtime and receipt are recorded;
the published MinHash component numbers remain candidate-retrieval diagnostics with the
scope stated in the manuscript.

The current bounded validation additions are reproducible on the local CPU:

```bash
python scripts/run_independent_opf_envelope.py
python scripts/replay_pglib_network_envelope.py
python scripts/create_template_family_holdout.py
python scripts/rebuild_latex_submission_package.py
```

The independent OPF envelope covers nine selected solves; the all-family parser envelope keeps
all 33 attempts in its denominator and reports 26 converged states. The template-family holdout
is an atomic construction-family split, and its bounded character-Jaccard values are descriptive
samples rather than an all-pair semantic certificate.

On the recorded local run, the nine OPF solves used 3.036 s, the 33-state family envelope used
30.514 s, and the 55,421-row template-family split plus CPU baseline used 46.624 s. The TF--IDF
fit is the dominant local step; no remote GPU job is required.

The current instruction-surface seed audit can be reproduced with:

```bash
python scripts/run_current_surface_seed_stability.py
```

It evaluates a stochastic character TF--IDF reference on the frozen surface test and OOD
partitions using five seeds. The output is a stability diagnostic and does not certify
semantic, physical, legal, or operational correctness.

## Fast physical regression

```bash
pytest -q tests/test_generate_grid_scenarios.py
ruff check scripts/generate_grid_scenarios.py tests/test_generate_grid_scenarios.py
```

The full local suite additionally covers PGLib source binding, scenario-link migration,
complete control vectors, OPF security boundaries, transformer contracts, independent-solver
manifests, and deterministic pipeline fingerprints.

The current local regression run is `163 passed, 1 skipped`; the exact count is recorded in
the terminal output and should be refreshed before each release build.

## Evidence policy

- Construction attempts and released converged states use separate denominators.
- Simulation, query, rule, OPF, split, model, figure, and archive claims point to named v1.2
  artifacts and exact hashes.
- Sealed OPF actions must pass both active-demand-only and constant-power-factor load
  uncertainty ledgers, each over the full base-plus-seven-N-1 Cartesian denominator.
- Classification and structured-generation tasks use different, task-compatible metrics.
- Five fixed seeds and grouped bootstrap uncertainty are retained without best-seed filtering.
- Machine-assisted consistency screening is reported separately from independent human
  review and adjudication.
- The final archive is rebuilt deterministically and validated after isolated extraction.

## Scope

The resource supports reproducible research on benchmark-grounded dispatch assistance. It is
not an autonomous control system or evidence of field deployment readiness. Operational use
would require utility-specific rules, live-system integration, cyber-security review,
dispatcher-in-the-loop validation, and regulatory approval.

## Licence and third-party inputs

Project-authored code is declared under MIT and project-authored data artifacts under CC BY
4.0, subject to third-party rights. The release archive excludes installed dependency source,
pretrained model weights, and raw pandapower/MATPOWER case files. Constructor provenance,
installed-input hashes, scholarly attribution, and remaining redistribution actions are
listed in `docs/LICENSES_AND_CITATION.md` and `docs/THIRD_PARTY_ASSETS.md`.

Public repository and archive identifiers, real author metadata, funding and competing-
interest declarations, completed expert review, and upstream redistribution confirmation
must use real external inputs; the automated pipeline does not synthesize them.
