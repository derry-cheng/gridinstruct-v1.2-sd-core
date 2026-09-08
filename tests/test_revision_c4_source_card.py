"""Check that source retrieval can return held-out sources without using gold."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('c4_source', ROOT / 'scripts/revision_20260908/c4_source_card_retrieval.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_prediction_does_not_depend_on_gold_source():
    cards = [{'rule_id': 'A', 'summary': 'reactive voltage control'},
             {'rule_id': 'B', 'summary': 'next day operational planning'}]
    queries = [{'id': 'one', 'query': 'next day operational planning', 'gold_source_ids': ['A']},
               {'id': 'two', 'query': 'next day operational planning', 'gold_source_ids': ['B']}]
    rows = MODULE.evaluate(queries, cards)
    assert rows[0]['prediction'] == rows[1]['prediction'] == 'B'
    assert not rows[0]['correct'] and rows[1]['correct']
    assert rows[0]['candidate_covered'] and rows[1]['candidate_covered']


def test_explicit_identifiers_and_summary_do_not_survive_masking():
    cards = [{'rule_id': 'ID7', 'standard_id': 'STD7', 'clause_id': 'Article 7', 'jurisdiction': 'AREA7'}]
    row = {'input': {'question': 'Explain STD7. Rule summary: THE GOLD ANSWER', 'deliverable': 'a checklist'}}
    query = MODULE.masked_query(row, cards)
    assert 'STD7' not in query and 'THE GOLD ANSWER' not in query
    assert 'checklist' in query


def test_report_denominators_and_complete_candidate_rankings():
    import json
    path = ROOT / 'results/revision_20260908/c4_source_card_predictions.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 512 * 2 + 32
    assert all(len(set(row['ranking'])) == 8 for row in rows)
    assert all(set(row['gold_source_ids']) <= set(row['ranking']) for row in rows)
    report = json.loads((ROOT / 'reports/revision_20260908/c4_source_card_retrieval.json').read_text())
    for view, metrics in report['metrics'].items():
        selected = [row for row in rows if row['view'] == view]
        assert metrics['overall'] == MODULE.summarize(selected)
