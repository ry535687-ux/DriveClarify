"""对两臂共用的局部停靠任务测量；不读取内部候选选择或控制命令。"""
from __future__ import annotations

import math

VERSION = 'LOCAL_STOP_REGION_V2_QUALIFICATION_1'
STOP_MAX_SPEED_MPS = 0.20
STOP_MIN_DURATION_S = 1.00
MAX_SAMPLE_GAP_S = 0.10
MAX_HEADING_ERROR_DEG = 20.0


def footprint_inside(row, region):
    theta = math.radians(region['yaw_degrees'])
    dx = row['xyz'][0] - region['center_xyz'][0]
    dy = row['xyz'][1] - region['center_xyz'][1]
    along = dx * math.cos(theta) + dy * math.sin(theta)
    across = -dx * math.sin(theta) + dy * math.cos(theta)
    angle = (row['yaw_degrees'] - region['yaw_degrees'] + 180) % 360 - 180
    angle_rad = math.radians(angle)
    ex, ey = row['bbox_extent_xy_m']
    longitudinal_extent = abs(ex * math.cos(angle_rad)) + abs(ey * math.sin(angle_rad))
    lateral_extent = abs(ex * math.sin(angle_rad)) + abs(ey * math.cos(angle_rad))
    if 'bbox_world_vertices' in row:
        # 真实 native 路径使用 CARLA 完整包围盒顶点，包含非零局部偏移及俯仰。
        projected=[((v[0]-region['center_xyz'][0])*math.cos(theta)+(v[1]-region['center_xyz'][1])*math.sin(theta),-(v[0]-region['center_xyz'][0])*math.sin(theta)+(v[1]-region['center_xyz'][1])*math.cos(theta)) for v in row['bbox_world_vertices']]
        body_fits=all(abs(a)<=region['half_length_m'] and abs(b)<=region['half_width_m'] for a,b in projected)
    else:
        # 仅保留已有静态软件/测量夹具入口；完整正式入口禁止缺少真实顶点。
        body_fits=abs(along)+longitudinal_extent<=region['half_length_m'] and abs(across)+lateral_extent<=region['half_width_m']
    return (
        body_fits
        and abs(row.get('roll_degrees',0))<=20
        and abs(row.get('pitch_degrees',0))<=20
        and abs(row['xyz'][2] - region['center_xyz'][2]) <= 2.0
        and abs(angle) <= MAX_HEADING_ERROR_DEG
        and row['lane_type'] == 'Parking'
        and row['road_id'] == region['road_id']
        and row['lane_id'] == region['parking_lane_id']
    )


def extract_task_outcome(trace, binding):
    """仅提取已执行区域身份；无 passenger truth 参数。"""
    required = {'frame', 'simulation_time_s', 'xyz', 'yaw_degrees', 'speed_mps', 'bbox_extent_xy_m', 'lane_type', 'road_id', 'lane_id'}
    if not trace:
        return {'status': 'UNKNOWN', 'reason': 'EMPTY_TRACE', 'completed_regions': None}
    previous = None
    for row in trace:
        if not required.issubset(row):
            return {'status': 'UNKNOWN', 'reason': 'MISSING_STATE_FIELD', 'completed_regions': None}
        numeric = [row['simulation_time_s'], row['yaw_degrees'], row['speed_mps'], *row['xyz'], *row['bbox_extent_xy_m']]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in numeric):
            return {'status': 'UNKNOWN', 'reason': 'NONFINITE_STATE', 'completed_regions': None}
        if len(row['xyz'])!=3 or len(row['bbox_extent_xy_m'])!=2 or row['speed_mps']<0 or min(row['bbox_extent_xy_m'])<=0:
            return {'status': 'UNKNOWN', 'reason': 'INVALID_STATE_DIMENSION_OR_RANGE', 'completed_regions': None}
        if previous is not None and (row['frame'] <= previous['frame'] or row['simulation_time_s'] <= previous['simulation_time_s']):
            return {'status': 'UNKNOWN', 'reason': 'NONMONOTONE_TRACE', 'completed_regions': None}
        if previous is not None and row['simulation_time_s'] - previous['simulation_time_s'] > MAX_SAMPLE_GAP_S + 1e-9:
            return {'status': 'UNKNOWN', 'reason': 'UNOBSERVED_TIME_GAP', 'completed_regions': None}
        previous = row
    events = []
    durations = {}
    for region in binding['regions']:
        start = None
        previous_time = None
        max_duration = 0.0
        for row in trace:
            t = row['simulation_time_s']
            is_stop = row['speed_mps'] <= STOP_MAX_SPEED_MPS and footprint_inside(row, region)
            gap = previous_time is not None and t - previous_time > MAX_SAMPLE_GAP_S + 1e-9
            if not is_stop:
                start = None
            elif start is None or gap:
                start = t
            if is_stop:
                duration = t - start
                max_duration = max(max_duration, duration)
            previous_time = t
        durations[region['region_id']] = max_duration
        if max_duration + 1e-9 >= STOP_MIN_DURATION_S:
            events.append(region['region_id'])
    return {'status': 'KNOWN', 'completed_regions': sorted(events), 'stop_duration_s': durations, 'binding_version': VERSION}


