"""仅B的现场前置检查；不启动驾驶，不导入native/model。"""
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import time

from display_binding import display_now
from display_preflight import command
from owned_runtime import process_rows, HARD_TOTAL_CAP
from resource_preflight import collect

REPORT=Path(__file__).resolve().parents[1]
ROOT=REPORT.parents[1]
DEV=ROOT/'experiments/driveclarify_native_clear_backend_dev_20260913'


def save(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def metadata(path):
    s=Path(path).stat()
    return {'device':s.st_dev,'inode':s.st_ino,'size':s.st_size,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns}


def preflight():
    entry=json.loads((REPORT/'evidence/ENTRY_IDENTITY.json').read_text())
    gate={'case':'B','case_id':'DEV_NATIVE_INIT_B_TOWN07_26458','created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'created_monotonic':time.monotonic(),'user_authorized':True,'native_started':False,'status':'PENDING',
          'identity':'UNKNOWN','physical_display':'UNKNOWN','resource':'UNKNOWN','process_port':'UNKNOWN','output_isolation':'UNKNOWN','wall_guard':'UNKNOWN'}
    save(REPORT/'B_PREFLIGHT_GATE.json',gate)
    try:
        if entry['git_status']!='PASS' or not entry['B_output_absent']:
            raise ValueError('ENTRY_GIT_OR_OUTPUT_BLOCKED')
        b=json.loads((DEV/'configs/BACKEND_IDENTITY.json').read_text())
        checkpoint=b['files']['checkpoint'];before=metadata(checkpoint['path'])
        spec=importlib.util.spec_from_file_location('stage3b_dry',DEV/'dry_run.py')
        dry=importlib.util.module_from_spec(spec);spec.loader.exec_module(dry)
        # 本轮仅一次完整checkpoint hash，完全不加载模型。
        effective=dry.prepare(DEV/'configs/case_B.json',verify_checkpoint=True)
        after=metadata(checkpoint['path'])
        if before!=after:raise ValueError('CHECKPOINT_CHANGED_DURING_HASH')
        contract=json.loads((DEV/'configs/evaluation_B.json').read_text())
        if contract['gates'][-1]['center']!=[-30.6,-161.4]:raise ValueError('B_LOCAL_END_MISMATCH')
        entry['core_checks']=[{'name':k,'path':v['path'],'sha256':v['sha256'],'status':'PASS',
                              **({'stat_before':before,'stat_after':after} if k=='checkpoint' else {})} for k,v in b['files'].items()]
        entry['core_status']='PASS';save(REPORT/'evidence/ENTRY_IDENTITY.json',entry)
        gate['identity']='PASS'
        wrapper=REPORT/'scripts/owned_runtime.py'
        source_hash=hashlib.sha256(wrapper.read_bytes()).hexdigest()
        reserve=json.loads((REPORT/'evidence/WATCHDOG_RESERVE.json').read_text())
        if source_hash!=entry['wrapper_sha256'] or HARD_TOTAL_CAP!=420.0 or reserve['cleanup_reserve_s']!=3.9 or reserve['term_grace_s']!=.5:
            raise ValueError('REVISED_WRAPPER_IDENTITY_OR_CONFIG_MISMATCH')
        gate['wall_guard']='PASS';gate['wrapper_sha256']=source_hash
        gate['wall_contract']={'hard_total_cap_s':420.0,'cleanup_reserve_seconds':3.9,'termination_trigger_s':416.1,'term_grace_seconds':.5,'reserve_source':'evidence/WATCHDOG_RESERVE.json'}
        display=display_now('B')
        release=Path('/etc/os-release').read_text()
        native_ubuntu='ID=ubuntu' in release or 'ID="ubuntu"' in release
        virtual=[v for v in process_rows().values() if v['argv'].split()
                 and Path(v['argv'].split()[0]).name.lower() in ('xvfb','xvnc','xtigervnc')]
        display.update(os_release=release,virtual_display_processes=virtual)
        if not native_ubuntu or virtual:display['status']='FAIL'
        save(REPORT/'evidence/PHYSICAL_DISPLAY_GATE_DECISION.json',display)
        save(REPORT/'evidence/B_FINAL_DISPLAY.json',display)
        gate['physical_display']=display['status']
        if display['status']!='PASS':raise ValueError('PHYSICAL_LOCAL_DISPLAY_NOT_VERIFIED')
        resources=collect('FINAL_BEFORE_B')
        commands_ok=all(v['exit_code']==0 for v in resources['commands'].values())
        gate['process_port']='PASS' if commands_ok and not resources['conflicting_native_processes'] and not resources['requested_port_occupancy'] else 'FAIL'
        gate['output_isolation']='PASS' if not resources['output_dirs_exist']['B'] else 'FAIL'
        compute_lines=[s for s in resources['commands']['compute']['stdout_text'].splitlines() if s.strip()]
        eligible=commands_ok and all('/opt/todesk/bin/ToDesk_Session' in s for s in compute_lines)
        gate['resource']='PENDING_REVIEW' if eligible else 'FAIL'
        effective['environment']['DISPLAY']=display['binding']['DISPLAY'];effective['environment']['XAUTHORITY']=display['binding']['XAUTHORITY']
        effective['stage3c_b_authorization_source_sha256']=entry['authorization_sha256']
        effective['stage3c_b_wall_contract']=gate['wall_contract'];effective['stage3c_b_wrapper_sha256']=source_hash
        effective['command']=shlex.join(['/usr/bin/env','-i',*[k+'='+v for k,v in sorted(effective['environment'].items())],*effective['argv']])
        save(REPORT/'B_EFFECTIVE_COMMAND.json',effective);save(REPORT/'B_EFFECTIVE_ENV.json',effective['environment'])
        (REPORT/'B_EFFECTIVE_COMMAND.txt').write_text('cwd='+effective['cwd']+'\n'+effective['command']+'\n必须通过本报告scripts/execute_b.py及逐字节修订wrapper；不得直连绕过总预算。\n')
        gate['effective_command_sha256']=hashlib.sha256((REPORT/'B_EFFECTIVE_COMMAND.json').read_bytes()).hexdigest()
        gate['status']='FAIL' if any(v=='FAIL' for v in gate.values()) else 'PENDING_RESOURCE_REVIEW'
    except (OSError,ValueError,KeyError) as exc:
        gate.update(status='FAIL',blocker=str(exc))
    save(REPORT/'B_PREFLIGHT_GATE.json',gate)
    print(json.dumps(gate,ensure_ascii=False,indent=2))
    return gate


if __name__=='__main__':
    gate=preflight()
    raise SystemExit(3 if gate['status']=='FAIL' else 0)
