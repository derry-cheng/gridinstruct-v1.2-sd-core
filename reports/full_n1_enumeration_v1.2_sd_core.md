# Full N-1 Enumeration Audit

- Generated: `2026-07-31T08:10:31.505621+00:00`
- Status: `pass`
- Enumerated events: 789

Every in-service line, two-winding transformer, and non-slack generator
is included in the denominator. Islanding, unsupplied load, non-convergence,
and unknown ratings remain in the denominator and cannot be reported as safe.

| Network | Load | Line | Transformer | Generator | Total | Within limits | Rating indeterminate | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| ieee14 | 0.500 | 15 | 5 | 4 | 24 | 0 | 0 | PASS |
| ieee14 | 0.600 | 15 | 5 | 4 | 24 | 0 | 0 | PASS |
| ieee14 | 0.700 | 15 | 5 | 4 | 24 | 0 | 0 | PASS |
| ieee118 | 0.500 | 173 | 13 | 53 | 239 | 0 | 0 | PASS |
| ieee118 | 0.600 | 173 | 13 | 53 | 239 | 0 | 0 | PASS |
| ieee118 | 0.700 | 173 | 13 | 53 | 239 | 0 | 0 | PASS |
