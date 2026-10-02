"""Explicit, process-local CARLA provenance observation for clear-task DEV.

No CARLA/agent/model imports. Installing wraps only the supplied class objects;
importing this module changes nothing. Original calls, arguments, exceptions and
return objects are preserved. Their own spawning/ticking remains their behavior.
This observer does not forbid traffic, classify roles, create sensors, or control.
"""
import functools
from collections import Counter
import hashlib
import inspect
import json
import math
from pathlib import Path
import sys
import threading
import time
import traceback


SPAWN_APIS = ('request_new_actor', 'request_new_actors',
              'request_new_batch_actors', 'handle_actor_batch')
UNCOVERED_ENTRIES = (
    'Direct world.spawn_actor/try_spawn_actor outside wrapped provider calls',
    'Direct client batches outside handle_actor_batch or a separately installed parked-ID observer',
    'Generation before explicit installation or through previously bound aliases',
    'Other CarlaDataProvider class objects/modules not explicitly supplied',
    'Native/internal CARLA spawning not returned through the wrapped APIs',
    'CollisionTest subclasses overriding the wrapped callback, other collision criteria',
)


def _type_name(value):
    cls = type(value)
    return cls.__module__ + '.' + cls.__name__


def _vector(value, names=('x', 'y', 'z')):
    return {name: _plain(getattr(value, name, None)) for name in names}


def _transform(value):
    return {'location_m': _vector(value.location),
            'rotation_degrees': _vector(value.rotation, ('pitch', 'yaw', 'roll'))}


