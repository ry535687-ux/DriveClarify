"""Join passive host samples to each ended run's recorded wall interval."""
import argparse
import hashlib
import json
from pathlib import Path


def join_resources(report_root, runs):
    source = Path(report_root) / 'RESOURCE_SAMPLES.jsonl'
    raw = source.read_bytes()
    samples = [json.loads(x) for x in raw.splitlines() if x.strip()]
    receipts = []
    for planned in runs:
        out = Path(planned['output']) / 'process_job'
        process_path = out / 'PROCESS_RECEIPT.json'
        if not process_path.exists():
            continue
        target = out / 'RESOURCE_USAGE.jsonl'
        if target.exists():
            continue
        process = json.loads(process_path.read_text())
        rows = []
        for s in samples:
            if not process['started_epoch'] <= s['epoch'] <= process['finished_epoch']:
                continue
            gpu = [v.strip() for v in s['host_gpu_memory_utilization_power'].split(',')]
            gpu_memory = float(gpu[0]) if gpu and gpu[0].replace('.', '', 1).isdigit() else None
            rss_kib = 0
            identities = []
            for line in s['matched_task_process_rows']:
                fields = line.split(None, 5)
                if len(fields) != 6:
                    continue
                pid, ppid, elapsed, cpu, rss, command = fields
                # Only actual evaluator/CARLA executables, not queue/read tools
                # whose argv happens to mention an experiment directory.
                is_ego = 'leaderboard_evaluator.py --host=127.0.0.1 --port=28100 ' in command and command.startswith('/home/buaa/anaconda3/envs/simlingo/bin/python ')
                is_carla = command.startswith('/home/buaa/CARLA_0.9.15/CarlaUE4/Binaries/Linux/CarlaUE4-Linux-Shipping ')
                if is_ego or is_carla:
                    rss_kib += int(rss)
                    identities.append(int(pid))
            rows.append({'epoch': s['epoch'], 'local_time': s['local_time'],
                         'gpu_memory_mib': gpu_memory, 'rss_mib': rss_kib / 1024.0,
                         'owned_sample_pids': identities, 'gpu_scope': 'HOST_INCLUDES_DESKTOP',
                         'rss_scope': 'SUM_OF_EVALUATOR_AND_CARLA_RSS_NOT_UNIQUE_PSS',
                         'disk_free_bytes': s['disk_free_bytes'], 'source': str(source),
                         'observational_only': True})
        # Empty/partial pre-sampler intervals are explicit, never invented peaks.
        target.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        meta = {'run_id': planned['run_id'], 'sample_count': len(rows),
                'interval_start_epoch': process['started_epoch'], 'interval_end_epoch': process['finished_epoch'],
                'source_prefix_bytes': len(raw), 'source_prefix_sha256': hashlib.sha256(raw).hexdigest(),
                'interval_fully_sampled': bool(rows) and rows[0]['epoch'] - process['started_epoch'] <= 20,
                'sampling_period_s': 15, 'source': str(source),
                'not_an_exact_peak_measurement': True}
        (out / 'RESOURCE_JOIN_RECEIPT.json').write_text(json.dumps(meta, indent=2) + '\n')
        receipts.append(meta)
    return receipts


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--report-root', required=True, type=Path)
    p.add_argument('--manifest', required=True, type=Path)
    a = p.parse_args()
    m = json.loads(a.manifest.read_text())
    join_resources(a.report_root, m.get('runs', m.get('rows', [])))


if __name__ == '__main__':
    main()
