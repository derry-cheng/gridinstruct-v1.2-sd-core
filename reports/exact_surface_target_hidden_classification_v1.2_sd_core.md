# Exact-surface target-hidden classification diagnostic

Generated: 2026-08-28T20:28:20.478615+00:00

This CPU diagnostic evaluates three closed-label tasks after assigning every character-5-gram component to one split. The target-hidden profile removes target-carrying fields before fitting a deterministic character TF--IDF linear SVC. The scores quantify learnability under a lexical near-duplicate stress split; they do not establish semantic independence, physical-label validity, or dispatch competence.

| task | input profile | train | test | macro-F1 | 95% CI | balanced accuracy |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| dispatcher_intent_tool_call | full_contract | 1025 | 129 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| operation_ticket_check | full_contract | 7772 | 972 | 0.9987 | [0.9961, 1.0000] | 0.9988 |
| regulation_compliance_check | full_contract | 2128 | 266 | 0.9294 | [0.8988, 0.9591] | 0.9458 |
| dispatcher_intent_tool_call | target_hidden | 1025 | 129 | 1.0000 | [1.0000, 1.0000] | 1.0000 |
| operation_ticket_check | target_hidden | 7772 | 972 | 0.9987 | [0.9961, 1.0000] | 0.9988 |
| regulation_compliance_check | target_hidden | 2128 | 266 | 0.8944 | [0.8562, 0.9289] | 0.9160 |
| dispatcher_intent_tool_call | instruction_only | 1025 | 129 | 0.3603 | [0.2847, 0.4461] | 0.3579 |
| operation_ticket_check | instruction_only | 7772 | 972 | 0.9953 | [0.9902, 0.9990] | 0.9953 |
| regulation_compliance_check | instruction_only | 2128 | 266 | 0.8873 | [0.8485, 0.9236] | 0.9086 |

The exact-surface component report and the canonical input hash are recorded in the JSON receipt.
