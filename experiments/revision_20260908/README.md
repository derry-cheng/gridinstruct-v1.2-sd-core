# 2026-09-08 reviewer revision experiments

All experiments run locally in the conda base environment. The fixed-control replay uses six processes and one numerical-library thread per process. The text and retrieval experiments use one thread. No GPU or external training service is used.

Run from the repository root:

```sh
python scripts/revision_20260908/c1_fixed_control.py --workers 6
python scripts/revision_20260908/c2_build_valid_duration_v2.py
python scripts/revision_20260908/c2_fair_input_evaluation.py --data-dir data/revision_20260908/c3_compositional_v2 --output-prefix c2_v2
python scripts/revision_20260908/c2_v2_similarity.py
python scripts/revision_20260908/c4_source_card_retrieval.py
python scripts/revision_20260908/c5_build_supplement.py
python scripts/revision_20260908/plot_evaluations.py
```

Code is under `scripts/revision_20260908`, data under `data/revision_20260908`, row-level predictions and replay outcomes under `results/revision_20260908`, reports under `reports/revision_20260908`, and manuscript sources under `paper/scientific_data_latex`.

The C1 test applies each published base control to all eight registered topologies and six demand levels under both demand models. It does not substitute contingency-specific corrective controls. Its complete denominator is 160 × 8 × 6 × 2 = 15,360 states. It enforces existing electrical criteria and the 0.001 p.u. voltage margin and records deviations between commanded and realized generator voltage under reactive-limit saturation. A complete run does not imply the acceptance claim passes.

C2 v2 retains the original 2,880 IDs, labels and partitions while enforcing the duration domain and cleaning a duplicated sentence prefix. The published-template text parser is a format-specific recoverability reference. Its training-free grammar is not an independently blind language-generalization experiment. C4 evaluates 32 fixed author-written operational queries over all eight source cards; it does not certify regulatory interpretations.

The current strong preventive-control requirement remains open. Real paired human review, complete historical construction ledgers, and formal data deposition also require completion. Do not infer submission readiness from compilation, package structure, or unit tests.
