"""One-shot read-only status, with no simulator or model imports."""
import datetime
import json
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
queue = json.loads((root / 'formal/queue/QUEUE_STATE.json').read_text())
latest = json.loads((root / 'LATEST_STATISTICS.json').read_text())
group = latest['summary']['groups'][0]
trace = root / 'formal/native' / queue['current_run'] / 'owner_evidence/V2_NATIVE_STATE_TRACE.jsonl'
last = {}
if trace.exists():
    with trace.open('rb') as stream:
        stream.seek(max(0, trace.stat().st_size - 16384))
        for line in reversed(stream.read().splitlines()):
            try:
                last = json.loads(line)
                break
            except (ValueError, UnicodeDecodeError):
                pass
print(json.dumps({
    'local_time': datetime.datetime.now().astimezone().isoformat(),
    'status': queue['status'], 'run': queue['current_run'],
    'dispatched': len(queue['runs']), 'complete_pairs': group['complete_terminal_pairs'],
    'complete_episodes': sum(a['complete_terminal'] for a in group['arms'].values()),
    'technical': sum(a['technical_interruption'] for a in group['arms'].values()),
    'primary_successes': {k: a['primary_successes'] for k, a in group['arms'].items()},
    'sim_s': round(last['simulation_time_s'], 2) if last else None,
    'speed_mps': round(last['speed_mps'], 3) if last else None,
    'controls': last.get('control_return_count'),
    'disk_free_gib': round(shutil.disk_usage(root).free / 1024 ** 3, 2),
    'snapshot': Path(latest['snapshot']).name}, ensure_ascii=False))
