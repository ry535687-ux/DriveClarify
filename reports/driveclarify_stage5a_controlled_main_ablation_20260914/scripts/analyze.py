"""评估侧：连接独立标签、反事实意图；重汇总既有时序与机制记录。"""
import sys
from pathlib import Path
from collections import Counter, defaultdict
import csv
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from common import *


def score_variant(decision, truth, intent, candidate_ids):
    """答案只在已锁定的 ASK 之后映射；非 ASK 不消费 intent。"""
    action = decision['action']
    selected = decision['selected_candidate']
    answer_consumed = False
    if action == 'ASK':
        selected = intent if intent in candidate_ids else None
        answer_consumed = selected is not None
    resolved = selected in candidate_ids
    defined = truth in (EQ, DV)
    correct = (int(resolved and (truth == EQ or selected == intent)) if defined else None)
    wrong = (int(resolved and truth == DV and selected != intent) if defined else None)
    return {'selected_candidate': selected, 'answer_consumed': answer_consumed,
            'correct_decision': correct, 'correctness': correct if resolved else None,
            'wrong_task': wrong, 'query': int(action == 'ASK'),
            'unnecessary_query': int(action == 'ASK') if truth == EQ else None,
            'missed_clarification': int(action != 'ASK' and bool(wrong)) if truth == DV else None,
            'unresolved': int(not resolved), 'coverage': int(resolved)}


def summarize(rows):
    result = {}
    for metric in METRICS:
        values = [r[metric] for r in rows if r[metric] is not None]
        result[metric] = {'numerator_base_equivalent': sum(values), 'denominator_records': len(values),
                          'rate': float(np.mean(values)) if values else None}
    resolved = [r for r in rows if r['correctness'] is not None]
    result['resolved_accuracy'] = {'numerator_base_equivalent': sum(r['correctness'] for r in resolved),
                                   'denominator_records': len(resolved),
                                   'rate': float(np.mean([r['correctness'] for r in resolved])) if resolved else None}
    return result


def paired_cluster_interval(layout_rows, split, baseline, metric, rng, repetitions):
    a = {r['layout_id']: r[metric] for r in layout_rows if r['split'] == split and r['policy'] == 'DRIVECLARIFY'}
    b = {r['layout_id']: r[metric] for r in layout_rows if r['split'] == split and r['policy'] == baseline}
    assert a.keys() == b.keys()
    differences = np.array([a[k] - b[k] for k in sorted(a)])
    draws = rng.choice(differences, size=(repetitions, len(differences)), replace=True).mean(axis=1)
    low, high = np.quantile(draws, [.025, .975])
    return {'effect_full_minus_baseline': float(differences.mean()), 'ci95': [float(low), float(high)],
            'clusters': len(differences), 'paired_layout_differences': differences.tolist(),
            'zero_width_interval': bool(abs(high-low) < 1e-12)}


