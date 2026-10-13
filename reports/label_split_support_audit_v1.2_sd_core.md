# Label and Split Support Audit

Generated: 2026-07-31T09:50:47.643022+00:00
Status: `pass`

This audit summarizes split-level task, label, network, severity, source-group, and scenario support. It is a technical validation artifact for reuse and benchmark reporting.

## Gates

- `required_splits_present`: pass
- `no_empty_required_split`: pass
- `classification_test_support_present`: pass
- `strict_classification_test_support_present`: pass
- `network_coverage_reported`: pass
- `severity_coverage_reported`: pass

## Split Summary

| split | records | source groups | scenarios | min classification label support |
| --- | ---: | ---: | ---: | ---: |
| train | 28684 | 9195 | 550 | 590 |
| validation | 3598 | 1094 | 65 | 77 |
| test | 3609 | 1114 | 66 | 74 |
| ood_test | 59588 | 6557 | 1915 | 288 |
| challenge_train | 44339 | 9343 | 2453 | 467 |
| challenge_validation | 5745 | 2223 | 217 | 20 |
| challenge_test | 5337 | 2029 | 494 | 3 |
| strict_train | 76350 | 14415 | 2081 | 820 |
| strict_validation | 9570 | 1761 | 259 | 105 |
| strict_test | 9559 | 1784 | 256 | 104 |
| proxyreduced_train | 18240 | 18240 | 0 | 590 |
| proxyreduced_validation | 2293 | 2293 | 0 | 77 |
| proxyreduced_test | 2305 | 2305 | 0 | 74 |
| strict_proxyreduced_train | 44300 | 44300 | 0 | 820 |
| strict_proxyreduced_validation | 5566 | 5566 | 0 | 105 |
| strict_proxyreduced_test | 5555 | 5555 | 0 | 104 |

## Label Distribution Shift

| family | task | comparison | Jensen-Shannon divergence |
| --- | --- | --- | ---: |
| standard | operation_ticket_check | train__validation | 0.0001 |
| standard | operation_ticket_check | train__test | 0.0004 |
| standard | regulation_compliance_check | train__validation | 0.0000 |
| standard | regulation_compliance_check | train__test | 0.0000 |
| standard | dispatcher_intent_tool_call | train__validation | 0.0000 |
| standard | dispatcher_intent_tool_call | train__test | 0.0000 |
| challenge | operation_ticket_check | challenge_train__challenge_validation | 0.0000 |
| challenge | operation_ticket_check | challenge_train__challenge_test | 0.0000 |
| challenge | regulation_compliance_check | challenge_train__challenge_validation | 0.0245 |
| challenge | regulation_compliance_check | challenge_train__challenge_test | 0.1983 |
| challenge | dispatcher_intent_tool_call | challenge_train__challenge_validation | 0.0000 |
| challenge | dispatcher_intent_tool_call | challenge_train__challenge_test | 0.0000 |
| strict | operation_ticket_check | strict_train__strict_validation | 0.0004 |
| strict | operation_ticket_check | strict_train__strict_test | 0.0002 |
| strict | regulation_compliance_check | strict_train__strict_validation | 0.0000 |
| strict | regulation_compliance_check | strict_train__strict_test | 0.0000 |
| strict | dispatcher_intent_tool_call | strict_train__strict_validation | 0.0000 |
| strict | dispatcher_intent_tool_call | strict_train__strict_test | 0.0000 |
| proxyreduced | operation_ticket_check | proxyreduced_train__proxyreduced_validation | 0.0001 |
| proxyreduced | operation_ticket_check | proxyreduced_train__proxyreduced_test | 0.0004 |
| proxyreduced | regulation_compliance_check | proxyreduced_train__proxyreduced_validation | 0.0000 |
| proxyreduced | regulation_compliance_check | proxyreduced_train__proxyreduced_test | 0.0000 |
| proxyreduced | dispatcher_intent_tool_call | proxyreduced_train__proxyreduced_validation | 0.0000 |
| proxyreduced | dispatcher_intent_tool_call | proxyreduced_train__proxyreduced_test | 0.0000 |
| strict_proxyreduced | operation_ticket_check | strict_proxyreduced_train__strict_proxyreduced_validation | 0.0004 |
| strict_proxyreduced | operation_ticket_check | strict_proxyreduced_train__strict_proxyreduced_test | 0.0002 |
| strict_proxyreduced | regulation_compliance_check | strict_proxyreduced_train__strict_proxyreduced_validation | 0.0000 |
| strict_proxyreduced | regulation_compliance_check | strict_proxyreduced_train__strict_proxyreduced_test | 0.0000 |
| strict_proxyreduced | dispatcher_intent_tool_call | strict_proxyreduced_train__strict_proxyreduced_validation | 0.0000 |
| strict_proxyreduced | dispatcher_intent_tool_call | strict_proxyreduced_train__strict_proxyreduced_test | 0.0000 |

## Scenario Construction Summary

- Candidate scenarios in metadata: 2472
- Converged scenarios in metadata: 2415
- Systems: ieee14, ieee30, ieee57, ieee118

## Figures

- label_support: `figures/sd_core/fig_label_split_support.png`
- split_task_coverage: `figures/sd_core/fig_split_task_network_coverage.png`
