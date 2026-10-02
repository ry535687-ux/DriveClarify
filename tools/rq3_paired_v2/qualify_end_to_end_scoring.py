"""合成权威证据验证最终 TCSC 联合逻辑、缺失处理和两臂对称。"""
import copy,hashlib,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from driveclarify_rq3_paired_v2.configuration import REPORT as R,CHECKPOINT_SHA256
from driveclarify_rq3_paired_v2.scoring import evaluate_run
Q=R/'qualification';root=Q/'SYNTHETIC_END_TO_END_SCORING';root.mkdir(exist_ok=False)
t=next(x for x in json.loads((R/'V2_TEMPLATE_CANDIDATE_MANIFEST.json').read_text())['templates'] if x['condition']=='USC-C');b=json.loads(Path(t['task_binding_path']).read_text());region=b['regions'][0];angle=math.radians(region['yaw_degrees']);cx,cy,cz=region['center_xyz']
trace=[{'frame':i+1,'simulation_time_s':i*.05,'xyz':[cx,cy,cz],'yaw_degrees':region['yaw_degrees'],'roll_degrees':0,'pitch_degrees':0,'speed_mps':0,'bbox_extent_xy_m':[2.45,.92],'bbox_world_vertices':[[cx+u*math.cos(angle)-v*math.sin(angle),cy+u*math.sin(angle)+v*math.cos(angle),cz+z] for u in [-2.45,2.45] for v in [-.92,.92] for z in [0,1.5]],'lane_type':'Parking','road_id':region['road_id'],'lane_id':region['parking_lane_id']} for i in range(26)]
infra={k:[] for k in ['collisions_layout','collisions_vehicle','collisions_pedestrian','outside_route_lanes','route_dev','red_light','stop_infraction','route_timeout','scenario_timeouts','vehicle_blocked','min_speed_infractions','yield_emergency_vehicle_infractions']}
checks=[]
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x)+'\n')
for arm in ['A0','A1']:
 for case in ['correct_safe','wrong_goal','collision','noncompletion','missing_frame','corrupt_rotation']:
  out=root/(arm+'_'+case);own=out/'owner_evidence';own.mkdir(parents=True)
  write(out/'process_job/PROCESS_RECEIPT.json',{'cleanup_pass':True})
  write(own/'V2_RUNTIME_IDENTITY.json',{'checkpoint_sha256':CHECKPOINT_SHA256,'raw_instruction':t['instruction'],'additional_model_execution_by_observer':0,'second_control_writer':False})
  rows=copy.deepcopy(trace)
  if case=='missing_frame':rows.pop(8)
  if case=='corrupt_rotation':rows[7]['roll_degrees']='invalid'
  p=own/'V2_NATIVE_STATE_TRACE.jsonl';p.write_text(''.join(json.dumps(x)+'\n' for x in rows))
  write(own/'V2_TRACE_TERMINAL_RECEIPT.json',{'trace_count':len(rows),'first_frame':1,'last_frame':26,'control_return_count':26,'trace_errors':[],'destroy_observed':True,'trace_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'raw_prompt_mismatches':0,'observer_ego_control_writes':0,'observer_model_calls':0,'model_forward_count':25})
  ii=copy.deepcopy(infra)
  if case=='collision':ii['collisions_vehicle']=['synthetic collision']
  record={'status':'Completed' if case!='noncompletion' else 'Failed','scores':{'score_composed':100,'score_penalty':1,'score_route':100 if case!='noncompletion' else 40},'infractions':ii,'meta':{'duration_game':1.30,'duration_system':2.}}
  write(out/'official_checkpoint.json',{'_checkpoint':{'records':[record]}})
  run={'run_id':arm+'_'+case,'pair_id':'SYNTHETIC','arm':arm,'condition':'USC-C','seed':0,'output':str(out)}
  result=evaluate_run(run,{'candidate_id':'B' if case=='wrong_goal' else 'A'},t)
  expected=None if case in ['missing_frame','corrupt_rotation'] else (1 if case=='correct_safe' else 0)
  checks.append({'check':arm+'_'+case,'pass':result['TCSC']==expected,'expected':expected,'actual':result['TCSC']})
  if case=='wrong_goal':assert result['wrong_goal']==1
  repeated=evaluate_run(run,{'candidate_id':'B' if case=='wrong_goal' else 'A'},t);assert repeated==result
receipt={'scope':'SYNTHETIC_NOT_NATIVE_OR_POLICY_RESULT','checks':checks,'pass':all(x['pass'] for x in checks),'same_fixture_A0_A1_invariant':True,'formal_exposure':0};write(Q/'END_TO_END_SCORING_QUALIFICATION.json',receipt);print(json.dumps(receipt));assert receipt['pass']
