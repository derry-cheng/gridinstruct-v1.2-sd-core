# Boundary Challenge Score Audit

Generated: `2026-08-04T03:56:52.622010+00:00`
Figure: `figures/sd_core/fig_boundary_challenge_subset_scores.png`

## Subset Results

| Task | Family | Records | Macro-F1 | Accuracy |
| --- | --- | ---: | ---: | ---: |
| operation_ticket_check | base | 1495 | 0.9986 | 0.9987 |
| operation_ticket_check | operation_implicit_ticket_llm | 264 | 1.0000 | 1.0000 |
| operation_ticket_check | operation_monitoring_boundary_counterfactual | 696 | 1.0000 | 1.0000 |
| operation_ticket_check | operation_plain_ticket_adversarial | 715 | 1.0000 | 1.0000 |
| operation_ticket_check | operation_ticket_rulebook_counterfactual | 700 | 1.0000 | 1.0000 |
| operation_ticket_check | all_boundary_families | 2375 | 1.0000 | 1.0000 |
| operation_ticket_check | all_challenge_records | 3870 | 0.9994 | 0.9995 |
| dispatcher_intent_tool_call | dispatcher_intent_compound_counterfactual | 1400 | 0.7756 | 0.7850 |
| dispatcher_intent_tool_call | all_boundary_families | 1400 | 0.7756 | 0.7850 |
| dispatcher_intent_tool_call | all_challenge_records | 1400 | 0.7756 | 0.7850 |

## Claim Guidance

Use these subset results to interpret near-perfect aggregate F1. Boundary-family scores remain validation evidence for constrained labels, not evidence of autonomous dispatch reasoning.
