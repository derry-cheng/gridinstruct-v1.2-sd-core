# Technical Validation

Generated: 2026-08-29

## International Rule-Probe Extension

The separate jurisdictional extension passes its local contract gate for 8 official rule cards and 512 directly generated English regulation-QA records. Four cards use NERC TOP-001-6, TOP-002-5, FAC-011-4, and VAR-001-5; four use Articles 18, 25, 33, and 72(3) of Commission Regulation (EU) 2017/1485. Each card has 64 records, one valid rule link per record, an official source URL, a clause locator, typed evidence fields, and direct-English generation metadata. The regenerated output has zero semantic-template tautologies. The gate does not measure expert agreement or semantic legal correctness.

The companion manifest contains two cross-jurisdiction 256/256 folds and two within-jurisdiction 192/64 variant-holdout folds, all with zero train/test ID overlap. A CPU character TF--IDF nearest-neighbour output-copy diagnostic completes all four folds with macro exact match 0.0000, cross-jurisdiction token-F1 0.5303, within-jurisdiction token-F1 0.9756, and structured field scores of 0.0000/1.0000 for the same two fold groups. An explicit-input contract-copy control reaches 1.000 on the five structured fields by construction and is retained only to expose input leakage. These are lexical-transfer diagnostics; they do not establish held-out-jurisdiction semantic generalization. The international review artifact contains 128 stratified records with two blank reviewer assignments per record; it is excluded from metrics and human agreement remains pending.

The current compact review archive was extracted in an isolated temporary directory and replayed. Its 107 dereferenced members, canonical English table, selected receipts, and review-package geometry pass the declared checks; the isolated validator reports zero duplicate-ID and JSONL parse errors. Scenario-link and query-truth checks remain explicitly deferred because the compact archive excludes the raw scenario registry and query-truth ledger.

## Dataset Integrity

| Check | Result |
| --- | ---: |
| Total records | 95479 |
| Validation passed | true |
| Near-duplicate rate (schema validator) | 0.011646540076875543 |
| Schema errors | 0 |
| Duplicate ids | 0 |
| Invalid rule links | 0 |
| Invalid scenario links | 0 |
| Semantic errors | 0 |

The official development projections were re-partitioned by exact normalized
task--instruction--input--output signature. Thirty-eight records moved to the
earliest split; train--validation, train--test, and validation--test exact
signature overlap is now zero. The strict source-group projections received a
corresponding 42-record repair with zero exact signature overlap. Full-table
exact duplicate rate is 990 records after the first occurrence (1.0369%),
below the prespecified 2% diagnostic limit. The schema validator's normalized-
field duplicate rate is reported separately in the table above because it uses
a different normalization contract.

The current in-memory compliance-label audit checks all 24,767
`regulation_compliance_check` records against the released scenario registry.
It reports zero mismatches in the deterministic label, observed-issue list,
issue profile, output label, and English rationale. No derived JSONL stage is
retained for this check; the older staged recomputation receipt is historical
and excluded from the current evidence binding.

An independent registry severity replay recomputes the loading/voltage
severity for 80,385 complete scenario-linked rows with zero mismatches. Eight
rows linked to the registered unsupplied-island boundary carry the explicit
`invalid` marker and remain outside numeric truth. Compact row summaries omit
maximum voltage for some records, so the text-only numeric parser is retained
as an inconclusive diagnostic rather than a physical gate.

The dispatcher-request contract receipt re-renders the typed request semantics
and target slot for all 19,773 dispatcher-intent records without dropping
records or changing targets. Conflicting prompt/input groups decrease from 936
to zero, and official cross-split prompt/input duplicates decrease from 17 to
zero. Rule-evidence pointers are refreshed after the same materialisation.
The resulting request surface explicitly states the operator's requested
dispatch route in natural language; consequently, the closed-label intent
baseline is expected to be highly separable and is reported as a learnability
diagnostic rather than evidence of dispatch competence.

The large instruction-surface OOD view is stored as an ID-only manifest and is
materialised from the canonical table on demand. This avoids a second copy of
the 75,240-row view while preserving the exact record denominator and its
canonical-data hash.

## Evidence Tiers

The full 95,479-row table is covered by machine-checkable schema, provenance, and task-contract checks. Within the auxiliary-decision family, 17,767 rows remain contract-level decision records and 320 rows carry the OPF closed-loop flag, complete executable controls, and post-action replay fields. The fixed-control replay covers 160 registered cases and the independent OPF envelope covers nine selected solves. These populations are separate evidence tiers and are not pooled into a population-wide executable-action claim. The core human-review ledger currently has 0 completed rows out of 1,600 assignments; the international assignment artifact has no human results.

## Simulation Validation

