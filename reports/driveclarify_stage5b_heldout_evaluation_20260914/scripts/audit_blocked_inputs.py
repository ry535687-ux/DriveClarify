"""仅复核停止条件；不导入或执行 predictor，不生成预测或 gold。"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).resolve().parents[1]
PACKET_DIR = REPO / 'reports/driveclarify_stage5b_heldout_annotation_20260914'
STAGE5A = REPO / 'reports/driveclarify_stage5a_controlled_main_ablation_20260914'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assigned_literal(path, function, variable):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    for item in ast.walk(node):
        if isinstance(item, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == variable for t in item.targets):
            return ast.literal_eval(item.value)
    raise ValueError((function, variable))


def inspect_inputs():
    freeze = json.loads((PACKET_DIR / 'PACKET_FREEZE.json').read_text())
    pairs = {
        'ANNOTATION_PACKET_CANONICAL.json': 'canonical_sha256',
        'ANNOTATION_PACKET_A.json': 'packet_A_sha256',
        'ANNOTATION_PACKET_B.json': 'packet_B_sha256',
        'ANNOTATION_GUIDELINE.md': 'guideline_sha256',
        'HELDOUT_DUPLICATE_AUDIT.json': 'duplicate_audit_sha256',
    }
    hashes = {name: {'actual': sha(PACKET_DIR / name), 'frozen': freeze[key]}
              for name, key in pairs.items()}
    for name, item in hashes.items():
        if item['actual'] != item['frozen']:
            raise ValueError('STAGE5B_ARTIFACT_INTEGRITY_BLOCKED:' + name)
    packets = {key: json.loads((PACKET_DIR / name).read_text()) for key, name in (
        ('canonical', 'ANNOTATION_PACKET_CANONICAL.json'),
        ('A', 'ANNOTATION_PACKET_A.json'), ('B', 'ANNOTATION_PACKET_B.json'))}
    for version, cases in packets.items():
        ids = [c['case_id'] for c in cases]
        if len(ids) != 30 or len(set(ids)) != 30 or set(ids) != set(freeze['case_ids']):
            raise ValueError('STAGE5B_ARTIFACT_INTEGRITY_BLOCKED:IDS:' + version)
        for case in cases:
            digest = hashlib.sha256(json.dumps(case, ensure_ascii=False, sort_keys=True,
                                              separators=(',', ':')).encode()).hexdigest()
            if digest != freeze['per_case_content_sha256'][case['case_id']]:
                raise ValueError('STAGE5B_ARTIFACT_INTEGRITY_BLOCKED:CASE:' + version)
    manifest = json.loads((STAGE5A / 'BENCHMARK_MANIFEST.json').read_text())
    code = []
    for row in manifest['sources']:
        if not row['path'].endswith('.py'):
            continue
        actual = sha(REPO / row['path'])
        if actual != row['sha256']:
            raise ValueError('STAGE5B_ARTIFACT_INTEGRITY_BLOCKED:' + row['path'])
        code.append({**row, 'actual_sha256': actual, 'matches_stage5a_freeze': True})
    contracts = REPO / 'driveclarify_rq1_grounded_relation_v3/contracts.py'
    methods = REPO / 'driveclarify_rq1_grounded_relation_v3/methods.py'
    required = assigned_literal(contracts, 'validate_method_input', 'required')
    components = assigned_literal(methods, '_topology', 'allowed')
    source_kinds = assigned_literal(methods, '_topology', 'source_kinds')
    schema = defaultdict(Counter)

    def walk(value, path='$'):
        schema[path][type(value).__name__] += 1
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, path + '.' + key)

    for case in packets['canonical']:
        walk(case)
    missing = [{'case_id': case['case_id'], 'missing_public_contract_keys': sorted(required - set(case))}
               for case in packets['canonical']]
    duplicate = json.loads((PACKET_DIR / 'HELDOUT_DUPLICATE_AUDIT.json').read_text())
    return {
        'audit_kind': 'STATIC_SCHEMA_AND_HASH_ONLY_NO_PREDICTOR_INVOCATION',
        'status': 'STAGE5B_HELDOUT_INPUT_SCHEMA_BLOCKED',
        'packet_file_hashes': hashes,
        'case_count': len(packets['canonical']),
        'case_ids_and_all_three_packet_content_hashes_match': True,
        'A_B_order_differs': [c['case_id'] for c in packets['A']] != [c['case_id'] for c in packets['B']],
        'public_contract_required_keys': sorted(required),
        'task_projection_fields': list(components),
        'required_source_kinds': sorted(source_kinds),
        'schema_type_counts': {key: dict(value) for key, value in sorted(schema.items())},
        'per_case_contract_missing_fields': missing,
        'direct_contract_compatible_count': sum(not r['missing_public_contract_keys'] for r in missing),
        'thin_adapter_semantic_compatibility': 'BLOCKED_BY_UNRESOLVED_NATURAL_LANGUAGE_TASK_EVIDENCE',
        'thin_adapter_assessment_basis':
            '源码静态复核：冻结调用链消费已解析 candidate_obligations 并比较五字段；'
            '未包含把本 packet 的自然语言场景与候选义务解析为该结构的入口。'
            '仅重命名不能恢复对象位置、入口计数、分支身份和完成边界；此项是合同复核结论，不是预测结果。',
        'stage5a_frozen_code': code,
        'stage5a_manifest_sha256': sha(STAGE5A / 'BENCHMARK_MANIFEST.json'),
        'relation_config_unchanged': manifest['relation_config'],
        'predictor_function': 'driveclarify_rq1_conditional_supplement.judges_revised.evaluate_c_m5_revised',
        'duplicate_audit_summary': duplicate['summary'],
        'duplicate_audit_limitations': duplicate['limitations'],
        'private_provenance_read': False,
        'prediction_execution_count': 0,
        'evaluation_execution_count': 0,
    }


if __name__ == '__main__':
    result = inspect_inputs()
    path = REPORT / 'INPUT_SCHEMA_AUDIT.json'
    with path.open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'status': result['status'], 'direct_compatible': result['direct_contract_compatible_count'],
                      'prediction_execution_count': 0}, ensure_ascii=False))
