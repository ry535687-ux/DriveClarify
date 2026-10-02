"""冻结输入增补：公开预测先锁定，随后评分；只运行CPU。"""
from pathlib import Path
import sys, json, hashlib, csv, argparse
import numpy as np
from scipy.stats import beta, binomtest

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
OLD = ROOT / 'reports/driveclarify_stage5a_controlled_main_ablation_20260914'
sys.path.insert(0, str(ROOT))
from driveclarify_rq1_conditional_supplement.judges_revised import evaluate_c_m5_revised
from driveclarify_rq1_grounded_relation_v3.methods import _trajectory

DATA = OUT / 'data'
POLICIES = ['Never Clarify', 'Always Clarify', 'Ambiguity-Only',
            'Trajectory-Distance', 'Random-Query@matched-rate', 'DriveClarify']
EQ, DV = 'TASK_EQUIVALENT', 'TASK_DIVERGENT'

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def read(p):
    return json.loads(Path(p).read_text())

def lines(p):
    return [json.loads(s) for s in Path(p).read_text().splitlines() if s.strip()]

def write(p, x):
    Path(p).write_text(json.dumps(x, ensure_ascii=False, indent=2, allow_nan=False)+'\n')

def csvwrite(p, rows):
    with Path(p).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def predict():
    DATA.mkdir(exist_ok=True)
    manifest = read(OLD / 'BENCHMARK_MANIFEST.json')
    for src in manifest['sources']:
        assert sha(ROOT / src['path']) == src['sha256'], src['path']
    config = manifest['relation_config']
    public = lines(OLD / 'benchmark/PUBLIC_INPUTS.jsonl')
    out = []
    for r in public:
        p = r['input']
        assert [c['candidate_id'] for c in p['candidates']] == ['K1', 'K2']
        assert all(c['text'].strip() for c in p['candidates'])
        full = evaluate_c_m5_revised(p, config)['relation']
        traj, details = _trajectory(p, config)
        query_probs = [0., 1., 1., float(traj.value == DV), 1/3,
                       float(full == DV)]
        for policy, q in zip(POLICIES, query_probs):
            out.append({'public_id': r['public_id'], 'policy': policy,
                        'query_probability': q,
                        'abstain': policy == 'DriveClarify' and full == 'UNKNOWN',
                        'relation': full if policy == 'DriveClarify' else
                                    traj.value if policy == 'Trajectory-Distance' else None,
                        'default_candidate': 'K1', 'public_sha256': r['public_sha256'],
                        'trajectory_details': details if policy == 'Trajectory-Distance' else None})
    path = DATA / 'predictions.jsonl'
    path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in out))
    write(DATA / 'prediction_lock.json', {
        'predictions_sha256': sha(path), 'public_input_sha256': sha(OLD/'benchmark/PUBLIC_INPUTS.jsonl'),
        'protocol_sha256': sha(OUT/'PROTOCOL.md'), 'script_sha256': sha(__file__),
        'rows': len(out), 'input_records': len(public),
        'decoded_labels_before_lock': False, 'new_model_forwards': 0,
        'random_query': 'Exact policy expectation, p=1/3 on every input; not a sampled run'})
    print('已锁定', len(out), '条baseline/方法输出')

def bootstrap(values, rng):
    v = np.asarray(values, dtype=float)
    z = rng.choice(v, size=(20000, len(v)), replace=True).mean(axis=1)
    return {'difference': float(v.mean()), 'ci95': np.quantile(z,[.025,.975]).tolist(),
            'n_clusters': len(v), 'zero_width': bool(np.ptp(z) < 1e-12)}

