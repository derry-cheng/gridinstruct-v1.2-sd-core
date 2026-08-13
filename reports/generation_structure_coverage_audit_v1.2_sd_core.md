# Generation Structure Coverage Audit

Generated: 2026-08-04T04:11:04.336919+00:00
Status: `pass`

This audit reports aggregate structure coverage for formal generation tasks. It distinguishes text-generation, structured-query, and tool-plan metrics so non-applicable metric fields are not interpreted as failures.

## Gates

- `all_formal_generation_reports_present`: pass
- `prediction_counts_match_reports`: pass
- `normalization_report_passed`: pass
- `metric_applicability_documented`: pass
- `no_empty_prediction_files`: pass

## Formal Runs

| task | n | named overlap F1 | JSON validity | structured field accuracy | tool Jaccard |
| --- | ---: | ---: | ---: | ---: | ---: |
| regulation_qa | 419 | 0.8072746532282353 | NA | NA | NA |
| auxiliary_decision | 448 | 0.9255203565884278 | 0.0 | NA | NA |
| intelligent_data_query | 437 | 0.9870428247973714 | 0.0 | NA | NA |

## Metric Applicability

| task | metric | applicability | reported value |
| --- | --- | --- | ---: |
| regulation_qa | token_f1 | primary text-overlap metric | None |
| auxiliary_decision | char_f1/canonical_json_exact/schema_valid/tool_set_exact/tool_set_jaccard | applies to tool-plan style auxiliary targets | None |
| intelligent_data_query | char_f1/canonical_json_exact/schema_valid/structured_query_exact/field_accuracy | applies to structured query targets | None |
| intelligent_data_query | tool_set_exact/tool_set_jaccard | not a tool-plan target; reported as not applicable for query claims | 0.0 |
| auxiliary_decision | structured_query_exact/field_accuracy | not a structured-query target; reported as not applicable for auxiliary claims | 0.0 |

## Figures

- generation_structure_coverage: `figures/sd_core/fig_generation_structure_coverage.png`
