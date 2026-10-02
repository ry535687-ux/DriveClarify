"""固定的三种 CPU fixture；校准后并发测试三个真实420s预算，无驾驶入口。"""
import concurrent.futures
import datetime
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

REPORT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
GRACE = .5  # TERM 协议等待期；延迟 fixture 为 .2s，忽略 TERM 需升级。


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def fixture(mode, depth):
    import signal
    if mode == 'delayed':
        def delayed_exit(sig, frame):
            time.sleep(.2)
            sys.exit(0)
        signal.signal(signal.SIGTERM, delayed_exit)
    elif mode == 'ignore':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif mode != 'fast':
        raise ValueError(mode)
    Path('pid_' + str(depth) + '.txt').write_text(str(os.getpid()))
    if depth:
        subprocess.Popen([sys.executable, '-B', str(SCRIPT), '--fixture', mode, str(depth-1)], start_new_session=True)
    else:
        port = socket.socket()
        port.bind(('127.0.0.1', 0)); port.listen()
        Path('port.txt').write_text(str(port.getsockname()[1]))
    while True:
        time.sleep(.1)


def child(spec_path):
    from owned_runtime import run_owned
    spec = json.loads(Path(spec_path).read_text())
    receipt = run_owned(spec['command'], dict(os.environ), spec['cwd'], spec['output'], spec['cap'],
                        cleanup_reserve_seconds=spec['reserve'], term_grace_seconds=GRACE,
                        reserve_source=spec['reserve_source'], native=False)
    print(json.dumps(receipt, ensure_ascii=False))
    if not receipt['strict_total_cap_met_at_receipt_assembly'] or receipt['exit_classification']=='TOTAL_CAP_OR_CLEANUP_FAILED':
        sys.exit(2)


def one(name, mode, cap, reserve, source):
    work = REPORT / 'evidence' / name
    work.mkdir(exist_ok=False)
    spec = {'command': [sys.executable, '-B', str(SCRIPT), '--fixture', mode, '2'],
            'cwd': str(work), 'output': str(work/'owned'), 'cap': cap, 'reserve': reserve, 'reserve_source': source}
    dump(work/'spec.json',spec)
    argv = [sys.executable, '-B', str(SCRIPT), '--child', str(work/'spec.json')]
    started = time.monotonic();utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cp = subprocess.run(argv, capture_output=True, text=True, timeout=cap+5)
    external_total = time.monotonic() - started
    (work/'wrapper.stdout.txt').write_text(cp.stdout); (work/'wrapper.stderr.txt').write_text(cp.stderr)
    receipt = json.loads((work/'owned/OWNED_RUNTIME_RECEIPT.json').read_text())
    pids = [int((work/('pid_'+str(d)+'.txt')).read_text()) for d in range(3)]
    children_gone = all(not Path('/proc/'+str(pid)).exists() for pid in pids)
    port = int((work/'port.txt').read_text())
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',port))
    start = receipt['start_monotonic']
    offsets = {key: (None if value is None else value-start) for key,value in receipt['watchdog'].items()
               if key.endswith('_monotonic')}
    expected = -15 if mode=='fast' else 0 if mode=='delayed' else -9
    checks = {'exit_code': receipt['exit_code']==expected, 'wrapper_exit': cp.returncode==0,
              'cleanup': receipt['cleanup_status']=='PASS' and children_gone,
              'watchdog_triggered': receipt['watchdog']['triggered'],
              'term_recorded': receipt['watchdog']['term_started_monotonic'] is not None,
              'kill_iff_ignore': (receipt['watchdog']['kill_started_monotonic'] is not None)==(mode=='ignore'),
              'all_three_births_recorded': set(pids).issubset({x['pid'] for x in receipt['owned_process_birth_identities']}),
              'receipt_strict_cap': receipt['total_wall_seconds_including_cleanup']<cap,
              'whole_wrapper_strict_cap_including_publication': external_total<cap,
              'no_signal_errors': not receipt['signal_errors']}
    result = {'name':name, 'mode':mode, 'started_utc':utc, 'argv':argv, 'process_exit':cp.returncode,
              'hard_total_cap_s':cap, 'cleanup_reserve_s':reserve, 'term_grace_s':GRACE,
              'offsets_s':offsets, 'final_cleanup_s':receipt['cleanup_finished_monotonic']-start,
              'receipt_total_s':receipt['total_wall_seconds_including_cleanup'],
              'whole_wrapper_total_s':external_total,
              'scheduled_trigger_to_complete_envelope_s':external_total-receipt['termination_trigger_seconds'],
              'termination_to_cleanup_s':receipt['termination_to_cleanup_seconds'],
              'child_cleanup':children_gone, 'dummy_port_rebind':True, 'dummy_port':port,
              'dummy_pids':pids, 'exit_classification':receipt['exit_classification'],
              'receipt':str((work/'owned/OWNED_RUNTIME_RECEIPT.json').relative_to(REPORT)),
              'checks':checks, 'passed':all(checks.values())}
    dump(work/'RESULT.json',result)
    return result


