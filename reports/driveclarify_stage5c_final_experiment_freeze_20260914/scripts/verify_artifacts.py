"""Read-only by default. --seal writes only final packaging receipts in this directory."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
from check_evidence import run_checks

OUT=Path(__file__).resolve().parents[1]
READY='STAGE5C_FINAL_EXPERIMENT_EVIDENCE_READY'
BLOCKED='STAGE5C_ARTIFACT_INTEGRITY_BLOCKED'
EXCLUDED={'ARTIFACT_MANIFEST.json','ARTIFACT_VERIFICATION.json'}


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def current_files():
    return sorted(p for p in OUT.rglob('*') if p.is_file() and '__pycache__' not in p.parts
                  and p.relative_to(OUT).as_posix() not in EXCLUDED)


def member(path):
    data=path.read_bytes()
    return dict(path=path.relative_to(OUT).as_posix(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seal',action='store_true',help='Package reviewed files and write a final integrity receipt; no scientific execution.')
    args=parser.parse_args()
    if args.seal:
        acceptance=run_checks(final_pass=False)
        write_json(OUT/'evidence/ACCEPTANCE_RESULTS.json',acceptance)
        status=READY if acceptance['passed'] else BLOCKED
        write_json(OUT/'FINAL_STATUS.json',dict(
            status=status,interface_boundary_accepted='STAGE5B_HELDOUT_INPUT_SCHEMA_BLOCKED',
            main_experiment='Controlled Selective Clarification under Structured Task Evidence',
            new_experiments_required=False,new_experiments_performed=0,heldout_predictions_performed=0,
            new_parser=False,relation_rule_changed=False,new_native_runs=0,
            next_step='Assemble the full manuscript from V5 and the five aligned section patches.',
            checks_passed=acceptance['checks_passed'],checks_total=acceptance['checks_total']))
        if not acceptance['passed']:
            text=(OUT/'FINAL_REPORT.md').read_text().replace(READY,BLOCKED)
            (OUT/'FINAL_REPORT.md').write_text(text)
            print(json.dumps(acceptance,ensure_ascii=False,indent=2))
            raise SystemExit(1)
        text=(OUT/'FINAL_REPORT.md').read_text().replace(BLOCKED,READY)
        (OUT/'FINAL_REPORT.md').write_text(text)
        write_json(OUT/'ARTIFACT_MANIFEST.json',dict(
            status=READY,created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            scope='Final manuscript/data/figure packet; source dependencies are separately pinned.',
            excluded_files=sorted(EXCLUDED), exclusion_reason='Manifest cannot hash itself; verification receipt is generated after sealing.',
            source_inventory='evidence/SOURCE_INTEGRITY_ENTRY.json',members=[member(p) for p in current_files()]))
        # Receipt exists before final link checking; replaced with the verified result below.
        write_json(OUT/'ARTIFACT_VERIFICATION.json',dict(status='verification_in_progress'))

    manifest=json.loads((OUT/'ARTIFACT_MANIFEST.json').read_text())
    failed=[]
    for item in manifest['members']:
        path=OUT/item['path']
        if not path.is_file() or member(path)!=item:failed.append(item['path'])
    if {p.relative_to(OUT).as_posix() for p in current_files()}!={i['path'] for i in manifest['members']}:
        failed.append('FILE_SET_MISMATCH')
    acceptance=run_checks(final_pass=True)
    failed.extend(c['name'] for c in acceptance['checks'] if not c['passed'])
    status=json.loads((OUT/'FINAL_STATUS.json').read_text())['status']
    if status!=READY or manifest['status']!=READY:failed.append('STATUS_NOT_READY')
    report=dict(status=READY if not failed else BLOCKED,passed=not failed,
                manifest_sha256=hashlib.sha256((OUT/'ARTIFACT_MANIFEST.json').read_bytes()).hexdigest(),
                artifact_members_checked=len(manifest['members']),
                source_files_checked=len(json.loads((OUT/'evidence/SOURCE_INTEGRITY_ENTRY.json').read_text())['sources']),
                checks_passed=acceptance['checks_passed'],checks_total=acceptance['checks_total'],
                failures=failed,read_only_verifier=not args.seal)
    if args.seal:write_json(OUT/'ARTIFACT_VERIFICATION.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if failed:raise SystemExit(1)


if __name__=='__main__':
    main()
