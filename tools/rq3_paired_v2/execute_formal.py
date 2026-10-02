"""冻结后36对72次串行native执行；已登记尝试绝不再执行。"""
import argparse,datetime,fcntl,hashlib,json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(8388608),b''):h.update(b)
 return h.hexdigest()
def read(p):return json.loads(Path(p).read_text())
def write(p,x):
 p=Path(p);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(x,indent=2)+'\n');tmp.replace(p)
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def main():
 receipt=read(R/'formal/FREEZE_RECEIPT.json');assert receipt['all_qualification_pass'] and receipt['seed_freshness_pass']
 assert sha(R/'formal/FREEZE_MANIFEST.json')==receipt['freeze_digest']
 for row in read(R/'formal/FREEZE_MANIFEST.json')['files']:
  assert sha(row['path'])==row['sha256'],row['path']
 roster=read(R/'formal/FORMAL_RUN_ROSTER.json')['runs'];truth={x['pair_id']:x for x in read(R/'evaluation_only/TRUE_INTENT_ROSTER.json')['pairs']}
 output=R/'formal_native';output.mkdir(exist_ok=True)
 with (output/'EXECUTOR_LOCK').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  ledger=output/'EXECUTION_LEDGER.jsonl';completed=0
  for run in roster:
   attempt=output/(run['run_id']+'_ATTEMPT.json')
   if attempt.exists():
    old=read(attempt)
    if old.get('state')=='COMPLETED':completed+=1;continue
    prior_pid=old.get('wrapper_pid');prior_cmd=Path('/proc/'+str(prior_pid)+'/cmdline')
    if prior_pid and prior_cmd.exists() and run['output'].encode() in prior_cmd.read_bytes():
     print('等待同一已登记 wrapper 结束，不启动第二次执行：'+run['run_id'],flush=True)
     while prior_cmd.exists() and run['output'].encode() in prior_cmd.read_bytes():time.sleep(10)
     prior_process=Path(run['output'])/'process_job/PROCESS_RECEIPT.json'
     if prior_process.exists():
      old.update(state='COMPLETED',wrapper_exit=read(prior_process)['wrapper_exit'],finished_utc=now(),resumed_observation_without_reexecution=True);write(attempt,old);completed+=1;continue
    # 不重启已登记的 cell。重新接管只将其留作 UNKNOWN，继续尚未登记的 cells。
    write(output/(run['run_id']+'_RESUME_NONREEXECUTION.json'),{'state':'CONSUMED_WITHOUT_REEXECUTION','prior_attempt':old,'recorded_utc':now()})
    completed+=1;continue
   # 所有运行在暴露前核对关键代码/配置；普通变化不能边跑边修改。
   for row in read(R/'formal/FREEZE_MANIFEST.json')['runtime_files']:
    assert sha(row['path'])==row['sha256'],row['path']
   status={'run_id':run['run_id'],'pair_id':run['pair_id'],'arm':run['arm'],'seed':run['seed'],'state':'ATTEMPT_REGISTERED_NO_REEXECUTION','started_utc':now(),'formal_roster_sha256':sha(R/'formal/FORMAL_RUN_ROSTER.json')}
   with attempt.open('x') as f:f.write(json.dumps(status,indent=2)+'\n');f.flush();os.fsync(f.fileno())
   command=['bash',str(ROOT/'tools/rq3_paired_v2/run_native_episode.sh'),run['config'],run['route'],str(run['seed']),'28740','NONE' if run['arm']=='A0' else truth[run['pair_id']]['candidate_id'],run['output'],'FORMAL_NO_SCIENTIFIC_RETRY',run['arm']]
   write(output/'CURRENT_PROGRESS.json',{'state':'RUNNING','current_run':run['run_id'],'position':run['execution_position'],'completed_runs':completed,'planned_runs':72,'started_utc':status['started_utc'],'frozen_digest':receipt['freeze_digest'],'policy_metrics_inspected_by_executor':False})
   p=subprocess.Popen(command,cwd=str(ROOT),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   status['wrapper_pid']=p.pid;status['state']='WRAPPER_RUNNING';write(attempt,status)
   while p.poll() is None:
    time.sleep(10)
    write(output/'CURRENT_PROGRESS.json',{'state':'RUNNING','current_run':run['run_id'],'position':run['execution_position'],'completed_runs':completed,'planned_runs':72,'updated_utc':now(),'elapsed_this_run_wall_s':time.time()-datetime.datetime.fromisoformat(status['started_utc']).timestamp(),'frozen_digest':receipt['freeze_digest'],'policy_metrics_inspected_by_executor':False})
   completed+=1;status.update(state='COMPLETED',wrapper_exit=p.returncode,finished_utc=now());write(attempt,status)
   with ledger.open('a') as f:f.write(json.dumps(status)+'\n');f.flush();os.fsync(f.fileno())
   print(json.dumps({'completed_runs':completed,'total_runs':72,'run_id':run['run_id'],'wrapper_exit':p.returncode}),flush=True)
   process_path=Path(run['output'])/'process_job/PROCESS_RECEIPT.json'
   if not process_path.exists() or not read(process_path).get('cleanup_pass'):
    raise RuntimeError('OWNED_INFRASTRUCTURE_CLEANUP_MUST_BE_RESOLVED_BEFORE_NEXT_UNEXPOSED_RUN')
  write(output/'CURRENT_PROGRESS.json',{'state':'ALL_72_ATTEMPTS_COMPLETE','completed_runs':completed,'planned_runs':72,'finished_utc':now(),'frozen_digest':receipt['freeze_digest']})
if __name__=='__main__':main()
