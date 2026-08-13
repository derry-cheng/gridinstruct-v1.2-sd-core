# Calibration Audit

Generated: 2026-08-04T03:54:21.057007+00:00
Status: `pass`

Expected calibration error (ECE) compares confidence with empirical accuracy across confidence bins. Brier score is the mean squared distance between predicted class probabilities and one-hot labels.

| split | task | n | ECE | Brier | accuracy | mean confidence |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| standard | operation_ticket_check | 1110 | 0.0011 | 0.0000 | 1.0000 | 0.9989 |
| standard | regulation_compliance_check | 701 | 0.0769 | 0.1559 | 0.9201 | 0.9867 |
| standard | dispatcher_intent_tool_call | 494 | 0.0043 | 0.0000 | 1.0000 | 0.9957 |
| challenge | operation_ticket_check | 3870 | 0.0031 | 0.0009 | 0.9995 | 0.9968 |
| challenge | regulation_compliance_check | 67 | 0.0003 | 0.0000 | 1.0000 | 0.9997 |
| challenge | dispatcher_intent_tool_call | 1400 | 0.1968 | 0.3978 | 0.7850 | 0.9818 |

## Figures

- reliability: `figures/sd_core/fig_calibration_reliability.png`
