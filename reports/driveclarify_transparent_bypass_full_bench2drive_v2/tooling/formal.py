"""通过资格后签发新freeze并顺序完成440条；首个权威结果不重跑。"""
import os
import fcntl
import subprocess
import sys
import time
from common import *
from run_route import run,authoritative

FORMAL_SEED=902609062


def verify_scientific_sources(freeze):
    for path,expected in freeze['scientific_files'].items():
        if not Path(path).is_file() or sha(path)!=expected:raise RuntimeError('SOURCE_INTEGRITY_FAILURE:'+path)
    p=Path(freeze['checkpoint']['path']);stat=p.stat()
    if stat.st_size!=freeze['checkpoint']['size'] or stat.st_mtime_ns!=freeze['checkpoint']['mtime_ns']:
        raise RuntimeError('CHECKPOINT_STAT_CHANGED_REQUIRES_HASH_AUDIT')


def make_freeze():
    existing=load(OUT/'FULL_B2D_FREEZE_RECEIPT.json')
    if existing:
        assert existing['formal_freeze_issued'];verify_scientific_sources(existing);return existing
    q=load(OUT/'TRANSPARENCY_QUALIFICATION_RECEIPT.json',{})
    assert q.get('pass') and q.get('qualified_routes')==8 and q.get('native_executions')==16, 'QUALIFICATION_NOT_PASSED'
    assert sha(OUT/'TRANSPARENCY_QUALIFICATION_RESULTS.json')==q['results_sha256']
    for p,h in q['interface_sources'].items():assert sha(p)==h,'QUALIFIED_INTERFACE_CHANGED'
    regression=load(OUT/'ACTIVE_PATH_REGRESSION_RECEIPT.json')
    assert regression['pass']
    for p,h in regression['sources'].items():assert sha(p)==h,'REGRESSION_INTERFACE_CHANGED_AFTER_QUALIFICATION_START'
    protected=load(OUT/'audit/PROTECTED_BEFORE.json')['files']
    mismatches=[r['path'] for r in protected if sha(r['path'])!=r['sha256']]
    assert not mismatches, mismatches
    scientific={r['path']:r['sha256'] for r in protected if Path(r['path']).suffix in ('.py','.yaml') and '/reports/' not in r['path']}
    for p,h in q['interface_sources'].items():scientific[p]=h
    scientific[str(OUT/'interface_config.json')]=sha(OUT/'interface_config.json')
    scientific[str(OUT/'carla_bootstrap_proxy/CarlaUE4.sh')]=sha(OUT/'carla_bootstrap_proxy/CarlaUE4.sh')
    # Hydra模型配置属于科学冻结，不随engineer-wrapper恢复改变。
    cp=load(OUT/'BENCHMARK_VERSION_AUDIT.json')['formal_checkpoint']
    audit=load(OUT/'BENCHMARK_VERSION_AUDIT.json')
    for repository,key in ((ROOT,'DriveClarify_HEAD'),(SIM,'SimLingo_HEAD')):
        assert subprocess.check_output(['git','-C',str(repository),'rev-parse','HEAD'],text=True).strip()==audit[key], 'SOURCE_HEAD_CHANGED'
    scientific[cp['hydra_config']]=cp['hydra_config_sha256']
    m=load(OUT/'FULL_B2D_ROUTE_MANIFEST.json')
    assert m['route_count']==m['unique_route_id_count']==220 and m['all_split_payloads_identical']
    pair_order=[{'route_id':r['route_id'],'canonical_index':i,'arm_order':['A0','A1'] if i%2==0 else ['A1','A0'],
      'route_path':r['split_files'][0]['path'],'route_sha256':r['split_files'][0]['sha256'],'seed':FORMAL_SEED} for i,r in enumerate(m['routes'])]
    freeze={'stage':'DRIVECLARIFY_TRANSPARENT_BYPASS_AND_FULL_BENCH2DRIVE_V2','formal_freeze_issued':True,'utc':now(),
      'new_freeze_not_V1':True,'qualification_receipt_sha256':sha(OUT/'TRANSPARENCY_QUALIFICATION_RECEIPT.json'),
      'activation_contract_sha256':sha(OUT/'TRANSPARENT_BYPASS_ACTIVATION_CONTRACT.json'),
      'interface_version':'DRIVECLARIFY_NO_CONTEXT_TRANSPARENT_BYPASS_V2.0','interface_sources':q['interface_sources'],
      'interface_digest':digest(q['interface_sources']),'scientific_files':scientific,
      'scientific_digest':digest(scientific),'checkpoint':{**cp,'mtime_ns':Path(cp['path']).stat().st_mtime_ns},
      'source_HEADs':{key:audit[key] for key in ['DriveClarify_HEAD','SimLingo_HEAD','embedded_git_tree_at_parent_HEAD']},
      'benchmark_version_audit_sha256':sha(OUT/'BENCHMARK_VERSION_AUDIT.json'),
      'evaluator':{'path':audit['evaluator_path'],'sha256':sha(audit['evaluator_path']),'tree_identity':audit['evaluator_identity']},
      'scenario_runner':{'path':audit['scenario_runner_path'],'tree_identity':audit['scenario_runner_identity']},
      'CARLA_bootstrap':load(OUT/'carla_bootstrap_proxy/PROXY_MANIFEST.json'),
      'Bench2Drive':'v0.0.3 (installed SimLingo bundle; existing patches disclosed)','CARLA':'0.9.15',
      'route_manifest_sha256':sha(OUT/'FULL_B2D_ROUTE_MANIFEST.json'),'official_XML_sha256':m['sha256'],
      'pair_order':pair_order,'arm_config':{'A0':'ObservedNativeAgent, no DriveClarify context','A1':'new router with context=null -> same ObservedNativeAgent'},
      'controller':'agent_simlingo.LingoAgent.control_pid, same original delegate in both arms',
      'background':'official B2D BackgroundBehavior and original scenario/parked vehicles retained',
      'native_randomness':'only nominal TrafficManager seed assigned; no deterministic torch/runtime forcing',
      'bootstrap':{'resamples':100000,'rng':'numpy.random.Generator(PCG64)','seed':902609063,
        'unit':'route pair','method':'paired resample indices with replacement; mean(A1-A0); 2.5/97.5 percentile linear interpolation'},
      'McNemar':'two-sided exact binomial discordant pairs','noninferiority_margin':None,
      'authoritative_result_rule':'first legally authoritative evaluator JSON final, including collision/blocked/policy-timeout/low score; no best-of selection',
      'technical_retry_policy':{'max_default_attempts':3,'only_before_authoritative_result':True,
        'allowed':'conclusive infrastructure failure, identical scientific route/model/controller configuration; log all attempts',
        'beyond_3':'only documented root-cause engineering repair, no authoritative outcome'},
      'wrapper_initial_hashes':{str(p):sha(p) for p in (OUT/'tooling').glob('*.py')},
      'metric_scripts':load(OUT/'BENCHMARK_VERSION_AUDIT.json')['metrics'],
      'smoothness_input':'Preserve raw native metric_info. Official script unchanged; record original sample cadence, no invented missing values.',
      'result_layout':'formal/<route_id>/<arm>/attempt_XX/official_checkpoint.json; official_merge/<arm>/ contains only first authoritative JSONs'}
    freeze['freeze_digest']=digest(freeze);save(OUT/'FULL_B2D_FREEZE_RECEIPT.json',freeze)
    (OUT/'FULL_B2D_FREEZE_RECEIPT.json.sha256').write_text(sha(OUT/'FULL_B2D_FREEZE_RECEIPT.json')+'  FULL_B2D_FREEZE_RECEIPT.json\n')
    for arm in ('A0','A1'):
        save(OUT/(arm+'_EXECUTION_LEDGER.json'),{'arm':arm,'total':220,'authoritative_completed':0,
          'entries':[{'route_id':p['route_id'],'canonical_index':p['canonical_index'],'status':'PENDING','authoritative_output':None,'raw_sha256':None,'attempts':[]} for p in pair_order]})
    qualification_attempts=[]
    qualification_retry_count=0
    for base in sorted((OUT/'non_formal').glob('*/*')):
        attempts=sorted(base.glob('attempt_*'))
        qualification_retry_count+=max(0,len(attempts)-1)
        for attempt in attempts:
            if authoritative(attempt/'official_checkpoint.json') is not None:continue
            qualification_attempts.append({'route_id':base.parent.name,'arm':base.name,'output':str(attempt),
              'scope':'NON_FORMAL','authoritative_result':False,'evaluator_started':(attempt/'RUN_SPEC.json').exists(),
              'prelaunch_failure':load(attempt/'PRELAUNCH_FAILURE.json'),
              'infrastructure_adjudication':load(attempt/'INFRASTRUCTURE_ADJUDICATION.json'),
              'process_receipt':load(attempt/'PROCESS_RECEIPT.json')})
    save(OUT/'TECHNICAL_RETRY_LEDGER.json',{'entries':[],'qualification_non_authoritative_attempts':qualification_attempts,
      'qualification_technical_retries':qualification_retry_count,'formal_technical_retries':0,
      'total_technical_retries':qualification_retry_count,'routes_requiring_retries':[],
      'definition':'total含资格及正式阶段；正式覆盖与分数仅使用formal首个权威结果。启动前端口恢复单列，不计为原生执行。'})
    update_pairs()
    log('NEW FORMAL FREEZE '+freeze['freeze_digest']+'; beginning full canonical paired 440-route execution')
    return freeze


