"""Publish queue evidence into handoff STATE files; no dispatch/control authority."""
import datetime
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def atomic(path, value):
    p = path.with_suffix('.tracker.tmp')
    p.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    p.replace(path)


while not (ROOT / 'STOP_STATUS_TRACKER').exists():
    qpath = ROOT / 'formal/queue/QUEUE_STATE.json'
    q = json.loads(qpath.read_text())
    current = json.loads((ROOT / 'STATE.json').read_text())
    current.update(current_run=q.get('current_run'), queue_status=q['status'],
                   queue_state_path=str(qpath), formal_started=len(q['runs']),
                   formal_processes_terminated=sum('TERMINATED' in v['status'] for v in q['runs'].values()),
                   last_updated_local=datetime.datetime.now().astimezone().isoformat())
    pointer = ROOT / 'LATEST_STATISTICS.json'
    if pointer.exists():
        latest = json.loads(pointer.read_text())
        groups = [g for g in latest['summary']['groups'] if g['phase'] == 'FORMAL']
        if groups:
            group = groups[0]
            current.update(formal_terminal_complete=sum(a['complete_terminal'] for a in group['arms'].values()),
                           formal_driving_failures=sum(a['driving_failure'] for a in group['arms'].values()),
                           formal_technical_interruptions=sum(a['technical_interruption'] for a in group['arms'].values()),
                           formal_pairs_complete=group['complete_terminal_pairs'],
                           latest_formal_statistics=latest['snapshot'])
    current['status'] = ('ABLATION_OVERNIGHT_FORMAL_RUNNING' if q['status'] == 'ACTIVE'
                         else 'ABLATION_OVERNIGHT_FORMAL_QUEUE_STOPPED_PENDING_FINAL_REVIEW')
    atomic(ROOT / 'STATE.json', current)
    main = json.loads((REPO / 'STATE.json').read_text())
    main['driveclarify_ablation_overnight_20260909_v1'] = current
    main['current_task_status'] = current['status']
    atomic(REPO / 'STATE.json', main)
    if q['status'] != 'ACTIVE':
        break
    time.sleep(15)
