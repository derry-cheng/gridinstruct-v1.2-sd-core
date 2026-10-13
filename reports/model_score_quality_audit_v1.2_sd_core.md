# Model Score Quality Audit

Generated: 2026-08-08T16:11:19.079655+00:00
Dataset: `data/gridinstruct_v1.2_sd_core_en.jsonl`
Verdict: `warn`

## Model Flags

- `benchmark/v1.2_sd_core_transformer_dispatcher_intent_tool_call_report.json`: standard=1.0, challenge_min=0.7756025485188864, challenge_best=0.7756025485188864, strict=1.0, flags=near_perfect_standard_score, standard_test_reuses_train_template_or_action_groups, challenge_groups_are_nearly_label_pure, input_fields_have_high_label_purity, challenge_score_drop
- `benchmark/v1.2_sd_core_transformer_operation_ticket_check_report.json`: standard=1.0, challenge_min=0.9993654879605177, challenge_best=0.9993654879605177, strict=1.0, flags=near_perfect_standard_score, near_perfect_challenge_score, challenge_groups_are_nearly_label_pure
- `benchmark/v1.2_sd_core_transformer_regulation_compliance_check_report.json`: standard=0.920374867245268, challenge_min=1.0, challenge_best=1.0, strict=0.976509903706574, flags=near_perfect_challenge_score, standard_test_reuses_train_template_or_action_groups

## Hard Gates

- standard_reports_present_for_all_classification_tasks: pass
- challenge_reports_present_for_all_classification_tasks: pass
- standard_split_group_overlap_clean: fail
- challenge_best_macro_f1_at_least_0_50: pass
- strict_source_group_reports_present_for_all_classification_tasks: pass
- strict_source_group_macro_f1_at_least_0_50: pass
- no_rationale_input_leakage_in_formal_reports: pass

## Generative Model Flags

- `benchmark/v1.2_sd_core_seq2seq_auxiliary_decision_report.json`: scores={'exact_match': 0.0, 'token_f1': None, 'char_f1': 0.9255203565884278, 'selection_f1': 0.9255203565884278, 'prediction_is_json': 0.0, 'structured_query_exact': None, 'tool_set_exact': None}, flags=none
- `benchmark/v1.2_sd_core_seq2seq_intelligent_data_query_report.json`: scores={'exact_match': 0.0, 'token_f1': None, 'char_f1': 0.9870428247973714, 'selection_f1': 0.9870428247973714, 'prediction_is_json': 0.0, 'structured_query_exact': None, 'tool_set_exact': None}, flags=none
- `benchmark/v1.2_sd_core_seq2seq_regqa_report.json`: scores={'exact_match': 0.0, 'token_f1': 0.8072746532282353, 'char_f1': None, 'selection_f1': 0.8072746532282353, 'prediction_is_json': None, 'structured_query_exact': None, 'tool_set_exact': None}, flags=none

## Label And Split Diagnostics

- operation_ticket_check: labels={'non_compliant': 3161, 'compliant_with_monitoring': 4949, 'compliant': 2771}; rare=none; group_purity=0.9998; standard_test_group_overlap=0.2802; proxy_fields=0
- regulation_compliance_check: labels={'non_compliant': 13041, 'compliant_with_monitoring': 10697, 'compliant': 1029}; rare=none; group_purity=0.9382; standard_test_group_overlap=1.0000; proxy_fields=0
- dispatcher_intent_tool_call: labels={'mitigate_violations': 6589, 'diagnose_and_dispatch': 6613, 'security_check_and_redispatch': 6571}; rare=none; group_purity=0.9999; standard_test_group_overlap=0.7935; proxy_fields=1
  - proxy field `utterance`: coverage=1.0000, repeated_value_rate=0.7109, value_label_purity=0.9999, pure_repeated_value_rate=0.9992

## Claim Guidance

- Treat near-perfect standard split classifier scores as trainability checks, not as claims of solved dispatch reasoning.
- Report challenge-split scores alongside standard split scores when discussing classification models.
- Treat deterministic structured-query generation scores separately from open-ended language generation scores.
- Avoid using gold rationales as classifier inputs in formal baseline claims.
