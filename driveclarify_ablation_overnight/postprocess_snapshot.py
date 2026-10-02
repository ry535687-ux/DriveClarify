"""Durable statistics snapshot after a queue unit; never used by the policy."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from driveclarify_ablation_overnight.resource_join import join_resources


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', required=True, type=Path)
    p.add_argument('--report-root', required=True, type=Path)
    p.add_argument('--snapshot', required=True, type=Path)
    a = p.parse_args()
    if a.snapshot.exists():
        raise FileExistsError('STATISTICS_SNAPSHOT_ALREADY_EXISTS')
    m = json.loads(a.manifest.read_text())
    join_resources(a.report_root, m.get('runs', m.get('rows', [])))
    a.snapshot.mkdir(parents=True)
    normalized = a.snapshot / 'normalized'
    subprocess.run([sys.executable, '-m', 'driveclarify_ablation_overnight.normalize',
        '--manifest', str(a.manifest), '--output-dir', str(normalized)], check=True)
    stats = a.snapshot / 'statistics'
    subprocess.run([sys.executable, '-m', 'driveclarify_ablation_overnight.statistics',
        '--manifest', str(normalized / 'STATISTICS_MANIFEST.json'),
        '--results', str(normalized / 'NORMALIZED_RESULTS.jsonl'), '--output-dir', str(stats)], check=True)
    summary = json.loads((stats / 'summary.json').read_text())
    pointer = a.report_root / 'LATEST_STATISTICS.json'
    temp = pointer.with_suffix('.tmp')
    temp.write_text(json.dumps({'snapshot': str(a.snapshot), 'summary': summary}, indent=2) + '\n')
    temp.replace(pointer)


if __name__ == '__main__':
    main()