def update_pairs():
    aa={arm:load(OUT/(arm+'_EXECUTION_LEDGER.json')) for arm in ['A0','A1']}
    rows=[]
    for a,b in zip(aa['A0']['entries'],aa['A1']['entries']):
        assert a['route_id']==b['route_id']
        rows.append({'route_id':a['route_id'],'A0_authoritative':a['status']=='AUTHORITATIVE','A1_authoritative':b['status']=='AUTHORITATIVE',
          'complete_pair':a['status']==b['status']=='AUTHORITATIVE'})
    save(OUT/'PAIRED_ROUTE_LEDGER.json',{'total':220,'complete_pairs':sum(r['complete_pair'] for r in rows),'entries':rows})


def infrastructure_reason(output):
    text=(Path(output)/'evaluator.log').read_text(errors='replace')
    markers={
      'CUDA out of memory':'GPU_OOM_BEFORE_AUTHORITATIVE_RESULT',
      'out of memory':'HOST_OR_RENDERER_OOM_REQUIRES_ROOT_CAUSE',
      'rpc::rpc_error':'RPC_DISCONNECT','time-out of 600000ms':'RPC_TIMEOUT','Connection refused':'RPC_CONNECTION_REFUSED',
      'Address already in use':'PORT_COLLISION','Segmentation fault':'CARLA_OR_EVALUATOR_SEGFAULT',
      'Fatal error:':'CARLA_FATAL_CRASH','Signal 11 caught':'CARLA_SIGNAL_11',
      'SensorReceivedNoData':'SENSOR_INFRASTRUCTURE_DISCONNECT'}
    for marker,reason in markers.items():
        if marker in text:return reason
    return None


