# High-score Plausibility Audit

Generated: 2026-08-04T04:00:00.591698+00:00

This audit consolidates evidence for interpreting near-perfect classification scores. It does not convert high held-out scores into operational dispatch-reasoning claims.

Status: pass

## Evidence by task

### operation_ticket_check
- standard_transformer_macro_f1: 1.0000
- challenge_transformer_macro_f1: 0.9994
- template_holdout_tfidf_macro_f1: 0.9996
- dedicated_boundary_tfidf_macro_f1: 0.8604
- proxy_reduced_standard_macro_f1: 1.0000
- proxy_reduced_strict_macro_f1: 1.0000
- rule_context_with_rule_macro_f1: 1.0000
- score flags: near_perfect_standard_score, near_perfect_challenge_score, challenge_groups_are_nearly_label_pure

### dispatcher_intent_tool_call
- standard_transformer_macro_f1: 1.0000
- challenge_transformer_macro_f1: 0.7756
- template_holdout_tfidf_macro_f1: 1.0000
- dedicated_boundary_tfidf_macro_f1: 1.0000
- proxy_reduced_standard_macro_f1: 1.0000
- proxy_reduced_strict_macro_f1: 1.0000
- rule_context_with_rule_macro_f1: 1.0000
- score flags: near_perfect_standard_score, standard_test_reuses_train_template_or_action_groups, challenge_groups_are_nearly_label_pure, input_fields_have_high_label_purity, challenge_score_drop

### regulation_compliance_check
- standard_transformer_macro_f1: 0.9204
- challenge_transformer_macro_f1: 1.0000
- template_holdout_tfidf_macro_f1: 0.9862
- dedicated_boundary_tfidf_macro_f1: not available
- proxy_reduced_standard_macro_f1: 0.8553
- proxy_reduced_strict_macro_f1: 0.9083
- rule_context_with_rule_macro_f1: 0.8718
- score flags: near_perfect_challenge_score, standard_test_reuses_train_template_or_action_groups

## Claim guidance

- Use near-perfect standard split results as trainability and data-consistency evidence.
- Report template-holdout, challenge, dedicated boundary-set, proxy-reduced, strict source-group, calibration, and statistical interval evidence beside high scores.
- Use rule-context baselines as a provenance-usefulness check, not as a handcrafted operational rule system.