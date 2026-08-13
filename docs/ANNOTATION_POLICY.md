# Annotation Policy

Generated: 2026-08-04T04:35:47.866308+00:00

This note records the current rule-based annotation policy for Rule-based label policy for GridInstruct v1.2 release snapshot.

## Compliance Labels

| label | meaning |
| --- | --- |
| `non_compliant` | Active issue exists and the proposed action either increases constrained-flow loading, skips required checks, or restores equipment prematurely. |
| `compliant_with_monitoring` | Action is directionally corrective but still requires post-action review, continued monitoring, or staged execution checks. |
| `compliant` | No active issue exists and the proposed action is a non-escalating corrective or risk-reducing action. |

## Limitations

- This policy is task-oriented and summarizes label behavior at the dataset level rather than reproducing full operational rulebooks.
- validate_dataset.py checks explicit semantic contradictions, but it does not yet formalize every expert judgment nuance.
