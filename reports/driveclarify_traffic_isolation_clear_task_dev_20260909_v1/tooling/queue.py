"""只执行本轮事前固定清单；逐单元保存状态，不重派已有目录。"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/home/buaa/wrh/DriveClarify')
REPORT = Path(__file__).resolve().parents[1]


def write(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def update(state):
    state['updated_local'] = datetime.datetime.now().astimezone().isoformat()
    write(REPORT / 'STATE.json', state)
    global_state = json.loads((ROOT / 'STATE.json').read_text())
    global_state['status'] = state['status']
    global_state['current_task_status'] = state['status']
    global_state['state_updated_at'] = state['updated_local']
    global_state['driveclarify_traffic_isolation_clear_task_dev_20260909_v1'] = state
    write(ROOT / 'STATE.json', global_state)


def main():
    manifest = json.loads((REPORT / 'DEV_MANIFEST.json').read_text())
    freeze = json.loads((REPORT / 'DEV_RUNTIME_FREEZE.json').read_text())
    state = json.loads((REPORT / 'STATE.json').read_text())
    if state.get('live_started', 0):
        raise RuntimeError('QUEUE_RESTART_REQUIRES_EXPLICIT_AUDITED_RESUME_NOT_AUTOMATIC')
    history = []
    for row in manifest['runs']:
        if time.time() >= datetime.datetime.fromisoformat(manifest['stop_dispatch_local']).timestamp():
            state.update(status='DEV_DISPATCH_STOPPED_BUDGET', current_run=None)
            break
        if shutil.disk_usage(REPORT).free < manifest['minimum_free_disk_bytes']:
            state.update(status='DEV_DISPATCH_STOPPED_DISK', current_run=None)
            break
        mismatch = [x['path'] for x in freeze['runtime_files'] if sha(x['path']) != x['sha256']]
        if mismatch:
            state.update(status='DEV_DISPATCH_STOPPED_RUNTIME_HASH_CHANGE', integrity_mismatches=mismatch)
            break
        if sha(row['config_path']) != row['config_sha256'] or Path(row['output']).exists():
            raise RuntimeError('CONFIG_CHANGED_OR_EXISTING_EXPOSURE_DIRECTORY:' + row['run_id'])
        state.update(status='DEV_CLEAR_TASK_RUNNING', current_run=row['run_id'],
                     current_condition=row['condition'], current_template=row['template'])
        update(state)
        command = [manifest['runner'], row['config_path'], row['route_path'], str(row['seed']),
                   str(manifest['rpc_port']), 'NONE', row['output'],
                   'DEV_CLEAR_TASK_TRAFFIC_DIAGNOSTIC_NO_POST_EXPOSURE_RETRY', row['condition']]
        started = datetime.datetime.now().astimezone().isoformat()
        log_path = REPORT / 'queue' / (row['run_id'] + '.log')
        with log_path.open('xb') as stream:
            child = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
            state['live_started'] += 1
            state['current_launcher_pid'] = child.pid
            history.append({'run_id': row['run_id'], 'argv': command, 'pid': child.pid,
                            'started_local': started, 'configuration_sha256': row['config_sha256']})
            write(REPORT / 'queue/DISPATCH_HISTORY.json', history)
            update(state)
            code = child.wait()
        history[-1].update(exit_code=code, finished_local=datetime.datetime.now().astimezone().isoformat())
        write(REPORT / 'queue/DISPATCH_HISTORY.json', history)
        state.update(current_run=None, current_launcher_pid=None,
                     terminated_processes=len(history), last_run=row['run_id'])
        checkpoint = Path(row['output']) / 'official_checkpoint.json'
        records = json.loads(checkpoint.read_text()).get('_checkpoint', {}).get('records', []) if checkpoint.exists() else []
        receipt_path = Path(row['output']) / 'process_job/PROCESS_RECEIPT.json'
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        state['last_official_status'] = records[0].get('status') if len(records) == 1 else None
        state['last_process_exit'] = code
        # Native collision/noncompletion is an outcome and does not stop dispatch.
        # Missing native terminal or failed cleanup needs a technical review.
        if code != 0 or len(records) != 1 or receipt.get('cleanup_pass') is not True:
            state.update(status='DEV_TECHNICAL_INTERRUPTION_REVIEW_REQUIRED')
            update(state)
            break
        state['complete_native_runs'] = state.get('complete_native_runs', 0) + 1
        state['status'] = 'DEV_UNIT_TERMINATED_RETAINED'
        update(state)
        print(json.dumps({'run_id': row['run_id'], 'status': state['last_official_status'],
                          'complete_native_runs': state['complete_native_runs']}), flush=True)
    else:
        state.update(status='DEV_MATRIX_TERMINATED_REPORT_PENDING', current_run=None)
    state['queue_finished_local'] = datetime.datetime.now().astimezone().isoformat()
    update(state)


if __name__ == '__main__':
    (REPORT / 'queue').mkdir(exist_ok=True)
    main()
