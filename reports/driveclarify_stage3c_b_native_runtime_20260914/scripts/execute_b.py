"""明确授权后的B单次入口：只调用固定修订wrapper，不支持其他任务/重试。"""
import datetime
import hashlib
import json
from pathlib import Path
import time

from owned_runtime import run_owned, write_json

REPORT=Path(__file__).resolve().parents[1]


def execute():
    gate=json.loads((REPORT/'B_PREFLIGHT_GATE.json').read_text())
    required=('identity','physical_display','resource','process_port','output_isolation','wall_guard')
    if gate.get('status')!='PASS' or not gate.get('user_authorized') or not all(gate.get(k)=='PASS' for k in required):
        raise RuntimeError('B_PREFLIGHT_NOT_ALL_PASS')
    if not 0<=time.monotonic()-gate['created_monotonic']<180:
        raise RuntimeError('PREFLIGHT_STALE')
    command_path=REPORT/'B_EFFECTIVE_COMMAND.json';wrapper_path=REPORT/'scripts/owned_runtime.py'
    if hashlib.sha256(command_path.read_bytes()).hexdigest()!=gate['effective_command_sha256'] or hashlib.sha256(wrapper_path.read_bytes()).hexdigest()!=gate['wrapper_sha256']:
        raise RuntimeError('FROZEN_COMMAND_OR_REVISED_WRAPPER_CHANGED')
    effective=json.loads(command_path.read_text())
    if effective['case_id']!='DEV_NATIVE_INIT_B_TOWN07_26458' or gate['wall_contract']!=effective['stage3c_b_wall_contract']:
        raise RuntimeError('NOT_THE_AUTHORIZED_B_CASE_OR_WALL_CONTRACT')
    output=Path(effective['output_dir'])
    if output.exists() or (REPORT/'B_ATTEMPT_STARTED.json').exists():
        raise RuntimeError('B_ALREADY_EXPOSED_NO_RETRY')
    with (REPORT/'B_ATTEMPT_STARTED.json').open('x') as f:
        json.dump({'case_id':effective['case_id'],'max_attempts':1,'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'monotonic':time.monotonic(),'effective_sha256':gate['effective_command_sha256'],'wrapper_sha256':gate['wrapper_sha256']},f)
        f.flush()
        import os
        os.fsync(f.fileno())
    outer_start=time.monotonic()
    receipt=run_owned(effective['argv'],effective['environment'],effective['cwd'],output,420.0,native=True,
                      cleanup_reserve_seconds=3.9,term_grace_seconds=.5,
                      reserve_source=str(REPORT/'evidence/WATCHDOG_RESERVE.json'))
    wrapper_return=time.monotonic()
    receipt.update(case_id=effective['case_id'],effective_command_sha256=gate['effective_command_sha256'],
                   wrapper_sha256=gate['wrapper_sha256'],wrapper_file=str(wrapper_path),
                   outer_call_start_monotonic=outer_start,wrapper_return_monotonic=wrapper_return,
                   total_wall_through_wrapper_return_s=wrapper_return-outer_start,
                   runtime_total_wall_contract='PASS' if wrapper_return-outer_start<420.0 and receipt['cleanup_status']=='PASS' else 'FAIL',
                   actual_vehicle_application_independently_verified=None)
    write_json(REPORT/'B_RUN_RECEIPT.json',receipt)
    print(json.dumps({k:receipt[k] for k in ['case_id','exit_code','stop_reason','total_wall_seconds_including_cleanup','total_wall_through_wrapper_return_s','runtime_total_wall_contract','cleanup_status','evaluator_or_dummy_pid']},ensure_ascii=False,indent=2))


if __name__=='__main__':execute()
