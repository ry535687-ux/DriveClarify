"""Verify the sealed Stage5D packet without changing figures or scientific data."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True
from audit_exports import audit

OUT = Path(__file__).resolve().parents[1]
MANIFEST = OUT / 'ARTIFACT_MANIFEST.json'
RECEIPT = OUT / 'ARTIFACT_VERIFICATION.json'
EXCLUDED = {MANIFEST.name, RECEIPT.name}
READY = 'STAGE5D_PUBLICATION_FIGURES_READY'
BLOCKED = 'STAGE5D_FIGURE_DATA_INTEGRITY_BLOCKED'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def members():
    return sorted(
        p for p in OUT.rglob('*')
        if p.is_file() and '__pycache__' not in p.parts
        and p.relative_to(OUT).as_posix() not in EXCLUDED
    )


def check_documents():
    required = [
        'FIGURE_STYLE_GUIDE.md', 'FIGURE_DATA_PROVENANCE.md', 'FINAL_REPORT.md',
        'scripts/README.md', 'scripts/render_publication_figures.py',
        'scripts/audit_exports.py', 'scripts/verify_artifacts.py',
        'qa/SOURCE_FREEZE.json', 'qa/RENDER_RECEIPTS.json',
        'qa/EXPORT_AUDIT.json', 'qa/VISUAL_QA.md', 'qa/USER_REQUEST.txt',
    ] + [f'captions/FIGURE_{letter}_CAPTION.md' for letter in 'ABCD']
    for name in required:
        path = OUT / name
        assert path.is_file() and path.stat().st_size, name
    checked = 0
    for doc in OUT.rglob('*.md'):
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', doc.read_text()):
            target = target.strip('<>')
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            path = (doc.parent / unquote(parsed.path)).resolve()
            # The manifest is created after all other required files pass.
            if path == MANIFEST:
                continue
            assert path.exists(), f'Broken link in {doc.name}: {target}'
            checked += 1
    return checked


def verify(seal=False):
    export_audit = audit(record=False)
    saved_audit = json.loads((OUT / 'qa/EXPORT_AUDIT.json').read_text())
    # Normalize tuple/list differences from JSON serialization.
    assert saved_audit == json.loads(json.dumps(export_audit)), 'Stale export audit'
    links = check_documents()
    if seal:
        manifest = {
            'status': READY,
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'scope': 'Publication figures, captions, provenance and QA only',
            'stage5c_manifest_sha256': export_audit['stage5c_manifest_sha256'],
            'scientific_statistics_added': False,
            'excluded_metadata': sorted(EXCLUDED),
            'exclusion_reason': 'Avoid self-referential hashes; verification receipt pins the manifest SHA.',
            'files': [
                {'path': p.relative_to(OUT).as_posix(), 'bytes': p.stat().st_size,
                 'sha256': sha(p)} for p in members()
            ],
        }
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    manifest = json.loads(MANIFEST.read_text())
    assert manifest['status'] == READY and manifest['scientific_statistics_added'] is False
    assert manifest['stage5c_manifest_sha256'] == export_audit['stage5c_manifest_sha256']
    assert manifest['excluded_metadata'] == sorted(EXCLUDED)
    entries = manifest['files']
    names = [entry['path'] for entry in entries]
    assert len(names) == len(set(names)), 'Duplicate manifest member'
    assert set(names) == {p.relative_to(OUT).as_posix() for p in members()}, 'Member set changed'
    for entry in entries:
        path = OUT / entry['path']
        assert path.stat().st_size == entry['bytes'] and sha(path) == entry['sha256'], entry['path']
    result = {
        'status': READY, 'passed': True,
        'artifact_manifest_sha256': sha(MANIFEST),
        'artifact_members_verified': len(entries),
        'stage5c_manifest_sha256': export_audit['stage5c_manifest_sha256'],
        'stage5c_files_unchanged': export_audit['stage5c_files_unchanged'],
        'figure_variants_checked': export_audit['figure_variants_checked'],
        'export_files_checked': export_audit['export_files_checked'],
        'local_document_links_checked': links,
        'versions': export_audit['versions'],
        'scientific_statistics_added': False,
    }
    if seal:
        RECEIPT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    else:
        assert json.loads(RECEIPT.read_text()) == result, 'Verification receipt changed'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seal', action='store_true', help='Write packaging metadata after review')
    args = parser.parse_args()
    try:
        result = verify(seal=args.seal)
    except Exception as exc:
        print(json.dumps({'status': BLOCKED, 'passed': False,
                          'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
