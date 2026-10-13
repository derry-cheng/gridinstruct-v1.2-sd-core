# Action-Level Validation Audit

- Generated: 2026-08-08T17:26:36.303178+00:00
- Status: `warn_no_independent_scenario_recompute`
- Scenario lookup available: `False`
- Compliance recompute source: `release_integrity_vs_current_release_rows`
- Auxiliary decision records: 18087
- Auxiliary records passing action-plan checks: 1.0000
- Compliance records with recomputable labels: 24767
- Compliance label agreement: 1.0000

## Issue Counts


## Scope

This audit verifies that action recommendations are tied to simulation scenarios, required validation tools, reversible-review wording, and recomputable compliance labels. When the authoritative scenarios file is mirrored locally, compliance labels are recomputed from the independently reconstructed scenario state; otherwise, the audit compares released labels with the selected local release-integrity source and does not claim an independent physical recomputation. It is not a closed-loop physical dispatch execution test.
