"""Archive new diagnostic source and config diffs without altering any runtime."""
import datetime
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path('/home/buaa/wrh/DriveClarify')
REPORT = Path(__file__).resolve().parents[1]


def main():
    output = REPORT / 'engineering_revision_r1'
    output.mkdir(exist_ok=False)
    paths = sorted((ROOT / 'driveclarify_clear_task_diagnostic').glob('*.py'))
    paths += sorted((ROOT / 'driveclarify_clear_task_entry').glob('*.py'))
    paths += sorted((ROOT / 'tests/clear_task_diagnostic').glob('*.py'))
    paths += [REPORT / 'tooling' / name for name in
              ['prepare.py', 'queue.py', 'run_native_episode.sh', 'resource_monitor.py']]
    records, patches = [], []
    for path in paths:
        data = path.read_bytes()
        relative = str(path.relative_to(ROOT))
        records.append({'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                        'bytes': len(data), 'change': 'NEW_FILE_CURRENT_DIAGNOSTIC'})
        patches.extend(difflib.unified_diff([], data.decode().splitlines(keepends=True),
                       fromfile='/dev/null', tofile=relative))
    (output / 'NEW_CODE_AND_TESTS.diff').write_text(''.join(patches))
    manifest = json.loads((REPORT / 'DEV_MANIFEST.json').read_text())
    config_patches, comparison = [], []
    for row in manifest['runs']:
        before = Path(row['source_config'])
        after = Path(row['config_path'])
        before_data = json.loads(before.read_text())
        after_data = json.loads(after.read_text())
        left = json.dumps(before_data, indent=2, sort_keys=True).splitlines(keepends=True)
        right = json.dumps(after_data, indent=2, sort_keys=True).splitlines(keepends=True)
        config_patches.extend(difflib.unified_diff(left, right, fromfile=str(before), tofile=str(after)))
        unchanged = {
            'runtime_actors': before_data['method_input']['runtime_actors'] == after_data['method_input']['runtime_actors'],
            'alternatives': before_data['method_input']['alternatives'] == after_data['method_input']['alternatives'],
            'checkpoint_sha256': before_data['checkpoint_sha256'] == after_data['checkpoint_sha256'],
        }
        assert all(unchanged.values())
        comparison.append({'run_id': row['run_id'], 'source': str(before),
                           'new': str(after), 'unchanged': unchanged})
    (output / 'SOURCE_TO_DIAGNOSTIC_CONFIGS.diff').write_text(''.join(config_patches))
    receipt = {'captured_local': datetime.datetime.now().astimezone().isoformat(),
               'scope': 'SOURCE_AND_CONFIG_ARCHIVE_ONLY_NO_RUNTIME_MUTATION',
               'runtime_revision': 'CLEAR_TASK_DEV_R1', 'files': records,
               'config_comparison': comparison,
               'runner_minimal_diff': str(REPORT / 'tooling/RUNNER_FROM_PRIOR.diff'),
               'freeze': str(REPORT / 'DEV_RUNTIME_FREEZE.json')}
    (output / 'RECEIPT.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'source_files': len(records),
                      'config_comparisons': len(comparison)}))


if __name__ == '__main__':
    main()