def _plain(value, depth=0):
    """Snapshot recognized data only; never consume generators or invoke repr/tolist."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {'value': None, 'reason': 'NONFINITE'}
    if depth > 12:
        return {'value': None, 'reason': 'MAX_SERIALIZATION_DEPTH', 'type': _type_name(value)}
    if isinstance(value, dict):
        return {str(k) if isinstance(k, (str, bool, int, float)) else _type_name(k):
                _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in value]
    name = type(value).__name__
    if name in ('Transform', 'FakeTransform'):
        return _transform(value)
    if name in ('Location', 'Vector3D', 'Vector2D', 'FakeVector'):
        return _vector(value)
    if name in ('Rotation', 'FakeRotation'):
        return _vector(value, ('pitch', 'yaw', 'roll'))
    # ActorConfigurationData has ordinary data fields. Unknown extension types are
    # intentionally opaque; no iterator or arbitrary serialization API is called.
    fields = getattr(value, '__dict__', None)
    if isinstance(fields, dict) and ('model' in fields or 'rolename' in fields):
        keys = ('model', 'transform', 'rolename', 'autopilot', 'random_location',
                'color', 'category', 'args', 'speed', 'name')
        return {'type': _type_name(value), 'configuration_fields': {
            key: _plain(fields[key], depth + 1) for key in keys if key in fields}}
    return {'value': None, 'reason': 'OPAQUE_TYPE_NOT_READ', 'type': _type_name(value)}


class ReadOnlyObserver:
    """Explicit output, provenance, actor inventory and existing-callback observer.

    clock_provider, when supplied, must itself be a read-only function returning
    a dict with its time-source/clock-domain labels. Without it, spawn records have
    wall time and simulation time UNKNOWN. observe_world uses caller-supplied
    frame/time and never fetches a world snapshot or advances the world.
    """
    def __init__(self, output_directory, run_id, full_actor_interval_frames=20,
                 clock_provider=None):
        if not run_id or int(full_actor_interval_frames) < 1:
            raise ValueError('Explicit run_id and positive actor snapshot interval required')
        self.output_directory = Path(output_directory).resolve()
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id)
        self.full_actor_interval_frames = int(full_actor_interval_frames)
        self.clock_provider = clock_provider
        self.errors = []
        self._lock = threading.RLock()
        self._local = threading.local()
        self._request_count = 0
        self._request_counts_by_api = {}
        self._actor_sources = {}
        self._actor_source_apis = {}
        self._actor_ids = set()
        self._inventory_initialized = False
        self._last_full_frame = None
        self._first_collision = set()
        self._installations = []
        self._world_observations = 0
        self._collision_callbacks = 0
        self._accepted_collision_events = 0
        self._actor_state_field_read_failures = 0

    def _error(self, stage, exc):
        item = {'run_id': self.run_id, 'wall_time_ns': time.time_ns(),
                'stage': stage, 'error_type': type(exc).__name__, 'message': str(exc),
                'observation_complete': False, 'control_changed_by_observer': False}
        with self._lock:
            self.errors.append(item)
            try:
                with (self.output_directory / 'OBSERVER_ERRORS.jsonl').open('a') as stream:
                    stream.write(json.dumps(item, ensure_ascii=False) + '\n')
            except Exception:
                try:
                    sys.stderr.write('CLEAR_TASK_OBSERVER_ERROR ' + json.dumps(item) + '\n')
                    sys.stderr.flush()
                except Exception:
                    pass  # errors remains available to explicit write_summary/agent inspection

    def guarded(self, stage, function, default=None):
        try:
            return function()
        except Exception as exc:
            self._error(stage, exc)
            return default

    def _clock(self):
        result = {'wall_time_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns(),
                  'simulation_clock': {'value': None, 'reason': 'NO_EXPLICIT_CLOCK_PROVIDER'}}
        if self.clock_provider is not None:
            supplied = self.guarded('clock_provider', self.clock_provider)
            result['simulation_clock'] = _plain(supplied)
        return result

    def emit(self, filename, event, **fields):
        def write():
            record = {'schema': 'CLEAR_TASK_READ_ONLY_OBSERVER_V1', 'run_id': self.run_id,
                      'event': event, **self._clock(), **fields}
            line = json.dumps(_plain(record), ensure_ascii=False, allow_nan=False) + '\n'
            with self._lock:
                with (self.output_directory / filename).open('a') as stream:
                    stream.write(line)
            return record
        return self.guarded('emit:' + filename, write)

    def actor_state(self, actor, include_state=True):
        """Read getters only. role_name is evidence, never an experimental role label."""
        errors = []
        def read(name, fn):
            try:
                return fn()
            except Exception as exc:
                errors.append({'field': name, 'error_type': type(exc).__name__, 'message': str(exc)})
                return None
        actor_id = read('actor_id', lambda: actor.id)
        result = {'actor_id': actor_id, 'blueprint_type_id': read('type_id', lambda: actor.type_id),
                  'attributes': read('attributes', lambda: _plain(dict(actor.attributes))),
                  'experiment_role': 'UNKNOWN_REQUIRES_SOURCE_AND_SCENARIO_EVIDENCE',
                  'retention_action': 'OBSERVED_ONLY_NO_ACTOR_CHANGE'}
        with self._lock:
            result['source_request_ids'] = list(self._actor_sources.get(actor_id, []))
            result['source_api_methods'] = list(self._actor_source_apis.get(actor_id, []))
        result['generation_source_status'] = ('CAPTURED_SOURCE_REQUEST_ID' if
            result['source_request_ids'] else 'UNKNOWN_NOT_CAPTURED')
        if include_state:
            result['transform'] = read('transform', lambda: _transform(actor.get_transform()))
            result['velocity_m_s'] = read('velocity', lambda: _vector(actor.get_velocity()))
            result['angular_velocity_degrees_s'] = read('angular_velocity', lambda: _vector(actor.get_angular_velocity()))
            result['acceleration_m_s2'] = read('acceleration', lambda: _vector(actor.get_acceleration()))
        result['read_errors'] = errors
        result['state_complete'] = not errors
        if errors:
            with self._lock:
                self._actor_state_field_read_failures += len(errors)
        return result

    def _spawn_begin(self, api, original, args, kwargs):
        with self._lock:
            self._request_count += 1
            request_id = '%s:spawn:%s' % (self.run_id, self._request_count)
            self._request_counts_by_api[api] = self._request_counts_by_api.get(api, 0) + 1
        stack = getattr(self._local, 'spawn_stack', [])
        self._local.spawn_stack = stack
        parent_id = stack[-1] if stack else None
        stack.append(request_id)
        def request_fields():
            bound = inspect.signature(original).bind(*args, **kwargs)
            bound.apply_defaults()
            return _plain(dict(bound.arguments))
        request = self.guarded('spawn_request_fields:' + api, request_fields,
                               {'value': None, 'reason': 'REQUEST_SERIALIZATION_FAILED'})
        callstack = [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                     for frame in traceback.extract_stack(limit=24)[:-2]]
        self.emit('ACTOR_SPAWN_TIMELINE.jsonl', 'SPAWN_API_REQUEST', request_id=request_id,
                  parent_request_id=parent_id, api=api, request=request, caller_stack=callstack,
                  original_call_count_by_wrapper=1, argument_mutations=0,
                  note='Nested APIs are linked; do not sum request counts as actor spawns.')
        return request_id

    def _spawn_end(self, request_id, api, result=None, exception=None):
        # Only known actor/list returns are inspected. An unknown iterable is never consumed.
        returned = result if isinstance(result, (list, tuple)) else ([result] if result is not None else [])
        actor_rows = []
        for actor in returned:
            if actor is None:
                actor_rows.append(None)
                continue
            actor_id = self.guarded('spawn_return_actor_id:' + api, lambda: actor.id)
            if actor_id is not None and request_id is not None:
                with self._lock:
                    self._actor_sources.setdefault(actor_id, []).append(request_id)
                    self._actor_source_apis.setdefault(actor_id, []).append(api)
            actor_rows.append(self.guarded('spawn_return_actor_state:' + api,
                                          lambda actor=actor: self.actor_state(actor)))
        self.emit('ACTOR_SPAWN_TIMELINE.jsonl',
                  'SPAWN_API_EXCEPTION' if exception is not None else 'SPAWN_API_RETURN',
                  request_id=request_id, api=api, returned_actors=actor_rows,
                  return_type=_type_name(result), returned_none=result is None,
                  exception_type=type(exception).__name__ if exception is not None else None,
                  exception_message=str(exception) if exception is not None else None,
                  original_return_identity_preserved=True,
                  extra_spawn_destroy_tick_or_rng_calls=0)

    def observe_world(self, world, *, frame, simulation_time_s=None, ego_actor=None, metadata=None):
        """One get_actors call; inventory changes each tick, full state every N frames.

        An unsuccessful inventory read leaves the previous inventory intact and
        records UNKNOWN, never a fabricated empty/safe world. Calling more than
        once at the same frame is permitted but does not advance any simulation.
        """
        def observe():
            actors = list(world.get_actors())
            actor_map = {actor.id: actor for actor in actors}
            current = set(actor_map)
            with self._lock:
                added = sorted(current - self._actor_ids)
                removed = sorted(self._actor_ids - current)
                full = (not self._inventory_initialized or self._last_full_frame is None or
                        frame < self._last_full_frame or
                        frame - self._last_full_frame >= self.full_actor_interval_frames)
                snapshot_ids = sorted(current) if full else added
                states = [self.actor_state(actor_map[actor_id]) for actor_id in snapshot_ids]
                initial = not self._inventory_initialized
                self._actor_ids = current
                self._inventory_initialized = True
                if full:
                    self._last_full_frame = frame
                self._world_observations += 1
            return self.emit('WORLD_ACTOR_TIMELINE.jsonl', 'WORLD_ACTOR_OBSERVATION',
                source_frame=frame, source_simulation_time_s=simulation_time_s,
                clock_basis='CALLER_SUPPLIED_WORLD_FRAME_AND_SIMULATION_SECONDS',
                inventory_complete=True, initial_observation=initial,
                added_actor_ids=added, removed_actor_ids=removed, all_actor_ids=sorted(current),
                full_state_snapshot=full, actor_states=states,
                ego_actor_id=ego_actor.id if ego_actor is not None else None,
                metadata=_plain(metadata), observer_error_count=len(self.errors),
                interpretation='First seen is not proof of creation at this frame; UNKNOWN actors retained.')
        result = self.guarded('observe_world', observe)
        if result is None:
            return self.emit('WORLD_ACTOR_TIMELINE.jsonl', 'WORLD_ACTOR_OBSERVATION_FAILED',
                source_frame=frame, source_simulation_time_s=simulation_time_s,
                inventory_complete=False, all_actor_ids=None,
                reason='UNKNOWN_READ_OR_LOG_FAILURE_NOT_EMPTY_WORLD', observer_error_count=len(self.errors))
        return result

    def record_tick_metadata(self, *, frame, simulation_time_s=None, metadata=None):
        """Copy already computed metadata; caller must not supply lazy computations."""
        return self.emit('OBSERVER_TICK_METADATA.jsonl', 'CACHED_TICK_METADATA',
                         source_frame=frame, source_simulation_time_s=simulation_time_s,
                         metadata=_plain(metadata), additional_model_pid_planner_control_calls=0)

    def _collision_before(self, criterion, event):
        ego_id = getattr(getattr(criterion, 'actor', None), 'id', None)
        other = getattr(event, 'other_actor', None)
        other_state = self.actor_state(other) if other is not None else None
        key = ('callback', ego_id)
        with self._lock:
            first = key not in self._first_collision
            self._first_collision.add(key)
            self._collision_callbacks += 1
        return {'criterion_process_identity': id(criterion),
                'criterion_name': getattr(criterion, 'name', type(criterion).__name__),
                'ego_actor_id': ego_id, 'other_actor': other_state,
                'collision_frame': getattr(event, 'frame', None),
                'collision_timestamp_s': getattr(event, 'timestamp', None),
                'normal_impulse': _plain(getattr(event, 'normal_impulse', None)),
                'first_observed_callback_for_ego': first,
                'original_event_count_before': len(criterion.events),
                'original_actual_value_before': getattr(criterion, 'actual_value', None)}

    def _collision_after(self, criterion, before, exception=None):
        before = before or {}
        start = before.get('original_event_count_before')
        events = list(criterion.events[start:]) if start is not None else []
        accepted = []
        for event in events:
            data = self.guarded('accepted_collision_event_data', event.get_dict, {})
            other = data.get('other_actor') if isinstance(data, dict) else None
            accepted.append({'event_type': self.guarded('accepted_collision_event_type', lambda: str(event.get_type())),
                             'frame': self.guarded('accepted_collision_event_frame', event.get_frame),
                             'message': self.guarded('accepted_collision_event_message', event.get_message),
                             'other_actor': self.actor_state(other) if other is not None else None,
                             'location': _plain(data.get('location')) if isinstance(data, dict) else None})
        key = ('accepted', before.get('ego_actor_id'))
        with self._lock:
            first_accepted = bool(accepted) and key not in self._first_collision
            if accepted:
                self._first_collision.add(key)
            self._accepted_collision_events += len(accepted)
        return self.emit('COLLISION_SOURCE_TIMELINE.jsonl', 'ORIGINAL_COLLISION_CALLBACK_RESULT',
            **before, original_event_count_after=len(criterion.events),
            original_actual_value_after=getattr(criterion, 'actual_value', None),
            accepted_original_events=accepted, accepted_original_event_count=len(accepted),
            first_accepted_original_event_for_ego=first_accepted,
            original_exception_type=type(exception).__name__ if exception is not None else None,
            original_exception_message=str(exception) if exception is not None else None,
            source_classification='EXACT_ACTOR_ID_JOIN_ONLY_ROLE_UNKNOWN_UNLESS_SEPARATELY_EVIDENCED',
            extra_collision_sensors=0, original_event_mutations=0)

    def write_summary(self):
        def write():
            with self._lock:
                summary = {'run_id': self.run_id, 'schema': 'CLEAR_TASK_OBSERVER_SUMMARY_V1',
                    'spawn_api_call_counts': dict(self._request_counts_by_api),
                    'distinct_actor_ids_returned_by_wrapped_apis': sorted(actor_id for actor_id, apis in
                        self._actor_source_apis.items() if any(api in SPAWN_APIS for api in apis)),
                    'distinct_actor_ids_from_parked_id_delta': sorted(actor_id for actor_id, apis in
                        self._actor_source_apis.items() if 'RouteScenario.spawn_parked_vehicles' in apis),
                    'world_observations': self._world_observations,
                    'collision_callbacks': self._collision_callbacks,
                    'accepted_original_collision_events': self._accepted_collision_events,
                    'actor_state_field_read_failure_count': self._actor_state_field_read_failures,
                    'error_count': len(self.errors), 'observation_errors': list(self.errors),
                    'installations': list(self._installations), 'uncovered_entry_points': list(UNCOVERED_ENTRIES),
                    'role_classification': 'NOT_PERFORMED_BY_OBSERVER',
                    'additional_model_pid_planner_worldtick_spawn_destroy_control_calls': 0}
                # Repeated summaries append; no historical summary is overwritten.
                return self.emit('OBSERVER_SUMMARY.jsonl', 'OBSERVER_SUMMARY', summary=summary)
        return self.guarded('write_summary', write)


class ObserverInstallation:
    """Restore only descriptors still owned by this explicit installation."""
    def __init__(self, observer):
        self.observer = observer
        self.patches = []

    def uninstall(self):
        for owner, name, original, installed in reversed(self.patches):
            if inspect.getattr_static(owner, name) is installed:
                setattr(owner, name, original)
                self.observer.emit('OBSERVER_INSTALLATION.jsonl', 'PATCH_UNINSTALLED', api=name)
            else:
                self.observer.emit('OBSERVER_INSTALLATION.jsonl', 'PATCH_NOT_RESTORED_OTHER_OWNER', api=name)
        self.patches = []


def _source_identity(function):
    path = inspect.getsourcefile(function)
    result = {'module': function.__module__, 'qualname': function.__qualname__, 'source_file': path}
    if path and Path(path).is_file():
        result['source_file_sha256_at_install'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return result


def install_runtime_observers(observer, data_provider_class, collision_test_class=None):
    """Explicitly patch supplied classes in this process, before native setup.

    Native provider methods may themselves spawn, consume RNG, tick or wait;
    wrappers delegate exactly once and add none of those operations. Existing
    pre-bound references and direct CARLA APIs remain explicitly uncovered.
    """
    handle = ObserverInstallation(observer)
    covered, missing = [], []
    for api in SPAWN_APIS:
        try:
            descriptor = inspect.getattr_static(data_provider_class, api)
        except AttributeError:
            missing.append(api)
            continue
        if isinstance(descriptor, (staticmethod, classmethod)):
            original = descriptor.__func__
            descriptor_type = type(descriptor)
        elif inspect.isfunction(descriptor):
            original, descriptor_type = descriptor, None
        else:
            missing.append(api + ':UNSUPPORTED_DESCRIPTOR')
            continue
        if getattr(original, '_clear_task_read_only_observer', None) is not None:
            raise RuntimeError('Observer already installed for ' + api)

        def make_spawn_wrapper(original, api):
            @functools.wraps(original)
            def wrapper(*args, **kwargs):
                request_id = observer.guarded('spawn_begin:' + api,
                    lambda: observer._spawn_begin(api, original, args, kwargs))
                try:
                    result = original(*args, **kwargs)
                except BaseException as exc:
                    observer.guarded('spawn_exception:' + api,
                                     lambda: observer._spawn_end(request_id, api, exception=exc))
                    raise
                else:
                    observer.guarded('spawn_end:' + api,
                                     lambda: observer._spawn_end(request_id, api, result=result))
                    return result
                finally:
                    stack = getattr(observer._local, 'spawn_stack', [])
                    if request_id in stack:
                        stack.remove(request_id)
            wrapper._clear_task_read_only_observer = observer
            return wrapper
        wrapped = make_spawn_wrapper(original, api)
        installed = descriptor_type(wrapped) if descriptor_type is not None else wrapped
        setattr(data_provider_class, api, installed)
        handle.patches.append((data_provider_class, api, descriptor, installed))
        covered.append({'api': api, 'source': observer.guarded('source_identity', lambda: _source_identity(original))})

    collision_coverage = 'NOT_REQUESTED'
    if collision_test_class is not None:
        descriptor = inspect.getattr_static(collision_test_class, '_count_collisions', None)
        if not inspect.isfunction(descriptor):
            collision_coverage = 'UNKNOWN_UNSUPPORTED_OR_MISSING_CALLBACK'
        else:
            if getattr(descriptor, '_clear_task_read_only_observer', None) is not None:
                raise RuntimeError('Collision observer already installed')
            original_collision = descriptor
            @functools.wraps(original_collision)
            def collision_wrapper(criterion, event, *args, **kwargs):
                before = observer.guarded('collision_before', lambda: observer._collision_before(criterion, event))
                try:
                    result = original_collision(criterion, event, *args, **kwargs)
                except BaseException as exc:
                    observer.guarded('collision_after_exception', lambda: observer._collision_after(criterion, before, exc))
                    raise
                else:
                    observer.guarded('collision_after', lambda: observer._collision_after(criterion, before))
                    return result
            collision_wrapper._clear_task_read_only_observer = observer
            setattr(collision_test_class, '_count_collisions', collision_wrapper)
            handle.patches.append((collision_test_class, '_count_collisions', descriptor, collision_wrapper))
            collision_coverage = {'callback': '_count_collisions',
                                 'source': observer.guarded('collision_source_identity', lambda: _source_identity(descriptor))}
    receipt = {'covered_spawn_apis': covered, 'missing_spawn_apis': missing,
               'collision_coverage': collision_coverage, 'uncovered_entry_points': list(UNCOVERED_ENTRIES),
               'explicit_process_local_installation': True, 'additional_sensors': 0,
               'original_api_arguments_and_return_identity_preserved': True}
    observer._installations.append(receipt)
    observer.emit('OBSERVER_INSTALLATION.jsonl', 'PATCH_INSTALLATION', **receipt)
    return handle


def install_parked_mesh_observer(observer, route_scenario_class):
    """Observe exact native _parked_ids deltas, including delayed parked mesh props.

    RouteScenario's original method still creates every actor exactly as before.
    The successful-ID list is evidence of creation by this entry, not evidence of
    an unrelated-background role. A missing get_actor handle is UNKNOWN until a
    later world inventory observes it; no position/blueprint is guessed.
    """
    api = 'RouteScenario.spawn_parked_vehicles'
    descriptor = inspect.getattr_static(route_scenario_class, 'spawn_parked_vehicles', None)
    if not inspect.isfunction(descriptor):
        raise TypeError('Expected existing RouteScenario.spawn_parked_vehicles method')
    if getattr(descriptor, '_clear_task_read_only_observer', None) is not None:
        raise RuntimeError('Parked mesh observer already installed')
    original = descriptor
    handle = ObserverInstallation(observer)

    def finish(scenario, request_id, before_ids, result, exception):
        after_ids = list(scenario._parked_ids)
        if before_ids is None:
            added_ids = None
            prefix_preserved = None
        else:
            previous = Counter(before_ids)
            added_ids = []
            for actor_id in after_ids:
                if previous[actor_id]:
                    previous[actor_id] -= 1
                else:
                    added_ids.append(actor_id)
            prefix_preserved = after_ids[:len(before_ids)] == before_ids
        states = []
        for actor_id in added_ids or []:
            if request_id is not None:
                with observer._lock:
                    observer._actor_sources.setdefault(actor_id, []).append(request_id)
                    observer._actor_source_apis.setdefault(actor_id, []).append(api)
            actor = observer.guarded('parked_mesh_get_actor', lambda actor_id=actor_id: scenario.world.get_actor(actor_id))
            state = observer.guarded('parked_mesh_actor_state', lambda: observer.actor_state(actor)) if actor is not None else None
            if state is None:
                state = {'actor_id': actor_id, 'blueprint_type_id': None, 'transform': None,
                         'state_complete': False, 'reason': 'ACTOR_HANDLE_NOT_YET_READABLE',
                         'source_request_ids': [request_id] if request_id is not None else [],
                         'source_api_methods': [api], 'generation_source_status': 'EXACT_PARKED_IDS_DELTA'}
            states.append(state)
        observer.emit('ACTOR_SPAWN_TIMELINE.jsonl', 'PARKED_MESH_NATIVE_METHOD_RESULT',
            request_id=request_id, api=api, parked_ids_before=before_ids,
            parked_ids_after=after_ids, original_id_prefix_preserved=prefix_preserved,
            newly_recorded_parked_actor_ids=added_ids, returned_actor_states=states,
            expected_blueprint_from_native_source='static.prop.mesh',
            expected_blueprint_is_not_observation=True,
            actor_retention='ALL_ORIGINAL_PARKED_PROPS_UNCHANGED_ROLE_NOT_CLASSIFIED',
            original_return_type=_type_name(result), original_return_identity_preserved=True,
            original_exception_type=type(exception).__name__ if exception is not None else None,
            original_exception_message=str(exception) if exception is not None else None,
            extra_spawn_destroy_tick_or_rng_calls=0)

    @functools.wraps(original)
    def wrapped(scenario, *args, **kwargs):
        before_ids = observer.guarded('parked_ids_before', lambda: list(scenario._parked_ids))
        request_id = observer.guarded('parked_mesh_begin',
            lambda: observer._spawn_begin(api, original, (scenario,) + args, kwargs))
        try:
            result = original(scenario, *args, **kwargs)
        except BaseException as exc:
            observer.guarded('parked_mesh_exception_result', lambda: finish(scenario, request_id, before_ids, None, exc))
            raise
        else:
            observer.guarded('parked_mesh_result', lambda: finish(scenario, request_id, before_ids, result, None))
            return result
        finally:
            stack = getattr(observer._local, 'spawn_stack', [])
            if request_id in stack:
                stack.remove(request_id)
    wrapped._clear_task_read_only_observer = observer
    setattr(route_scenario_class, 'spawn_parked_vehicles', wrapped)
    handle.patches.append((route_scenario_class, 'spawn_parked_vehicles', descriptor, wrapped))
    receipt = {'covered_parked_mesh_api': api, 'exact_source_key': '_parked_ids before/after multiset delta',
               'source': observer.guarded('parked_mesh_source_identity', lambda: _source_identity(original)),
               'late_native_calls_remain_wrapped': True, 'original_calls_per_invocation': 1,
               'all_original_actors_preserved': True, 'role_classification': 'NOT_PERFORMED',
               'direct_batch_response_failures_not_captured': True,
               'missing_actor_handle_remains_unknown_until_world_observation': True}
    observer._installations.append(receipt)
    observer.emit('OBSERVER_INSTALLATION.jsonl', 'PARKED_MESH_PATCH_INSTALLATION', **receipt)
    return handle
