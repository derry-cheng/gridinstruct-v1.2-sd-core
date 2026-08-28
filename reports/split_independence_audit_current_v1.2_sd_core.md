# Split Independence Audit

Generated: 2026-08-28T21:19:25.771181+00:00
Status: `pass`

This audit separates record-level isolation, scenario-level diagnostics, source-record diagnostics, and OOD isolation so that split claims use a single scope.

## Hard Gates

- `standard_record_id_disjoint`: True
- `challenge_record_id_disjoint`: True
- `ood_scenario_disjoint_from_standard`: True
- `all_released_splits_nonempty`: True

## Split Summary

| split | records | scenarios | source_records | source_groups |
| --- | ---: | ---: | ---: | ---: |
| train | 26545 | 665 | 20129 | 9115 |
| validation | 3561 | 295 | 3379 | 1585 |
| test | 3894 | 368 | 3706 | 1643 |
| ood_test | 61479 | 1915 | 55633 | 8343 |
| challenge_train | 0 | 0 | 0 | 0 |
| challenge_validation | 0 | 0 | 0 | 0 |
| challenge_test | 0 | 0 | 0 | 0 |

## Overlap Diagnostics

### record_id_standard
- `ood_test__test`: 0
- `ood_test__train`: 0
- `ood_test__validation`: 0
- `test__train`: 0
- `test__validation`: 0
- `train__validation`: 0
### scenario_id_standard
- `ood_test__test`: 0
- `ood_test__train`: 0
- `ood_test__validation`: 0
- `test__train`: 360
- `test__validation`: 187
- `train__validation`: 287
### source_record_id_standard
- `ood_test__test`: 171
- `ood_test__train`: 647
- `ood_test__validation`: 147
- `test__train`: 1100
- `test__validation`: 320
- `train__validation`: 1134
### source_group_standard
- `ood_test__test`: 171
- `ood_test__train`: 647
- `ood_test__validation`: 147
- `test__train`: 1008
- `test__validation`: 261
- `train__validation`: 1017
### record_id_challenge
- `challenge_test__challenge_train`: 0
- `challenge_test__challenge_validation`: 0
- `challenge_train__challenge_validation`: 0
### scenario_id_challenge
- `challenge_test__challenge_train`: 0
- `challenge_test__challenge_validation`: 0
- `challenge_train__challenge_validation`: 0

## Interpretation

Record identifiers are isolated across released splits. Standard train/validation/test scenario overlap is reported as a diagnostic because multiple task surfaces can originate from the same physical scenario. OOD isolation is evaluated separately and must remain scenario-disjoint from standard splits.

## Figures

- overlap_diagnostics: `figures/sd_core/fig_split_independence_overlap.png`
