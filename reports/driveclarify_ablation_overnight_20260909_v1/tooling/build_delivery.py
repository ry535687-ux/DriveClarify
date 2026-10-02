"""Read-only evidence packaging; never changes frozen scoring or native records.

Run only after selecting an explicit immutable analysis snapshot. This script
does not dispatch, simulate, infer, score, or filter episodes by outcome.
"""
import argparse
import csv
import datetime
import hashlib
import json
import math
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def write_csv(path, rows, fields):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_snapshot(root, snapshot):
    """Validate evidence identity before creating any delivery output."""
    root, snapshot = Path(root).resolve(), Path(snapshot).resolve()
    source_files = {}

    def record(path, expected=None):
        digest = sha(path)
        if expected is not None and digest != expected:
            raise SystemExit('Evidence digest mismatch: ' + str(path))
        source_files[str(path.resolve())] = digest
        return digest

    queue_path = root / 'formal/queue/QUEUE_STATE.json'
    queue = read_json(queue_path)
    if queue.get('status') == 'ACTIVE':
        raise SystemExit('Final evidence packaging requires the formal queue to stop first')
    latest_path = root / 'LATEST_STATISTICS.json'
    latest = read_json(latest_path)
    if Path(latest['snapshot']).resolve() != snapshot:
        raise SystemExit('Snapshot must equal LATEST_STATISTICS.snapshot')
    record(queue_path)
    record(latest_path)
    manifest_path = root / 'formal/FORMAL_QUEUE_MANIFEST.json'
    manifest = read_json(manifest_path)
    normalization_path = snapshot / 'normalized/NORMALIZATION_RECEIPT.json'
    normalization = read_json(normalization_path)
    if Path(normalization['source_manifest']).resolve() != manifest_path.resolve():
        raise SystemExit('Normalization references a different formal manifest')
    manifest_digest = record(manifest_path, normalization['source_manifest_sha256'])
    if queue.get('manifest_sha256') != manifest_digest:
        raise SystemExit('Queue and normalization manifest digests differ')
    record(normalization_path)
    normalized_path = snapshot / 'normalized/NORMALIZED_RESULTS.jsonl'
    statistics_manifest = snapshot / 'normalized/STATISTICS_MANIFEST.json'
    analysis_path = snapshot / 'statistics/ANALYSIS_PROVENANCE.json'
    analysis = read_json(analysis_path)
    expected_sources = {str(Path(item['path']).resolve()): item['sha256']
                        for item in analysis['sources']}
    for path in (normalized_path, statistics_manifest):
        expected = expected_sources.get(str(path.resolve()))
        if expected is None:
            raise SystemExit('Statistics provenance lacks required input: ' + str(path))
        record(path, expected)
    if read_json(statistics_manifest)['runs'] != manifest['runs']:
        raise SystemExit('Statistics and formal manifest rows differ')
    for name in ('episode_results.csv', 'paired_results.csv', 'summary_tables.md',
                 'paper_table.csv', 'summary.json', 'ANALYSIS_PROVENANCE.json'):
        record(snapshot / 'statistics' / name)
    if latest.get('summary') != read_json(snapshot / 'statistics/summary.json'):
        raise SystemExit('LATEST_STATISTICS summary differs from snapshot summary')
    rows = [json.loads(line) for line in normalized_path.read_text().splitlines()
            if line.strip()]
    planned_ids = [p['run_id'] for p in manifest['runs']]
    row_ids = [row['run_id'] for row in rows]
    if (len(set(planned_ids)) != len(planned_ids) or
            len(set(row_ids)) != len(row_ids) or set(row_ids) != set(planned_ids)):
        raise SystemExit('Manifest and normalized rows must contain every planned run exactly once')
    for planned in manifest['runs']:
        record(Path(planned['config_path']), planned['config_sha256'])
        raw_path = snapshot / 'normalized' / (planned['run_id'] + '_RAW_LOG_INDEX.json')
        raw_index = read_json(raw_path)
        if raw_index.get('run_id') != planned['run_id']:
            raise SystemExit('Raw index run identity mismatch: ' + str(raw_path))
        record(raw_path)
        receipt_path = Path(planned['output']) / 'process_job/PROCESS_RECEIPT.json'
        indexed = [item for item in raw_index['files']
                   if Path(item['path']).resolve() == receipt_path.resolve()]
        if bool(indexed) != receipt_path.exists() or len(indexed) > 1:
            raise SystemExit('Process receipt presence differs from snapshot: ' + str(receipt_path))
        if indexed:
            if indexed[0].get('stable_size_during_read') is not True:
                raise SystemExit('Snapshot process receipt was not read stably: ' + str(receipt_path))
            record(receipt_path, indexed[0]['sha256'])
    return manifest, queue, rows, source_files