def checks():
    sentinel = subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(850)'])
    results={'status':'IN_PROGRESS','native_runs':0,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'calibration':[],'validation_420s':[]}
    try:
        for repeat in range(3):
            for mode in ['fast','delayed','ignore']:
                # 4s cap/2s provisional reserve 仅为校准刺激，不据此认证420s。
                item=one('calibration_'+mode+'_'+str(repeat),mode,4.,2.,'CALIBRATION_ONLY_NOT_QUALIFIED')
                results['calibration'].append(item)
                dump(REPORT/'WATCHDOG_DUMMY_RESULTS.json',results)
                print('calibration',item['name'],item['passed'],item['termination_to_cleanup_s'],flush=True)
                assert item['passed'],item
        envelope=max(x['scheduled_trigger_to_complete_envelope_s'] for x in results['calibration'])
        # 3倍实测最大包络向上取0.1s；额外2s来自旧wrapper原有nvidia-smi timeout。
        # 倍数为保守工程策略，2s不是CARLA teardown测得上界；CPU不调用nvidia-smi。
        reserve=math.ceil((3*envelope+2.)*10)/10
        results['reserve_selection']={'measured_max_envelope_s':envelope,'factor':3.,
             'existing_resource_query_timeout_allowance_s':2.,'round_up_s':.1,
             'cleanup_reserve_s':reserve,'term_grace_s':GRACE,
             'formula':'ceil((3*max(calibration whole_wrapper_total - scheduled_trigger)+2)*10)/10',
             'historical_carla_cleanup_upper_bound':None,
             'historical_A_cleanup_single_observation_s':0.42281375313177705}
        dump(REPORT/'evidence/WATCHDOG_RESERVE.json',results['reserve_selection'])
        dump(REPORT/'WATCHDOG_DUMMY_RESULTS.json',results)
        print('FROZEN_RESERVE',reserve,'FULL_420S_VALIDATION_STARTED',flush=True)
        # 三种有限fixture同时等待真正的420-reserve触发点，约7分钟，无模拟时钟。
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures=[executor.submit(one,'validation_420_'+mode,mode,420.,reserve,'evidence/WATCHDOG_RESERVE.json')
                     for mode in ['fast','delayed','ignore']]
            for future in concurrent.futures.as_completed(futures):
                item=future.result(); results['validation_420s'].append(item)
                dump(REPORT/'WATCHDOG_DUMMY_RESULTS.json',results)
                print('validation',item['mode'],item['passed'],item['whole_wrapper_total_s'],flush=True)
        results['unrelated_sentinel_survived']=sentinel.poll() is None
        results['status']=('WATCHDOG_CPU_CONTRACT_FIXED' if results['unrelated_sentinel_survived'] and
                           all(x['passed'] for x in results['validation_420s']) else 'FAILED')
        results['worst_420_receipt_total_s']=max(x['receipt_total_s'] for x in results['validation_420s'])
        results['worst_420_whole_wrapper_total_s']=max(x['whole_wrapper_total_s'] for x in results['validation_420s'])
    finally:
        sentinel.terminate();sentinel.wait(timeout=3)
        results['sentinel_cleaned']=True
        results['ended_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        dump(REPORT/'WATCHDOG_DUMMY_RESULTS.json',results)
    print(results['status'],flush=True)
    if results['status']!='WATCHDOG_CPU_CONTRACT_FIXED':sys.exit(1)


if __name__=='__main__':
    if len(sys.argv)==5 and sys.argv[1]=='--fixture': fixture(sys.argv[2],int(sys.argv[3]))
    elif len(sys.argv)==4 and sys.argv[1]=='--fixture': fixture(sys.argv[2],int(sys.argv[3]))
    elif len(sys.argv)==3 and sys.argv[1]=='--child': child(sys.argv[2])
    else: checks()
