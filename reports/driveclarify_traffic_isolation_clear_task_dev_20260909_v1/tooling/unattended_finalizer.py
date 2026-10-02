#!/usr/bin/env python3
"""等待既有队列终止后离线归约、报告并定向收尾；绝不派发驾驶。"""
import argparse
import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback

ROOT = Path('/home/buaa/wrh/DriveClarify')
REPORT = Path(__file__).resolve().parents[1]
PYTHON = '/home/buaa/anaconda3/envs/simlingo/bin/python'
QUEUE_UNIT = 'driveclarify-clear-task-dev-v1-queue.service'
RESOURCE_UNIT = 'driveclarify-clear-task-dev-v1-resource.service'
PANEL_UNIT = 'driveclarify-clear-task-dev-v2-panel.service'
FINAL = REPORT / 'finalization'
FINAL_STATE = REPORT / 'UNATTENDED_FINALIZER_STATE.json'
LOCK = REPORT / 'queue/UNATTENDED_FINALIZER.lock'


def now():
    return datetime.datetime.now().astimezone().isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def service_active(unit):
    result = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', unit],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def queue_snapshot():
    state = read_json(REPORT / 'STATE.json', {})
    history = read_json(REPORT / 'queue/DISPATCH_HISTORY.json', [])
    return {
        'queue_service_active': service_active(QUEUE_UNIT),
        'queue_status': state.get('status'),
        'current_run': state.get('current_run'),
        'started_count': len(history),
        'terminated_count': sum('finished_local' in row for row in history),
    }


def check_contract():
    manifest = read_json(REPORT / 'DEV_MANIFEST.json')
    state = read_json(REPORT / 'STATE.json')
    if not manifest or not state:
        raise RuntimeError('MANIFEST_OR_STATE_UNREADABLE')
    runs = manifest.get('runs', [])
    if len(runs) != 8 or len({row['run_id'] for row in runs}) != 8:
        raise RuntimeError('MANIFEST_NOT_EXACTLY_EIGHT_UNIQUE_RUNS')
    if manifest.get('planned_runs') != 8:
        raise RuntimeError('PLANNED_RUN_COUNT_CHANGED')
    required = ['summarize_clear_tasks.py', 'analyze_actors.py', 'summarize_operations.py']
    missing = [name for name in required if not (REPORT / 'tooling' / name).is_file()]
    if missing:
        raise RuntimeError('FINALIZER_TOOL_MISSING:' + ','.join(missing))
    if state.get('report_root') != str(REPORT):
        raise RuntimeError('STATE_REPORT_ROOT_MISMATCH')
    return {'manifest_runs': len(runs), 'current_run': state.get('current_run'),
            'queue_active': service_active(QUEUE_UNIT),
            'stop_dispatch_local': manifest.get('stop_dispatch_local'),
            'deadline_local': manifest.get('deadline_local')}


def run_output(name, command, target):
    target = Path(target)
    if target.exists():
        return {'name': name, 'status': 'EXISTING_OUTPUT_RETAINED', 'path': str(target)}
    build = FINAL / ('.' + name + '.build-' + str(os.getpid()))
    log = FINAL / (name + '.log')
    started = now()
    with log.open('xb') as stream:
        result = subprocess.run(command + [str(build)], stdout=stream,
                                stderr=subprocess.STDOUT, timeout=1800)
    row = {'name': name, 'started_local': started, 'finished_local': now(),
           'exit_code': result.returncode, 'log': str(log), 'path': str(target)}
    if result.returncode == 0 and build.is_dir():
        build.replace(target)
        row['status'] = 'GENERATED_POST_HOC_FROM_RETAINED_RAW_RECORDS'
    else:
        row['status'] = 'FAILED_RETAINED_FOR_REVIEW'
        row['partial_build'] = str(build) if build.exists() else None
    return row


def port_open(port):
    with contextlib.closing(socket.socket()) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def stop_task_services():
    (REPORT / 'STOP_RESOURCE_MONITOR').touch(exist_ok=True)
    rows = []
    for unit in (RESOURCE_UNIT, PANEL_UNIT):
        before = service_active(unit)
        result = subprocess.run(['systemctl', '--user', 'stop', unit],
                                capture_output=True, text=True, timeout=30)
        rows.append({'unit': unit, 'active_before': before,
                     'stop_exit_code': result.returncode,
                     'active_after': service_active(unit),
                     'stderr': result.stderr.strip() or None})
    time.sleep(2)
    ports = {str(port): port_open(port) for port in (28100, 28101, 28202)}
    return {'services': rows, 'task_ports_open_after_queue_exit': ports,
            'cleanup_pass': not any(ports.values()) and
                            all(not row['active_after'] for row in rows)}


def classify(summary):
    rows = summary.get('results', []) if summary else []
    counts = {'completed': 0, 'running': 0, 'not_started': 0, 'interrupted': 0}
    for row in rows:
        status = row.get('status')
        if status == 'COMPLETED':
            counts['completed'] += 1
        elif status == 'RUNNING_PARTIAL':
            counts['running'] += 1
        elif status == 'PLANNED_NOT_STARTED':
            counts['not_started'] += 1
        else:
            counts['interrupted'] += 1
    return counts


