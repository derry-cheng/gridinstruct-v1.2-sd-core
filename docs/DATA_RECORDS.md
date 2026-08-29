# Data Records

Generated: 2026-08-29

GridInstruct v1.2 contains 95,479 instruction records. Records are stored as UTF-8 JSON Lines and validated against `metadata/schema.json`. The release-facing English table is rendered directly from typed scenario and rule contracts.

## Core Files

| path | records | bytes | sha256 |
| --- | ---: | ---: | --- |
| data/gridinstruct_v1.2_sd_core_en.jsonl | 95479 | 593841086 | 18a4d36153f9d77ae9b26744978a7bea27138aee048d1b12906335797e908013 |
| data/v1.2_sd_core_train_en.jsonl | 26545 | 151419585 | 2fffd5d0b7d1cb417a4dd919af39aba859f736d3f2da2d3f52340169388b3ac9 |
| data/v1.2_sd_core_validation_en.jsonl | 3561 | 20200172 | 03a4f3126a1ae181524610cf6ef6efd60774f61b78a31e8fb9e9d232a3abc06f |
| data/v1.2_sd_core_test_en.jsonl | 3894 | 21668841 | 83576760eb091531d08ae6a7b6bf95d52b8266a6e5fe4aa9c2875c3eccd56110 |
| data/v1.2_sd_core_ood_test_en.jsonl | 61479 | 417267736 | a7a75d1a0551cfe8f688b90af32b535cb54767944fbf319a4a8c53e64b2adb17 |
| data/v1.2_sd_core_strict_train.jsonl | 76378 | 476844445 | 4ea2d05b59ff253fd44c00f8f69a978159b7548b72a5016822c0fcc7520f0153 |
| data/v1.2_sd_core_strict_validation.jsonl | 9558 | 57778166 | 52f5da72bf071c27bc8d842de55f8a46d2ed5cb4d33f567b9e4ce3d597cc7152 |
| data/v1.2_sd_core_strict_test.jsonl | 9543 | 59218475 | 753734fe89bf899592591cf863b56bfcbc6219b5735aaadd4c5980dbcb2458ed |
| metadata/schema.json | None | 4060 | 344efb82e99b2c2716f9cecfe8fc5c0f71aa7bc465dee5dc3b03aa95a7e8cfe5 |
| metadata/task_taxonomy.json | None | 243 | 6dc8399ae3491756bf088f72eb58cbdcb7a95e4673f59d8b8f5f60a0acde0afa |
| metadata/data_dictionary.csv | None | 5627 | 37d473c9bbd5c29fc2a9811bde90e377e9ec5c2b2a92b312c407943373b30ac9 |
| rules/regulation_rules.json | 17 | 37019 | 0c834cdf81dc0b10e76d525845111dc5e845ac8a559ef9c21bc02aeb0b4c1a83 |

## International Rule-Probe Extension

The core table keeps its original 95,479 records and 17 domestic rule cards. A separate extension contains 512 directly generated English regulation-QA records linked to four NERC standards and four European system-operation articles.

| path | records/cards | sha256 |
| --- | ---: | --- |
| data/international_rule_probe_v1.jsonl | 512 records | ffd3ce089f704d1f55341a66ce355cfc5c0a739e8c90024e40d1590c283cc85d |
| rules/international_rule_profiles.json | 8 cards | 63d702b1cf5a8881d8fa008e0f99f1cb2e2b0f94ce990e7cfcbe4b400826f793 |
| metadata/international_rule_profile_matrix.csv | 8 rows | 15f8934dbce8d393fbb12ac816507f1d9c5791a71c5fac9f10e1bccc428046c8 |
| metadata/international_rule_probe_splits_v1.json | four leave-one-jurisdiction and variant-holdout folds | 848af2e7204c64df36ff0e50d4c6e504fe2a0e0ae24653a6c9883340b01c5903 |
| reports/international_rule_probe_v1.json | validation receipt | 8fc8b60b087656b5fb058fa065824488318ced20c8ca75adecf792081d1bc69e |
| reports/international_rule_probe_splits_v1.json | split receipt | 848af2e7204c64df36ff0e50d4c6e504fe2a0e0ae24653a6c9883340b01c5903 |
| benchmark/international_rule_probe_v1/nearest_neighbor_report.json | four-fold CPU retrieval diagnostic | 5e366e7ccf78cb069ddf09e8da2501e767b4238ebaef7fcb67368c6261ac7364 |
| reports/international_rule_probe_controls_v1.json | explicit-input leakage control receipt | 31b80495a106c1df4e22dedda0892e74c62968909c53f81c6c58eb2f887d8907 |
| reports/international_rule_review_assignments_v1.json | 128-record, two-reviewer assignment receipt | 261abcd4279681251efce5d54e0183329ee5f15144c3e2ea21599e2a4a85cafa |
| review_packages/stratified_expert_review_v1.2_sd_core/ | 800-record blinded sample, 1,600 empty assignment slots, and blank review-log schema | Generated locally; no completed human judgments |

The evidence-tier receipt separates the 95,479-row contract layer, 80,393 scenario-and-rule rows, 18,087 auxiliary-decision contracts, 320 OPF-tagged auxiliary rows, 160 fixed-control replay cases, and nine selected independent OPF solves. The remaining 17,767 auxiliary rows carry contract-level decision evidence and are not counted as executable-control replays. The core human-review ledger currently has 0 completed rows out of 1,600 assignments; the international assignment artifact has no human results.

