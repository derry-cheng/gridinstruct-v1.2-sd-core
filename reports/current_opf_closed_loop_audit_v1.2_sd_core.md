# OPF Closed-Loop Assumption Audit

- Generated: `2026-08-05T09:39:57.098978+00:00`
- Status: `pass`
- Dataset: `data/gridinstruct_v1.2_sd_core_en.jsonl`
- OPF-derived records: 320
- Unique OPF scenarios: 160
- Tool-sequence pass rate: 1.0000
- Closed-loop field pass rate: 1.0000
- Native-bound and control-vector pass rate: 1.0000
- Executable target replay pass rate: 1.0000
- Post-action N-1 pass rate: 1.0000
- Embedded uncertainty minimum voltage margin: 0.008671 p.u.
- Mean relative violation reduction: 1.0000

## Network Summary

| Network | Records | Scenarios | Mean pre-score | Mean post-score | Mean relative reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| IEEE118 | 160 | 80 | 0.326882 | 0.000000 | 1.000000 |
| IEEE14 | 160 | 80 | 0.005050 | 0.000000 | 1.000000 |

## Modeling Assumptions

- OPF evidence is based on `pandapower.runopp` over the stored IEEE14 and IEEE118 benchmark scenarios.
- Native cost curves and device capability limits are retained. The external-grid active-power interval is moved to the robust interior of its native capability; generator P/Q and external-grid Q use full native capability or the documented finite fallback when a source limit is absent.
- A registered 90/80/70/60% pre-contingency loading search selects the first cost-minimizing dispatch that passes both embedded load-stress models. Corrective optimization uses the 0.96--1.04 p.u. and 80% internal envelopes, while final validation remains at the native-intersected 0.95--1.05 p.u. and 100% limits and requires at least 0.001 p.u. worst-case voltage margin.
- Every published action retains bidirectional external-grid headroom for a 10% active-load error plus a 2% loss buffer and passes five network-element plus two generator N-1 checks.
- The audit verifies computational closure between pre-action power flow, OPF redispatch, post-action power flow, and post-action contingency recomputation.
- The evidence supports dataset construction validity for auxiliary-decision records; it is not a field-deployment dispatch trial.
