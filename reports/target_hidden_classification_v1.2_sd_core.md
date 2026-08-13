# Target-hidden classification baseline

Generated: 2026-08-05T15:21:51.874689+00:00

The target-hidden profile removes scenario/source identifiers, generated state summaries, rule/label fields, tool plans, structured targets, and released query/result objects. It retains dispatcher-facing text and non-target procedural fields. Scores are diagnostic learnability evidence, not an operational dispatch claim.

| split | task | input profile | n test | macro-F1 | 95% CI | balanced accuracy |
| --- | --- | --- | ---: | ---: | --- | ---: |
| standard | dispatcher_intent_tool_call | full_contract | 494 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| standard | operation_ticket_check | full_contract | 1110 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| standard | regulation_compliance_check | full_contract | 701 | 0.8636 | [0.8375, 0.8917] | 0.8580 |
| standard | dispatcher_intent_tool_call | target_hidden | 494 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| standard | operation_ticket_check | target_hidden | 1110 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| standard | regulation_compliance_check | target_hidden | 701 | 0.6678 | [0.6243, 0.7073] | 0.6949 |
| standard | dispatcher_intent_tool_call | instruction_only | 494 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| standard | operation_ticket_check | instruction_only | 1110 | 0.2533 | [0.2360, 0.2714] | 0.3191 |
| standard | regulation_compliance_check | instruction_only | 701 | 0.6679 | [0.6212, 0.7080] | 0.6897 |
| strict_source_group | dispatcher_intent_tool_call | full_contract | 1975 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | operation_ticket_check | full_contract | 1103 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | regulation_compliance_check | full_contract | 2477 | 0.9202 | [0.9009, 0.9362] | 0.9207 |
| strict_source_group | dispatcher_intent_tool_call | target_hidden | 1975 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | operation_ticket_check | target_hidden | 1103 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | regulation_compliance_check | target_hidden | 2477 | 0.7191 | [0.6935, 0.7454] | 0.7593 |
| strict_source_group | dispatcher_intent_tool_call | instruction_only | 1975 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | operation_ticket_check | instruction_only | 1103 | 0.2260 | [0.2116, 0.2412] | 0.3325 |
| strict_source_group | regulation_compliance_check | instruction_only | 2477 | 0.7137 | [0.6867, 0.7402] | 0.7548 |
| template_holdout | dispatcher_intent_tool_call | full_contract | 3955 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| template_holdout | operation_ticket_check | full_contract | 2186 | 0.9996 | [0.9988, 1.0000] | 0.9995 |
| template_holdout | regulation_compliance_check | full_contract | 4975 | 0.9887 | [0.6534, 0.9910] | 0.9889 |
| template_holdout | dispatcher_intent_tool_call | target_hidden | 3955 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| template_holdout | operation_ticket_check | target_hidden | 2186 | 0.9996 | [0.9988, 1.0000] | 0.9995 |
| template_holdout | regulation_compliance_check | target_hidden | 4975 | 0.6359 | [0.6306, 0.6414] | 0.9498 |
| template_holdout | dispatcher_intent_tool_call | instruction_only | 3955 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| template_holdout | operation_ticket_check | instruction_only | 2186 | 0.0818 | [0.0731, 0.0900] | 0.3308 |
| template_holdout | regulation_compliance_check | instruction_only | 4975 | 0.6412 | [0.6346, 0.6489] | 0.9591 |

Input removal tokens

`scenario`, `source`, `simulation`, `metadata`, `template`, `validation`, `tool_plan`, `structured_query`, `query_result`, `rule_summary`, `rule_id`, `active_constraints`, `observed_issues`, `grid_state_summary`, `compliance_label`, `intent`, `slots`, `rationale`, `chosen_response`, `rejected_response`