def main_benchmark(manifest):
    lock = read_json(REPORT / 'benchmark/PREDICTION_LOCK.json')
    assert lock['protocol_sha256'] == manifest['protocol_sha256']
    for name, expected in lock['files'].items():
        assert sha(REPORT / f'benchmark/{name}.jsonl') == expected
    public = {r['public_id']: r for r in read_jsonl(REPORT / 'benchmark/PUBLIC_INPUTS.jsonl')}
    index = read_jsonl(REPORT / 'benchmark/RECORD_INDEX.jsonl')
    decisions = {(r['public_id'], r['policy']): r for r in read_jsonl(REPORT / 'benchmark/POLICY_DECISIONS.jsonl')}
    # 标签仅在预测文件锁定并验证之后解析。
    labels = {}
    for split in ('DEV', 'HIST'):
        entries = read_jsonl(CROOT / f'label_authority/{split}_LABELS.jsonl')
        assert len({x['sample_id'] for x in entries}) == len(entries)
        for label in entries:
            assert label['annotation']['method_output_read'] is False
            assert label['annotation']['full_relation_function_called'] is False
            assert label['human_double_annotation_completed'] is False
            labels[(split, label['sample_id'])] = label
    assert {(r['split'], r['record_id']) for r in index} == labels.keys()
    base, variants = [], []
    for meta in index:
        label = labels[(meta['split'], meta['record_id'])]
        assert label['layout_id'] == meta['layout_id']
        truth = label['truth']
        candidate_ids = [c['candidate_id'] for c in public[meta['public_id']]['input']['candidates']]
        intents = candidate_ids if truth == DV else [candidate_ids[0]]
        mode = ('BALANCED_COUNTERFACTUAL_INTENT_PAIR' if truth == DV else
                'EQUIVALENCE_CLASS_RESPONSE_A' if truth == EQ else 'OUTPUT_ONLY_RESPONSE_A')
        for policy in POLICIES:
            decision = decisions[(meta['public_id'], policy)]
            assert decision['public_sha256'] == public[meta['public_id']]['public_sha256']
            scored = []
            for intent in intents:
                score = score_variant(decision, truth, intent, candidate_ids)
                scored.append(score)
                variants.append({**meta, 'policy': policy, 'truth_relation': truth, 'variant_mode': mode,
                                 'evaluator_response_candidate': intent, 'public_sha256': decision['public_sha256'],
                                 'base_weight': 1 / len(intents), 'action_before_answer': decision['action'], **score})
            averaged = {k: (float(np.mean([s[k] for s in scored])) if scored[0][k] is not None else None)
                        for k in (*METRICS, 'correctness')}
            base.append({**meta, 'policy': policy, 'truth_relation': truth, 'truth_defined': truth in (EQ, DV),
                         'truth_status': label['truth_status'], 'variant_mode': mode, 'variant_count': len(intents),
                         'public_sha256': decision['public_sha256'], 'action_before_answer': decision['action'],
                         'predicted_relation': decision['relation'], **averaged})
    write_csv(REPORT / 'MAIN_POLICY_RESULTS.csv', base)
    write_csv(REPORT / 'benchmark/INTENT_VARIANT_RESULTS.csv', variants)
    layout_rows = []
    for split in ('DEV', 'HIST'):
        layouts = sorted({r['layout_id'] for r in base if r['split'] == split})
        for layout in layouts:
            for policy in POLICIES:
                all_rows = [r for r in base if r['split'] == split and r['layout_id'] == layout and r['policy'] == policy]
                defined = [r for r in all_rows if r['truth_defined']]
                summary = summarize(defined)
                layout_rows.append({'split': split, 'layout_id': layout, 'policy': policy, 'records_all': len(all_rows),
                                    'records_defined': len(defined), 'records_undefined': len(all_rows)-len(defined),
                                    **{k: summary[k]['rate'] for k in METRICS},
                                    'all_query_rate': float(np.mean([r['query'] for r in all_rows])),
                                    'all_coverage_rate': float(np.mean([r['coverage'] for r in all_rows]))})
    write_csv(REPORT / 'MAIN_LAYOUT_SUMMARY.csv', layout_rows)
    comparison = {'scope': 'CONTROLLED_DECISION_BENCHMARK', 'splits': {}, 'comparisons': [],
                  'unit': 'intent variants -> base record -> layout', 'bootstrap': {
                      'unit': 'paired layouts within split', 'replicates': manifest['bootstrap_repetitions'],
                      'computational_seed': manifest['bootstrap_computational_seed'], 'pooled_CI': None},
                  'closed_loop_records_in_primary': 0}
    for split in ('DEV', 'HIST', 'POOLED_DESCRIPTIVE'):
        subset = [r for r in base if split == 'POOLED_DESCRIPTIVE' or r['split'] == split]
        first = [r for r in subset if r['policy'] == POLICIES[0]]
        out = {'base_records': len(first), 'defined_records': sum(r['truth_defined'] for r in first),
               'undefined_records': sum(not r['truth_defined'] for r in first),
               'layouts': len({r['layout_id'] for r in first}),
               'relation_counts': dict(Counter(r['truth_relation'] or 'UNDEFINED' for r in first)), 'policies': {}}
        for policy in POLICIES:
            all_rows = [r for r in subset if r['policy'] == policy]
            defined = [r for r in all_rows if r['truth_defined']]
            undefined = [r for r in all_rows if not r['truth_defined']]
            layouts = [r for r in layout_rows if r['policy'] == policy and (split == 'POOLED_DESCRIPTIVE' or r['split'] == split)]
            stats = {}
            for metric in METRICS:
                v = np.array([r[metric] for r in layouts])
                stats[metric] = {'mean': float(v.mean()), 'median': float(np.median(v)),
                                 'q1': float(np.quantile(v, .25)), 'q3': float(np.quantile(v, .75))}
            out['policies'][policy] = {'defined': summarize(defined), 'all_records': summarize(all_rows),
                                       'undefined_output_only': summarize(undefined),
                                       'undefined_actions': dict(Counter(r['action_before_answer'] for r in undefined)),
                                       'layout_statistics': stats}
        comparison['splits'][split] = out
    rng = np.random.default_rng(manifest['bootstrap_computational_seed'])
    for split in ('DEV', 'HIST'):
        for baseline in POLICIES[:2]:
            for metric in METRICS:
                comparison['comparisons'].append({'split': split, 'baseline': baseline, 'metric': metric,
                    **paired_cluster_interval(layout_rows, split, baseline, metric, rng, manifest['bootstrap_repetitions'])})
    write_json(REPORT / 'MAIN_COMPARISON.json', comparison)
    return base, comparison


