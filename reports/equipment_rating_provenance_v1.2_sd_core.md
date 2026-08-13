# Equipment Rating Provenance Audit

- Generated: `2026-07-31T08:09:32.153492+00:00`
- Status: `pass`
- Networks: 11
- In-service equipment audited: 15835
- Unknown or placeholder ratings: 0
- Source-explicit high limits: 7

A converged power flow is not accepted as thermal-security evidence when any
in-service branch rating is missing, invalid, or encoded by a known placeholder.

| Network | Audited | Known | Explicit high | Unknown | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| ieee14 | 25 | 25 | 0 | 0 | PASS |
| ieee30 | 47 | 47 | 0 | 0 | PASS |
| ieee57 | 87 | 87 | 0 | 0 | PASS |
| ieee118 | 240 | 240 | 0 | 0 | PASS |
| ieee300 | 480 | 473 | 7 | 0 | PASS |
| illinois200 | 283 | 283 | 0 | 0 | PASS |
| pegase89 | 222 | 222 | 0 | 0 | PASS |
| pegase1354 | 2251 | 2251 | 0 | 0 | PASS |
| rte1888 | 2821 | 2821 | 0 | 0 | PASS |
| rte2848 | 4287 | 4287 | 0 | 0 | PASS |
| pegase2869 | 5092 | 5092 | 0 | 0 | PASS |

## Gate

Every in-service line, two-winding transformer, three-winding transformer,
and controllable generator must have a finite source-bound rating. Overrides
are accepted only with a source document identifier, SHA-256, and locator.
