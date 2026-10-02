#!/usr/bin/env python3
"""Read-only clear-task DEV summary; native physical reducer is unchanged.

An output directory must be new. Ongoing episodes retain null final endpoints.
No CARLA, model, controller or evaluation-policy modules are imported/executed.
"""
import argparse
import ast
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
from driveclarify_ablation_overnight.task_outcome import evaluate_run_directory
from driveclarify_rq3_paired_v2.task_evaluator import (
    footprint_inside, STOP_MAX_SPEED_MPS, STOP_MIN_DURATION_S, MAX_SAMPLE_GAP_S,
)

VERSION = 'CLEAR_TASK_READ_ONLY_SUMMARY_V1_2'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceReader:
    def __init__(self):
        self.files = {}
        self.errors = []

    def read(self, path, lines=False):
        path = Path(path)
        key = str(path.resolve())
        if not path.is_file():
            self.files[key] = {'exists': False}
            return [] if lines else {}
        raw = path.read_bytes()
        self.files[key] = {'exists': True, 'sha256': sha(raw), 'bytes': len(raw),
                           'captured_at': datetime.now().astimezone().isoformat(),
                           'jsonl_reproduction': 'HASH_FIRST_RECORDED_BYTES_IF_APPEND_ONLY_LOG_CONTINUES' if lines else None}
        if lines:
            result = []
            for number, line in enumerate(raw.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('JSONL_ROW_NOT_OBJECT')
                    result.append(row)
                except (ValueError, TypeError) as exc:
                    self.errors.append({'path': key, 'line': number, 'error': str(exc)})
            return result
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as exc:
            self.errors.append({'path': key, 'error': str(exc)})
            return {}

    def text(self, path):
        path = Path(path)
        key = str(path.resolve())
        if not path.is_file():
            self.files[key] = {'exists': False}
            return ''
        raw = path.read_bytes()
        self.files[key] = {'exists': True, 'sha256': sha(raw), 'bytes': len(raw),
                           'captured_at': datetime.now().astimezone().isoformat()}
        return raw.decode('utf-8', errors='replace')


def event_time(row):
    return None if row is None else {'frame': row.get('frame'),
        'simulation_time_s': row.get('simulation_time_s')}


def consecutive_duration(rows, key):
    start, previous, longest = None, None, 0.0
    for row in rows:
        now = row['simulation_time_s']
        if not row[key]:
            start = None
        elif start is None or previous is None or now - previous > MAX_SAMPLE_GAP_S + 1e-9:
            start = now
        if start is not None:
            longest = max(longest, now - start)
        previous = now
    return longest


def region_decomposition(trace, region):
    """Describe observed geometry; no derived column replaces the frozen result."""
    if not trace:
        return {'available': False, 'reason': 'NO_PHYSICAL_SAMPLES'}
    c, s = math.cos(math.radians(region['yaw_degrees'])), math.sin(math.radians(region['yaw_degrees']))
    cx, cy, cz = region['center_xyz']
    result = []
    try:
        for row in trace:
            dx, dy = row['xyz'][0] - cx, row['xyz'][1] - cy
            along, across = c * dx + s * dy, -s * dx + c * dy
            vertices = row.get('bbox_world_vertices', [])
            if len(vertices) != 8:
                raise ValueError('MISSING_NATIVE_BODY_VERTICES')
            bbox = all(abs(c*(v[0]-cx)+s*(v[1]-cy)) <= region['half_length_m'] and
                       abs(-s*(v[0]-cx)+c*(v[1]-cy)) <= region['half_width_m'] for v in vertices)
            full = footprint_inside(row, region)
            result.append(dict(event_time(row),
                longitudinal_offset_m=along, lateral_offset_m=across, speed_mps=row['speed_mps'],
                longitudinal_span=abs(along) <= region['half_length_m'],
                center_rectangle_xy=abs(along) <= region['half_length_m'] and abs(across) <= region['half_width_m'],
                bbox_rectangle_xy=bbox, full_region_conditions=full,
                low_speed=row['speed_mps'] <= STOP_MAX_SPEED_MPS,
                qualified_stop=full and row['speed_mps'] <= STOP_MAX_SPEED_MPS,
                heading_condition=abs((row['yaw_degrees']-region['yaw_degrees']+180)%360-180)<=20,
                roll_pitch_condition=abs(row['roll_degrees'])<=20 and abs(row['pitch_degrees'])<=20,
                height_condition=abs(row['xyz'][2]-cz)<=2,
                lane_condition=row['lane_type']=='Parking' and row['road_id']==region['road_id'] and row['lane_id']==region['parking_lane_id']))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        return {'available': False, 'reason': 'GEOMETRY_INPUT_INVALID', 'error': str(exc)}
    keys = ['longitudinal_span', 'center_rectangle_xy', 'bbox_rectangle_xy', 'full_region_conditions',
            'low_speed', 'qualified_stop', 'heading_condition', 'roll_pitch_condition', 'height_condition', 'lane_condition']
    spans = [row for row in result if row['longitudinal_span']]
    full_rows = [row for row in result if row['full_region_conditions']]
    return {'available': True, 'region': region, 'observed_sample_count': len(result),
        'coordinate_domain': 'CARLA_WORLD_XYZ_METERS_PROJECTED_IN_AUTHORED_REGION_YAW',
        'condition_counts': {key: sum(row[key] for row in result) for key in keys},
        'first_condition_times': {key: event_time(next((row for row in result if row[key]), None)) for key in keys},
        'longitudinal_passed_from_before_to_after': min(row['longitudinal_offset_m'] for row in result)<-region['half_length_m'] and max(row['longitudinal_offset_m'] for row in result)>region['half_length_m'],
        'longitudinal_span_min_speed_mps': min((row['speed_mps'] for row in spans), default=None),
        'longitudinal_span_min_abs_lateral_m': min((abs(row['lateral_offset_m']) for row in spans), default=None),
        'full_region_min_speed_mps': min((row['speed_mps'] for row in full_rows), default=None),
        'maximum_observed_qualified_stop_duration_s': consecutive_duration(result, 'qualified_stop'),
        'observed_stop_duration_reaches_original_threshold': consecutive_duration(result, 'qualified_stop')+1e-9 >= STOP_MIN_DURATION_S,
        'time_coverage_must_be_validated_by_original_complete_episode_reducer': True}


def pid_configuration(simlingo):
    """Read literal GlobalConfig assignments; do not instantiate native modules."""
    path = Path(simlingo) / 'team_code/config_simlingo.py'
    raw = path.read_bytes()
    values = {}
    names = {'carla_fps', 'wp_dilation', 'data_save_freq', 'brake_speed', 'brake_ratio'}
    for node in ast.walk(ast.parse(raw)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self' and target.attr in names:
                    try:
                        values[target.attr] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    if not names.issubset(values):
        raise ValueError('NATIVE_PID_LITERAL_CONFIGURATION_INCOMPLETE')
    one = int(values['carla_fps'] // (values['wp_dilation'] * values['data_save_freq']))
    return {'values': values, 'indices': [one // 2 - 2, one - 2], 'source': str(path),
            'source_sha256': sha(raw), 'formula': 'norm(pred_speed_wps[i]-pred_speed_wps[j])*2.0',
            'evidence': 'READ_ONLY_NATIVE_SOURCE_DERIVATION_NOT_RUNTIME_PID_REEXECUTION',
            'source_agent': str(Path(simlingo)/'team_code/agent_simlingo.py'),
            'source_agent_sha256': sha((Path(simlingo)/'team_code/agent_simlingo.py').read_bytes())}


def speed_plan_diagnostic(control, trace, region, config):
    physical = {row['frame']: row for row in trace}
    values, near = [], []
    c, s = math.cos(math.radians(region['yaw_degrees'])), math.sin(math.radians(region['yaw_degrees']))
    for row in control:
        plan = row.get('native_plan')
        if not plan:
            continue
        try:
            wps = plan['pred_speed_wps'][0]
            i, j = config['indices']
            desired = math.sqrt(sum((a-b)**2 for a,b in zip(wps[i], wps[j]))) * 2.0
            if not math.isfinite(desired):
                raise ValueError('NONFINITE')
        except (TypeError, KeyError, IndexError, ValueError):
            continue
        entry = dict(event_time(row), source_formula_desired_speed_mps=desired,
                     returned_brake=row.get('brake'), returned_throttle=row.get('throttle'))
        values.append(entry)
        ego = physical.get(row.get('frame'))
        if ego:
            dx, dy = ego['xyz'][0]-region['center_xyz'][0], ego['xyz'][1]-region['center_xyz'][1]
            if abs(c*dx+s*dy) <= region['half_length_m']:
                near.append(entry)
    return {'formula_provenance': config, 'valid_plan_rows': len(values),
            'minimum_derived_desired_speed_mps': min((x['source_formula_desired_speed_mps'] for x in values), default=None),
            'target_longitudinal_span_plan_rows': len(near),
            'target_longitudinal_span_minimum_derived_desired_speed_mps': min((x['source_formula_desired_speed_mps'] for x in near), default=None),
            'target_longitudinal_span_first_plan': near[0] if near else None,
            'target_longitudinal_span_last_plan': near[-1] if near else None,
            'diagnostic_only_not_model_or_PID_fault_attribution': True}


def auxiliary_teardown_state(closed, evaluator_text, events, observer_summary):
    known = ('Failed to stop the agent' in evaluator_text and
             'driveclarify_clear_task_diagnostic/agent.py' in evaluator_text and
             '_diag_record' in evaluator_text and
             "AttributeError: 'NoneType' object has no attribute 'get_snapshot'" in evaluator_text)
    return {'auxiliary_teardown_logging_error': True if known else False if closed else None,
            'auxiliary_native_termination_event_missing': not any(x.get('event')=='DIAGNOSTIC_NATIVE_TERMINATION' for x in events) if closed else None,
            'auxiliary_observer_summary_missing': not bool(observer_summary) if closed else None,
            'auxiliary_error_affects_original_physical_endpoint_classification': False}


def chain_evidence(spec, config, events, language, control, complete, physical, ask_count, answer_count, trace=()):
    diag = config.get('diagnostic', {})
    clear, original = diag.get('clear_instruction'), diag.get('original_instruction')
    received = [x for x in events if x.get('event') == 'CLEAR_TASK_RECEIVED']
    bound = [x for x in events if x.get('event') == 'TASK_ROUTE_BINDING_PROPOSED']
    installed = [x for x in events if x.get('event') == 'CLEAR_TASK_ROUTE_INSTALLED']
    assessments = [x for x in events if x.get('event') == 'TASK_ROUTE_ASSESSMENT']
    errors = []
    anchor = diag.get('anchor_xyz')
    radius = diag.get('anchor_distance_m')
    anchored = []
    if isinstance(anchor, list) and isinstance(radius, (int,float)):
        anchored = [row for row in trace if math.sqrt(sum((a-b)**2 for a,b in zip(row['xyz'],anchor))) <= radius]
    checks = []
    expected_rows = []
    for row in language:
        expected = clear if row.get('task_epoch', 0) >= 1 else original
        actual = row.get('actual_model_prompt_inference')
        valid = bool(expected and isinstance(actual, list) and actual and all(expected in text for text in actual))
        fields = row.get('method_instruction') == expected and row.get('custom_prompt') == expected
        checks.append(valid and fields)
        if not valid or not fields:
            errors.append({'stage': 'TASK_LANGUAGE_MODEL_INPUT_MISMATCH', **event_time(row),
                           'method_fields_match': fields, 'actual_prompt_inference_match': valid})
        if row.get('task_epoch', 0) >= 1:
            expected_rows.append(row)
    wrong_binding = [x for x in bound if x.get('selected_candidate_id') != diag.get('supplied_candidate_id')]
    if wrong_binding:
        errors.append({'stage': 'EXPLICIT_TASK_CANDIDATE_BINDING_MISMATCH', **event_time(wrong_binding[0])})
    consumptions = [row for row in language if row.get('route_consumption_receipt')]
    consumption_checks = [bool(row['route_consumption_receipt'].get('installed_equals_next_tick_consumed') is True and
        row['route_consumption_receipt'].get('installed_route_identity') == row.get('active_route_identity')) for row in consumptions]
    if consumption_checks and not all(consumption_checks):
        bad = consumptions[consumption_checks.index(False)]
        errors.append({'stage': 'INSTALLED_ROUTE_NOT_CONSUMED_BY_NATIVE_MODEL_TICK', **event_time(bad)})
    for row in control:
        for key in ['model_calls_by_diagnostic', 'PID_calls_by_diagnostic', 'planner_steps_by_diagnostic', 'control_writes_by_diagnostic']:
            if row.get(key) not in (0, None):
                errors.append({'stage': 'DIAGNOSTIC_EXTRA_EXECUTION_REPORTED', 'field': key, **event_time(row)})
    if ask_count or answer_count:
        errors.append({'stage': 'UNEXPECTED_QUESTION_OR_ANSWER_EVENT', 'ask_count': ask_count, 'answer_count': answer_count})
    d1 = spec['condition'] == 'CLEAR_FROM_START'
    if not d1 and received:
        trigger_pose = next((row for row in trace if row['frame']==received[0]['frame']),None)
        if trigger_pose and (not anchored or anchored[0]['frame'] != received[0]['frame']):
            errors.append({'stage':'D2_TASK_RECEIPT_NOT_AT_FIRST_ORIGINAL_ANCHOR_CAPTURE',
                           'receipt':event_time(received[0]),
                           'first_physical_anchor':event_time(anchored[0]) if anchored else None})
    before_first = None if not bound else bound[0].get('native_model_forwards_before_binding') == 0
    if d1 and before_first is False:
        errors.append({'stage': 'D1_ROUTE_BINDING_AFTER_FIRST_NATIVE_MODEL', **event_time(bound[0])})
    if errors:
        earliest = errors[0]
    elif not received:
        earliest = {'stage': ('D2_ANCHOR_REACHED_TASK_UPDATE_MISSING' if anchored and complete and not d1 else
                             'D2_TRIGGER_NOT_REACHED' if complete and not d1 else 'TASK_UPDATE_NOT_OBSERVED_YET'),
                    'replan_tested': False}
    elif not bound:
        earliest = {'stage': 'RECEIVED_TASK_WITHOUT_ROUTE_PROPOSAL', 'replan_tested': False}
    elif not installed:
        earliest = {'stage': 'PUBLIC_ROUTE_TRANSITION_NOT_COMMITTED', 'last_assessment': assessments[-1].get('assessment') if assessments else None}
    elif not consumptions or not expected_rows:
        earliest = {'stage': 'NATIVE_LANGUAGE_OR_ROUTE_CONSUMPTION_EVIDENCE_INCOMPLETE'}
    elif not complete:
        earliest = {'stage': 'INPUT_BINDING_AND_CONSUMPTION_OBSERVED_ENDPOINT_PENDING'}
    elif physical.get('correct_task_complete') is not True:
        earliest = {'stage': 'POST_INPUT_PLAN_CONTROL_PHYSICAL_CHAIN_NOT_FURTHER_LOCALIZED',
                    'explanation': 'Explicit model input and native route consumption were observed; physical stop/continuation failed. This does not by itself attribute fault to model or PID.'}
    elif physical.get('correct_safe_task_complete') is not True:
        earliest = {'stage': 'TASK_COMPLETED_WITH_SAFETY_FAILURE'}
    else:
        earliest = {'stage': 'NO_OBSERVED_TASK_OR_SAFETY_FAILURE'}
    return {'first_task_received': event_time(received[0]) if received else None,
            'first_physical_anchor_capture': event_time(anchored[0]) if anchored else None,
            'anchor_capture_evidence': 'ORIGINAL_CONFIG_XYZ_3D_RADIUS_IN_INDEPENDENT_PHYSICAL_TRACE',
            'task_received_count': len(received), 'route_proposal_count': len(bound),
            'route_install_count': len(installed), 'route_assessment_count': len(assessments),
            'first_binding': event_time(bound[0]) if bound else None,
            'D1_binding_before_first_native_model': before_first if d1 else None,
            'language_observed_rows': len(language), 'post_update_language_rows': len(expected_rows),
            'all_observed_model_language_matches_current_epoch': all(checks) if checks else None,
            'route_consumption_observed_model_rows': len(consumptions),
            'all_observed_installed_routes_consumed': all(consumption_checks) if consumption_checks else None,
            'first_route_consumption': event_time(consumptions[0]) if consumptions else None,
            'task_update_replan_observed': bool(installed and consumptions and expected_rows and all(consumption_checks)),
            'last_assessment': assessments[-1].get('assessment') if assessments else None,
            'errors': errors, 'earliest': earliest,
            'fault_attribution_to_model_or_PID': None}


def summarize_run(spec, pid):
    reader = SourceReader()
    output = Path(spec['output']); owner = output/'owner_evidence'
    config = reader.read(spec['config_path']); binding = reader.read(spec['task_binding_path'])
    trace = reader.read(owner/'V2_NATIVE_STATE_TRACE.jsonl', True)
    terminal = reader.read(owner/'V2_TRACE_TERMINAL_RECEIPT.json')
    official = reader.read(output/'official_checkpoint.json')
    process = reader.read(output/'process_job/PROCESS_RECEIPT.json')
    events = reader.read(owner/'CLEAR_TASK_EVENTS.jsonl', True)
    control = reader.read(owner/'CLEAR_TASK_CONTROL_PLAN.jsonl', True)
    language = reader.read(owner/'CLEAR_TASK_MODEL_INPUT.jsonl', True)
    collisions = reader.read(output/'diagnostic_observer/COLLISION_SOURCE_TIMELINE.jsonl', True)
    policy = reader.read(output/'diagnostic_observer/RUNTIME_POLICY.json')
    gate = reader.read(output/'diagnostic_observer/BACKGROUND_GATE_TIMELINE.jsonl', True)
    reader.read(owner/'POST_SWITCH_PLAN_ACCEPTANCE.json')
    reader.read(owner/'V2_RUNTIME_IDENTITY.json')
    observer_summary = reader.read(output/'diagnostic_observer/OBSERVER_SUMMARY.jsonl', True)
    evaluator_text = reader.text(output/'process_job/evaluator.log')
    question_receipts, answers = [], []
    for path in sorted((owner/'oracle_exchange').glob('*.json')):
        item = reader.read(path)
        if item.get('action') == 'ASK' and item.get('durable') is True:
            question_receipts.append(item)
        if 'ANSWER' in path.name.upper():
            answers.append({'path': str(path), 'receipt': item})
    try:
        physical = evaluate_run_directory(output, spec['task_binding_path'], {'candidate_id': spec['evaluation_candidate_id']})
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        physical = {'task_metric_evaluable': False, 'reason_codes': ['SCORER_INPUT_ERROR:'+type(exc).__name__]}
    exposed = bool(trace or control or language)
    closed = bool(process)
    auxiliary = auxiliary_teardown_state(closed, evaluator_text, events, observer_summary)
    complete = bool(closed and terminal.get('destroy_observed') is True and physical.get('task_metric_evaluable') is True)
    started = exposed or bool(events) or (output/'process_job/evaluator.log').exists() or (output/'process_job/world_ready.json').exists()
    status = ('COMPLETED' if complete else 'TECHNICAL_INTERRUPTION' if closed and exposed else
              'PRE_AGENT_FAILED' if closed else 'RUNNING_PARTIAL' if started else 'PLANNED_NOT_STARTED')
    if not complete:
        physical['original_scorer_task_metric_evaluable_at_read'] = physical.get('task_metric_evaluable')
        physical['task_metric_evaluable'] = False
        for key in ['correct_task_complete','correct_safe_task_complete','correct_local_task_obligation','wrong_target_execution','native_route_complete']:
            physical[key] = None
        physical['final_endpoint_withheld_reason'] = status
    target_id = binding.get('candidate_region_map', {}).get(spec['evaluation_candidate_id'])
    region = next((x for x in binding.get('regions', []) if x['region_id']==target_id), None)
    geometry = region_decomposition(trace, region) if region else {'available': False, 'reason': 'TARGET_BINDING_MISSING'}
    plan = speed_plan_diagnostic(control, trace, region, pid) if region else {}
    chain = chain_evidence(spec, config, events, language, control, complete, physical, len(question_receipts), len(answers), trace)
    counts = physical.get('all_official_infraction_counts', {})
    native_count = max((x.get('native_model_forward_count', 0) for x in control+language), default=0)
    control_count = max((x.get('control_return_count', 0) for x in control), default=0)
    count_alignment = (len(language)==native_count and len(control)==control_count and
                       control_count==native_count+1 and len(trace) in (control_count,control_count+1)) if complete else None
    accepted = [x for x in collisions if x.get('accepted_original_event_count', 0)>0]
    first_collision = accepted[0] if accepted else None
    first_callback = collisions[0] if collisions else None
    first_native_event = (first_collision.get('accepted_original_events') or [None])[0] if first_collision else None
    first_native_state = next((x for x in trace if first_native_event and x.get('frame')==first_native_event.get('frame')),None)
    row = {key: spec.get(key) for key in ['schedule_position','run_id','pair_id','template','seed','condition','traffic_condition','runtime_revision']}
    row.update(status=status, started=started, exposed=exposed, complete_endpoint=complete,
        original_instruction=config.get('diagnostic',{}).get('original_instruction'), clear_instruction=config.get('diagnostic',{}).get('clear_instruction'),
        supplied_candidate_id=config.get('diagnostic',{}).get('supplied_candidate_id'), target_region_id=target_id,
        correct_language_task_complete=physical.get('correct_task_complete'), correct_safe_task_complete=physical.get('correct_safe_task_complete'),
        correct_local_stop=physical.get('correct_local_task_obligation'), wrong_target_execution=physical.get('wrong_target_execution'),
        native_route_complete=physical.get('native_route_complete'), native_route_completion_percent=physical.get('native_route_completion_percent') if complete else None,
        collision=(any(counts.get(k,0)>0 for k in ['collisions_vehicle','collisions_layout','collisions_pedestrian']) if complete and all(isinstance(counts.get(k),int) for k in ['collisions_vehicle','collisions_layout','collisions_pedestrian']) else None),
        offroad_or_wrong_lane=(counts.get('outside_route_lanes',0)>0 if complete and isinstance(counts.get('outside_route_lanes'),int) else None),
        timeout=(any(counts.get(k,0)>0 for k in ['route_timeout','scenario_timeouts']) if complete else None),
        nonprogress=(counts.get('vehicle_blocked',0)>0 if complete else None),
        driving_failure=(physical.get('correct_safe_task_complete') is False if complete else None),
        observed_question_request_count=sum(x.get('question_requested') is not None for x in events),
        observed_ask_count=len(question_receipts), observed_answer_count=len(answers),
        first_observed_collision_callback_frame=first_callback.get('collision_frame') if first_callback else None,
        first_accepted_collision_frame=first_collision.get('collision_frame') if first_collision else None,
        first_accepted_collision_sim_s=first_collision.get('collision_timestamp_s') if first_collision else None,
        first_accepted_collision_actor_id=(first_collision.get('other_actor') or {}).get('actor_id') if first_collision else None,
        first_accepted_collision_type=(first_collision.get('other_actor') or {}).get('blueprint_type_id') if first_collision else None,
        first_accepted_collision_clock_basis='CARLA_COLLISION_SENSOR_CALLBACK_TIMESTAMP' if first_collision else None,
        first_accepted_native_criterion_frame=first_native_event.get('frame') if first_native_event else None,
        first_accepted_native_criterion_sim_s=first_native_state.get('simulation_time_s') if first_native_state else None,
        observed_native_model_count=native_count, observed_control_count=control_count,
        terminal_model_control_trace_count_alignment=count_alignment,
        task_received=bool(chain['task_received_count']), task_update_replan_observed=chain['task_update_replan_observed'],
        earliest_chain_stage=chain['earliest']['stage'],
        target_center_rectangle_samples=geometry.get('condition_counts',{}).get('center_rectangle_xy'),
        target_full_bbox_condition_samples=geometry.get('condition_counts',{}).get('full_region_conditions'),
        maximum_observed_qualified_stop_s=geometry.get('maximum_observed_qualified_stop_duration_s'),
        longitudinal_passage_observed=geometry.get('longitudinal_passed_from_before_to_after'),
        runtime_wall_s=process.get('total_wall_s'),
        runtime_sim_s=(trace[-1]['simulation_time_s']-trace[0]['simulation_time_s']) if trace else None,
        cleanup_pass=process.get('cleanup_pass'), config_sha256_observed=reader.files[str(Path(spec['config_path']).resolve())].get('sha256'),
        config_sha256_matches_manifest=reader.files[str(Path(spec['config_path']).resolve())].get('sha256')==spec.get('config_sha256'))
    row.update(auxiliary)
    detail = {'run': row, 'physical_result': physical, 'target_decomposition': geometry, 'speed_plan_diagnostic': plan,
        'chain': chain, 'first_accepted_collision': first_collision,
        'first_observed_collision_callback': first_callback,
        'collision_callback_count': len(collisions), 'accepted_collision_event_count': sum(x.get('accepted_original_event_count',0) for x in collisions),
        'process_receipt': process, 'traffic_runtime_policy': policy,
        'auxiliary_teardown': auxiliary,
        'auxiliary_teardown_error_excerpt': evaluator_text[evaluator_text.rfind('Failed to stop the agent'):][-4000:] if auxiliary['auxiliary_teardown_logging_error'] else None,
        'background_generation_blocked_requests_observed': len(gate),
        'background_gate_actual_spawned_sum': sum(x.get('actual_spawned',0) for x in gate),
        'parse_errors': reader.errors,
        'partial_geometry_is_observed_prefix_not_complete_episode_failure': not complete,
        'old_V11_desired_speed_diagnostic_not_used': True,
        'ablation_judge_outputs_not_used': True}
    return row, detail, {'run_id': spec['run_id'], 'files': reader.files, 'parse_errors': reader.errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--simlingo', type=Path, default=Path('/home/buaa/wrh/simlingo'))
    parser.add_argument('--require-no-running', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    raw = args.manifest.read_bytes(); manifest = json.loads(raw)
    specs = manifest['runs']
    if len({x['run_id'] for x in specs}) != len(specs):
        raise ValueError('DUPLICATE_RUN_ID')
    pid = pid_configuration(args.simlingo)
    rows, details, indices = [], [], []
    for spec in specs:
        row, detail, index = summarize_run(spec, pid)
        rows.append(row); details.append(detail); indices.append(index)
    conditions = {}
    for name in sorted({x['condition'] for x in rows}):
        subset = [x for x in rows if x['condition']==name]
        complete = [x for x in subset if x['complete_endpoint']]
        conditions[name] = {'planned': len(subset), 'status_counts': dict(Counter(x['status'] for x in subset)),
            'complete_denominator': len(complete),
            'correct_language_task_complete_count': sum(x['correct_language_task_complete'] is True for x in complete),
            'correct_safe_task_complete_count': sum(x['correct_safe_task_complete'] is True for x in complete),
            'collision_count': sum(x['collision'] is True for x in complete),
            'incomplete_or_technical_primary_null': len(subset)-len(complete)}
    pairs = defaultdict(list)
    for row in rows:
        pairs[row['pair_id']].append(row)
    summary = {'schema': VERSION, 'snapshot_at': datetime.now().astimezone().isoformat(),
        'phase': 'DEV_CLEAR_TASK_TRAFFIC_DIAGNOSTIC', 'formal_or_FULL_TRAJ_comparison': False,
        'manifest_path': str(args.manifest.resolve()), 'manifest_sha256': sha(raw),
        'planned_runs': len(specs), 'status_counts': dict(Counter(x['status'] for x in rows)),
        'snapshot_is_terminal_matrix': all(x['status']!='RUNNING_PARTIAL' for x in rows) and all(x['status']!='PLANNED_NOT_STARTED' for x in rows),
        'complete_pairs': sum(len(v)==2 and all(x['complete_endpoint'] for x in v) for v in pairs.values()),
        'incomplete_pairs': [key for key,v in pairs.items() if len(v)!=2 or not all(x['complete_endpoint'] for x in v)],
        'conditions': conditions, 'results': rows,
        'all_planned_rows_retained': len(rows)==len(specs), 'driving_failures_retained_in_complete_denominator': True,
        'technical_running_unstarted_final_endpoints_are_null': True,
        'fault_attribution_to_model_or_PID': 'NOT_INFERRED_FROM_TASK_FAILURE',
        'scientific_statistics': 'DESCRIPTIVE_DEV_COUNTS_NO_BOOTSTRAP_OR_FORMAL_INFERENCE',
        'source_script_sha256': sha(Path(__file__).read_bytes()),
        'frozen_evaluator_sha256': sha((PROJECT/'driveclarify_ablation_overnight/task_outcome.py').read_bytes()),
        'frozen_physical_reducer_sha256': sha((PROJECT/'driveclarify_rq3_paired_v2/task_evaluator.py').read_bytes())}
    for name, value in [('SUMMARY.json',summary),('RAW_LOG_INDEX.json',indices),
                        ('EARLIEST_CHAIN_INCONSISTENCY.json',[{'run_id':x['run']['run_id'],**x['chain']} for x in details]),
                        ('PER_RUN_DETAILS.json',details)]:
        (args.output/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    with (args.output/'CLEAR_TASK_RESULTS.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else ['run_id'])
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps({key:summary[key] for key in ['planned_runs','status_counts','complete_pairs','snapshot_is_terminal_matrix']}))
    if args.require_no_running and any(row['status']=='RUNNING_PARTIAL' for row in rows):
        raise SystemExit('RUNNING_PARTIAL_PRESERVED_NOT_A_TERMINAL_SNAPSHOT')


if __name__ == '__main__':
    main()
