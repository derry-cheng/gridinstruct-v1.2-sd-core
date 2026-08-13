# Proxy-Reduced Input Audit

Generated: 2026-08-04T03:58:52.020219+00:00
Status: `pass`

This audit uses TF-IDF classifiers over instruction-only and proxy-reduced input text. It excludes metadata, source identifiers, template fields, explicit label fields, and tool-plan fields from the input surface.

| split | task | profile | n_eval | macro-F1 | balanced accuracy |
| --- | --- | --- | ---: | ---: | ---: |
| standard | dispatcher_intent_tool_call | instruction_only | 494 | 1.0000 | 1.0000 |
| standard | operation_ticket_check | instruction_only | 1110 | 0.2143 | 0.3333 |
| standard | regulation_compliance_check | instruction_only | 701 | 0.7368 | 0.7060 |
| standard | dispatcher_intent_tool_call | proxy_reduced | 494 | 1.0000 | 1.0000 |
| standard | operation_ticket_check | proxy_reduced | 1110 | 1.0000 | 1.0000 |
| standard | regulation_compliance_check | proxy_reduced | 701 | 0.8553 | 0.8432 |
| strict_source_group | dispatcher_intent_tool_call | instruction_only | 1975 | 1.0000 | 1.0000 |
| strict_source_group | operation_ticket_check | instruction_only | 1103 | 0.2122 | 0.3333 |
| strict_source_group | regulation_compliance_check | instruction_only | 2477 | 0.7308 | 0.7019 |
| strict_source_group | dispatcher_intent_tool_call | proxy_reduced | 1975 | 1.0000 | 1.0000 |
| strict_source_group | operation_ticket_check | proxy_reduced | 1103 | 1.0000 | 1.0000 |
| strict_source_group | regulation_compliance_check | proxy_reduced | 2477 | 0.9083 | 0.8840 |

## Figures

- proxy_reduced_scores: `figures/sd_core/fig_proxy_reduced_input_scores.png`
