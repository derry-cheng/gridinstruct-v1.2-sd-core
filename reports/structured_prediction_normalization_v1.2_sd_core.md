# Structured Prediction Normalization Audit

Generated: 2026-08-04T04:11:26.834990+00:00

This report evaluates schema-aware structured prediction files and recomputes strict field-level metrics after normalizing tokenizer artifacts.

| Task | Status | Records | Raw JSON rate | Normalized JSON rate | Trailing garbage | Normalized exact | Scenario exact | Filter exact | Tool exact |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `intelligent_data_query` | pass | 412 | 1.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| `auxiliary_decision` | pass | 416 | 1.0000 | 1.0000 | 0.0000 | 0.7812 | 0.0000 | 0.0000 | 1.0000 |

## Gates

- `intelligent_data_query`: pass (all checks passed)
- `auxiliary_decision`: pass (all checks passed)
