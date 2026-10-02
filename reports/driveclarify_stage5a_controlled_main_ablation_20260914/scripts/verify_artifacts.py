"""只读复核最终清单、预测锁及冻结源摘要；不执行实验。"""
from common import *

if __name__ == '__main__':
    manifest = read_json(REPORT / 'ARTIFACT_MANIFEST.json')
    for row in manifest['members']:
        p = REPORT / row['path']
        assert p.is_file() and not p.is_symlink()
        assert p.resolve().is_relative_to(REPORT.resolve())
        assert p.stat().st_size == row['bytes']
        assert sha(p) == row['sha256'], row['path']
    actual = {str(p.relative_to(REPORT)) for p in REPORT.rglob('*') if p.is_file()}
    expected = {r['path'] for r in manifest['members']} | set(manifest['excluded_self_referential_files'])
    assert actual <= expected, sorted(actual - expected)
    sources = verify_sources()
    lock = read_json(REPORT / 'benchmark/PREDICTION_LOCK.json')
    for name, h in lock['files'].items():
        assert sha(REPORT / f'benchmark/{name}.jsonl') == h
    closed = read_json(REPORT / 'evidence/CLOSED_LOOP_EVIDENCE.json')
    for row in closed['source_files']:
        assert sha(REPO / row['path']) == row['sha256']
    print({'artifact_members_verified': len(manifest['members']), 'frozen_sources_verified': len(sources['sources']),
           'prediction_lock_verified': True, 'closed_loop_sources_verified': len(closed['source_files']),
           'artifact_manifest_sha256': sha(REPORT / 'ARTIFACT_MANIFEST.json'), 'exit_code': 0})
