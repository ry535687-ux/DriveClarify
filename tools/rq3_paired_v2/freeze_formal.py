"""按用户要求先封合同/测量/真意/统计/顺序，再生成种子，最后签发摘要。"""
import ast,datetime,hashlib,json,secrets,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from driveclarify_rq3_paired_v2.configuration import REPORT as R,make_runtime_config,CHECKPOINT
from driveclarify_rq3_paired_v2.task_evaluator import VERSION
Q=R/'qualification';F=R/'formal';E=R/'evaluation_only'
def read(p):return json.loads(Path(p).read_text())
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(8388608),b''):h.update(b)
 return h.hexdigest()
def write(p,x):Path(p).write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def main():
 qualification=read(R/'V2_QUALIFICATION_RECEIPT.json');assert qualification['all_required_families_pass'] and qualification['scientific_exposure_before_freeze']==0
 assert read(Q/'END_TO_END_SCORING_QUALIFICATION.json')['pass']
 inventory=read(Q/'HISTORICAL_SEED_SOURCE_INVENTORY.json');assert not inventory['errors']
 assert sha(inventory['exclusion_set_path'])==inventory['exclusion_set_sha256']
 for p in [*Path(ROOT/'driveclarify_rq3_paired_v2').glob('*.py'),*Path(ROOT/'tools/rq3_paired_v2').glob('*.py')]:ast.parse(p.read_text())
 subprocess.run(['bash','-n',str(ROOT/'tools/rq3_paired_v2/run_native_episode.sh')],check=True)
 F.mkdir(exist_ok=False);E.mkdir(exist_ok=False);(F/'runtime_configs').mkdir();sequence=[]
 def seal_step(number,path):sequence.append({'legal_step':number,'path':str(path),'sha256':sha(path),'sealed_utc':now()})
 templates=read(R/'V2_TEMPLATE_CANDIDATE_MANIFEST.json')['templates'];by_condition={t['condition']:t for t in templates}
 contract={'stage':'RQ3_PAIRED_COMPARISON_V2_TASK_BINDING_QUALIFICATION_AND_EXECUTION','qualification_receipt_sha256':sha(R/'V2_QUALIFICATION_RECEIPT.json'),'HIGH':{'conditions':['REF-C','LMK-C','ORD-C','USC-C'],'pairs_per_condition':6,'pairs':24,'true_intent_A_per_condition':3,'true_intent_B_per_condition':3,'A0_first_per_condition':3,'A1_first_per_condition':3,'evaluable_minimum':22,'per_condition_evaluable_minimum':5},'LOW':{'conditions':['REF-E','LMK-E','ORD-E'],'pairs_per_condition':4,'pairs':12,'A0_first_per_condition':2,'A1_first_per_condition':2,'evaluable_minimum':10,'per_condition_evaluable_minimum':3,'pooled_into_HIGH':False,'noninferiority_margin':None,'scope':'DESCRIPTIVE_NONINTERFERENCE_AND_INTERACTION_COST_CONTROL'},'total_pairs':36,'native_runs':72,'TCSC':'correct_goal==1 AND (official_status in [Perfect,Completed] OR score_route>=95) AND no inherited major safety failure','major_safety_event_union':['collisions_layout','collisions_vehicle','collisions_pedestrian','outside_route_lanes','route_dev'],'missing_evidence':'UNKNOWN, no scientific outcome-dependent exclusion, no imputation. Complete valid failures are zeros; unknown pairs excluded from point inference but all24 missing-data RD bounds reported.','formal_execution_policy':'all72 once; never stop for unfavorable outcomes; no scientific retry, no seed replacement. Only pre-world/pre-evaluator startup retries within the same wrapper.','wall_watchdog_s':7200,'native_evaluator_timeout_s':480,'installed_native_blocked_time_s':180,'installed_native_route_timeout_min_sim_s':300,'same_checkpoint':str(CHECKPOINT),'same_native_model_PID_controller':True,'A0_input':'raw original instruction + common official plan + public scene only','A1_input':'same backbone + frozen DriveClarify + all public candidate possibilities; selected intent only after durable legal ASK','true_intent_firewall':'evaluation_only file read by executor/broker/offline scorer only; A0 process receives NONE and no truth path; no truth in arm configs; broker releases answer only after durable ASK.','seed_semantics':'unchanged installed evaluator --traffic-manager-seed; no additional model or CarlaDataProvider RNG seeding or deterministic algorithms; nominal paired conditions equal, numerical trajectories may diverge','scope_limit':'seven controlled ambiguity templates on one official route; repeatability on this subset, not broad scene sampling or full official leaderboard','historical_results_unchanged':True,'frozen_method_components_changed':[],'created_utc':now()}
 write(F/'SCIENTIFIC_CONTRACT.json',contract);seal_step(9,F/'SCIENTIFIC_CONTRACT.json')
 evaluator={'version':VERSION,'module':str(ROOT/'driveclarify_rq3_paired_v2/task_evaluator.py'),'module_sha256':sha(ROOT/'driveclarify_rq3_paired_v2/task_evaluator.py'),'scoring_module':str(ROOT/'driveclarify_rq3_paired_v2/scoring.py'),'scoring_sha256':sha(ROOT/'driveclarify_rq3_paired_v2/scoring.py'),'spec_path':str(R/'V2_TASK_EVALUATOR_SPEC.md'),'spec_sha256':sha(R/'V2_TASK_EVALUATOR_SPEC.md'),'bindings':{t['condition']:{'path':t['task_binding_path'],'sha256':sha(t['task_binding_path'])} for t in templates},'symmetric_A0_A1':True,'formal_entry':'extract_complete_episode -> score_task_truth, joined to unchanged official metrics','full_trace_required':True,'unknown_not_failed':True}
 write(F/'TASK_EVALUATOR_FREEZE.json',evaluator);seal_step(10,F/'TASK_EVALUATOR_FREEZE.json')
 pairs=[]
 for r in range(6):
  for j,t in enumerate(templates):
   high=t['level']=='HIGH'
   if not high and r>=4:continue
   base=['A','B','B','A','A','B'] if high else ['A','B','B','A']
   true=base[(r+j)%len(base)];first='A0' if (r+j)%2==0 else 'A1'
   pair_id='V2-P'+str(len(pairs)+1).zfill(2)+'-'+t['condition']
   pairs.append({'pair_id':pair_id,'condition':t['condition'],'template_id':t['template_id'],'level':t['level'],'within_condition_pair':r+1,'candidate_id':true,'first_arm':first,'second_arm':'A1' if first=='A0' else 'A0'})
 assert len(pairs)==36
 for t in templates:
  group=[p for p in pairs if p['condition']==t['condition']];half=len(group)//2
  assert sum(x['candidate_id']=='A' for x in group)==half and sum(x['first_arm']=='A0' for x in group)==half
 write(E/'TRUE_INTENT_ROSTER.json',{'scope':'EVALUATION_ONLY_NOT_AGENT_INPUT','pairs':pairs,'formal_seeds_not_yet_generated':True,'created_utc':now()});seal_step(11,E/'TRUE_INTENT_ROSTER.json')
 stats={'primary':'HIGH TCSC, 24 planned matched pairs','alpha':.05,'exact_mcnemar':{'alternative':'two-sided','null_discordance_probability':.5,'zero_discordance_p':1.},'risk_difference':'mean(A1-A0)=(01-10)/n','ci95':{'method':'PAIRED_DISCORDANCE_CLOPPER_PEARSON_BONFERRONI_CONSERVATIVE_95','definition':'For benefit and harm probabilities among n paired units, separate equal-tailed97.5% Clopper-Pearson intervals [Lb,Ub],[Lh,Uh]; RD interval=[Lb-Uh,Ub-Lh] intersect[-1,1]. Bonferroni simultaneous coverage at least95%.','beta_quantiles':[.0125,.9875],'point_orientation':'A1_MINUS_A0'},'continuous_ci':{'method':'whole-pair percentile bootstrap','resamples':50000,'analysis_rng_seed':721936,'rng':'numpy.default_rng','quantiles':[.025,.975],'quantile_method':'linear'},'binary_secondary':['correct_goal','wrong_goal','native_completion','safe_completion','collision','official_success'],'continuous':['Driving_Score','Route_Completion','Driving_Efficiency','Driving_Smoothness'],'secondary_claim_control':'descriptive only, no confirmatory multiplicity-adjusted superiority claims','LOW':'separate, same paired descriptive tables; no formal noninferiority claim','supported_rule':'integrity PASS AND HIGH evaluability gates PASS AND paired RD>0 AND two-sided exact McNemar p<.05 AND conservative95%CI lower>0','not_evaluable_rule':'integrity failure OR HIGH evaluability gate failure; never redefine favorable subset','implementation_path':str(ROOT/'driveclarify_rq3_paired_v2/analysis_statistics.py'),'implementation_sha256':sha(ROOT/'driveclarify_rq3_paired_v2/analysis_statistics.py'),'references':read(Q/'ANALYSIS_AND_OFFICIAL_POSTPROCESSOR_QUALIFICATION.json')['method_references']}
 write(F/'STATISTICAL_ANALYSIS_PLAN.json',stats);seal_step(12,F/'STATISTICAL_ANALYSIS_PLAN.json')
 order=[{k:v for k,v in p.items() if k!='candidate_id'} for p in pairs];write(F/'PAIR_ORDER_WITHOUT_SEEDS.json',{'pairs':order,'interleaving':'round-robin conditions, HIGH6 rounds/LOW4','created_utc':now()});seal_step(13,F/'PAIR_ORDER_WITHOUT_SEEDS.json')
 write(F/'PRESEED_CONTRACT_FREEZE_RECEIPT.json',{'sequence':sequence,'all_steps_9_through_13_complete':True,'formal_seed_count_at_contract_freeze':0,'created_utc':now()})
 # 只有以上五项已落盘后才实例化系统随机种子抽样。
 excluded=set(read(inventory['exclusion_set_path']));excluded.add(721936);excluded.update([1731584188,1529594113]);seeds=[];rejected=[];seed_generation_started=now()
 while len(seeds)<36:
  candidate=1000000000+secrets.randbelow(1000000000)
  if candidate in excluded or candidate in seeds:rejected.append(candidate);continue
  seeds.append(candidate)
 assigned=[{**p,'seed':s} for p,s in zip(pairs,seeds)];write(F/'FORMAL_PAIRED_SEEDS.json',{'generation_started_utc':seed_generation_started,'preseed_receipt_sha256':sha(F/'PRESEED_CONTRACT_FREEZE_RECEIPT.json'),'assigned':[{'pair_id':p['pair_id'],'seed':p['seed']} for p in assigned],'rejected_before_formal_allocation':rejected});seal_step(14,F/'FORMAL_PAIRED_SEEDS.json')
 audit={'pass':len(set(seeds))==36 and not(set(seeds)&excluded),'formal_seeds':seeds,'historical_text_files_scanned':inventory['file_count'],'historical_text_bytes_scanned':inventory['total_bytes'],'historical_inventory_sha256':sha(Q/'HISTORICAL_SEED_SOURCE_INVENTORY.json'),'exclusion_set_sha256':inventory['exclusion_set_sha256'],'development_seeds_excluded':[1731584188,1529594113],'historical_intersection':sorted(set(seeds)&excluded),'no_seed_replacement_after_allocation':True,'generated_after_contract_order_stat_truth_freeze':True,'created_utc':now()};assert audit['pass'];write(F/'SEED_FRESHNESS_AUDIT.json',audit);seal_step(15,F/'SEED_FRESHNESS_AUDIT.json')
 runs=[]
 for pair in assigned:
  for arm in [pair['first_arm'],pair['second_arm']]:
   run_id=pair['pair_id']+'-'+arm;t=by_condition[pair['condition']];cfg=F/'runtime_configs'/(run_id+'.json');write(cfg,make_runtime_config(t,arm,run_id,pair['seed']))
   runs.append({'execution_position':len(runs)+1,'run_id':run_id,'pair_id':pair['pair_id'],'condition':pair['condition'],'arm':arm,'seed':pair['seed'],'config':str(cfg),'route':t['official_route_context']['official_route_path'],'output':str(R/'formal_native'/run_id)})
 write(F/'FORMAL_RUN_ROSTER.json',{'runs':runs,'run_count':72,'pairs':36,'contains_true_intent':False})
 # 必需源码/输入均纳入摘要。历史科学源码和结果使用已经逐文件复验的原快照记录。
 old=read(ROOT/'reports/driveclarify_rq3_paired_ambiguous_closed_loop_comparison_v1/audit/PRESERVATION_BEFORE.json')['files'];files={x['path']:x for x in old}
 extra=[*list((ROOT/'driveclarify_rq3_paired_v2').glob('*.py')),*list((ROOT/'tools/rq3_paired_v2').glob('*.py')),ROOT/'tools/rq3_paired_v2/run_native_episode.sh',*list((R/'candidate_assets').glob('*.json')),*list(F.rglob('*.json')),*list(E.glob('*.json')),R/'V2_TEMPLATE_CANDIDATE_MANIFEST.json',R/'V2_QUALIFICATION_RECEIPT.json',R/'V2_A0_COMPARABILITY_RECEIPT.json',R/'V2_TASK_EVALUATOR_SPEC.md',Q/'END_TO_END_SCORING_QUALIFICATION.json',Q/'HISTORICAL_SEED_SOURCE_INVENTORY.json',Path(inventory['exclusion_set_path']),Path('/home/buaa/wrh/simlingo/Bench2Drive/tools/efficiency_smoothness_benchmark.py'),Path('/home/buaa/wrh/simlingo/team_code/config_simlingo.py')]
 for p in extra:files[str(p)]={'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
 runtime=[x for x in files.values() if (x['path'].startswith(str(ROOT/'driveclarify_rq3_paired_v2')) or x['path'].startswith(str(F/'runtime_configs')) or any(s in x['path'] for s in ['team_code/agent_simlingo.py','team_code/config_simlingo.py','team_code/nav_planner.py','driveclarify_rq3/simlingo_agent.py','driveclarify_rq1_v2/simlingo_agent.py','driveclarify_clear_passthrough_v11/','tools/rq3_paired_v2/run_native_episode.sh','a1_selected.ckpt/pytorch_model.pt','selected/.hydra/config.yaml','leaderboard/leaderboard_evaluator.py','statistics_manager.py']))]
 frozen_manifest={'files':sorted(files.values(),key=lambda x:x['path']),'runtime_files':sorted(runtime,key=lambda x:x['path']),'legal_order_sequence':sequence,'created_utc':now()};write(F/'FREEZE_MANIFEST.json',frozen_manifest);digest=sha(F/'FREEZE_MANIFEST.json')
 write(F/'FREEZE_RECEIPT.json',{'stage':contract['stage'],'all_qualification_pass':True,'seed_freshness_pass':True,'freeze_digest':digest,'formal_seed_count':36,'formal_native_runs_at_freeze':0,'scientific_exposure_at_freeze':0,'planned_native_runs':72,'sealed_utc':now(),'legal_order':sequence,'status':'FORMAL_FREEZE_COMPLETE_EXECUTION_AUTHORIZED'})
 with (R/'COMMAND_LOG.md').open('a') as f:f.write('\n15. freeze_formal.py：先封合同/评估器/真意/统计/顺序，随后生成36个正式种子，审计与历史及开发排除集无交集，签发正式冻结摘要 '+digest+'。冻结时正式native与科学暴露均0。\n16. execute_formal.py 将按冻结顺序串行执行72次；每次先登记，已登记cell绝不重新执行。\n')
 print(json.dumps({'status':'FORMAL_FREEZE_COMPLETE_EXECUTION_AUTHORIZED','freeze_digest':digest,'formal_seeds':36,'native_runs_at_freeze':0,'files_frozen':len(files),'runtime_files':len(runtime)}))
if __name__=='__main__':main()
