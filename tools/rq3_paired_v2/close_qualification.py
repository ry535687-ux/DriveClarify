"""只有实际资格证据全部通过才能关闭 Phase A；不做正式冻结。"""
import datetime,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];R=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution';Q=R/'qualification'
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
names=['PHYSICAL_BINDING_QUALIFICATION.json','FROZEN_REPLAN_AND_MAP_COMPATIBILITY.json','INTERFACE_AND_EVALUATOR_SOFTWARE_QUALIFICATION.json','PRESERVATION_QUALIFICATION_CHECK.json','A0_NATIVE_INTERFACE_QUALIFICATION.json','A1_NATIVE_INTERFACE_QUALIFICATION.json','ANALYSIS_AND_OFFICIAL_POSTPROCESSOR_QUALIFICATION.json']
for name in names:assert read(Q/name)['pass'],name
for run in ['V2-NONFORMAL-INTERFACE-A0-01-PREWORLD-ATTEMPT02','V2-NONFORMAL-INTERFACE-A1-01']:
 receipt=read(Q/'development'/run/'process_job/PROCESS_RECEIPT.json');assert receipt['cleanup_pass'] and receipt['ports_released'] and not receipt['scientific_retry']
m=read(R/'V2_TEMPLATE_CANDIDATE_MANIFEST.json');semantic=read(Q/'FAMILY_SEMANTIC_EVIDENCE.json')['rows'];assert len(m['templates'])==7 and len(semantic)==7
rows=[]
for t,s in zip(m['templates'],semantic):
 assert t['condition']==s['condition'] and s['plausible_both'] and s['balanced_truth_structurally_possible']
 t.update(status='PHASE_A_QUALIFIED_NOT_FORMALLY_FROZEN',native_layout_verified=True,semantic_certificate_scope='PUBLIC_PHYSICAL_SCENE_AND_SYMMETRIC_TASK_QUALIFIED')
 rows.append({'template_id':t['template_id'],'condition':t['condition'],'route_neutral':'YES','a0_comparable':'YES','task_binding_observable':'YES','same_evaluator_A0_A1':'YES','both_intents_valid_and_balancing_possible':'YES','formal_balancing_allocated':False,'paired_comparable':'YES','development_native_runs_direct':int(t['condition'] in ['REF-C','USC-C']),'development_evidence_scope':'两臂共享接口分别原生验证一次；其他模板公共几何/配置/测量夹具验证，无策略筛选。','evidence':[str(Q/x) for x in names]})
write(R/'V2_TEMPLATE_CANDIDATE_MANIFEST.json',m)
common={'stage':'RQ3_PAIRED_COMPARISON_V2_TASK_BINDING_QUALIFICATION_AND_EXECUTION','issued_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'PHASE_A_QUALIFICATION_ONLY','formal_freeze_digest':None,'formal_seeds_generated':0,'formal_native_runs':0,'scientific_exposure_before_freeze':0,'development_native_runs':2,'development_pre_world_failed_launches':1,'permanently_excluded_development_seeds':[1731584188,1529594113],'model_or_policy_outcome_used_for_template_selection':False,'scientific_components_changed':[],'historical_results_changed':False}
a0={**common,'classification':'B_THIN_NONSCIENTIFIC_INTERFACE_ADAPTER_REQUIRED','raw_language_semantics':'PASS','raw_instruction_mismatches':0,'candidate_or_truth_in_A0_input':False,'model_controller_planner_modified':False,'templates':rows,'pass':True};write(R/'V2_A0_COMPARABILITY_RECEIPT.json',a0)
receipt={**common,'status':'PASS_RQ3_PAIRED_V2_PHASE_A_QUALIFICATION','templates':rows,'route_leaking_templates':0,'a0_raw_language_semantics':'PASS','task_evaluator_symmetric':'PASS','true_intent_firewall_design':'PASS','official_evaluator_compatibility':'PASS','all_required_families_pass':True,'phase_B_authorized_by_user_condition':True,'phase_C_not_yet_authorized_until_freeze_and_seed_audit':True,'qualification_evidence_sha256':{str(Q/name):sha(Q/name) for name in names},'formal_seeds_generated_after_this_receipt_only':True}
write(R/'V2_QUALIFICATION_RECEIPT.json',receipt)
with (R/'COMMAND_LOG.md').open('a') as f:f.write('\n14. close_qualification.py：逐项检查实际PASS及两次原生退出清理收据，签发 Phase A 资格通过收据。尚无正式冻结或正式种子。\n')
print(json.dumps({'status':receipt['status'],'templates':7,'development_native_runs':2,'formal_seeds':0,'formal_runs':0}))
