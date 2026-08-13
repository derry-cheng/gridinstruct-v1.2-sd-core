# External Network × Severity Matched-Strata Audit

- Generated: `2026-07-31T08:11:00.679650+00:00`
- Status: `pass`
- Target per cell: 13
- Full-factorial diagnostic shortage: 207
- Common-support networks: illinois200, pegase1354
- Common-support severities: normal-envelope, emergency-stress
- Raw Cramer's V: 0.746287
- Conditional matched Cramer's V: 0.000000

The 7×4 table is a transparent support diagnostic. Unobserved cells are not imputed and are not claimed physically unreachable. Balance and conditional association are evaluated only in the fully observed common-support block.

| Network | Normal | Operational | Emergency | Extreme |
| --- | ---: | ---: | ---: | ---: |
| ieee300 | 0 | 0 | 0 | 26 |
| illinois200 | 128 | 37 | 17 | 0 |
| pegase89 | 0 | 63 | 1 | 1 |
| pegase1354 | 13 | 12 | 27 | 0 |
| rte1888 | 0 | 0 | 0 | 26 |
| rte2848 | 0 | 0 | 0 | 39 |
| pegase2869 | 0 | 0 | 26 | 13 |

## Severity-conditioned maximum network support

| Severity | Supported networks | Per-network selected | Total |
| --- | --- | ---: | ---: |
| normal-envelope | illinois200, pegase1354 | 13 | 26 |
| operational-stress | illinois200, pegase89 | 13 | 26 |
| emergency-stress | illinois200, pegase1354, pegase2869 | 13 | 39 |
| extreme-stress | ieee300, rte1888, rte2848, pegase2869 | 13 | 52 |

## Executable resampling commands

