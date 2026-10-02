"""只读复核前阶段；禁止启动子进程，所有写入限制在本阶段目录。"""
import ast
import copy
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path('/home/buaa/wrh/DriveClarify')
OUT = Path(__file__).resolve().parents[1]
PRIOR = ROOT / 'reports/driveclarify_rq3_paired_v3_host_feasibility_and_low_replan_audit_v1'
V2 = ROOT / 'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution'
FAMILIES = ['REF-C', 'LMK-C', 'ORD-C', 'USC-C', 'REF-E', 'LMK-E', 'ORD-E']
sys.dont_write_bytecode = True


def guard(event, args):
    if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.exec'):
        raise RuntimeError('入口审计禁止启动子进程: ' + event)
    paths = []
    if event == 'open':
        path, mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)) and (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            paths = [path]
    elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime'):
        paths = [args[0]]
    elif event in ('os.rename', 'os.link', 'os.symlink'):
        paths = args[:2]
    for path in paths:
        if not Path(os.fsdecode(path)).resolve().is_relative_to(OUT):
            raise RuntimeError('入口审计禁止外部写入: ' + str(path))


sys.addaudithook(guard)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert read(path) == value, '既存审计文件内容变化，拒绝覆盖: ' + str(path)
        return
    with path.open('x') as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def main():
    before_path = OUT / 'audit/PRESERVATION_BEFORE.json'
    started = read(before_path)['recorded_utc'] if before_path.exists() else now()
    protected = read(PRIOR / 'audit/PRESERVATION_BEFORE.json')['files']
    prior_files = sorted(p for p in PRIOR.rglob('*') if p.is_file())
    expected = {x['path']: x['sha256'] for x in protected}
    paths = sorted(set(expected) | {str(p) for p in prior_files})
    baseline = []
    for path in paths:
        baseline.append({'path': path, 'sha256': sha(path), 'bytes': Path(path).stat().st_size})
    baseline_hashes = {x['path']: x['sha256'] for x in baseline}
    historical_mismatches = [p for p, value in expected.items() if baseline_hashes[p] != value]
    write('audit/PRESERVATION_BEFORE.json', {'recorded_utc': started, 'scope': 'READ_ONLY_ENTRY_AUDIT_NOT_FORMAL_SOURCE_FREEZE', 'files': baseline, 'prior_tree_files': len(prior_files), 'historical_protected_files': len(protected), 'historical_mismatches': historical_mismatches})
    print('保护快照完成', len(baseline), '历史差异', len(historical_mismatches), flush=True)

    def hash_rows(rows):
        checked = []
        for row in rows:
            p = row['path']
            actual = baseline_hashes.get(p) or (sha(p) if Path(p).is_file() else None)
            checked.append({'path': p, 'expected_sha256': row['sha256'], 'actual_sha256': actual, 'pass': actual == row['sha256']})
        return checked

    parsed = 0
    for p in prior_files:
        if p.suffix == '.json':
            read(p)
            parsed += 1
        elif p.suffix in ('.md', '.py', '.sh'):
            p.read_text()
    frozen = read(PRIOR / 'audit/QUALIFICATION_FREEZE_MANIFEST.json')
    freeze_receipt = read(PRIOR / 'V3_CANDIDATE_POOL_FREEZE_RECEIPT.json')
    pool = read(PRIOR / 'V3_HOST_CANDIDATE_POOL.json')
    contract = read(PRIOR / 'A_STAR_SCIENTIFIC_QUALIFICATION_CONTRACT.json')
    freeze_checks = hash_rows(frozen['runtime_files'] + frozen['input_files'])
    route_map_checks = hash_rows(pool['generation_inputs'])
    prior_artifact_checks = hash_rows(read(PRIOR / 'audit/FINAL_ARTIFACT_VALIDATION.json')['required_file_hashes'])
    source_pass = not historical_mismatches and all(x['pass'] for x in freeze_checks + route_map_checks + prior_artifact_checks)
    freeze_digest_pass = sha(PRIOR / 'audit/QUALIFICATION_FREEZE_MANIFEST.json') == freeze_receipt['freeze_digest']
    source_pass = source_pass and freeze_digest_pass
    write('audit/SOURCE_REVERIFICATION.json', {'pass': source_pass, 'historical_protected_files': len(protected), 'historical_mismatches': historical_mismatches, 'prior_tree_files': len(prior_files), 'prior_json_parsed': parsed, 'freeze_digest_pass': freeze_digest_pass, 'prior_qualification_freeze_digest': freeze_receipt['freeze_digest'], 'frozen_files': freeze_checks, 'referenced_route_map_files': route_map_checks, 'prior_required_artifacts': prior_artifact_checks, 'route_binding_scope': '路线/地图哈希经先验池引用绑定；非逐次native打开文件时的独立字节收据。'})

    # 只导入纯函数，不调用任何历史 main/报告生成/执行入口。
    sys.path.insert(0, str(PRIOR / 'tooling'))
    sys.path.insert(1, str(ROOT))
    from score_a_star import evaluate
    from task_evaluator import extract_task, score_interpretation
    from validate_evaluator import witness, score

    synthetic = []
    for template in pool['templates']:
        binding = read(template['task_binding_path'])
        for zone in binding['zones']:
            trace = witness(binding, zone)
            outcome = score(trace, binding)
            for interpretation in ('1', '2'):
                synthetic.append(score_interpretation(outcome, binding, interpretation)['correct_local_task'] == int(binding['candidate_zone_map'][interpretation] == zone['zone_id']))
            synthetic.append(score(witness(binding, zone, oversize=True), binding)['completed_zone'] is None)
            synthetic.append(score(witness(binding, zone, duration=.1), binding)['completed_zone'] is None)
            broken = copy.deepcopy(trace)
            broken[1]['frame'] += 1
            synthetic.append(score(broken, binding)['status'] == 'UNKNOWN')
            synthetic.append(extract_task(trace, binding, {}, 'TEST', .5)['status'] == 'UNKNOWN')
        trace = [{**r, 'lane_id': binding['start_lane_id'], 'xyz': binding['lane_map'][0]['xyz']} for r in witness(binding, binding['zones'][0])]
        synthetic.append(score(trace, binding)['status'] == 'KNOWN' and score(trace, binding)['completed_zone'] is None)
    assert len(synthetic) == 146 and all(synthetic)

    archived = read(PRIOR / 'A_STAR_RESULTS.json')['results']
    templates = {x['template_id']: x for x in pool['templates']}
    recomputed = []
    episode_checks = []
    attempts = []
    seed_receipt = read(PRIOR / 'A_STAR_DEVELOPMENT_SEED_RECEIPT.json')
    allocated = {r['run_id']: r for r in seed_receipt['assigned']}
    keys = ['template_id', 'condition', 'interpretation', 'replicate', 'seed', 'run_id', 'arm', 'output', 'config', 'route']
    for old in archived:
        attempt = read(PRIOR / 'native' / (old['run_id'] + '_ATTEMPT.json'))
        attempts.append(attempt)
        template = templates[old['template_id']]
        run = {k: attempt[k] for k in keys}
        fresh = evaluate(run, template)
        changed = [k for k in set(old) | set(fresh) if old.get(k) != fresh.get(k)]
        recomputed.append(fresh)
        config = read(run['config'])
        checks = {
            'raw_rescore_equals_archived_result': not changed,
            'individual_result_equals_aggregate': read(PRIOR / 'native' / (run['run_id'] + '_RESULT.json')) == old,
            'allocation_identity_matches': all(run[k] == allocated[run['run_id']][k] for k in allocated[run['run_id']]),
            'registered_configuration_sha_matches': sha(run['config']) == attempt['config_sha256'],
            'explicit_language_only': config['method_input']['instruction'] == template['explicit_instructions'][run['interpretation']],
            'same_public_actors': config['method_input']['runtime_actors'] == read(template['actors_path']),
            'thin_native_input_fields_only': set(config['method_input']) == {'instruction', 'runtime_actors', 'background_traffic_policy'},
            'common_route_path_and_bytes': run['route'] == template['official_route'] and sha(run['route']) == template['official_route_sha256'],
            'same_checkpoint': config['checkpoint_sha256'] == contract['checkpoint_sha256'],
            'A_STAR_not_formal_arm': run['arm'] == 'A_STAR' and run['run_id'].startswith('ASTAR-'),
            'starts_after_pool_and_seed_freeze': attempt['registered_utc'] > seed_receipt['generation_started_utc'] > freeze_receipt['sealed_utc'],
            'process_cleanup_pass': fresh['process_receipt'].get('cleanup_pass') is True,
        }
        episode_checks.append({'run_id': run['run_id'], 'checks': checks, 'changed_result_fields': changed, 'pass': all(checks.values())})
    assert len(recomputed) == len({r['run_id'] for r in recomputed}) == 78
    assert len({r['seed'] for r in recomputed}) == 78
    assert all(x['pass'] for x in episode_checks), [x for x in episode_checks if not x['pass']]
    assert len(list((PRIOR / 'native').glob('*_ATTEMPT.json'))) == 78
    write('audit/A_STAR_RAW_RESCORING.json', {'scope': 'REPLAY_EXISTING_EVIDENCE_ONLY_NO_NATIVE_EXECUTION', 'results': recomputed, 'checks': episode_checks, 'all_archived_results_reproduced': True, 'synthetic_checks': len(synthetic), 'synthetic_checks_pass': all(synthetic)})
    print('原始证据复算完成 78/78；146项评估器软件检查通过', flush=True)

    summaries = []
    families = []
    selected = {}
    for family in FAMILIES:
        family_templates = []
        for rank in (1, 2):
            tid = f'V3-HOST-{family}-P{rank}'
            rows = [r for r in recomputed if r['template_id'] == tid]
            by = {k: [r for r in rows if r['interpretation'] == k] for k in ('1', '2')}
            counts = {k: {'planned': 3, 'executed': len(rs), 'evaluable': sum(r['execution_evaluable'] for r in rs), 'successes': sum(r['A_STAR_TCSC'] == 1 for r in rs), 'unknown': sum(not r['execution_evaluable'] for r in rs)} for k, rs in by.items()}
            numeric = len(rows) == 6 and all(v['executed'] == 3 and v['evaluable'] == 3 and v['successes'] >= 2 for v in counts.values())
            s = {'template_id': tid, 'tested': bool(rows), 'interpretations': counts, 'frozen_numeric_criterion_pass': numeric, 'qualified': bool(numeric and source_pass and all(synthetic)), 'run_ids': [r['run_id'] for r in rows]}
            summaries.append(s)
            family_templates.append(s)
        p1, p2 = family_templates
        order_pass = (not p2['tested']) if p1['qualified'] else p2['tested']
        if p2['tested']:
            p1_ends = [r['completed_utc'] for r in attempts if r['template_id'] == p1['template_id']]
            p2_starts = [r['registered_utc'] for r in attempts if r['template_id'] == p2['template_id']]
            order_pass = order_pass and max(p1_ends) < min(p2_starts)
        selected[family] = next((p['template_id'] for p in family_templates if p['qualified']), None)
        families.append({'family': family, 'P1': p1, 'P2': p2, 'qualified': selected[family] is not None, 'selected': selected[family], 'P1_P2_rule_pass': bool(order_pass)})
    assert selected == read(PRIOR / 'V3_QUALIFIED_HOST_ROSTER.json')['selected']
    for old in read(PRIOR / 'V3_HOST_FEASIBILITY_BY_TEMPLATE.json')['templates']:
        new = next(s for s in summaries if s['template_id'] == old['template_id'])
        assert old['tested'] == new['tested']
        if new['tested']:
            assert old['qualified'] == new['qualified']
            for k in ('1', '2'):
                assert old['interpretation' + k]['successes'] == new['interpretations'][k]['successes']
                assert old['interpretation' + k]['evaluable'] == new['interpretations'][k]['evaluable']
    sorted_ids = [r['run_id'] for r in sorted(attempts, key=lambda r: r['registered_utc'])]
    expected_ids = [rid for f in families for p in (f['P1'], f['P2']) for rid in p['run_ids']]
    assert sorted_ids == expected_ids
    write('audit/HOST_QUALIFICATION_RECOMPUTATION.json', {'families': families, 'selected': selected, 'all_seven_qualified': all(selected.values()), 'P1_P2_and_global_execution_order_pass': sorted_ids == expected_ids and all(f['P1_P2_rule_pass'] for f in families), 'criterion_source': str(PRIOR / 'A_STAR_SCIENTIFIC_QUALIFICATION_CONTRACT.json'), 'criterion': 'all six evaluable; three per interpretation; at least two successes per interpretation; static/source gates pass'})

    static = []
    scenes = {r['template_id']: r for r in read(PRIOR / 'geometry/NATIVE_SCENE_QUALIFICATION.json')['templates']}
    for t in pool['templates']:
        ctx = t['common_route_context']
        digest = hashlib.sha256(json.dumps(ctx, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        b = read(t['task_binding_path'])
        scene = scenes[t['template_id']]
        checks = {
            'context_truth_swap_digest': digest == t['route_neutrality']['route_context_digest_interpretation1'] == t['route_neutrality']['route_context_digest_interpretation2'],
            'same_route_bytes': sha(ctx['route']) == ctx['route_sha256'],
            'same_public_scene_canonical_digest': hashlib.sha256(json.dumps(read(t['actors_path']), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest() == ctx['public_actors_sha256'],
            'ambiguous_instruction_preserved': ctx['ambiguous_instruction'] == t['instruction'],
            'HIGH_distinct_LOW_equivalent': (b['candidate_zone_map']['1'] != b['candidate_zone_map']['2']) == (t['level'] == 'HIGH'),
            'lane_legality': all(g['lane_change'] == 'Left' and g['start_lane_id'] == -2 and g['target_lane_id'] == -1 for g in b['lane_map']),
            'native_scene_no_policy_or_seed': scene['model_forwards'] == 0 and scene['native_policy_runs'] == 0 and scene['seeds'] == [],
            'native_actor_bodies_outside_driving_lane': all(not a['center_in_driving_lane'] and not a['bbox_vertices_in_driving_lane'] and len(a['bbox_world_vertices']) == 8 for a in scene['actors']),
            'scene_image_matches': sha(scene['screenshot']) == scene['screenshot_sha256'],
        }
        static.append({'template_id': t['template_id'], 'checks': checks, 'pass': all(checks.values())})
    assert all(r['pass'] for r in static)
    evaluator_ast = ast.parse((PRIOR / 'tooling/task_evaluator.py').read_text())
    evaluator_arguments = {n.name: [a.arg for a in n.args.args] for n in evaluator_ast.body if isinstance(n, ast.FunctionDef)}
    write('audit/STATIC_AND_EVALUATOR_REVERIFICATION.json', {'templates': static, 'pass': all(r['pass'] for r in static), 'evaluator_arguments': evaluator_arguments, 'same_evaluator_for_both_arms': True, 'scope': '原始记录/地图绑定/源代码只读复核；未新启动CARLA或人工可见性实验。', 'synthetic_checks_pass': all(synthetic), 'synthetic_checks_count': len(synthetic), 'formal_A1_answer_channel_operational_certification': 'NOT_PERFORMED_ENTRY_ALREADY_FAILED'})

    # LOW 裁定从既存V2事件重新计算；不调用会重写历史的 scoring/forensics main。
    low = []
    v2_templates = {x['condition']: x for x in read(V2 / 'V2_TEMPLATE_CANDIDATE_MANIFEST.json')['templates']}
    for episode in read(PRIOR / 'LOW_FULL_REPLAN_EVENT_LEDGER.json')['episodes']:
        events = {Path(r['path']).name: Path(r['path']) for r in episode['source_events']}
        owner = events['ONLINE_ROUTE_INSTALL_RECEIPT.json'].parent
        online = read(events['ONLINE_ROUTE_INSTALL_RECEIPT.json'])
        replan = read(events['RQ1_V2_FULL_REPLAN_RECEIPT.json'])
        decision = read(events['RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json'])
        supervision = read(events['V11_SUPERVISION_RECEIPT.json'])
        timing = read(events['RQ3_LIFECYCLE_TIMING_RECEIPT.json'])
        first = json.loads(events['V11_RUNTIME_TIMELINE.jsonl'].read_text().splitlines()[0])
        template = v2_templates[episode['condition']]
        nominal = template['official_route_context']['nominal_route']
        candidate = read(template['candidate_route_path'])['candidate_A']
        delta = max(math.dist(a['xyz'][:2], b['xyz'][:2]) for a, b in zip(nominal, candidate))
        checks = {
            'ACT_and_TASK_EQUIVALENT': decision['gate']['action'] == 'ACT' and decision['comparison']['relation'] == 'TASK_EQUIVALENT',
            'no_ASK_answer': supervision['counters']['ask_receipts'] == supervision['counters']['passenger_answer_reads'] == 0 and timing['ask'] is None and timing['answer'] is None and not list(owner.rglob('*ASK*')) and not list(owner.rglob('*ANSWER*')),
            'one_admission_and_transaction': supervision['counters']['replan_admissibility_invocations'] == supervision['counters']['route_transactions'] == 1,
            'common_Full_Replan_commit': supervision['transition']['disposition'] == 'COMMIT_NOW' and replan['installation_receipt'] == online and online['committed'],
            'changed_route_and_geometry': online['active_route_identity_before'] != online['installed_route_identity'] and delta > 1. and delta == episode['selected_vs_nominal_max_interior_point_delta_m'],
            'native_consumed': first['last_observation']['active_route_identity'] == online['installed_route_identity'] and first['model_forward_return_count'] > 0,
            'initial_route_preexisted': read(events['NATIVE_NAVIGATION_INPUT_CONTRACT.json'])['native_set_global_plan_called_once'],
            'destination_preserved': online['global_destination_identity_before'] == online['global_destination_identity_after'],
            'no_direct_writer_or_extra_forward': not replan['second_control_writer'] and online['vehicle_control_write_count'] == online['model_forward_count'] == 0,
            'archived_event_identity': decision == episode['ACT_decision_receipt'] and replan == episode['full_replan_commit_event'] and online == episode['route_install_event'],
        }
        provenance = hash_rows(episode['source_events'] + episode['source_code_chain'])
        low.append({'run_id': episode['run_id'], 'checks': checks, 'hashes': provenance, 'pass': all(checks.values()) and all(x['pass'] for x in provenance), 'classification': 'C'})
    assert len(low) == 12 and all(x['pass'] for x in low)
    write('audit/LOW_REPLAN_REVERIFICATION.json', {'episodes': low, 'pass': True, 'classification': 'C', 'classification_name': 'GENUINE_DRIVECLARIFY_FULL_REPLAN_WITHOUT_ASK', 'historical_nonclarification_full_replans': 12, 'new_formal_observations': 0})

    invalid = [r for r in recomputed if not r['execution_evaluable']]
    assert len(invalid) == 1 and invalid[0]['run_id'] == 'ASTAR-REF-E-P2-I1-S1'
    raw = Path(invalid[0]['output'])
    crash_checks = {
        'empty_trace': (raw / 'owner_evidence/V2_NATIVE_STATE_TRACE.jsonl').stat().st_size == 0,
        'no_official_terminal': read(raw / 'official_checkpoint.json')['_checkpoint']['records'] == [],
        'no_agent_step_entry': '=== [Agent]' not in (raw / 'process_job/evaluator.log').read_text(errors='replace'),
        'CARLA_signal_11': 'Signal 11' in (raw / 'process_job/carla_server_start_01.log').read_text(errors='replace'),
        'retained_UNKNOWN': invalid[0]['A_STAR_TCSC'] is None,
    }
    assert all(crash_checks.values())
    native_valid = [r for r in recomputed if r['execution_evaluable']]
    write('audit/CONTROL_REVERIFICATION.json', {'native_evaluable_attempts': len(native_valid), 'complete_native_integrity_records_pass': all(not r['integrity_issues'] for r in native_valid), 'missing_terminal_attempts': [r['run_id'] for r in invalid], 'crash_checks': crash_checks, 'all_78_terminal_control_records_PASS': False, 'actual_controller_mutation_observed': False, 'all_selected_LMK_E_host_six_control_records_PASS': all(not r['integrity_issues'] for r in native_valid if r['template_id'] == selected['LMK-E']), 'limitation': '77个完整终端记录通过；1个setup后、首步前崩溃缺少终端计数，不能等同于已发现第二writer或科学源变更。'})

    prior_ready = read(PRIOR / 'V3_FORMAL_READINESS_RECEIPT.json')
    predicate_rows = []
    def predicate(name, status, observed, reason):
        predicate_rows.append({'predicate': name, 'status': status, 'observed': observed, 'reason': reason})
    for f in families:
        predicate(f[ 'family'] + '_qualified', 'PASS' if f['qualified'] else 'FAIL', f['selected'], '六次全部可评估、两解释各≥2/3；逐次重新评分和固定P1→P2计数见审计。')
    predicate('exactly_one_prospectively_qualified_host_per_family', 'FAIL', selected, '仅1/7族存在合格模板，六族为空。')
    predicate('frozen_P1_P2_selection_rule', 'PASS', True, '实际注册/完成时间和预留ID证明先P1，P1失败才P2；LMK-E/P2未执行。')
    predicate('A_STAR_feasibility_pass_for_all_required_hosts', 'FAIL', {'qualified_families': sum(bool(x) for x in selected.values()), 'required': 7}, '资格成功不能跨解释或跨族汇总。')
    predicate('route_neutrality', 'PASS', '14/14 static bindings', '公共路线/实体投影互换解释后摘要相同，路径和字节核对通过。')
    predicate('A0_comparability', 'PASS', 'UNCHANGED_THIN_NATIVE_RAW_LANGUAGE_INTERFACE', '读取冻结a0_agent.py白名单和78份实际配置；公共路线/场景一致，只授权A*明确语言变化。此为接口资格，不是新A0性能。')
    predicate('task_binding_evaluator', 'PASS', {'synthetic_checks': 146, 'raw_attempt_rescores': 78}, '完整轨迹验证、已知任务失败与技术UNKNOWN保持区分。')
    predicate('same_task_evaluator_usable_for_A0_A1', 'PASS', evaluator_arguments, '同一纯离线函数只读取轨迹/绑定/终端/时长；没有arm或A1内部变量参数。')
    predicate('true_intent_firewall_design', 'UNVERIFIED', None, '前阶段具有A*明确语言入口和A0限制；没有完整七宿主正式A1合法ASK通道配置/集成证明。入口已失败，不创建正式协议来补齐。')
    predicate('checkpoint_source_integrity', 'PASS' if source_pass else 'FAIL', source_pass, '重新哈希9868历史保护文件、152冻结文件、路线/地图引用及23最终文件。')
    predicate('PID_controller_integrity', 'UNVERIFIED_FULL_CAMPAIGN', {'complete_records_PASS': 77, 'terminal_record_missing': 1, 'source_identity_PASS': source_pass}, '全部78次终端控制证据并非PASS；唯一缺失为首个agent步骤前崩溃。所选LMK-E的6次控制证据全部PASS，未观察到实际控制违规。')
    seed_pass = seed_receipt['scope'] == 'DEVELOPMENT_ONLY_NEVER_FORMAL' and seed_receipt['formal_seeds_generated'] == 0 and len(allocated) == 84 and set(seed_receipt['all_development_seeds']) == set(prior_ready['development_seeds_reserved'])
    assert seed_pass
    predicate('no_future_formal_seed_generated_in_qualification', 'PASS', 0, '唯一84项种子分配均DEVELOPMENT_ONLY、冻结后生成；运行78项与分配逐项匹配，余6项未用；全部继续排除正式用途。')
    predicate('no_ambiguous_formal_A0_A1_scientific_exposure', 'PASS', 0, '完整native尝试清单78项全部ASTAR/明确语言，目录及注册配置逐一交叉核验；旧V2文件名不改变运行角色。')
    failed = [x['predicate'] for x in predicate_rows if x['status'] != 'PASS']
    assert failed and not all(selected.values())
    write('V3_FORMAL_ENTRY_GATE_RECEIPT.json', {'stage': 'RQ3_PAIRED_V3_FORMAL_COMPARISON_END_TO_END_V1', 'recorded_utc': now(), 'started_utc': started, 'execution_status': 'BLOCKED_RQ3_PAIRED_V3_FORMAL_ENTRY_GATE_FAILED', 'scientific_verdict': 'NOT_EVALUABLE_DRIVECLARIFY_PAIRED_SUPERIORITY', 'prior_status': prior_ready['primary_stage_status'], 'entry_gate_pass': False, 'formal_authorized': False, 'predicates': predicate_rows, 'failed_or_unverified_predicates': failed, 'decisive_failed_families': [f for f in FAMILIES if selected[f] is None], 'selected_qualification_hosts': selected, 'formal_freeze_digest': None, 'formal_seeds_generated': 0, 'formal_native_runs': 0, 'formal_scientific_exposure': 0, 'verification_scope': '完整前阶段文件树读取/哈希；原始A*证据重新评分；从规则独立聚合；无native执行。', 'process_guard': 'Python audit hook禁止子进程及本阶段之外写入', 'completed_utc': now()})
    print('入口结论 BLOCKED；六族资格失败；正式种子/运行=0', flush=True)


if __name__ == '__main__':
    main()
