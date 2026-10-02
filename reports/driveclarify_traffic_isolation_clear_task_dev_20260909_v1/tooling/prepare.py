"""本轮固定8次开发清单；不运行CARLA，不根据新结果选样。"""
import copy
import datetime
import hashlib
import json
from pathlib import Path

from driveclarify_clear_task_diagnostic.configuration import make_config

ROOT = Path('/home/buaa/wrh/DriveClarify')
REPORT = ROOT / 'reports/driveclarify_traffic_isolation_clear_task_dev_20260909_v1'
OLD = ROOT / 'reports/driveclarify_ablation_overnight_20260909_v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(str(path))
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    manifest = json.loads((OLD / 'formal/FORMAL_QUEUE_MANIFEST.json').read_text())
    seeds = [1553736718, 1173054324]
    assert not set(seeds).intersection(x['seed'] for x in manifest['runs'])
    rows = []
    for seed_index, seed in enumerate(seeds, 1):
        for template in ('REF-C', 'REF-E'):
            source_row = next(x for x in manifest['runs']
                if x['condition'] == template and x['configuration_id'] == 'ABL_FULL')
            source_path = Path(source_row['config_path'])
            source = json.loads(source_path.read_text())
            conditions = ['CLEAR_FROM_START', 'CLEAR_AT_ANCHOR']
            if seed_index == 2:
                conditions.reverse()
            for condition in conditions:
                short = 'D1' if condition == 'CLEAR_FROM_START' else 'D2'
                run_id = 'DCTC-DEV-R1-%s-S%02d-%s' % (template, seed_index, short)
                config = make_config(source, run_id=run_id, seed=seed, condition=condition)
                path = REPORT / 'configs' / (run_id + '.json')
                write(path, config)
                rows.append({'schedule_position': len(rows) + 1, 'run_id': run_id,
                    'pair_id': 'DCTC-DEV-R1-%s-S%02d' % (template, seed_index),
                    'phase': 'DEV_CLEAR_TASK_TRAFFIC_DIAGNOSTIC',
                    'protocol_id': 'DCTC_DEV_R1', 'template': template,
                    'seed': seed, 'seed_source': 'PREVIOUS_DEVELOPMENT_SEED_REUSE_NOT_NEW_INDEPENDENT_FORMAL_SAMPLE',
                    'condition': condition, 'traffic_condition': 'TASK_ONLY_TRAFFIC',
                    'configuration_id': 'COMMON_EXECUTION_BACKEND_CLEAR_TASK',
                    'underlying_ablation_config_arm': 'ABL_FULL_NOT_A_COMPARISON_ARM',
                    'runtime_revision': 'CLEAR_TASK_DEV_R1',
                    'config_path': str(path), 'config_sha256': sha(path),
                    'source_config': str(source_path), 'source_config_sha256': sha(source_path),
                    'route_path': source_row['route_path'], 'route_sha256': source_row['route_sha256'],
                    'task_binding_path': source_row['task_binding_path'],
                    'task_binding_sha256': source_row['task_binding_sha256'],
                    'candidate_route_path': source_row['candidate_route_path'],
                    'candidate_route_sha256': source_row['candidate_route_sha256'],
                    'evaluation_candidate_id': 'B',
                    'checkpoint_sha256': source['checkpoint_sha256'],
                    'output': str(REPORT / 'development/native' / run_id),
                    'status': 'PLANNED_NOT_STARTED',
                    'agent_exposed_retry_allowed': False})
    for arm in ('ABL_FULL', 'ABL_TRAJ_ONLY'):
        source_row = next(x for x in manifest['runs']
            if x['condition'] == 'REF-C' and x['configuration_id'] == arm)
        config = make_config(json.loads(Path(source_row['config_path']).read_text()),
            run_id='DCTC-WIRING-ONLY-' + arm, seed=seeds[0], condition='CLEAR_FROM_START')
        config['diagnostic']['wiring_check_only_not_extra_live_run'] = True
        write(REPORT / 'configs/arm_integration' / (arm + '_TASK_ONLY_TRAFFIC.json'), config)
    state = json.loads((REPORT / 'STATE.json').read_text())
    write(REPORT / 'DEV_MANIFEST.json', {
        'schema': 'driveclarify.clear-task-traffic-dev.manifest.v1',
        'created_local': datetime.datetime.now().astimezone().isoformat(),
        'phase': 'DEV_CLEAR_TASK_TRAFFIC_DIAGNOSTIC', 'protocol_id': 'DCTC_DEV_R1',
        'planned_runs': 8, 'planned_template_seed_units': 4, 'runs': rows,
        'runner': str(REPORT / 'tooling/run_native_episode.sh'), 'rpc_port': 28100,
        'deadline_local': state['deadline_local'],
        'stop_dispatch_local': '2026-09-09T14:00:00+08:00',
        'minimum_free_disk_bytes': 5 * 1024 ** 3,
        'template_selection': 'All two templates from previous DEV, documented collisions and longitudinal passage without entering correct stop region; not selected for success.',
        'original_traffic_extra_runs': 0,
        'original_traffic_extra_runs_reason': 'Historical random-background actor spawn identities/initial states are not fully archived, so do not claim exact reconstruction or add arbitrary traffic controls.',
        'ablation_performance_comparison': False,
        'question_or_answer_broker': False,
        'correct_information_is_explicit_diagnostic_input': True,
        'driving_failures_retained': True, 'post_exposure_automatic_retries': 0,
    })


if __name__ == '__main__':
    main()