def infrastructure_maintenance_index(root, manifest, rows):
    """Index retained crash/maintenance receipts without rescoring their episodes."""
    root = Path(root).resolve()
    planned = {p['run_id']: p for p in manifest['runs']}
    normalized = {row['run_id']: row for row in rows}
    incidents, source_files = [], {}
    for capture_path in sorted((root / 'infrastructure').glob('*/CRASH_CAPTURE_RECEIPT.json')):
        directory = capture_path.parent
        capture = read_json(capture_path)
        run_id = capture['run_id']
        if run_id not in planned or run_id not in normalized:
            raise SystemExit('Infrastructure receipt references an unplanned run: ' + run_id)
        row = normalized[run_id]
        records, files = {}, []
        for name in ('CRASH_CAPTURE_RECEIPT.json', 'ORPHAN_EVALUATOR_CLEANUP_INTENT.json',
                     'ORPHAN_EVALUATOR_SIGNAL_SENT.json', 'PROCESS_RECEIPT_AFTER_CLEANUP.json',
                     'QUEUE_STATE_STOPPED_BEFORE_RESUME.json', 'QUEUE_RESUMPTION_RECEIPT.json',
                     'RESUME_PRECONDITIONS_AND_ARCHIVE.json', 'Diagnostics.txt',
                     'CrashContext.runtime-xml', 'carla_server_start_01.log',
                     'evaluator_before_orphan_cleanup.log', 'evaluator_after_cleanup.log'):
            path = directory / name
            if path.exists():
                digest = sha(path)
                source_files[str(path.resolve())] = digest
                files.append({'path': str(path.resolve()), 'sha256': digest})
                if path.suffix == '.json':
                    records[name] = read_json(path)
        receipt = records.get('PROCESS_RECEIPT_AFTER_CLEANUP.json', {})
        if receipt:
            current_receipt = Path(planned[run_id]['output']) / 'process_job/PROCESS_RECEIPT.json'
            if sha(current_receipt) != sha(directory / 'PROCESS_RECEIPT_AFTER_CLEANUP.json'):
                raise SystemExit('Archived cleanup receipt differs from retained native receipt: ' + run_id)
        resume = records.get('QUEUE_RESUMPTION_RECEIPT.json')
        if resume is not None:
            if resume.get('same_manifest_sha256') != sha(root / 'formal/FORMAL_QUEUE_MANIFEST.json'):
                raise SystemExit('Queue resumption used a different formal manifest')
            archive = Path(resume['archived_stopped_queue'])
            if sha(archive) != resume['archived_stopped_queue_sha256']:
                raise SystemExit('Archived pre-resumption dispatch history digest mismatch')
            source_files[str(archive.resolve())] = sha(archive)
        incidents.append({
            'run_id': run_id, 'pair_id': planned[run_id]['pair_id'],
            'schedule_position': planned[run_id]['schedule_position'],
            'evidence_directory': str(directory), 'evidence_files': files,
            'classification_from_capture': capture.get('classification'),
            'status_from_normalized': row['status'],
            'terminal_complete': row.get('terminal_complete'),
            'technical_interruption': row.get('technical_interruption'),
            'agent_exposed': row.get('agent_exposed'),
            'observed_partial_exposure': {key: row.get(key) for key in (
                'actual_control_count', 'native_model_forward_count', 'candidate_forward_count')},
            'last_observed_control': capture.get('last_actual_control'),
            'scientific_endpoints_from_normalized': {key: row.get(key) for key in (
                'language_task_complete', 'wrong_target_execution', 'collision',
                'offroad_or_wrong_lane', 'traffic_violation', 'timeout', 'nonprogress')},
            'runtime_wall_s': row.get('runtime_wall_s'),
            'runtime_sim_s': row.get('runtime_sim_s'),
            'wall_clock_basis': 'PROCESS_START_THROUGH_POST_CRASH_ORPHAN_CLEANUP_NOT_NATURAL_EPISODE_DURATION',
            'cleanup_process_receipt': receipt or None,
            'cleanup_intent': records.get('ORPHAN_EVALUATOR_CLEANUP_INTENT.json'),
            'cleanup_signal': records.get('ORPHAN_EVALUATOR_SIGNAL_SENT.json'),
            'queue_resumption_receipt': resume,
            'pair_retained_incomplete_for_primary_endpoint': not row.get('terminal_complete'),
            'crash_cause_limit': capture.get('causal_limit'),
        })
    return ({'schema': 'DRIVECLARIFY_INFRASTRUCTURE_MAINTENANCE_INDEX_V1',
             'incidents': incidents,
             'queue_resumption_event_count': sum(i['queue_resumption_receipt'] is not None for i in incidents),
             'queue_resumption_count_basis': 'DISTINCT_RETAINED_QUEUE_RESUMPTION_RECEIPTS_NOT_EPISODE_RETRIES',
             'planned_runs_unchanged': len(manifest['runs']),
             'planned_pairs_unchanged': len({p['pair_id'] for p in manifest['runs']}),
             'scientific_endpoint_reduction_changed': False}, source_files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-root', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, snapshot, out = (p.resolve() for p in
                           (args.report_root, args.snapshot, args.output))
    if out.exists():
        raise SystemExit('Refusing to overwrite an existing delivery directory')
    manifest, queue_state, rows, source_files = validate_snapshot(root, snapshot)
    maintenance, maintenance_sources = infrastructure_maintenance_index(root, manifest, rows)
    source_files.update(maintenance_sources)
    maintenance_by_run = {item['run_id']: item for item in maintenance['incidents']}
    by_run = {row['run_id']: row for row in rows}
    planned_ids = [p['run_id'] for p in manifest['runs']]
    provenance = {'created_local': datetime.datetime.now().astimezone().isoformat(),
                  'snapshot': str(snapshot), 'source_files': source_files,
                  'scoring_changed': False, 'native_runs_launched': 0,
                  'selection_rule': 'ALL_FROZEN_MANIFEST_ROWS_IN_SCHEDULE_ORDER'}
    out.mkdir(parents=True)
    for name in ('episode_results.csv', 'paired_results.csv', 'summary_tables.md',
                 'paper_table.csv', 'summary.json', 'ANALYSIS_PROVENANCE.json'):
        path = snapshot / 'statistics' / name
        shutil.copyfile(path, out / name)
        provenance['source_files'][str(path)] = sha(path)
    run_index, event_index, resources, failures, coverage = [], [], [], [], []
    receipt_totals = {'server_start_attempts': 0, 'infrastructure_retries': 0,
                      'preworld_startup_failures': 0, 'scientific_retry': 0,
                      'formal_seed_replacement': 0, 'receipt_count': 0}
    missing_receipt_fields = []
    missing_process_receipts = []
    for planned in manifest['runs']:
        row = by_run[planned['run_id']]
        native = Path(planned['output'])
        receipt_path = native / 'process_job/PROCESS_RECEIPT.json'
        receipt = read_json(receipt_path) if receipt_path.exists() else {}
        if not receipt and (row.get('started') or row.get('agent_exposed')):
            missing_process_receipts.append(planned['run_id'])
        if receipt:
            receipt_totals['receipt_count'] += 1
            for key in receipt_totals:
                if key != 'receipt_count':
                    if receipt.get(key) is None:
                        missing_receipt_fields.append({'run_id': planned['run_id'], 'field': key})
                    else:
                        receipt_totals[key] += int(receipt[key])
        raw_index = snapshot / 'normalized' / (planned['run_id'] + '_RAW_LOG_INDEX.json')
        item = {key: planned[key] for key in ('run_id', 'pair_id', 'condition_id',
                    'configuration_id', 'seed', 'schedule_position', 'order_in_pair')}
        item.update(status=row['status'], started=row.get('started'),
                    agent_exposed=row.get('agent_exposed'),
                    terminal_complete=row.get('terminal_complete'),
                    native_directory=str(native), raw_log_index=str(raw_index),
                    raw_log_index_sha256=sha(raw_index),
                    process_receipt=str(receipt_path) if receipt else None,
                    process_receipt_sha256=sha(receipt_path) if receipt else None)
        run_index.append(item)
        events = {key: row.get(key) for key in (
            'run_id', 'status', 'episode_clock_origin_simulation_s',
            'relation_predictions', 'question_requests', 'actual_question_receipts',
            'actual_ask_simulation_s', 'actual_answer_received_simulation_s',
            'first_evidence_sufficient_sim_s', 'ask_remaining_margin_sim_s',
            'answer_remaining_margin_sim_s', 'postask_answer_wait_sim_s',
            'preask_active_wait_sim_s',
            'ask_requested_count', 'ask_emitted_count', 'answer_released_count',
            'answer_received_count', 'answer_binding_valid', 'fresh_replan_count',
            'actual_control_count', 'native_model_forward_count',
            'candidate_forward_count', 'diagnostic_forward_count',
            'task_outcome', 'nominal_window_definition', 'missing_reasons')}
        events['timeline_sources'] = {name: str(native / 'owner_evidence' / name)
            for name in ('ABL_DECISION_TIMELINE.jsonl',
                         'RQ3_TEMPORAL_DECISION_TIMELINE.jsonl',
                         'V11_SUPERVISION_TIMELINE.jsonl',
                         'V11_RUNTIME_TIMELINE.jsonl',
                         'ABL_ACTUAL_CONTROL_TIMELINE.jsonl',
                         'ABL_CANDIDATE_TIMELINE.jsonl',
                         'V2_NATIVE_STATE_TRACE.jsonl')
            if (native / 'owner_evidence' / name).exists()}
        events['clock_basis'] = {
            'actual_ask_and_answer_and_relation_prediction_times': 'ABSOLUTE_SIMULATION_SECONDS',
            'first_evidence_sufficient_sim_s': 'SECONDS_SINCE_EPISODE_CLOCK_ORIGIN',
            'wait_fields': 'SIMULATION_DURATION_SECONDS_OR_NULL',
            'remaining_margin_fields': 'NOMINAL_SCRIPT_WINDOW_DIAGNOSTIC_ONLY'}
        event_index.append(events)
        decisions = row.get('relation_predictions') or []
        first = decisions[0] if decisions else {}
        metric = first.get('trajectory_metric_m')
        threshold = read_json(Path(planned['config_path']))['ablation']['metric']['threshold_m']
        short_condition = 'UNKNOWN'
        if (isinstance(metric, (int, float)) and not isinstance(metric, bool) and math.isfinite(metric)
                and first.get('trajectory_metric_reason') == 'SHORT_TRAJECTORY_THRESHOLD'):
            short_condition = 'DIFFERENT' if metric > threshold else 'CLOSE'
        coverage.append({'run_id': planned['run_id'], 'pair_id': planned['pair_id'],
            'arm': planned['variant'], 'status': row['status'],
            'physical_relation_truth': row.get('physical_relation_truth'),
            'first_decision_absolute_simulation_s': first.get('simulation_time_s'),
            'episode_clock_origin_simulation_s': row.get('episode_clock_origin_simulation_s'),
            'short_trajectory_condition': short_condition,
            'max_aligned_distance_m': metric, 'frozen_threshold_m': threshold,
            'interpretation': 'POST_FREEZE_DESCRIPTIVE_FIRST_DECISION_MEASUREMENT_NOT_SCENE_SELECTION'})
        resource = {key: row.get(key) for key in ('run_id', 'status', 'runtime_wall_s',
            'runtime_sim_s', 'peak_gpu_memory_mib', 'peak_rss_mib', 'artifact_bytes',
            'native_model_forward_count', 'candidate_forward_count',
            'diagnostic_forward_count', 'actual_control_count')}
        incident = maintenance_by_run.get(planned['run_id'])
        resource['wall_clock_basis'] = (incident['wall_clock_basis'] if incident else
            'PROCESS_WALL_INCLUDING_STARTUP_EXECUTION_AND_CLEANUP_NOT_SIMULATION_DURATION')
        resource['maintenance_evidence_directory'] = incident['evidence_directory'] if incident else None
        resources.append(resource)
        failures.append({**item, 'driving_failure': row.get('driving_failure'),
            'technical_interruption': row.get('technical_interruption'),
            'termination_reason': row.get('termination_reason'),
            'collision': row.get('collision'), 'timeout': row.get('timeout'),
            'nonprogress': row.get('nonprogress'),
            'language_task_complete': row.get('language_task_complete'),
            'wrong_target_execution': row.get('wrong_target_execution'),
            'attempt_count': row.get('attempt_count'), 'process_receipt_values': receipt,
            'infrastructure_maintenance': incident,
            'retained_in_delivery': True})
    write_json(out / 'INFRASTRUCTURE_MAINTENANCE_INDEX.json', maintenance)
    provenance['source_files'][str(out / 'INFRASTRUCTURE_MAINTENANCE_INDEX.json')] = sha(
        out / 'INFRASTRUCTURE_MAINTENANCE_INDEX.json')
    write_json(out / 'RAW_LOG_INDEX.json', run_index)
    write_json(out / 'KEY_EVENT_TIMELINES.json', event_index)
    write_csv(out / 'resource_usage.csv', resources, list(resources[0]))
    write_csv(out / 'observed_condition_coverage.csv', coverage, list(coverage[0]))
    write_json(out / 'OBSERVED_CONDITION_COVERAGE.json', {
        'rows': coverage,
        'unit': 'EACH_INDEPENDENTLY_DRIVEN_ARM_FIRST_DECISION_RECORD_INCLUDING_UNKNOWN',
        'not_a_frozen_pair_level_geometric_condition': True,
        'note': 'Observed coverage only; arms can differ because their own observations differ. '
                'No new samples, filtering, threshold changes or strategy-result labels are used.'})
    write_json(out / 'FAILURE_AND_ATTEMPT_AUDIT.json',
               {'planned_rows': len(planned_ids), 'receipt_totals': receipt_totals,
                'missing_receipt_counter_fields': missing_receipt_fields,
                'started_run_ids_without_process_receipt': missing_process_receipts,
                'receipt_totals_basis': 'OBSERVED_RECEIPT_FIELDS_ONLY_MISSING_IS_NOT_ZERO',
                'queue_resumption_event_count': maintenance['queue_resumption_event_count'],
                'queue_resumption_is_episode_retry': False,
                'infrastructure_maintenance_index': str(out / 'INFRASTRUCTURE_MAINTENANCE_INDEX.json'),
                'all_rows': failures,
                'formal_native_directories_not_in_frozen_manifest': sorted(
                    p.name for p in (root / 'formal/native').iterdir()
                    if p.is_dir() and p.name not in set(planned_ids)),
                'development_attempt_history': str(root / 'development/ENGINEERING_REVISIONS.jsonl'),
                'note': 'Formal driving failures retained; DEV repair is a separate versioned record.'})
    pairs = {}
    for item in run_index:
        pairs.setdefault(item['pair_id'], []).append(item)
    remaining = [item for item in run_index if not item['terminal_complete']]
    write_json(out / 'REMAINING_AND_INCOMPLETE_PAIRS.json', {
        'planned_run_count': len(planned_ids), 'planned_pair_count': len(pairs),
        'not_complete_runs': remaining,
        'incomplete_pairs': {key: value for key, value in pairs.items()
                             if not all(r['terminal_complete'] for r in value)},
        'automatic_resume_authorized_after_final_handoff': False})
    (out / 'RESOURCE_REPORT.md').write_text(
        '# 资源记录口径\n\n'
        '逐实例数据见 resource_usage.csv，来自冻结 normalization 输出；'
        '每个 episode 的 process_job 保留资源样本与关联回执。\n\n'
        '连续采样周期为15秒，峰值是采样峰值，不能保证捕捉瞬时最大值。'
        'GPU 数字为整张主机 GPU，包含桌面显示；RSS 是属于当前 '
        'CARLA/evaluator 进程的 RSS 之和，不是独占物理内存/PSS。'
        'wall 时间含启动、执行及清理，simulation 时间与之分别报告。'
        'artifact_bytes 是归约时已有产物大小，不包含之后生成的报告图。\n\n'
        '发生CARLA崩溃的技术中断仍保留已暴露控制、模型调用和进程成本；'
        '其wall时间可能包含崩溃后的等待和孤儿evaluator清理，不能当作自然完整episode时长。'
        '逐实例维护口径、信号、恢复回执和原始证据见INFRASTRUCTURE_MAINTENANCE_INDEX.json。'
        '队列基础设施恢复与episode基础设施重试分别计数，不合并成重跑次数。\n\n'
        '两次候选 forward 是本轮两个比较臂共同的方法推理；'
        'diagnostic_forward_count 单列。显示、统计和资源采样没有调用模型、'
        'PID、route planner 或车辆控制。\n')
    write_json(out / 'DELIVERY_PROVENANCE.json', provenance)
    print(json.dumps({'delivery': str(out), 'planned_rows': len(planned_ids),
                      'complete_rows': sum(bool(r['terminal_complete']) for r in run_index),
                      'remaining_rows': len(remaining)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
