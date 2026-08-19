# Current split five-seed stability audit

Generated: 2026-08-19T16:55:37.398905+00:00
Status: `pass`

The stochastic linear reference is evaluated on the frozen instruction-surface test and OOD splits.

| evaluation | task | runs | n | macro-F1 mean | std | min | max |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| surface_ood | dispatcher_intent_tool_call | 5 | 19143 | 0.3263 | 0.0017 | 0.3244 | 0.3289 |
| surface_ood | operation_ticket_check | 5 | 945 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| surface_ood | regulation_compliance_check | 5 | 21845 | 0.6668 | 0.0300 | 0.6351 | 0.7125 |
| surface_test | dispatcher_intent_tool_call | 5 | 121 | 0.6114 | 0.0119 | 0.5940 | 0.6262 |
| surface_test | operation_ticket_check | 5 | 2981 | 0.9995 | 0.0001 | 0.9993 | 0.9996 |
| surface_test | regulation_compliance_check | 5 | 454 | 0.8654 | 0.0238 | 0.8271 | 0.8963 |

The audit is a trainability diagnostic; it is not a physical or semantic validity certificate.
