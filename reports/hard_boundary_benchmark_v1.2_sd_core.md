# Hard Boundary Benchmark

Generated: 2026-07-09T17:09:42.955576+00:00
Status: `pass`

This benchmark uses masked scenario identifiers and numeric values so that lightweight models cannot rely on exact case ids.

## Results

| task | train | test | macro-F1 | balanced accuracy | surface purity |
| --- | ---: | ---: | ---: | ---: | ---: |
| operation_ticket_check | 1620 | 1080 | 1.0000 | 1.0000 | 1.0000 |
| dispatcher_intent_tool_call | 1620 | 1080 | 1.0000 | 1.0000 | 1.0000 |
| regulation_compliance_check | 1362 | 906 | 0.9418 | 0.9415 | 1.0000 |

## Interpretation

The hard-boundary benchmark is a diagnostic stress set for tasks with high standard-split scores. It is derived from released records, masks exact scenario identifiers and numeric values, and reports task-specific lightweight baseline scores beside label-support and surface-purity diagnostics.
