# Current release lineage and evidence boundary

This note is the authoritative lineage companion for the 28 August 2026 local
Scientific Data rebuild. It binds the English release and all current CPU/MPS
baselines to `metadata/current_revision_manifest_v1.2_sd_core.json`.

The promoted English table contains 95,479 unique records. The row-level audit
replays JSON/schema checks, scenario and tool links, explicit severity fields,
structured-query contracts, source-stage row alignment, record-id disjointness,
and the declared legacy template-surface and atomic template-family keys. The
official split provides record-level separation and registered OOD
scenario/topology holdout; development scenario/source-group overlaps are
retained as diagnostics. The strict source-group split supplies the five-key
provenance isolation used for the stronger stress evaluation. Those gates pass
for the file
whose SHA-256 is
`882dc4960dcf14d5b6e6465ced25051c860f1b360ffdb58770c3b68d6e7b60b3`.

The OPF subset contains 320 records over 160 unique IEEE14/IEEE118 scenarios.
The current embedded closed-loop audit verifies the three-step tool sequence,
control-vector fields, executable-target contract, seven post-action N-1
entries, and two 48-state uncertainty ledgers per action. This is a
record-level construction audit. A retained native-source LightSim2Grid
fixed-control report independently passes all 160 registered cases after being
rebound to `metadata/independent_solver_case_manifest_v1.json`; the raw
scenario registry and construction ledger remain external.

Current baselines are stored under
`benchmark/current_v1.2_sd_core/`. Classification scores use macro-F1,
accuracy, and balanced accuracy; generation scores use exact match and
token-F1; structured-query and auxiliary-tool scores are reported as separate
schema-contract metrics. The standard, strict, legacy template-surface, atomic
template-family, challenge,
and proxy-reduced reports all record the hashes of their input split files.

Three external submission gates remain open and are intentionally not converted
into claims: the raw scenario and candidate ledgers needed for full-population
reconstruction, completed double-blind expert review (0/1,600 assignment rows),
and a persistent data DOI with final author/funding metadata. The public code
repository and software DOI are already recorded in the manuscript. The release
package therefore supports local reproducibility and review, but it is not
labelled submission-ready until the remaining external artifacts are supplied.
