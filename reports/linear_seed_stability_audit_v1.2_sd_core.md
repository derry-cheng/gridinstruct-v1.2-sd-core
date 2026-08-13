# Linear Multi-Seed Stability Audit

Generated: 2026-08-04T04:17:46.723969+00:00
Status: `pass`

This audit trains stochastic linear text classifiers over released classification labels across multiple random seeds. It is a lightweight stability check; formal neural baselines remain in the main benchmark reports.

## Gates

- `all_expected_split_families_present`: pass
- `all_runs_completed`: pass
- `seed_count_at_least_five`: pass
- `macro_f1_std_bounded`: pass
- `no_empty_test_sets`: pass

## Summary

| split family | task | runs | mean macro-F1 | std | min | max |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| challenge | dispatcher_intent_tool_call | 5 | 0.9990 | 0.0010 | 0.9971 | 1.0000 |
| challenge | operation_ticket_check | 5 | 0.9994 | 0.0000 | 0.9994 | 0.9994 |
| challenge | regulation_compliance_check | 5 | 0.6667 | 0.0000 | 0.6667 | 0.6667 |
| proxyreduced | dispatcher_intent_tool_call | 5 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| proxyreduced | operation_ticket_check | 5 | 0.9980 | 0.0008 | 0.9969 | 0.9989 |
| proxyreduced | regulation_compliance_check | 5 | 0.8884 | 0.0114 | 0.8685 | 0.8993 |
| standard | dispatcher_intent_tool_call | 5 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| standard | operation_ticket_check | 5 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| standard | regulation_compliance_check | 5 | 0.8884 | 0.0114 | 0.8685 | 0.8993 |
| strict | dispatcher_intent_tool_call | 5 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| strict | operation_ticket_check | 5 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| strict | regulation_compliance_check | 5 | 0.9397 | 0.0021 | 0.9378 | 0.9431 |
| strict_proxyreduced | dispatcher_intent_tool_call | 5 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| strict_proxyreduced | operation_ticket_check | 5 | 0.9967 | 0.0006 | 0.9957 | 0.9977 |
| strict_proxyreduced | regulation_compliance_check | 5 | 0.9397 | 0.0021 | 0.9378 | 0.9431 |

## Figures

- linear_seed_stability: `figures/sd_core/fig_linear_seed_stability.png`
