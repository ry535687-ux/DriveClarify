"""只执行 A*；固定 P1→P2，尝试登记不可重复。"""
import fcntl,os,subprocess,time
from common import *
from score_a_star import evaluate,summarize

def verify():
 receipt=read(R/'V3_CANDIDATE_POOL_FREEZE_RECEIPT.json');manifest=R/'audit/QUALIFICATION_FREEZE_MANIFEST.json';assert sha(manifest)==receipt['freeze_digest']
 for x in read(manifest)['runtime_files']+read(manifest)['input_files']:assert sha(x['path'])==x['sha256'],x['path']

def main():
 verify();seeds=read(R/'A_STAR_DEVELOPMENT_SEED_RECEIPT.json');assert seeds['freshness_pass']
 (R/'native').mkdir(exist_ok=True);(R/'runtime_configs').mkdir(exist_ok=True)
 lock=(R/'native/EXECUTION_LOCK').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 pool=read(R/'V3_HOST_CANDIDATE_POOL.json');all_results=[];templates=[];families=[]
 for condition in FAMILIES:
  selected=None
  for template in sorted([t for t in pool['templates'] if t['condition']==condition],key=lambda t:t['rank']):
   if selected:
    templates.append({'template_id':template['template_id'],'condition':condition,'tested':False,'native_runs':0,'qualified':None,'result':'NOT_EXECUTED_P1_PASSED','interpretation1':None,'interpretation2':None,'run_ids':[]});continue
   results=[]
   for row in [r for r in seeds['assigned'] if r['template_id']==template['template_id']]:
    verify();output=R/'native'/row['run_id'];cfgpath=R/'runtime_configs'/(row['run_id']+'.json');run={**row,'arm':'A_STAR','output':str(output),'config':str(cfgpath),'route':template['official_route']}
    attempt=R/'native'/(row['run_id']+'_ATTEMPT.json')
    if attempt.exists():
     old=read(attempt)
     if old.get('state')!='COMPLETE':raise RuntimeError('REGISTERED_ATTEMPT_NOT_REEXECUTABLE:'+row['run_id'])
     result=read(R/'native'/(row['run_id']+'_RESULT.json'));results.append(result);all_results.append(result);continue
    config={'schema':'driveclarify.v11.native-runtime-config.v1','run_id':row['run_id'],'mode':'NATIVE_SIMLINGO','checkpoint':str(CHECKPOINT),'checkpoint_sha256':CHECKPOINT_SHA,'a1_trainable_parameters':896,'training_performed':False,'observation_window_ticks':96,'receipt_completion_mode':'NATURAL_EVALUATOR_DESTROY','nonprogress_diagnostic_window_ticks':80,'scientific_seed_not_available_to_method':row['seed'],'method_input':{'instruction':template['explicit_instructions'][row['interpretation']],'runtime_actors':read(template['actors_path']),'background_traffic_policy':{'random_background_vehicle_count':0,'traffic_manager_random_generation_enabled':False,'retained_scientific_actors':[{'scientific_role':'UNCHANGED_OFFICIAL_SCENARIO_OWNED_ACTORS'}]}}}
    write(cfgpath,config)
    command=['bash',str(R/'tooling/run_a_star.sh'),str(cfgpath),template['official_route'],str(row['seed']),str(output)]
    status={**run,'state':'REGISTERED_NO_RETRY','registered_utc':now(),'command':command,'config_sha256':sha(cfgpath),'explicit_instruction_sha256':digest(config['method_input']['instruction']),'source_integrity_prelaunch':'PASS'}
    with attempt.open('x') as f:
     f.write(__import__('json').dumps(status,indent=2)+'\n');f.flush();os.fsync(f.fileno())
    log=(R/'native'/(row['run_id']+'_WRAPPER.log')).open('x');p=subprocess.Popen(command,cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT);status.update(state='RUNNING',wrapper_pid=p.pid);write(attempt,status)
    while p.poll() is None:
     write(R/'A_STAR_EXECUTION_LEDGER.json',{'state':'RUNNING','current_run':row['run_id'],'completed_native_attempts':len(all_results),'updated_utc':now(),'attempt_files':[str(x) for x in sorted((R/'native').glob('*_ATTEMPT.json'))],'valid_failures_retried':0,'development_seed_replacements':0,'formal_ambiguous_native_runs':0});time.sleep(5)
    log.close();verify();result=evaluate(run,template);write(R/'native'/(row['run_id']+'_RESULT.json'),result);status.update(state='COMPLETE',wrapper_exit=p.returncode,completed_utc=now());write(attempt,status);results.append(result);all_results.append(result)
    write(R/'A_STAR_RESULTS.json',{'scope':'DEVELOPMENT_ONLY','results':all_results,'valid_failures_retried':0,'formal_comparisons':0})
    print(__import__('json').dumps({'completed':len(all_results),'run_id':row['run_id'],'evaluable':result['execution_evaluable'],'A_STAR_TCSC':result['A_STAR_TCSC']}),flush=True)
    if not result['process_receipt'].get('cleanup_pass'):raise RuntimeError('OWNED_PROCESS_CLEANUP_FAILURE')
   summary=summarize(template,results);templates.append(summary)
   if summary['qualified']:selected=template['template_id']
   write(R/'V3_HOST_FEASIBILITY_BY_TEMPLATE.json',{'templates':templates,'updated_utc':now()})
  families.append({'family':condition,'qualified':selected is not None,'selected_qualified_template':selected,'P1':next(t for t in templates if t['template_id']==f'V3-HOST-{condition}-P1'),'P2':next(t for t in templates if t['template_id']==f'V3-HOST-{condition}-P2')})
  write(R/'V3_HOST_FEASIBILITY_BY_FAMILY.json',{'families':families,'updated_utc':now()})
  print(__import__('json').dumps({'family_complete':condition,'selected':selected}),flush=True)
 write(R/'V3_HOST_FEASIBILITY_BY_TEMPLATE.json',{'templates':templates,'updated_utc':now()})
 write(R/'V3_QUALIFIED_HOST_ROSTER.json',{'scope':'DEVELOPMENT_QUALIFIED_ONLY_NOT_FORMAL_ROSTER','selected':{f['family']:f['selected_qualified_template'] for f in families},'all_seven_qualified':all(f['qualified'] for f in families),'formal_seeds_generated':0})
 write(R/'A_STAR_EXECUTION_LEDGER.json',{'state':'ALL_REQUIRED_DEVELOPMENT_ATTEMPTS_COMPLETE','completed_native_attempts':len(all_results),'native_exposure_observed':sum(r['native_exposure_observed'] for r in all_results),'updated_utc':now(),'attempt_files':[str(x) for x in sorted((R/'native').glob('*_ATTEMPT.json'))],'valid_failures_retried':0,'development_seed_replacements':0,'formal_ambiguous_native_runs':0})
if __name__=='__main__':main()
