"""既有 V2 冻结活动的持久工程 owner；科学执行仍调用 formal.main。"""
import errno
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import traceback
import psutil
import formal
from common import ROOT, SIM, OUT, PYTHON, Path, now, sha, digest, load, save
from run_route import authoritative

SERVICE = 'driveclarify_full_b2d_v2.service'
EXPECTED_FREEZE = 'e123eb22107831437881e518391c5c69087e5941b8e5ed47e815a7030aad9c6c'
POLICY = OUT/'UNATTENDED_ENGINEERING_CONTRACT.json'


class HardStop(RuntimeError):
    pass


class DiskStop(HardStop):
    pass


def identity(pid):
    try:
        p = psutil.Process(pid)
        return dict(pid=pid, create_time=p.create_time(), ppid=p.ppid(),
                    pgid=os.getpgid(pid), sid=os.getsid(pid), command=p.cmdline(),
                    status=p.status(), cgroup=Path('/proc/%s/cgroup'%pid).read_text())
    except (psutil.NoSuchProcess, ProcessLookupError):
        return dict(pid=pid, alive=False)


def acquire_lock(path):
    handle = Path(path).open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise HardStop('ACTIVE_OWNER_EXISTS:'+str(path))
    return handle


def disk_level(free, policy):
    if free < policy['disk_hard_bytes']:
        return 'HARD_STOP'
    if free < policy['disk_soft_bytes']:
        return 'SOFT_WARNING'
    return 'OK'


