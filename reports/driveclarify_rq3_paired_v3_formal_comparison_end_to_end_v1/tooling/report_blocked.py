"""入口失败报告；只创建未执行回执，不生成实验协议、种子或观测。"""
import csv
import json
from pathlib import Path
from verify_entry import OUT, PRIOR, ROOT, FAMILIES, now, read, sha, write

STAGE = 'RQ3_PAIRED_V3_FORMAL_COMPARISON_END_TO_END_V1'
STATUS = 'BLOCKED_RQ3_PAIRED_V3_FORMAL_ENTRY_GATE_FAILED'
VERDICT = 'NOT_EVALUABLE_DRIVECLARIFY_PAIRED_SUPERIORITY'
REASON = 'FORMAL_ENTRY_GATE_FAILED'
REQUIRED = '''FINAL_REPORT.md
V3_FORMAL_SCIENTIFIC_CONTRACT.md
V3_FORMAL_SCIENTIFIC_CONTRACT.json
V3_QUALIFIED_HOST_IMPORT_RECEIPT.json
V3_LOW_REPLAN_SEMANTICS_IMPORT_RECEIPT.json
V3_TRUE_INTENT_ROSTER.json
V3_TCSC_ENDPOINT_DEFINITION.md
V3_TCSC_ENDPOINT_DEFINITION.json
V3_PRIMARY_VERDICT_RULE.md
V3_PRIMARY_VERDICT_RULE.json
V3_STATISTICAL_ANALYSIS_PLAN.md
V3_STATISTICAL_ANALYSIS_PLAN.json
V3_BENCH2DRIVE_METRIC_AUDIT.md
V3_BENCH2DRIVE_METRIC_AUDIT.json
V3_PAIR_MANIFEST.json
V3_PAIR_ORDER_RECEIPT.json
V3_SEED_FRESHNESS_RECEIPT.json
V3_SOURCE_FREEZE_RECEIPT.json
V3_FORMAL_FREEZE_RECEIPT.json
V3_EXECUTION_LEDGER.json
V3_PAIR_VALIDITY_LEDGER.json
ALL_PAIRED_RESULTS.csv
ALL_NATIVE_RESULTS.csv
V3_HIGH_PRIMARY_RESULTS.json
V3_HIGH_MCNEMAR_ANALYSIS.json
V3_HIGH_PAIRED_RISK_ANALYSIS.json
V3_SECONDARY_BINARY_ANALYSIS.json
V3_BENCH2DRIVE_PAIRED_ANALYSIS.json
V3_PER_FAMILY_RESULTS.json
V3_LOW_CONTROL_RESULTS.json
V3_INTERACTION_COST_RESULTS.json
V3_REPLAN_SEMANTICS_RESULTS.json
V3_TRUE_INTENT_FIREWALL_RECEIPT.json
V3_CONTROL_INTEGRITY_RECEIPT.json
V3_ENGINEERING_REPAIR_LOG.md
FINAL_ARTIFACT_AUDIT.json
FINAL_VALIDATION_RECEIPT.json
COMMAND_LOG.md
TABLE_V3_HIGH_PRIMARY_TCSC.md
TABLE_V3_BENCH2DRIVE_METRICS.md
TABLE_V3_LOW_CONTROL.md
TABLE_V3_INTERACTION_COST.md'''.splitlines()


def md(name, content):
    with (OUT / name).open('x') as f:
        f.write(content.rstrip() + '\n')


def envelope(state, **fields):
    return dict(stage=STAGE, execution_status=STATUS, scientific_verdict=VERDICT,
                artifact_state=state, reason=REASON, formal_frozen=False,
                formal_scientific_exposure=0, **fields)


def absent(**fields):
    return envelope('NOT_CREATED_ENTRY_GATE_FAILED', **fields)


def result(**fields):
    return envelope('NOT_RUN_NOT_EVALUABLE', **fields)


def count(planned):
    return dict(requested_planned_pairs=planned, frozen_pairs=0, executed_pairs=0, evaluable_pairs=0)


def table(name, title, rows, columns):
    lines = ['# ' + title, '', '入口门槛失败，正式实验未执行。所有 N/E 表示未观测，不能读作零成功或零成本。', '',
             '|' + '|'.join(['范围'] + columns) + '|', '|' + '|'.join(['---'] * (len(columns) + 1)) + '|']
    lines += ['|' + '|'.join([r] + ['N/E'] * len(columns)) + '|' for r in rows]
    md(name, '\n'.join(lines))


