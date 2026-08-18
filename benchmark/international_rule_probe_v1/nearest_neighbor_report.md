# International rule-probe retrieval diagnostic

This CPU-only character TF--IDF nearest-neighbour baseline copies the answer from the closest training record.
It is a lexical-transfer diagnostic, not a semantic compliance or expert-agreement result.

| Fold | Train jurisdiction | Test jurisdiction | Test records | Exact match | Token-F1 | Mean similarity |
|---|---|---|---:|---:|---:|---:|
| train_european_union_test_nerc_north_america | European Union | NERC North America | 256 | 0.0000 | 0.3545 | 0.7889 |
| train_nerc_north_america_test_european_union | NERC North America | European Union | 256 | 0.0000 | 0.3519 | 0.7832 |

Macro exact match: **0.0000**; macro token-F1: **0.3532**.
The held-out folds contain different rule-card IDs, so same-rule and same-standard retrieval rates are reported only as diagnostics.
No cross-jurisdiction semantic generalization claim is made without independent expert review.