The extension is a source-conditioned rule-grounding probe. Its leakage-controlled CPU retrieval diagnostic gives macro exact match 0.0000, token-F1 0.5303 on the two cross-jurisdiction folds and 0.9756 on the two within-jurisdiction variant holdouts, with structured field scores of 0.000 and 1.000 respectively. The regenerated records contain zero semantic-template tautologies. An explicit-input contract-copy control reaches 1.000 on rule, standard, jurisdiction, clause, and evidence fields by construction; this is a leakage diagnostic rather than a model score. All values are lexical-transfer diagnostics, not semantic legal or physical-compliance scores. The review receipt contains blank labels for two independent reviewer slots per sampled record. The extension does not change the core split counts or imply that NERC or European rules endorse the domestic numeric policies.

## Rule Dictionary

| rule_id | standard_id | clause_id | rule_type | evidence_fields | applicable_tasks |
| --- | --- | --- | --- | --- | --- |
| REG_OVERLOAD_001 | GRIDINSTRUCT-V1.2-RULEBOOK | 3.1 | threshold | instruction, input | regulation_compliance_check, auxiliary_decision, regulation_qa, intelligent_data_query |
| REG_VOLTAGE_001 | GRIDINSTRUCT-V1.2-RULEBOOK | 3.2 | threshold | instruction, input | regulation_compliance_check, auxiliary_decision, regulation_qa, intelligent_data_query |
| REG_SWITCHING_001 | GRIDINSTRUCT-V1.2-RULEBOOK | 4.1 | procedural | instruction, input | operation_ticket_check, regulation_qa |
| REG_TOOL_001 | GRIDINSTRUCT-V1.2-RULEBOOK | 5.1 | procedural | instruction, input | dispatcher_intent_tool_call, regulation_qa |
| REG_QUERY_001 | GRIDINSTRUCT-V1.2-RULEBOOK | 6.1 | data_query | instruction, input | intelligent_data_query, regulation_qa |
| REG_OVERLOAD_ALERT_002 | GRIDINSTRUCT-V1.2-RULEBOOK | 3.1.1 | threshold_detail | input.observed_issues, input.grid_state_summary | regulation_compliance_check, auxiliary_decision, intelligent_data_query |
| REG_OVERLOAD_EMERGENCY_003 | GRIDINSTRUCT-V1.2-RULEBOOK | 3.1.2 | threshold_detail | input.observed_issues, input.grid_state_summary | regulation_compliance_check, auxiliary_decision |
| REG_VOLTAGE_LOW_002 | GRIDINSTRUCT-V1.2-RULEBOOK | 3.2.1 | threshold_detail | input.observed_issues, input.grid_state_summary | regulation_compliance_check, auxiliary_decision |
| REG_VOLTAGE_HIGH_003 | GRIDINSTRUCT-V1.2-RULEBOOK | 3.2.2 | threshold_detail | input.observed_issues, input.grid_state_summary | regulation_compliance_check, auxiliary_decision |
| REG_SECURITY_REDISPATCH_004 | GRIDINSTRUCT-V1.2-RULEBOOK | 3.3 | closed_loop_validation | input.available_actions, input.utterance | auxiliary_decision, dispatcher_intent_tool_call |
| REG_SWITCHING_PERMISSION_002 | GRIDINSTRUCT-V1.2-RULEBOOK | 4.2 | procedural_detail | input.dispatch_permit_status | operation_ticket_check |
| REG_SWITCHING_MONITORING_003 | GRIDINSTRUCT-V1.2-RULEBOOK | 4.3 | procedural_detail | input.monitoring_arrangement | operation_ticket_check |
| REG_TOOL_DIAGNOSIS_002 | GRIDINSTRUCT-V1.2-RULEBOOK | 5.2 | tool_routing_detail | input.utterance | dispatcher_intent_tool_call |
| REG_TOOL_MITIGATION_003 | GRIDINSTRUCT-V1.2-RULEBOOK | 5.3 | tool_routing_detail | input.utterance | dispatcher_intent_tool_call |
| REG_TOOL_POSTCHECK_004 | GRIDINSTRUCT-V1.2-RULEBOOK | 5.4 | tool_routing_detail | input.decision_window, input.utterance, instruction | dispatcher_intent_tool_call, auxiliary_decision, regulation_compliance_check |
| REG_QUERY_FILTER_002 | GRIDINSTRUCT-V1.2-RULEBOOK | 6.2 | data_query_detail | input.scenario_id, input.question, instruction | intelligent_data_query |
| REG_QUERY_AGGREGATION_003 | GRIDINSTRUCT-V1.2-RULEBOOK | 6.3 | data_query_detail | input.question, instruction | intelligent_data_query |

## Task Distribution

| task_type | records |
| --- | ---: |
| auxiliary_decision | 18087 |
| dispatcher_intent_tool_call | 19773 |
| intelligent_data_query | 17766 |
| operation_ticket_check | 10881 |
| regulation_compliance_check | 24767 |
| regulation_qa | 4205 |

## Split Distribution

| split | records |
| --- | ---: |
| train | 26545 |
| validation | 3561 |
| test | 3894 |
| ood_test | 61479 |

## Split Coverage Notes

- All six task families occur in the promoted OOD split; task counts are recomputed from the current English files.
- The 75,240-record instruction-surface OOD view is distributed as the ID-only manifest `data/v1.2_sd_core_instruction_surface_balanced_ood_test_ids.jsonl`; evaluation scripts materialize its rows from the canonical English table on demand and bind the resulting metrics to the canonical SHA-256.

## Record Schema

Every instruction record includes `id`, `task_stage`, `task_type`, `instruction`, `input`, `output`, `source_regulation_ids`, and `metadata`.
Topology-grounded tasks additionally link to `network_model`, `scenario_id`, and `source_simulation_case_id` when applicable.
`metadata/data_dictionary.csv` defines the released fields.