class Supervisor:
    def __init__(self):
        self.policy = load(POLICY)
        self.policy_sha256 = sha(POLICY) if POLICY.exists() else None
        self.freeze = load(OUT/'FULL_B2D_FREEZE_RECEIPT.json')
        self.current = None
        self.last = None
        self.phase = 'PREFLIGHT'
        self.last_disk_level = None
        self.original_run = formal.run
        self.original_reason = formal.infrastructure_reason

    def event(self, kind, **data):
        with (OUT/'UNATTENDED_SUPERVISOR.log').open('a') as stream:
            import json
            stream.write(json.dumps(dict(timestamp=now(), event=kind, **data),
                                    ensure_ascii=False, sort_keys=True)+'\n')
            stream.flush()

    def ledger_snapshot(self):
        ledgers = {a:load(OUT/(a+'_EXECUTION_LEDGER.json')) for a in ('A0','A1')}
        counts = {a:sum(e['status']=='AUTHORITATIVE' for e in l['entries']) for a,l in ledgers.items()}
        for a,l in ledgers.items():
            if len(l['entries']) != 220 or l['authoritative_completed'] != counts[a]:
                raise HardStop('LEDGER_COUNT_INTEGRITY:'+a)
        pending = None
        for pair in self.freeze['pair_order']:
            for arm in pair['arm_order']:
                e = ledgers[arm]['entries'][pair['canonical_index']]
                if e['route_id'] != pair['route_id']:
                    raise HardStop('LEDGER_MANIFEST_ORDER_CHANGED')
                if e['status'] != 'AUTHORITATIVE' and pending is None:
                    pending = dict(route_id=pair['route_id'], arm=arm, canonical_index=pair['canonical_index'])
        return counts, pending

    def state(self, hard_stop=False, reason=None):
        counts, pending = self.ledger_snapshot()
        retry = load(OUT/'TECHNICAL_RETRY_LEDGER.json',{})
        free = shutil.disk_usage(OUT).free
        data = dict(timestamp=now(), supervisor_pid=os.getpid(), service=SERVICE,
                    freeze_digest=EXPECTED_FREEZE, result_root=str(OUT), phase=self.phase,
                    A0_authoritative=counts['A0'], A0_expected=220,
                    A1_authoritative=counts['A1'], A1_expected=220,
                    total_authoritative=sum(counts.values()), total_expected=440,
                    current_route=self.current, next_route=pending,
                    technical_retry_total=retry.get('total_technical_retries',0),
                    formal_technical_retries=retry.get('formal_technical_retries',0),
                    qualification_technical_retries=retry.get('qualification_technical_retries',0),
                    disk_free_bytes=free, disk_guard=disk_level(free,self.policy),
                    last_completed_route_arm=self.last, hard_stop=hard_stop, reason=reason,
                    process_health_path=(str(Path(self.current['output'])/'process_heartbeat.json')
                                         if self.current else None))
        save(OUT/'UNATTENDED_SUPERVISOR_STATE.json',data)
        return data

    def verify(self, checkpoint=True):
        if self.policy is None or self.freeze is None:
            raise HardStop('EXISTING_FREEZE_AND_ENGINEERING_CONTRACT_REQUIRED')
        if getattr(self,'policy_sha256',None) and sha(POLICY)!=self.policy_sha256:
            raise HardStop('ENGINEERING_GUARD_CONTRACT_CHANGED')
        frozen = load(OUT/'FULL_B2D_FREEZE_RECEIPT.json')
        if (sha(OUT/'FULL_B2D_FREEZE_RECEIPT.json') != self.policy['freeze_file_sha256']
                or frozen.get('freeze_digest') != EXPECTED_FREEZE
                or digest({k:v for k,v in frozen.items() if k!='freeze_digest'}) != EXPECTED_FREEZE):
            raise HardStop('FROZEN_CAMPAIGN_CHANGED')
        formal.verify_scientific_sources(frozen)
        expected = dict(frozen['scientific_files'])
        expected.update(self.policy['engineering_files'])
        expected.update({str(OUT/'FULL_B2D_ROUTE_MANIFEST.json'):frozen['route_manifest_sha256'],
                         str(OUT/'TRANSPARENT_BYPASS_ACTIVATION_CONTRACT.json'):frozen['activation_contract_sha256'],
                         str(OUT/'BENCHMARK_VERSION_AUDIT.json'):frozen['benchmark_version_audit_sha256'],
                         frozen['evaluator']['path']:frozen['evaluator']['sha256']})
        expected.update({v['path']:v['sha256'] for v in frozen['metric_scripts'].values()})
        manifest = load(OUT/'FULL_B2D_ROUTE_MANIFEST.json')
        expected[manifest['path']] = frozen['official_XML_sha256']
        expected.update({p['route_path']:p['route_sha256'] for p in frozen['pair_order']})
        if checkpoint:
            expected[frozen['checkpoint']['path']] = frozen['checkpoint']['sha256']
        for path, value in expected.items():
            if not Path(path).is_file() or sha(path) != value:
                raise HardStop('SOURCE_OR_ARTIFACT_HASH_CHANGED:'+path)
        for repo,key in ((ROOT,'DriveClarify_HEAD'),(SIM,'SimLingo_HEAD')):
            actual = subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
            if actual != frozen['source_HEADs'][key]:
                raise HardStop('SOURCE_HEAD_CHANGED:'+str(repo))

    def disk_guard(self):
        free = shutil.disk_usage(OUT).free
        level = disk_level(free,self.policy)
        if level != self.last_disk_level and level != 'OK':
            self.event('DISK_'+level, disk_free_bytes=free)
        self.last_disk_level = level
        if level == 'HARD_STOP':
            save(OUT/'DISK_SPACE_HARD_STOP.json',dict(timestamp=now(), disk_free_bytes=free,
                 hard_bytes=self.policy['disk_hard_bytes'], result_root=str(OUT),
                 action='保留全部数据，不再启动下一条路线。'))
            raise DiskStop('RESULT_FILESYSTEM_BELOW_FROZEN_ENGINEERING_GUARD')

    def before_item(self, freeze, pair, arm, entry):
        # 每项读取原账本，并校验唯一权威结果；坏分数从不进入重试判定。
        self.verify(checkpoint=False)
        outputs = [p for p in (OUT/'formal'/pair['route_id']/arm).glob('attempt_*')
                   if authoritative(p/'official_checkpoint.json') is not None]
        if len(outputs) > 1:
            save(OUT/'DUPLICATE_AUTHORITY_ADJUDICATION_REQUIRED.json',dict(
                route_id=pair['route_id'], arm=arm, outputs=list(map(str,outputs))))
            raise HardStop('DUPLICATE_AUTHORITATIVE_RESULTS')
        if outputs:
            record = authoritative(outputs[0]/'official_checkpoint.json')
            spec = load(outputs[0]/'RUN_SPEC.json',{})
            if (record['route_id']!='RouteScenario_'+pair['route_id']+'_rep0'
                    or spec.get('arm')!=arm or spec.get('seed')!=pair['seed']
                    or spec.get('route_sha256')!=pair['route_sha256']):
                raise HardStop('AUTHORITATIVE_ROUTE_ARM_PROVENANCE_MISMATCH')
        if entry['status']=='AUTHORITATIVE':
            if len(outputs)!=1 or str(outputs[0])!=entry['authoritative_output']:
                raise HardStop('AUTHORITATIVE_LEDGER_OUTPUT_MISMATCH')
            if sha(outputs[0]/'official_checkpoint.json')!=entry['raw_sha256']:
                raise HardStop('AUTHORITATIVE_RESULT_HASH_CHANGED')
            terminal = load(outputs[0]/'agent_terminal.json',{})
            if not terminal.get('forward_count_contract') or terminal.get('violations'):
                raise HardStop('CONTROL_INTEGRITY_FAILURE')

    def reason(self, output):
        path = Path(output)
        # 首个权威结果优先，错误字符串不能使有效科学失败进入重试。
        if authoritative(path/'official_checkpoint.json') is not None:
            return None
        reason = self.original_reason(path) if (path/'evaluator.log').exists() else None
        if reason is None:
            adjudication = load(path/'UNATTENDED_INFRASTRUCTURE_RECEIPT.json',{})
            if adjudication.get('conclusive_prelaunch_failure'):
                reason = adjudication.get('reason')
        if reason is None and (path/'evaluator.log').exists():
            try:
                data = load(path/'official_checkpoint.json',{})
            except (ValueError,OSError):
                return None
            records = data.get('_checkpoint',{}).get('records',[])
            text = (path/'evaluator.log').read_text(errors='replace')
            if (len(records)==1 and records[0].get('status')=="Failed - Agent couldn't be set up"
                    and records[0].get('meta',{}).get('duration_game')==0
                    and records[0].get('meta',{}).get('duration_system')==0
                    and 'CUDA driver initialization failed' in text
                    and 'torch._C._cuda_init()' in text
                    and not (path/'agent_setup.json').exists()
                    and not (path/'agent_terminal.json').exists()):
                return 'CUDA_DRIVER_INITIALIZATION_FAILED_BEFORE_DRIVING'
        return reason

    def reviewed_first_forward_memory_failure(self, output, pair, arm):
        # 仅恢复逐次人工根因裁定并由工程契约绑定的旧失败；新 OOM 仍停止。
        path = Path(output)
        binding = self.policy.get('reviewed_first_forward_memory_failures',{}).get(str(path))
        if binding is None:
            return False
        receipt_path = Path(binding['path'])
        if not receipt_path.is_file() or sha(receipt_path) != binding['sha256']:
            raise HardStop('MEMORY_ADJUDICATION_HASH_CHANGED')
        receipt = load(receipt_path)
        required = ('RUN_SPEC.json','official_checkpoint.json','agent_setup.json',
                    'agent_terminal.json','evaluator.log','PROCESS_RECEIPT.json')
        evidence = receipt.get('evidence_sha256',{})
        if any(name not in evidence or not (path/name).is_file()
               or sha(path/name) != evidence[name] for name in required):
            raise HardStop('MEMORY_FAILURE_EVIDENCE_CHANGED')
        spec = load(path/'RUN_SPEC.json')
        terminal = load(path/'agent_terminal.json')
        records = load(path/'official_checkpoint.json').get('_checkpoint',{}).get('records',[])
        probe_path = Path(receipt.get('cuda_probe_path',''))
        valid = (
            receipt.get('freeze_digest') == EXPECTED_FREEZE
            and receipt.get('output') == str(path)
            and receipt.get('route_id') == pair['route_id']
            and receipt.get('arm') == arm
            and receipt.get('attempt') == int(path.name.split('_')[-1])
            and receipt.get('conclusive_infrastructure_failure') is True
            and receipt.get('no_authoritative_result') is True
            and receipt.get('scientific_configuration_unchanged') is True
            and receipt.get('previous_owned_processes_exited') is True
            and spec.get('arm') == arm and spec.get('scope') == 'FORMAL'
            and spec.get('seed') == pair['seed']
            and spec.get('route_sha256') == pair['route_sha256']
            and authoritative(path/'official_checkpoint.json') is None
            and len(records) == 1
            and records[0].get('route_id') == 'RouteScenario_'+pair['route_id']+'_rep0'
            and records[0].get('status') == 'Failed - Agent crashed'
            and records[0].get('meta',{}).get('duration_game') == 0.1
            and terminal.get('native_step_final') == 1
            and terminal.get('model_pre_count') == 1
            and terminal.get('model_post_count') == 0
            and terminal.get('PID_count') == 0
            and terminal.get('violations') == []
            and 'torch.cuda.OutOfMemoryError: CUDA out of memory.'
                in (path/'evaluator.log').read_text(errors='replace')
            and probe_path.is_file()
            and sha(probe_path) == receipt.get('cuda_probe_sha256')
            and load(probe_path).get('pass_') is True)
        if not valid:
            raise HardStop('MEMORY_ADJUDICATION_NOT_FIRST_FORWARD_INFRASTRUCTURE')
        self.event('REVIEWED_INFRASTRUCTURE_RESUME',output=str(path),
                   adjudication=str(receipt_path),existing_attempt_budget_unchanged=True)
        return True

    def reviewed_setup_memory_failure(self, output, pair, arm):
        path = Path(output)
        binding = self.policy.get('reviewed_setup_memory_failures',{}).get(str(path))
        if binding is None:
            return False
        receipt_path = Path(binding['path'])
        if not receipt_path.is_file() or sha(receipt_path) != binding['sha256']:
            raise HardStop('SETUP_MEMORY_ADJUDICATION_HASH_CHANGED')
        receipt = load(receipt_path)
        required = ('RUN_SPEC.json','official_checkpoint.json','evaluator.log','PROCESS_RECEIPT.json')
        evidence = receipt.get('evidence_sha256',{})
        if any(name not in evidence or not (path/name).is_file()
               or sha(path/name) != evidence[name] for name in required):
            raise HardStop('SETUP_MEMORY_FAILURE_EVIDENCE_CHANGED')
        spec = load(path/'RUN_SPEC.json')
        records = load(path/'official_checkpoint.json').get('_checkpoint',{}).get('records',[])
        recovery_path = Path(receipt.get('resource_recovery_path',''))
        valid = (
            receipt.get('freeze_digest') == EXPECTED_FREEZE
            and receipt.get('phase') == 'AGENT_SETUP'
            and receipt.get('output') == str(path)
            and receipt.get('route_id') == pair['route_id'] and receipt.get('arm') == arm
            and receipt.get('attempt') == int(path.name.split('_')[-1])
            and receipt.get('conclusive_infrastructure_failure') is True
            and receipt.get('no_authoritative_result') is True
            and receipt.get('scientific_configuration_unchanged') is True
            and receipt.get('previous_owned_processes_exited') is True
            and spec.get('arm') == arm and spec.get('scope') == 'FORMAL'
            and spec.get('seed') == pair['seed'] and spec.get('route_sha256') == pair['route_sha256']
            and authoritative(path/'official_checkpoint.json') is None
            and len(records) == 1
            and records[0].get('route_id') == 'RouteScenario_'+pair['route_id']+'_rep0'
            and records[0].get('status') == "Failed - Agent couldn't be set up"
            and records[0].get('meta',{}).get('duration_game') == 0
            and records[0].get('meta',{}).get('duration_system') == 0
            and not (path/'agent_setup.json').exists()
            and not (path/'agent_terminal.json').exists()
            and 'torch.cuda.OutOfMemoryError: CUDA out of memory.'
                in (path/'evaluator.log').read_text(errors='replace')
            and recovery_path.is_file()
            and sha(recovery_path) == receipt.get('resource_recovery_sha256')
            and load(recovery_path).get('pass_') is True
            and load(recovery_path).get('scientific_render_settings_unchanged') is True)
        if not valid:
            raise HardStop('SETUP_MEMORY_ADJUDICATION_NOT_PRE_DRIVING_INFRASTRUCTURE')
        self.event('REVIEWED_SETUP_MEMORY_RESUME',output=str(path),
                   adjudication=str(receipt_path),existing_attempt_budget_unchanged=True)
        return True

    def before_resume(self, pair, arm, old):
        # 原 runner 恢复入口会跳过旧目录；先补齐同一冻结政策要求的证据。
        for output in old:
            if authoritative(output/'official_checkpoint.json') is not None:
                raise HardStop('AUTHORITATIVE_RESULT_MUST_BE_RECOVERED_WITHOUT_RERUN')
            heartbeat = load(output/'process_heartbeat.json',{})
            pid = heartbeat.get('evaluator_pid')
            if pid and identity(pid).get('alive') is not False:
                actual = identity(pid)
                if any(str(output) in token for token in actual.get('command',[])):
                    raise HardStop('LIVE_OLD_EVALUATOR_REQUIRES_FINISH_NO_DUPLICATE')
            reason = self.reason(output)
            if reason is None or ('OOM' in reason and not (
                    self.reviewed_first_forward_memory_failure(output,pair,arm)
                    or self.reviewed_setup_memory_failure(output,pair,arm))):
                raise HardStop('EXISTING_ATTEMPT_REQUIRES_ROOT_CAUSE:'+str(output))
            retry = load(OUT/'TECHNICAL_RETRY_LEDGER.json')
            if not any(e['output']==str(output) for e in retry['entries']):
                exceptions = load(OUT/'TECHNICAL_ATTEMPT_EXCEPTIONS.json',{'entries':[]})
                budget = max([self.freeze['technical_retry_policy']['max_default_attempts']]+
                    [e['max_attempts'] for e in exceptions['entries'] if e['route_id']==pair['route_id']
                     and e['arm']==arm and e['scientific_configuration_unchanged'] is True
                     and e['infrastructure_proven'] is True])
                retry['entries'].append(dict(route_id=pair['route_id'],arm=arm,
                    attempt=int(output.name.split('_')[-1]), output=str(output), reason=reason,
                    no_authoritative_result=True, retry_allowed=len(old)<budget, recovered_index=True))
                save(OUT/'TECHNICAL_RETRY_LEDGER.json',retry)
                self.event('TECHNICAL_FAILURE_INDEX_RECOVERED',output=str(output),reason=reason)

    def before_attempt(self, pair, arm, attempt):
        self.verify(checkpoint=True)
        self.disk_guard()
        # 与 frozen formal.py 的顺序及 attempt budget 完全相同。
        self.current = dict(route_id=pair['route_id'], arm=arm,
                            canonical_index=pair['canonical_index'], output=str(attempt),
                            attempt=int(attempt.name.split('_')[-1]))
        self.phase = 'RUNNING'
        transfer = load(OUT/'UNATTENDED_OWNER_TRANSFER_RECEIPT.json')
        if transfer.get('first_route_under_unattended_owner') is None:
            transfer['first_route_under_unattended_owner'] = self.current
            transfer['first_route_start_timestamp'] = now()
            save(OUT/'UNATTENDED_OWNER_TRANSFER_RECEIPT.json',transfer)
        self.event('LEGAL_RETRY' if self.current['attempt']>1 else 'ROUTE_START',**self.current)
        self.state()

    def guarded_run(self, arm, route, seed, port, output, qualification):
        try:
            return self.original_run(arm,route,seed,port,output,qualification)
        except Exception as exc:
            # 只自动归类有明确 OS 证据的启动失败；不把模型/策略异常泛化为技术重试。
            reason = None
            if str(exc)=='ISOLATED_DYNAMIC_PORT_POOL_EXHAUSTED':
                reason = 'PORT_COLLISION'
            elif isinstance(exc,OSError) and exc.errno in (errno.EADDRINUSE,errno.ECONNREFUSED,errno.EAGAIN,errno.ETIMEDOUT):
                reason = 'CONCLUSIVE_OS_LAUNCHER_FAILURE'
            if reason is None:
                raise
            output = Path(output)
            if (output/'process_heartbeat.json').exists():
                raise HardStop('RUN_EXCEPTION_AFTER_PROCESS_START_REQUIRES_ADJUDICATION')
            output.mkdir(exist_ok=True,parents=True)
            raw = output/'official_checkpoint.json'
            record = authoritative(raw)
            save(output/'UNATTENDED_INFRASTRUCTURE_RECEIPT.json',dict(timestamp=now(),
                 reason=reason, exception=repr(exc), conclusive_prelaunch_failure=True,
                 no_authoritative_result=record is None, scientific_configuration_changed=False))
            result = dict(exit_code=None,watchdog=None,wall_seconds=0,authoritative=record is not None,
                          raw_json_path=str(raw) if raw.exists() else None,
                          raw_json_sha256=sha(raw) if raw.exists() else None,
                          finished_utc=now(),owned_carla_pids=[],prelaunch_failure=reason)
            save(output/'PROCESS_RECEIPT.json',result)
            return result

    def technical_failure(self, entry):
        self.event('TECHNICAL_FAILURE',**entry)
        self.state()

    def after_authoritative(self, pair, arm, output):
        if not (output/'PROCESS_RECEIPT.json').exists():
            save(output/'AUTHORITATIVE_INDEX_RECOVERY_RECEIPT.json',dict(timestamp=now(),
                 raw_sha256=sha(output/'official_checkpoint.json'),
                 terminal_sha256=sha(output/'agent_terminal.json'),
                 process_exit_code=None,process_exit_code_unobserved=True,
                 authoritative_result_preserved=True,native_execution_repeated=False,
                 reason='原 owner 已退出；现有官方终结结果及完整控制收据满足原权威定义，补登记索引。'))
        self.last = dict(route_id=pair['route_id'],arm=arm,output=str(output))
        self.current = None
        self.phase = 'BETWEEN_ROUTES'
        self.event('ROUTE_COMPLETE',**self.last)
        self.state()

    def before_analysis(self):
        self.verify(checkpoint=True)
        counts,_ = self.ledger_snapshot()
        if counts != {'A0':220,'A1':220}:
            raise HardStop('INCOMPLETE_AUTHORITY_COVERAGE')
        self.current = None
        self.phase = 'OFFICIAL_AGGREGATION'
        self.event('ALL_440_READY_FOR_FROZEN_ANALYSIS')
        self.state()

    def complete(self):
        validation = load(OUT/'FINAL_VALIDATION_RECEIPT.json',{})
        counts,_ = self.ledger_snapshot()
        if (counts != {'A0':220,'A1':220}
                or validation.get('status')!='PASS_TRANSPARENT_BYPASS_AND_FULL_BENCH2DRIVE_COMPLETE'
                or validation.get('freeze_digest')!=EXPECTED_FREEZE
                or validation.get('official_merge_counts')!=[220,220]):
            raise HardStop('FINAL_VALIDATION_NOT_COMPLETE')
        self.phase = 'COMPLETE'
        data = self.state()
        save(OUT/'UNATTENDED_EXECUTION_COMPLETE.json',dict(
            finish_timestamp=now(),A0_authoritative=220,A1_authoritative=220,total_authoritative=440,
            technical_retry_count=data['technical_retry_total'],
            formal_technical_retry_count=data['formal_technical_retries'],freeze_digest=EXPECTED_FREEZE,
            final_results={n:str(OUT/n) for n in ['FINAL_REPORT.md','FINAL_VALIDATION_RECEIPT.json',
                'FINAL_ARTIFACT_AUDIT.json','A0_OFFICIAL_MERGED_RESULTS.json','A1_OFFICIAL_MERGED_RESULTS.json',
                'FULL_B2D_OFFICIAL_COMPARISON.json','TECHNICAL_RETRY_LEDGER.json']},
            scientific_native_processes_cleaned_by='run_route.run finally; analyze.invoke finally'))
        self.event('FINAL_COMPLETION',total=440)

    def stop(self, exc):
        self.phase = 'HARD_STOP'
        receipt = dict(timestamp=now(),reason=str(exc),supervisor=identity(os.getpid()),
                       freeze_digest=EXPECTED_FREEZE,result_root=str(OUT),
                       no_new_routes=True,existing_data_preserved=True,
                       exit_code=78 if isinstance(exc,DiskStop) else 70,
                       traceback=traceback.format_exc())
        save(OUT/'HARD_STOP.json',receipt)
        self.event('HARD_STOP',reason=str(exc),exit_code=receipt['exit_code'])
        try:
            self.state(hard_stop=True,reason=str(exc))
        except Exception as state_error:
            save(OUT/'UNATTENDED_SUPERVISOR_STATE.json',dict(timestamp=now(),hard_stop=True,
                 phase='HARD_STOP',reason=str(exc),state_read_error=repr(state_error)))
        return receipt['exit_code']


