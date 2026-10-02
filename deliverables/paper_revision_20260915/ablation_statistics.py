"""同轨迹B1/B2配对统计；保留共享种子/模板聚类。"""
from pathlib import Path
import csv,json
import numpy as np
from scipy.stats import binomtest
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1]
rows=list(csv.DictReader((ROOT/'reports/driveclarify_stage5a_controlled_main_ablation_20260914/TIMING_POINT_DATA.csv').open()))
result=[];rng=np.random.default_rng(20260915)
for pop,cluster in [('ORIGINAL','seed'),('EXTENSION','template_layout')]:
    s=[r for r in rows if r['source']==pop and r['endpoint_complete']=='True']
    views={v:{r['record_id']:r for r in s if r['memory_condition']==v} for v in ['B1','B2']}
    assert views['B1'].keys()==views['B2'].keys()
    for metric in ['sufficient','actionable','late_trigger_without_timing']:
        pairs=[]
        for rid,a in views['B1'].items():
            b=views['B2'][rid]
            assert a['shared_trace_digest']==b['shared_trace_digest']
            if metric=='sufficient':x=int(a['first_evidence_sufficient_time']!='');y=int(b['first_evidence_sufficient_time']!='')
            elif metric=='actionable':x=int(a['actionable_window']=='True');y=int(b['actionable_window']=='True')
            else:x=0;y=int(b['first_evidence_sufficient_time']!='' and b['actionable_window']=='False')
            pairs.append((a[cluster],x,y))
        groups=sorted({p[0] for p in pairs});totals=np.array([sum(y-x for c,x,y in pairs if c==g) for g in groups]);ns=np.array([sum(c==g for c,x,y in pairs) for g in groups])
        draw=rng.integers(0,len(groups),size=(20000,len(groups)))
        values=totals[draw].sum(axis=1)/ns[draw].sum(axis=1)
        n01=sum(x==0 and y==1 for c,x,y in pairs);n10=sum(x==1 and y==0 for c,x,y in pairs)
        result.append({'population':pop,'metric':metric,'paired_records':len(pairs),'cluster_unit':cluster,'clusters':len(groups),
                       'difference':float(totals.sum()/ns.sum()),'ci95':np.quantile(values,[.025,.975]).tolist(),
                       'n01':n01,'n10':n10,'nominal_episode_mcnemar_p':binomtest(n01,n01+n10).pvalue if n01+n10 else 1.,
                       'inference_limit':'Small fixed scene/seed design; nominal McNemar ignores cross-record dependence. No population-generalization claim.'})
(OUT/'data/paired_ablation_statistics.json').write_text(json.dumps({'results':result,'repetitions':20000,'seed':20260915},indent=2)+'\n')
print('已补齐同轨迹消融的配对统计及聚类限制')
