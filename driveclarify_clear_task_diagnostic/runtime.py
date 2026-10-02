"""独立 evaluator entry 的显式诊断观测与交通开关安装。"""
import json
import os
from pathlib import Path
import time

from .configuration import validate_config
from .traffic import install_background_gate

OBSERVER = None
BACKGROUND_STREAM = None
HANDLES = None


def initialize():
    global OBSERVER, BACKGROUND_STREAM, HANDLES
    if OBSERVER is not None:
        raise RuntimeError('DIAGNOSTIC_RUNTIME_ALREADY_INSTALLED')
    config = json.loads(Path(os.environ['DRIVECLARIFY_V11_CONFIG']).read_text())
    diag = validate_config(config)
    output = Path(os.environ['DRIVECLARIFY_V11_OWNER_DIR']).parent / 'diagnostic_observer'
    output.mkdir(exist_ok=False)
    from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
    from srunner.scenariomanager.scenarioatomics.atomic_criteria import CollisionTest
    from srunner.scenarios.background_activity import BackgroundBehavior
    from leaderboard.scenarios.route_scenario import RouteScenario
    from .observer import ReadOnlyObserver, install_runtime_observers, install_parked_mesh_observer
    def clock_provider():
        world = CarlaDataProvider.get_world()
        snap = world.get_snapshot() if world is not None else None
        return {'frame': None if snap is None else snap.frame,
                'simulation_time_s': None if snap is None else snap.timestamp.elapsed_seconds,
                'clock_source': 'CARLA_WORLD_SNAPSHOT_READ_ONLY',
                'carla_data_provider_random_seed': CarlaDataProvider._random_seed,
                'wall_time_epoch': time.time()}
    OBSERVER = ReadOnlyObserver(output, config['run_id'], clock_provider=clock_provider)
    HANDLES = install_runtime_observers(OBSERVER, CarlaDataProvider, CollisionTest)
    install_parked_mesh_observer(OBSERVER, RouteScenario)
    BACKGROUND_STREAM = (output / 'BACKGROUND_GATE_TIMELINE.jsonl').open('x')

    def emit(row):
        world = CarlaDataProvider.get_world()
        snap = world.get_snapshot() if world is not None else None
        row.update(run_id=config['run_id'], wall_time_epoch=time.time(),
                   frame=None if snap is None else snap.frame,
                   simulation_time_s=None if snap is None else snap.timestamp.elapsed_seconds)
        BACKGROUND_STREAM.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        BACKGROUND_STREAM.flush()
    install_background_gate(BackgroundBehavior, enabled=diag['traffic_gate_enabled'], emit=emit)
    (output / 'RUNTIME_POLICY.json').write_text(json.dumps({
        'phase': diag['phase'], 'traffic_condition': diag['traffic_condition'],
        'background_gate_enabled': diag['traffic_gate_enabled'],
        'intercepted_background_entries': ['_spawn_actor', '_spawn_actors', '_spawn_source_actor'],
        'generic_spawn_filtering': False, 'destroy_filtering': False,
        'global_traffic_manager_changes': False,
        'environment': {k: os.environ.get(k) for k in [
            'DRIVECLARIFY_RANDOM_BACKGROUND_VEHICLE_COUNT', 'RANDOM_BACKGROUND_VEHICLE_COUNT',
            'DRIVECLARIFY_TRAFFIC_MANAGER_RANDOM_GENERATION', 'DRIVECLARIFY_ABLATION_SEED']},
        'old_zero_configuration_is_not_runtime_enforcement': True,
    }, sort_keys=True, indent=2) + '\n')