def timing_data():
    original = read_json(ORIGINAL / 'FORMAL_V3_EPISODE_LEVEL_PRIMARY_TABLE.json')['rows']
    ledger = read_json(ORIGINAL / 'FORMAL_V3_EXECUTION_LEDGER.json')
    ext = read_json(EXTENSION / 'RQ2_EXTENSION_RESULTS.json')
    points, aggregate, archived_rule_rows = [], {}, {}
    for source, episodes, planned in [('ORIGINAL', original, ledger['planned_episode_count']),
                                      ('EXTENSION', ext['episodes_with_complete_endpoints'], ext['planned_episodes'])]:
        memory_rows = defaultdict(list)
        rules = defaultdict(list)
        for e in episodes:
            assert e['same_source_identity_all_views'] is True
            assert abs(e['commitment_time_s'] - e['deadline_time_s'] - 1.2) < 1e-8
            rr = e['rule_results'] if source == 'ORIGINAL' else e['decision_timing']
            compared = ([rr['R-EVIDENCE-ONLY(B1)'], rr['R-JOINT(B1)'], rr['R-EVIDENCE-ONLY(B2)'], rr['R-JOINT(B2)']]
                        if source == 'ORIGINAL' else [rr['evidence_only'], rr['joint']])
            assert len({r['trace_digest'] for r in compared}) == 1
            for memory in ('B1', 'B2'):
                suff = e['first_sufficiency'][memory] if source == 'ORIGINAL' else e[f'{memory}_first_sufficiency']
                window = e[f'{memory}_window_observed'] if source == 'ORIGINAL' else e[f'{memory}_actionable_window']
                first = suff['simulation_time_s'] if suff else None
                margin = e['deadline_time_s'] - first if first is not None else None
                actionable = (suff['ClarificationActionable'] if source == 'ORIGINAL' else suff['actionable']) if suff else None
                if suff:
                    assert abs(suff['TTCmt_s'] - (e['commitment_time_s'] - first)) < 1e-7
                    assert actionable == (margin > 0)  # 数据无等号样本；不改冻结协议
                point = {'source': source, 'family': e['family'], 'template_layout': e['scene_code'],
                         'record_id': e['cell_id'], 'seed': e['seed'], 'memory_condition': memory,
                         'first_evidence_sufficient_time': first, 'deadline_time_s': e['deadline_time_s'],
                         'reference_commitment_time_s': e['commitment_time_s'], 'frozen_reserve_s': 1.2,
                         'remaining_margin_s': margin, 'TTCmt_at_first_sufficiency_s': suff['TTCmt_s'] if suff else None,
                         'actionable_at_first_sufficiency': actionable, 'actionable_window': window,
                         'endpoint_complete': True, 'missing_reason': '' if suff else 'EVIDENCE_NEVER_SUFFICIENT',
                         'stratum': e.get('condition', e.get('timing_stratum')),
                         'same_source_identity_all_views': True, 'shared_trace_digest': compared[0]['trace_digest'],
                         'source_file': str((ORIGINAL / 'FORMAL_V3_EPISODE_LEVEL_PRIMARY_TABLE.json' if source == 'ORIGINAL' else EXTENSION / 'RQ2_EXTENSION_RESULTS.json').relative_to(REPO))}
                assert bool(suff) == bool(e[f'{memory}_precommitment_sufficiency'])
                points.append(point)
                memory_rows[memory].append(point)
            for name, r in [('EVIDENCE_ONLY', rr['R-EVIDENCE-ONLY(B2)'] if source == 'ORIGINAL' else rr['evidence_only']),
                            ('JOINT', rr['R-JOINT(B2)'] if source == 'ORIGINAL' else rr['joint'])]:
                query = r['proposed_query']
                triggered = query is not None
                late = bool(triggered and query['remaining_margin_s'] <= 0)
                window = bool(memory_rows['B2'][-1]['actionable_window'])
                if triggered:
                    assert abs(query['remaining_margin_s'] - (e['deadline_time_s'] - query['simulation_time_s'])) < 1e-7
                    assert (r['classification'] == 'TOO_LATE_RULE_TRIGGER') == late
                rules[name].append({'record_id': e['cell_id'], 'trigger': triggered, 'late': late, 'window': window,
                                    'timely_trigger': triggered and not late,
                                    'missed_existing_opportunity': window and (not triggered or late),
                                    'late_evidence_no_actionable_window': r['classification'] == 'ACTIONABLE_WINDOW_MISSED_EVIDENCE_TOO_LATE',
                                    'no_sufficient_evidence': memory_rows['B2'][-1]['first_evidence_sufficient_time'] is None,
                                    'rule_unresolved': not triggered, 'classification': r['classification']})
        info = {'scope': 'OFFLINE_SHARED_TRAJECTORY_MECHANISM_ABLATION', 'planned_executed': planned,
                'complete_endpoints': len(episodes), 'incomplete_endpoints': planned-len(episodes), 'memory': {}, 'timing': {}}
        for memory, rows in memory_rows.items():
            first = [r for r in rows if r['first_evidence_sufficient_time'] is not None]
            def dist(key):
                vals = [r[key] for r in first]
                return {'n': len(vals), 'median': float(np.median(vals)) if vals else None,
                        'q1': float(np.quantile(vals, .25)) if vals else None,
                        'q3': float(np.quantile(vals, .75)) if vals else None}
            info['memory'][memory] = {'evidence_sufficient': len(first), 'actionable_windows': sum(r['actionable_window'] for r in rows),
                                      'unresolved_no_evidence': len(rows)-len(first), 'denominator': len(rows),
                                      'first_sufficient_time_distribution': dist('first_evidence_sufficient_time'),
                                      'remaining_margin_distribution': dist('remaining_margin_s')}
        info['memory_by_stratum'] = {}
        for stratum in sorted({r['stratum'] for r in memory_rows['B2']}):
            info['memory_by_stratum'][stratum] = {}
            for memory, rows in memory_rows.items():
                selected = [r for r in rows if r['stratum'] == stratum]
                info['memory_by_stratum'][stratum][memory] = {'denominator': len(selected),
                    'evidence_sufficient': sum(r['first_evidence_sufficient_time'] is not None for r in selected),
                    'actionable_windows': sum(r['actionable_window'] for r in selected)}
        for rule, rows in rules.items():
            info['timing'][rule] = {'denominator': len(rows), **{k: sum(r[k] for r in rows) for k in (
                'trigger', 'late', 'window', 'timely_trigger', 'missed_existing_opportunity',
                'late_evidence_no_actionable_window', 'no_sufficient_evidence', 'rule_unresolved')},
                'classification_counts': dict(Counter(r['classification'] for r in rows))}
        aggregate[source] = info
        archived_rule_rows[source] = dict(rules)
    missing = [('ORIGINAL', e) for e in ledger['entries'] if not e['primary_evaluable']]
    missing.append(('EXTENSION', ext['scientific_noncompletion_episode']))
    for source, e in missing:
        for memory in ('B1', 'B2'):
            prototype = next(r for r in points if r['source'] == source)
            p = {key: None for key in prototype}
            p.update(source=source, family=e.get('family', e['scene_code'].split('-')[0]), template_layout=e['scene_code'],
                     record_id=e['cell_id'], seed=e['seed'], memory_condition=memory, frozen_reserve_s=1.2,
                     endpoint_complete=False, missing_reason=e.get('reason_code', e['classification']),
                     stratum=e.get('timing_stratum', 'LATE_REVEAL'),
                     source_file=str((ORIGINAL / 'FORMAL_V3_EXECUTION_LEDGER.json' if source == 'ORIGINAL' else EXTENSION / 'RQ2_EXTENSION_RESULTS.json').relative_to(REPO)))
            points.append(p)
    write_csv(REPORT / 'TIMING_POINT_DATA.csv', points)
    write_json(REPORT / 'evidence/TIMING_RULE_ROWS.json', {'source_note': '保存 episode 表内已冻结规则，未重跑 frame reducer',
        'incomplete': [{'source': s, **e} for s, e in missing], 'rule_rows_by_source': archived_rule_rows, 'reserve_s': 1.2})
    return aggregate


