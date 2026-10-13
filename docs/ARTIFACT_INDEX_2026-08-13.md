# Current artifact index

This index separates current submission evidence from historical diagnostics. The current
hash-bound set is enumerated in `metadata/evidence_binding_manifest.json`; the reviewer-facing
subset is `release/reviewer_access_manifest_2026-08-13.json`.

## Current evidence

| Area | Current artifact | Interpretation |
| --- | --- | --- |
| Canonical data | `data/gridinstruct_v1.2_sd_core_en.jsonl` | 95,479 English records rendered from typed contracts |
| Task-balanced split | `reports/instruction_surface_balanced_split_v1.2_sd_core.json` | 10,689/5,118/4,432/75,240 records; exact normalized-instruction collisions zero; character-similarity warning retained |
| Template-family holdout | `reports/template_family_holdout_v1.2_sd_core.json` | 38,775/5,557/11,089 records; zero atomic group overlap; compliance macro-F1 0.944 |
| Embedded OPF contract | `reports/current_opf_closed_loop_audit_v1.2_sd_core.json` | 320 records over 160 cases pass released-field checks |
| Core-4 N-1 denominator | `reports/core_n1_denominator_v1.2_sd_core.json` | 1,896 registered IEEE14/30/57/118 line-outage states; 1,772 converge and 124 fail with recorded solver/islanding status |
| Robust IEEE14 candidates | `simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json` | 120 selected candidate rows bound to the 120-candidate acceptance report |
| Active-load stress replay | `simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json` | Full 7,680-case Cartesian replay passes; published-control replay uses the recorded 0.02 MW/Mvar storage-rounding tolerance |
| Constant-power-factor replay | `simulation_outputs/opf_closed_loop/opf_action_constant_power_factor_stress_cases_v1.json` | Full 7,680-case Cartesian replay passes under jointly scaled P/Q demand with the same recorded storage-rounding tolerance |
| Native fixed-control replay | `reports/independent_solver_validation_v1.2_sd_core_rebound.json` | 160/160 registered IEEE14/IEEE118 replays pass; fixed-control AC power-flow scope |
| Independent OPF envelope | `reports/independent_opf_envelope_v1.2_sd_core.json` | 9/9 selected solves converge; no global-optimality claim |
| All-family envelope | `reports/pglib_network_envelope_replay_v1.2_sd_core.json` | 26/33 attempts converge over 11 pinned families; failed attempts remain visible |
| LaTeX package | `paper/scientific_data_latex/LATEX_BUILD_REPORT.json` | 34 main pages = 34 embedded pages; 10 figure inclusions; 41 bibliography entries; hard errors, undefined references, and overfull boxes are zero |

## Historical or diagnostic artifacts

Reports with older dates or older split contracts remain only when they are needed to reproduce
the evidence history. For example, `reports/template_holdout_split_v1.2_sd_core.md` describes a
previous input-surface grouping and is not the current template-family holdout. The current
`reports/high_score_plausibility_audit_v1.2_sd_core.md` is regenerated from the atomic
template-family receipt and reports its current 0.944 compliance macro-F1. These files
must not be cited as current evidence without their configuration name.

The removed `_materialize_*` report snapshots were stale logs containing `/data/gaiav2` paths and
service-endpoint details; they were not bound by the current manifest. Strict source-group
projections were regenerated from the current canonical table and are now included in the
hash-bound local package. Challenge, template-holdout, and instruction-surface derivatives remain
named diagnostics; obsolete intermediate caches and Python cache directories remain excluded from
the release, while their generation scripts are retained.

## Open submission gates

The local package still lacks the raw scenario/candidate ledgers, full solver arrays, completed
double human review, persistent data DOI, and final author metadata. The compact archive replay
passes within its declared scope; the remaining gates remain explicit in
`reports/scientific_data_submission_gate.json` and are not inferred from the current local receipts.
