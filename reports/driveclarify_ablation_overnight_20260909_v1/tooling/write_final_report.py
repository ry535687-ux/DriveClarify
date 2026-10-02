"""Compose the final narrative from an explicit completed delivery, without scoring."""
import argparse
import datetime
import json
from pathlib import Path

from build_delivery import sha, validate_snapshot


def load(path):
    return json.loads(path.read_text())


def number(value):
    return '缺失' if value is None else ('%.4f' % value).rstrip('0').rstrip('.')


def validate_delivery(root, delivery, audit_path):
    """Bind the narrative, frozen summary and final audit to one snapshot."""
    root, delivery = Path(root).resolve(), Path(delivery).resolve()
    source = load(delivery / 'DELIVERY_PROVENANCE.json')
    snapshot = Path(source['snapshot']).resolve()
    manifest, queue, rows, expected_sources = validate_snapshot(root, snapshot)
    recorded = source['source_files']
    for path, expected in expected_sources.items():
        if recorded.get(path) != expected:
            raise SystemExit('Delivery provenance does not match current verified source: ' + path)
    for path, expected in recorded.items():
        if sha(Path(path)) != expected:
            raise SystemExit('Delivery source changed after packaging: ' + path)
    for name in ('episode_results.csv', 'paired_results.csv', 'summary_tables.md',
                 'paper_table.csv', 'summary.json', 'ANALYSIS_PROVENANCE.json'):
        if sha(delivery / name) != sha(snapshot / 'statistics' / name):
            raise SystemExit('Copied delivery statistics differ from source snapshot: ' + name)
    audit = load(audit_path)
    if Path(audit['fixed_snapshot']).resolve() != snapshot:
        raise SystemExit('Audit and delivery refer to different snapshots')
    if audit.get('failed_check_count') != 0 or not audit.get('status', '').startswith('PASS'):
        raise SystemExit('Final narrative requires a passing audit; preserve failures for review')
    if audit.get('raw_terminal_audit_count') != sum(bool(r.get('terminal_complete')) for r in rows):
        raise SystemExit('Audit terminal coverage differs from normalized snapshot')
    summary = load(delivery / 'summary.json')
    groups = [g for g in summary['groups'] if g.get('experiment_id') == 'ABL_TASK_RELATION'
              and g.get('phase') == 'FORMAL']
    if len(groups) != 1:
        raise SystemExit('Expected exactly one formal task-relation summary group')
    return source, manifest, queue, rows, summary, groups[0], audit


def metric_eligible_rows(rows, terminal_rows, metric_summary):
    if metric_summary.get('eligibility_rule') == 'OBSERVED_COST_INCLUDING_PARTIAL_RUNS':
        return rows
    return terminal_rows


def descriptive_sequence_block(audit):
    pairs = audit.get('pairs', [])
    flagged = [(p['pair_id'], p['execution_sequence_comparison']) for p in pairs
               if (p.get('execution_sequence_comparison') or {}).get('same_sequence_different_endpoint')]
    if 'descriptive_same_sequence_different_endpoint_pairs' not in audit:
        return ('当前输入审计未提供V3同序列/异终点字段；不据此推断纯噪声。'
                '已有定向检查仅属冻结后描述，见protocol_statistics/targeted_lmk_c_s01_descriptive_v3。')
    lines = ['同序列/异终点审计描述：%s个配对记录到相同语义事件与公共动作序列、不同原生终点。'
             '序列匹配忽略随机ID和事件时刻，已记录时间差另列；相同选择不能证明隐藏状态相同、'
             '不能把差异归为纯噪声，也不能排除策略影响。这不是新增科学端点或性能案例筛选。' % len(flagged)]
    for pair_id, comparison in flagged:
        lines.append('- %s：不同原始端点 `%s`；安装route identity序列相同=%s；'
                     '事件时刻比较 `%s`。' % (pair_id,
                         json.dumps(comparison.get('differing_endpoint_fields'), ensure_ascii=False),
                         comparison.get('installed_route_identity_sequence_equal'),
                         json.dumps(comparison.get('matching_sequence_event_time_comparison'), ensure_ascii=False)))
    return '\n\n'.join(lines)


