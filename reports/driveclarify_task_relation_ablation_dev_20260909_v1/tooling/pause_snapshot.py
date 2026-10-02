"""只读冻结账本及首个权威原始结果；不补登记、不重配 seed。"""
from pathlib import Path
import csv
import hashlib
import json
import datetime

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1]
BATCH = ROOT / 'reports/driveclarify_transparent_bypass_full_bench2drive_v2'


def load(path):
    return json.loads(path.read_text())


def authoritative(path):
    if not path.exists():
        return False
    try:
        records = load(path).get('_checkpoint', {}).get('records', [])
    except ValueError:
        return False
    if len(records) != 1:
        return False
    row = records[0]
    status = row.get('status', '')
    return (status.startswith(('Failed', 'Completed', 'Perfect'))
            and not any(v in status.lower() for v in ['crashed', "couldn't"])
            and all(k in row.get('scores', {}) for k in ['score_composed', 'score_route', 'score_penalty']))


def snapshot():
    freeze = load(BATCH / 'FULL_B2D_FREEZE_RECEIPT.json')
    ledgers = {a: load(BATCH / (a + '_EXECUTION_LEDGER.json')) for a in ['A0', 'A1']}
    rows = []
    pairs = []
    for pair in freeze['pair_order']:
        paired = []
        for arm in pair['arm_order']:
            entry = ledgers[arm]['entries'][pair['canonical_index']]
            assert entry['route_id'] == pair['route_id']
            attempts = sorted((BATCH / 'formal' / pair['route_id'] / arm).glob('attempt_*'))
            finals = [a for a in attempts if authoritative(a / 'official_checkpoint.json')]
            assert len(finals) <= 1, 'DUPLICATE_AUTHORITY_REVIEW_REQUIRED'
            indexed = entry['status'] == 'AUTHORITATIVE'
            if indexed:
                assert len(finals) == 1 and str(finals[0]) == entry['authoritative_output']
                assert hashlib.sha256((finals[0] / 'official_checkpoint.json').read_bytes()).hexdigest() == entry['raw_sha256']
            status = ('COMPLETED_INDEXED' if indexed else 'COMPLETED_RAW_PENDING_INDEX' if finals
                      else 'ACTIVE_DRAINING' if pair['route_id'] == '3904' and arm == 'A1'
                      else 'NOT_RUN' if not attempts else 'PRIOR_ATTEMPT_REVIEW_REQUIRED')
            record = dict(canonical_index=pair['canonical_index'], route_id=pair['route_id'], seed=pair['seed'], arm=arm,
                          status=status, attempts=[str(a) for a in attempts], authoritative_output=str(finals[0]) if finals else None,
                          raw_sha256=hashlib.sha256((finals[0] / 'official_checkpoint.json').read_bytes()).hexdigest() if finals else None)
            rows.append(record)
            paired.append(record)
        count = sum(r['status'].startswith('COMPLETED') for r in paired)
        pairs.append(dict(route_id=pair['route_id'], seed=pair['seed'], completed_arms=count,
                          incomplete_pair=count == 1, arms={x['arm']: x['status'] for x in paired}))
    data = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), batch_root=str(BATCH),
                freeze_digest=freeze['freeze_digest'], rows=rows, pairs=pairs,
                counts={s: sum(r['status'] == s for r in rows) for s in sorted({r['status'] for r in rows})},
                complete_pairs=sum(p['completed_arms'] == 2 for p in pairs),
                incomplete_pairs=[p for p in pairs if p['incomplete_pair']],
                original_ledgers_modified=False, automatic_resume=False)
    path = OUT / 'bench2drive_pause/PAUSE_INVENTORY_CURRENT.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)
    with (OUT / 'bench2drive_pause/ROUTE_ARM_STATUS_CURRENT.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=['canonical_index', 'route_id', 'seed', 'arm', 'status', 'authoritative_output', 'raw_sha256'])
        writer.writeheader()
        writer.writerows({k: row[k] for k in writer.fieldnames} for row in rows)
    return data


if __name__ == '__main__':
    data = snapshot()
    print(json.dumps({k: data[k] for k in ['counts', 'complete_pairs', 'incomplete_pairs']}))
