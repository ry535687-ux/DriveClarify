"""仅解析已存在的 A 记录；不导入 native、模型、控制器或 simulator。"""
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]
RAW = ROOT / 'experiments/driveclarify_native_clear_backend_dev_20260913/outputs/DEV_NATIVE_INIT_A_TOWN07_25968'


def stats(values):
    values = [v for v in values if v is not None]
    return {'n': len(values), 'min': min(values), 'median': statistics.median(values),
            'max': max(values)} if values else None


def points(value, count):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 1 or len(value[0]) != count:
        raise ValueError('INVALID_RECORDED_TENSOR_SHAPE')
    for p in value[0]:
        if not isinstance(p, list) or len(p) != 2 or any(
                isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in p):
            raise ValueError('INVALID_RECORDED_POINT')
    return value[0]


def target_from_record(value):
    p = points(value, 10)
    # agent_simlingo.py:1026-1029, GlobalConfig:12,17,18:
    # one_second=20//(1*5)=4; half_second=2; indices 0 and 2.
    # native 注释称 m/s；这里是源码公式重建值，并非直接记录的 PID 内部变量。
    return None if p is None else 2.0 * math.dist(p[0], p[2])


def route_summary(value):
    p = points(value, 20)
    if p is None:
        return None
    steps = [math.dist(a, b) for a, b in zip(p, p[1:])]
    return {'point_count': len(p), 'first_axis_delta_raw': p[-1][0] - p[0][0],
            'second_axis_delta_raw': p[-1][1] - p[0][1], 'path_length_raw': sum(steps),
            'min_segment_raw': min(steps), 'first_axis_strictly_increasing':
            all(b[0] > a[0] for a, b in zip(p, p[1:]))}


def lag_rows(rows, lag):
    out = []
    for i in range(len(rows) - lag):
        a, b = rows[i], rows[i + lag]
        if b['frame'] - a['frame'] != lag:
            continue
        if abs((b['sim_time'] - a['sim_time']) - lag * .05) > 1e-5:
            continue
        speed_delta = (None if a['ego_speed_if_recorded'] is None or b['ego_speed_if_recorded'] is None
                       else b['ego_speed_if_recorded'] - a['ego_speed_if_recorded'])
        distance = (None if any(v is None for v in [a['ego_x'], a['ego_y'], b['ego_x'], b['ego_y']])
                    else math.hypot(b['ego_x'] - a['ego_x'], b['ego_y'] - a['ego_y']))
        out.append({'frame': a['frame'], 'throttle': a['throttle'], 'brake': a['brake'],
                    'speed_delta_mps': speed_delta, 'xy_displacement_m': distance})
    return out


