# Current Scientific Data Release Integrity Audit

Generated: 2026-08-28T21:40:19.640620+00:00
Dataset: `data/gridinstruct_v1.2_sd_core_en.jsonl`
Local integrity status: `pass`; release readiness: `blocked_external_gates`

| Gate | Status |
| --- | --- |
| jsonl_schema_and_unique_ids | pass |
| scenario_links_present_and_equal | pass |
| network_and_tool_links_consistent | pass |
| severity_explicit_consistency | pass |
| severity_registry_consistency | pass |
| query_contract_complete | pass |
| source_stage_row_alignment | pass |
| split_ids_disjoint | pass |
| official_ood_scenario_isolation | pass |
| strict_split_provenance_keys_disjoint | pass |
| template_family_holdout_integrity | pass |
| opf_two_variants_per_scenario | pass |

Records: 95,479; unique IDs: 95,479.
Scenario-link missing/mismatch: 0/0; severity mismatches: 0 of 55,215 checked.
Independent registry severity: 80,385 complete states checked; 8 explicit invalid boundaries; mismatches=0; missing links=0.
Numeric severity diagnostic: 31,485 partial states; 0 complete three-field states; status=inconclusive_due_to_missing_numeric_state_fields.
Solver-bound query records: 2 (retained with incomplete scenario truth).
OPF closed-loop rows/scenarios: 320/160.

The audit intentionally keeps unavailable raw replays and external expert review as failed gates. They must be regenerated or deposited before a Scientific Data submission claim can be upgraded.
