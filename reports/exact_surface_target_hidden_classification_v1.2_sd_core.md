# Exact-surface target-hidden classification diagnostic

Generated: 2026-08-12T15:46:37.328385+00:00

This CPU diagnostic evaluates three closed-label tasks after assigning every character-5-gram component to one split. The target-hidden profile removes target-carrying fields before fitting a deterministic character TF--IDF linear SVC. The scores quantify learnability under a lexical near-duplicate stress split; they do not establish semantic independence, physical-label validity, or dispatch competence.

| task | input profile | train | test | macro-F1 | 95% CI | balanced accuracy |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| dispatcher_intent_tool_call | full_contract | 5456 | 682 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| operation_ticket_check | full_contract | 8705 | 1088 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| regulation_compliance_check | full_contract | 6713 | 840 | 0.9605 | [0.9451, 0.9732] | 0.9685 |
| dispatcher_intent_tool_call | target_hidden | 5456 | 682 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| operation_ticket_check | target_hidden | 8705 | 1088 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| regulation_compliance_check | target_hidden | 6713 | 840 | 0.6454 | [0.6089, 0.6761] | 0.6406 |
| dispatcher_intent_tool_call | instruction_only | 5456 | 682 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| operation_ticket_check | instruction_only | 8705 | 1088 | 0.2307 | [0.2127, 0.2525] | 0.2956 |
| regulation_compliance_check | instruction_only | 6713 | 840 | 0.6561 | [0.6196, 0.6861] | 0.6521 |

The exact-surface component report and the canonical input hash are recorded in the JSON receipt.
