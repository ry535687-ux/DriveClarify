"""仅新诊断进程显式启用的普通背景车辆源头开关。"""
import functools
import inspect

BACKGROUND_ENTRY_POINTS = ('_spawn_actor', '_spawn_actors', '_spawn_source_actor')


def install_background_gate(background_class, *, enabled, emit):
    """保留 BackgroundBehavior 的其余行为、场景黑板及 TrafficManager。

    只拦截已核验的三条普通随机 vehicle.* 创建入口；不拦截
    CarlaDataProvider 通用生成，不删车，也不改变任务场景创建。
    """
    originals = {}
    for name in BACKGROUND_ENTRY_POINTS:
        original = getattr(background_class, name)
        originals[name] = original
        if not enabled:
            continue

        def build(method, entry):
            @functools.wraps(method)
            def wrapped(self, *args, **kwargs):
                count = len(args[0]) if entry == '_spawn_actors' and args else (len(kwargs.get('spawn_wps', [])) if entry == '_spawn_actors' else 1)
                emit({'event': 'UNRELATED_BACKGROUND_GENERATION_BLOCKED',
                      'entry': entry, 'source_module': method.__module__,
                      'requested_source_slots': count,
                      'request_count_semantics': 'SOURCE_ATTEMPT_BEFORE_ORIGINAL_DISTANCE_FILTER_NOT_ACTUAL_SPAWN_COUNT',
                      'actual_spawned': 0, 'actor_destroy_calls': 0,
                      'caller_functions': [x.function for x in inspect.stack(context=0)[1:7]],
                      'global_traffic_manager_settings_modified': False})
                return [] if entry == '_spawn_actors' else None
            return wrapped
        setattr(background_class, name, build(original, name))
    return originals
