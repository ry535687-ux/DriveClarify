"""首次执行冻结；已存在时只核验，不覆盖协议身份。"""
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from common import *

def run():
    target = REPORT / 'BENCHMARK_MANIFEST.json'
    if target.exists():
        verify_sources()
        print('EXISTING_FREEZE_VERIFIED')
        return
    sources = []
    def add(path, role):
        p = Path(path)
        assert p.is_file(), str(p)
        sources.append({'path': str(p.relative_to(REPO)), 'sha256': sha(p), 'bytes': p.stat().st_size, 'role': role})
    for split in ('DEV', 'HIST'):
        add(CROOT / f'method_inputs/{split}_METHOD_INPUTS.jsonl', 'PRIMARY_PUBLIC_SOURCE')
        add(CROOT / f'label_authority/{split}_LABELS.jsonl', 'EVALUATOR_ONLY_LABELS')
    for name in ('INDEPENDENCE_AUDIT.md', 'LABEL_PROVENANCE_AND_REVIEW.md', 'manifests/INFERENCE_CONFIG_DIGEST.json', 'manifests/SPLIT_MANIFEST.json'):
        add(CROOT / name, 'PROVENANCE')
    for pkg, names in {'driveclarify_rq1_grounded_relation_v3': ('__init__.py', 'methods.py', 'trajectory.py', 'contracts.py'),
                       'driveclarify_rq1_conditional_supplement': ('__init__.py', 'judges_revised.py', 'judges.py')}.items():
        for name in names:
            add(REPO / pkg / name, 'FROZEN_CPU_RELATION_IMPLEMENTATION')
    for name in ('FORMAL_V3_EPISODE_LEVEL_PRIMARY_TABLE.json', 'FORMAL_V3_EXECUTION_LEDGER.json', 'FORMAL_V3_PROTOCOL_FREEZE_RECEIPT.json'):
        add(ORIGINAL / name, 'ORIGINAL_SHARED_TRAJECTORY_ARCHIVE')
    for name in ('RQ2_EXTENSION_RESULTS.json', 'S04_ENDPOINT_EVALUABILITY_RECEIPT.json'):
        add(EXTENSION / name, 'EXTENSION_SHARED_TRAJECTORY_ARCHIVE')
    add(REPO / 'reports/driveclarify_rq1_conditional_supplement_20260911/sample_results.csv', 'STEP1_VERIFIED_TRAJECTORY_POINTS')
    for relative in (
        'driveclarify_experiment_restructure_step1_20260912/results/RECOMPUTED_RQ1.json',
        'driveclarify_rq3_v3_technical_validity_recovery_and_resume_v1/PART_B_FINAL_RESULTS.json',
        'driveclarify_rq3_v3_technical_validity_recovery_and_resume_v1/PART_B_FINAL_LIFECYCLE_ANALYSIS.json',
        'driveclarify_stage3c_b_native_runtime_20260914/FINAL_REPORT.md',
        'driveclarify_stage3c_native_clear_runtime_20260914/FINAL_REPORT.md',
        'driveclarify_stage3d_a_stall_watchdog_20260914/FINAL_REPORT.md'):
        add(REPO / 'reports' / relative, 'HISTORICAL_CONTEXT_NOT_PRIMARY')
    for name in ('policies.py', 'predict.py', 'common.py'):
        add(REPORT / 'scripts' / name, 'NEW_POLICY_IMPLEMENTATION_FROZEN_BEFORE_OUTPUT')
    write_json(target, {
        'protocol_id': 'DC_CONTROLLED_SELECTION_V1_20260914', 'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
        'protocol_sha256': sha(REPORT / 'BENCHMARK_PROTOCOL.md'), 'historical_data_previously_exposed': True,
        'new_predictions_generated_at_freeze': False, 'primary_sources': ['C_DEV', 'C_HIST'],
        'public_default': 'K1', 'counterfactual_intents': ['K1', 'K2'], 'variant_unit_weight': '1 / variants_in_base',
        'undefined_label_response_diagnostic': 'OUTPUT_ONLY_RESPONSE_A; no correctness scoring',
        'cluster': 'layout_id within split', 'bootstrap_repetitions': 10000, 'bootstrap_computational_seed': 20260914,
        'relation_config': read_json(CROOT / 'manifests/INFERENCE_CONFIG_DIGEST.json')['frozen_config'],
        'B_trajectory_threshold_m': 0.27119792945561905, 'protocol_reserve_s': 1.2,
        'policy_function': 'driveclarify_rq1_conditional_supplement.judges_revised.evaluate_c_m5_revised',
        'sources': sources})
    print({'frozen_sources': len(sources), 'manifest_sha256': sha(target)})

if __name__ == '__main__':
    run()