def analyze():
    raw = [json.loads(line) for line in (RAW / 'world_state.jsonl').read_text().splitlines()]
    probe = {r['observation_id']: r for r in map(json.loads, (RAW / 'probe.jsonl').read_text().splitlines())}
    rows, transitions = [], []
    last_lights = object()
    formula_mismatches, alignment_issues = [], []
    for r in raw:
        ctl, ego = r.get('baseline_control') or {}, r.get('ego') or {}
        target = target_from_record(r.get('pred_speed_wps_values'))
        route = route_summary(r.get('pred_route_values'))
        light_info = r.get('traffic_lights')
        lights = ({str(x['id']): x.get('state') for x in light_info['lights']}
                  if isinstance(light_info, dict) and light_info.get('query_ok') is True else None)
        context = r.get('route_context') or {}
        nav = (context.get('navigation_target_consumed') or {}).get('road_option')
        xyz = ego.get('location_xyz') or [None, None, None]
        v = ctl.get('gt_velocity')
        inferred_brake = None if target is None or v is None else (target < .4 or (target > 0 and v / target > 1.1))
        if inferred_brake is not None and inferred_brake != bool(ctl.get('brake')):
            formula_mismatches.append(r['snapshot_frame'])
        p = probe.get(r['observation_id'])
        if p is None or p['baseline_control'] != ctl or p['frame']['carla_snapshot_frame'] != r['snapshot_frame']:
            alignment_issues.append(r['snapshot_frame'])
        row = {'frame': r['snapshot_frame'], 'sim_time': r['snapshot_elapsed_seconds'],
               'gametime_seconds': r.get('gametime_seconds'), 'observation_id': r['observation_id'],
               'target_speed_raw_summary': target, 'pred_route_motion_raw_summary': route,
               'steer': ctl.get('steer'), 'throttle': ctl.get('throttle'), 'brake': ctl.get('brake'),
               'pid_gt_velocity_recorded': v, 'ego_speed_if_recorded': ego.get('speed_world_mps'),
               'ego_x': xyz[0], 'ego_y': xyz[1],
               'route_command_if_recorded': nav.get('name') if isinstance(nav, dict) else None,
               'traffic_light_if_recorded': lights, 'scenario_state_if_recorded': r.get('scenario_state')}
        rows.append(row)
        if lights != last_lights:
            transitions.append({'frame': row['frame'], 'sim_time': row['sim_time'], 'states': lights})
            last_lights = lights
    def subset_summary(ss):
        return {'target_reconstructed': stats([r['target_speed_raw_summary'] for r in ss]),
                'ego_speed_mps': stats([r['ego_speed_if_recorded'] for r in ss]),
                'pid_gt_velocity': stats([r['pid_gt_velocity_recorded'] for r in ss]),
                'xy_first_to_last_m': math.hypot(ss[-1]['ego_x'] - ss[0]['ego_x'], ss[-1]['ego_y'] - ss[0]['ego_y']) if ss else None}
    segments = []
    for r in rows:
        state = 'BRAKE' if r['brake'] > 0 else 'THROTTLE' if r['throttle'] > 0 else 'OTHER'
        if not segments or segments[-1]['state'] != state:
            segments.append({'state': state, 'rows': []})
        segments[-1]['rows'].append(r)
    segment_summaries = [{'state': s['state'], 'frame_start': s['rows'][0]['frame'],
        'frame_end': s['rows'][-1]['frame'], 'sim_start': s['rows'][0]['sim_time'],
        'sim_end': s['rows'][-1]['sim_time'], **subset_summary(s['rows'])} for s in segments]
    lags = {}
    for lag in [1, 5, 10]:
        lr = lag_rows(rows, lag)
        lags[str(lag)] = {name: {'pairs': len(sub), 'delta_speed_mps': stats([v['speed_delta_mps'] for v in sub]),
                                 'xy_displacement_m': stats([v['xy_displacement_m'] for v in sub])}
            for name, sub in [('initial_brake', [v for v in lr if v['brake'] > 0]),
                              ('initial_throttle', [v for v in lr if v['throttle'] > 0])]}
    actors = [a for r in raw for a in (r.get('actors') or {}).get('actors', [])]
    out = {'source': str(RAW.relative_to(ROOT)), 'world_sha256': hashlib.sha256((RAW/'world_state.jsonl').read_bytes()).hexdigest(),
           'derived_not_direct_pid_target_log': True, 'target_formula': '2 * norm(pred_speed_wps[0][0] - pred_speed_wps[0][2])',
           'native_target_semantics': 'native source treats formula result as m/s; no new coordinate/physical calibration',
           'row_count_for_alignment': len(rows), 'alignment_issues': alignment_issues,
           'overall': subset_summary(rows), 'segments': segment_summaries,
           'brake_target': stats([r['target_speed_raw_summary'] for r in rows if r['brake'] > 0]),
           'throttle_target': stats([r['target_speed_raw_summary'] for r in rows if r['throttle'] > 0]),
           'formula_brake_mismatch_frames': formula_mismatches,
           'route': {k: stats([r['pred_route_motion_raw_summary'][k] for r in rows])
                     for k in ['first_axis_delta_raw', 'path_length_raw', 'min_segment_raw']},
           'route_all_first_axis_increasing': all(r['pred_route_motion_raw_summary']['first_axis_strictly_increasing'] for r in rows),
           'lag_associations_only': lags, 'light_transitions': transitions,
           'route_commands': sorted(set(r['route_command_if_recorded'] for r in rows)),
           'actor_nearest_recorded_m': min(a['distance_m'] for a in actors if a.get('distance_m') is not None),
           'actor_types': sorted(set(a['type_id'] for a in actors)),
           'scenario_runtime_state': None, 'ego_applicable_traffic_light_id': None,
           'ACTUAL_APPLY_CONTROL_INDEPENDENTLY_VERIFIED': 'UNKNOWN',
           'model_wall_per_call_s': stats([r['model_end_monotonic_s']-r['model_start_monotonic_s'] for r in raw]),
           'special_frames': [r for r in rows if r['frame'] in [2227,2228,2375,2376,2437,2438,2439,2443,2448,2462]]}
    with (REPORT/'A_FRAME_ALIGNED_SUMMARY.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]));writer.writeheader()
        for row in rows:
            writer.writerow({k: 'null' if v is None else json.dumps(v,separators=(',',':')) if isinstance(v,dict) else v for k,v in row.items()})
    (REPORT/'evidence/A_ANALYSIS_NUMBERS.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in out.items() if k not in ['special_frames','lag_associations_only']},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    analyze()
