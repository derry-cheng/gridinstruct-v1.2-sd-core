# Split Independence Audit

Generated: 2026-08-04T04:26:15.709761+00:00
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
| train | 28684 | 550 | 20790 | 9195 |
| validation | 3598 | 65 | 2576 | 1094 |
| test | 3609 | 66 | 2585 | 1114 |
| ood_test | 59588 | 1915 | 53847 | 6557 |
| challenge_train | 44339 | 2453 | 36248 | 9343 |
| challenge_validation | 5745 | 217 | 2223 | 2223 |
| challenge_test | 5337 | 494 | 2029 | 2029 |

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
- `test__train`: 0
- `test__validation`: 0
- `train__validation`: 0
### source_record_id_standard
- `ood_test__test`: 0
- `ood_test__train`: 0
- `ood_test__validation`: 0
- `test__train`: 0
- `test__validation`: 0
- `train__validation`: 0
### source_group_standard
- `ood_test__test`: 0
- `ood_test__train`: 0
- `ood_test__validation`: 0
- `test__train`: 0
- `test__validation`: 0
- `train__validation`: 0
### record_id_challenge
- `challenge_test__challenge_train`: 0
- `challenge_test__challenge_validation`: 0
- `challenge_train__challenge_validation`: 0
### scenario_id_challenge
- `challenge_test__challenge_train`: 494
- `challenge_test__challenge_validation`: 34
- `challenge_train__challenge_validation`: 217

## Interpretation

Record identifiers are isolated across released splits. Standard train/validation/test scenario overlap is reported as a diagnostic because multiple task surfaces can originate from the same physical scenario. OOD isolation is evaluated separately and must remain scenario-disjoint from standard splits.

## Figures

- overlap_diagnostics: `figures/sd_core/fig_split_independence_overlap.png`
