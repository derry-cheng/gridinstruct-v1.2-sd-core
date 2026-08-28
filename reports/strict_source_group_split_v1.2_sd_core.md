# Strict Source-Group Split

Generated: 2026-08-28T21:19:00.386935+00:00
Status: `pass`

This split is an additional stress split. It assigns global provenance-connected components to train, validation, or test so source groups and scenario identifiers do not cross strict split boundaries.

## Split Summary

| split | records | source groups | scenarios |
| --- | ---: | ---: | ---: |
| train | 76378 | 14455 | 2079 |
| validation | 9558 | 1747 | 256 |
| test | 9543 | 1758 | 261 |

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
