"""仅等待既有 evaluator 自然退出，再停止其已暂停的调度服务；绝不派发。"""
from pathlib import Path
import datetime
import json
import subprocess
import time

import psutil
from pause_snapshot import OUT, BATCH, snapshot

EVALUATOR_PID = 3431772
CREATED = 1788885002.12
SUPERVISOR_PID = 1252111
SERVICE = 'driveclarify_full_b2d_v2.service'


def write(value):
    value['utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path = OUT / 'bench2drive_pause/DRAIN_STATUS.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def main():
    while True:
        try:
            p = psutil.Process(EVALUATOR_PID)
            if abs(p.create_time() - CREATED) > .01:
                raise RuntimeError('EVALUATOR_PID_IDENTITY_CHANGED')
            if p.status() == psutil.STATUS_ZOMBIE:
                break
            assert str(BATCH / 'formal/3904/A1/attempt_01/official_checkpoint.json') in ' '.join(p.cmdline())
            parent = psutil.Process(SUPERVISOR_PID)
            assert parent.status() == psutil.STATUS_STOPPED
            write(dict(status='DRAINING_ORIGINAL_EPISODE_NO_DISPATCH', evaluator_pid=p.pid,
                       parent_stopped=True, active_episode_interrupted=False, automatic_resume=False,
                       evaluator_rss_bytes=p.memory_info().rss, evaluator_cpu_seconds=sum(p.cpu_times()[:2])))
        except psutil.NoSuchProcess:
            break
        time.sleep(30)
    # 只有 evaluator 自然退出后才能停止服务；保留其真实 /proc exit_code（若仍为 zombie）。
    proc = Path('/proc/%d/stat' % EVALUATOR_PID)
    raw_stat = proc.read_text() if proc.exists() else None
    if raw_stat:
        (OUT / 'bench2drive_pause/evaluator_terminal_proc_stat.txt').write_text(raw_stat)
    inventory = snapshot()
    result = subprocess.run(['systemctl', '--user', 'stop', SERVICE], capture_output=True, text=True, timeout=150)
    residue = []
    for p in psutil.process_iter(['pid', 'cmdline', 'status']):
        cmd = p.info['cmdline'] or []
        if (p.pid in [EVALUATOR_PID, SUPERVISOR_PID, 3431811, 3431812, 3431819]
                and p.info['status'] != psutil.STATUS_ZOMBIE):
            residue.append(p.info)
    write(dict(status='PAUSED_DRAIN_COMPLETE' if result.returncode == 0 and not residue else 'DRAIN_COMPLETE_CLEANUP_REVIEW_REQUIRED',
               evaluator_exited_naturally=True, active_episode_interrupted=False, automatic_resume=False,
               service_stop_exit=result.returncode, service_stop_stdout=result.stdout, service_stop_stderr=result.stderr,
               residual_owned_processes=residue, inventory_counts=inventory['counts'],
               original_ledger_pending_recovery=True,
               process_receipt_missing_reason='Original dispatcher paused; native evaluator raw result retained; do not invent runner receipt'))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        write(dict(status='DRAIN_WATCHER_ERROR_REVIEW_REQUIRED', error=repr(exc), automatic_resume=False))
        raise
