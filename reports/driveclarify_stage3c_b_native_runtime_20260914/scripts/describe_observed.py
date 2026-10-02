"""运行结束后仅描述B已有记录；不执行模型/PID，不做停车根因诊断。"""
import json
import math
from pathlib import Path
import statistics
from collections import Counter

REPORT=Path(__file__).resolve().parents[1]
ROOT=REPORT.parents[1]
RAW=ROOT/'experiments/driveclarify_native_clear_backend_dev_20260913/outputs/DEV_NATIVE_INIT_B_TOWN07_26458'


def stats(values):
    values=[x for x in values if x is not None]
    return {'n':len(values),'min':min(values),'median':statistics.median(values),'max':max(values)} if values else None


def describe():
    rows=[];errors=[]
    if (RAW/'world_state.jsonl').exists():
        for i,line in enumerate((RAW/'world_state.jsonl').read_text().splitlines(),1):
            try:rows.append(json.loads(line))
            except json.JSONDecodeError as exc:errors.append({'line':i,'error':str(exc)})
    result={'case_id':'DEV_NATIVE_INIT_B_TOWN07_26458','observed_records':len(rows),'parse_errors':errors,
            'actual_apply_control_independently_verified':'UNKNOWN','new_model_or_pid_calls':0}
    if rows:
        xyz=[v['ego']['location_xyz'] for v in rows];origin=xyz[0];targets=[];lengths=[]
        for r in rows:
            w=r.get('pred_speed_wps_values');p=r.get('pred_route_values')
            # 已冻结agent:1027-1029，fps20/(wp_dilation1*data_save_freq5)；仅重建标量。
            targets.append(2*math.dist(w[0][0],w[0][2]) if w and len(w[0])>=3 else None)
            lengths.append(sum(math.dist(a,b) for a,b in zip(p[0],p[0][1:])) if p and len(p[0])>=2 else None)
        controls=[r.get('baseline_control') or {} for r in rows]
        lights={}
        for r in rows:
            for lamp in (r.get('traffic_lights') or {}).get('lights',[]):
                lights.setdefault(str(lamp['id']),Counter()).update([lamp.get('state')])
        navigation=Counter((r.get('route_context') or {}).get('navigation_target_consumed',{}).get('road_option',{}).get('name') for r in rows)
        result.update(frame_first=rows[0]['snapshot_frame'],frame_last=rows[-1]['snapshot_frame'],
                      snapshot_elapsed_first=rows[0]['snapshot_elapsed_seconds'],snapshot_elapsed_last=rows[-1]['snapshot_elapsed_seconds'],
                      gametime_first=rows[0]['gametime_seconds'],gametime_last=rows[-1]['gametime_seconds'],
                      ego_xyz_first=xyz[0],ego_xyz_last=xyz[-1],
                      max_xy_displacement_from_first_m=max(math.hypot(p[0]-origin[0],p[1]-origin[1]) for p in xyz),
                      ego_speed_world_mps=stats([r['ego'].get('speed_world_mps') for r in rows]),
                      target_speed_reconstructed=stats(targets),
                      target_semantics='按冻结原生公式2*norm(speed_wps[0]-speed_wps[2])重建，源码按m/s使用；非直接PID内部日志',
                      target_formula_source='/home/buaa/wrh/simlingo/team_code/agent_simlingo.py:1027-1029',
                      brake_target=stats([t for t,c in zip(targets,controls) if c.get('brake',0)>0]),
                      pred_route_path_length_raw=stats(lengths),raw_route_is_not_world_distance=True,
                      brake_positive=sum(c.get('brake',0)>0 for c in controls),throttle_positive=sum(c.get('throttle',0)>0 for c in controls),
                      returned_brake=stats([c.get('brake') for c in controls]),returned_throttle=stats([c.get('throttle') for c in controls]),
                      recorded_navigation_commands=dict(navigation),recorded_light_states={k:dict(v) for k,v in lights.items()},
                      scenario_runtime_state=[r.get('scenario_state') for r in rows if r.get('scenario_state') is not None] or None,
                      scenario_state_missing_is_unknown=True)
    (REPORT/'evidence/B_OBSERVED_MOTION.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':describe()
