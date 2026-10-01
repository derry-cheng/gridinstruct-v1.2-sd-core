# Current release lineage and evidence boundary

This note describes the GridInstruct data-generation and evaluation evidence
used by the Energy & AI manuscript. The current revision is recorded in
`metadata/current_revision_manifest_v1.2_sd_core.json`. The retained `sd_core`
filename suffix identifies the data version and does not specify the target journal.

The canonical table contains 95,479 unique records. The row-level audit checks
JSON/schema validity, scenario and tool links, explicit severity fields,
structured-query contracts, source-stage alignment, record-id separation and
template-family identifiers. For 80,385 scenario-linked records with complete
electrical fields, severity labels are recomputed from stored voltages and
loadings. Eight records preserve the explicitly marked unsupplied-island
boundary. These checks establish data and label consistency.

The official split provides record-level separation and registered OOD
scenario/topology holdout. Development scenario/source-group overlaps are
reported in the audit. The strict-source split independently separates source
groups, parent records, scenarios, simulation cases and operation groups.
Surface and template-family splits address different forms of text reuse.
No one split establishes separation across every dimension. The current
canonical table is bound by SHA-256
`18a4d36153f9d77ae9b26744978a7bea27138aee048d1b12906335797e908013`.

The official development projections retain 34,000 records after a
deterministic exact-content repair (26,545/3,561/3,894 for
train/validation/test); 38 cross-split exact signatures were assigned to the
earliest split. The official OOD view contains 61,479 records. The strict
projections retain all 95,479 records after the corresponding 42-record repair
and have zero cross-split exact signatures.

The OPF subset contains 320 records over 160 unique IEEE14/IEEE118 scenarios.
`reports/opf_selection_mapping_v1.2_sd_core.json` connects the 35,200
registered IEEE14 candidates to 30,800 converged cases, 8,508 eligible cases,
136 robust-screened cases and 120 accepted cases. Screening stopped after
the acceptance target, leaving 8,372 eligible cases unscreened. The released
subset uses 80 scenarios from this 120-case IEEE14 pool and 80 scenarios
from the separate IEEE118 source pool. Each released scenario has two
instruction records.

The embedded OPF audit checks the three-step tool sequence, control-vector
fields, executable-target contract, seven post-action N-1 entries and two
48-state uncertainty ledgers per action. A retained native-source LightSim2Grid
fixed-control report passes its 160 registered replay cases after being bound
to `metadata/independent_solver_case_manifest_v1.json`. Cross-solver checks
cover these 160 cases and 1,120 registered contingency checks. These results
establish numerical replay and label agreement within the registered scope.
They do not measure successful generation for an arbitrary new operating state.
The compact archive includes the converged scenario registry, candidate-selection
pools and screening receipt; full construction-attempt and raw solver-array
archives remain outside this package.

The query replay uses the release's deterministic Newton setting and the
declared Iwamoto-Newton fallback for stressed cases. Its separate parser and
query executor recompute 2,452 of 2,453 query-linked scenarios and check
17,764 of 17,766 query outputs by complete-set equality. Two records linked
to the registered unsupplied-island state remain explicitly incomplete.

The compliance-label audit recomputes all 24,767 compliance records against
the same scenario registry. Labels, observed-issue lists, issue profiles,
output labels and rationales have zero mismatches. The older staged receipt
references a superseded file and remains historical. The dispatcher-request
repair covers all 19,773 intent records; the current request surface has zero
conflicting prompt/input groups and zero official cross-split prompt/input
duplicates. The instruction-surface OOD view is an ID-only manifest that is
materialized from the canonical table on demand.

The main lexical baseline is recorded in
`benchmark/direct_english_tfidf_v1.2_sd_core_leakage_fixed_report.json`.
The strict-source comparison is in
`benchmark/v1.2_sd_core_strict_tfidf_report.json`, and the instruction-surface
comparison is in
`benchmark/instruction_surface_balanced_v1.2_sd_core_leakage_fixed/tfidf_report.json`.
Classification scores use macro-F1, accuracy and balanced accuracy.
Nearest-neighbor output retrieval uses exact match and token-F1; the separate
structured-query and auxiliary-tool reports measure task-contract validity.
These baselines assess learnability and input dependence. The separate
`reports/local_instruction_transfer_20261001.json` records the pinned
SmolLM2-135M-Instruct comparison: mean macro-F1 increases from 0.161 to
0.823 (standard deviation 0.046 across three seeds) on 288 label-balanced
strict-test records over 164 scenarios. It evaluates synthetic compliance
labels by fixed candidate scoring, with separate state and request probes.

`reports/compliance_contrast_pairs_v1.2_sd_core.json` reuses saved predictions
to compare identical requests across different states and request variants
within a fixed scenario/action/label group. Operator goals can differ in the
latter groups, and pairs share records. The paired rates are descriptive
diagnostics; they do not establish independent-trial confidence intervals or
pure paraphrase invariance.

The authors confirm that five doctoral-level reviewers completed external
review. Per-record judgments, reviewer assignments and an agreement statistic
are unavailable in the release package. This is an author-attested completed
review; published quantitative validation rests on the reported automatic
checks and numerical replay.

Data and core code are distributed through the versioned GitHub repository and
its data-only release archive. The repository is public; tag
`v1.2-eai-20261001` identifies this code and data revision. This distribution route does not require a
Zenodo or software DOI. Manuscripts, editable artwork and human-review
materials remain local and excluded from Git history. The package supports
the stated local reproduction scope and bounded instruction-model training
comparison. Per-record human agreement and live operational validity remain
outside the available quantitative evidence.
