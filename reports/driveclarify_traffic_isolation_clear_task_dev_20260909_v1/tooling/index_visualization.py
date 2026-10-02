"""Index real captures and retained missing-event receipts; never generate images."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    captures, maintenance = [], []
    for path in sorted((args.report_root / 'visualization').rglob('RECEIPT.json')):
        record = json.loads(path.read_text())
        if 'WINDOW_POSITION' in record.get('schema', '') or path.parent.name == 'WINDOW_POSITION_MAINTENANCE':
            maintenance.append({'receipt': str(path.resolve()), 'run_id': record.get('run_id'),
                                'status': record.get('status'),
                                'dimensions_unchanged': record.get('dimensions_unchanged'),
                                'focus_unchanged': record.get('focus_unchanged')})
            continue
        row = {key: record.get(key) for key in [
            'run_id', 'category', 'status', 'error', 'trigger_source_frame',
            'trigger_source_simulation_time_s', 'capture_started_local', 'capture_finished_local',
            'latest_logged_control_frame_before_capture',
            'latest_logged_control_simulation_time_s_before_capture',
            'pixel_log_frame_alignment', 'capture_time_interpretation',
            'same_native_pid_starttime_after', 'run_unchanged_during_capture']}
        row['receipt'] = str(path.resolve())
        row['visualization_revision'] = path.relative_to(args.report_root / 'visualization').parts[0]
        row['image'] = record.get('image')
        if row['image']:
            image = Path(row['image']['path'])
            row['image_sha256_verified'] = image.exists() and hashlib.sha256(image.read_bytes()).hexdigest() == row['image']['sha256']
            assert row['image_sha256_verified'], str(image)
        captures.append(row)
    with args.output.open('x') as stream:
        json.dump({'created_local': datetime.datetime.now().astimezone().isoformat(),
                   'captures': captures, 'window_maintenance': maintenance,
                   'genuine_image_count': sum(x['image'] is not None for x in captures),
                   'notes': [
                       'Native CARLA local DISPLAY :1 window pixels; not an ego-camera recording.',
                       'Night-time overhead native view alone does not establish a collision or parking outcome.',
                       'Receipts separate trigger clocks from capture wall time and logged context.',
                       'V1 first-run failed attempts and V2 no-backfill exclusions are retained.',
                       'CURRENT_WINDOW_ENGINEERING_SAMPLE is not a reconstruction of a missed event.',
                       'Scientific runtime and renderer/spectator were not modified by these tools.']},
                  stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'receipts': len(captures), 'real_images': sum(x['image'] is not None for x in captures)}))


if __name__ == '__main__':
    main()
