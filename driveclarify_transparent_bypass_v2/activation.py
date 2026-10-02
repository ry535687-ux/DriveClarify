"""纯上下文谓词；不读取路线成绩、运行时速度或控制结果。"""
import json
from pathlib import Path

from driveclarify_rq1_v2.consequence import TaskSignature

INTERFACE_VERSION = 'DRIVECLARIFY_NO_CONTEXT_TRANSPARENT_BYPASS_V2.0'
PREDICATE_VERSION = 'COMPLETE_EXISTING_CLARIFICATION_OPERANDS_V2.0'


def activation(context):
    """只验证既有操作数完整性，不改变任何激活路径的输入。"""
    if not isinstance(context, dict):
        return False, ['CONTEXT_ABSENT_OR_NOT_MAPPING']
    errors = []
    if not isinstance(context.get('instruction'), str) or not context['instruction'].strip():
        errors.append('NONEMPTY_PASSENGER_INSTRUCTION_ABSENT')
    candidates = context.get('alternatives')
    signatures = context.get('task_signatures')
    if not isinstance(candidates, list) or len(candidates) != 2:
        errors.append('TWO_CANDIDATE_REPRESENTATIONS_ABSENT')
        candidates = []
    if not isinstance(signatures, list) or len(signatures) != 2:
        errors.append('TWO_TASK_SIGNATURES_ABSENT')
        signatures = []
    candidate_ids = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            errors.append('CANDIDATE_NOT_MAPPING')
            continue
        candidate_ids.append(candidate.get('candidate_id'))
        if any(not isinstance(candidate.get(k), str) or not candidate[k] for k in
               ('candidate_id', 'description', 'evidence_id', 'route_source_field', 'connector_id')):
            errors.append('CANDIDATE_OPERAND_INCOMPLETE')
    signature_ids = []
    for signature in signatures:
        try:
            sig = TaskSignature.from_mapping(signature)
            signature_ids.append(sig.candidate_id)
            if sig.validation_errors():
                errors.append('TASK_SIGNATURE_INVALID')
        except (AttributeError, TypeError, ValueError):
            errors.append('TASK_SIGNATURE_NOT_PARSEABLE')
    if len(candidate_ids) != 2 or len(set(candidate_ids)) != 2 or signature_ids != candidate_ids:
        errors.append('CANDIDATE_SIGNATURE_BINDING_INCOMPLETE')
    background = context.get('background_traffic_policy')
    if not isinstance(background, dict) or background.get('random_background_vehicle_count') != 0 or background.get('traffic_manager_random_generation_enabled') is not False:
        errors.append('EXISTING_ACTIVE_BACKGROUND_OPERAND_ABSENT')
    route_source = context.get('route_source')
    try:
        routes = json.loads(Path(route_source).read_text())
        if any(not isinstance(routes.get(c['route_source_field']), list) or len(routes[c['route_source_field']]) < 2 for c in candidates):
            errors.append('CANDIDATE_ROUTE_REPRESENTATION_ABSENT')
    except (TypeError, KeyError, ValueError, OSError):
        errors.append('CANDIDATE_ROUTE_SOURCE_ABSENT')
    temporal = context.get('rq3_temporal_contract')
    if temporal is not None:
        if not isinstance(temporal, dict) or temporal.get('rule') != 'R-JOINT(B2)' or temporal.get('T_FIXED_s') != 3.0 or temporal.get('reserve_s') != 1.2 or temporal.get('clock') != 'CARLA_SIMULATION_TIME' or temporal.get('evidence_anchor_certified') is not True:
            errors.append('EXISTING_TEMPORAL_OPERAND_INVALID')
    return not errors, errors


def select_implementation(arm, context, native_class, active_class_loader):
    if arm not in ('A0', 'A1'):
        raise ValueError('ARM_MUST_BE_A0_OR_A1')
    active, reasons = activation(context)
    if arm == 'A1' and active:
        return active_class_loader(), True, reasons
    # 不实例化、不setup、不读取旧V11 task配置；两臂返回完全相同class对象。
    return native_class, False, reasons
