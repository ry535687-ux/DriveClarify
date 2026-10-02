"""Single-CARLA recoverable queue. Existing outputs can never be overwritten."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def atomic(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish(path, value, cfg):
    atomic(path, value)
    if not cfg.get('report_root'):
        return
    round_path = Path(cfg['report_root']) / 'STATE.json'
    current = json.loads(round_path.read_text())
    current.update(current_run=value.get('current_run'), queue_status=value['status'],
                   queue_state_path=str(path), last_updated_local=datetime.datetime.now().astimezone().isoformat())
    atomic(round_path, current)
    main_path = Path(cfg['repo_root']) / 'STATE.json'
    main_state = json.loads(main_path.read_text())
    main_state['driveclarify_ablation_overnight_20260909_v1'] = current
    atomic(main_path, main_state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--queue-dir', required=True, type=Path)
    args = parser.parse_args()
    cfg = json.loads(args.manifest.read_text())
    root = args.queue_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / 'QUEUE_STATE.json'
    state = {'status': 'ACTIVE', 'manifest_sha256': digest(args.manifest), 'runs': {},
             'pid': os.getpid(), 'started_epoch': time.time()}
    publish(state_path, state, cfg)
    deadline = datetime.datetime.fromisoformat(cfg['stop_dispatch_local']).timestamp()
    for planned in cfg['runs']:
        run_id = planned['run_id']
        out = Path(planned['output'])
        receipt = out / 'process_job/PROCESS_RECEIPT.json'
        state['current_run'] = run_id
        publish(state_path, state, cfg)
        if planned.get('wait_for_existing_unit') and not receipt.exists():
            while not receipt.exists():
                check = subprocess.run(['systemctl', '--user', 'is-active', planned['wait_for_existing_unit']],
                                       capture_output=True, text=True)
                if check.returncode != 0:
                    # Allow the runner's EXIT trap to finish its atomic receipt.
                    time.sleep(2)
                    if not receipt.exists():
                        state['status'] = 'EXISTING_RUN_HAS_NO_TERMINAL_RECEIPT'
                        publish(state_path, state, cfg)
                        return 2
                time.sleep(5)
        if receipt.exists():
            state['runs'][run_id] = {'status': 'ALREADY_TERMINATED_PRESERVED',
                                     'receipt_sha256': digest(receipt)}
            publish(state_path, state, cfg)
            continue
        if out.exists():
            state['status'] = 'EXISTING_INCOMPLETE_OUTPUT_REQUIRES_REVIEW'
            publish(state_path, state, cfg)
            return 2
        new_pair_cutoff = cfg.get('stop_new_pairs_local')
        if (new_pair_cutoff and planned.get('order_in_pair') == 1
                and time.time() >= datetime.datetime.fromisoformat(new_pair_cutoff).timestamp()):
            state['status'] = 'STOPPED_NEW_PAIR_DISPATCH_BUDGET'
            publish(state_path, state, cfg)
            return 0
        if time.time() >= deadline or (root / 'STOP_DISPATCH').exists():
            state['status'] = 'STOPPED_DISPATCH_BUDGET_OR_USER'
            publish(state_path, state, cfg)
            return 0
        if shutil.disk_usage(root).free < cfg.get('minimum_disk_free_bytes', 3 * 1024 ** 3):
            state['status'] = 'STOPPED_LOW_DISK'
            publish(state_path, state, cfg)
            return 2
        for path, expected in cfg['runtime_hashes'].items():
            if digest(path) != expected:
                state.update(status='STOPPED_RUNTIME_HASH_MISMATCH', mismatched_path=path)
                publish(state_path, state, cfg)
                return 2
        for key in ('config', 'route', 'candidate_route', 'task_binding'):
            if digest(planned[key + '_path']) != planned[key + '_sha256']:
                state.update(status='STOPPED_INPUT_HASH_MISMATCH', mismatched_input=key)
                publish(state_path, state, cfg)
                return 2
        cmd = [cfg['runner'], planned['config_path'], planned['route_path'],
               str(planned['seed']), str(cfg['rpc_port']), planned['broker_selected_candidate_id'],
               str(out), cfg['attempt_kind'], planned['variant']]
        state['runs'][run_id] = {'status': 'DISPATCHED', 'started_epoch': time.time(), 'argv': cmd}
        publish(state_path, state, cfg)
        with (root / 'LAUNCHER.log').open('ab') as stream:
            child = subprocess.Popen(cmd, stdout=stream, stderr=subprocess.STDOUT)
            state['runs'][run_id]['launcher_pid'] = child.pid
            publish(state_path, state, cfg)
            returncode = child.wait()
        result = json.loads(receipt.read_text()) if receipt.exists() else {}
        state['runs'][run_id].update(status='TERMINATED_PENDING_OFFLINE_CLASSIFICATION',
             launcher_exit=returncode, finished_epoch=time.time(), process_receipt=result)
        publish(state_path, state, cfg)
        if cfg.get('post_run_analysis'):
            snapshot = root / ('after_run_%03d' % planned['schedule_position'])
            command = [sys.executable, '-m', 'driveclarify_ablation_overnight.postprocess_snapshot',
                       '--manifest', str(args.manifest), '--report-root', cfg['report_root'],
                       '--snapshot', str(snapshot)]
            with (root / 'ANALYSIS.log').open('ab') as stream:
                analyzed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
            state['runs'][run_id]['analysis_exit'] = analyzed.returncode
            state['runs'][run_id]['analysis_snapshot'] = str(snapshot)
            publish(state_path, state, cfg)
            if analyzed.returncode:
                state['status'] = 'STOPPED_POSTPROCESSING_ERROR'
                publish(state_path, state, cfg)
                return 2
        if not result.get('cleanup_pass'):
            state['status'] = 'STOPPED_CLEANUP_INCOMPLETE'
            publish(state_path, state, cfg)
            return 2
        # An exposed driving failure is retained and does not stop dispatch.
        # A setup/implementation failure needs review, never an automatic retry.
        official_path = out / 'official_checkpoint.json'
        official = json.loads(official_path.read_text()) if official_path.exists() else {}
        records = official.get('_checkpoint', {}).get('records', [])
        statuses = [str(row.get('status', '')).lower() for row in records]
        if (returncode != 0 or not records or any("couldn't be set up" in s or 'crashed' in s for s in statuses)
                or (out / 'owner_evidence/ABL_FATAL_INTEGRITY.json').exists()):
            state['status'] = 'STOPPED_TECHNICAL_FAILURE_REVIEW_REQUIRED'
            publish(state_path, state, cfg)
            return 2
    state['status'] = 'QUEUE_MATRIX_TERMINATED'
    state['finished_epoch'] = time.time()
    publish(state_path, state, cfg)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