def _extract_complete_episode_checked(trace, binding, receipt, trace_sha256, official_duration_s):
    """正式调用入口：未证明完整记录的片段不能产生 task noncompletion。"""
    def unknown(reason):return {'status':'UNKNOWN','reason':reason,'completed_regions':None}
    if not receipt or receipt.get('destroy_observed') is not True:return unknown('NO_TERMINAL_RECEIPT')
    if receipt.get('trace_errors'):return unknown('OBSERVER_ERRORS')
    if trace_sha256!=receipt.get('trace_sha256'):return unknown('TRACE_HASH_MISMATCH')
    if not trace or len(trace)!=receipt.get('trace_count'):return unknown('TRACE_COUNT_MISMATCH')
    if any(len(x.get('bbox_world_vertices',[]))!=8 or 'roll_degrees' not in x or 'pitch_degrees' not in x for x in trace):return unknown('MISSING_NATIVE_BODY_GEOMETRY')
    if any(len(v)!=3 for x in trace for v in x['bbox_world_vertices']):return unknown('INVALID_NATIVE_BODY_VERTEX_DIMENSION')
    if any(not math.isfinite(x[k]) for x in trace for k in ['roll_degrees','pitch_degrees']):return unknown('NONFINITE_NATIVE_BODY_ROTATION')
    if any(not math.isfinite(v) for x in trace for vertex in x['bbox_world_vertices'] for v in vertex):return unknown('NONFINITE_NATIVE_BODY_GEOMETRY')
    if trace[0]['frame']!=receipt.get('first_frame') or trace[-1]['frame']!=receipt.get('last_frame'):return unknown('TRACE_BOUNDARY_MISMATCH')
    count=receipt.get('control_return_count',-1)
    if len(trace) not in (count,count+1):return unknown('CONTROL_OBSERVATION_COVERAGE_MISMATCH')
    if any(b['frame']!=a['frame']+1 for a,b in zip(trace,trace[1:])):return unknown('MISSING_WORLD_FRAME')
    if not isinstance(official_duration_s,(int,float)) or not math.isfinite(official_duration_s):return unknown('NO_OFFICIAL_EPISODE_DURATION')
    span=trace[-1]['simulation_time_s']-trace[0]['simulation_time_s']
    if abs(span-official_duration_s)>MAX_SAMPLE_GAP_S+1e-9:return unknown('OFFICIAL_DURATION_COVERAGE_MISMATCH')
    return extract_task_outcome(trace,binding)


def extract_complete_episode(trace, binding, receipt, trace_sha256, official_duration_s):
    try:
        return _extract_complete_episode_checked(trace,binding,receipt,trace_sha256,official_duration_s)
    except (KeyError,TypeError,ValueError,IndexError):
        return {'status':'UNKNOWN','reason':'CORRUPT_NATIVE_TRACE_OR_ENVELOPE','completed_regions':None}


def score_task_truth(outcome, binding, evaluation_truth):
    """独立离线 truth join；此函数不能由任何驾驶 arm 导入。"""
    if outcome['status'] != 'KNOWN':
        return {'correct_goal': None, 'wrong_goal': None, 'status': 'UNKNOWN'}
    expected = binding['candidate_region_map'][evaluation_truth['candidate_id']]
    observed = outcome['completed_regions']
    correct = observed == [expected]
    wrong = bool(observed) and not correct
    return {'correct_goal': int(correct), 'wrong_goal': int(wrong), 'status': 'KNOWN'}
