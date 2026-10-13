"""Source-card retrieval with complete candidates and explicit query provenance.

This is an authored source-selection diagnostic, not an independent legal audit.
The index contains local curated cards, not the full official regulations.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_international_rule_probe_baseline import CharTfidf

# Fixed before scoring. Each item asks for the responsibility described by one
# existing card; no jurisdiction, standard number, source ID or answer is input.
SCENARIOS = [
    [
        'A controller cannot carry out a received dispatch instruction. Which source describes the operational communication duty?',
        'The shift log must show what the operator did to preserve reliable operation and how instructions were handled. Find the relevant source.',
        'An instruction reaches the control room, but the recipient cannot comply. Identify the source governing this situation.',
        'Which source should support a checklist covering operating commands, operator actions and inability to follow a command?',
    ],
    [
        'Tomorrow\'s forecast indicates that a network limit may be crossed. Which source requires advance analysis and a plan?',
        'The next shift needs a next-day study and measures for predicted overloads. Identify the planning obligation.',
        'Before the operating day begins, engineers need to document potential limit breaches and the response plan. Find the source.',
        'A dispatcher asks what records should connect tomorrow\'s network study with preparations for anticipated limit violations. Select the source.',
    ],
    [
        'A reliability coordinator must document how equipment ratings, voltage bounds and stability bounds enter operations. Which source applies?',
        'Engineers are reviewing the methodology used by the coordinator to establish operational limits, including stability. Select the source.',
        'Which source should be consulted for a documented method combining facility ratings with voltage and stability limits?',
        'The coordinator needs a written explanation of how operational boundaries are derived from ratings and stability. Find the governing card.',
    ],
    [
        'During real-time operation, the controller needs to track reactive reserves and voltage schedules. Select the source.',
        'Reactive flows are changing during the shift. Which source addresses monitoring and controlling voltage and reactive resources?',
        'An operator is checking whether the available reactive devices can maintain the current voltage schedule. Identify the relevant source.',
        'Which source supports a real-time checklist of reactive resource status, voltage settings and reactive power schedules?',
    ],
    [
        'The control room must decide whether current conditions correspond to normal, alert, emergency or blackout. Which source defines this classification?',
        'Frequency, reserves and the consequences of an outage must be combined to assign a system state. Find the classification source.',
        'A dashboard needs a system-state category based on limits, reserve conditions and frequency. Identify the source.',
        'Operators must distinguish an alert from an emergency using present conditions and contingency consequences. Which source supports that decision?',
    ],
    [
        'The transmission operator needs element-specific security bounds covering short-circuit characteristics as well as voltage and heat. Find the source.',
        'Which source covers specification of security limits for individual transmission elements, including short-circuit limits?',
        'An engineer is assembling voltage, short-circuit and thermal limits for each element. Select the applicable source card.',
        'The asset register needs operator-defined electrical and thermal boundaries, with short-circuit capability included. Which source applies?',
    ],
    [
        'The operator must update the list of outages included in coordinated security studies. Which source describes maintaining that list?',
        'Which source supports reviewing external contingencies and the observability area when updating the outage list?',
        'A neighboring network changes, so the contingency list needs revision before coordinated analysis. Find the list-maintenance source.',
        'An engineer asks who establishes and refreshes the contingency inventory used in security assessment. Identify the relevant source.',
    ],
    [
        'Starting from the intact system, analysts must simulate every listed outage and check the resulting limits. Select the source.',
        'The outage list is already fixed. Which source requires calculating each post-outage state and checking security bounds?',
        'Which source covers verifying that operating limits hold after every contingency simulated from the base network state?',
        'The study team needs a procedure that runs the listed contingencies from the intact state and tests each resulting state against applicable limits. Find the source.',
    ],
]


def read_json(path):
    return json.loads(path.read_text())


def masked_query(row, cards):
    question = row['input']['question'].split('Rule summary:')[0]
    for card in cards:
        for field in ('rule_id', 'standard_id', 'clause_id', 'jurisdiction'):
            question = question.replace(card[field], '[source]')
    return question.strip() + ' Requested deliverable: ' + row['input']['deliverable']


def summarize(rows):
    n = len(rows)
    return {'n': n, 'top1_accuracy': sum(r['correct'] for r in rows) / n,
            'mean_reciprocal_rank': sum(1 / r['gold_rank'] for r in rows) / n,
            'mean_gold_rank': sum(r['gold_rank'] for r in rows) / n,
            'candidate_coverage': sum(r['candidate_covered'] for r in rows) / n,
            'top_score_tie_rows': sum(r['top_tie_size'] > 1 for r in rows)}


def evaluate(queries, cards):
    texts = [c['summary'] for c in cards]
    index = CharTfidf().fit(texts)
    vectors = [index.vector(t) for t in texts]
    ids = [c['rule_id'] for c in cards]
    predictions = []
    for q in queries:
        query_vector = index.vector(q['query'])
        scores = [index.cosine(query_vector, vec) for vec in vectors]
        order = sorted(range(len(ids)), key=lambda i: (-scores[i], ids[i]))
        ranked = [ids[i] for i in order]
        golds = q['gold_source_ids']
        predictions.append({**q, 'prediction': ranked[0], 'ranking': ranked,
                            'scores': [scores[i] for i in order],
                            'gold_rank': min(ranked.index(g) + 1 for g in golds),
                            'correct': ranked[0] in golds,
                            'top_tie_size': sum(abs(s - scores[order[0]]) < 1e-12 for s in scores),
                            'candidate_covered': all(g in ids for g in golds)})
    return predictions


def main():
    cards = read_json(ROOT / 'rules/international_rule_profiles.json')
    rows = [json.loads(s) for s in (ROOT / 'data/international_rule_probe_v1.jsonl').read_text().splitlines() if s]
    splits = read_json(ROOT / 'metadata/international_rule_probe_splits_v1.json')
    assert len(cards) == 8 and len(rows) == 512
    existing = []
    for row in rows:
        base = {'id': row['id'], 'gold_source_ids': [row['target_contract']['rule_id']]}
        existing.append({**base, 'view': 'summary_removed', 'query': masked_query(row, cards)})
        existing.append({**base, 'view': 'summary_supplied', 'query': masked_query(row, cards) + ' ' + row['input']['rule_summary']})
    authored = [{'id': f'c4_authored_{i:02d}_{j:02d}', 'gold_source_ids': [card['rule_id']],
                 'query': query, 'view': 'authored_business_question',
                 'provenance': 'author_written_2026-09-08_before_scoring; not expert validated'}
                for i, card in enumerate(cards) for j, query in enumerate(SCENARIOS[i])]
    predictions = evaluate(existing + authored, cards)
    metrics = {}
    for view in ('summary_removed', 'summary_supplied', 'authored_business_question'):
        selected = [p for p in predictions if p['view'] == view]
        groups = {c['rule_id']: summarize([p for p in selected if c['rule_id'] in p['gold_source_ids']]) for c in cards}
        metrics[view] = {'overall': summarize(selected), 'per_card': groups}
        if view != 'authored_business_question':
            metrics[view]['existing_folds'] = {f['name']: summarize([p for p in selected if p['id'] in set(f['test_ids'])]) for f in splits['folds']}
    ambiguous = defaultdict(Counter)
    for q in existing:
        if q['view'] == 'summary_removed':
            ambiguous[q['query']][q['gold_source_ids'][0]] += 1
    report = {
        'status': 'completed', 'candidate_count': 8,
        'index_text': 'curated rule-card summary only; official full source text is not indexed',
        'algorithm': 'character 2-4 gram TF-IDF cosine; IDF fitted on eight source cards only; no training answers or test fitting',
        'tie_break': 'lexicographic source ID, independent of gold',
        'existing_record_count': len(rows), 'authored_record_count': len(authored),
        'metrics': metrics,
        'query_identifiability': {'distinct_summary_removed_queries': len(ambiguous),
                                 'queries_with_multiple_gold_cards': sum(len(c) > 1 for c in ambiguous.values()),
                                 'empirical_best_single_answer_accuracy': sum(max(c.values()) for c in ambiguous.values()) / len(rows)},
        'summary_overlap': {'existing_rows_containing_exact_card_summary_in_question': sum(r['input']['rule_summary'] in r['input']['question'] for r in rows),
                            'authored_queries_containing_exact_card_summary': sum(c['summary'].lower() in q['query'].lower() for c in cards for q in authored)},
        'scope': 'Source-card selection among eight available curated cards. Existing folds are reporting strata; all eight candidates are accessible in every fold. No claim of unseen-source, cross-jurisdiction semantic generalization or independent legal correctness.',
        'authored_scope': '32 author-written business questions, four per card, selected before scores. Gold follows intended source-card construction, pending independent review. No selection or wording revision after scoring.',
    }
    result_dir = ROOT / 'results/revision_20260908'
    report_dir = ROOT / 'reports/revision_20260908'
    result_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    for name, content in [('c4_source_card_predictions.jsonl', predictions), ('c4_authored_queries.jsonl', authored)]:
        (result_dir / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in content))
    (report_dir / 'c4_source_card_retrieval.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
