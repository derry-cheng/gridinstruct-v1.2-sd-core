# International rule-probe retrieval diagnostic

This CPU-only character TF--IDF nearest-neighbour baseline transfers the typed rule contract from the closest training record.
Explicit rule, standard, clause, and jurisdiction identifiers are excluded from its input.

| Fold | Type | Train jurisdiction | Test jurisdiction | N | Rule acc. | Standard acc. | Evidence F1 | Answer exact |
|---|---|---|---|---:|---:|---:|---:|---:|
| train_european_union_test_nerc_north_america | cross_jurisdiction | European Union | NERC North America | 256 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| train_nerc_north_america_test_european_union | cross_jurisdiction | NERC North America | European Union | 256 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| within_european_union_variant_holdout | within_jurisdiction_variant_holdout | European Union | European Union | 64 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| within_nerc_north_america_variant_holdout | within_jurisdiction_variant_holdout | NERC North America | NERC North America | 64 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |

Overall macro rule-card accuracy: **0.5000**; overall macro standard accuracy: **0.5000**; overall macro evidence-field F1: **0.5000**.
Cross-jurisdiction rule-card accuracy: **0.0000**; within-jurisdiction variant-holdout rule-card accuracy: **1.0000**.
Copied-answer exact match is a secondary diagnostic (**0.0000**).
No cross-jurisdiction semantic generalization claim is made without independent expert review.