def trajectory_data(base, manifest):
    path = REPO / 'reports/driveclarify_rq1_conditional_supplement_20260911/sample_results.csv'
    archived = list(csv.DictReader(path.open()))
    rows = [r for r in archived if r['mask_variant'] == 'FULL_INPUT' and r['method'] == 'M3_TRAJECTORY_ONLY']
    assert len(rows) == 203, len(rows)
    assert len({(r['source'], r['sample_id']) for r in rows}) == len(rows)
    index = {(r['split'], r['record_id']): r for r in base if r['policy'] == 'DRIVECLARIFY'}
    signature = {r['sample_id']: r['prediction'] != UNKNOWN for r in archived
                 if r['mask_variant'] == 'FULL_INPUT' and r['method'] == 'M4_TASK_SIGNATURE_ONLY_GIVEN'}
    points = []
    for r in rows:
        is_b = r['source'] == 'B_ABLATION_OVERNIGHT'
        source = 'B_' + r['arm_of_origin'] if is_b else 'C_' + r['split']
        threshold = manifest['B_trajectory_threshold_m'] if is_b else manifest['relation_config']['trajectory_threshold_m']
        distance = float(r['max_distance_m']) if r['max_distance_m'] else None
        relation = (DV if distance > threshold else EQ) if distance is not None else UNKNOWN
        assert relation == r['prediction']
        truth = r['truth'] or None
        if not is_b:
            matched = index[(r['split'], r['sample_id'])]
            assert matched['truth_relation'] == truth
            layout = matched['layout_id']
            evidence = matched['predicted_relation'] != UNKNOWN
        else:
            layout = 'AUTHORED_TEMPLATE:' + r['unit_id']
            evidence = signature[r['sample_id']]
        points.append({'source': source, 'layout_id': layout, 'record_id': r['sample_id'],
                       'trajectory_distance': distance, 'frozen_threshold': threshold, 'truth_relation': truth,
                       'trajectory_relation': relation, 'agreement': relation == truth if truth in (EQ, DV) else None,
                       'task_evidence_available': evidence, 'source_file': str(path.relative_to(REPO)),
                       'mask_variant': 'FULL_INPUT', 'metric_source': 'STEP1_VERIFIED_SAVED_EQUAL_TIME_FUTURES'})
    write_csv(REPORT / 'TRAJECTORY_TASK_POINTS.csv', points)
    summary = {}
    for source in sorted({r['source'] for r in points}):
        p = [r for r in points if r['source'] == source]
        summary[source] = {'records_all': len(p), 'defined': sum(r['truth_relation'] in (EQ, DV) for r in p),
                           'undefined': sum(r['truth_relation'] is None for r in p),
                           'close_but_divergent': sum(r['truth_relation'] == DV and r['trajectory_relation'] == EQ for r in p),
                           'far_but_equivalent': sum(r['truth_relation'] == EQ and r['trajectory_relation'] == DV for r in p),
                           'agreement': sum(r['agreement'] is True for r in p)}
    write_json(REPORT / 'evidence/TRAJECTORY_MECHANISM_SUMMARY.json', summary)
    return summary


