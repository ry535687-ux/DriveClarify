"""Read-only final comparison against the existing entry and formal hashes."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path


def digest(path):
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.report_root.resolve()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite a source verification receipt')
    if read(root / 'formal/queue/QUEUE_STATE.json')['status'] == 'ACTIVE':
        raise SystemExit('Final source verification requires the queue to stop')
    predecessor = root.parent / 'driveclarify_task_relation_ablation_dev_20260909_v1'
    freeze = read(root / 'formal/FORMAL_FREEZE.json')
    manifest_path = root / 'formal/FORMAL_QUEUE_MANIFEST.json'
    manifest = read(manifest_path)
    groups = {
        'formal_runtime_sources': read(root / 'formal/SOURCE_SHA256.json'),
        'historical_scientific_sources': {
            row['path']: row['expected']
            for row in read(predecessor / 'SOURCE_PRESERVATION.json')['files']},
        'historical_audited_evidence': {
            path: row['sha256']
            for path, row in read(predecessor / 'HISTORICAL_EVIDENCE_HASHES.json').items()
            if row.get('exists')},
        'formal_manifest_protocol_checkpoint': {
            str(manifest_path): freeze['manifest_sha256'],
            str(root / 'ABLATION_PROTOCOL.md'): freeze['protocol_sha256'],
            freeze['checkpoint_path']: freeze['checkpoint_sha256']},
        'formal_configuration_and_scene_inputs': {},
        'long_term_requirements': {
            str(root.parents[1] / 'PROJECT_LONG_TERM_REQUIREMENTS.md'):
                digest(predecessor / 'entry/PROJECT_LONG_TERM_REQUIREMENTS.md')},
    }
    for row in manifest['runs']:
        for prefix in ('config', 'route', 'candidate_route', 'task_binding'):
            path, expected = row[prefix + '_path'], row[prefix + '_sha256']
            existing = groups['formal_configuration_and_scene_inputs'].get(path)
            if existing is not None and existing != expected:
                raise SystemExit('Conflicting frozen hashes for ' + path)
            groups['formal_configuration_and_scene_inputs'][path] = expected
    cache, results = {}, {}
    for name, entries in groups.items():
        rows = []
        for path, expected in entries.items():
            if path not in cache:
                cache[path] = digest(Path(path))
            rows.append({'path': path, 'expected_sha256': expected,
                         'actual_sha256': cache[path],
                         'matches': expected is not None and cache[path] == expected})
        results[name] = {'checked': len(rows), 'files': rows,
                         'mismatches': [row for row in rows if not row['matches']]}
    mismatches = sum(len(group['mismatches']) for group in results.values())
    receipt = {'schema': 'driveclarify.ablation.final-source-preservation.v1',
               'created_local': datetime.datetime.now().astimezone().isoformat(),
               'status': 'PASS' if not mismatches else 'FAIL',
               'mismatch_count': mismatches, 'groups': results,
               'scope': 'Only the named entry evidence and frozen scientific inputs; '
                        'not a content hash of every pre-existing untracked file.',
               'scientific_mutations_performed': 0}
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': receipt['status'], 'groups': {
        name: {'checked': group['checked'], 'mismatches': len(group['mismatches'])}
        for name, group in results.items()}}, ensure_ascii=False))
    if mismatches:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
