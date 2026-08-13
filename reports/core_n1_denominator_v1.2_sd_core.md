# Core-4 full N-1 line denominator audit

- Status: `pass`
- Registered system-load strata: 28
- Expected attempts: 1896
- Observed attempts: 1896
- Converged attempts: 1772
- Classified non-converged attempts: 124

| System | Load strata | In-service lines | Expected attempts |
| --- | ---: | ---: | ---: |
| IEEE118 | 6 | 175 | 1050 |
| IEEE14 | 7 | 17 | 119 |
| IEEE30 | 8 | 34 | 272 |
| IEEE57 | 7 | 65 | 455 |

## Hard gates

- `scope_is_exactly_four_core_networks`: PASS
- `registered_load_levels_are_formal`: PASS
- `pglib_registry_and_loaded_sources_match`: PASS
- `simulation_generation_report_passed`: PASS
- `registered_load_levels_match_generation_report`: PASS
- `expected_and_attempt_identity_sets_match`: PASS
- `attempt_identity_keys_unique`: PASS
- `attempt_scenario_ids_unique_and_nonempty`: PASS
- `attempt_rows_are_contract_complete`: PASS
- `converged_attempt_subset_matches_core_truth`: PASS
- `complete_truth_subset_matches_core_truth`: PASS
- `core_truth_ids_unique_and_nonempty`: PASS
- `complete_truth_ids_unique_and_nonempty`: PASS
- `failed_attempts_are_excluded_from_released_truth`: PASS
- `all_input_hashes_present`: PASS
