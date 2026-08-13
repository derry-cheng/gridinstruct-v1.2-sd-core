# Shortcut and Input-Field Ablation Audit

Generated: 2026-08-08T17:11:47.028062+00:00
Status: `pass`
Keyword mask status: `pass`

This audit evaluates how much each classification task depends on specific input fields. It is used to interpret near-perfect held-out scores as trainability and input-sensitivity evidence.

## Full-Input Scores

- `challenge|dispatcher_intent_tool_call`: 1.0000
- `challenge|operation_ticket_check`: 0.9994
- `challenge|regulation_compliance_check`: 0.6667
- `standard|dispatcher_intent_tool_call`: 1.0000
- `standard|operation_ticket_check`: 1.0000
- `standard|regulation_compliance_check`: 0.8615

## Highest Sensitivity Rows

- `challenge | dispatcher_intent_tool_call | grid_state_only`: macro-F1=0.3572, drop=0.6428, risk=`high_field_dependence`
- `standard | dispatcher_intent_tool_call | grid_state_only`: macro-F1=0.3860, drop=0.6140, risk=`high_field_dependence`
- `challenge | regulation_compliance_check | state_only`: macro-F1=0.2470, drop=0.4197, risk=`high_field_dependence`
- `challenge | regulation_compliance_check | proposed_action_only`: macro-F1=0.3257, drop=0.3410, risk=`high_field_dependence`
- `standard | regulation_compliance_check | state_only`: macro-F1=0.5891, drop=0.2724, risk=`high_field_dependence`
- `standard | regulation_compliance_check | proposed_action_only`: macro-F1=0.6897, drop=0.1719, risk=`high_field_dependence`
- `challenge | regulation_compliance_check | no_grid_state_summary`: macro-F1=0.4949, drop=0.1718, risk=`high_field_dependence`
- `challenge | regulation_compliance_check | no_observed_issues`: macro-F1=0.5974, drop=0.0693, risk=`moderate_field_dependence`
- `standard | regulation_compliance_check | no_observed_issues`: macro-F1=0.8312, drop=0.0303, risk=`low_field_dependence`
- `challenge | dispatcher_intent_tool_call | utterance_only`: macro-F1=0.9721, drop=0.0279, risk=`low_field_dependence`
- `challenge | operation_ticket_check | ticket_text_only`: macro-F1=0.9946, drop=0.0047, risk=`low_field_dependence`
- `standard | regulation_compliance_check | keyword_mask`: macro-F1=0.8595, drop=0.0021, risk=`low_field_dependence`

## Figures

- shortcut_ablation_challenge: `figures/sd_core/fig_shortcut_ablation_challenge.png`
- shortcut_ablation_standard: `figures/sd_core/fig_shortcut_ablation_standard.png`
- shortcut_ablation_delta: `figures/sd_core/fig_shortcut_ablation_delta.png`
