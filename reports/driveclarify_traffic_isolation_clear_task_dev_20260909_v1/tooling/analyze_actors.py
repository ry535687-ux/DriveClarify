#!/usr/bin/env python3
"""Snapshot and audit current DEV actor sources using stdlib only.

No runtime imports or mutations. JSONL snapshots retain the bytes observed;
analysis excludes incomplete trailing lines and records after one fixed cutoff.
Unknown actors are retained. Spawn API calls are never summed as actor counts.
"""
import argparse
import ast
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

REPO = Path('/home/buaa/wrh/DriveClarify')
SIM = Path('/home/buaa/wrh/simlingo')
OLD = REPO / 'reports/driveclarify_ablation_overnight_20260909_v1'
CORE = SIM / 'scenario_runner_autopilot/srunner/scenarios/object_crash_vehicle.py'
BA = SIM / 'scenario_runner_autopilot/srunner/scenarios/background_activity.py'
ROUTE = SIM / 'leaderboard_autopilot/leaderboard/scenarios/route_scenario.py'
PROVIDER = SIM / 'scenario_runner_autopilot/srunner/scenariomanager/carla_data_provider.py'
ENTRIES = {'_spawn_actor', '_spawn_actors', '_spawn_source_actor'}
OBS_FILES = ['ACTOR_SPAWN_TIMELINE.jsonl', 'WORLD_ACTOR_TIMELINE.jsonl',
             'COLLISION_SOURCE_TIMELINE.jsonl', 'BACKGROUND_GATE_TIMELINE.jsonl',
             'OBSERVER_INSTALLATION.jsonl', 'OBSERVER_SUMMARY.jsonl',
             'OBSERVER_ERRORS.jsonl', 'RUNTIME_POLICY.json']
OWNER_FILES = ['V2_PUBLIC_SCENE_RECEIPT.json', 'V2_NATIVE_STATE_TRACE.jsonl',
               'V11_RUNTIME_HEARTBEAT.json', 'CLEAR_TASK_CONTROL_PLAN.jsonl',
               'CLEAR_TASK_EVENTS.jsonl', 'V2_TRACE_TERMINAL_RECEIPT.json']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def write_csv(path, rows, minimum_fields=('run_id',)):
    fields = list(dict.fromkeys(k for row in rows for k in row)) or list(minimum_fields)
    with path.open('x', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: ('UNKNOWN' if v is None else json.dumps(v, ensure_ascii=False)
                            if isinstance(v, (dict, list, bool)) else v) for k, v in row.items()})


def event_ns(row):
    if row.get('wall_time_ns') is not None:
        return int(row['wall_time_ns'])
    if row.get('wall_time_epoch') is not None:
        return int(row['wall_time_epoch'] * 1e9)
    return None


