# Typed Configuration and Semantic-Pattern Audit

- Generated: 2026-08-30T02:55:32.499270+00:00
- Status: `pass`
- Records: 95479

This audit counts structured configuration signatures from the released table. Instruction, rationale, free-text output, and record identifiers are excluded. The result measures configuration multiplicity and template fan-out; it is not a human semantic or linguistic validation.

## Aggregate accounting

| View | Records | Unique signatures | Unique rate | Median multiplicity | P95 multiplicity | Maximum multiplicity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| typed_configuration | 95479 | 8823 | 0.0924 | 2 | 45 | 2879 |
| abstract_operating_task_pattern | 95479 | 4968 | 0.0520 | 1.0 | 66 | 3798 |

## Task-stratified accounting

| Task | Records | Typed configurations | Abstract patterns | Typed unique rate | Abstract unique rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| auxiliary_decision | 18087 | 2015 | 475 | 0.1114 | 0.0263 |
| dispatcher_intent_tool_call | 19773 | 1355 | 300 | 0.0685 | 0.0152 |
| intelligent_data_query | 17766 | 1261 | 228 | 0.0710 | 0.0128 |
| operation_ticket_check | 10881 | 582 | 582 | 0.0535 | 0.0535 |
| regulation_compliance_check | 24767 | 312 | 85 | 0.0126 | 0.0034 |
| regulation_qa | 4205 | 3298 | 3298 | 0.7843 | 0.7843 |

## Interpretation

The typed view preserves network and scenario classes plus structured task fields while excluding user-facing wording and free-text targets. The abstract view additionally collapses network and scenario identity to distinguish operating/task patterns from physical-state identity. Both views are deterministic accounting diagnostics; neither establishes independent semantic correctness, linguistic naturalness, or expert agreement.
