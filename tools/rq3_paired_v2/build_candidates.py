#!/usr/bin/env python3
"""仅构建前瞻资格候选，不生成正式种子、不执行驾驶策略。"""
import ast
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from driveclarify_language_grounding_v1.slot_parser import SemanticSlotParser
from driveclarify_rq1_v2.consequence import TaskSignature, compare_task_signatures
from driveclarify_rq3_paired_v2.task_evaluator import extract_task_outcome, score_task_truth, VERSION
from driveclarify_clear_passthrough_v11.replan import ReplanAdmissibility
from driveclarify_clear_passthrough_v11.contracts import AuthoritativeRoute, RoutePoint, EgoState

REPORT = ROOT / 'reports/driveclarify_rq3_paired_comparison_v2_task_binding_qualification_and_execution'
Q = REPORT / 'qualification'
ASSETS = REPORT / 'candidate_assets'
FAMILIES = ['REF-C', 'LMK-C', 'ORD-C', 'USC-C', 'REF-E', 'LMK-E', 'ORD-E']


def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write(p, v):
    p.write_text(json.dumps(v, indent=2, ensure_ascii=False) + '\n')


def witness(region, speed=0, duration=1.25):
    return [{'frame': i+1, 'simulation_time_s': i * .05, 'xyz': list(region['center_xyz']), 'yaw_degrees': region['yaw_degrees'], 'speed_mps': speed, 'bbox_extent_xy_m': [2.45, 1.08], 'lane_type': 'Parking', 'road_id': region['road_id'], 'lane_id': region['parking_lane_id']} for i in range(round(duration/.05)+1)]


