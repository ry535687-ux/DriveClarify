"""只重新汇总冻结策略；分支展开验证 LOW 正确直接执行，不修改 evaluator。"""
from pathlib import Path
from fractions import Fraction as F
import csv
import hashlib
import json
import math

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
DATA = OUT / 'data'
OLD = ROOT / 'reports/driveclarify_stage5a_controlled_main_ablation_20260914'
POLICIES = ['Never Clarify', 'Always Clarify', 'Ambiguity-Only',
            'Trajectory-Distance', 'Random-Query@matched-rate', 'DriveClarify']
EQ, DV = 'TASK_EQUIVALENT', 'TASK_DIVERGENT'
METRICS = ['correct', 'wrong', 'query', 'unnecessary_low', 'high_query_recall', 'low_direct_act']


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def csvread(p):
    with p.open() as f:
        return list(csv.DictReader(f))


def csvwrite(p, rows):
    with p.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    lock = json.loads((DATA / 'prediction_lock.json').read_text())
    locked = {'predictions_sha256': DATA / 'predictions.jsonl',
              'public_input_sha256': OLD / 'benchmark/PUBLIC_INPUTS.jsonl',
              'protocol_sha256': OUT / 'PROTOCOL.md', 'script_sha256': OUT / 'analyze.py'}
    for key, path in locked.items():
        assert sha(path) == lock[key], path
    manifest = json.loads((OLD / 'BENCHMARK_MANIFEST.json').read_text())
    for source in manifest['sources']:
        assert sha(ROOT / source['path']) == source['sha256'], source['path']
    public = {r['public_id']: r for r in map(json.loads, locked['public_input_sha256'].read_text().splitlines())}
    predictions = list(map(json.loads, (DATA / 'predictions.jsonl').read_text().splitlines()))
    saved = {(r['public_id'], r['policy']): r for r in csvread(DATA / 'main_record_results.csv')}
    historical = {(r['public_id'], r['policy']): r for r in csvread(OLD / 'MAIN_POLICY_RESULTS.csv')}
    assert len(predictions) == len(saved) == len({(r['public_id'], r['policy']) for r in predictions}) == 1056
    assert len(public) == 176
    records, branches, excluded = [], [], set()
    for pred in predictions:
        pid, policy = pred['public_id'], pred['policy']
        row = saved[pid, policy]
        truth = row['truth_relation']
        assert pred['public_sha256'] == public[pid]['public_sha256']
        assert [c['candidate_id'] for c in public[pid]['input']['candidates']] == ['K1', 'K2']
        assert pred['default_candidate'] == 'K1'
        direct = historical[pid, 'NO_CLARIFICATION']
        asked = historical[pid, 'IMMEDIATE_QUERY']
        assert direct['truth_relation'] == asked['truth_relation'] == truth
        if truth not in (EQ, DV):
            excluded.add(pid)
            assert row['correct'] == row['wrong'] == ''
            if policy == 'DriveClarify':
                assert pred['abstain'] and pred['relation'] == 'UNKNOWN'
            continue
        assert not pred['abstain'] and float(row['coverage']) == 1
        q = F(1, 3) if policy == 'Random-Query@matched-rate' else F(str(pred['query_probability']))
        assert math.isclose(float(q), pred['query_probability'], abs_tol=1e-14)
        assert math.isclose(float(q), float(row['query']), abs_tol=1e-14)
        if policy == 'Trajectory-Distance':
            assert q == int(pred['relation'] == DV)
        if policy == 'DriveClarify':
            h = historical[pid, 'DRIVECLARIFY']
            assert h['action_before_answer'] == ('ASK' if q else 'ACT')
            assert pred['relation'] == h['predicted_relation']
        totals = {m: F(0) for m in METRICS}
        for action, weight, source in [('ACT', 1-q, direct), ('ASK', q, asked)]:
            if not weight:
                continue
            # 使用同一 public_id 的历史服务分支，核对动作、已决状态及实际评分。
            # Random 展开为解析概率分支；不是抽样，不把期望权重当 episode。
            assert source['action_before_answer'] == action
            assert float(source['unresolved']) == 0 and float(source['coverage']) == 1
            correct, wrong = F(source['correct_decision']), F(source['wrong_task'])
            direct_success = truth == EQ and action == 'ACT' and correct == 1 and wrong == 0
            event = {'correct': correct, 'wrong': wrong, 'query': F(action == 'ASK'),
                     'unnecessary_low': F(truth == EQ and action == 'ASK'),
                     'high_query_recall': F(truth == DV and action == 'ASK'),
                     'low_direct_act': F(direct_success)}
            for metric in METRICS:
                totals[metric] += weight * event[metric]
            branches.append({'public_id': pid, 'policy': policy, 'truth_relation': truth,
                             'action': action, 'weight_exact': str(weight),
                             'selected_candidate_if_direct': 'K1' if action == 'ACT' else '',
                             'correct_exact': str(correct), 'wrong_exact': str(wrong),
                             'low_correct_direct_event': int(direct_success),
                             'historical_source_policy': source['policy']})
        for metric in ['correct', 'wrong', 'query']:
            assert math.isclose(float(totals[metric]), float(row[metric]), abs_tol=1e-12), (pid, policy, metric)
        if truth == EQ:
            assert math.isclose(float(totals['unnecessary_low']), float(row['unnecessary']), abs_tol=1e-12)
            # 此等价性在确认 ACT 分支正确且无 abstain 后逐条检验，不能先当作定义。
            assert totals['low_direct_act'] == 1 - totals['unnecessary_low']
        records.append({'public_id': pid, 'record_id': row['record_id'], 'policy': policy,
                        'truth_relation': truth, **{k: str(v) for k, v in totals.items()}})
    assert len(excluded) == 44 and len(records) == 132 * 6
    summaries = []
    for policy in POLICIES:
        rows = [r for r in records if r['policy'] == policy]
        high = [r for r in rows if r['truth_relation'] == DV]
        low = [r for r in rows if r['truth_relation'] == EQ]
        assert (len(rows), len(high), len(low)) == (132, 44, 88)
        summary = {'policy': policy}
        for metric, subset in zip(METRICS, [rows, rows, rows, low, high, low]):
            numerator = sum((F(r[metric]) for r in subset), F(0))
            summary.update({metric+'_numerator_exact': str(numerator),
                            metric+'_denominator': len(subset), metric: float(numerator/len(subset))})
        summaries.append(summary)
    old_summary = {r['policy']: r for r in csvread(DATA / 'main_summary.csv') if r['split'] == 'ALL'}
    for row in summaries:
        for metric, oldmetric in [('correct','correct'), ('wrong','wrong'), ('query','query'), ('unnecessary_low','unnecessary')]:
            assert math.isclose(row[metric], float(old_summary[row['policy']][oldmetric]), abs_tol=1e-12)
    best = {m: (max if m in ('correct', 'high_query_recall', 'low_direct_act') else min)(r[m] for r in summaries) for m in METRICS}
    table = []
    for row in summaries:
        policy = row['policy']
        name = policy.replace('@matched-rate', '')
        if policy == 'Ambiguity-Only':
            name += r'$^\dagger$'
        if policy.startswith('Random'):
            name += r'$^\ddagger$'
        if policy == 'DriveClarify':
            name = r'\textbf{'+name+'}'
        cells = []
        for metric in METRICS:
            value = f'{100*row[metric]:.2f}'
            if policy == 'DriveClarify' or math.isclose(row[metric], best[metric], abs_tol=1e-12):
                value = r'\textbf{'+value+'}'
            cells.append(value)
        table.append(name+' & '+' & '.join(cells)+r' \\')
    csvwrite(DATA / 'main_selectivity_records.csv', records)
    csvwrite(DATA / 'main_selectivity_branches.csv', branches)
    csvwrite(DATA / 'main_selectivity_summary.csv', summaries)
    (DATA / 'main_table_rows.tex').write_text('\n'.join(table)+'\n')
    paths = list(locked.values()) + [DATA/'main_record_results.csv', DATA/'main_summary.csv',
                                   OLD/'MAIN_POLICY_RESULTS.csv', OLD/'BENCHMARK_PROTOCOL.md']
    audit = {'status': 'PASS', 'defined': 132, 'high': 44, 'low': 88,
             'excluded_evidence_insufficient_ids': sorted(excluded), 'policy_record_rows': len(records),
             'analytic_branch_rows': len(branches), 'missing_cells': [],
             'low_direct_act_verified_using_correct_ACT_branches': True,
             'low_complement_equality_verified_per_record_after_action_check': True,
             'first_four_metrics_match_frozen_results': True,
             'random_query': 'Independent p=1/3 analytic expectation in all six columns; no sampled rollout.',
             'scope': 'Controlled high-level task selection; not physical driving completion.',
             'source_hashes': {str(p.relative_to(ROOT)): sha(p) for p in paths},
             'aggregator_sha256': sha(Path(__file__)), 'summaries': summaries}
    (DATA/'main_selectivity_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n')
    print('\n'.join(table))


if __name__ == '__main__':
    main()