def write_text_atomic(path, text):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text)
    temporary.replace(path)


def update_project_state(status, counts, outputs, cleanup, remaining):
    state = read_json(REPORT / 'STATE.json', {})
    state.update(status=status, current_run=None, current_launcher_pid=None,
                 unattended_finalized_local=now(), final_counts=counts,
                 remaining_run_ids=remaining, automated_outputs=outputs,
                 final_cleanup=cleanup)
    atomic_json(REPORT / 'STATE.json', state)
    global_state = read_json(ROOT / 'STATE.json', {})
    global_state.update(status=status, current_task_status=status,
                        state_updated_at=state['unattended_finalized_local'])
    global_state['driveclarify_traffic_isolation_clear_task_dev_20260909_v1'] = state
    atomic_json(ROOT / 'STATE.json', global_state)


def report_text(receipt):
    counts = receipt['counts']
    outputs = receipt['outputs']
    lines = [
        '# 背景交通隔离＋明确任务诊断：自动收尾报告', '',
        f"状态：`{receipt['status']}`", '',
        '本报告由主机独立 systemd 收尾器在既有唯一队列退出后生成。它没有派发、重启或重跑任何驾驶。',
        '所有分析文件均为“事后从保留原始记录重建”；它们不是当时的原生 observer 回执。',
        'observer 收尾日志缺失不触发驾驶重跑；可由官方终点和完整物理轨迹计算的字段按原冻结判据离线归约，不能重建的字段保持缺失。', '',
        '## 终点统计', '',
        f"- 完整终点：{counts['completed']}",
        f"- 仍在运行：{counts['running']}",
        f"- 未启动：{counts['not_started']}",
        f"- 技术中断/不完整终点：{counts['interrupted']}",
        f"- 已派发：{receipt['dispatch']['started_count']} / {receipt['dispatch']['planned_count']}", '',
        '驾驶碰撞、停滞和 AgentBlocked 均保留为结果，不当工程失败，也不自动重跑。',
        '本阶段不是 FULL/TRAJ 性能消融；与旧歧义实验的差异不能全部归因于关闭背景交通。', '',
        '## 自动输出', ''
    ]
    for name, path in outputs.items():
        lines.append(f'- {name}：`{path}`')
    lines += ['', '## 自动清理', '',
              f"- 本任务定向清理通过：{receipt['cleanup']['cleanup_pass']}",
              f"- 端口状态：`{json.dumps(receipt['cleanup']['task_ports_open_after_queue_exit'], ensure_ascii=False)}`",
              '- Bench2Drive 未恢复；未启动训练、新消融或第二个 CARLA。', '',
              '## 边界', '',
              f"- 停止新派发：`{receipt['budget']['stop_dispatch_local']}`",
              f"- 阶段截止：`{receipt['budget']['deadline_local']}`",
              '- 原 native AgentBlocked 180 仿真秒、RouteTimeout、RPC 480 秒和工程墙钟 7200 秒未改变。', '']
    if receipt['remaining_run_ids']:
        lines += ['## 未运行清单', '', *[f'- `{run_id}`' for run_id in receipt['remaining_run_ids']], '']
    return '\n'.join(lines)


