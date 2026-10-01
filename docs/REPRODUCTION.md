# Reproduction Notes

GridInstruct v1.2-sd-core supports local reproduction of the released
data-generation evidence and lexical baselines. The retained `sd_core` filename
suffix identifies the data version used by the Energy & AI manuscript. Run
commands from the repository root after extracting the data-only release archive.
The repository is public. The current code and data snapshot is release
`v1.2-eai-20261001`; historical `sd_core` filenames retain their data identity.

## Selection and paired diagnostics

The selection-mapping check and paired compliance diagnostic reuse released
data and saved predictions:

```bash
python scripts/audit_opf_selection_mapping.py
python scripts/evaluate_compliance_contrast_pairs.py
```

The first writes `reports/opf_selection_mapping_v1.2_sd_core.json`. It checks
the 35,200 registered IEEE14 candidates, their screening counts, the selected
120-case pool, the 80 IEEE14 plus 80 IEEE118 released cases, and the 320 linked
instruction records. The second writes
`reports/compliance_contrast_pairs_v1.2_sd_core.json`. It measures state
dependence and agreement across request variants without retraining. Pairs
share records; request-variant groups hold the state and action fixed but may
have different operator goals.

The 27-row CPU target-hidden diagnostic can be regenerated with:

```bash
python scripts/run_target_hidden_classification.py \
  --output-json reports/target_hidden_classification_replay_20260812.json \
  --output-md reports/target_hidden_classification_replay_20260812.md
```

The existing lexical-surface diagnostic is regenerated with:

```bash
python scripts/run_exact_surface_target_hidden.py
```

For a deterministic surface-component split with exact candidate verification:

```bash
python scripts/create_deterministic_surface_components.py \
  --input data/gridinstruct_v1.2_sd_core_en.jsonl \
  --output-prefix data/v1.2_sd_core_deterministic_surface \
  --report reports/deterministic_surface_component_split_v1.2_sd_core.json
```

The prefix-filter recall property is tested in
`tests/test_deterministic_surface_components.py`. Existing MinHash component
receipts remain candidate-index diagnostics until a release-scale deterministic
receipt is available.

## Runtime environment

The current local environment is the macOS Conda base environment recorded in
the run receipts. Computation uses CPU or Apple Metal Performance Shaders
(MPS), with no remote GPU requirement. Limit numerical-library threads to at
most 20 when reproducing baseline training:

```bash
export OMP_NUM_THREADS=20
export OPENBLAS_NUM_THREADS=20
export MKL_NUM_THREADS=20
export VECLIB_MAXIMUM_THREADS=20
```

The optional project environment can be created with:

```bash
conda env create -f environment.yml
conda activate gridinstruct-sd-core
```

Provider credentials are absent from the environment specification.
Instructions are rendered directly from typed scenario and rule contracts
using the stored variant index and deterministic templates.

The pandapower 3.3.2 loader supplies `tap_dependency_table=False` when a
transformer table omits the flag. The bundled cases contain no tap-dependent
characteristic rows. `tests/test_generate_grid_scenarios.py` checks this input
contract and energization behavior; the retained equivalence probe reports
machine-precision differences from the deprecated fallback path.

## Inputs and outputs

Physical inputs are the benchmark constructors listed in
`metadata/third_party_asset_inventory.json`. Installed case files and intermediate
construction stages are outside the compact package. The canonical table is
`data/gridinstruct_v1.2_sd_core_en.jsonl`;
`data/gridinstruct_v1.2_sd_core.jsonl` is an identical compatibility copy.
Official and strict-source projections, challenge files, template-family
manifests, proxy-reduced files and surface manifests are under `data/`.
The 75,240-record instruction-surface OOD view is
`data/v1.2_sd_core_instruction_surface_balanced_ood_test_ids.jsonl`.
Baseline scripts materialize those rows from the canonical table.

Core rule cards are in `rules/regulation_rules.json`. The separate NERC/EU
probe uses `python scripts/generate_international_rule_probe.py`; its two
leave-one-jurisdiction-out folds use
`python scripts/create_international_rule_probe_splits.py`. The lexical-transfer
diagnostic uses `python scripts/run_international_rule_probe_baseline.py`.
Human-review materials are local submission files and are not a prerequisite
for this automatic probe.

The compact archive contains the converged scenario registry, OPF source and
selection pools, candidate-screening receipt and released control evidence.
The full construction-attempt ledger and complete raw solver arrays remain
outside the package. Full reconstruction from all original attempts needs
those additional inputs.

Data and numerical audits are under `reports/`; baseline outputs are under
`benchmark/`. Publication artwork and manuscripts remain local and excluded
from Git. The latest isolated archive replay reruns the selection-mapping and
paired-compliance scripts. Its automatic checks do not supply missing
construction attempts or per-record human judgments.

## Baselines

The manuscript's main baseline trains character TF-IDF with a linear support
vector classifier for closed-set tasks. Open-output tasks retrieve the nearest
same-task training answer. Reproduce it with:

```bash
python scripts/run_tfidf_task_baselines.py \
  --output-prefix benchmark/direct_english_tfidf_v1.2_sd_core_leakage_fixed
```

Its report is
`benchmark/direct_english_tfidf_v1.2_sd_core_leakage_fixed_report.json`.
The strict-source report is `benchmark/v1.2_sd_core_strict_tfidf_report.json`;
the surface comparison is
`benchmark/instruction_surface_balanced_v1.2_sd_core_leakage_fixed/tfidf_report.json`.
These are the current report paths. TF-IDF results measure learnability and
lexical dependence. The current pretrained instruction-model comparison is
reproduced by `experiments/revision_20261001/README.md`. It fixes the base
model revision and uses the same candidate scoring before and after adaptation.
Individual predictions and small adapters are included in the data archive;
base-model weights are downloaded separately.

## Validation scope

The release-integrity report checks schema validity, links and the stated
split boundaries. Severity recomputation from stored electrical fields checks
label consistency. The separate query executor replays 17,764 of 17,766
queries; two queries preserve their explicit unsupplied-island boundary.
Native-source fixed-control replay covers 160 registered cases, and the
cross-solver report covers 160 cases and 1,120 registered contingency checks.
Each validation level retains its own denominator and interpretation.

The authors report completed review by five doctoral-level reviewers.
Per-record judgments and agreement statistics are unavailable in this package.
Automatic checks do not measure that human review or establish real closed-loop
dispatch capability. Data and code distribution uses the GitHub release archive;
the current code/data repository is public. Author
declarations and other submission materials remain outside this repository.
