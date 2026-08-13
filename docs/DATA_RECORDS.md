# Data Records

Generated: 2026-08-04T04:35:54.537983+00:00

GridInstruct v1.2 contains 95479 instruction records. Records are stored as UTF-8 JSON Lines and validated against `metadata/schema.json`.

## Core Files

| path | records | bytes | sha256 |
| --- | ---: | ---: | --- |
| data/gridinstruct_v1.2_sd_core_en.jsonl | 95479 | 534958426 | 75ffc5c5386e331eed6c7f4092d186e042be8a020a936993f5220a78c1fd058e |
| data/v1.2_sd_core_train_en.jsonl | 28684 | 140766902 | ff09925aacb7767dc303eebd4d4089c69f7216977bd9c0e76198788660c9ed3a |
| data/v1.2_sd_core_validation_en.jsonl | 3598 | 17473764 | bbb8fab5478b7a4cc8b0b729180d3225cafc081ea56c5e30938f021b28b1e4a5 |
| data/v1.2_sd_core_test_en.jsonl | 3609 | 17785811 | 982e6bef11f3cbc8fa8e3e280c447aadbb22bd26e708fd070957042f436755c0 |
| data/v1.2_sd_core_ood_test_en.jsonl | 59588 | 363341461 | b8fa701e9661e92d5df92328e9c1ece60d5440081c54cd509ad502008608fa8a |
| metadata/schema.json | None | 3677 | b1304984822f104af78f3055f4ae0af995f8da441bc69b0f9d09d2ddfa77b070 |
| metadata/task_taxonomy.json | None | 243 | 6dc8399ae3491756bf088f72eb58cbdcb7a95e4673f59d8b8f5f60a0acde0afa |
| metadata/data_dictionary.csv | None | 3982 | 30e75d55c6d39e6dc1bbab47cd62e85734b81a21b638def81734a95685c6c5c5 |
| rules/regulation_rules.json | 17 | 37019 | 0c834cdf81dc0b10e76d525845111dc5e845ac8a559ef9c21bc02aeb0b4c1a83 |

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
| train | 28684 |
| validation | 3598 |
| test | 3609 |
| ood_test | 59588 |

## Split Coverage Notes

- ood_test missing task types: operation_ticket_check, regulation_qa

## Record Schema

Every instruction record includes `id`, `task_stage`, `task_type`, `instruction`, `input`, `output`, `source_regulation_ids`, and `metadata`.
Topology-grounded tasks additionally link to `network_model`, `scenario_id`, and `source_simulation_case_id` when applicable.
`metadata/data_dictionary.csv` defines the released fields.