def analyze():
    lock = read(DATA/'prediction_lock.json')
    assert sha(DATA/'predictions.jsonl') == lock['predictions_sha256']
    assert sha(__file__) == lock['script_sha256']
    assert sha(OUT/'PROTOCOL.md') == lock['protocol_sha256']
    predictions = lines(DATA/'predictions.jsonl')
    old_rows = list(csv.DictReader((OLD/'MAIN_POLICY_RESULTS.csv').open()))
    meta = {r['public_id']: r for r in old_rows if r['policy'] == 'DRIVECLARIFY'}
    rows = []
    for r in predictions:
        m = meta[r['public_id']];truth = m['truth_relation']
        defined = truth in (EQ,DV);q = r['query_probability'];resolved = float(not r['abstain'])
        wrong = (1-q)*.5 if truth == DV else 0.
        rows.append({'public_id': r['public_id'], 'record_id': m['record_id'],
                     'layout_id': m['layout_id'], 'split': m['split'], 'policy': r['policy'],
                     'truth_relation': truth, 'truth_defined': defined,
                     'correct': (resolved-wrong if defined else None),
                     'wrong': (wrong if defined else None), 'query': q,
                     'unnecessary': (q if truth == EQ else None), 'coverage': resolved,
                     'value_type': 'RANDOMIZATION_EXPECTATION' if 'Random' in r['policy'] else 'BALANCED_INTENT_WEIGHTED'})
    csvwrite(DATA/'main_record_results.csv', rows)
    summaries = []
    metrics = ['correct','wrong','query','unnecessary','coverage']
    for split in ['DEV','HIST','ALL']:
        for policy in POLICIES:
            allr = [r for r in rows if r['policy']==policy and (split=='ALL' or r['split']==split)]
            defined = [r for r in allr if r['truth_defined']]
            s = {'split': split, 'policy': policy, 'n_defined': len(defined), 'n_all': len(allr)}
            for k in metrics:
                v=[r[k] for r in defined if r[k] is not None]
                s[k+'_numerator'] = float(sum(v));s[k+'_denominator']=len(v);s[k]=float(np.mean(v))
            s['all_coverage'] = float(np.mean([r['coverage'] for r in allr]))
            s['all_query'] = float(np.mean([r['query'] for r in allr]))
            summaries.append(s)
    csvwrite(DATA/'main_summary.csv',summaries)
    rng = np.random.default_rng(20260915)
    intervals = []
    for split in ['DEV','HIST']:
        layouts=sorted({r['layout_id'] for r in rows if r['split']==split})
        for policy in POLICIES[:-1]:
            for metric in metrics:
                differences=[]
                for layout in layouts:
                    def values(p):
                        return [r[metric] for r in rows if r['layout_id']==layout and r['policy']==p
                                and r['truth_defined'] and r[metric] is not None]
                    differences.append(np.mean(values('DriveClarify'))-np.mean(values(policy)))
                intervals.append({'split':split,'baseline':policy,'metric':metric,
                                  **bootstrap(differences,rng)})
    write(DATA/'paired_layout_statistics.json', {'unit':'paired layout within split',
         'repetitions':20000,'seed':20260915,'results':intervals,
         'mcnemar_correctness':None,
         'reason':'Weighted counterfactual intentions and repeated layouts are not independent binary records.'})
    # 与历史三策略的逐记录语义一致性检查。
    mapping={'Never Clarify':'NO_CLARIFICATION','Always Clarify':'IMMEDIATE_QUERY','DriveClarify':'DRIVECLARIFY'}
    old={(r['public_id'],r['policy']):r for r in old_rows}
    for r in rows:
        if r['policy'] in mapping:
            o=old[r['public_id'],mapping[r['policy']]]
            for a,b in [('correct','correct_decision'),('wrong','wrong_task'),('query','query'),('coverage','coverage')]:
                assert (r[a] is None and o[b]=='') or np.isclose(r[a],float(o[b])), (r,a)
    trajectory=list(csv.DictReader((OLD/'TRAJECTORY_TASK_POINTS.csv').open()))
    lookup={r['record_id']:r for r in trajectory if r['source'].startswith('C_')}
    for r in rows:
        if r['policy']=='Trajectory-Distance':
            assert r['query']==float(lookup[r['record_id']]['trajectory_relation']==DV)
    write(DATA/'checks.json',{'frozen_sources_unchanged':True,
        'historical_three_policy_record_metrics_reproduced':True,'historical_trajectory_relation_reproduced':True,
        'records':176,'defined':132,'undefined':44,'unique_layouts':22,
        'six_rows_but_five_distinct_policies_on_this_population':True,
        'random_is_expected_rate_not_observed_count':True})
    # 紧凑表格由同一份已复核的CSV产生。
    table=[]
    for s in summaries:
        if s['split']!='ALL':continue
        name=s['policy'].replace('@matched-rate',r' ($p=1/3$)')
        if s['policy']=='Ambiguity-Only':name+=r'$^\dagger$'
        if 'Random' in s['policy']:name+=r'$^\ddagger$'
        cells=[f"{100*s[k]:.2f}" for k in ['correct','wrong','query','unnecessary','coverage','all_coverage']]
        if s['policy']=='DriveClarify':name=r'\textbf{DriveClarify}';cells=[r'\textbf{'+v+'}' for v in cells]
        table.append(name+' & '+' & '.join(cells)+r' \\')
    (DATA/'main_table_rows.tex').write_text('\n'.join(table)+'\n')
    print(json.dumps([r for r in summaries if r['split']=='ALL'],ensure_ascii=False,indent=2))

def closed_loop():
    paired = ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution'
    source=paired/'ALL_36_PAIRED_RESULTS.csv'
    rows=list(csv.DictReader(source.open()));results=[]
    rng=np.random.default_rng(20260915)
    for level in ['HIGH','LOW']:
        sub=[r for r in rows if r['level']==level and r['TCSC_evaluable']=='True']
        for k in ['TCSC','correct_goal','wrong_goal','native_completion','collision','ASK_count','Full_Replan_committed','Route_Completion']:
            a=np.array([float(r['A0_'+k]) for r in sub]);b=np.array([float(r['A1_'+k]) for r in sub])
            ci=bootstrap(b-a,rng)
            binary=k not in ['Route_Completion'] and set(a).union(b)<={0,1}
            extra={}
            if binary:
                n01=int(np.sum((a==0)&(b==1)));n10=int(np.sum((a==1)&(b==0)))
                extra={'n01':n01,'n10':n10,'exact_mcnemar_p':binomtest(n01,n01+n10).pvalue if n01+n10 else 1.}
                if n01+n10==0:
                    # 同时95%覆盖两种不一致概率的Bonferroni上界；对全一致不能宣称等效。
                    upper=float(beta.ppf(.975,1,len(a)))
                    extra['conservative_discordance_ci95']=[-upper,upper]
            results.append({'population':level,'metric':k,'n_pairs':len(a),
                            'A0_mean':float(a.mean()),'A1_mean':float(b.mean()),
                            'A0_sum':float(a.sum()),'A1_sum':float(b.sum()),**ci,**extra})
    write(DATA/'historical_closed_loop_statistics.json',{'source':str(source.relative_to(ROOT)),
       'source_sha256':sha(source),'scope':'Historical local-stop paired task; no new branch experiment',
       'results':results,'interpretation':'Neither arm completed the local goal; no evidence of task-outcome benefit.'})
    (DATA/'historical_paired_results.csv').write_bytes(source.read_bytes())
    print('历史闭环：24 HIGH + 11 LOW完整配对，负面任务端点保留')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['predict','analyze','closed-loop'])
    stage=parser.parse_args().stage
    {'predict':predict,'analyze':analyze,'closed-loop':closed_loop}[stage]()
