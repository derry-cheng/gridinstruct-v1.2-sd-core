# Exact-surface target-hidden classification diagnostic

Generated: 2026-08-28T16:29:56.656333+00:00

This CPU diagnostic evaluates three closed-label tasks after assigning every character-5-gram component to one split. The target-hidden profile removes target-carrying fields before fitting a deterministic character TF--IDF linear SVC. The scores quantify learnability under a lexical near-duplicate stress split; they do not establish semantic independence, physical-label validity, or dispatch competence.

| task | input profile | train | test | macro-F1 | 95% CI | balanced accuracy |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| dispatcher_intent_tool_call | full_contract | 415 | 101 | 0.1966 | [0.1360, 0.2660] | 0.3284 |
| operation_ticket_check | full_contract | 7776 | 970 | 0.9987 | [0.9962, 1.0000] | 0.9988 |
| regulation_compliance_check | full_contract | 2130 | 265 | 0.9397 | [0.9116, 0.9667] | 0.9525 |
| dispatcher_intent_tool_call | target_hidden | 415 | 101 | 0.1406 | [0.0960, 0.1752] | 0.3333 |
| operation_ticket_check | target_hidden | 7776 | 970 | 0.9987 | [0.9962, 1.0000] | 0.9988 |
| regulation_compliance_check | target_hidden | 2130 | 265 | 0.8911 | [0.8520, 0.9251] | 0.9087 |
| dispatcher_intent_tool_call | instruction_only | 415 | 101 | 0.1406 | [0.0960, 0.1752] | 0.3333 |
| operation_ticket_check | instruction_only | 7776 | 970 | 0.9939 | [0.9882, 0.9988] | 0.9940 |
| regulation_compliance_check | instruction_only | 2130 | 265 | 0.8952 | [0.8579, 0.9277] | 0.9142 |

The exact-surface component report and the canonical input hash are recorded in the JSON receipt.
