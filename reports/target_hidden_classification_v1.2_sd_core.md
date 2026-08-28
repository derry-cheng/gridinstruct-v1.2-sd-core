# Target-hidden classification baseline

Generated: 2026-08-28T20:19:01.263989+00:00

The target-hidden profile removes scenario/source identifiers, generated state summaries, rule/label fields, tool plans, structured targets, and released query/result objects. It retains dispatcher-facing text and non-target procedural fields. Scores are diagnostic learnability evidence, not an operational dispatch claim.

| split | task | input profile | n test | macro-F1 | 95% CI | balanced accuracy |
| --- | --- | --- | ---: | ---: | --- | ---: |
| standard | dispatcher_intent_tool_call | full_contract | 867 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| standard | operation_ticket_check | full_contract | 928 | 0.9987 | [0.9960, 1.0000] | 0.9988 |
| standard | regulation_compliance_check | full_contract | 921 | 0.9774 | [0.9628, 0.9899] | 0.9734 |
| standard | dispatcher_intent_tool_call | target_hidden | 867 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| standard | operation_ticket_check | target_hidden | 928 | 0.9987 | [0.9960, 1.0000] | 0.9988 |
| standard | regulation_compliance_check | target_hidden | 921 | 0.4989 | [0.4731, 0.5257] | 0.5428 |
| standard | dispatcher_intent_tool_call | instruction_only | 867 | 0.4237 | [0.3889, 0.4551] | 0.4241 |
| standard | operation_ticket_check | instruction_only | 928 | 0.9962 | [0.9907, 1.0000] | 0.9964 |
| standard | regulation_compliance_check | instruction_only | 921 | 0.4989 | [0.4731, 0.5257] | 0.5428 |
| strict_source_group | dispatcher_intent_tool_call | full_contract | 1976 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | operation_ticket_check | full_contract | 1113 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | regulation_compliance_check | full_contract | 2481 | 0.9446 | [0.9294, 0.9581] | 0.9705 |
| strict_source_group | dispatcher_intent_tool_call | target_hidden | 1976 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | operation_ticket_check | target_hidden | 1113 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | regulation_compliance_check | target_hidden | 2481 | 0.9119 | [0.8910, 0.9280] | 0.9400 |
| strict_source_group | dispatcher_intent_tool_call | instruction_only | 1976 | 0.4039 | [0.3830, 0.4260] | 0.4043 |
| strict_source_group | operation_ticket_check | instruction_only | 1113 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| strict_source_group | regulation_compliance_check | instruction_only | 2481 | 0.9105 | [0.8900, 0.9277] | 0.9397 |
| template_holdout | dispatcher_intent_tool_call | full_contract | 3958 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| template_holdout | operation_ticket_check | full_contract | 2177 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| template_holdout | regulation_compliance_check | full_contract | 4954 | 0.9634 | [0.9537, 0.9738] | 0.9624 |
| template_holdout | dispatcher_intent_tool_call | target_hidden | 3958 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| template_holdout | operation_ticket_check | target_hidden | 2177 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| template_holdout | regulation_compliance_check | target_hidden | 4954 | 0.8884 | [0.8730, 0.9032] | 0.9090 |
| template_holdout | dispatcher_intent_tool_call | instruction_only | 3958 | 0.1997 | [0.1892, 0.2092] | 0.3349 |
| template_holdout | operation_ticket_check | instruction_only | 2177 | 0.9966 | [0.9938, 0.9989] | 0.9970 |
| template_holdout | regulation_compliance_check | instruction_only | 4954 | 0.8977 | [0.8823, 0.9110] | 0.9118 |

Input removal tokens

`scenario`, `source`, `simulation`, `metadata`, `template`, `validation`, `tool_plan`, `structured_query`, `query_result`, `rule_summary`, `rule_id`, `active_constraints`, `observed_issues`, `grid_state_summary`, `compliance_label`, `intent`, `slots`, `rationale`, `chosen_response`, `rejected_response`
