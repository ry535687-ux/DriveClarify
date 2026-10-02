"""标签盲策略：没有文件访问、评估器或答案服务。"""
from copy import deepcopy
import hashlib
from driveclarify_rq1_conditional_supplement import judges_revised
from driveclarify_rq1_grounded_relation_v3.contracts import validate_method_input

PUBLIC_KEYS = frozenset(('sample_id', 'candidates', 'observation', 'runtime_context',
                         'candidate_futures', 'ego_state', 'route_context', 'raw_instruction'))
FORBIDDEN = frozenset(('truth', 'truth_status', 'true_intent', 'intent', 'answer', 'label',
                       'correctness', 'correct_binding', 'expected_decision', 'annotation'))

def assert_public(value):
    if isinstance(value, dict):
        if FORBIDDEN.intersection(value):
            raise ValueError('EVALUATOR_FIELD_IN_PUBLIC_INPUT')
        for child in value.values():
            assert_public(child)
    elif isinstance(value, list):
        for child in value:
            assert_public(child)

def project_public(raw):
    public = {k: deepcopy(raw[k]) for k in PUBLIC_KEYS if k in raw}
    public['sample_id'] = 'PUBLIC_' + hashlib.sha256(raw['sample_id'].encode()).hexdigest()[:24]
    public['runtime_context'] = {'candidate_obligations': deepcopy(raw['runtime_context']['candidate_obligations'])}
    assert_public(public)
    validate_method_input(public)
    return public

def decide(policy, public, config):
    if set(public) - PUBLIC_KEYS:
        raise ValueError('NONPUBLIC_TOP_LEVEL_FIELD')
    assert_public(public)
    candidates = public.get('candidates')
    valid = (isinstance(candidates, list) and len(candidates) == 2
             and all(isinstance(c, dict) and isinstance(c.get('text'), str) and c['text'].strip()
                     and isinstance(c.get('candidate_id'), str) for c in candidates)
             and candidates[0]['candidate_id'] != candidates[1]['candidate_id'])
    if not valid:
        return {'action': 'ABSTAIN', 'selected_candidate': None, 'relation': None, 'reason': 'INVALID_CANDIDATES'}
    default = candidates[0]['candidate_id']
    if policy == 'NO_CLARIFICATION':
        return {'action': 'ACT', 'selected_candidate': default, 'relation': None, 'reason': 'FIXED_PUBLIC_DEFAULT'}
    if policy == 'IMMEDIATE_QUERY':
        return {'action': 'ASK', 'selected_candidate': None, 'relation': None, 'reason': 'TWO_VALID_CANDIDATES'}
    if policy != 'DRIVECLARIFY':
        raise ValueError('UNKNOWN_POLICY')
    result = judges_revised.evaluate_c_m5_revised(public, config)
    relation = result['relation']
    action = {'TASK_EQUIVALENT': 'ACT', 'TASK_DIVERGENT': 'ASK', 'UNKNOWN': 'ABSTAIN'}[relation]
    return {'action': action, 'selected_candidate': default if action == 'ACT' else None,
            'relation': relation, 'reason': result['reason_codes'], 'revised_result': result}
