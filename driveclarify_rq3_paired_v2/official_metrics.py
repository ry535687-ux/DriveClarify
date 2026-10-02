"""读取 installed 权威输出；官方后处理仅做路径/采样率适配，不改公式。"""
import hashlib,importlib.util,json,math
from pathlib import Path

OFFICIAL_POSTPROCESSOR=Path('/home/buaa/wrh/simlingo/Bench2Drive/tools/efficiency_smoothness_benchmark.py')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load_postprocessor():
    spec=importlib.util.spec_from_file_location('v2_installed_official_efficiency_smoothness',OFFICIAL_POSTPROCESSOR)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def extract_official(record):
    scores=record['scores'];infra=record['infractions']
    count=lambda name:len(infra[name])
    collision=sum(count(x) for x in ['collisions_layout','collisions_vehicle','collisions_pedestrian'])
    union=count('outside_route_lanes');dev=count('route_dev')
    # 与原 RQ3-V3 相同的完成/重大失败判定；不把成功状态手工重写。
    success=record['status'] in ('Perfect','Completed')
    completion=success or float(scores['score_route'])>=95.
    major=bool(collision or union or dev)
    return {'Driving_Score':scores['score_composed'],'Route_Completion':scores['score_route'],'infraction_penalty':scores['score_penalty'],'official_status':record['status'],'official_success':int(success),'native_completion':int(completion),'major_safety_failure':int(major),'safe_completion':int(completion and not major),'collision':int(collision>0),'collision_event_count':collision,'outside_route_lanes_union_count':union,'offroad_separate_count':None,'wrong_lane_separate_count':None,'route_deviation':dev,'red_light':count('red_light'),'stop_sign':count('stop_infraction'),'route_timeout':count('route_timeout'),'scenario_timeouts':count('scenario_timeouts'),'vehicle_blocked':count('vehicle_blocked'),'minimum_speed':count('min_speed_infractions'),'yield_emergency_vehicle':count('yield_emergency_vehicle_infractions'),'raw_official_infractions':infra,'duration_game_s':record['meta']['duration_game'],'duration_system_s':record['meta']['duration_system']}


def run_official_postprocessing(output,record,trace,bridge_dir):
    output=Path(output);bridge=Path(bridge_dir);bridge.mkdir(parents=True,exist_ok=False)
    paths=list((output/'official_data').rglob('metric_info.json'))
    result={'Driving_Efficiency':None,'Driving_Smoothness':None,'postprocessor':str(OFFICIAL_POSTPROCESSOR),'postprocessor_sha256':sha(OFFICIAL_POSTPROCESSOR),'native_metric_files':[str(p) for p in paths],'formula_modified':False}
    def finish(value):
        (bridge/'OFFICIAL_POSTPROCESSING_RECEIPT.json').write_text(json.dumps(value,indent=2)+'\n')
        return value
    if len(paths)!=1:
        return finish({**result,'availability_reason':'UNIQUE_NATIVE_METRIC_INFO_NOT_AVAILABLE'})
    original=json.loads(paths[0].read_text());keys=sorted(original,key=lambda k:int(k));all_steps=[int(k) for k in keys]
    if len(keys)<3 or any(b!=a+1 for a,b in zip(all_steps,all_steps[1:])):
        return finish({**result,'availability_reason':'NATIVE_METRIC_SEQUENCE_INCOMPLETE'})
    # 原生每个模型步骤0.05s。installed 官方公式 time_interval 默认0.1s。
    # 以实际 native trace 时间验证20Hz，然后每2步取1条原始记录作为10Hz输入。
    if len(trace)<3 or any(abs(b['simulation_time_s']-a['simulation_time_s']-.05)>1e-5 for a,b in zip(trace,trace[1:])):
        return finish({**result,'availability_reason':'NATIVE_TIMEBASE_NOT_20HZ'})
    sampled={k:original[k] for k in keys[::2]};metric_dir=bridge/'native_10hz';metric_dir.mkdir();(metric_dir/'metric_info.json').write_text(json.dumps(sampled)+'\n')
    projected={**record,'save_name':'native_10hz'};checkpoint=bridge/'official_record_with_path_binding.json';checkpoint.write_text(json.dumps({'_checkpoint':{'records':[projected]}})+'\n')
    pp=load_postprocessor()
    try:
        arrays,efficiency=pp.read_from_json(str(checkpoint),str(bridge))
        comfort=pp.seg_compute_comfort_metric(**arrays[0])
        result.update(Driving_Smoothness=float(comfort),Driving_Efficiency=None if not efficiency else float(sum(efficiency)/len(efficiency)),availability_reason='OFFICIAL_FUNCTIONS_EXECUTED_WITH_DOCUMENTED_10HZ_INPUT',native_metric_sha256=sha(paths[0]),sampling='native steps [::2], 0.05s -> 0.10s; unchanged raw rows; no interpolation',path_binding_only_added_to_record=True,samples_10hz=len(sampled))
    except (ValueError,ZeroDivisionError,KeyError,AttributeError) as exc:
        result['availability_reason']='OFFICIAL_POSTPROCESSOR_UNAVAILABLE:'+repr(exc)
    return finish(result)