def infrastructure_maintenance_block(index):
    incidents = index.get('incidents', [])
    if not incidents:
        return '本交付维护索引未列出崩溃事件；不据索引缺失推断从未发生基础设施故障。'
    lines = ['基础设施维护：索引记录%s次队列恢复；这个计数与episode启动重试、暴露后重试分别报告。'
             '完整原始崩溃、定向清理、旧dispatch history及恢复回执见INFRASTRUCTURE_MAINTENANCE_INDEX.json。' %
             number(index.get('queue_resumption_event_count'))]
    for item in incidents:
        exposure = item['observed_partial_exposure']
        receipt = item.get('cleanup_process_receipt') or {}
        intent = item.get('cleanup_intent') or {}
        preconditions = intent.get('preconditions') or {}
        cleanup_scope = ('这是已崩溃世界的工程清理，没有改写存活episode的冻结horizon。' if
                         preconditions.get('carla_process_gone') is True and
                         preconditions.get('native_480s_timeout_already_occurred') is True and
                         intent.get('episode_retry') is False else
                         '清理范围须按原始intent核对，本生成器不从缺失条件推断horizon保持不变。')
        endpoints = item['scientific_endpoints_from_normalized']
        endpoint_text = ('归约科学终点保持null，不填作驾驶成功、驾驶失败或自然完成' if
                         endpoints and all(v is None for v in endpoints.values()) else
                         '科学终点原样保留为`%s`，不另行填补' % json.dumps(endpoints, ensure_ascii=False))
        lines.append('- 第%s次 `%s`：`%s`，已暴露%s次实际control、%s次native forward、%s次候选forward。'
                     '%s；对应配对`%s`保留为不完整=%s。进程总墙钟%s秒，含启动、执行及崩溃后等待/清理；'
                     '不能当作同长自然episode或同长驾驶，亦未单独估计纯维护时长。runtime_sim_s=%s。'
                     '原480秒超时已发生=%s；清理前CARLA已退出=%s；定向孤儿evaluator信号=%s，退出码%s。'
                     '%s' % (
                         item['schedule_position'], item['run_id'], item['classification_from_capture'],
                         number(exposure.get('actual_control_count')), number(exposure.get('native_model_forward_count')),
                         number(exposure.get('candidate_forward_count')), endpoint_text, item['pair_id'],
                         item['pair_retained_incomplete_for_primary_endpoint'], number(item.get('runtime_wall_s')),
                         number(item.get('runtime_sim_s')), preconditions.get('native_480s_timeout_already_occurred'),
                         preconditions.get('carla_process_gone'), (item.get('cleanup_signal') or {}).get('signal'),
                         number(receipt.get('evaluator_exit')), cleanup_scope))
        resume = item.get('queue_resumption_receipt')
        if resume:
            lines.append('  恢复回执记录：前%s次记录原样保留，从`%s`继续同一manifest的未运行余项；'
                         'episode重试%s、种子替换%s。计划仍为%s次/%s配对；此次技术缺失后最多可有%s次完整终止/'
                         '%s个完整配对，这些是上限，实际完成数以前表为准。恢复队列不等于重跑崩溃episode。' % (
                             number(resume.get('preserved_prior_run_count')), resume.get('resumed_at_run_id'),
                             number(resume.get('episode_retries')), number(resume.get('seed_replacements')),
                             number(resume.get('frozen_planned_runs')), number(resume.get('frozen_planned_pairs')),
                             number(resume.get('maximum_possible_complete_episodes')),
                             number(resume.get('maximum_possible_complete_pairs'))))
    return '\n\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-root', type=Path, required=True)
    parser.add_argument('--delivery', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing final report')
    root = args.report_root.resolve()
    delivery = args.delivery.resolve()
    source, manifest, queue, rows, summary, group, audit = validate_delivery(root, delivery, args.audit)
    freeze = load(root / 'formal/FORMAL_FREEZE.json')
    arms = ['ABL_FULL', 'ABL_TRAJ_ONLY']
    arm_by_run = {p['run_id']: p['variant'] for p in manifest['runs']}
    arm_runs = {arm: [r for r in rows if arm_by_run[r['run_id']] == arm] for arm in arms}
    terminals = {arm: [r for r in arm_runs[arm] if r.get('terminal_complete')] for arm in arms}
    coverage = load(delivery / 'OBSERVED_CONDITION_COVERAGE.json')['rows']
    complete_ids = {r['run_id'] for rs in terminals.values() for r in rs}
    coverage_lines = ['正式完整终止episode的首决策实测覆盖如下。它是冻结后的描述，'
                      '不是根据结果选择场景；不同臂独立驾驶，不能将其强行视为事前固定的配对分层。', '',
                      '| 实测短轨迹 / 独立任务关系 | ABL_FULL | ABL_TRAJ_ONLY |',
                      '|---|---:|---:|']
    for truth, short, label in [
        ('TASK_EQUIVALENT', 'CLOSE', '接近 / 等价'),
        ('TASK_EQUIVALENT', 'DIFFERENT', '不同 / 等价'),
        ('TASK_CRITICAL', 'CLOSE', '接近 / 分歧'),
        ('TASK_CRITICAL', 'DIFFERENT', '不同 / 分歧')]:
        counts = [sum(r['run_id'] in complete_ids and r['arm'] == arm and
                      r['physical_relation_truth'] == truth and
                      r['short_trajectory_condition'] == short for r in coverage) for arm in arms]
        coverage_lines.append('| %s | %s | %s |' % (label, counts[0], counts[1]))
    unknown_counts = [sum(r['run_id'] in complete_ids and r['arm'] == arm and
        (r['physical_relation_truth'] not in ('TASK_EQUIVALENT', 'TASK_CRITICAL') or
         r['short_trajectory_condition'] not in ('CLOSE', 'DIFFERENT')) for r in coverage) for arm in arms]
    coverage_lines.append('| 缺失 / UNKNOWN | %s | %s |' % tuple(unknown_counts))
    coverage_block = '\n'.join(coverage_lines)
    arm_rows = []
    for arm in arms:
        a = group['arms'][arm]
        arm_rows.append('| %s | %s | %s | %s | %s | %s | %s/%s |' %
            (arm, a['planned'], a['started'], a['complete_terminal'],
             a['driving_failure'], a['technical_interruption'],
             a['primary_successes'], a['primary_observed']))
    metric_specs = [
        ('实际询问总数', 'ask_emitted_count', 'sum'),
        ('询问请求总数', 'ask_requested_count', 'sum'),
        ('已接收回答总数', 'answer_received_count', 'sum'),
        ('关系预测：分歧 / 等价 / UNKNOWN', None, 'relation'),
        ('任务等价 episode 中发生询问', 'unnecessary_question', 'binary'),
        ('任务关键 episode 全程无实际询问', 'critical_episode_no_question', 'binary'),
        ('正确语言任务完成', 'language_task_complete', 'binary'),
        ('错误目标实际停车执行', 'wrong_target_execution', 'binary'),
        ('原生路线完成', 'native_route_complete', 'binary'),
        ('碰撞', 'collision', 'binary'),
        ('驶出路线车道 / 错误车道合并端点', 'offroad_or_wrong_lane', 'binary'),
        ('红灯 / 停车违规', 'traffic_violation', 'binary'),
        ('原生超时', 'timeout', 'binary'),
        ('停止推进', 'nonprogress', 'binary'),
        ('FALLBACK', 'fallback', 'binary'),
        ('平均进程墙钟秒（含故障等待/清理）', 'runtime_wall_s', 'mean'),
        ('平均模型 forward 总数', 'model_forward_count', 'mean'),
        ('方法候选 forward 总数', 'candidate_forward_count', 'sum'),
        ('诊断额外 forward 总数', 'diagnostic_forward_count', 'sum')]
    metric_rows = []
    for label, key, kind in metric_specs:
        values = []
        for arm in arms:
            rs = metric_eligible_rows(arm_runs[arm], terminals[arm],
                                      group['arms'][arm]['metrics'].get(key, {}))
            if kind == 'relation':
                parts = []
                for field in ('relation_divergent_count', 'relation_equivalent_count', 'relation_unknown_count'):
                    observed = [r[field] for r in rs if r.get(field) is not None]
                    parts.append('%s（n=%s）' % (number(sum(observed)) if observed else '缺失', len(observed)))
                value = ' / '.join(parts)
            else:
                observed = [r[key] for r in rs if r.get(key) is not None]
                if not observed:
                    value = '缺失（0可用）'
                elif kind == 'binary':
                    value = '%s/%s' % (sum(bool(x) for x in observed), len(observed))
                elif kind == 'sum':
                    value = '%s（%s episodes）' % (number(sum(observed)), len(observed))
                else:
                    value = '%s（n=%s）' % (number(sum(observed) / len(observed)), len(observed))
            values.append(value)
        metric_rows.append('| %s | %s | %s |' % (label, values[0], values[1]))
    effect = group['paired_effects']['correct_safe_complete']
    attempt_audit = load(delivery / 'FAILURE_AND_ATTEMPT_AUDIT.json')
    attempts = attempt_audit['receipt_totals']
    maintenance_path = delivery / 'INFRASTRUCTURE_MAINTENANCE_INDEX.json'
    maintenance = load(maintenance_path) if maintenance_path.exists() else {}
    completed = sum(len(v) for v in terminals.values())
    floor_note = ('本矩阵所有完整终止episode的正确语言任务完成均为0，存在任务终点的底限问题。'
                  '这不能证明两个判断器等效或删减机制无用；公共执行能力可能限制本矩阵对最终任务收益的辨别，'
                  '这是结果解释的限制，不是已证明的唯一失败原因，也不是重跑或调参的理由。'
                  if completed and all(r.get('language_task_complete') is False
                                       for rs in terminals.values() for r in rs) else '')
    ask_chains = audit.get('ask_chain_counts', {})
    commits = audit.get('native_route_commit_counts', {})
    lines = [
        '# DriveClarify 本轮闭环消融最终报告', '',
        '状态：`ABLATION_OVERNIGHT_PARTIAL_REVIEW_REQUIRED`。A 已完整终止 **%s/%s 次运行、%s/%s 配对**；'
        'B 正式运行 **0 次、0 配对**，因当前完整实现没有已定义且实际参与决策的非 oracle 未来证据预测而单独阻断。'
        '本状态不代表完整方法获胜，也不代表论文所有模块已形成闭环实现。' %
        (completed, manifest['planned_runs'], group['complete_terminal_pairs'], manifest['planned_pairs']), '',
        '真实主机预算：Asia/Shanghai，2026-09-09 00:56:26 至 10:56:26（10小时）。'
        '本报告生成于 %s；队列结束状态 `%s`。标准 Bench2Drive 持续暂停，不自动恢复。' %
        (datetime.datetime.now().astimezone().isoformat(), queue['status']), '',
        '为了检验任务关系判断对询问与最终驾驶结果的影响，本轮比较了保持共同候选、记忆、时机、'
        '回答和执行后端的 ABL_FULL 与 ABL_TRAJ_ONLY。主要指标是独立物理任务判据下的正确安全完成，'
        '比较方向预先指定为 FULL−TRAJ。', '',
        '| 版本 | 计划 | 启动 | 完整终止 | 驾驶失败 | 技术中断 | 正确安全完成/可用 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    lines += arm_rows + ['',
        '主要指标配对差为 **%s**，按模板聚类 bootstrap 95%% 区间 **[%s, %s]**，'
        '可用配对 %s，模板 %s。区间基于少量模板；退化为零的经验区间不证明等效或非劣效。'
        '所有驾驶失败保留在分母，未运行/技术缺失没有填成成功、零时间或零风险。' %
        (number(effect['estimate']), number(effect['ci95_low']), number(effect['ci95_high']),
         effect['n'], effect['template_count']), '',
        '| 指标 | ABL_FULL | ABL_TRAJ_ONLY |', '|---|---:|---:|']
    lines += metric_rows + ['',
        '行为计数与结果比例使用相应完整终止且指标有观测的 episode；资源时间与模型/候选/诊断成本'
        '沿用冻结summary的OBSERVED_COST_INCLUDING_PARTIAL_RUNS口径，保留技术中断的已观测成本。'
        '全部28个计划行、缺失原因、'
        '不完整配对和每项配对差/区间仍完整见 episode_results.csv、paired_results.csv、paper_table.csv '
        '及 summary_tables.md。次要机制指标是描述性结果，没有事后挑选显著项或追加样本。', '',
        '以上结果将机制变化与语言任务终点分开。实际问答与路由重规划可以完成，而车辆仍可能碰撞、'
        '未在正确区域停车或停止推进；这些都不能算成任务完成。错误目标执行采用“在错误目标区域完成规定停车”'
        '的严格定义，未在任何区域停车不会被误写成正确执行。', '',
        floor_note, '',
        '两个版本的精确定义与范围：', '',
        '- ABL_FULL 委托已有 RQ1/RQ3 的公共任务义务比较器；它使用受控场景的预认证任务签名，'
        '不是任意视觉/语言任务关系推理器。',
        '- ABL_TRAJ_ONLY 仅比较两个候选的原生等时 speed waypoints：自车坐标，x向前/y向右，米；'
        '0至2秒，0.25秒网格，对齐后最大 L2 距离大于0.27119792945561905米判为任务分歧，否则等价。'
        '缺失/无效输入保持 UNKNOWN。未把等距离 route 样点充当时间采样点。',
        '- TRAJ 判断器只收 TimedTrajectory。runtime task_signatures 仅含候选/绑定 identity tokens；'
        '任务标签、正确目标、私人意图和 Full 的关系答案没有作为判断输入。公共导航与独立评分仍合法使用地图/任务定义，'
        '其用途与被消融判断器隔离。',
        '- 两臂共同首次接入两个同观测 official Dreaming 候选 forward、共同随机数初始化和只读日志；'
        '比较同一时刻的候选时非语言上下文相同并有摘要核验。它们是本轮共同工程接线，不能追认旧实验已经使用。',
        '- 共同 checkpoint 为 cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044，'
        '包含原896参数适配器；未训练、未改 PID。回答绑定、路由安装、fresh native inference、安全/可恢复性检查沿用相同后端。',
        '- 两臂保留现有 RQ3 记忆与联合时机。其部分安全/可问证据来自预认证常量，窗口为脚本假设，'
        '没有独立动态安全或真实 TTC 证明；本次结果范围因此限于受控比较。', '',
        '时钟与缺失端点：候选使用前一完成的原生传感器 tick，明确记录真实来源frame/time与不超过0.1秒仿真年龄，'
        '没有冒充当前帧。问答、决策和等待按仿真时间记录；推理耗时与broker文件轮询用墙钟。'
        '同步CARLA中模型计算的墙钟耗时不等于车辆已行驶同长仿真时间，本轮不宣称实时运行或真实时间安全余量。共同0.5秒保守回答预留'
        '不是实际延迟；实际接收与发问时间分别落盘。独立真实合法询问机会尚不可得，及时/漏问/过晚/错窗端点为null。'
        'critical episode无询问只是描述计数，不是“所有HIGH都必须立刻问”的漏问率。脚本anchor+1.8秒余量仅为 nominal 诊断。', '',
        '开发与冻结：2个开发配对、4次真实驾驶均完整终止且失败，另有1次模型setup前工程失败。'
        '阈值只取预先声明的两个无标签开发候选距离的中位数，未读取正式结果。正式采用全部7个可用物理模板、'
        '每模板2个新种子，两臂先后顺序每模板平衡；28次规模根据开发耗时、磁盘和长尾预算一次性冻结。'
        '旧 USC-E 缺少合法模板，未凑成第8模板。开发四格缺“轨迹不同/任务等价”的实车覆盖。'
        '7个模板均复用Town03的controlled route24206（约133米）及其受控局部任务布局，'
        '不等于7条独立道路；模板聚类区间也不能提供跨地图不确定性。'
        '本轮是同开发模板的新种子评价，不是未见模板泛化。', '',
        coverage_block, '',
        '正式 freeze revision %s，bundle `%s`。rev1 在任何正式暴露前仅补接已声明的09:15新配对预算门；'
        '旧版完整保留。正式策略、阈值、样本、成功判据和原生终止规则随后未按结果调整。'
        '余项入口为 formal/FORMAL_QUEUE_MANIFEST.json 与 delivery 的 REMAINING_AND_INCOMPLETE_PAIRS.json；'
        '终止后不自动重启。' % (freeze['freeze_revision'], freeze['source_bundle_sha256']), '',
        'B 为何没有伪造对照：当前 RQ3 在证据充分且可问时即 ASK，没有读取可测试的非 oracle 未来证据收益/到达时间估计。'
        '论文公式还未确定估计器、参数或校准方式；旧固定到达时间声明与其他lease/回答等待不能替代该功能。'
        '禁用一个不存在的lookahead会重复同一策略。因此B为 `STRUCTURALLY_UNIDENTIFIABLE_CURRENT_FULL_HAS_NO_LOOKAHEAD`；'
        '定义映射、决策表和最小复现见 active_wait/IDENTIFIABILITY_REPORT.md。', '',
        '历史证据核验：', '',
        '| 对照 | 能复核的原始结果 | 实际证据类型 |',
        '|---|---|---|',
        '| RQ1 后果感知 vs 发现歧义即问 | 48 native rows，46有效决策；critical ASK决策22/22 vs22/22，等价0/24 vs24/24 | 只有完整方法驾驶；基线在同episode上构造规则评分，不能将规则ASK计数等同两臂真实发问 |',
        '| RQ2 B1 vs B2 | 48 native、47 eligible；18 ASYNC 中充分0 vs12，可行动窗口0 vs6 | 同源轨迹的离线证据视图，不是两臂分别驾驶 |',
        '| RQ2 time-only/evidence-only/joint | 47个冻结规则输出可重现；time premature41，evidence late17，joint premature/late0且missed17 | 同源轨迹反事实规则评分，没有三策略各自发问/驾驶 |', '',
        '完整源文件、生成脚本、样本ID、配对、配置/版本、缺失原因见 HISTORICAL_ABLATION_EVIDENCE.md 与前阶段核验清单。'
        '旧结果与冻结 reducer 未覆盖；离线重算没有增加独立 episode 数。旧RQ1部分任务端点依赖被测关系，'
        '本轮采用已有独立物理停车区域 reducer，未沿用循环定义。', '',
        '工程与完整性：113项共同CPU检查、3项队列保护检查通过；独立固定快照复核状态 `%s`，'
        '%s项检查、%s项失败。完整 ASK 链计数 `%s`；公共路由提交计数 `%s`。公共 commit 中包括无回答 ACT，'
        '不能把全部 fresh_replan_count 称为回答后重规划。详 protocol_statistics 最终原始证据复核。' %
        (audit['status'], audit['check_count'], audit['failed_check_count'],
         json.dumps(ask_chains, ensure_ascii=False), json.dumps(commits, ensure_ascii=False)), '',
        '本次是不是两个版本分别真实驾驶？**A是**：每个版本重新启动CARLA/evaluator，分别留下传感器、'
        '原生模型和实际车辆控制轨迹，没有强迫共用录制轨迹。B没有运行，不能作同样声称。', '',
        '是不是只改变任务关系判断？**两臂之间的研究差异是该判断器及其正常下游关系输出**；'
        '共同记忆、时机、回答、执行后端和安全检查保持一致。相对进入本轮时的旧完整版本，两个臂共同增加候选推理与记录接线，'
        '所以不是“旧整个系统一个字未改”的复跑。Full判断器行为回归通过，不声称原生车辆逐帧确定性。', '',
        '是否存在真值泄漏或公共逻辑抵消？已检查的运行配置、数据依赖、UNKNOWN与ASK下游没有发现关系答案旁路；'
        'DEV以及正式记录中的实际决策说明替换会改变询问，不是只改名称。原生控制重复运行在决策前仍有观测噪声；'
        '天气仅有共同配置证据，隐藏RNG状态未逐帧测量，不能把所有轨迹差异都归因于消融。', '',
        descriptive_sequence_block(audit), '',
        '是否剔除、替换或重复运行失败？正式episode回执计数：启动基础设施重试%s、科学重试%s、种子替换%s；'
        '全部计划行与原始目录在 FAILURE_AND_ATTEMPT_AUDIT.json。开发唯一新增尝试源于pre-agent模块命名遮蔽stdlib，'
        '原21秒失败和修复版本均保留。驾驶失败行全部保留，回执计数仅代表已观测字段；'
        '缺失回执字段见missing_receipt_counter_fields，不可由缺失推断零重试。' %
        (attempts['infrastructure_retries'], attempts['scientific_retry'], attempts['formal_seed_replacement']), '',
        infrastructure_maintenance_block(maintenance), '',
        '资源与可视化：保留本地物理DISPLAY:1、单CARLA及只读面板，未使用headless/offscreen/Xvfb/VNC。'
        '15秒资源采样峰值不是瞬时峰值，GPU包含桌面，RSS不是PSS；详 RESOURCE_REPORT.md 和 resource_usage.csv。'
        '统一日志回放图与逐实例索引属于交付检查项，完成数量和存在性由最终检查索引确认；'
        '本叙述生成器不据计划数宣称图已全部生成。日志回放不冒充RGB录像，不额外调用模型/PID/planner/control。', '',
        '另保留一次USC-C FULL物理窗口维护抽样：native窗口为高空视角，无法证明自车像素与日志精确同帧；'
        '发现与panel重叠后仅平移当前窗口到桌面允许的x72/y87，保持1280×720、焦点、renderer和冻结配置。'
        '原截图、位置/PID/时点及限制完整保留visualization/midrun_physical_display_v1及相邻维护回执。'
        '这不是预注册性能案例选择，也不替代全部配对的统一日志回放交付检查。', '',
        '哪些可以写入论文：本轮A可以作为明确限定实现、模板和新种子的独立闭环对照，完整报告负结果、'
        '询问变化和任务/安全终点。历史C/D及RQ1规则基线只能按其真实同轨迹证据类型表述。DEV阈值校准、'
        '工程probe、脚本机会窗口和未实现的B不能写成未见正式验证或主动等待收益。', '',
        '标准 Bench2Drive 保留203个已完成单臂（A0=101，A1=102）、101个完整配对和237个未运行单臂；'
        '3904/A1自然结束后暂停，3904/A0同种子待运行，未中断episode、未删除已完成单臂。'
        '恢复命令、原冻结合同、checkpoint与未索引末单臂处理见 BENCH2DRIVE_PAUSE_AND_RESUME.md。'
        '本轮结束后不恢复标准评测、不训练、不扩大样本。', '',
        '交付入口：根目录的CSV/表格、KEY_EVENT_TIMELINES.json、RAW_LOG_INDEX.json、FINAL_REPORT.md；'
        '原始日志位于 formal/native，原冻结矩阵/摘要位于 formal，版本与修复位于 versions/development，'
        '退出Git、冻结文件与进程清理属于后续最终检查项，完成后由 exit 和 FINAL_VALIDATION_RECEIPT.json '
        '建立索引；本报告不预先宣称回执已生成或进程已清理。']
    args.output.write_text('\n'.join(lines) + '\n')
    print(str(args.output.resolve()))


if __name__ == '__main__':
    main()