| Check | Result |
| --- | ---: |
| Systems | ieee14, ieee30, ieee57, ieee118 |
| Core-4 N-1 attempts | 1896 |
| Core-4 converged attempts | 1772 |
| Core-4 convergence rate | 0.9345991561181435 |
| Release scenario identifiers | 2822 requested; 2821 converged |

The independent query replay recomputes 2,452 of 2,453 query-linked scenarios
and exactly matches 17,764 of 17,766 query records after the declared
Newton/Iwamoto--Newton deterministic solver policy. Two records associated with
the retained unsupplied-island state remain solver-bound and are excluded from
complete numerical-truth claims.

## Source Traceability

| task_type | records | rule_link_rate | simulation_link_rate | rationale_rate |
| --- | ---: | ---: | ---: | ---: |
| auxiliary_decision | 18087 | 1.0 | 1.0 | 1.0 |
| dispatcher_intent_tool_call | 19773 | 1.0 | 1.0 | 1.0 |
| intelligent_data_query | 17766 | 1.0 | 1.0 | 1.0 |
| operation_ticket_check | 10881 | 1.0 | 0.0 | 1.0 |
| regulation_compliance_check | 24767 | 1.0 | 1.0 | 1.0 |
| regulation_qa | 4205 | 1.0 | 0.0 | 1.0 |

## Expanded Rule Taxonomy

- Status: `pass`.
- Rule cards before expansion: 17.
- Rule cards after expansion: 17.
- Canonical records changed: 95479.
- REG_OVERLOAD_ALERT_002: 30508 linked records.
- REG_OVERLOAD_EMERGENCY_003: 17993 linked records.
- REG_QUERY_AGGREGATION_003: 13122 linked records.
- REG_QUERY_FILTER_002: 17765 linked records.
- REG_SECURITY_REDISPATCH_004: 29646 linked records.
- REG_SWITCHING_MONITORING_003: 10880 linked records.
- REG_SWITCHING_PERMISSION_002: 10880 linked records.
- REG_TOOL_DIAGNOSIS_002: 10295 linked records.
- REG_TOOL_MITIGATION_003: 11879 linked records.
- REG_TOOL_POSTCHECK_004: 34442 linked records.
- REG_VOLTAGE_HIGH_003: 8662 linked records.
- REG_VOLTAGE_LOW_002: 39298 linked records.

## Model Evidence

The current CPU reference uses TF--IDF features with a linear support-vector
classifier for closed-label tasks and nearest-neighbour retrieval for open
targets. On the direct-English official test split, macro-F1 is 0.9987 for
operation-ticket checking, 0.9790 for regulation compliance, and 1.0000 for
dispatcher-intent routing; the corresponding OOD values are 1.0000, 0.8130,
and 1.0000. On the exact instruction-surface test split, the same classifier
obtains 0.9996, 0.8723, and 1.0000, with OOD values 1.0000, 0.7054, and
1.0000. Nearest-neighbour retrieval gives official test exact match 0.8366,
0.8904, and 0.0000 for regulation QA, auxiliary decision drafting, and
intelligent data querying, respectively. These values are task-specific
learnability and structured-output diagnostics; they are not dispatch,
legal-compliance, or electrical-safety certificates.

## External Topology Physical Envelope

- Status: `pass`.
- Candidate attempts in the current seven-network construction: 534.
- Converged source-bound scenarios in the current release: 416.
- External-topology instruction records in the release: 7,792.
- Envelope counts: 128 normal, 112 operational-stress, 71 emergency-stress, and 105 extreme-stress.
- All-family parser/envelope diagnostic: 33 pinned attempts across 11 network families, 26 converged; this is coverage evidence and not a complete-population convergence certificate.

## OPF Closed-Loop Assumption Audit

- Status: `pass`.
- OPF-derived records: 320.
- Unique OPF scenarios: 160.
- Tool-sequence pass rate: 1.0.
- Closed-loop field pass rate: 1.0.

## Hard Boundary Benchmark

- Status: `pass`.
- Model: `char_tfidf_logistic_regression_with_masked_scenarios_and_numbers`.
- dispatcher_intent_tool_call: train 1620, test 1080, macro-F1 1.0, surface purity 1.0.
- operation_ticket_check: train 1620, test 1080, macro-F1 1.0, surface purity 1.0.
- regulation_compliance_check: train 1362, test 906, macro-F1 0.9418138166334309, surface purity 1.0.

## Review Pipeline

- Machine-assisted screen: 548 records, agreement rate 0.7646; this is not human-expert evidence.
- External human review: 800 sampled records, 1,600 assignment rows, 0 completed rows.

## Limits

This release demonstrates schema integrity, traceability, split integrity, simulation linkage, and local trainability.
Stable public archival identifiers must be added before external submission; independent domain review can further strengthen reuse confidence.
