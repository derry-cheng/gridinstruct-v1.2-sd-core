#!/usr/bin/env python3
"""C3 input-matched diagnostics; no generator imports or observation-fed text parsing.

The grammar is specified with knowledge of the published format. Its held-out
scores demonstrate text recoverability, not unseen-language generalization.
"""
import os
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[key] = '1'
import json
import argparse
import re
import time
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import accuracy_score, f1_score

ROOT = Path(__file__).resolve().parents[2]
TICKET = 'operation_ticket_check'
FIELDS = {
 TICKET: ['start_minute', 'permit_minute', 'ticket_object', 'state_object', 'required_watch_min', 'assigned_watch_min'],
 'dispatcher_intent_tool_call': ['thermal_loading_pct', 'thermal_limit_pct', 'voltage_pu', 'voltage_lower_bound_pu', 'contingency_exposure', 'redispatch_reserve'],
}
NUM = r'(\d+(?:\.\d+)?)'
CLOCK = r'(\d{2}:\d{2})'
DEVICE = r'(EQ-\d+)'

def parse_instruction(text, task):
    """Only text and task identity enter this function; no row/gold is accepted."""
    found = {}
    def single(pattern, key, kind=float):
        m = re.search(pattern, text, re.I)
        if m:
            value = m.group(1)
            found[key] = (int(value[:2])*60+int(value[3:])) if kind == 'clock' else kind(value)
    def pair(pattern, keys, kind=float):
        m = re.search(pattern, text, re.I)
        if m:
            for key, value in zip(keys, m.groups()):
                found[key] = (int(value[:2])*60+int(value[3:])) if kind == 'clock' else kind(value)
    if task == TICKET:
        for names, key in [('action time|planned start is|planned time|execution time', 'start_minute'), ('authority time|permit time|authorization', 'permit_minute')]:
            single(r'(?:'+names+r')\s+'+CLOCK, key, 'clock')
        pair(r'authority/action times\s+'+CLOCK+'/'+CLOCK, ['permit_minute','start_minute'], 'clock')
        for names,key in [('listed device|ticket object|declared object','ticket_object'), ('state device|state object|observed object','state_object')]:
            single(r'(?:'+names+r')\s+'+DEVICE,key,str)
        pair(r'state/ticket devices\s+'+DEVICE+'/'+DEVICE, ['state_object','ticket_object'],str)
        for names,key in [('watch need|required observation|minimum watch|watch minimum','required_watch_min'), ('watch plan|assigned observation|scheduled watch|watch allocation','assigned_watch_min')]:
            single(r'(?:'+names+r')\s+'+NUM,key)
        pair(r'planned/required observation\s+'+NUM+'/'+NUM,['assigned_watch_min','required_watch_min'])
    else:
        for names,key in [('measured loading|loading|thermal state','thermal_loading_pct'), ('thermal boundary|thermal ceiling|ceiling','thermal_limit_pct'), ('measured voltage|bus voltage','voltage_pu'), ('lower-voltage boundary|voltage floor|floor','voltage_lower_bound_pu'), ('contingency exposure|exposure','contingency_exposure'), ('reserve','redispatch_reserve')]:
            single(r'(?:'+names+r')\s+'+NUM,key)
        for name,keys in [('reserve-exposure',['redispatch_reserve','contingency_exposure']),('floor-voltage',['voltage_lower_bound_pu','voltage_pu']),('limit-loading',['thermal_limit_pct','thermal_loading_pct'])]:
            pair(name+r'\s+'+NUM+'-'+NUM,keys)
    return found

def relation_vector(values, task):
    if task == TICKET:
        return [values['start_minute']-values['permit_minute'], float(values['ticket_object']==values['state_object']), values['assigned_watch_min']-values['required_watch_min']]
    return [values['thermal_loading_pct']-values['thermal_limit_pct'], values['voltage_pu']-values['voltage_lower_bound_pu'], values['contingency_exposure']-values['redispatch_reserve']]

def specified_rule(values, task):
    """Independent implementation of the disclosed specification, not its generator."""
    a,b,c = relation_vector(values,task)
    if task == TICKET:
        admissible = all((a >= 0, b == 1, c >= 0))
        return ('compliant' if c >= 15 else 'compliant_with_monitoring') if admissible else 'non_compliant'
    return 'security_check_and_redispatch' if c > 0 else ('mitigate_violations' if a > 0 or b < 0 else 'diagnose_and_dispatch')

