# Technical Validation

Generated: 2026-08-18

## International Rule-Probe Extension

The separate jurisdictional extension passes its local contract gate for 8 official rule cards and 512 directly generated English regulation-QA records. Four cards use NERC TOP-001-6, TOP-002-5, FAC-011-4, and VAR-001-5; four use Articles 18, 25, 33, and 72(3) of Commission Regulation (EU) 2017/1485. Each card has 64 records, one valid rule link per record, an official source URL, a clause locator, typed evidence fields, and direct-English generation metadata. The gate does not measure expert agreement or semantic legal correctness.

The companion leave-one-jurisdiction-out manifest contains two disjoint 256/256 folds. A CPU character TF--IDF nearest-neighbour output-copy diagnostic completes both folds with macro exact match 0.0000, macro token-F1 0.3532, and mean nearest-neighbour cosine similarity 0.7861. This is a lexical-transfer diagnostic; it does not establish held-out-jurisdiction semantic generalization. The review package contains 128 stratified records with two blank reviewer assignments per record, so human agreement remains pending.

The final release-candidate archive was extracted in an isolated temporary directory and replayed. All 959 payload hashes and the scoped evidence bindings match; the two canonical validators report zero schema, duplicate-ID, and invalid-rule-link errors. Scenario-link and query-truth checks remain explicitly deferred because the compact archive excludes the raw scenario registry and query-truth ledger.

## Dataset Integrity

| Check | Result |
| --- | ---: |
| Total records | 95479 |
| Validation passed | true |
| Near-duplicate rate | 0.0036028864986017866 |
| Schema errors | 0 |
| Duplicate ids | 0 |
| Invalid rule links | 0 |
| Invalid scenario links | 0 |
| Semantic errors | 0 |

## Simulation Validation

| Check | Result |
| --- | ---: |
| Systems | ieee14, ieee30, ieee57, ieee118 |
| Total scenarios | 3034 |
| Converged scenarios | 2630 |
| Convergence rate | 0.8668424522083059 |

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

- TF-IDF baseline: test macro score None, OOD macro score None.
- Transformer `dispatcher_intent_tool_call`: test macro F1 1.0.
- Transformer `operation_ticket_check`: test macro F1 1.0.
- Transformer `regulation_compliance_check`: test macro F1 0.4980109492414864.
- Transformer `regulation_compliance_check`: test macro F1 0.9881287661660919.
- Transformer `regulation_compliance_check`: test macro F1 0.6449108106721044.
- Transformer `regulation_compliance_check`: test macro F1 0.920374867245268.
- Pretrained seq2seq generation (`auxiliary_decision`): test token F1 None.
- Pretrained seq2seq generation (`intelligent_data_query`): test token F1 None.
- Pretrained seq2seq generation (`regulation_qa`): test token F1 0.8072746532282353.

## External Topology Physical Envelope

- Status: `pass`.
- Stress-source scenarios: 126.
- Converged stress-source scenarios: 117.
- Formal external instruction records in release: 2768.
- Formal external scenarios in release: 173.
- emergency-stress: 62.
- extreme-stress: 40.
- operational-stress: 15.
- solver-not-converged: 9.

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

- No LLM review report was provided for this refresh run.

## Limits

This release demonstrates schema integrity, traceability, split integrity, simulation linkage, and local trainability.
Stable public archival identifiers must be added before external submission; independent domain review can further strengthen reuse confidence.
