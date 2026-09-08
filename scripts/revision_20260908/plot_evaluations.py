"""Render the new input-controlled and source-selection evaluations."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'figures/revision_20260908'
OUT.mkdir(parents=True,exist_ok=True)
plt.rcParams.update({'font.family':'DejaVu Serif','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42})
read=lambda path: json.loads((ROOT/path).read_text())
old=read('results/revision_20260908/c2_v2_original_baselines.json')['tasks']
new=read('results/revision_20260908/c2_v2_fair_input_metrics.json')['tasks']
tasks=['operation_ticket_check','dispatcher_intent_tool_call']
fig,ax=plt.subplots(figsize=(6.6,3.4))
colors=['#705C98','#B6A7CC','#287F85','#496E9A']
series=[('Text TF–IDF',[old[t]['full_tfidf']['macro_f1'] for t in tasks]),('Masked text TF–IDF',[old[t]['masked_tfidf']['macro_f1'] for t in tasks]),('Text parser + rule',[new[t]['text_parser_rule']['macro_f1'] for t in tasks]),('Structured relation tree',[new[t]['structured_relation_tree']['macro_f1'] for t in tasks])]
for i,(name,values) in enumerate(series):
    bars=ax.bar(np.arange(2)+(i-1.5)*.18,values,.17,label=name,color=colors[i])
    for b,v in zip(bars,values):ax.text(b.get_x()+b.get_width()/2,v+.025,f'{v:.3f}',ha='center',fontsize=9)
ax.set(xticks=[0,1],xticklabels=['Ticket review (n = 240)','Tool routing (n = 240)'],ylabel='Macro-F1',ylim=(0,1.14))
ax.legend(loc='upper center',bbox_to_anchor=(.5,1.32),ncol=2,frameon=False,fontsize=9)
ax.set_axisbelow(True);ax.grid(axis='y',alpha=.16)
fig.tight_layout();fig.savefig(OUT/'fig_c2_v2.pdf',bbox_inches='tight');fig.savefig(OUT/'fig_c2_v2.png',dpi=220,bbox_inches='tight');plt.close(fig)
report=read('reports/revision_20260908/c4_source_card_retrieval.json')
print('C4 metric keys',list(report['metrics']))
authored=next(value for key,value in report['metrics'].items() if 'authored' in key)
cards=authored['per_card']
labels=['NERC TOP-001-6','NERC TOP-002-5','NERC FAC-011-4','NERC VAR-001-5','EU Article 18','EU Article 25','EU Article 33','EU Article 72']
values=[round(v['top1_accuracy']*v['n']) for v in cards.values()]
fig,ax=plt.subplots(figsize=(6.6,3.2))
bars=ax.barh(np.arange(8),values,color=['#287F85']*4+['#496E9A']*4,height=.58)
ax.set(yticks=np.arange(8),yticklabels=labels,xlim=(0,4.6),xticks=[0,1,2,3,4],xlabel='Correct first-ranked source selections')
ax.invert_yaxis()
for b,v in zip(bars,values):ax.text(v+.08,b.get_y()+b.get_height()/2,f'{v}/4',va='center',fontsize=10)
ax.set_axisbelow(True);ax.grid(axis='x',alpha=.16)
fig.tight_layout();fig.savefig(OUT/'fig_c4_source.pdf',bbox_inches='tight');fig.savefig(OUT/'fig_c4_source.png',dpi=220,bbox_inches='tight')
