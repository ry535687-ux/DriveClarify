"""验证入口失败包、空观测和历史保留；不执行科学实验。"""
import ast
import csv
from pathlib import Path
from verify_entry import OUT, PRIOR, now, read, sha, write
from report_blocked import REQUIRED, STATUS, VERDICT


def main():
    checks = []
    def check(name, passed, detail=None):
        checks.append({'check': name, 'pass': bool(passed), 'detail': detail})

    baseline = read(OUT / 'audit/PRESERVATION_BEFORE.json')
    mismatches = []
    for row in baseline['files']:
        path = Path(row['path'])
        actual = sha(path) if path.is_file() else None
        if actual != row['sha256']:
            mismatches.append({'path': str(path), 'expected': row['sha256'], 'actual': actual})
    before_paths = {row['path'] for row in baseline['files'] if Path(row['path']).is_relative_to(PRIOR)}
    after_paths = {str(p) for p in PRIOR.rglob('*') if p.is_file()}
    added = sorted(after_paths - before_paths)
    removed = sorted(before_paths - after_paths)
    check('all_prior_and_historical_bytes_unchanged', not mismatches, {'checked': len(baseline['files']), 'mismatches': mismatches})
    check('entire_prior_tree_file_set_unchanged', not added and not removed, {'count': len(after_paths), 'added': added, 'removed': removed})
    source = read(OUT / 'audit/SOURCE_REVERIFICATION.json')
    final_source_rows = []
    for row in source['frozen_files'] + source['referenced_route_map_files'] + source['prior_required_artifacts']:
        actual = sha(row['path'])
        final_source_rows.append({'path': row['path'], 'expected_sha256': row['expected_sha256'], 'actual_sha256': actual, 'pass': actual == row['expected_sha256']})
    check('frozen_sources_inputs_route_map_and_prior_reports_still_match', all(x['pass'] for x in final_source_rows))
    preservation = {'recorded_utc': now(), 'pass': not mismatches and not added and not removed and all(x['pass'] for x in final_source_rows), 'files_checked': len(baseline['files']), 'historical_protected_count': baseline['historical_protected_files'], 'prior_tree_count': len(after_paths), 'mismatches': mismatches, 'prior_tree_added': added, 'prior_tree_removed': removed, 'baseline_path': str(OUT / 'audit/PRESERVATION_BEFORE.json'), 'baseline_sha256': sha(OUT / 'audit/PRESERVATION_BEFORE.json'), 'final_source_checks': final_source_rows, 'historical_RQ1_RQ2_RQ3_changed': False if not mismatches else None, 'historical_paired_V2_changed': False if not mismatches else None, 'prior_qualification_changed': False if not mismatches and not added and not removed else None}
    write('audit/PRESERVATION_AFTER.json', preservation)

    pending = {'FINAL_ARTIFACT_AUDIT.json', 'FINAL_VALIDATION_RECEIPT.json'}
    missing = [name for name in REQUIRED if name not in pending and not (OUT / name).is_file()]
    check('all_required_artifacts_except_currently_generated_audit_present', not missing, missing)
    parsed = []
    for path in sorted(OUT.rglob('*.json')):
        value = read(path)
        parsed.append(str(path))
        if path.parent == OUT and path.name not in pending:
            check(path.name + '_exact_execution_status', value.get('execution_status') == STATUS)
            check(path.name + '_exact_scientific_verdict', value.get('scientific_verdict') == VERDICT)
    for path in sorted((OUT / 'tooling').rglob('*.py')):
        ast.parse(path.read_text())
    check('all_JSON_parse_and_Python_AST_parse', True, {'json_count': len(parsed)})

    for name in ['ALL_NATIVE_RESULTS.csv', 'ALL_PAIRED_RESULTS.csv']:
        with (OUT / name).open(newline='') as f:
            rows = list(csv.reader(f))
        check(name + '_header_only_no_fabricated_rows', len(rows) == 1 and len(set(rows[0])) == len(rows[0]))
    gate = read(OUT / 'V3_FORMAL_ENTRY_GATE_RECEIPT.json')
    check('entry_really_fails', gate['entry_gate_pass'] is False and len(gate['decisive_failed_families']) == 6)
    check('no_formal_freeze', read(OUT / 'V3_FORMAL_FREEZE_RECEIPT.json')['freeze_digest'] is None)
    check('no_formal_seeds', read(OUT / 'V3_SEED_FRESHNESS_RECEIPT.json')['formal_seeds'] == [])
    check('development_exclusions_84_unique', len(set(read(OUT / 'V3_SEED_FRESHNESS_RECEIPT.json')['development_seeds_permanently_excluded'])) == 84)
    check('no_true_intent_assignment', read(OUT / 'V3_TRUE_INTENT_ROSTER.json')['assignments'] == [])
    check('no_pair_or_run_manifest_allocation', read(OUT / 'V3_PAIR_MANIFEST.json')['pairs'] == read(OUT / 'V3_PAIR_MANIFEST.json')['runs'] == [])
    check('no_executions', read(OUT / 'V3_EXECUTION_LEDGER.json')['runs'] == read(OUT / 'V3_EXECUTION_LEDGER.json')['attempts'] == [])
    check('no_native_artifact_directories', not any((OUT / name).exists() for name in ['native', 'formal_native', 'runtime_configs', 'smoke', 'formal']))
    for name in ['V3_FORMAL_SCIENTIFIC_CONTRACT.json', 'V3_TRUE_INTENT_ROSTER.json', 'V3_TCSC_ENDPOINT_DEFINITION.json', 'V3_PRIMARY_VERDICT_RULE.json', 'V3_STATISTICAL_ANALYSIS_PLAN.json', 'V3_PAIR_MANIFEST.json', 'V3_PAIR_ORDER_RECEIPT.json', 'V3_SOURCE_FREEZE_RECEIPT.json', 'V3_FORMAL_FREEZE_RECEIPT.json']:
        check(name + '_not_a_valid_freeze', read(OUT / name)['formal_frozen'] is False and read(OUT / name)['artifact_state'] == 'NOT_CREATED_ENTRY_GATE_FAILED')
    high = read(OUT / 'V3_HIGH_PRIMARY_RESULTS.json')
    check('HIGH_no_zero_success_or_p_fabrication', high['A0_TCSC']['successes'] is None and high['A1_TCSC']['successes'] is None and high['exact_McNemar_p'] is None and high['confidence_interval_95'] is None)
    check('discordance_cells_missing_not_zero', all(high['discordance_table'][k] is None for k in ['A0_0_A1_0', 'A0_0_A1_1', 'A0_1_A1_0', 'A0_1_A1_1']))
    check('LOW_costs_unobserved_not_zero_claim', all(v is None for v in read(OUT / 'V3_LOW_CONTROL_RESULTS.json')['A1_interaction'].values()))
    ret = read(OUT / 'FINAL_RETURN.json')['requested_60_items']
    check('exactly_60_numbered_return_items', [r['number'] for r in ret] == list(range(1, 61)))
    check('60_return_fields_match_request_positions', ret[18]['field'] == 'A0_A1_correct_goal_rates' and ret[31]['field'] == 'smoothness_comfort_comparison' and ret[42]['field'] == 'REF-C_paired_results' and ret[54]['field'] == 'source_checkpoint_identity_status')
    check('requested_counts_separate_from_frozen_zero_counts', ret[8]['value'] == {'requested_planned_pairs': 24, 'frozen_pairs': 0, 'executed_pairs': 0, 'evaluable_pairs': 0} and ret[10]['value'] == {'requested_planned_pairs': 12, 'frozen_pairs': 0, 'executed_pairs': 0, 'evaluable_pairs': 0})
    check('recomputed_A_STAR_results_all_equal_archived', read(OUT / 'audit/A_STAR_RAW_RESCORING.json')['all_archived_results_reproduced'])
    check('LOW_provenance_all_12_pass', read(OUT / 'audit/LOW_REPLAN_REVERIFICATION.json')['pass'] and len(read(OUT / 'audit/LOW_REPLAN_REVERIFICATION.json')['episodes']) == 12)
    for name in REQUIRED:
        check(name + '_absolute_path_in_return', ret[59]['value'].get(name) == str(OUT / name))
    assert all(x['pass'] for x in checks), [x for x in checks if not x['pass']]

    files = [{'path': str(p), 'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(OUT.rglob('*')) if p.is_file() and p.name not in pending]
    write('FINAL_ARTIFACT_AUDIT.json', {'stage': gate['stage'], 'execution_status': STATUS, 'scientific_verdict': VERDICT, 'artifact_audit_pass': True, 'scope': 'BLOCKED_ENTRY_PACKAGE_COMPLETENESS_NOT_FORMAL_SCIENTIFIC_SUCCESS', 'recorded_utc': now(), 'required_artifact_count': len(REQUIRED), 'required_artifacts': REQUIRED, 'files': files, 'checks': checks, 'hash_scope_exclusions': sorted(pending), 'hash_scope_exclusion_reason': '避免自引用摘要；最终验证单向绑定此审计。'})
    write('FINAL_VALIDATION_RECEIPT.json', {'stage': gate['stage'], 'execution_status': STATUS, 'scientific_verdict': VERDICT, 'validation_pass': True, 'validation_scope': 'COMPLETE_AND_TRUTHFUL_BLOCKED_ENTRY_REPORT', 'formal_entry_pass': False, 'formal_experiments_completed': False, 'recorded_utc': now(), 'check_count': len(checks), 'all_checks_pass': True, 'all_required_artifacts_present': all((OUT / name).is_file() for name in REQUIRED if name != 'FINAL_VALIDATION_RECEIPT.json'), 'formal_freeze_digest': None, 'formal_seeds_generated': 0, 'formal_native_runs': 0, 'formal_scientific_exposure': 0, 'preserved_file_count': len(baseline['files']), 'preservation_pass': preservation['pass'], 'source_checkpoint_identity_pass': True, 'scientific_files_changed': [], 'historical_results_changed': False, 'final_report_path': str(OUT / 'FINAL_REPORT.md'), 'final_report_sha256': sha(OUT / 'FINAL_REPORT.md'), 'final_return_path': str(OUT / 'FINAL_RETURN.json'), 'final_return_sha256': sha(OUT / 'FINAL_RETURN.json'), 'artifact_audit_path': str(OUT / 'FINAL_ARTIFACT_AUDIT.json'), 'artifact_audit_sha256': sha(OUT / 'FINAL_ARTIFACT_AUDIT.json'), 'self_hash_included': False})
    assert all((OUT / name).is_file() for name in REQUIRED)
    print('PASS：入口失败报告完整性验证；检查', len(checks), '保护文件', len(baseline['files']), '要求产物', len(REQUIRED), '正式运行0', flush=True)


if __name__ == '__main__':
    main()