def main():
    gate = read(OUT / 'V3_FORMAL_ENTRY_GATE_RECEIPT.json')
    assert gate['entry_gate_pass'] is False and gate['formal_native_runs'] == 0
    source = read(OUT / 'audit/SOURCE_REVERIFICATION.json')
    assert source['pass']
    hosts = read(OUT / 'audit/HOST_QUALIFICATION_RECOMPUTATION.json')
    prior_contract = read(PRIOR / 'A_STAR_SCIENTIFIC_QUALIFICATION_CONTRACT.json')
    low = read(PRIOR / 'LOW_FULL_REPLAN_CLASSIFICATION_RECEIPT.json')
    seed = read(PRIOR / 'A_STAR_DEVELOPMENT_SEED_RECEIPT.json')
    family_counts = {f: count(6 if f.endswith('-C') else 4) for f in FAMILIES}
    requested = {'HIGH': count(24), 'LOW': count(12), 'total': count(36), 'requested_native_runs': 72, 'frozen_native_runs': 0, 'executed_native_runs': 0, 'evaluable_native_runs': 0}
    links = {n: str(OUT / n) for n in REQUIRED}

    write('V3_FORMAL_SCIENTIFIC_CONTRACT.json', absent(contract=None, contract_digest=None, requested_design_reference=requested,
          note='此文件是未创建正式契约的状态回执；没有采纳或冻结正式科学协议。入口失败终止条件优先。'))
    write('V3_QUALIFIED_HOST_IMPORT_RECEIPT.json', envelope('IMPORT_REJECTED_INCOMPLETE_ROSTER', source_path=str(PRIOR / 'V3_QUALIFIED_HOST_ROSTER.json'), source_sha256=sha(PRIOR / 'V3_QUALIFIED_HOST_ROSTER.json'), observed_qualification_roster=hosts['selected'], required_conditions=FAMILIES, qualified_count=1, required_count=7, imported_formal_hosts=[], pass_for_formal_import=False, verification=str(OUT / 'audit/HOST_QUALIFICATION_RECOMPUTATION.json')))
    write('V3_LOW_REPLAN_SEMANTICS_IMPORT_RECEIPT.json', envelope('VERIFIED_REFERENCE_IMPORTED_NO_FORMAL_FREEZE', source_classification=low, source_path=str(PRIOR / 'LOW_FULL_REPLAN_CLASSIFICATION_RECEIPT.json'), source_sha256=sha(PRIOR / 'LOW_FULL_REPLAN_CLASSIFICATION_RECEIPT.json'), metric_semantics_path=str(PRIOR / 'FUTURE_REPLAN_METRIC_SEMANTICS.md'), metric_semantics_sha256=sha(PRIOR / 'FUTURE_REPLAN_METRIC_SEMANTICS.md'), historical_provenance_reverification=str(OUT / 'audit/LOW_REPLAN_REVERIFICATION.json'), new_formal_event_count=0, historical_12_events_are_not_V3_observations=True))
    write('V3_TRUE_INTENT_ROSTER.json', absent(assignments=[], assignment_count=0, balancing_not_adopted=True))
    write('V3_TCSC_ENDPOINT_DEFINITION.json', absent(formal_endpoint=None, inherited_reference_only={'source': str(PRIOR / 'A_STAR_SCIENTIFIC_QUALIFICATION_CONTRACT.json'), 'source_sha256': sha(PRIOR / 'A_STAR_SCIENTIFIC_QUALIFICATION_CONTRACT.json'), 'endpoint': prior_contract['endpoint'], 'native_completion': prior_contract['native_completion'], 'major_safety_event_union': prior_contract['major_safety_event_union'], 'evaluator_path': prior_contract['evaluator'], 'evaluator_sha256': sha(prior_contract['evaluator'])}, no_endpoint_modification=True))
    write('V3_PRIMARY_VERDICT_RULE.json', absent(adopted_primary_rule=None, requested_rule_reference_only='HIGH evaluability; A1 TCSC>A0; exact two-sided McNemar p<0.05; paired 95% risk-difference CI lower bound>0', entry_failure_verdict=VERDICT))
    write('V3_STATISTICAL_ANALYSIS_PLAN.json', absent(adopted_analysis_plan=None, CI_implementation=None, exact_paired_test_implementation=None, statistical_code_executed=False, LOW_nonregression_margin=None, reason_detail='入口失败，没有科学契约、种子或暴露；不能产生事后正式冻结。'))

    official = ROOT / 'driveclarify_rq3_paired_v2/official_metrics.py'
    evaluator_files = [r for r in source['frozen_files'] if any(x in r['path'] for x in ['statistics_manager.py', 'leaderboard_evaluator.py', 'efficiency_smoothness_benchmark.py', 'official_metrics.py'])]
    fields = {'Driving_Score': 'scores.score_composed', 'Route_Completion': 'scores.score_route', 'infraction_penalty': 'scores.score_penalty', 'official_success': "status in ('Perfect','Completed')", 'collision_event_count': 'len(collisions_layout)+len(collisions_vehicle)+len(collisions_pedestrian)', 'collision_incidence': 'collision_event_count > 0', 'outside_route_lanes': 'len(infractions.outside_route_lanes)', 'route_deviation': 'len(infractions.route_dev)', 'red_light': 'len(infractions.red_light)', 'stop_sign': 'len(infractions.stop_infraction)', 'timeout': 'route_timeout和scenario_timeouts分别记录', 'vehicle_blocked': 'len(infractions.vehicle_blocked)', 'Driving_Efficiency': 'installed efficiency_smoothness_benchmark.py；本阶段未运行后处理', 'Driving_Smoothness_Comfort': 'installed seg_compute_comfort_metric；本阶段未运行后处理', 'offroad_separate': 'UNAVAILABLE_SEPARATE_FIELD', 'wrong_lane_separate': 'UNAVAILABLE_SEPARATE_FIELD'}
    write('V3_BENCH2DRIVE_METRIC_AUDIT.json', envelope('REFERENCE_SOURCE_AUDIT_ONLY_NO_FORMAL_METRICS', label='Bench2Drive evaluator metrics on the paired ambiguity subset', full_leaderboard_claim=False, installed_source_files=evaluator_files, reader_path=str(official), reader_sha256=sha(official), fields=fields, official_tool_modified=False, formal_metrics=None, metric_availability_during_formal_runs='NOT_TESTED_NO_RUNS'))
    write('V3_PAIR_MANIFEST.json', absent(pairs=[], runs=[], digest=None, counts=requested))
    write('V3_PAIR_ORDER_RECEIPT.json', absent(order=[], order_frozen=False))
    write('V3_SEED_FRESHNESS_RECEIPT.json', absent(formal_seeds=[], generated_count=0, freshness_evaluation='NOT_APPLICABLE_NO_FORMAL_SEEDS', source_development_exclusion_receipt=str(PRIOR / 'A_STAR_DEVELOPMENT_SEED_RECEIPT.json'), source_sha256=sha(PRIOR / 'A_STAR_DEVELOPMENT_SEED_RECEIPT.json'), development_seeds_permanently_excluded=seed['all_development_seeds'], development_exclusion_count=84, new_smoke_or_development_seeds=[], full_historical_freshness_scan_for_new_seeds_performed=False, seed_replacements=0))
    write('V3_SOURCE_FREEZE_RECEIPT.json', absent(formal_source_freeze_digest=None, prior_identity_reverification='PASS', source_audit=str(OUT / 'audit/SOURCE_REVERIFICATION.json'), historical_protected_files=source['historical_protected_files'], prior_qualification_freeze_digest=source['prior_qualification_freeze_digest'], checkpoint_sha256=prior_contract['checkpoint_sha256'], note='入口的源身份核验不等于正式科学源冻结。'))
    write('V3_FORMAL_FREEZE_RECEIPT.json', absent(freeze_digest=None, frozen_at_utc=None, freeze_created=False, authorized_to_execute=False, formal_seed_count=0, formal_pair_count=0, controlling_entry_receipt=str(OUT / 'V3_FORMAL_ENTRY_GATE_RECEIPT.json')))
    write('V3_EXECUTION_LEDGER.json', envelope('NOT_STARTED_ENTRY_GATE_FAILED', attempts=[], runs=[], scientific_retries=0, pre_exposure_infrastructure_retries=0, seed_replacements=0, technical_invalid_exposed_cells=0, native_processes_launched=0, model_forwards=0, counts=requested))
    write('V3_PAIR_VALIDITY_LEDGER.json', result(pairs=[], counts=requested, per_family=family_counts, technical_invalid_pairs=0, gate_assessment='NOT_REACHED_ENTRY_FAILED', requested_evaluability_thresholds_reference={'HIGH_overall': '>=22/24', 'HIGH_each_family': '>=5/6', 'LOW_overall': '>=10/12', 'LOW_each_family': '>=3/4'}))

    native_columns = ['run_id', 'pair_id', 'condition', 'arm', 'host_id', 'seed', 'true_intent_evaluation_metadata', 'execution_evaluable', 'local_task_achieved', 'correct_goal', 'wrong_goal', 'task_outcome_identity', 'native_completion', 'safe_completion', 'TCSC', 'Driving_Score', 'Route_Completion', 'official_success', 'infraction_penalty', 'collision_event_count', 'collision_incidence', 'outside_route_lanes', 'route_deviation', 'red_light', 'stop_sign', 'route_timeout', 'scenario_timeouts', 'vehicle_blocked', 'Driving_Efficiency', 'Driving_Smoothness_Comfort', 'ACT', 'ASK', 'WAIT', 'timely_ASK', 'answer_receipt', 'clarification_latency', 'answer_latency', 'clarification_triggered_full_replan', 'nonclarification_route_or_task_installation', 'nonclarification_full_replan', 'full_replan_admission', 'full_replan_execution', 'replan_latency', 'direct_control_intervention', 'extra_model_forwards', 'duplicate_model_candidate_execution']
    paired_columns = ['pair_id', 'condition', 'host_id', 'seed', 'true_intent_evaluation_metadata', 'first_arm', 'pair_evaluable', 'A0_run_id', 'A1_run_id', 'A0_TCSC', 'A1_TCSC', 'A0_correct_goal', 'A1_correct_goal', 'A0_wrong_goal', 'A1_wrong_goal', 'A0_native_completion', 'A1_native_completion', 'A0_safe_completion', 'A1_safe_completion', 'A0_Driving_Score', 'A1_Driving_Score', 'A0_Route_Completion', 'A1_Route_Completion', 'A0_collision', 'A1_collision']
    for name, columns in [('ALL_NATIVE_RESULTS.csv', native_columns), ('ALL_PAIRED_RESULTS.csv', paired_columns)]:
        with (OUT / name).open('x', newline='') as f:
            csv.writer(f).writerow(columns)
    rate = {'successes': None, 'observed_denominator': 0, 'rate': None, 'reason': 'NO_FORMAL_OBSERVATIONS'}
    discordance = {'A0_0_A1_0': None, 'A0_0_A1_1': None, 'A0_1_A1_0': None, 'A0_1_A1_1': None, 'observed_pair_count': 0}
    write('V3_HIGH_PRIMARY_RESULTS.json', result(counts=count(24), A0_TCSC=rate, A1_TCSC=rate, paired_risk_difference=None, confidence_interval_95=None, exact_McNemar_p=None, discordance_table=discordance, per_family_evaluability={f: family_counts[f] for f in FAMILIES[:4]}))
    write('V3_HIGH_MCNEMAR_ANALYSIS.json', result(exact_McNemar_p=None, test_executed=False, discordance_table=discordance, no_zero_cell_fabrication=True))
    write('V3_HIGH_PAIRED_RISK_ANALYSIS.json', result(paired_risk_difference=None, confidence_interval_95=None, CI_implementation=None, calculation_executed=False))
    binary = ['correct_goal', 'wrong_goal', 'native_completion', 'safe_completion', 'collision', 'official_success']
    write('V3_SECONDARY_BINARY_ANALYSIS.json', result(endpoints={e: {'A0': rate, 'A1': rate, 'discordance_table': discordance, 'exact_p': None} for e in binary}))
    description = dict(A0_mean=None, A0_median=None, A0_SD=None, A1_mean=None, A1_median=None, A1_SD=None, paired_mean_difference=None, paired_median_difference=None, paired_95_CI=None)
    write('V3_BENCH2DRIVE_PAIRED_ANALYSIS.json', result(label='Bench2Drive evaluator metrics on the paired ambiguity subset', continuous={e: description for e in ['Driving_Score', 'Route_Completion', 'Driving_Efficiency', 'Driving_Smoothness_Comfort']}, other_metrics={e: {'A0': None, 'A1': None, 'paired_comparison': None} for e in fields}, formal_evaluable_runs=0))
    arm_metrics = {e: None for e in ['TCSC', 'local_task_achieved', 'correct_goal', 'wrong_goal', 'task_outcome_identity', 'native_completion', 'safe_completion', 'Driving_Score', 'Route_Completion', 'collisions', 'official_success']}
    write('V3_PER_FAMILY_RESULTS.json', result(families={f: {'counts': family_counts[f], 'A0': arm_metrics, 'A1': arm_metrics, 'paired_results': []} for f in FAMILIES}))
    interactions = {e: None for e in ['ACT', 'ASK', 'WAIT', 'unnecessary_ASK', 'timely_ASK', 'answer_receipt', 'clarification_latency', 'answer_latency', 'clarification_triggered_full_replan', 'nonclarification_route_or_task_installation', 'nonclarification_full_replan', 'full_replan_admission', 'full_replan_execution', 'replan_latency', 'direct_control_intervention', 'extra_model_forwards', 'duplicate_model_candidate_execution']}
    write('V3_LOW_CONTROL_RESULTS.json', result(counts=count(12), A0=arm_metrics, A1=arm_metrics, A1_interaction=interactions, per_family_evaluability={f: family_counts[f] for f in FAMILIES[4:]}, nonregression_margin=None, nonregression_claim=False, LOW_pooled_into_HIGH=False))
    write('V3_INTERACTION_COST_RESULTS.json', result(HIGH_A1=interactions, LOW_A1=interactions, no_zero_cost_claim=True))
    write('V3_REPLAN_SEMANTICS_RESULTS.json', result(inherited_classification=low['classification_name'], formal_HIGH_A1=interactions, formal_LOW_A1=interactions, historical_reference={'scope': 'V2_ONLY_NOT_NEW_DATA', 'nonclarification_full_replans': 12}, subset_relationship='nonclarification_full_replan ⊆ nonclarification_route_or_task_installation；禁止父项与子项相加。', reference_path=str(PRIOR / 'FUTURE_REPLAN_METRIC_SEMANTICS.md')))
    write('V3_TRUE_INTENT_FIREWALL_RECEIPT.json', envelope('NOT_EXERCISED_NO_FORMAL_RUNTIME', formal_design_PASS=False, runtime_tested=False, formal_A0_pre_runtime_truth_reads=0, formal_A1_pre_ASK_truth_reads=0, formal_violations=0, formal_A1_answer_exposures=0, note='0仅表示没有正式运行事件；不表示已通过正式firewall集成认证。A*通过明确语言获知解释是合法开发输入，不能借旧truth字段常量声称其意图盲。'))
    write('V3_CONTROL_INTEGRITY_RECEIPT.json', envelope('NOT_EXERCISED_NO_FORMAL_RUNTIME', runtime_tested=False, formal_second_writer_count=0, formal_PID_controller_mutations=0, formal_unauthorized_model_forward_count=0, formal_duplicate_model_candidate_execution=0, frozen_source_identity_reverification='PASS', qualification_control_audit=str(OUT / 'audit/CONTROL_REVERIFICATION.json'), note='正式计数0来自未执行；前阶段77完整记录PASS、1首步前技术UNKNOWN，不伪报全部终端PASS。'))

    for name, title, text in [
        ('V3_FORMAL_SCIENTIFIC_CONTRACT.md', '正式契约未创建', '入口条件失败。用户要求的24 HIGH / 12 LOW配对仅作为原始目标记录；未创建有效契约、宿主分配、真意、顺序、种子或冻结。'),
        ('V3_TCSC_ENDPOINT_DEFINITION.md', 'TCSC定义引用，未正式冻结', '前阶段端点为局部任务正确、满足原生完成条件且没有冻结重大安全失败。原生完成为Perfect/Completed或RC≥95；重大失败并集为三类碰撞、outside_route_lanes、route_dev。沿用来源引用不构成正式协议冻结。'),
        ('V3_PRIMARY_VERDICT_RULE.md', '主判决规则未采纳或冻结', '入口门槛失败，主科学判决为NOT_EVALUABLE_DRIVECLARIFY_PAIRED_SUPERIORITY。未选择或运行检验，未冻结优效控制规则。'),
        ('V3_STATISTICAL_ANALYSIS_PLAN.md', '统计计划未创建', '没有CI实现或正式统计冻结，没有McNemar、风险差、置信区间或次要检验。所有结果为空；不能从无观测推断无效、等效或LOW非退化。'),
        ('V3_BENCH2DRIVE_METRIC_AUDIT.md', 'Bench2Drive来源只读审计', '标签：Bench2Drive evaluator metrics on the paired ambiguity subset。JSON列出安装来源哈希和字段映射，未运行新后处理。效率及舒适度引用既有官方工具；正式运行可用性未测试。没有可单列的offroad/wrong-lane字段，不创建。没有完整排行榜分数。'),
    ]:
        md(name, '# ' + title + '\n\n' + STATUS + '\n\n' + text + '\n\n对应JSON保存明确的未执行状态与来源路径。')
    table('TABLE_V3_HIGH_PRIMARY_TCSC.md', 'HIGH主要任务端点', FAMILIES[:4] + ['Overall'], [a + ' ' + m for m in ['TCSC', 'correct goal', 'wrong goal', 'safe completion'] for a in ['A0', 'A1']])
    table('TABLE_V3_BENCH2DRIVE_METRICS.md', 'Bench2Drive evaluator metrics on the paired ambiguity subset', ['HIGH overall', 'LOW overall'] + FAMILIES, [a + ' ' + m for m in ['Driving Score', 'Route Completion', 'official success', 'infraction penalty', 'collision', 'outside_route_lanes', 'route deviation', 'blocked/timeout', 'efficiency', 'smoothness/comfort'] for a in ['A0', 'A1']])
    table('TABLE_V3_LOW_CONTROL.md', 'LOW对照', FAMILIES[4:] + ['Overall'], [a + ' ' + m for m in ['TCSC', 'native completion', 'safe completion', 'DS', 'RC', 'success', 'collisions'] for a in ['A0', 'A1']] + ['A1 unnecessary ASK', 'A1 WAIT', 'A1 clarification-triggered replan', 'A1 nonclarification route/task installation', 'A1 direct control intervention'])
    table('TABLE_V3_INTERACTION_COST.md', 'HIGH A1交互成本', FAMILIES[:4] + ['Overall'], ['ASK', 'timely ASK', 'answer', 'clarification latency', 'Full Replan admission', 'Full Replan execution', 'replan latency'])

    repairs = [
        {'file': str(OUT / 'tooling/verify_entry.py'), 'function': 'main.static.same_public_scene_canonical_digest', 'classification': 'ENGINEERING_AUDIT_PARSER_FIX', 'reason': '池的public_actors_sha256为规范JSON摘要，首次审计误按格式化文件字节比较；按冻结build_pool.py:72定义校正。本轮只读审计重新计算，原始结果未重跑。', 'behavior_affected': '审计字段解释', 'model_control_task_semantics_changed': False},
        {'file': str(OUT / 'tooling/verify_entry.py'), 'function': 'main.crash_checks.no_agent_step_entry', 'classification': 'ENGINEERING_EVIDENCE_PATH_FIX', 'reason': '只读发现evaluator.log实际在process_job目录，修正定位。', 'behavior_affected': '原始日志发现', 'model_control_task_semantics_changed': False},
        {'file': str(OUT / 'tooling/verify_entry.py'), 'function': 'write/main', 'classification': 'ENGINEERING_AUDIT_RESUME', 'reason': '修复后保留最初保护基线；既存审计必须内容完全一致，拒绝覆盖不同内容。', 'behavior_affected': '只读审计可恢复写入', 'model_control_task_semantics_changed': False},
    ]
    write('audit/ENGINEERING_MODIFICATIONS.json', {'repairs': repairs, 'new_tools': [{'file': str(OUT / 'tooling' / n), 'classification': 'NON_SCIENTIFIC_READ_ONLY_VERIFICATION_OR_REPORTING', 'model_control_task_semantics_changed': False} for n in ['verify_entry.py', 'report_blocked.py', 'validate_final.py']], 'original_verifier_preserved': str(OUT / 'tooling/revisions/verify_entry_initial.py'), 'scientific_files_modified': [], 'native_launch_repairs': [], 'scientific_retry_count': 0})
    md('V3_ENGINEERING_REPAIR_LOG.md', '# 工程修改记录\n\n新增工具全部位于本阶段tooling目录，仅做只读入口核验、报告和最终完整性验证。未修改模型、控制器、宿主、任务评估器或科学源。\n\n' + '\n\n'.join(f"- `{r['file']}` / `{r['function']}`：{r['reason']} 分类`{r['classification']}`；影响{r['behavior_affected']}，模型/控制/任务语义改变NO。" for r in repairs) + '\n\n第一次入口审计在静态摘要断言处停止，已完成78次原始证据复算。第二次为离线审计复核，不是native科学重试。初始工具副本保存在tooling/revisions/verify_entry_initial.py。详细分类见audit/ENGINEERING_MODIFICATIONS.json。')
    md('COMMAND_LOG.md', '''# 本阶段命令日志

仅在新阶段目录生成文件；既有资格与历史目录只读。命令运行目录为 /home/buaa/wrh/DriveClarify。

1. `cat /home/buaa/.codex/attachments/1ef181b5-1aa7-4a52-b463-8b20dbe1eebb/pasted-text.txt` 读取完整条件授权。`rg --files` 检查AGENTS/CLAUDE；读取CLAUDE.md。目标新目录不存在（ls退出2是预期的不存在）。
2. Python/read_text、cat、sed、rg分批读取完整前阶段要求的18项材料、pool/contract/ledger/results结构、冻结与保护清单、LOW源码链、静态证据、任务评分器、raw-language adapter和官方指标源。一次查找不存在的oracle.py退出2；未修改任何源。
3. 通过apply_patch新增tooling/verify_entry.py。`PYTHONDONTWRITEBYTECODE=1 python -u reports/driveclarify_rq3_paired_v3_formal_comparison_end_to_end_v1/tooling/verify_entry.py` 第一次：完成11288文件保护快照、78原始运行复算、146软件检查，在公共实体摘要字段断言处退出1。没有native子进程。
4. Python只读核查冻结build_pool.py摘要算法及实际日志位置；保存初版到tooling/revisions/verify_entry_initial.py。apply_patch修正规范JSON摘要、process_job/evaluator.log路径、相同内容的幂等审计恢复。详见工程修改记录。
5. 同一`PYTHONDONTWRITEBYTECODE=1 python -u .../tooling/verify_entry.py`重新执行只读审计；原始记录再计算，前阶段文件禁止写入；完成入口失败回执。无科学重试。
6. `PYTHONDONTWRITEBYTECODE=1 python -u reports/driveclarify_rq3_paired_v3_formal_comparison_end_to_end_v1/tooling/report_blocked.py` 创建未冻结/未执行状态文件、表头CSV、N/E表格、60项返回、最终报告。
7. `PYTHONDONTWRITEBYTECODE=1 python -u reports/driveclarify_rq3_paired_v3_formal_comparison_end_to_end_v1/tooling/validate_final.py` 再查保护文件与前阶段完整树、JSON/CSV、必需文件、入口/状态/空结果一致性，生成FINAL_ARTIFACT_AUDIT.json和FINAL_VALIDATION_RECEIPT.json；权威结果见两文件。

本阶段没有执行CARLA、evaluator launcher、seed generator、formal freeze或任何native/smoke命令。入口审计/报告/最终检查共享Python audit hook，禁止子进程和新阶段目录外写入。审计工具错误及恢复都属于工程处理，不改变先验资格或科学结果。
''')

    values = [
        ('exact_execution_status', STATUS), ('exact_scientific_verdict', VERDICT), ('prior_host_readiness_status', gate['prior_status']), ('LOW_replan_classification_imported', low['classification_name']), ('formal_freeze_digest', None), ('qualified_host_IDs_all_conditions', hosts['selected']), ('all_formal_seeds', []), ('all_true_intent_assignments', []), ('HIGH_planned_executed_evaluable_pairs', count(24)), ('per_HIGH_family_evaluability', {f: family_counts[f] for f in FAMILIES[:4]}), ('LOW_planned_executed_evaluable_pairs', count(12)), ('per_LOW_family_evaluability', {f: family_counts[f] for f in FAMILIES[4:]}),
        ('A0_HIGH_TCSC', rate), ('A1_HIGH_TCSC', rate), ('paired_risk_difference', None), ('paired_95_CI', None), ('exact_McNemar_p', None), ('full_TCSC_discordance_table', discordance),
    ]
    for key in ['correct_goal_rates', 'wrong_goal_rates', 'native_completion', 'safe_completion']:
        values.append(('A0_A1_' + key, {'A0': None, 'A1': None}))
    values += [('A0_A1_Driving_Score_mean_median', description), ('paired_DS_difference_CI', None)]
    for key in ['Route_Completion', 'official_success', 'collision_comparison', 'outside_route_lanes_comparison', 'route_deviation_comparison', 'blocked_timeout_comparison', 'efficiency_comparison', 'smoothness_comfort_comparison']:
        values.append((key, {'A0': None, 'A1': None}))
    for key in ['A1_LOW_unnecessary_ASK', 'A1_LOW_WAIT', 'A1_LOW_clarification_triggered_Full_Replan', 'A1_LOW_nonclarification_route_task_installation', 'A1_LOW_direct_control_intervention', 'A1_HIGH_timely_ASK', 'A1_HIGH_answer_receipt', 'A1_HIGH_Full_Replan_admission_execution', 'clarification_latency', 'replan_latency']:
        values.append((key, None))
    values += [(f + '_paired_results', []) for f in FAMILIES[:4]]
    for key in ['scientific_retries', 'pre_exposure_infrastructure_retries', 'seed_replacements', 'technical_invalid_exposed_cells', 'true_intent_firewall_violations', 'second_writer_count', 'PID_controller_mutations', 'unauthorized_model_forward_count']:
        values.append((key, 0))
    values += [('source_checkpoint_identity_status', 'PASS_PRIOR_IDENTITY_REVERIFIED_NO_FORMAL_FREEZE'), ('scientific_files_changed', []), ('historical_RQ1_RQ2_RQ3_changed', False), ('historical_paired_V2_changed', False), ('all_engineering_repairs', repairs), ('exact_artifact_paths', links)]
    assert len(values) == 60
    return_items = [{'number': i, 'field': key, 'value': value, 'scope': 'CURRENT_STAGE_NO_FORMAL_EXECUTION' if i not in [3, 4, 6] else 'PRIOR_QUALIFICATION_REFERENCE'} for i, (key, value) in enumerate(values, 1)]
    write('FINAL_RETURN.json', envelope('COMPLETE_BLOCKED_ENTRY_REPORT', requested_60_items=return_items, counts=requested, null_semantics='没有观测或没有创建；不等于0%、p=1、等效或实验性失败。', zero_process_counters_semantics='没有正式启动，因而本阶段事件数为0；不是运行时验证PASS。'))
    lines = ['# V3正式比较入口审计', '', '**' + STATUS + '**', '', '**' + VERDICT + '**', '',
             '本阶段在正式入口停止。前阶段结果不能满足七族全部合格；本轮从原始证据重算后仍仅LMK-E合格。没有形成正式比较，不能据此支持或反驳DriveClarify优效，也不能推断等效。', '',
             '## 决定性失败条件', '', '冻结资格要求：同一模板六次全部可评估，两解释各三次且各≥2次TCSC成功，再满足静态和完整性门槛。下表仅是既有A*开发资格，绝不是本阶段A0/A1结果。', '',
             '|条件|P1：解释1；解释2|P2：解释1；解释2|选定合格宿主|', '|---|---|---|---|']
    def fmt(t):
        if not t['tested']:
            return '未执行（P1已通过）'
        return '；'.join(f"{x['successes']}/{x['evaluable']}可评估" + (f" + {x['unknown']} UNKNOWN（计划3）" if x['unknown'] else '') for x in t['interpretations'].values())
    for f in hosts['families']:
        lines.append(f"|{f['family']}|{fmt(f['P1'])}|{fmt(f['P2'])}|{f['selected'] or 'NONE'}|")
    lines += ['', '明确失败：REF-C、LMK-C、ORD-C、USC-C、REF-E、ORD-E的qualified=YES谓词均为假；因此“每族恰好一个合格宿主”和“全部所需宿主A*可行性通过”也为假。P1→P2顺序复核通过；没有补充、替换或扩宽宿主。', '', '## 其他入口谓词', '', '|谓词|结果|证据范围与原因|', '|---|---|---|']
    for p in gate['predicates'][7:]:
        lines.append(f"|{p['predicate']}|{p['status']}|{p['reason']}|")
    lines += ['', 'true-intent firewall设计没有完整七宿主正式A1通道的集成证明，保留UNVERIFIED；入口已被资格条件阻断，不通过创建正式协议补齐。PID/controller源身份一致，77个完整终端记录通过；ASTAR-REF-E-P2-I1-S1在setup后首个agent步骤前CARLA Signal11崩溃，轨迹空且无官方终端记录，故全78次终端证据无法记为PASS。这不代表发现了第二控制writer。LMK-E/P1的六次记录全部完整。', '',
              '## 独立核验与完整性', '',
              f"重新哈希{source['historical_protected_files']}个历史保护文件，差异0；前阶段完整树{source['prior_tree_files']}个文件纳入本阶段前后保护。读取并解析前阶段{source['prior_json_parsed']}个JSON；152个冻结源/输入、路线及地图引用、23个最终产物的哈希全部一致。既有checkpoint保持`{prior_contract['checkpoint_sha256']}`。最终前后保护校验见FINAL_VALIDATION_RECEIPT.json。", '',
              '对78项既有A*尝试逐项用冻结纯函数重读原始轨迹、权威指标和终端证据，全部复现归档评分；另从两解释分母和冻结规则独立重建七族选择。77次可评估，1次技术UNKNOWN；146项纯软件评分检查通过。本阶段没有native模型调用。84项既有开发种子仍全部排除未来正式用途，包括LMK-E/P2未使用的6项。路线/地图通过冻结池中的哈希引用绑定；不声称历史逐次打开文件时另有路线字节收据。', '',
              '## LOW语义', '',
              '导入C — GENUINE_DRIVECLARIFY_FULL_REPLAN_WITHOUT_ASK。12/12历史事件重新核对源码/事件哈希、ACT/无ASK/无answer、准入、事务、几何改变和原生消费。必须保留为真实方法成本。分别记录clarification_triggered_full_replan、nonclarification_route_or_task_installation及其nonclarification_full_replan子集，父子项不相加。12次属于V2历史引用，本阶段LOW观测为空。', '',
              '## 正式执行与统计', '',
              '|范围|请求计划配对|实际冻结|已执行|可评估|', '|---|---:|---:|---:|---:|', '|HIGH|24|0|0|0|', '|LOW|12|0|0|0|', '|合计|36|0|0|0|', '',
              '请求目标为72次native运行；实际运行0。正式冻结digest=null，正式种子=[]，真意分配=[]，配对顺序=[]。没有正式科学契约、CI实现、优效规则或源冻结。要求的同名文件只记录未创建/未执行状态，不能用作有效冻结。主次端点、配对风险差、95% CI、McNemar p、四格表、Bench2Drive指标和交互成本全部为null/N/E；表头CSV无数据行。不是0/24成功率，也不是零成本结论。', '',
              '所有正式科学重试、基础设施重试、换种子、暴露后技术无效单元、第二writer、PID变更、越权forward和true-intent违规计数均为0，原因是本阶段未执行。它们不替代运行时完整性认证。', '',
              '## 历史保留', '',
              'RQ1/RQ2不变。历史RQ3-V3保持H-RQ3-A=NOT_EVALUABLE、H-RQ3-B=NOT_SUPPORTED、H-RQ3-C=SUPPORTED、overall=RQ3_V3_NOT_SUPPORTED_INSUFFICIENT_EVALUABILITY。Paired V1的blocked树不变。V2保持PASS_RQ3_PAIRED_V2_COMPARISON_COMPLETE及NOT_SUPPORTED_DRIVECLARIFY_IMPROVES_TASK_CORRECT_SAFE_COMPLETION，是存在任务宿主地板效应的合法前瞻结果；未覆盖或改释为等效。', '',
              '没有修改科学文件、论文或既有宿主，没有启动替代实验或第二骨干。只读审计初版的摘要类型/日志路径/恢复逻辑问题已修复并记录；不涉及科学重试。', '',
              '## 完整返回与路径', '',
              '[FINAL_RETURN.json](FINAL_RETURN.json)逐项提供用户要求的60项返回和全部绝对路径。[V3_FORMAL_ENTRY_GATE_RECEIPT.json](V3_FORMAL_ENTRY_GATE_RECEIPT.json)给出所有入口谓词；[audit/A_STAR_RAW_RESCORING.json](audit/A_STAR_RAW_RESCORING.json)和[audit/HOST_QUALIFICATION_RECOMPUTATION.json](audit/HOST_QUALIFICATION_RECOMPUTATION.json)提供逐次复算及选取证据。[FINAL_ARTIFACT_AUDIT.json](FINAL_ARTIFACT_AUDIT.json)与[FINAL_VALIDATION_RECEIPT.json](FINAL_VALIDATION_RECEIPT.json)记录最终验证。', '', '完整目录：`' + str(OUT) + '/`。工作于入口失败报告完成后停止。']
    md('FINAL_REPORT.md', '\n'.join(lines))
    write('audit/REQUIRED_ARTIFACTS.json', {'required': REQUIRED, 'count': len(REQUIRED), 'final_report_path': str(OUT / 'FINAL_REPORT.md'), 'final_return_path': str(OUT / 'FINAL_RETURN.json')})
    print('入口失败报告已生成；42项规定产物待最终验证；正式运行0', flush=True)


if __name__ == '__main__':
    main()