- `python scripts/generate_extended_topology_stress.py --systems ieee300 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.70 0.76 0.82 0.88 --dispatch-policy normal_ac_opf --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_normal_v2_ieee300 --output-json simulation_outputs/topology_stress/matched_normal_pool_ieee300.json --attempts-json reports/matched_normal_attempts_ieee300.json --report-json reports/matched_normal_audit_ieee300.json --report-md reports/matched_normal_audit_ieee300.md`
- `python scripts/generate_extended_topology_stress.py --systems ieee300 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.88 0.94 1.00 1.06 1.08 1.16 1.24 --scale-generation-with-load --generator-voltage-factors 0.98 1.00 1.02 --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_stress_v2_ieee300 --output-json simulation_outputs/topology_stress/matched_stress_pool_ieee300.json --attempts-json reports/matched_stress_attempts_ieee300.json --report-json reports/matched_stress_audit_ieee300.json --report-md reports/matched_stress_audit_ieee300.md`
- `python scripts/generate_extended_topology_stress.py --systems illinois200 --pglib-root third_party/pglib-opf-v23.07 --load-levels 1.12 1.20 1.28 1.36 --scale-generation-with-load --generator-voltage-factors 0.98 1.00 1.02 --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_stress_v2_illinois200 --output-json simulation_outputs/topology_stress/matched_stress_pool_illinois200.json --attempts-json reports/matched_stress_attempts_illinois200.json --report-json reports/matched_stress_audit_illinois200.json --report-md reports/matched_stress_audit_illinois200.md`
- `python scripts/generate_extended_topology_stress.py --systems pegase89 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.70 0.76 0.82 0.88 --dispatch-policy normal_ac_opf --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_normal_v2_pegase89 --output-json simulation_outputs/topology_stress/matched_normal_pool_pegase89.json --attempts-json reports/matched_normal_attempts_pegase89.json --report-json reports/matched_normal_audit_pegase89.json --report-md reports/matched_normal_audit_pegase89.md`
- `python scripts/generate_extended_topology_stress.py --systems pegase89 --pglib-root third_party/pglib-opf-v23.07 --load-levels 1.00 1.08 1.12 1.16 1.20 1.24 1.28 1.36 --scale-generation-with-load --generator-voltage-factors 0.98 1.00 1.02 --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_stress_v2_pegase89 --output-json simulation_outputs/topology_stress/matched_stress_pool_pegase89.json --attempts-json reports/matched_stress_attempts_pegase89.json --report-json reports/matched_stress_audit_pegase89.json --report-md reports/matched_stress_audit_pegase89.md`
- `python scripts/generate_extended_topology_stress.py --systems pegase1354 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.88 0.94 1.00 1.06 1.12 1.20 1.28 1.36 --scale-generation-with-load --generator-voltage-factors 0.98 1.00 1.02 --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_stress_v2_pegase1354 --output-json simulation_outputs/topology_stress/matched_stress_pool_pegase1354.json --attempts-json reports/matched_stress_attempts_pegase1354.json --report-json reports/matched_stress_audit_pegase1354.json --report-md reports/matched_stress_audit_pegase1354.md`
- `python scripts/generate_extended_topology_stress.py --systems rte1888 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.70 0.76 0.82 0.88 --dispatch-policy normal_ac_opf --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_normal_v2_rte1888 --output-json simulation_outputs/topology_stress/matched_normal_pool_rte1888.json --attempts-json reports/matched_normal_attempts_rte1888.json --report-json reports/matched_normal_audit_rte1888.json --report-md reports/matched_normal_audit_rte1888.md`
- `python scripts/generate_extended_topology_stress.py --systems rte1888 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.88 0.94 1.00 1.06 1.08 1.16 1.24 --scale-generation-with-load --generator-voltage-factors 0.98 1.00 1.02 --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_stress_v2_rte1888 --output-json simulation_outputs/topology_stress/matched_stress_pool_rte1888.json --attempts-json reports/matched_stress_attempts_rte1888.json --report-json reports/matched_stress_audit_rte1888.json --report-md reports/matched_stress_audit_rte1888.md`
- `python scripts/generate_extended_topology_stress.py --systems rte2848 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.70 0.76 0.82 0.88 --dispatch-policy normal_ac_opf --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_normal_v2_rte2848 --output-json simulation_outputs/topology_stress/matched_normal_pool_rte2848.json --attempts-json reports/matched_normal_attempts_rte2848.json --report-json reports/matched_normal_audit_rte2848.json --report-md reports/matched_normal_audit_rte2848.md`
- `python scripts/generate_extended_topology_stress.py --systems rte2848 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.88 0.94 1.00 1.06 1.08 1.16 1.24 --scale-generation-with-load --generator-voltage-factors 0.98 1.00 1.02 --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_stress_v2_rte2848 --output-json simulation_outputs/topology_stress/matched_stress_pool_rte2848.json --attempts-json reports/matched_stress_attempts_rte2848.json --report-json reports/matched_stress_audit_rte2848.json --report-md reports/matched_stress_audit_rte2848.md`
- `python scripts/generate_extended_topology_stress.py --systems pegase2869 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.70 0.76 0.82 0.88 --dispatch-policy normal_ac_opf --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_normal_v2_pegase2869 --output-json simulation_outputs/topology_stress/matched_normal_pool_pegase2869.json --attempts-json reports/matched_normal_attempts_pegase2869.json --report-json reports/matched_normal_audit_pegase2869.json --report-md reports/matched_normal_audit_pegase2869.md`
- `python scripts/generate_extended_topology_stress.py --systems pegase2869 --pglib-root third_party/pglib-opf-v23.07 --load-levels 0.88 0.94 1.00 1.06 --scale-generation-with-load --generator-voltage-factors 0.98 1.00 1.02 --max-lines-per-system 13 --max-attempts-per-stratum 128 --seed 20260723 --scenario-namespace matched_stress_v2_pegase2869 --output-json simulation_outputs/topology_stress/matched_stress_pool_pegase2869.json --attempts-json reports/matched_stress_attempts_pegase2869.json --report-json reports/matched_stress_audit_pegase2869.json --report-md reports/matched_stress_audit_pegase2869.md`
