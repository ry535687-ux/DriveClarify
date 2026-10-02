"""只构造明确任务诊断配置，不读取运行结果或修改原配置。"""
import copy
import hashlib
import json

PHASE = 'DEV_CLEAR_TASK_TRAFFIC_DIAGNOSTIC'
TRAFFIC = 'TASK_ONLY_TRAFFIC'
CONDITIONS = ('CLEAR_FROM_START', 'CLEAR_AT_ANCHOR')


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def make_config(source, *, run_id, seed, condition, supplied_candidate_id='B',
                traffic=TRAFFIC):
    if condition not in CONDITIONS:
        raise ValueError('UNKNOWN_CLEAR_TASK_CONDITION')
    if traffic not in (TRAFFIC, 'ORIGINAL_TRAFFIC'):
        raise ValueError('UNKNOWN_TRAFFIC_CONDITION')
    config = copy.deepcopy(source)
    method = config['method_input']
    selected = next(x for x in method['alternatives']
                    if x['candidate_id'] == supplied_candidate_id)
    original = method['instruction']
    clear = selected['description']
    config.update(run_id=run_id, paired_environment_seed=int(seed),
                  ablation_stage=PHASE)
    config['diagnostic'] = {
        'phase': PHASE, 'revision': 'CLEAR_TASK_DEV_R1',
        'condition': condition, 'traffic_condition': traffic,
        'traffic_gate_enabled': traffic == TRAFFIC,
        'supplied_candidate_id': supplied_candidate_id,
        'original_instruction': original, 'clear_instruction': clear,
        'information_source': 'USER_AUTHORIZED_CORRECT_EXPLICIT_TASK_DIAGNOSTIC',
        'is_nonoracle_clarification_evaluation': False,
        'anchor_xyz': copy.deepcopy(method['observation_anchor_xyz']),
        'anchor_distance_m': method['anchor_capture_distance_m'],
        'trigger_rule': ('SETUP_LANGUAGE_FIRST_NATIVE_MODEL_ROUTE_BINDING'
                         if condition == CONDITIONS[0] else
                         'FIRST_NATIVE_TICK_WITHIN_ORIGINAL_TEMPLATE_ANCHOR'),
        'actual_question_or_answer_expected': False,
        'runtime_actors_sha256': canonical_digest(method['runtime_actors']),
        'background_policy_before': copy.deepcopy(method['background_traffic_policy']),
        'source_ablation_arm': config['ablation']['configuration_id'],
    }
    if condition == CONDITIONS[0]:
        method['instruction'] = clear
    # Keep all alternatives, scene objects, safety, checkpoint, and evaluator.
    # The diagnostic gate has its own field; old count=0 was not enforcement.
    return config


def validate_config(config):
    diag = config['diagnostic']
    if diag['phase'] != PHASE or config['ablation_stage'] != PHASE:
        raise ValueError('NOT_EXPLICIT_DEVELOPMENT_DIAGNOSTIC')
    if diag['condition'] not in CONDITIONS:
        raise ValueError('UNKNOWN_CLEAR_TASK_CONDITION')
    if diag['traffic_condition'] not in (TRAFFIC, 'ORIGINAL_TRAFFIC'):
        raise ValueError('UNKNOWN_TRAFFIC_CONDITION')
    if diag['traffic_gate_enabled'] != (diag['traffic_condition'] == TRAFFIC):
        raise ValueError('TRAFFIC_GATE_CONDITION_MISMATCH')
    if canonical_digest(config['method_input']['runtime_actors']) != diag['runtime_actors_sha256']:
        raise ValueError('RETAINED_PUBLIC_ACTORS_CHANGED')
    if config['mode'] != 'DRIVECLARIFY':
        raise ValueError('EXISTING_TRANSITION_MANAGER_REQUIRED')
    if config['training_performed'] is not False:
        raise ValueError('TRAINING_NOT_ALLOWED')
    return diag
