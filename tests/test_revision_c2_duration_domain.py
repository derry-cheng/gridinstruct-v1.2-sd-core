"""C3 v2 must keep real duration and label/split invariants for all records."""
import json
from pathlib import Path
ROOT=Path(__file__).parents[1]

def test_v2_domain_and_identity_for_entire_dataset():
    total=0
    for split in ('train','validation','test'):
        old={r['id']:r for r in map(json.loads,(ROOT/'data/c3_compositional_v1'/f'{split}.jsonl').read_text().splitlines())}
        rows=list(map(json.loads,(ROOT/'data/revision_20260908/c3_compositional_v2'/f'{split}.jsonl').read_text().splitlines()))
        assert set(old)=={r['id'] for r in rows}
        for r in rows:
            total+=1
            assert r['split']==old[r['id']]['split']
            assert r['label']==old[r['id']]['label']
            assert 'At at ' not in r['instruction']
            if r['task_type']=='operation_ticket_check':
                assert r['observations']['assigned_watch_min']>=0
    assert total==2880
