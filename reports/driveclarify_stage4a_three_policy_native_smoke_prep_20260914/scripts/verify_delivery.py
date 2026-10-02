#!/usr/bin/env python3
"""CPU checks for this stopped delivery, not qualification of a smoke run."""
import ast
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    def read(path):
        return json.loads((root/path).read_text())
    checks = []
    def check(name, condition):
        checks.append({'check': name, 'pass': bool(condition)})
    findings = read('evidence/ASSET_FINDINGS.json')
    snapshots = read('evidence/SOURCE_SNAPSHOTS.json')
    check('Exactly REF and sole replacement LMK, both stopped',
          [x['family'] for x in findings['assets']] == ['REF','LMK'] and
          all(x['stage4a_eligibility'] is False for x in findings['assets']))
    check('32 copied and original source hashes match', len(snapshots)==32 and all(
        hashlib.sha256((root/'evidence'/x['snapshot']).read_bytes()).hexdigest() == x['sha256'] and
        hashlib.sha256(Path(x['path']).read_bytes()).hexdigest() == x['sha256'] for x in snapshots))
    check('Both route pairs are parseable in-corridor arrays, no different branch claim', all(
        x['geometry']['count_A']==x['geometry']['count_B']==68 and
        x['geometry']['same_x_z_at_every_index'] and
        x['geometry']['same_declared_irreversible_branch'] and
        x['geometry']['same_declared_corridor'] and
        x['geometry']['all_road_options']==['LANEFOLLOW','STRAIGHT']
        for x in findings['assets']) and findings['REF_LMK_candidate_arrays_equal'])
    check('Archived ASK-answer identities, ordering, hashes, commit and terminal evidence', all(
        all(x['historical_chain'][k] for k in ('durable_ask','answer_query_matches','answer_receipt_matches',
            'answer_after_ask_ns','ask_hash_matches_release','answer_hash_matches_release','transaction_committed','owner_committed'))
        and x['historical_chain']['native_status']=='Completed' for x in findings['assets']))
    cfg=read('SMOKE_CONFIG.json')
    check('No runnable or fabricated smoke input', cfg['enabled'] is False and cfg['runnable'] is False and
          cfg['public_policy_config'] is None and cfg['selected_scene_id'] is None and cfg['run_commands']==[])
    qualification=read('CPU_QUALIFICATION.json')
    check('All 15 required qualifications accounted for without false PASS',
          len(qualification['required_checks'])==15 and qualification['smoke_qualified'] is False and
          sum(x['status']=='BLOCKED_ASSET' for x in qualification['required_checks'])==13)
    isolation=read('HIDDEN_INTENT_ISOLATION.json')
    check('Unperformed isolation and geometry remain null', isolation['no_clarification_hidden_file_read_count'] is None and
          isolation['digest_exchange_equal'] is None and read('TASK_EVALUATION_CONTRACT.json')['branch_A_gate'] is None and
          read('TASK_EVALUATION_CONTRACT.json')['branch_B_gate'] is None)
    repo=root.parents[1]
    wrapper=repo/'reports/driveclarify_stage3d_a_stall_watchdog_20260914/scripts/owned_runtime.py'
    check('Stage3D wrapper exact identity', hashlib.sha256(wrapper.read_bytes()).hexdigest()==
          'a8734561827bd9b724395f8f709e919802463bad5be1e258da1856d4db603ef8')
    allowed={'argparse','hashlib','json','math','pathlib','xml'}
    tree=ast.parse((root/'scripts/inspect_selected_assets.py').read_text())
    imports=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import): imports.extend(x.name.split('.')[0] for x in node.names)
        if isinstance(node,ast.ImportFrom): imports.append(node.module.split('.')[0])
    check('Extractor only standard file/geometry imports; no generated run commands',
          set(imports)<=allowed and not list((root/'commands').glob('*.command.txt')))
    boundary=read('evidence/SEARCH_BOUNDARY.json')
    check('Bounded localization under 15 minutes', boundary['elapsed_s']<boundary['limit_s'] and boundary['selected'] is None)
    result={'scope':'STOPPED_DELIVERY_FILE_AND_EVIDENCE_CHECKS_ONLY', 'checks':checks,
            'passed':sum(x['pass'] for x in checks),'total':len(checks),
            'smoke_qualification_pass':False,'native_execution':False}
    (root/'logs/delivery_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if all(x['pass'] for x in checks) else 1


if __name__=='__main__':
    raise SystemExit(main())
