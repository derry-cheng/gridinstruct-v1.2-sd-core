#!/usr/bin/env python3
"""Package existing OPF evidence; check extracted records without rerunning solvers."""
from __future__ import annotations

import ast
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'release/revision_20260908'
REPORT = ROOT / 'reports/revision_20260908/c5_supplement.json'
FILES = [
    'simulation_outputs/opf_closed_loop/auxiliary_opf_results.json',
    'simulation_outputs/opf_closed_loop/opf_action_uncertainty_stress_cases_v1.json',
    'simulation_outputs/opf_closed_loop/opf_action_constant_power_factor_stress_cases_v1.json',
    'simulation_outputs/opf_closed_loop/ieee14_ieee118_source_scenarios_v1.json',
    'simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json',
    'reports/ieee14_opf_secure_candidate_augmentation_v1.2_sd_core.json',
    'reports/opf_candidate_register_v1.2_sd_core.json',
    'simulation_outputs/contingency/scenario_reconstruction_attempts.json',
    'rules/international_rule_profiles.json', 'rules/regulation_rules.json',
    'metadata/source_traceability.csv', 'metadata/rule_clause_matrix.json',
    'metadata/international_rule_profile_matrix.csv',
    'metadata/pglib_opf_v23_07_case_registry.json',
    'metadata/requirements-lock.txt', 'metadata/release_licenses.json',
    'LICENSE-CODE', 'LICENSE-DATA',
]


def read(path):
    return json.loads(path.read_text())


def script_dependencies():
    pending = ['validate_opf_action_uncertainty_stress',
               'validate_opf_action_constant_power_factor_stress',
               'generate_opf_secure_candidate_augmentation']
    found = set()
    while pending:
        name = pending.pop()
        path = ROOT / 'scripts' / (name + '.py')
        if name in found or not path.exists():
            continue
        found.add(name)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                pending.append(node.module.split('.')[0])
            elif isinstance(node, ast.Import):
                pending.extend(alias.name.split('.')[0] for alias in node.names)
    return ['scripts/' + name + '.py' for name in sorted(found)]


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    paths = FILES + script_dependencies()
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / 'third_party/pglib-opf-v23.07').glob('*')) if p.is_file()]
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / 'network_cases').glob('*/*base.json'))]
    paths = sorted(set(paths))
    missing = [p for p in paths if not (ROOT / p).is_file()]
    if missing:
        raise FileNotFoundError(missing)
    archive = DEST / 'GridInstruct_OPF_evidence_supplement.tar.gz'
    # Standard tar preserves repository paths and uses one compression process.
    subprocess.run(['tar', '-czf', str(archive), '-C', str(ROOT), *paths,
                    'release/revision_20260908/README.md'], check=True)
    if archive.stat().st_size >= 50_000_000:
        raise RuntimeError('Supplement exceeds 50 MB compressed budget')
    with tempfile.TemporaryDirectory(prefix='gridinstruct-c5-') as temp:
        extracted = Path(temp)
        subprocess.run(['tar', '-xzf', str(archive), '-C', str(extracted)], check=True)
        for path in paths:
            assert (extracted / path).is_file(), path
            if path.endswith('.json'):
                read(extracted / path)
        results = read(extracted / FILES[0])['results']
        ids = {row['scenario_id'] for row in results}
        sources = read(extracted / FILES[3]) + read(extracted / FILES[4])
        source_ids = {row['scenario_id'] for row in sources}
        assert len(results) == len(ids) == 160
        assert ids <= source_ids
        states = {}
        for path in FILES[1:3]:
            rows = read(extracted / path)['cases']
            assert len(rows) == 7680
            assert len({row['case_id'] for row in rows}) == 7680
            assert {row['scenario_id'] for row in rows} == ids
            counts = {sid: sum(row['scenario_id'] == sid for row in rows) for sid in ids}
            assert set(counts.values()) == {48}
            states[Path(path).name] = {'rows': len(rows), 'scenarios': len(ids), 'states_per_scenario': 48}
        candidates = read(extracted / FILES[5])
        checks = {'opf_results': len(results), 'source_definitions_cover_all_160': True,
                  'stress_ledgers': states,
                  'candidate_screening_rows': len(candidates['screening_outcomes']),
                  'selected_candidate_ids': len(candidates['output_scenario_ids']),
                  'international_source_cards': len(read(extracted / 'rules/international_rule_profiles.json'))}
    report = {'status': 'packaged_and_structurally_verified',
              'archive': str(archive.relative_to(ROOT)), 'compressed_bytes': archive.stat().st_size,
              'member_count': len(paths) + 1, 'files': paths, 'checks': checks,
              'verification_scope': 'Independent extraction, JSON parsing, identities and denominator coverage only; no numerical solver rerun.',
              'remaining_gaps': [
                  'Full per-candidate power-flow ledger for all 35200 registered candidates is unavailable; 136 screening rows and the generation grid are included.',
                  'Full construction-attempt ledger for the core scenario generator is unavailable; the one existing island reconstruction entry is included and is not a complete ledger.',
                  'Raw core scenario arrays and 95479 instruction records remain in separate project/release inputs, not this supplement.',
                  'International regulation full texts are not present locally; source metadata and authored summaries are included.',
                  'Completed independent human review records, public data deposit and final author metadata remain external deliverables.'
              ]}
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