class Capture:
    def __init__(self, output):
        self.output = output
        self.cutoff_ns = time.time_ns()
        self.index = []

    def get(self, source, local):
        source = Path(source)
        target = self.output / 'raw_snapshot' / local
        row = {'original_path': str(source), 'snapshot_path': str(target)}
        if not source.exists():
            row['status'] = 'MISSING_AT_SNAPSHOT'
            self.index.append(row)
            return [] if source.suffix == '.jsonl' else None
        before = source.stat()
        data = source.read_bytes()
        after = source.stat()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write(data)
        row.update(status='CAPTURED', sha256=digest(data), bytes=len(data),
                   source_size_before=before.st_size, source_size_after=after.st_size,
                   source_changed_during_read=(before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size))
        self.index.append(row)
        if source.suffix != '.jsonl':
            try:
                return json.loads(data)
            except (ValueError, UnicodeDecodeError) as e:
                row['parse_error'] = type(e).__name__
                return None
        complete = data[:data.rfind(b'\n') + 1]
        row['trailing_incomplete_bytes_excluded'] = len(data) - len(complete)
        parsed, errors, future = [], [], 0
        for n, line in enumerate(complete.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                errors.append(n)
                continue
            ns = event_ns(value)
            if ns is not None and ns > self.cutoff_ns:
                future += 1
                continue
            value['_snapshot_line'] = n
            parsed.append(value)
        row.update(parsed_rows=len(parsed), malformed_complete_lines=errors,
                   rows_after_fixed_cutoff_excluded=future)
        return parsed


def core_call_sites(source_text):
    """Map the actual AST call site, not an actor type/id, to core ownership."""
    result = []
    tree = ast.parse(source_text)
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name != 'ParkingCrossingPedestrian':
            continue
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                    continue
                call = node.value
                if not isinstance(call.func, ast.Attribute) or call.func.attr != 'request_new_actor':
                    continue
                names = [x.id for x in node.targets if isinstance(x, ast.Name)]
                role = ('CORE_BLOCKER' if 'blocker' in names else
                        'CORE_WALKER_REPLACEMENT' if fn.name == '_replace_walker' else
                        'CORE_WALKER_INITIAL' if 'walker' in names else 'UNKNOWN')
                result.append((node.lineno, node.end_lineno, fn.name, role))
    return result


def request_role(request, sites):
    stack = request.get('caller_stack', [])
    for frame in reversed(stack):
        file = frame.get('file')
        if file == str(CORE):
            for start, end, fn, role in sites:
                if start <= frame.get('line', -1) <= end and frame.get('function') == fn:
                    return role, 'E', 'EXACT_REQUEST_CALL_SITE_IN_ParkingCrossingPedestrian'
        if file == str(BA) and frame.get('function') in ENTRIES:
            return 'ORDINARY_BACKGROUND', 'F', 'EXACT_REQUEST_CALL_SITE_IN_BACKGROUND_GENERATOR'
        if file == str(ROUTE) and frame.get('function') == '_spawn_ego_vehicle':
            return 'EGO', 'A', 'EXACT_REQUEST_CALL_SITE_IN_EGO_SPAWNER'
    if request.get('api') == 'RouteScenario.spawn_parked_vehicles':
        return 'MAP_PARKED_MESH', 'MAP_STATIC_RETAIN', 'EXACT_NATIVE_PARKED_ID_DELTA'
    return 'UNKNOWN', 'G', 'SOURCE_OR_ROLE_NOT_CERTIFIED'


def pose(value):
    if not value:
        return None
    return {'location_m': value.get('location_m', value.get('location')),
            'rotation_degrees': value.get('rotation_degrees', value.get('rotation'))}


def pose_delta(a, b):
    a, b = pose(a), pose(b)
    if not a or not b or not a['location_m'] or not b['location_m']:
        return None, None
    loc = math.sqrt(sum((a['location_m'][k] - b['location_m'][k]) ** 2 for k in ('x', 'y', 'z')))
    aa, bb = a.get('rotation_degrees'), b.get('rotation_degrees')
    angle = max(abs((aa[k] - bb[k] + 180) % 360 - 180) for k in ('pitch', 'yaw', 'roll')) if aa and bb else None
    return loc, angle


def physical_type(blueprint):
    # Physical type describes the actor; it never assigns source/retention role.
    value = blueprint or ''
    return ('VEHICLE' if value.startswith('vehicle.') else 'PEDESTRIAN' if value.startswith('walker.') else
            'STATIC_PROP' if value.startswith('static.') else 'SENSOR' if value.startswith('sensor.') else
            'TRAFFIC_OR_MAP_ENTITY' if value.startswith('traffic.') else 'UNKNOWN')


def four_way_collision_class(actor):
    kind = physical_type(actor.get('blueprint'))
    role = actor.get('role', 'G')
    if role == 'G':
        return 'SOURCE_UNKNOWN'
    if kind == 'STATIC_PROP':
        return 'STATIC_ROAD_OR_ENVIRONMENT_OBJECT'
    if kind == 'VEHICLE' and role == 'F':
        return 'RANDOM_UNRELATED_BACKGROUND_VEHICLE'
    if kind == 'VEHICLE' and role in {'B|C', 'E'}:
        return 'TASK_RELATED_OR_SCENE_CORE_VEHICLE'
    if kind == 'PEDESTRIAN' and role == 'E':
        return 'OUTSIDE_FOUR_CLASSES_CORE_NONVEHICLE'
    return 'OUTSIDE_FOUR_CLASSES_KNOWN_SOURCE'


def native_terminal_complete(process, official, cutoff_ns):
    if not process or process.get('finished_epoch', 1e30) * 1e9 > cutoff_ns or process.get('evaluator_exit') != 0:
        return False
    records = (official or {}).get('_checkpoint', {}).get('records', [])
    if len(records) != 1:
        return False
    status = records[0].get('status', '')
    # An evaluator can catch an agent exception and exit 0 with a failed record.
    technical = ['Simulation crashed', 'Agent crashed', "Agent couldn't be set up", "Agent's sensors were invalid"]
    if any(reason in status for reason in technical):
        return False
    return status == 'Perfect' or status == 'Completed' or status.startswith('Failed')


def accepted_clock_fields(frame, trace_by_frame, controls_by_frame):
    return {
        'original_criterion_same_frame_world_elapsed_s': trace_by_frame.get(frame, {}).get('simulation_time_s'),
        'original_criterion_same_frame_agent_game_time_s': controls_by_frame.get(frame, {}).get('timestamp_argument'),
    }


def analyze_run(run, cap, sites, source_hashes):
    rid = run['run_id']
    output = Path(run['output'])
    logs = {}
    capture_index_start = len(cap.index)
    for name in OBS_FILES:
        logs[name] = cap.get(output / 'diagnostic_observer' / name, Path(rid) / 'diagnostic_observer' / name)
    for name in OWNER_FILES:
        logs[name] = cap.get(output / 'owner_evidence' / name, Path(rid) / 'owner_evidence' / name)
    process = cap.get(output / 'process_job/PROCESS_RECEIPT.json', Path(rid) / 'process_job/PROCESS_RECEIPT.json')
    official = cap.get(output / 'official_checkpoint.json', Path(rid) / 'official_checkpoint.json')
    config = cap.get(run['config_path'], Path(rid) / 'config.json')
    source_config = cap.get(run['source_config'], Path(rid) / 'source_config.json')
    layout = cap.get(OLD / 'scenarios/public_assets' / (run['template'] + '_LAYOUT.json'), Path(rid) / 'public_layout.json')
    assert config and source_config and layout
    assert digest(Path(run['config_path']).read_bytes()) == run['config_sha256'], 'Prospective config drift'
    planned = not logs['ACTOR_SPAWN_TIMELINE.jsonl'] and not logs['WORLD_ACTOR_TIMELINE.jsonl']
    finished = bool(process and process.get('finished_epoch', 1e30) * 1e9 <= cap.cutoff_ns)
    records = (official or {}).get('_checkpoint', {}).get('records', [])
    complete = native_terminal_complete(process, official, cap.cutoff_ns)
    status = 'COMPLETE_EPISODE' if complete else 'TECHNICAL_OR_MISSING_ENDPOINT' if finished else 'PLANNED' if planned else 'RUNNING_SNAPSHOT'
    spawn = logs['ACTOR_SPAWN_TIMELINE.jsonl'] or []
    world = logs['WORLD_ACTOR_TIMELINE.jsonl'] or []
    gate = logs['BACKGROUND_GATE_TIMELINE.jsonl'] or []
    callbacks = logs['COLLISION_SOURCE_TIMELINE.jsonl'] or []
    requests = {r['request_id']: r for r in spawn if r['event'] == 'SPAWN_API_REQUEST'}
    classified = {key: request_role(value, sites) for key, value in requests.items()}
    # Parent request IDs preserve ownership if a nested wrapper's stack is short.
    for _ in range(4):
        for key, request in requests.items():
            parent = request.get('parent_request_id')
            if classified[key][0] == 'UNKNOWN' and parent in classified:
                classified[key] = classified[parent]
    actors = {}
    base = {k: run[k] for k in ('run_id', 'pair_id', 'template', 'seed', 'condition', 'traffic_condition')}

    def get_actor(actor_id):
        return actors.setdefault(actor_id, dict(base, actor_id=actor_id, blueprint=None, logical_role='UNKNOWN',
            role='G', provenance='SOURCE_OR_ROLE_NOT_CERTIFIED', source_request_ids=[],
            spawn_request=None, spawn_return_transform=None, spawn_request_frame=None, spawn_return_frame=None,
            spawn_request_world_time_s=None, spawn_return_world_time_s=None, first_seen_frame=None,
            first_world_transform=None, public_configured_pose=None, retain_action='RETAIN_UNKNOWN',
            evidence_lines=[], caller_stack=[]))

    for r in spawn:
        if r['event'] not in {'SPAWN_API_RETURN', 'PARKED_MESH_NATIVE_METHOD_RESULT'}:
            continue
        states = r.get('returned_actors', r.get('returned_actor_states', []))
        request = requests.get(r['request_id'], {})
        ids = set(r.get('newly_recorded_parked_actor_ids', [])) | {a['actor_id'] for a in states}
        for actor_id in ids:
            a = get_actor(actor_id)
            role, letter, provenance = classified.get(r['request_id'], ('UNKNOWN', 'G', 'SOURCE_OR_ROLE_NOT_CERTIFIED'))
            if r['event'] == 'PARKED_MESH_NATIVE_METHOD_RESULT':
                role, letter, provenance = 'MAP_PARKED_MESH', 'MAP_STATIC_RETAIN', 'EXACT_NATIVE_PARKED_ID_DELTA'
            if role != 'UNKNOWN':
                a.update(logical_role=role, role=letter, provenance=provenance, retain_action='SOURCE_GATE_REQUIRED' if letter == 'F' else 'RETAIN_NATIVE_BEHAVIOR')
            if r['request_id'] not in a['source_request_ids']:
                a['source_request_ids'].append(r['request_id'])
            a['evidence_lines'].append('ACTOR_SPAWN_TIMELINE.jsonl:' + str(r['_snapshot_line']))
            if a['spawn_request'] is None:
                a.update(spawn_request=request.get('request'), caller_stack=request.get('caller_stack', []),
                         spawn_request_frame=request.get('simulation_clock', {}).get('frame'),
                         spawn_request_world_time_s=request.get('simulation_clock', {}).get('simulation_time_s'),
                         spawn_return_frame=r.get('simulation_clock', {}).get('frame'),
                         spawn_return_world_time_s=r.get('simulation_clock', {}).get('simulation_time_s'))
            for state in states:
                if state['actor_id'] == actor_id:
                    a['blueprint'] = state.get('blueprint_type_id')
                    a['spawn_return_transform'] = state.get('transform')
    world_ids = set()
    for r in world:
        world_ids.update(r.get('all_actor_ids', []))
        for state in r.get('actor_states', []):
            a = get_actor(state['actor_id'])
            a['blueprint'] = state.get('blueprint_type_id') or a['blueprint']
            if a['first_seen_frame'] is None:
                a['first_seen_frame'] = r.get('source_frame')
                a['first_world_transform'] = state.get('transform')
                a['evidence_lines'].append('WORLD_ACTOR_TIMELINE.jsonl:' + str(r['_snapshot_line']))
        if r.get('ego_actor_id') is not None:
            get_actor(r['ego_actor_id']).update(logical_role='EGO', role='A', provenance='EXACT_WORLD_CALLER_EGO_ID', retain_action='RETAIN_NATIVE_BEHAVIOR')
    for actor_id in world_ids:
        get_actor(actor_id)
    public = (logs['V2_PUBLIC_SCENE_RECEIPT.json'] or {}).get('actors', [])
    public_checks = []
    for p in public:
        match = [x for x in layout['actors'] if x['blueprint'] == p['blueprint'] and x['pose'] == p['pose']]
        cfg = [x for x in config['method_input']['runtime_actors'] if x['runtime_track_id'] == p['runtime_track_id'] and x['blueprint'] == p['blueprint'] and x['pose'] == p['pose']]
        assert len(match) == len(cfg) == 1, (rid, 'Public source ambiguity')
        a = get_actor(p['actor_id'])
        a.update(logical_role='PUBLIC_' + p['runtime_track_id'], role='B|C',
                 provenance='EXACT_PUBLIC_SPAWN_RECEIPT_CONFIG_LAYOUT_JOIN',
                 blueprint=p['blueprint'], public_configured_pose=p['pose'], retain_action='RETAIN_BOTH_CANDIDATES',
                 scientific_role=match[0]['scientific_role'])
        a['evidence_lines'].append('V2_PUBLIC_SCENE_RECEIPT.json')
        poserr, roterr = pose_delta(p['pose'], a['first_world_transform'])
        public_checks.append(dict(run_id=rid, actor_id=p['actor_id'], logical_role=a['logical_role'], blueprint=p['blueprint'],
            world_observed=a['first_seen_frame'] is not None, source_config_exact_match=True,
            first_world_position_error_m=poserr, first_world_rotation_error_degrees=roterr,
            numerical_pose_check=(poserr <= 0.001 and roterr <= 0.001) if poserr is not None and roterr is not None else None,
            tolerance='0.001m / 0.001degree for readback floating point; not a task outcome threshold'))
    accepted = []
    for r in callbacks:
        state = r.get('other_actor') or {}
        if state.get('actor_id') is not None:
            get_actor(state['actor_id'])['blueprint'] = state.get('blueprint_type_id')
        for event in r.get('accepted_original_events', []):
            accepted.append((r, event))
    trace = {r['frame']: r for r in logs['V2_NATIVE_STATE_TRACE.jsonl'] or []}
    controls_by_frame = {r['frame']: r for r in logs['CLEAR_TASK_CONTROL_PLAN.jsonl'] or []}
    def collision_row(kind, callback, event=None):
        state = (event or {}).get('other_actor', callback.get('other_actor')) or {}
        actor = get_actor(state.get('actor_id')) if state.get('actor_id') is not None else {}
        frame = event.get('frame') if event else callback.get('collision_frame')
        category = ('TASK_RELATED_OR_CORE' if actor.get('role') in {'B|C', 'E'} else
                    'ORDINARY_BACKGROUND' if actor.get('role') == 'F' else
                    'MAP_STATIC_OBJECT' if actor.get('logical_role') == 'MAP_PARKED_MESH' else 'UNKNOWN')
        return dict(base, status=status, event_kind=kind, actor_id=state.get('actor_id'),
            blueprint=state.get('blueprint_type_id'), logical_role=actor.get('logical_role', 'UNKNOWN'),
            role=actor.get('role', 'G'), source_category=category, source_provenance=actor.get('provenance'),
            physical_actor_type=physical_type(state.get('blueprint_type_id')),
            user_four_way_collision_category=four_way_collision_class(actor),
            event_frame=frame, raw_sensor_timestamp_s=callback.get('collision_timestamp_s') if not event else None,
            **(accepted_clock_fields(frame, trace, controls_by_frame) if event else
               {'original_criterion_same_frame_world_elapsed_s': None,
                'original_criterion_same_frame_agent_game_time_s': None}),
            callback_observer_world_frame=callback.get('simulation_clock', {}).get('frame'),
            callback_observer_world_time_s=callback.get('simulation_clock', {}).get('simulation_time_s'),
            sensor_and_criterion_clocks_kept_separate=True, source_request_ids=actor.get('source_request_ids', []),
            callback_line=callback['_snapshot_line'], event_message=(event or {}).get('message'),
            original_criterion_accepted_count=callback.get('accepted_original_event_count'),
            evidence_snapshot=str(cap.output / 'raw_snapshot' / rid / 'diagnostic_observer/COLLISION_SOURCE_TIMELINE.jsonl'))
    collision_ledger = [collision_row('RAW_CALLBACK', r) for r in callbacks]
    collision_ledger += [collision_row('ORIGINAL_CRITERION_ACCEPTED', r, e) for r, e in accepted]
    raw_first = min((r for r in collision_ledger if r['event_kind']=='RAW_CALLBACK'), key=lambda r: r['event_frame'] if r['event_frame'] is not None else float('inf'), default=None)
    accepted_first = min((r for r in collision_ledger if r['event_kind']=='ORIGINAL_CRITERION_ACCEPTED'), key=lambda r: r['event_frame'] if r['event_frame'] is not None else float('inf'), default=None)
    collisions_summary = dict(base, status=status, complete_episode=complete,
        raw_callback_count=len(callbacks), original_accepted_count=len(accepted),
        first_raw_callback=raw_first, first_original_accepted=accepted_first,
        no_collision_endpoint=(not callbacks and not accepted) if complete else None,
        missing_reason=None if complete else 'NOT_A_COMPLETE_EPISODE_NO_FULL_HORIZON_CLAIM')
    actual_ba = [a for a in actors.values() if a['role']=='F']
    gate_entries = Counter(r.get('entry') for r in gate)
    slots = sum(r.get('requested_source_slots', 0) for r in gate)
    late = [r for r in gate if 'initialise' not in r.get('caller_functions', [])]
    first_gate_frame = min((r['frame'] for r in gate if r.get('frame') is not None), default=None)
    core_actors = [a for a in actors.values() if a['role']=='E']
    core_before = bool(core_actors) and first_gate_frame is not None and all(a['spawn_return_frame'] is not None and a['spawn_return_frame'] < first_gate_frame for a in core_actors)
    seeds = sorted({r['simulation_clock']['carla_data_provider_random_seed'] for r in spawn + world
                    if r.get('simulation_clock', {}).get('carla_data_provider_random_seed') is not None})
    summaries = logs['OBSERVER_SUMMARY.jsonl'] or []
    summary = summaries[-1].get('summary', {}) if summaries else {}
    errors = logs['OBSERVER_ERRORS.jsonl'] or []
    installations = logs['OBSERVER_INSTALLATION.jsonl'] or []
    covered = {x['api'] for r in installations for x in r.get('covered_spawn_apis', [])}
    policy = logs['RUNTIME_POLICY.json'] or {}
    unknown_vehicles = [a['actor_id'] for a in actors.values() if a['role']=='G' and (a['blueprint'] or '').startswith('vehicle.')]
    gate_ok = bool(gate) and all(r.get('actual_spawned') == 0 and r.get('actor_destroy_calls') == 0 and not r.get('global_traffic_manager_settings_modified') for r in gate)
    actor_cfg_same = config['method_input']['runtime_actors'] == source_config['method_input']['runtime_actors']
    cpd_request_ids = {r['request_id'] for r in spawn if r['event']=='SPAWN_API_REQUEST' and r.get('api')!='RouteScenario.spawn_parked_vehicles'}
    cpd_return_ids = {r['request_id'] for r in spawn if r['event']=='SPAWN_API_RETURN'}
    capture_errors = [r['original_path'] for r in cap.index[capture_index_start:] if
                      r.get('parse_error') or r.get('malformed_complete_lines') or r.get('trailing_incomplete_bytes_excluded')]
    check = dict(base, status=status, complete_episode=complete,
        background_gate_installed_three_entries=(set(policy.get('intercepted_background_entries', []))==ENTRIES and policy.get('background_gate_enabled') is True),
        blocked_source_call_count=len(gate), blocked_source_calls_by_entry=dict(gate_entries),
        blocked_requested_source_slots=slots, slot_semantics='ATTEMPTS_BEFORE_NATIVE_DISTANCE_FILTER_NOT_WOULD_HAVE_SPAWNED',
        actual_unique_successful_CPD_background_actor_count=len(actual_ba),
        actual_unique_successful_CPD_background_actor_ids=[a['actor_id'] for a in actual_ba],
        late_source_blocked_call_count=len(late), late_source_callers=dict(Counter(f for r in late for f in r.get('caller_functions', [])[:1])),
        first_gate_frame=first_gate_frame, last_gate_frame=max((r['frame'] for r in gate if r.get('frame') is not None), default=None),
        gate_records_keep_native_tm_and_no_destroy=gate_ok,
        cpd_observer_required_apis_covered={'request_new_actor','request_new_actors','request_new_batch_actors','handle_actor_batch'}<=covered,
        cpd_requests_missing_return_ids=sorted(cpd_request_ids - cpd_return_ids),
        observed_public_actor_count=len(public), expected_public_actor_count=len(config['method_input']['runtime_actors']),
        public_config_equal_to_previous_condition=actor_cfg_same,
        public_receipt_and_world_pose_checks=all(x['numerical_pose_check'] for x in public_checks) if public_checks else None,
        core_actor_roles=[a['logical_role'] for a in core_actors], core_created_before_first_background_gate=core_before if not planned else None,
        provider_seed_values=seeds, provider_seed2000_observed=(seeds==[2000]) if seeds else None,
        provider_rng_state_digest_recorded=False,
        core_behavior_source_unchanged=source_hashes['all_match_previous_formal_freeze'],
        parked_native_call_count=sum(r['event']=='PARKED_MESH_NATIVE_METHOD_RESULT' for r in spawn),
        actual_unique_parked_mesh_actor_ids=[a['actor_id'] for a in actors.values() if a['logical_role']=='MAP_PARKED_MESH'],
        unknown_vehicle_ids=unknown_vehicles, unknown_all_actor_count=sum(a['role']=='G' for a in actors.values()),
        world_inventory_rows=len(world), world_inventory_all_complete=all(r.get('inventory_complete') is True for r in world) if world else None,
        first_world_frame=world[0].get('source_frame') if world else None, last_world_frame=world[-1].get('source_frame') if world else None,
        observer_summary_present=bool(summaries), observer_error_rows=len(errors), observer_summary_error_count=summary.get('error_count'),
        observer_actor_state_field_read_failure_count=summary.get('actor_state_field_read_failure_count'),
        snapshot_parse_gap_files=capture_errors,
        observer_accepted_count_reconciled=(summary.get('accepted_original_collision_events')==len(accepted)) if summaries else None,
        official_accepted_count=sum(len(v) for rec in records for k,v in rec.get('infractions',{}).items() if k.startswith('collisions_')) if complete else None,
        full_episode_evidence_status='PENDING_NOT_TERMINAL',
        uncovered_entry_points=summary.get('uncovered_entry_points', [s for r in installations for s in r.get('uncovered_entry_points', [])]))
    if complete:
        checks = [check['background_gate_installed_three_entries'], gate_ok, not actual_ba, not unknown_vehicles,
                  check['cpd_observer_required_apis_covered'], len(public)==check['expected_public_actor_count'],
                  actor_cfg_same, check['public_receipt_and_world_pose_checks'], core_before, seeds==[2000],
                  check['core_behavior_source_unchanged'], bool(world), check['world_inventory_all_complete'],
                  bool(summaries), not errors, summary.get('error_count')==0,
                  summary.get('actor_state_field_read_failure_count')==0, not capture_errors,
                  not check['cpd_requests_missing_return_ids'],
                  check['observer_accepted_count_reconciled'], check['official_accepted_count']==len(accepted)]
        check['full_episode_evidence_status'] = 'PASS_OBSERVED_DECLARED_SOURCES_WITH_RECORDED_COVERAGE_LIMITS' if all(checks) else 'REVIEW_REQUIRED'
    for a in actors.values():
        a['status'] = status
        a['physical_actor_type'] = physical_type(a['blueprint'])
        a['snapshot_directory'] = str(cap.output / 'raw_snapshot' / rid)
        a['initial_state_not_backfilled_into_previous_runs'] = True
    return list(actors.values()), collisions_summary, collision_ledger, check, public_checks


def compare_retained(actors):
    groups = defaultdict(list)
    for a in actors:
        if a['role'] not in {'E', 'A', 'B|C'}:
            continue
        groups[(a['template'], a['logical_role'])].append(a)
    rows = []
    for (_, role), group in sorted(groups.items()):
        ref = group[0]
        for a in group:
            rp = ref['public_configured_pose'] or (ref.get('spawn_request') or {}).get('spawn_point')
            ap = a['public_configured_pose'] or (a.get('spawn_request') or {}).get('spawn_point')
            p, r = pose_delta(rp, ap)
            actual_p, actual_r = pose_delta(ref['spawn_return_transform'], a['spawn_return_transform'])
            rows.append(dict(run_id=a['run_id'], reference_run_id=ref['run_id'], logical_role=role,
                template=a['template'], condition=a['condition'], seed=a['seed'], actor_id=a['actor_id'],
                reference_actor_id=ref['actor_id'], blueprint_equal=a['blueprint']==ref['blueprint'],
                requested_pose_position_difference_m=p, requested_pose_rotation_difference_degrees=r,
                returned_pose_position_difference_m=actual_p, returned_pose_rotation_difference_degrees=actual_r,
                compare_only_observed_runs=True, match_by_logical_role_not_actor_id=True,
                comparison_is_not_native_determinism_or_task_success_claim=True))
    return rows


def main(manifest_path, output):
    output.mkdir(parents=True, exist_ok=False)
    cap = Capture(output)
    manifest = cap.get(manifest_path, Path('DEV_MANIFEST.json'))
    frozen = json.loads((OLD / 'formal/SOURCE_SHA256.json').read_text())
    code = {str(p): digest(p.read_bytes()) for p in [CORE, BA, ROUTE, PROVIDER]}
    source_hashes = {'sha256': code, 'all_match_previous_formal_freeze': all(h==frozen.get(p) for p,h in code.items())}
    assert source_hashes['all_match_previous_formal_freeze'], 'Core/native source drift'
    sites = core_call_sites(CORE.read_text())
    assert {s[3] for s in sites} == {'CORE_BLOCKER', 'CORE_WALKER_INITIAL', 'CORE_WALKER_REPLACEMENT'}
    all_actors, all_collisions, all_events, all_checks, all_public = [], [], [], [], []
    for run in manifest['runs']:
        actors, collision, events, check, public = analyze_run(run, cap, sites, source_hashes)
        all_actors.extend(actors); all_collisions.append(collision); all_events.extend(events)
        all_checks.append(check); all_public.extend(public)
    assert len({(a['run_id'],a['actor_id']) for a in all_actors}) == len(all_actors)
    write_csv(output / 'CURRENT_ACTOR_ROLE_MANIFEST.csv', all_actors)
    write_csv(output / 'CURRENT_COLLISION_SOURCE_SUMMARY.csv', all_collisions)
    write_csv(output / 'ALL_CURRENT_COLLISION_EVENTS.csv', all_events)
    write_csv(output / 'CURRENT_RUN_TRAFFIC_QA.csv', all_checks)
    write_csv(output / 'PUBLIC_RETAINED_POSE_QA.csv', all_public)
    write_csv(output / 'RETAINED_CROSS_RUN_COMPARISON.csv', compare_retained(all_actors))
    write_json(output / 'SOURCE_CODE_IDENTITY.json', source_hashes)
    write_json(output / 'CAPTURE_INDEX.json', {'fixed_cutoff_wall_time_ns':cap.cutoff_ns, 'files':cap.index})
    summary = {'schema':'DCTC_CURRENT_TRAFFIC_AUDIT_V1', 'phase':'DEV_CLEAR_TASK_TRAFFIC_DIAGNOSTIC',
        'fixed_cutoff_utc':datetime.fromtimestamp(cap.cutoff_ns/1e9,timezone.utc).isoformat(),
        'planned_run_count':len(manifest['runs']), 'statuses':dict(Counter(r['status'] for r in all_checks)),
        'complete_episode_source_evidence':dict(Counter(r['full_episode_evidence_status'] for r in all_checks)),
        'total_blocked_source_calls_observed':sum(r['blocked_source_call_count'] for r in all_checks),
        'total_blocked_source_slots_observed':sum(r['blocked_requested_source_slots'] for r in all_checks),
        'actual_CPD_background_success_actors_observed':sum(r['actual_unique_successful_CPD_background_actor_count'] for r in all_checks),
        'raw_callback_count_observed':sum(r['raw_callback_count'] for r in all_collisions),
        'original_accepted_events_observed':sum(r['original_accepted_count'] for r in all_collisions),
        'unknown_vehicles_by_run':{r['run_id']:r['unknown_vehicle_ids'] for r in all_checks if r['unknown_vehicle_ids']},
        'core_and_provider_source_match_previous_freeze':source_hashes['all_match_previous_formal_freeze'],
        'role_classification_uses_collision_outcome_type_or_id_alone':False,
        'historical_results_modified_or_backfilled':False, 'runtime_or_model_imports':False,
        'full_stage_complete':all(r['complete_episode'] for r in all_checks),
        'scientific_claim':'Source-gate and retained-object evidence only; no safety or task-completion claim',
        'script_path':str(Path(__file__).resolve()), 'script_sha256':digest(Path(__file__).read_bytes())}
    write_json(output / 'CURRENT_TRAFFIC_QA_SUMMARY.json', summary)
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    main(args.manifest,args.output)
