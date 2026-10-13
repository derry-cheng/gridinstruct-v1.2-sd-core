#!/usr/bin/env python3
"""Recompute published surface statistics on C3 v2 without changing records."""
import os
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[key]='1'
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from c3.build_compositional_shortcut_control import normalized_surface, exhaustive_similarity

def main():
    start=time.perf_counter()
    data={s:[json.loads(l) for l in (ROOT/'data/revision_20260908/c3_compositional_v2'/f'{s}.jsonl').read_text().splitlines()] for s in ('train','validation','test')}
    surfaces={s:[normalized_surface(r['instruction']) for r in rows] for s,rows in data.items()}
    sets={s:set(v) for s,v in surfaces.items()}
    all_surfaces=sum(surfaces.values(),[])
    result={'data_directory':'data/revision_20260908/c3_compositional_v2','algorithm':'Unmodified normalized_surface and exhaustive_similarity from scripts/c3/build_compositional_shortcut_control.py; exhaustive binary character 5-grams; no sampling.','normalized_surface':{'records':len(all_surfaces),'unique':len(set(all_surfaces)),'unique_rate':len(set(all_surfaces))/len(all_surfaces),'cross_split_collisions':{f'{a}_{b}':len(sets[a]&sets[b]) for a,b in [('train','validation'),('train','test'),('validation','test')]},'split_unique':{s:len(v) for s,v in sets.items()}},'tasks':{}}
    for task in sorted({r['task_type'] for rows in data.values() for r in rows}):
        train=[r for r in data['train'] if r['task_type']==task]
        test=[r for r in data['test'] if r['task_type']==task]
        metric=exhaustive_similarity([normalized_surface(r['instruction']) for r in train],[normalized_surface(r['instruction']) for r in test])
        for name in ('jaccard','cosine'):
            a,b=metric[f'max_{name}_pair_index']
            metric[f'max_{name}_pair_id']=[train[a]['id'],test[b]['id']]
        result['tasks'][task]={'train_test_surface_similarity':metric}
    result['elapsed_seconds']=time.perf_counter()-start
    (ROOT/'results/revision_20260908/c2_v2_similarity.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()