def run():
    manifest = verify_sources()
    base, main = main_benchmark(manifest)
    temporal = timing_data()
    trajectory = trajectory_data(base, manifest)
    effects = []
    for c in main['comparisons']:
        if c['baseline'] == 'IMMEDIATE_QUERY' and c['metric'] in ('query', 'unnecessary_query', 'correct_decision'):
            effects.append({'ablation': 'WITHOUT_TASK_GATE', 'source': c['split'], 'metric': c['metric'],
                            'effect_ablated_minus_full': -c['effect_full_minus_baseline'],
                            'ci_low': -c['ci95'][1], 'ci_high': -c['ci95'][0],
                            'denominator': c['clusters'], 'unit': 'paired_layout_rate_difference',
                            'full_count': None, 'ablated_count': None})
    for source, t in temporal.items():
        for metric in ('evidence_sufficient', 'actionable_windows', 'unresolved_no_evidence'):
            a, f = t['memory']['B1'][metric], t['memory']['B2'][metric]
            effects.append({'ablation': 'WITHOUT_MEMORY', 'source': source, 'metric': metric,
                            'effect_ablated_minus_full': (a-f)/t['complete_endpoints'], 'ci_low': None, 'ci_high': None,
                            'denominator': t['complete_endpoints'], 'unit': 'shared_episode_rate_difference_descriptive',
                            'full_count': f, 'ablated_count': a})
        for metric in ('trigger', 'late', 'rule_unresolved'):
            a, f = t['timing']['EVIDENCE_ONLY'][metric], t['timing']['JOINT'][metric]
            effects.append({'ablation': 'WITHOUT_TIMING', 'source': source, 'metric': metric,
                            'effect_ablated_minus_full': (a-f)/t['complete_endpoints'], 'ci_low': None, 'ci_high': None,
                            'denominator': t['complete_endpoints'], 'unit': 'shared_episode_rate_difference_descriptive',
                            'full_count': f, 'ablated_count': a})
    write_csv(REPORT / 'ABLATION_EFFECTS.csv', effects)
    write_json(REPORT / 'ABLATION_RESULTS.json', {
        'task_gate': {'exact_alias_of': 'MAIN_COMPARISON.json::IMMEDIATE_QUERY', 'new_duplicate_baseline_run': False},
        'temporal': temporal, 'trajectory_mechanism': trajectory,
        'effect_direction': 'ablated minus full', 'new_driving_runs': 0, 'new_forward': 0,
        'limits': 'memory/timing 是保存同轨迹分析的重汇总；不是新的闭环对照'})
    print(json.dumps({'primary': {s: {k:v for k,v in x.items() if k != 'policies'} for s,x in main['splits'].items()},
                      'temporal': temporal, 'trajectory': trajectory}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    run()
