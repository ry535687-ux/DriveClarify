"""本次已授权恢复：CUDA验证通过才归档既有硬停止并启动同一个服务。"""
import json
import os
import shutil
import subprocess
import sys
import time
import psutil
import unattended as u
from common import OUT, ROOT, PYTHON, load, save, sha, now

AUDIT=OUT/'audit/cuda_init_recovery_20260907'
ATTEMPT=OUT/'formal/2164/A0/attempt_01'


def main():
    if os.geteuid()==0:
        raise RuntimeError('以 buaa 用户运行；只允许 modprobe 子命令使用 sudo。')
    # 在驱动检查及归档期间独占两把原锁。
    with u.acquire_lock(OUT/'.unattended_supervisor.lock') as outer, \
         u.acquire_lock(OUT/'.formal_supervisor.lock') as formal_lock:
        supervisor=u.Supervisor()
        supervisor.verify(checkpoint=True)
        if supervisor.ledger_snapshot()[0]!={'A0':18,'A1':18}:
            raise RuntimeError('CAMPAIGN_ADVANCED_OR_CHANGED_REVIEW_REQUIRED')
        if (OUT/'DISK_SPACE_HARD_STOP.json').exists():
            raise RuntimeError('DISK_HARD_STOP_NOT_AUTHORIZED_BY_THIS_RECOVERY')
        supervisor.disk_guard()
        for p in psutil.process_iter(['pid','cmdline']):
            cmd=p.info['cmdline'] or []
            if any(x.endswith('/leaderboard_evaluator.py') for x in cmd) and any(str(OUT/'formal') in x for x in cmd):
                raise RuntimeError('LIVE_EVALUATOR_NO_RECOVERY_ALLOWED')
        original=AUDIT/'HARD_STOP.json.before.txt'
        if not (OUT/'HARD_STOP.json').exists() or sha(OUT/'HARD_STOP.json')!=sha(original):
            raise RuntimeError('HARD_STOP_IS_NOT_THE_ADJUDICATED_CUDA_FAILURE')
        if supervisor.reason(ATTEMPT)!='CUDA_DRIVER_INITIALIZATION_FAILED_BEFORE_DRIVING':
            raise RuntimeError('ATTEMPT_NO_LONGER_MATCHES_CONCLUSIVE_SETUP_FAILURE')
        evidence=load(AUDIT/'DIAGNOSIS.json')
        for row in evidence['existing_authoritative_results']:
            if sha(row['path'])!=row['sha256']:
                raise RuntimeError('PREVIOUS_AUTHORITATIVE_RESULT_CHANGED')
        stamp=now().replace(':','-')
        with (AUDIT/('CUDA_PROBE_'+stamp+'.json')).open('w') as stream:
            result=subprocess.run([PYTHON,'-B',str(OUT/'tooling/cuda_readiness_probe.py')],
                                   stdout=stream,stderr=subprocess.STDOUT,timeout=45)
        probe_path=AUDIT/('CUDA_PROBE_'+stamp+'.json')
        if result.returncode or not load(probe_path).get('pass_'):
            raise RuntimeError('CUDA_STILL_UNAVAILABLE_NO_FORMAL_ATTEMPT_CONSUMED:'+str(probe_path))
        archive=AUDIT/('resumption_'+stamp)
        archive.mkdir()
        names=['HARD_STOP.json','ENGINEERING_ATTENTION.json','UNATTENDED_SUPERVISOR_STATE.json',
               'UNATTENDED_LAUNCH_RECEIPT.json','UNATTENDED_OWNER_TRANSFER_RECEIPT.json',
               'TECHNICAL_RETRY_LEDGER.json']
        for name in names:
            if (OUT/name).exists():shutil.copyfile(OUT/name,archive/name)
        adjudication=dict(timestamp=now(),route_id='2164',arm='A0',attempt=1,
             reason='CUDA_DRIVER_INITIALIZATION_FAILED_BEFORE_DRIVING',
             phase='AGENT_SETUP',conclusive_infrastructure_failure=True,
             no_authoritative_result=True,scientific_configuration_unchanged=True,
             raw_sha256=sha(ATTEMPT/'official_checkpoint.json'),
             evaluator_log_sha256=sha(ATTEMPT/'evaluator.log'),
             cuda_probe_pass_receipt=str(probe_path),next_attempt=2,max_attempts=3,
             freeze_digest=u.EXPECTED_FREEZE,previous_classification_archive=str(archive))
        save(ATTEMPT/'INFRASTRUCTURE_ADJUDICATION.json',adjudication)
        retry=load(OUT/'TECHNICAL_RETRY_LEDGER.json')
        row=next(x for x in retry['entries'] if x['output']==str(ATTEMPT))
        row.update(reason=adjudication['reason'],retry_allowed=True,
                   adjudication=str(ATTEMPT/'INFRASTRUCTURE_ADJUDICATION.json'),
                   prior_unknown_classification_preserved=str(archive/'TECHNICAL_RETRY_LEDGER.json'))
        save(OUT/'TECHNICAL_RETRY_LEDGER.json',retry)
        save(AUDIT/'RESUME_AUTHORIZATION_RECEIPT.json',dict(timestamp=now(),
             user_instruction='评测任务继续',freeze_digest=u.EXPECTED_FREEZE,
             previous_authoritative_results=36,no_authoritative_result_rerun=True,
             source_and_checkpoint_hashes_match=True,cuda_probe_pass=True,
             next_item=dict(route_id='2164',arm='A0',attempt=2),
             existing_retry_budget_preserved=True,hard_stop_archive=str(archive)))
        # 原硬停止完整保留在归档；只有上述验证全部通过后才解除根目录启动门控。
        (OUT/'HARD_STOP.json').rename(archive/'HARD_STOP.active_original.json')
        if (OUT/'ENGINEERING_ATTENTION.json').exists():
            (OUT/'ENGINEERING_ATTENTION.json').rename(archive/'ENGINEERING_ATTENTION.active_original.json')
        with (OUT/'COMMAND_LOG.md').open('a') as stream:
            stream.write('\n- '+now()+' CUDA独立验证通过；归档原硬停止并按既有三次预算恢复2164/A0 attempt_02，36份权威结果与科学冻结保持原哈希。\n')
    subprocess.run(['systemctl','--user','reset-failed',u.SERVICE],check=True)
    subprocess.run(['bash',str(ROOT/'scripts/run_full_bench2drive_unattended.sh')],check=True)
    deadline=time.monotonic()+150
    while time.monotonic()<deadline:
        if (OUT/'HARD_STOP.json').exists():
            raise RuntimeError('RESUMED_SERVICE_HARD_STOP:'+str(OUT/'HARD_STOP.json'))
        state=load(OUT/'UNATTENDED_SUPERVISOR_STATE.json',{})
        current=state.get('current_route') or {}
        if current.get('route_id')=='2164' and current.get('arm')=='A0' and current.get('attempt')==2:
            setup=load(Path(current['output'])/'agent_setup.json',{})
            heartbeat=load(Path(current['output'])/'process_heartbeat.json',{})
            if setup and heartbeat.get('evaluator_pid') and psutil.pid_exists(heartbeat['evaluator_pid']):
                pid=state['supervisor_pid']
                if psutil.Process(heartbeat['evaluator_pid']).ppid()!=pid:
                    raise RuntimeError('RESUMED_PROCESS_OWNER_MISMATCH')
                save(AUDIT/'RESUMED_SERVICE_VERIFICATION.json',dict(timestamp=now(),pass_=True,
                     supervisor_pid=pid,evaluator_pid=heartbeat['evaluator_pid'],
                     carla_pids=heartbeat['carla_pids'],current=current,model_setup_pass=True,
                     previous_authoritative_results=36,freeze_digest=u.EXPECTED_FREEZE))
                print('恢复成功：同一个后台服务已完成模型初始化，继续2164/A0的第2次技术尝试。',flush=True)
                return 0
        time.sleep(3)
    print('后台服务已启动；本次限时核验尚未观察到模型setup完成，请查看状态文件。',flush=True)
    return 0


if __name__=='__main__':
    from pathlib import Path
    sys.exit(main())
