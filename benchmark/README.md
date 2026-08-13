# Benchmark artifact layout

The current reproducible baselines are grouped by evaluation regime:

- `current_v1.2_sd_core/` contains the official, strict, template-holdout, proxy-reduced, and challenge CPU references.
- `instruction_surface_balanced_v1.2_sd_core/` contains the task-balanced surface split, query/tool contract replay, TF--IDF scores, calibrated probabilities, and prediction hashes.
- `near_neighbor_free_v1.2_sd_core/` and `near_neighbor_free_multiseed_v1.2_sd_core/` retain historical detected-component diagnostics and fixed-budget MPS references. Their original temporary materialization paths are historical metadata; the stable ID manifests under `data/` are the canonical split definition.
- `multiseed_v1.2_sd_core/` contains the earlier registered seed summaries.

Root-level files are retained for backward-compatible pipeline entry points. New experiments should write into a regime-specific subdirectory and include input, code, prediction, and environment hashes in the report.
