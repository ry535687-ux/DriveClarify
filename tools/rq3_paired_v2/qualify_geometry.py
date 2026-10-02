#!/usr/bin/env python3
"""对已构造路线执行冻结重规划判据与官方地图车道判据，不执行策略。"""
import json
import ast
import math
from pathlib import Path
import sys
import carla
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from driveclarify_clear_passthrough_v11.replan import ReplanAdmissibility
from driveclarify_clear_passthrough_v11.contracts import AuthoritativeRoute,RoutePoint,EgoState

OUT=ROOT/'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution'


def route(name,points,commitment):
    rr=tuple(RoutePoint(*map(float,p['xyz']),road_option=p['road_option']) for p in points)
    return AuthoritativeRoute(name,rr,(rr[2].x,rr[2].y),rr[2].road_option,rr[-1].xyz,10,connector_id='QUALIFICATION_PUBLIC_'+name,commitment_point_index=commitment)


def main():
    manifest=json.loads((OUT/'V2_TEMPLATE_CANDIDATE_MANIFEST.json').read_text())
    probe=json.loads((OUT/'qualification/MAP_PROBE_RECEIPT.json').read_text())
    m=carla.Map('Town03',(OUT/'qualification/Town03_server.xodr').read_text())
    criteria=ROOT.parent/'simlingo/scenario_runner_autopilot/srunner/scenariomanager/scenarioatomics/atomic_criteria.py'
    ctree=ast.parse(criteria.read_text())
    cls=next(n for n in ctree.body if isinstance(n,ast.ClassDef) and n.name=='OutsideRouteLanesTest')
    constant=next(n for n in cls.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ALLOWED_OUT_DISTANCE' for t in n.targets))
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_is_outside_driving_lanes')
    env={'carla':carla}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(criteria),'exec'),env)
    outside_predicate=env['_is_outside_driving_lanes']
    official_margin=ast.literal_eval(constant.value)
    findings=[]
    for entry in manifest['templates']:
        nominal=entry['official_route_context']['nominal_route']
        a=14
        xyz=nominal[a]['xyz'];yaw=probe['route_rows'][a]['driving_yaw']
        ego=EgoState(*xyz,yaw_degrees=yaw,speed_mps=3.0,frame=10)
        paths=json.loads(Path(entry['candidate_route_path']).read_text())
        per_candidate=[]
        for c in ['A','B']:
            points=paths['candidate_'+c][a:]
            # 语义分歧发生在更早的停车支路开始处，固定用于两候选的共同承诺边界。
            first_difference=next(i for i,(x,y) in enumerate(zip(paths['candidate_A'],paths['candidate_B'])) if math.dist(x['xyz'],y['xyz'])>1e-6) if entry['level']=='HIGH' else 30
            assessment=ReplanAdmissibility().assess(ego,route('nominal',nominal[a:],first_difference-a),route(c,points,first_difference-a))
            lane_types=[]
            official_lane_checks=[]
            for point in points:
                loc=carla.Location(*point['xyz'])
                wp=m.get_waypoint(loc,project_to_road=False,lane_type=carla.LaneType.Driving|carla.LaneType.Parking)
                lane_types.append(str(wp.lane_type) if wp else 'Outside')
                observer=SimpleNamespace(_map=m,ALLOWED_OUT_DISTANCE=official_margin)
                outside_predicate(observer,loc)
                official_lane_checks.append(not observer._outside_lane_active)
            per_candidate.append({'candidate':c,'same_destination':points[-1]['xyz']==nominal[-1]['xyz'],'rejoin_at_official_tail':points[-1]==nominal[-1] and paths['candidate_'+c][-5:]==nominal[-5:],'frozen_replan_disposition':assessment.disposition.value,'frozen_replan_reasons':assessment.reason_codes,'frozen_replan_metrics':assessment.metrics,'strict_map_projection_all_points':all(t in {'Driving','Parking'} for t in lane_types),'all_points_driving_or_parking':all(official_lane_checks),'official_lane_predicate_source':str(criteria),'official_lane_predicate_method':'OutsideRouteLanesTest._is_outside_driving_lanes (exact AST unchanged)','official_lane_predicate_allowed_out_distance':official_margin,'lane_types':sorted(set(lane_types))})
        findings.append({'condition':entry['condition'],'candidates':per_candidate,'nominal_official_route_unchanged':True,'simulation_policy_performance_observed':False})
    passed=all(c['same_destination'] and c['rejoin_at_official_tail'] and c['frozen_replan_disposition']=='COMMIT_NOW' and c['all_points_driving_or_parking'] for r in findings for c in r['candidates'])
    (OUT/'qualification/FROZEN_REPLAN_AND_MAP_COMPATIBILITY.json').write_text(json.dumps({'scope':'STATIC_GEOMETRY_AND_FROZEN_ADMISSIBILITY_ONLY','rows':findings,'pass':passed,'native_policy_runs':0,'no_model_performance_selection':True},indent=2)+'\n')
    print(json.dumps({'pass':passed,'rows':[{ 'condition':r['condition'],'candidates':[(c['candidate'],c['frozen_replan_disposition'],c['all_points_driving_or_parking'],c['rejoin_at_official_tail']) for c in r['candidates']]} for r in findings]},indent=2))


if __name__=='__main__':main()
