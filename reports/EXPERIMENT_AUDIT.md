# Final experiment-integrity audit (2026-08-06)

**Verdict: SCOPED PASS — local metrics and their code bindings are reproducible; external release gates remain open.** The audit found no model-generated ground truth, self-normalized score, altered denominator, best-seed selection, or prediction/label overlap in the checked scripts.

## A. Ground-truth provenance — PASS

Transformer, TF–IDF, target-hidden, structured-query, and auxiliary-tool evaluations read targets from released row fields. The structured baselines do not use a hidden gold fallback; the query baseline copies only the exposed `scenario_id` input field. The dataset targets remain simulation/template-derived and are not field-operator labels.

## B. Metrics and 1.000 values — WARN, repaired in code and prose

Accuracy, macro-F1, balanced accuracy, exact match, token-F1, and the five-seed mean/sample standard deviation use explicit test-set denominators. The structured baselines now emit `deterministic_serialization_validity` and majority-label diagnostics. Their JSON value is excluded from performance interpretation because each script serializes an in-memory prediction object and immediately parses it. The auxiliary clean test has one observed sequence label (145/145); its OOD support is 16,096/305 and the majority accuracy is 0.981. These support counts and majority baselines are now bound in `structured_auxiliary_report.json`.

## C. Result-to-claim closure — SCOPED PASS

The following are directly bound and rechecked: the 95,479-row count; near-neighbour-free split counts and sensitivity; near-neighbour-free TF–IDF results; structured query/tool results; five-seed DistilBERT summaries; and the 320-record/160-scenario OPF counts. The actual TF–IDF entry point is `scripts/run_tfidf_task_baselines.py`, and its SHA-256 is recorded in the report and remediation manifest.

The official, strict, template-holdout, proxy-reduced, challenge, near-neighbour-free, target-hidden, structured, OPF, rule, translation, and native-replay reports are now explicitly listed in the claim-evidence map with real nested JSON paths. All six TF–IDF reports were rerun from the current entry point and contain the matching `code_sha256`; standard and near-neighbour-free structured reports likewise contain code hashes, support counts, majority diagnostics, and deterministic-serialization fields.

## D. Metric invocation — PASS

The checked scripts call the metrics stated in the manuscript. ECE is defined for reuse, but no ECE value is promoted in the manuscript.

## E. Scope — WARN

The five seeds are fixed as `[13, 29, 42, 57, 71]` with no best-seed selection. Ticket and intent neural references use one epoch and compliance uses three epochs; they are reproducible warm-start references, not converged upper bounds. The MinHash audit is an approximate 64-permutation MinHash–LSH detector; the manuscript now says “detected components” and does not imply exact semantic deduplication. The near-neighbour-free manifests are ID-only and the auxiliary split is not label-stratified.

## F. Evaluation-type classification — PASS

The neural, TF–IDF, structured, and OPF field audits are `simulation_only`; MinHash is a `self_supervised_proxy`; the 1,600-row external double review is `human_eval` and remains at 0 completed assignments. No human agreement statistic is reported.

## Required external gates

The local candidate remains blocked for Scientific Data release until the raw scenario/candidate ledgers, full solver case package, public repository and DOI, completed double review, and final author/funding metadata are supplied. These are external state changes and cannot be represented by a local score.
