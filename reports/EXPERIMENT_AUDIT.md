# Experiment-integrity audit

**Audit date:** 2026-08-29
**Overall verdict:** WARN — the current local metrics are reproducible and
their stale dispatcher-intent values have been removed from the manuscript;
simulation scope, two solver-bound query records, human review, and data
deposition remain explicit boundaries.

## Ground-truth provenance — PASS

The physical scenario registry is produced by the deterministic pandapower
construction in scripts/generate_grid_scenarios.py:397-499,502-652.
Independent query validation reads the registry, rebuilds the network, executes
the query, and compares complete result sets in
scripts/validate_query_truth_independent.py:249-373. The English release is
rendered from typed contracts by scripts/regenerate_direct_english_core.py:1-9,72-249;
it does not call a translation service or use model predictions as targets.
Classification and generation baselines read gold fields from the released
rows, as shown in scripts/run_tfidf_task_baselines.py:97-215 and
scripts/run_structured_query_filter_baseline.py:93-166.

The evidence is therefore simulation- or contract-grounded. It is not
operator-annotated ground truth and it does not establish legal interpretation
or field dispatch correctness.

## Metric denominators and normalization — PASS

Classification metrics use the released label field and the number of
evaluation rows; generation metrics compare each prediction with the released
output on the same task. The implementation is in
scripts/run_tfidf_task_baselines.py:97-215,249-274. The calibrated baseline
checks finite, non-negative probabilities with unit row sums and computes ECE
and multiclass Brier scores against released labels in
scripts/run_calibrated_tfidf_baseline.py:59-96,129-170. No metric is divided
by a statistic of the model's own predictions, and no best seed is selected.

Structured baselines copy only the exposed scenario_id input field. They
report schema-field exactness separately from full-contract exactness, and
their JSON serialization check is explicitly a format diagnostic in
scripts/run_structured_query_filter_baseline.py:93-166,217-238 and
scripts/run_structured_auxiliary_tool_baseline.py:80-145,199-220.

## Result-to-claim closure — PASS after current repair

The current reports are bound to the 18a4d361... canonical SHA-256. Official
TF-IDF intent macro-F1 is 1.000 on both test and OOD; instruction-surface
intent is also 1.000 on both partitions. The five-seed surface audit gives
0.9983 (0.0035) on test and 1.0000 (0.0000) on OOD. The corresponding
calibration values are official ECE/Brier 0.049/0.004 on test and OOD, and
surface ECE/Brier 0.210/0.069 on test and 0.211/0.068 on OOD. The manuscript
now prints these values, and scripts/audit_manuscript_metric_bindings.py
passes all 20 checks, including an explicit stale-value absence check.

The dispatcher-request repair receipt
reports/dispatcher_request_contract_repair_v1.2_sd_core.json records
95,479 retained rows, 936 to 0 conflicting prompt-input groups, and 17 to 0
official cross-split prompt-input duplicates. This repair changes the
learnability interpretation: the intent target is observable in the natural
request semantics and the resulting 1.000 score is a surface diagnostic.

## Metric invocation and dead code — PASS

The reported TF-IDF, calibrated, structured-query, structured-auxiliary, and
target-hidden metrics are called by their current entry points. The five-seed
and split-specific reports retain fixed denominators, input hashes, and
prediction receipts. The audit does not promote the compact-row numeric
severity parser because the released summaries omit maximum voltage for
31,485 rows; that parser is retained as an inconclusive diagnostic in
reports/current_release_integrity_audit_v1.2_sd_core.json.

## Scope and evaluation type — WARN

The core table contains 95,479 simulation- and rule-contract records. The
scenario registry has 2,821 converged states out of 2,822 requested states.
Independent query replay covers 17,764 of 17,766 query records; two records
linked to the unsupplied-island state remain solver-bound. The fixed-control
electrical replay covers 160 cases, and the selected independent OPF envelope
covers nine solves. These denominators support bounded reconstruction claims,
not population-wide AC-OPF optimality or autonomous dispatch.

The official split is record-ID disjoint and holds out the registered OOD
scenario strata, while development scenario and source-group overlaps are
reported diagnostics. The strict split is the five-key provenance-isolated
stress split. The instruction-surface OOD partition is distributed as an
ID-only manifest and materialized from the canonical table. Residual
character similarity remains a warning diagnostic.

The evaluation classes are simulation_only for scenario-grounded tasks,
synthetic_proxy for rule-card and contract probes,
self_supervised_proxy for MinHash/lexical similarity diagnostics, and
human_eval for the prepared external assignments. Human completion is
0/1,600; no agreement statistic is claimed.

## External gates

The local evidence package still lacks the raw population-level scenario and
candidate ledgers, a persistent data DOI, final deposition metadata, and
completed independent human review. These are external state changes and are
not inferred from local receipts. The package is therefore suitable for
scoped technical inspection but remains not submission-ready.