def metrics(gold,pred):
    return {'n': len(gold), 'accuracy': float(accuracy_score(gold,pred)), 'macro_f1':float(f1_score(gold,pred,labels=sorted(set(gold)),average='macro',zero_division=0))}

def main():
    cli=argparse.ArgumentParser()
    cli.add_argument('--data-dir',default='data/c3_compositional_v1')
    cli.add_argument('--output-prefix',default='c2')
    args=cli.parse_args()
    started=time.perf_counter()
    data={s:[json.loads(line) for line in (ROOT/args.data_dir/f'{s}.jsonl').read_text().splitlines()] for s in ['train','validation','test']}
    all_predictions=[]
    report={'data_origin':'Independent synthetic diagnostic set, not a projection of the 95,479-row core dataset.', 'total_rows':sum(map(len,data.values())), 'protocol':{'fit_split':'train only','test_families':[10,11], 'grammar_scope':'Hand-specified named fields and ordered-pair formats, authored after inspecting published template definitions. No claim of blind unseen-template generalization. No iterative test tuning.', 'model_selection':'Fixed LinearSVC C=1 and decision tree max_depth=4; no test-based hyperparameter selection.', 'text_inputs':['instruction','task_type'], 'structured_inputs':'The same six observations supplied to the existing typed contract, converted into three explicit relation features for a trained tree.', 'threads':1},'tasks':{}}
    for task,fields in FIELDS.items():
        train=[r for r in data['train'] if r['task_type']==task]
        test=[r for r in data['test'] if r['task_type']==task]
        gold=[r['label'] for r in test]
        vec=TfidfVectorizer(analyzer='char',ngram_range=(3,5),min_df=2,sublinear_tf=True)
        clf=LinearSVC(C=1.0,class_weight='balanced',random_state=20260901,dual='auto')
        clf.fit(vec.fit_transform([r['instruction'] for r in train]),[r['label'] for r in train])
        lexical=list(clf.predict(vec.transform([r['instruction'] for r in test])))
        tree=DecisionTreeClassifier(max_depth=4,random_state=20260908)
        tree.fit([relation_vector(r['observations'],task) for r in train],[r['label'] for r in train])
        tree_pred=list(tree.predict([relation_vector(r['observations'],task) for r in test]))
        parsed_predictions=[]; exact={f:0 for f in fields}; complete=0; all_exact=0
        for row,lp,tp in zip(test,lexical,tree_pred):
            parsed=parse_instruction(row['instruction'],task)
            valid=all(f in parsed for f in fields)
            pred=specified_rule(parsed,task) if valid else '__parse_failure__'
            parsed_predictions.append(pred); complete+=valid
            correct={f: f in parsed and parsed[f]==row['observations'][f] for f in fields}
            for f in fields: exact[f]+=correct[f]
            all_exact+=all(correct.values())
            all_predictions.append({'id':row['id'],'task_type':task,'template_family':row['template_family'],'instruction':row['instruction'],'parsed_fields':parsed,'field_correct':correct,'gold':row['label'],'text_rule_prediction':pred,'text_tfidf_prediction':str(lp),'structured_tree_prediction':str(tp),'parse_complete':valid})
        report['tasks'][task]={'n_train':len(train),'n_test':len(test),'text_tfidf':metrics(gold,lexical),'text_parser_rule':metrics(gold,parsed_predictions),'structured_relation_tree':metrics(gold,tree_pred),'field_extraction':{'complete':complete,'all_fields_exact':all_exact,'total_fields':len(test)*len(fields),'correct_fields':sum(exact.values()),'per_field_correct':exact},'per_family':{str(f):metrics([r['gold'] for r in all_predictions if r['task_type']==task and r['template_family']==f],[r['text_rule_prediction'] for r in all_predictions if r['task_type']==task and r['template_family']==f]) for f in [10,11]}}
    report['elapsed_seconds']=time.perf_counter()-started
    report['data_directory']=args.data_dir
    report['domain_findings']={s:[{'id':r['id'],'assigned_watch_min':r['observations']['assigned_watch_min']} for r in rows if r['task_type']==TICKET and r['observations']['assigned_watch_min']<0] for s,rows in data.items()}
    out=ROOT/'results/revision_20260908'; out.mkdir(parents=True,exist_ok=True)
    (out/f'{args.output_prefix}_predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in all_predictions))
    (out/f'{args.output_prefix}_fair_input_metrics.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