def refresh_retry_counts(freeze):
    retry=load(OUT/'TECHNICAL_RETRY_LEDGER.json')
    counts=[(p['route_id'],a,len(list((OUT/'formal'/p['route_id']/a).glob('attempt_*')))) for p in freeze['pair_order'] for a in ['A0','A1']]
    retry['formal_technical_retries']=sum(max(0,n-1) for _,_,n in counts)
    retry['total_technical_retries']=retry['formal_technical_retries']+retry.get('qualification_technical_retries',0)
    retry['routes_requiring_retries']=sorted({route for route,arm,n in counts if n>1})
    retry['route_arms_with_retries']=[{'route_id':route,'arm':arm,'attempts':n} for route,arm,n in counts if n>1]
    save(OUT/'TECHNICAL_RETRY_LEDGER.json',retry)


def main(supervisor_lock=None,hooks=None):
    if supervisor_lock is None:
        supervisor_lock=(OUT/'.formal_supervisor.lock').open('a')
        fcntl.flock(supervisor_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    freeze=make_freeze()
    for pair in freeze['pair_order']:
        for arm in pair['arm_order']:
            verify_scientific_sources(freeze)
            if sha(pair['route_path'])!=pair['route_sha256']:raise RuntimeError('ROUTE_CHANGED')
            ledger=load(OUT/(arm+'_EXECUTION_LEDGER.json'));entry=ledger['entries'][pair['canonical_index']]
            if hooks:hooks.before_item(freeze,pair,arm,entry)
            if entry['status']=='AUTHORITATIVE':
                assert sha(Path(entry['authoritative_output'])/'official_checkpoint.json')==entry['raw_sha256']
                continue
            base=OUT/'formal'/pair['route_id']/arm
            old=sorted(base.glob('attempt_*')) if base.exists() else []
            found=[p for p in old if (p/'official_checkpoint.json').exists() and authoritative(p/'official_checkpoint.json') is not None]
            if len(found)>1:
                save(OUT/'DUPLICATE_AUTHORITY_ADJUDICATION_REQUIRED.json',{'route_id':pair['route_id'],'arm':arm,'outputs':[str(p) for p in found]})
                raise RuntimeError('DUPLICATE_AUTHORITATIVE_RESULTS_STOP_AGGREGATION')
            if found:output=found[0]
            else:
                output=None
                if hooks:hooks.before_resume(pair,arm,old)
                exceptions=load(OUT/'TECHNICAL_ATTEMPT_EXCEPTIONS.json',{'entries':[]})
                max_attempts=max([3]+[e['max_attempts'] for e in exceptions['entries'] if e['route_id']==pair['route_id'] and e['arm']==arm and e['scientific_configuration_unchanged'] is True and e['infrastructure_proven'] is True])
                for attempt_number in range(len(old)+1,max_attempts+1):
                    attempt=base/('attempt_%02d'%attempt_number)
                    if hooks:hooks.before_attempt(pair,arm,attempt)
                    print('FORMAL_START',pair['canonical_index']+1,pair['route_id'],arm,'attempt',attempt_number,flush=True)
                    process=run(arm,pair['route_path'],pair['seed'],27000,attempt,False)
                    entry['attempts'].append({'output':str(attempt),**process});save(OUT/(arm+'_EXECUTION_LEDGER.json'),ledger)
                    refresh_retry_counts(freeze)
                    if process['authoritative']:output=attempt;break
                    reason=infrastructure_reason(attempt)
                    retry=load(OUT/'TECHNICAL_RETRY_LEDGER.json')
                    retry['entries'].append({'route_id':pair['route_id'],'arm':arm,'attempt':attempt_number,'reason':reason,
                       'output':str(attempt),'no_authoritative_result':True,
                       'retry_allowed':reason is not None and 'OOM' not in reason and attempt_number<max_attempts})
                    save(OUT/'TECHNICAL_RETRY_LEDGER.json',retry)
                    refresh_retry_counts(freeze)
                    if hooks:hooks.technical_failure(retry['entries'][-1])
                    if reason is None or 'OOM' in reason:
                        save(OUT/'ENGINEERING_ATTENTION.json',{'route_id':pair['route_id'],'arm':arm,'output':str(attempt),'reason':reason,
                          'action':'diagnose root cause; no authoritative result; do not change scientific inputs'})
                        print('ROOT_CAUSE_DIAGNOSIS_REQUIRED',attempt,reason,flush=True);return 2
                    time.sleep(5)
                if output is None:
                    print('TECHNICAL_ATTEMPTS_EXHAUSTED',pair['route_id'],arm,flush=True);return 3
            entry.update(status='AUTHORITATIVE',authoritative_output=str(output),raw_sha256=sha(output/'official_checkpoint.json'))
            ledger['authoritative_completed']=sum(r['status']=='AUTHORITATIVE' for r in ledger['entries'])
            save(OUT/(arm+'_EXECUTION_LEDGER.json'),ledger);update_pairs()
            if hooks:hooks.after_authoritative(pair,arm,output)
            telemetry=load(output/'agent_terminal.json',{})
            if not telemetry.get('forward_count_contract') or telemetry.get('violations'):
                save(OUT/'CONTROL_ATTENTION.json',{'output':str(output),'authoritative_result_preserved':True,'reason':'telemetry integrity qualification needs adjudication'})
                print('CONTROL_INTEGRITY_DIAGNOSIS_REQUIRED',output,flush=True);return 4
            print('FORMAL_AUTHORITATIVE',pair['route_id'],arm,'arm_total',ledger['authoritative_completed'],flush=True)
    verify_scientific_sources(freeze)
    print('ALL_440_AUTHORITATIVE_READY_FOR_OFFICIAL_MERGE',flush=True)
    if hooks:hooks.before_analysis()
    return subprocess.call([PYTHON,'-B',str(OUT/'tooling/analyze.py')],cwd=str(ROOT))

if __name__=='__main__':sys.exit(main())