def finalize():
    FINAL.mkdir(exist_ok=True)
    manifest = read_json(REPORT / 'DEV_MANIFEST.json', {})
    cleanup = stop_task_services()
    outputs = {}
    jobs = []
    jobs.append(run_output('clear_task_results',
        [PYTHON, str(REPORT/'tooling/summarize_clear_tasks.py'),
         '--manifest', str(REPORT/'DEV_MANIFEST.json'), '--require-no-running', '--output'],
        FINAL/'clear_task_results'))
    jobs.append(run_output('traffic_audit',
        [PYTHON, str(REPORT/'tooling/analyze_actors.py'),
         '--manifest', str(REPORT/'DEV_MANIFEST.json'), '--output'],
        FINAL/'traffic_audit'))
    jobs.append(run_output('resource_summary',
        [sys.executable, str(REPORT/'tooling/summarize_operations.py'),
         '--report-root', str(REPORT), '--output'],
        FINAL/'resource_summary'))
    summary = read_json(FINAL/'clear_task_results/SUMMARY.json', {})
    counts = classify(summary)
    history = read_json(REPORT/'queue/DISPATCH_HISTORY.json', [])
    started_ids = {row.get('run_id') for row in history}
    remaining = [row['run_id'] for row in manifest.get('runs', [])
                 if row['run_id'] not in started_ids]
    outputs.update({
        '结果表': str(FINAL/'clear_task_results/CLEAR_TASK_RESULTS.csv'),
        '机器可读结果': str(FINAL/'clear_task_results/SUMMARY.json'),
        '交通源审计': str(FINAL/'traffic_audit/CURRENT_TRAFFIC_QA_SUMMARY.json'),
        '资源汇总': str(FINAL/'resource_summary/RESOURCE_SUMMARY.json'),
        '最终报告': str(REPORT/'FINAL_REPORT.md'),
        '最终回执': str(REPORT/'FINAL_RECEIPT.json'),
        '自动交接': str(REPORT/'AUTOMATED_HANDOFF.json'),
    })
    job_ok = all(row.get('status') in {
        'GENERATED_POST_HOC_FROM_RETAINED_RAW_RECORDS', 'EXISTING_OUTPUT_RETAINED'}
                 for row in jobs)
    no_running = counts['running'] == 0
    status = ('DEV_CLEAR_TASK_UNATTENDED_FINALIZED' if job_ok and no_running and cleanup['cleanup_pass']
              else 'DEV_CLEAR_TASK_UNATTENDED_FINALIZATION_REVIEW_REQUIRED')
    receipt = {
        'schema': 'driveclarify.clear-task-unattended-finalization.v1',
        'status': status, 'generated_local': now(),
        'provenance': 'POST_HOC_FROM_RETAINED_RAW_RECORDS_NOT_NATIVE_OBSERVER_RECEIPT',
        'queue_unit': QUEUE_UNIT,
        'finalizer_unit': 'driveclarify-clear-task-dev-v1-finalizer.service',
        'dispatch': {'planned_count': len(manifest.get('runs', [])),
                     'started_count': len(history),
                     'terminated_count': sum('finished_local' in row for row in history)},
        'counts': counts, 'remaining_run_ids': remaining,
        'budget': {key: manifest.get(key) for key in ('stop_dispatch_local', 'deadline_local')},
        'jobs': jobs, 'cleanup': cleanup, 'outputs': outputs,
        'no_driving_dispatched_by_finalizer': True,
        'no_exposed_episode_retry': True,
        'bench2drive_resumed': False,
    }
    atomic_json(REPORT/'FINAL_RECEIPT.json', receipt)
    write_text_atomic(REPORT/'FINAL_REPORT.md', report_text(receipt))
    atomic_json(REPORT/'AUTOMATED_HANDOFF.json', receipt)
    update_project_state(status, counts, outputs, cleanup, remaining)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='只做CPU契约检查，不等待或写阶段状态')
    parser.add_argument('--wait-seconds', type=float, default=5.0)
    args = parser.parse_args()
    contract = check_contract()
    if args.check:
        print(json.dumps({'status': 'PASS_CPU_CONTRACT_CHECK', **contract},
                         ensure_ascii=False, sort_keys=True))
        return 0
    LOCK.parent.mkdir(exist_ok=True)
    with LOCK.open('a+') as lock_stream:
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('ANOTHER_UNATTENDED_FINALIZER_HOLDS_LOCK')
        lock_stream.seek(0)
        lock_stream.truncate()
        lock_stream.write(json.dumps({'pid': os.getpid(), 'started_local': now(),
                                      'queue_unit': QUEUE_UNIT}) + '\n')
        lock_stream.flush()
        state = {'schema': 'driveclarify.clear-task-unattended-finalizer-state.v1',
                 'status': 'WAITING_FOR_EXISTING_QUEUE_TO_EXIT',
                 'pid': os.getpid(), 'started_local': now(),
                 'queue_unit': QUEUE_UNIT, 'contract': contract,
                 'polling_owner': 'INDEPENDENT_HOST_SYSTEMD_SERVICE_NOT_AGENT',
                 'driving_dispatch_capability': False,
                 'last_queue_snapshot': queue_snapshot()}
        atomic_json(FINAL_STATE, state)
        while service_active(QUEUE_UNIT):
            time.sleep(max(args.wait_seconds, 1.0))
            snapshot = queue_snapshot()
            if snapshot != state['last_queue_snapshot']:
                state['last_queue_snapshot'] = snapshot
                atomic_json(FINAL_STATE, state)
        state.update(status='QUEUE_EXITED_FINALIZATION_RUNNING', queue_exited_local=now(),
                     last_queue_snapshot=queue_snapshot())
        atomic_json(FINAL_STATE, state)
        receipt = finalize()
        state.update(status=receipt['status'], finished_local=now(),
                     final_receipt=str(REPORT/'FINAL_RECEIPT.json'),
                     final_report=str(REPORT/'FINAL_REPORT.md'),
                     counts=receipt['counts'])
        atomic_json(FINAL_STATE, state)
        return 0 if receipt['status'] == 'DEV_CLEAR_TASK_UNATTENDED_FINALIZED' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        failure = {'schema': 'driveclarify.clear-task-unattended-finalizer-state.v1',
                   'status': 'UNATTENDED_FINALIZER_FAILED_REVIEW_REQUIRED',
                   'failed_local': now(), 'exception_type': type(exc).__name__,
                   'exception': str(exc), 'traceback': traceback.format_exc(),
                   'queue_snapshot': queue_snapshot(),
                   'no_automatic_restart_requested': True,
                   'no_driving_dispatched_by_finalizer': True}
        try:
            atomic_json(FINAL_STATE, failure)
        finally:
            raise