def main():
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    # 先取得两把锁；拒绝第二 owner 时不覆盖当前服务的状态。
    try:
        unattended_lock = acquire_lock(OUT/'.unattended_supervisor.lock')
        formal_lock = acquire_lock(OUT/'.formal_supervisor.lock')
    except HardStop as exc:
        print(str(exc),file=sys.stderr,flush=True)
        return 73
    try:
        supervisor = Supervisor()
    except Exception as exc:
        save(OUT/'HARD_STOP.json',dict(timestamp=now(),reason='SUPERVISOR_CONTRACT_UNREADABLE:'+repr(exc),
             exit_code=70,existing_data_preserved=True,no_new_routes=True))
        return 70
    try:
        if (OUT/'HARD_STOP.json').exists() or (OUT/'DISK_SPACE_HARD_STOP.json').exists():
            raise HardStop('EXISTING_HARD_STOP_REQUIRES_EXPLICIT_ADJUDICATION')
        supervisor.verify()
        # 拒绝任何本活动残留 native evaluator；不能靠杀健康路线实现转移。
        others=[]
        for p in psutil.process_iter(['pid','cmdline']):
            cmd=p.info['cmdline'] or []
            if any('leaderboard_evaluator.py' in x for x in cmd) and any(str(OUT/'formal') in x for x in cmd):
                others.append(identity(p.pid))
        if others:
            raise HardStop('EXISTING_NATIVE_EVALUATOR_STILL_ALIVE:'+repr(others))
        discovery = load(OUT/'audit/unattended_transition/DISCOVERY.json')
        previous_launch = load(OUT/'UNATTENDED_LAUNCH_RECEIPT.json',{})
        previous_state = load(OUT/'UNATTENDED_SUPERVISOR_STATE.json',{})
        old_owner = ({'supervisor':previous_launch['supervisor']} if previous_launch
                     else discovery['old_owner'])
        last_old_route = (previous_state.get('last_completed_route_arm')
                          or discovery['last_route_under_old_owner'])
        owner = identity(os.getpid())
        launch = dict(timestamp=now(),mechanism='systemd --user with linger=yes',service=SERVICE,
            supervisor=owner,supervisor_pid=os.getpid(),freeze_digest=EXPECTED_FREEZE,
            freeze_file_sha256=sha(OUT/'FULL_B2D_FREEZE_RECEIPT.json'),result_root=str(OUT),
            engineering_contract_sha256=sha(POLICY),formal_lock=str(OUT/'.formal_supervisor.lock'),
            unattended_lock=str(OUT/'.unattended_supervisor.lock'),
            manifest=str(OUT/'FULL_B2D_ROUTE_MANIFEST.json'),
            ledgers=[str(OUT/(a+'_EXECUTION_LEDGER.json')) for a in ('A0','A1')],
            retry_ledger=str(OUT/'TECHNICAL_RETRY_LEDGER.json'),initial_state=supervisor.state())
        save(OUT/'UNATTENDED_LAUNCH_RECEIPT.json',launch)
        save(OUT/'UNATTENDED_OWNER_TRANSFER_RECEIPT.json',dict(timestamp=now(),
             old_owner=old_owner,new_owner=owner,
             last_route_under_old_owner=last_old_route,
             last_attempt_under_old_owner=previous_state.get('current_route'),
             first_route_under_unattended_owner=None,duplicate_formal_executions=0,
             proof='旧进程均已退出；独占两把 flock；发现的旧权威结果按原 formal.py 恢复登记，绝不重跑。',
             no_healthy_route_terminated=True,freeze_digest=EXPECTED_FREEZE))
        supervisor.event('OWNER_TRANSFER',supervisor_pid=os.getpid(),service=SERVICE)
        formal.run = supervisor.guarded_run
        formal.infrastructure_reason = supervisor.reason
        result = formal.main(supervisor_lock=formal_lock,hooks=supervisor)
        if result:
            raise HardStop('FROZEN_FORMAL_RUNNER_STOP_CODE:'+str(result))
        supervisor.complete()
        return 0
    except Exception as exc:
        return supervisor.stop(exc)


if __name__=='__main__':
    sys.exit(main())
