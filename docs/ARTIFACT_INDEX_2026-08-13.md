# Current artifact index

This index separates current submission evidence from historical diagnostics. The current
hash-bound set is enumerated in `metadata/evidence_binding_manifest.json`; the reviewer-facing
subset is `release/reviewer_access_manifest_2026-08-13.json`.

## Current evidence

| Area | Current artifact | Interpretation |
| --- | --- | --- |
| Canonical data | `data/gridinstruct_v1.2_sd_core_en.jsonl` | 95,479 English-derived records |
| Task-balanced split | `reports/instruction_surface_balanced_split_v1.2_sd_core.json` | 14,257/4,735/4,696/71,791 records; exact normalized-instruction collisions zero; character-similarity warning retained |
| Template-family holdout | `reports/template_family_holdout_v1.2_sd_core.json` | 38,775/5,557/11,089 records; zero atomic group overlap; compliance macro-F1 0.961 |
| Embedded OPF contract | `reports/current_opf_closed_loop_audit_v1.2_sd_core.json` | 320 records over 160 cases pass released-field checks |
| Core-4 N-1 denominator | `reports/core_n1_denominator_v1.2_sd_core.json` | 1,896 registered IEEE14/30/57/118 line-outage states; 1,772 converge and 124 fail with recorded solver/islanding status |
| Robust IEEE14 candidates | `simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json` | 120 selected candidate rows bound to the 120-candidate acceptance report |
| Active-load stress replay | `simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json` | Full 7,680-case Cartesian replay passes; published-control replay uses the recorded 0.02 MW/Mvar storage-rounding tolerance |
| Constant-power-factor replay | `simulation_outputs/opf_closed_loop/opf_action_constant_power_factor_stress_cases_v1.json` | Full 7,680-case Cartesian replay passes under jointly scaled P/Q demand with the same recorded storage-rounding tolerance |
| Native fixed-control replay | `reports/independent_solver_validation_v1.2_sd_core_rebound.json` | 160/160 registered IEEE14/IEEE118 replays pass; fixed-control AC power-flow scope |
| Independent OPF envelope | `reports/independent_opf_envelope_v1.2_sd_core.json` | 9/9 selected solves converge; no global-optimality claim |
| All-family envelope | `reports/pglib_network_envelope_replay_v1.2_sd_core.json` | 26/33 attempts converge over 11 pinned families; failed attempts remain visible |
| LaTeX package | `paper/scientific_data_latex/LATEX_BUILD_REPORT.json` | 36 main pages = 36 embedded pages; 10 figures; 35 bibliography entries; hard errors, undefined references, and overfull boxes are zero |

## Historical or diagnostic artifacts

Reports with older dates or older split contracts remain only when they are needed to reproduce
the evidence history. For example, `reports/template_holdout_split_v1.2_sd_core.md` describes a
previous input-surface grouping (38,623/5,682/11,116) and is not the current template-family
holdout. `reports/high_score_plausibility_audit_v1.2_sd_core.md` contains a separate historical
score geometry and is not used to overwrite the current 0.961 template-family result. These files
must not be cited as current evidence without their configuration name.

The removed `_materialize_*` report snapshots were stale logs containing `/data/gaiav2` paths and
service-endpoint details; they were not bound by the current manifest. Legacy strict, challenge,
template-holdout, instruction-surface split JSONL files, the translation cache, and Python cache
directories were removed because they were regenerated derivatives outside the current bound
release; their generation scripts remain available.

## Open submission gates

The local package still lacks the raw scenario/candidate ledgers, full solver arrays, completed
double human review, public repository and DOI, final author metadata, and isolated archive replay.
Those gates remain explicit in `reports/scientific_data_submission_gate.json` and are not inferred
from the current local receipts.
