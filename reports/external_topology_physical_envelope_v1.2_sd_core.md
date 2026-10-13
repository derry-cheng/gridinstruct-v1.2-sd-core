# External Topology Physical Envelope Audit

- Generated: `2026-07-09T16:48:19.034177+00:00`
- Status: `pass`
- Scenario source: `simulation_outputs/topology_stress/extended_topology_scenarios.json`
- Scenario records: 126
- Converged records: 117
- Formal external instruction records: 2768
- Formal external scenarios in release: 173
- Formal records linked to the current stress-source scenarios: 1872
- Formal scenarios linked to the current stress-source scenarios: 117

## Envelope Classes

| Class | Count | Meaning |
| --- | ---: | --- |
| emergency-stress | 62 | Stronger stress within 0.80-1.20 p.u. and at or below 300% branch loading. |
| solver-not-converged | 9 | Power-flow solver did not converge and the row is retained only as stress-audit evidence. |
| operational-stress | 15 | Outside the normal envelope but within 0.90-1.10 p.u. and at or below 120% branch loading. |
| extreme-stress | 40 | Converged stress case outside the emergency-stress envelope. |

## Network Summary

| Network | Scenarios | Converged | Dominant class | Formal instruction records |
| --- | ---: | ---: | --- | ---: |
| IEEE300 | 21 | 14 | emergency-stress | 304 |
| PEGASE1354 | 21 | 21 | emergency-stress | 704 |
| PEGASE2869 | 21 | 21 | emergency-stress | 336 |
| PEGASE89 | 21 | 21 | operational-stress | 704 |
| RTE1888 | 21 | 19 | extreme-stress | 384 |
| RTE2848 | 21 | 21 | extreme-stress | 336 |

## Interpretation

The external-topology block is explicitly separated into physical-envelope strata. Normal and operational-stress rows are closest to conventional operating envelopes, emergency-stress rows test security-boundary behaviour, and extreme-stress rows are retained as stress-test evidence rather than presented as typical field operating states.
