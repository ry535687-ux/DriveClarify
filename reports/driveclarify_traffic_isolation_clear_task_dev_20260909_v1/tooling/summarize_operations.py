"""Summarize recorded host/task resources without querying or touching a simulator."""
import argparse
import csv
import datetime
import json
from pathlib import Path


def values(rows, field):
    return [r[field] for r in rows if r.get(field) is not None]


def stats(rows, field):
    v = values(rows, field)
    return {'n': len(v), 'min': min(v) if v else None,
            'max': max(v) if v else None, 'mean': sum(v) / len(v) if v else None}


def parse_sample(sample):
    result = {'epoch': sample['epoch'], 'disk_free_bytes': sample['disk_free_bytes']}
    try:
        gpu = [float(x.strip()) for x in sample['host_gpu_memory_utilization_power'].split(',')]
        for key, value in zip(['host_gpu_memory_used_MiB', 'host_gpu_memory_free_MiB',
                               'host_gpu_utilization_percent', 'host_gpu_power_W'], gpu):
            result[key] = value
    except (ValueError, KeyError, AttributeError):
        result['gpu_sample_missing_or_unparseable'] = True
    rss, cpu, pids = [], [], []
    for row in sample.get('matched_task_process_rows', []):
        parts = row.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            pids.append(int(parts[0]))
            cpu.append(float(parts[3]))
            rss.append(int(parts[4]))
        except ValueError:
            continue
    result.update(task_matched_process_count=len(pids),
                  task_process_rss_sum_KiB=sum(rss), task_process_cpu_percent_sum=sum(cpu))
    return result


def summarize(rows):
    return {field: stats(rows, field) for field in [
        'host_gpu_memory_used_MiB', 'host_gpu_utilization_percent', 'host_gpu_power_W',
        'task_process_rss_sum_KiB', 'task_process_cpu_percent_sum', 'disk_free_bytes']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    samples = [parse_sample(json.loads(line)) for line in
               (args.report_root / 'RESOURCE_SAMPLES.jsonl').read_text().splitlines() if line.strip()]
    history = json.loads((args.report_root / 'queue/DISPATCH_HISTORY.json').read_text())
    manifest = json.loads((args.report_root / 'DEV_MANIFEST.json').read_text())
    dispatched = {x['run_id']: x for x in history}
    rows, per_run = [], []
    for run in manifest['runs']:
        dispatch = dispatched.get(run['run_id'])
        row = {'run_id': run['run_id'], 'condition': run['condition'],
               'started_local': dispatch.get('started_local') if dispatch else None,
               'finished_local': dispatch.get('finished_local') if dispatch else None,
               'wall_duration_s': None, 'resource_sample_count': 0}
        selected = []
        if row['started_local'] and row['finished_local']:
            start = datetime.datetime.fromisoformat(row['started_local']).timestamp()
            end = datetime.datetime.fromisoformat(row['finished_local']).timestamp()
            selected = [s for s in samples if start <= s['epoch'] <= end]
            row.update(wall_duration_s=end-start, resource_sample_count=len(selected))
        detail = summarize(selected)
        for field, stat in detail.items():
            row[field + '_max'] = stat['max']
        per_run.append(dict(row, resource_stats=detail))
        rows.append(row)
    with (args.output / 'PER_RUN_RESOURCES.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = {'captured_local': datetime.datetime.now().astimezone().isoformat(),
              'source': str(args.report_root / 'RESOURCE_SAMPLES.jsonl'),
              'sample_count': len(samples), 'overall': summarize(samples), 'runs': per_run,
              'scope_notes': [
                  'GPU samples are host-wide and include unrelated desktop processes.',
                  'RSS is the sum of matched process RSS, not unique physical memory.',
                  'CPU percentages are ps-reported process averages and may exceed 100 across cores.',
                  'Wall duration includes original server startup and cleanup, not just model inference.',
                  'No finished dispatch means final resource interval is pending; it is not zero usage.',
                  'No real-time performance or visualization overhead equivalence is inferred.']}
    (args.output / 'RESOURCE_SUMMARY.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'sample_count': len(samples), 'runs': len(rows)}))


if __name__ == '__main__':
    main()
