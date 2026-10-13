"""Test one published base control over the complete declared stress grid.

This run never substitutes an accident-specific corrective control. It preserves
the original 160-scenario denominator and both 48-state load-stress grids.
"""
from __future__ import annotations

import os
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[key] = '1'
import argparse
import copy
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import validate_opf_action_uncertainty_stress as active
from validate_opf_action_constant_power_factor_stress import apply_constant_power_factor_stress
from pandapower.topology import unsupplied_buses


def run_scenario(payload):
    result, scenario = payload
    net = active.build_net(scenario)
    active.apply_published_action(net, result)
    rows = []
    contingencies = [None] + [{"element":c['element'], "index":c['index']} for c in result['post_action_n1']['checks']]
    for model in ('fixed_q', 'constant_pf'):
        for contingency in contingencies:
            for fraction in active.PREREGISTERED_ACTIVE_LOAD_STRESS:
                case = copy.deepcopy(net)
                row = dict(scenario_id=result['scenario_id'], model=model, contingency=contingency, stress_fraction=fraction)
                outaged = None
                if contingency:
                    element, index = contingency['element'], contingency['index']
                    getattr(case, element).at[index, 'in_service'] = False
                    if element == 'gen':
                        outaged = index
                if model == 'fixed_q':
                    active.apply_active_load_stress(case, fraction)
                else:
                    apply_constant_power_factor_stress(case, fraction)
                if len(unsupplied_buses(case)):
                    row.update(converged=False, safe=False, reason='unsupplied_island')
                else:
                    try:
                        active.run_power_flow(case)
                        measured = active.case_metrics(case, result, balance_tolerance_mw=1e-3, outaged_generator=outaged)
                        voltage_tracking = []
                        for index, generator in case.gen.iterrows():
                            if bool(generator['in_service']):
                                actual = float(case.res_bus.at[int(generator['bus']), 'vm_pu'])
                                target = float(generator['vm_pu'])
                                if abs(actual-target) > 1e-5:
                                    voltage_tracking.append(dict(generator=int(index),target_pu=target,actual_pu=actual,reactive_output_mvar=float(case.res_gen.at[index,'q_mvar'])))
                        row['voltage_target_deviations'] = voltage_tracking
                        row.update(converged=True, safe=bool(measured['safe'] and measured['worst_voltage_margin_pu'] >= 0.001), metrics=measured)
                    except Exception as error:
                        row.update(converged=False, safe=False, reason=type(error).__name__, message=str(error)[:300])
                rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.workers <= 12:
        raise ValueError('workers must be between 1 and 12')
    results = json.loads((ROOT/'simulation_outputs/opf_closed_loop/auxiliary_opf_results.json').read_text())['results']
    scenarios = {}
    for filename in ('ieee14_ieee118_source_scenarios_v1.json', 'ieee14_secure_candidate_scenarios_v1.json'):
        for row in json.loads((ROOT/'simulation_outputs/opf_closed_loop'/filename).read_text()):
            scenarios[row['scenario_id']] = row
    if args.limit:
        results = results[:args.limit]
    missing = [r['scenario_id'] for r in results if r['scenario_id'] not in scenarios]
    if missing:
        raise ValueError(f'Missing source scenarios: {missing}')
    out = ROOT/'results/revision_20260908'
    out.mkdir(parents=True, exist_ok=True)
    reportdir = ROOT/'reports/revision_20260908'
    reportdir.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    all_rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_scenario, (r,scenarios[r['scenario_id']])) for r in results]
        with (out/'c1_fixed_control_rows.jsonl').open('w') as handle:
            for complete, future in enumerate(as_completed(futures),1):
                rows = future.result()
                for row in rows:
                    if row.get('converged'):
                        row['safe'] = bool(row['metrics']['safe'] and row['metrics']['worst_voltage_margin_pu'] >= 0.001)
                    handle.write(json.dumps(row,allow_nan=False)+'\n')
                handle.flush()
                all_rows.extend(rows)
                print(json.dumps(dict(completed_scenarios=complete,total_scenarios=len(results),percent=round(100*complete/len(results),1),elapsed_seconds=round(time.monotonic()-start,1))),flush=True)
    groups = {}
    for model in ('fixed_q','constant_pf'):
        selected = [r for r in all_rows if r['model']==model]
        groups[model] = dict(n=len(selected),converged=sum(r['converged'] for r in selected),safe=sum(r['safe'] for r in selected),fully_secure_scenarios=sum(all(x['safe'] for x in selected if x['scenario_id']==r['scenario_id']) for r in results))
    report = dict(status='complete',claim_supported=all(r['safe'] for r in all_rows),control_policy='same_published_base_control_for_every_contingency_and_load_point',reactive_limit_policy='enforce_q_lims=True; fixed voltage commands may saturate through PV/PQ switching',states_with_voltage_target_deviation=sum(bool(r.get('voltage_target_deviations')) for r in all_rows),scenario_count=len(results),expected_rows=len(results)*96,observed_rows=len(all_rows),models=groups,selected_construction_limits=dict(Counter(str(r['selected_loading_margin_percent']) for r in results)),workers=args.workers,blas_threads_per_worker=1,elapsed_seconds=time.monotonic()-start)
    (reportdir/'c1_fixed_control.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)

if __name__ == '__main__':
    main()
