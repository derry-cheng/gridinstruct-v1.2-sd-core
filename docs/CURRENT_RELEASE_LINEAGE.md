# Current release lineage and evidence boundary

This note is the authoritative lineage companion for the 29 August 2026 local
Scientific Data rebuild. It binds the English release and all current CPU/MPS
baselines to `metadata/current_revision_manifest_v1.2_sd_core.json`.

The promoted English table contains 95,479 unique records. The row-level audit
replays JSON/schema checks, scenario and tool links, explicit severity fields,
structured-query contracts, source-stage row alignment, record-id disjointness,
and the atomic template-family identifiers. The earlier full legacy
template-surface projections are historical artifacts and are not part of the
current package. The
official split provides record-level separation and registered OOD
scenario/topology holdout; development scenario/source-group overlaps are
retained as diagnostics. The strict source-group split supplies the five-key
provenance isolation used for the stronger stress evaluation. Those gates pass
for the file
whose SHA-256 is
`18a4d36153f9d77ae9b26744978a7bea27138aee048d1b12906335797e908013`.

The official development projections retain 44,000 records after a deterministic
exact-content repair (26,545/3,561/3,894 for train/validation/test); all 38
cross-split exact signature collisions were assigned to the earliest split.
The strict projections retain all 95,479 records after the corresponding 42-record
repair and have zero cross-split exact signatures.

The OPF subset contains 320 records over 160 unique IEEE14/IEEE118 scenarios.
The current embedded closed-loop audit verifies the three-step tool sequence,
control-vector fields, executable-target contract, seven post-action N-1
entries, and two 48-state uncertainty ledgers per action. This is a
record-level construction audit. A retained native-source LightSim2Grid
fixed-control report independently passes all 160 registered cases after being
rebound to `metadata/independent_solver_case_manifest_v1.json`; the raw
scenario registry and construction ledger remain external.

The independent query replay uses the release's deterministic Newton setting
and the declared Iwamoto--Newton fallback for stressed cases. It independently
recomputes 2,452 of 2,453 query-linked scenarios; two records linked to the
registered unsupplied-island state remain solver-bound and are excluded from
complete numerical-truth claims.

The current compliance-label audit recomputes all 24,767 compliance records in
memory against the same scenario registry. Labels, observed-issue lists, issue
profiles, output labels, and English rationales have zero mismatches. The older
staged-recomputation receipt points to a superseded hash and an unshipped
derived table, so it remains historical rather than current evidence.

The dispatcher-request contract repair re-rendered all 19,773 intent records
after detecting conflicting prompt--input groups. The current request surface
has zero conflicting groups and zero official cross-split prompt--input
duplicates, with rule-evidence pointers refreshed against the same rows. The
instruction-surface OOD view is retained as an ID-only manifest and is
materialised from the canonical table on demand.

Current baselines are stored under
`benchmark/current_v1.2_sd_core/`. Classification scores use macro-F1,
accuracy, and balanced accuracy; generation scores use exact match and
token-F1; structured-query and auxiliary-tool scores are reported as separate
schema-contract metrics. The standard, strict, atomic template-family, challenge,
and proxy-reduced reports all record the hashes of their input split files.

Three external submission gates remain open and are intentionally not converted
into claims: the raw scenario and candidate ledgers needed for full-population
reconstruction, completed double-blind expert review (0/1,600 assignment rows),
and a persistent data DOI with final author/funding metadata. The public code
repository and software DOI are already recorded in the manuscript. The release
package therefore supports local reproducibility and review, but it is not
labelled submission-ready until the remaining external artifacts are supplied.
