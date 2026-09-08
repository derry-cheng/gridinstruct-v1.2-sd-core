#!/usr/bin/env python3
"""Versioned C3 regeneration with domain-valid durations and fixed splits."""
import os
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[key]='1'
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from c3.build_compositional_shortcut_control import build_rows, fit_tfidf, contract_metrics, normalized_surface
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC

def main():
    output=ROOT/'data/revision_20260908/c3_compositional_v2'
    output.mkdir(parents=True,exist_ok=True)
    old={r['id']:r for s in ('train','validation','test') for r in map(json.loads,(ROOT/'data/c3_compositional_v1'/f'{s}.jsonl').read_text().splitlines())}
    new=build_rows(480)
    changes=[]
    for r in new:
        before=old[r['id']]
        assert r['split']==before['split'] and r['label']==before['label'] and r['template_family']==before['template_family']
        changed={k:{'before':before[k],'after':v} for k,v in r.items() if before[k]!=v}
        if changed: changes.append({'id':r['id'],'split':r['split'],'changes':changed})
        if r['task_type']=='operation_ticket_check': assert r['observations']['assigned_watch_min']>=0
    for s in ('train','validation','test'):
        (output/f'{s}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in new if r['split']==s))
    out=ROOT/'results/revision_20260908'; out.mkdir(parents=True,exist_ok=True)
    (out/'c2_v2_changes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in changes))
    report={'records':len(new),'changed_records':len(changes),'duration_changed_records':sum('observations' in r['changes'] for r in changes),'split_and_labels_preserved':True,'old_data_preserved':True,'rule':'Assigned duration max(0, required+margin); strictly positive required duration preserves noncompliance. Removed redundant At prefix before phase phrase in template family 10.','tasks':{}}
    baseline_rows=[]
    for task in sorted({r['task_type'] for r in new}):
        train=[r for r in new if r['task_type']==task and r['split']=='train']
        test=[r for r in new if r['task_type']==task and r['split']=='test']
        report['tasks'][task]={'full_tfidf':fit_tfidf(train,test,mask_values=False),'masked_tfidf':fit_tfidf(train,test,mask_values=True),'generator_contract_self_check':contract_metrics(test)}
        for masked in (False,True):
            transform=normalized_surface if masked else str
            vec=TfidfVectorizer(analyzer='char',ngram_range=(3,5),min_df=2,sublinear_tf=True)
            classifier=LinearSVC(C=1.0,class_weight='balanced',random_state=20260901,dual='auto')
            classifier.fit(vec.fit_transform([transform(r['instruction']) for r in train]),[r['label'] for r in train])
            predictions=classifier.predict(vec.transform([transform(r['instruction']) for r in test]))
            baseline_rows.extend({'id':r['id'],'task_type':task,'profile':'masked' if masked else 'full','gold':r['label'],'prediction':str(p)} for r,p in zip(test,predictions))
    (out/'c2_v2_original_baseline_predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in baseline_rows))
    (out/'c2_v2_original_baselines.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__': main()
