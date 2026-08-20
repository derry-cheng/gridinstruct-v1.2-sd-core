# Script index

The project keeps entry points flat so that the commands recorded in the evidence
receipts remain stable. This index provides the hierarchy for reuse without changing
those paths.

| Area | Entry points |
| --- | --- |
| Physical construction | `generate_grid_scenarios.py`, `recompute_scenario_truth.py`, `generate_extended_topology_stress.py`, `generate_opf_secure_candidate_augmentation.py` |
| Dataset and splits | `generate_instruction_data.py`, `create_splits.py`, `create_group_aware_splits.py`, `create_strict_source_group_splits.py`, `create_template_holdout_splits.py`, `create_instruction_surface_balanced_splits.py`, `create_exact_surface_components.py`, `create_deterministic_surface_components.py` |
| Audits and evidence | `validate_dataset.py`, `validate_cross_solver_power_flow.py`, `validate_independent_solver_evidence.py`, `run_linear_seed_stability_audit.py`, `run_current_surface_seed_stability.py`, `run_international_rule_probe_controls.py`, `run_shortcut_ablation_audit.py`, `run_project_milestone_audit.py` |
| Baselines | `run_tfidf_task_baselines.py`, `run_transformer_classifier_baseline.py`, `run_target_hidden_classification.py`, `run_exact_surface_target_hidden.py`, `run_calibrated_tfidf_baseline.py`, `run_structured_query_filter_baseline.py`, `run_structured_auxiliary_tool_baseline.py` |
| Figures and manuscript | `generate_publication_sd_figures.py`, `generate_sd_figures.py`, `sync_manuscript_claims_from_evidence.py`, `build_reviewer_access_manifest.py` |

Temporary split materializations, stopped checkpoints, and scratch solver outputs belong
under `/tmp` and are excluded from the project tree. The canonical table is stored once
under `data/`; ID-only manifests avoid multiplying the 510 MB record table.
