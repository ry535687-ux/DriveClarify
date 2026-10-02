"""限定核验论文4.2/4.3：读原始收据和同轨迹规则，不调用历史写入式 reducer。"""
from pathlib import Path
from collections import Counter
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from driveclarify_rq2_t_cg.rules import evaluate_rules


def read(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    evidence = {}
    def register(path):
        path = Path(path)
        evidence[str(path)] = {'exists': path.is_file(), 'sha256': sha(path) if path.is_file() else None}

    r1 = ROOT / 'reports/driveclarify_rq1_v4_ord_critical_stability_and_formal_v1'
    r2 = ROOT / 'reports/driveclarify_rq2_t_cg_formal_v3_prospective_evaluability_and_execution_v1'
    l1 = read(r1 / 'FORMAL_EXECUTION_LEDGER.json')
    roster1 = {c['cell_id']: c for c in read(r1 / 'FORMAL_ROSTER.json')['cells']}
    rows1 = []
    for entry in l1['entries']:
        cell = roster1[entry['cell_id']]
        output = ROOT / entry['output_path']
        receipt = output / 'owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json'
        raw = read(receipt) if receipt.exists() else {}
        action = raw.get('gate', {}).get('action')
        baseline = 'ASK' if raw.get('reasonable_interpretation_count') == 2 else None
        row = {'cell_id': entry['cell_id'], 'seed': cell['seed'], 'scene_code': cell['scene_code'],
               'pair_id': entry['cell_id'], 'pairing': 'SAME_EPISODE_DECISION_RECEIPT',
               'method_actual_gate_action': action, 'baseline_offline_rule_action': baseline,
               'decision_evaluable': entry['decision_evaluable'], 'execution_evaluable': entry['execution_evaluable'],
               'method_actual_durable_ask': entry.get('ask_receipt'),
               'baseline_native_run_count': 0, 'raw_receipt_path': str(receipt),
               'config_path': cell['config_path'], 'result_output': str(output),
               'raw_gate_matches_ledger': action == entry['policy_action'],
               'offline_baseline_matches_ledger': baseline == entry['baseline_action']}
        rows1.append(row)
        for p in [receipt, output / 'RQ1_V4_FORMAL_CELL_RESULT.json', output / 'official_checkpoint.json',
                  output / 'owner_evidence/V11_SUPERVISION_RECEIPT.json', ROOT / cell['config_path']]:
            register(p)
    available1 = [r for r in rows1 if r['decision_evaluable']]
    counts1 = {}
    for condition in ['CRITICAL', 'EQUIVALENT']:
        group = [r for r in available1 if r['scene_code'].endswith('-' + condition)]
        counts1[condition] = {'n': len(group),
            'method_ask': sum(r['method_actual_gate_action'] == 'ASK' for r in group),
            'baseline_rule_ask': sum(r['baseline_offline_rule_action'] == 'ASK' for r in group)}
    save('HISTORICAL_RQ1_SAMPLE_INDEX.json', rows1)

    l2 = read(r2 / 'FORMAL_V3_EXECUTION_LEDGER.json')
    roster2 = {c['cell_id']: c for c in read(r2 / 'FORMAL_V3_ROSTER.json')['cells']}
    rows2 = []
    rule_counts = Counter()
    all_rules_reproduced = True
    for entry in l2['entries']:
        cell = roster2[entry['cell_id']]
        output = ROOT / entry['output_path']
        row = {'cell_id': entry['cell_id'], 'seed': cell['seed'], 'scene_code': cell['scene_code'],
               'pair_id': entry['cell_id'], 'pairing': 'SAME_NATIVE_TRACE_SOURCE_FRAME',
               'primary_evaluable': entry['primary_evaluable'], 'output_path': str(output),
               'scene_config': cell['execution_scene_manifest'], 'independent_rule_native_runs': 0}
        paired = output / 'FORMAL_PAIRED_VIEWS.jsonl'
        rules_file = output / 'FORMAL_RULE_OUTPUTS.json'
        if entry['primary_evaluable']:
            records = [json.loads(line) for line in paired.read_text().splitlines() if line.strip()]
            rules = evaluate_rules(records)  # pure function，仅结果写本轮。
            same_rules = rules == read(rules_file)
            all_rules_reproduced &= same_rules
            row.update(paired_rows=len(records), same_native_source_identity=all(
                all(v[k] == r['source_identity'][k] for k in ['source_frame_id', 'source_observation_id', 'simulation_time_s'])
                for r in records for v in [r['views']['B1'], r['views']['B2']]),
                frozen_rules_reproduced_exactly=same_rules)
            for view in ['B1', 'B2']:
                row[view + '_precommitment_sufficiency'] = any(
                    r['views'][view]['EpistemicEvidenceSufficient'] is True
                    and r['views'][view]['TTCmt_s'] >= -1e-9 for r in records)
                row[view + '_actionable_window'] = any(r['views'][view]['ClarificationOpportunity'] is True for r in records)
            row['rule_classifications'] = {}
            for rule in rules['rules']:
                key = rule['rule_id'] + '(' + str(rule['evidence_view'] or 'NONE') + ')'
                row['rule_classifications'][key] = rule['classification']
                rule_counts[key + ':' + rule['classification']] += 1
        rows2.append(row)
        for p in [paired, rules_file, output / 'FORMAL_V3_EXECUTION_RESULT.json',
                  output / 'post_hoc_world_state.jsonl', output / 'leaderboard_results.json',
                  Path(cell['execution_scene_manifest'])]:
            register(p)
    async_rows = [r for r in rows2 if r['primary_evaluable'] and r['scene_code'].endswith('-ASYNC')]
    summary2 = {'n': len(async_rows)}
    for key in ['B1_precommitment_sufficiency', 'B2_precommitment_sufficiency', 'B1_actionable_window', 'B2_actionable_window']:
        summary2[key] = sum(row[key] for row in async_rows)
    save('HISTORICAL_RQ2_SAMPLE_INDEX.json', rows2)
    source_checks = []
    for root, mapping_key in [(r1, 'source_hashes'), (r2, 'files')]:
        freeze = read(root / 'SOURCE_FREEZE_RECEIPT.json')
        values = freeze[mapping_key]
        entries = values.items() if isinstance(values, dict) else ((r['path'], r['sha256']) for r in values)
        for path, expected in entries:
            p = ROOT / path
            register(p)
            source_checks.append({'path': str(p), 'expected': expected,
                                  'current': evidence[str(p)]['sha256'],
                                  'matches': evidence[str(p)]['sha256'] == expected})
    for root, names in [(r1, ['SOURCE_FREEZE_RECEIPT.json', 'FORMAL_ROSTER.json', 'FORMAL_EXECUTION_LEDGER.json',
                            'RQ1_V4_PRIMARY_RESULTS.json', 'RQ1_V4_SECONDARY_RESULTS.json', 'RQ1_V4_SCIENTIFIC_CONTRACT.json',
                            'RQ1_V4_FORMAL_FREEZE_RECEIPT.json']),
                        (r2, ['SOURCE_FREEZE_RECEIPT.json', 'FORMAL_V3_ROSTER.json', 'FORMAL_V3_EXECUTION_LEDGER.json',
                              'FORMAL_V3_HCG_RESULTS.json', 'FORMAL_V3_PROTOCOL_FREEZE_RECEIPT.json'])]:
        for name in names:
            register(root / name)
    for name in ['tools/run_rq1_v3_consequence_selectivity.py', 'tools/run_rq1_v4_consequence_selectivity.py',
                 'tools/run_rq2_t_cg_formal_v3.py', 'driveclarify_rq2_t_cg_formal_execution/builder.py',
                 'driveclarify_rq2_t_cg/rules.py',
                 'deliverables/driveclarify_method_conclusion_latest_2026_09_07/experiments_rq12_cn.tex']:
        register(ROOT / name)
    save('HISTORICAL_EVIDENCE_HASHES.json', evidence)
    save('HISTORICAL_SOURCE_VERSION_CHECK.json', source_checks)
    summary = {'RQ1': {'attempts': len(rows1), 'decision_evaluable': len(available1),
                'execution_evaluable': sum(r['execution_evaluable'] for r in rows1), 'counts': counts1,
                'raw_decision_receipt_matches': sum(r['raw_gate_matches_ledger'] for r in rows1),
                'baseline_rule_matches': sum(r['offline_baseline_matches_ledger'] for r in rows1),
                'baseline_independent_closed_loop': False,
                'task_completion_caveat': '历史结果依赖relation/action与RC>=90；不能用作本轮独立任务完成判据'},
               'RQ2': {'attempts': len(rows2), 'primary_evaluable': sum(r['primary_evaluable'] for r in rows2),
                'ASYNC': summary2, 'rules_exact_reproduction': all_rules_reproduced,
                'rule_counts': dict(rule_counts), 'B1_B2_independent_closed_loop': False,
                'time_evidence_joint_independent_closed_loop': False,
                'evidence_construction': 'POST_TRACE_BUILDER_FROM_NATIVE_WORLD_STATES_AND_CERTIFIED_EVENT_CONTRACT'},
               'source_hash_mismatches': [r for r in source_checks if not r['matches']]}
    save('HISTORICAL_VERIFICATION.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
