# Strict Source-Group Split

Generated: 2026-07-31T09:51:12.004905+00:00
Status: `pass`

This split is an additional stress split. It assigns global provenance-connected components to train, validation, or test so source groups and scenario identifiers do not cross strict split boundaries.

## Split Summary

| split | records | source groups | scenarios |
| --- | ---: | ---: | ---: |
| train | 76350 | 14415 | 2081 |
| validation | 9570 | 1761 | 259 |
| test | 9559 | 1784 | 256 |

## Hard Gates

- `all_splits_nonempty`: pass
- `record_id_disjoint`: pass
- `source_group_disjoint`: pass
- `scenario_id_disjoint`: pass
- `source_record_id_disjoint`: pass
- `source_simulation_case_id_disjoint`: pass
- `operation_group_disjoint`: pass
- `all_tasks_in_train`: pass
- `classification_targets_in_test`: pass

## Figures

- strict_group_overlap: `figures/sd_core/fig_strict_split_group_overlap.png`
