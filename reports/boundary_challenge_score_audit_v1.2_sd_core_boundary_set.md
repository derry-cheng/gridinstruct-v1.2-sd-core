# Boundary Challenge Score Audit

Generated: `2026-07-08T10:50:02.919867+00:00`
Figure: `figures/sd_core/fig_boundary_challenge_dedicated_subset_scores.png`

## Subset Results

| Task | Family | Records | Macro-F1 | Accuracy |
| --- | --- | ---: | ---: | ---: |
| operation_ticket_check | operation_multilabel_plain_boundary | 3000 | 0.8604 | 0.8660 |
| operation_ticket_check | all_boundary_families | 3000 | 0.8604 | 0.8660 |
| operation_ticket_check | all_challenge_records | 3000 | 0.8604 | 0.8660 |
| dispatcher_intent_tool_call | dispatcher_priority_multilabel_boundary | 1800 | 1.0000 | 1.0000 |
| dispatcher_intent_tool_call | all_boundary_families | 1800 | 1.0000 | 1.0000 |
| dispatcher_intent_tool_call | all_challenge_records | 1800 | 1.0000 | 1.0000 |

## Claim Guidance

Use these subset results to interpret near-perfect aggregate F1. Boundary-family scores remain validation evidence for constrained labels, not evidence of autonomous dispatch reasoning.