def main():
    ASSETS.mkdir(exist_ok=True)
    probe = json.loads((Q / 'MAP_PROBE_RECEIPT.json').read_text())
    rows = probe['route_rows']
    nominal = [{'xyz': r['official_xyz'], 'road_option': 'LANEFOLLOW'} for r in rows]
    manifest = []
    checks = []
    geometry = []
    for condition in FAMILIES:
        family, level = condition.split('-')
        high = level == 'C'
        centers = [32, 50] if high else [50]
        if family == 'ORD' and not high:
            centers = [40]
        regions = []
        for j, index in enumerate(centers):
            row = rows[index]
            regions.append({'region_id': condition+'-BAY-'+str(j+1), 'center_xyz': row['parking_xyz'], 'yaw_degrees': row['parking_yaw'], 'half_length_m': 6.0, 'half_width_m': row['parking_width']/2, 'road_id': row['road_id'], 'parking_lane_id': -2, 'official_route_index': index, 'minimum_pause_s': 1.0, 'purpose': 'ACTUAL_LOCAL_PULLOVER_STOP_WITHIN_PARKING_LANE'})
        candidate_map = {'A': regions[0]['region_id'], 'B': regions[-1]['region_id']}
        if family == 'REF':
            text = 'Pull over in the marked bay beside the white van, pause briefly, then continue on the assigned route.'
            rationale = '两个可见白色厢式车指称；HIGH 对应不同合法停车区域，LOW 两车位于同一区域的两端且均指向同一停车任务。'
        elif family == 'LMK':
            text = 'Pull over in the marked bay by the building, pause briefly, then continue on the assigned route.'
            rationale = '标记停车区域相邻的两个真实建筑物地标；HIGH 各自对应一个区域，LOW 相邻两栋建筑共享同一标记区域。'
        elif family == 'ORD':
            text = 'Pull over at the second marked opening, pause briefly, then continue on the assigned route.'
            rationale = '计数所有标记开口，或计数可容纳本车的标记开口。HIGH 首个小型开口不容纳本车，两个计数的第二项分别是不同合法区域；LOW 首个开口可容纳本车，两种计数的第二项相同。'
        else:
            text = 'Pull over at a suitable distance in a marked bay, pause briefly, then continue on the assigned route.'
            rationale = '两个合法标记停车区域对应当前乘客意图中的较近或较远停靠距离；原文未给定距离约束。停靠任务后继续同一官方路线。'
        parsed = SemanticSlotParser().parse(text)
        paths = {}
        for candidate, center in [('A', centers[0]), ('B', centers[-1])]:
            path = []
            for i, row in enumerate(rows):
                # 16 m 过渡、12 m 停靠区域、16 m 汇合；最终点保持官方路线。
                offset = abs(i-center)
                if offset <= 3:
                    weight = 1.0
                elif offset < 11:
                    u = (offset-3)/8
                    weight = .5 * (1+math.cos(math.pi*u))
                else:
                    weight = 0.0
                point = [(1-weight)*a+weight*b for a,b in zip(row['official_xyz'], row['parking_xyz'])]
                path.append({'xyz': point, 'road_option': 'LANEFOLLOW'})
            paths['candidate_'+candidate] = path
        bindings = {'schema': 'driveclarify.rq3-paired-v2.task-binding.candidate.v1', 'template_id': 'V2-'+condition+'-01', 'evaluation_version': VERSION, 'regions': regions, 'candidate_region_map': candidate_map, 'unknown_policy': 'missing/corrupt authoritative trace => UNKNOWN; no qualifying stop in valid complete trace => task noncompletion', 'no_A1_internal_fields_used': True}
        signatures = []
        for c in ['A', 'B']:
            signatures.append({'candidate_id': c, 'certificate_id': 'V2-'+condition+'-GEOMETRY-PROVISIONAL-'+c, 'certified': True, 'relevant_components': ['task_completion_region'], 'task_completion_region': candidate_map[c]})
        comparison = compare_task_signatures(*[TaskSignature.from_mapping(x) for x in signatures])
        raw = {'instruction': text, 'official_route_path': probe['route'], 'official_route_sha256': hashlib.sha256(Path(probe['route']).read_bytes()).hexdigest(), 'official_destination': nominal[-1]['xyz'], 'nominal_route': nominal, 'world': probe['map'], 'layout_id': 'V2-'+condition+'-PUBLIC-LAYOUT-01'}
        # 两个真意反事实均构造相同 runtime 投影；truth 单独创建，不传入投影函数。
        truth_a = {'candidate_id': 'A'}
        truth_b = {'candidate_id': 'B'}
        runtime_a = dict(raw)
        runtime_b = dict(raw)
        neutrality = {'classification': 'ROUTE_NEUTRAL', 'runtime_projection_sha256_intent_A': digest(runtime_a), 'runtime_projection_sha256_intent_B': digest(runtime_b), 'counterfactual_runtime_equal': runtime_a == runtime_b, 'truth_argument_used_in_runtime_projection': False, 'performance_bias_tested': False, 'native_route_preference_is_not_evaluation_truth': True}
        assert runtime_a == runtime_b and truth_a != truth_b
        for region in regions:
            outcome = extract_task_outcome(witness(region), bindings)
            for gold in ['A', 'B']:
                scores = score_task_truth(outcome, bindings, {'candidate_id': gold})
                expected = int(candidate_map[gold] == region['region_id'])
                checks.append({'condition': condition, 'kind': 'SYNTHETIC_ONLY_NOT_MODEL_PERFORMANCE', 'region': region['region_id'], 'gold': gold, 'pass': scores['correct_goal'] == expected, 'arm_label_invariant': True})
            fast = extract_task_outcome(witness(region, speed=2), bindings)
            brief = extract_task_outcome(witness(region, duration=.5), bindings)
            checks.extend([{'condition': condition, 'check': 'moving_through_not_stop', 'pass': fast['completed_regions'] == []}, {'condition': condition, 'check': 'brief_stop_not_complete', 'pass': brief['completed_regions'] == []}])
        checks.append({'condition': condition, 'check': 'missing_trace_unknown', 'pass': extract_task_outcome([],bindings)['status']=='UNKNOWN'})
        entry = {'template_id': 'V2-'+condition+'-01', 'condition': condition, 'family': family, 'level': 'HIGH' if high else 'LOW', 'status': 'CANDIDATE_NOT_FULLY_QUALIFIED', 'instruction': text, 'family_rationale': rationale, 'parsed_slots': parsed.to_dict(), 'expected_family_kind': {'REF':'REFERENTIAL','LMK':'LANDMARK','ORD':'SPATIAL_ORDER','USC':'UNDERSPECIFIED_CONSTRAINT'}[family], 'task_relation_static': comparison.relation.value, 'semantic_certificate_scope': 'PROVISIONAL_PHYSICAL_REGION_BINDING_REQUIRES_NATIVE_LAYOUT_AND_INTERFACE_QUALIFICATION', 'task_signatures': signatures, 'official_route_context': raw, 'route_neutrality_static': neutrality, 'task_binding_path': str(ASSETS/(condition+'_TASK_BINDING.json')), 'candidate_route_path': str(ASSETS/(condition+'_CANDIDATE_ROUTES.json')), 'balanced_intents_structurally_available': high and len(regions)==2, 'formal_intents_allocated': False, 'formal_seeds': [], 'native_layout_verified': False, 'development_policy_performance_used': False}
        write(ASSETS/(condition+'_TASK_BINDING.json'), bindings)
        write(ASSETS/(condition+'_CANDIDATE_ROUTES.json'), {'schema':'driveclarify.v2.qualification-candidate-routes.v1','same_global_destination':True,**paths})
        write(ASSETS/(condition+'_PUBLIC_RUNTIME.json'), raw)
        manifest.append(entry)
    assert all(c['pass'] for c in checks)
    assert all(x['parsed_slots']['ambiguity_kind']==x['expected_family_kind'] for x in manifest)
    assert all(x['task_relation_static']==('TASK_CRITICAL' if x['level']=='HIGH' else 'TASK_EQUIVALENT') for x in manifest)
    write(REPORT/'V2_TEMPLATE_CANDIDATE_MANIFEST.json',{'stage':'RQ3_PAIRED_COMPARISON_V2_TASK_BINDING_QUALIFICATION_AND_EXECUTION','scope':'QUALIFICATION_ONLY_NOT_FROZEN','templates':manifest,'USC_E_excluded':True,'formal_seeds_generated':0,'formal_exposure':0})
    write(Q/'TASK_EVALUATOR_STATIC_CHECKS.json',{'scope':'SYNTHETIC_NON_POLICY_SOFTWARE_QUALIFICATION','checks':checks,'pass':all(x['pass'] for x in checks),'scientific_performance_estimates':False})
    print(json.dumps({'templates':len(manifest),'evaluator_checks':len(checks),'pass':True,'native_layout_qualified':False}))


if __name__ == '__main__':
    main()
