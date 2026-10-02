"""无真意参数的两臂运行配置构造；evaluation truth 另存。"""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution'
CHECKPOINT=ROOT/'reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt'
CHECKPOINT_SHA256='cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044'


def make_runtime_config(template,arm,run_id,scenario_seed):
    assert arm in {'A0','A1'}
    condition=template['condition']
    layout=json.loads((REPORT/'candidate_assets'/(condition+'_LAYOUT.json')).read_text())
    actors=[]
    for i,actor in enumerate(layout['actors']):
        actors.append({'runtime_track_id':'marker_'+str(i),'blueprint':actor['blueprint'],'pose':actor['pose'],'attributes':actor['attributes'],'scientific_role':'PUBLIC_SHARED_SCENE_ENTITY'})
    common={'instruction':template['instruction'],'runtime_actors':actors,'background_traffic_policy':{'random_background_vehicle_count':0,'traffic_manager_random_generation_enabled':False,'retained_scientific_actors':[{'scientific_role':'UNCHANGED_OFFICIAL_SCENARIO_OWNED_ACTORS'}]}}
    config={'schema':'driveclarify.v11.native-runtime-config.v1','run_id':run_id,'mode':'NATIVE_SIMLINGO' if arm=='A0' else 'DRIVECLARIFY','checkpoint':str(CHECKPOINT),'checkpoint_sha256':CHECKPOINT_SHA256,'a1_trainable_parameters':896,'training_performed':False,'observation_window_ticks':96,'receipt_completion_mode':'NATURAL_EVALUATOR_DESTROY','nonprogress_diagnostic_window_ticks':80,'scientific_seed_not_available_to_method':scenario_seed,'method_input':common}
    if arm=='A1':
        nominal=template['official_route_context']['nominal_route']
        anchor_index=14
        common.update({'observation_anchor_xyz':nominal[anchor_index]['xyz'],'anchor_capture_distance_m':3.0,'grounding_evidence_status':'VERIFIED','grounding_evidence_source':'PROSPECTIVELY_CERTIFIED_PUBLIC_PHYSICAL_V2_SCENE','grounding_reason_codes':['TWO_PLAUSIBLE_PUBLIC_TASK_BINDINGS_NOT_SELECTED_INTENT'],'route_source':template['candidate_route_path'],'current_connector_id':run_id+'-CURRENT','alternatives':[{'candidate_id':c,'description':template['candidate_interpretations'][c],'evidence_id':run_id+'-EVIDENCE-'+c,'route_source_field':'candidate_'+c,'connector_id':run_id+'-CONNECTOR-'+c,'commitment_point_index':22 if template['level']=='HIGH' else 30} for c in ['A','B']],'task_signatures':template['task_signatures'],'rq3_temporal_contract':{'rule':'R-JOINT(B2)','evidence_anchor_certified':True,'T_FIXED_s':3.0,'reserve_s':1.2,'clock':'CARLA_SIMULATION_TIME'}})
    return config
