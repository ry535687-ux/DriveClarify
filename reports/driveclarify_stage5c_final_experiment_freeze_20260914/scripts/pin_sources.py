"""Read-only source integrity capture. Does not import any method or runtime."""
from pathlib import Path
import datetime
import hashlib
import json
import subprocess

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[1]
pins, prior = {}, []


def add(path, role, expected=None):
    path = Path(path)
    path = path if path.is_absolute() else ROOT / path
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    if expected is not None:
        prior.append(dict(path=key, expected=expected, actual=digest, match=digest == expected))
        if digest != expected:
            raise RuntimeError('Frozen hash mismatch: ' + key)
    if key in pins:
        pins[key]['roles'] = sorted(set(pins[key]['roles'] + [role]))
    else:
        pins[key] = dict(path=key, sha256=digest, bytes=len(data), roles=[role])


def main():
    target = OUT / 'evidence/SOURCE_INTEGRITY_ENTRY.json'
    if target.exists():
        raise SystemExit('Entry freeze exists; use verify_artifacts.py to check it.')
    for dirname in ['driveclarify_stage5a_controlled_main_ablation_20260914',
                    'driveclarify_stage5b_heldout_evaluation_20260914']:
        directory = ROOT / 'reports' / dirname
        manifest = json.loads((directory / 'ARTIFACT_MANIFEST.json').read_text())
        add(directory / 'ARTIFACT_MANIFEST.json', 'prior_manifest')
        for member in manifest.get('members', manifest.get('files', [])):
            add(directory / member['path'], 'preserved_prior_artifact', member['sha256'])
    directory = ROOT / 'reports/driveclarify_stage5a_controlled_main_ablation_20260914'
    for member in json.loads((directory / 'BENCHMARK_MANIFEST.json').read_text())['sources']:
        add(member['path'], member['role'], member['sha256'])
    for member in json.loads((directory / 'evidence/CLOSED_LOOP_EVIDENCE.json').read_text())['source_files']:
        add(member['path'], 'closed_loop_source', member['sha256'])
    directory = ROOT / 'reports/driveclarify_stage5b_heldout_annotation_20260914'
    freeze = json.loads((directory / 'PACKET_FREEZE.json').read_text())
    for name, key in [('ANNOTATION_PACKET_CANONICAL.json', 'canonical_sha256'),
                      ('ANNOTATION_PACKET_A.json', 'packet_A_sha256'),
                      ('ANNOTATION_PACKET_B.json', 'packet_B_sha256'),
                      ('ANNOTATION_GUIDELINE.md', 'guideline_sha256'),
                      ('HELDOUT_DUPLICATE_AUDIT.json', 'duplicate_audit_sha256')]:
        add(directory / name, 'annotation_public_source', freeze[key])
    for name in ['PACKET_FREEZE.json', 'annotations_B.csv', 'ANNOTATOR_B_NOTES.md', 'BUILD_REPORT.md']:
        add(directory / name, 'annotation_provenance')
    for name in ['FINAL_REPORT.md', 'B_TASK_EVENT_RESULT.json', 'evidence/B_OBSERVED_MOTION.json']:
        add(ROOT / 'reports/driveclarify_stage3c_b_native_runtime_20260914' / name, 'native_diagnostic')
    for name in ['evidence/PAPER_BODY_WITH_MATH.txt', 'evidence/PAPER_IDENTITY.json',
                 'REVIEW_RESOLUTION_AND_METHOD_PATCH.md']:
        add(ROOT / 'reports/driveclarify_experiment_restructure_step2_20260912' / name, 'manuscript_anchor')
    for name in ['method.tex', 'method_cn.tex', 'conclusion_cn.tex', 'experiments_rq12_cn.tex']:
        add(ROOT / 'deliverables/driveclarify_method_latex_v1' / name, 'legacy_manuscript_anchor')
    request = Path('/home/buaa/.codex/attachments/c103a611-c9f3-4f43-80e0-97b81d76c4f7/pasted-text.txt')
    add(request, 'user_request')
    (OUT / 'evidence/USER_REQUEST.txt').write_bytes(request.read_bytes())
    record = dict(created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  scope='Listed evidence and frozen code only; not a full workspace byte audit.',
                  sources=list(pins.values()), previous_hash_checks=prior, prior_hash_checks_pass=True,
                  git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  git_tracked_diff_sha256=hashlib.sha256(subprocess.check_output(
                      ['git', 'diff', '--binary', 'HEAD'], cwd=ROOT)).hexdigest())
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(sources_pinned=len(pins), prior_hash_checks_passed=len(prior))))


if __name__ == '__main__':
    main()
