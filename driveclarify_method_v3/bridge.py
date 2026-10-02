"""Transactional reuse of existing CARLA/SimLingo navigation owners."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping, Sequence

from driveclarify_candidate_local_navigation_bridge import (
    CandidateForwardNavigationBinding,
    CandidateLocalNavigationObligation,
    MissionNavigationContext,
    QualificationStatus,
    canonical_navigation_projection,
    navigation_projection_digest,
)


def _route_rows(value: Any) -> tuple[Any, ...]:
    route = getattr(value, "route", value)
    if isinstance(route, (str, bytes, bytearray)):
        raise RuntimeError("METHOD_V3_EXISTING_PLANNER_TRACE_INVALID")
    try:
        rows = tuple(route)
    except TypeError as error:
        raise RuntimeError("METHOD_V3_EXISTING_PLANNER_TRACE_INVALID") from error
    if not rows:
        raise RuntimeError("METHOD_V3_EXISTING_PLANNER_TRACE_EMPTY")
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise RuntimeError("METHOD_V3_EXISTING_PLANNER_TRACE_ROW_INVALID")
        if getattr(getattr(row[0], "transform", None), "location", None) is None:
            raise RuntimeError("METHOD_V3_EXISTING_PLANNER_WAYPOINT_INVALID")
    return rows


def _trace_existing_grp_with_fresh_turn_context(
    planner_capability: Any, origin: Any, destination: Any
) -> tuple[Any, ...]:
    """Run one existing-GRP trace without cross-call turn-state leakage."""

    planner_object = planner_capability.planner_object
    if not (
        hasattr(planner_object, "_previous_decision")
        and hasattr(planner_object, "_intersection_end_node")
    ):
        return _route_rows(planner_capability.trace_route(origin, destination))
    previous_decision = planner_object._previous_decision
    intersection_end_node = planner_object._intersection_end_node
    void_decision = getattr(type(previous_decision), "VOID", None)
    if void_decision is None:
        raise RuntimeError("METHOD_V3_GRP_TURN_CONTEXT_OWNER_UNAVAILABLE")
    planner_object._previous_decision = void_decision
    planner_object._intersection_end_node = -1
    try:
        return _route_rows(planner_capability.trace_route(origin, destination))
    finally:
        planner_object._previous_decision = previous_decision
        planner_object._intersection_end_node = intersection_end_node


def _route_digest(rows: Sequence[Any]) -> str:
    payload = []
    for waypoint, option in rows:
        location = waypoint.transform.location
        payload.append(
            {
                "waypoint_id": int(waypoint.id),
                "xyz": [
                    float(location.x).hex(),
                    float(location.y).hex(),
                    float(location.z).hex(),
                ],
                "road_id": int(waypoint.road_id),
                "lane_id": int(waypoint.lane_id),
                "road_option": {
                    "name": str(getattr(option, "name", "")),
                    "value": getattr(option, "value", None),
                },
            }
        )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _location_xyz(waypoint: Any) -> tuple[float, float, float]:
    location = waypoint.transform.location
    values = (float(location.x), float(location.y), float(location.z))
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError("METHOD_V3_TOPOLOGY_LOCATION_INVALID")
    return values


def _distance(left: Any, right: Any) -> float:
    left_xyz = _location_xyz(left)
    right_xyz = _location_xyz(right)
    return math.hypot(left_xyz[0] - right_xyz[0], left_xyz[1] - right_xyz[1])


def _waypoint_id(waypoint: Any) -> int:
    try:
        return int(waypoint.id)
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError("METHOD_V3_TOPOLOGY_WAYPOINT_ID_INVALID") from error


def _junction_id(waypoint: Any) -> int | None:
    if not bool(getattr(waypoint, "is_junction", False)):
        return None
    value = getattr(waypoint, "junction_id", None)
    if value is not None:
        return int(value)
    junction = waypoint.get_junction()
    return None if junction is None else int(junction.id)


@dataclass(frozen=True)
class BoundRoadOptionV3(Mapping[str, Any]):
    """Immutable exact RoadOption token stored in the installed-route sidecar."""

    name: str
    value: Any

    def __getitem__(self, key: str) -> Any:
        if key not in ("name", "value"):
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(("name", "value"))

    def __len__(self) -> int:
        return 2


@dataclass(frozen=True)
class InstalledRouteRowV3(Mapping[str, Any]):
    """Immutable metadata for one installation-order RoutePlanner row."""

    original_index: int
    waypoint_id: int
    xyz_m: tuple[float, float, float]
    xyz_hex: tuple[str, str, str]
    carla_world_xyz_m: tuple[float, float, float]
    coordinate_domain: str
    carla_world_coordinate_domain: str
    road_option: BoundRoadOptionV3
    road_id: int
    section_id: int
    lane_id: int
    junction_id: int | None
    is_junction: bool
    topology_role: str

    def __getitem__(self, key: str) -> Any:
        if key not in self.__dataclass_fields__:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.__dataclass_fields__)

    def __len__(self) -> int:
        return len(self.__dataclass_fields__)


def target_window_catalog_digest(
    catalog: Sequence[Mapping[str, Any]],
) -> str:
    payload = [
        asdict(row) if isinstance(row, InstalledRouteRowV3) else dict(row)
        for row in catalog
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _forward_xy(waypoint: Any) -> tuple[float, float]:
    transform = waypoint.transform
    getter = getattr(transform, "get_forward_vector", None)
    vector = getter() if callable(getter) else transform.rotation.get_forward_vector()
    x_value = float(vector.x)
    y_value = float(vector.y)
    magnitude = math.hypot(x_value, y_value)
    if not math.isfinite(magnitude) or magnitude <= 0.0:
        raise RuntimeError("METHOD_V3_TOPOLOGY_DIRECTION_INVALID")
    return x_value / magnitude, y_value / magnitude


def _option_token(option: Any) -> tuple[str, Any]:
    name = str(getattr(option, "name", "")).strip().upper()
    value = getattr(option, "value", None)
    if not name and value is None:
        raise RuntimeError("METHOD_V3_ROUTE_COMMAND_INVALID")
    return name, value


_MANEUVER_COMMAND_NAMES = frozenset(("LEFT", "RIGHT", "STRAIGHT"))


def _selected_edge_command_context(
    command_trace: Sequence[Any],
    topology_edge: Sequence[Any],
    *,
    selected_entry: Any,
    selected_junction_id: int,
    expected_command_name: str,
    mismatch_observer: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[int, tuple[Any, ...], bool]:
    """Resolve one exact edge interval and its existing-GRP maneuver owner."""

    edge_ids = tuple(_waypoint_id(waypoint) for waypoint in topology_edge)
    interval_length = len(edge_ids)
    trace_ids = tuple(_waypoint_id(row[0]) for row in command_trace)
    starts = tuple(
        index
        for index in range(len(trace_ids) - interval_length + 1)
        if trace_ids[index : index + interval_length] == edge_ids
    )
    if not starts:
        raise RuntimeError("METHOD_V3_SELECTED_EDGE_NOT_IN_EXISTING_GRP_COMMAND_TRACE")
    if len(starts) != 1:
        raise RuntimeError("METHOD_V3_SELECTED_EDGE_COMMAND_AMBIGUOUS")
    first_edge_index = starts[0]

    # CARLA may emit the preceding graph edge's exit waypoint at the decision
    # node. It can carry the selected junction id while being a boundary row,
    # not a traversed incompatible connector. Admit only that one adjacent,
    # sampling-coincident row; every other same-junction row remains forbidden.
    incompatible_before = any(
        _junction_id(waypoint) == selected_junction_id
        and not (
            index == first_edge_index - 1
            and all(
                math.isclose(observed, expected, rel_tol=0.0, abs_tol=1.0e-6)
                for observed, expected in zip(
                    _location_xyz(waypoint), _location_xyz(selected_entry)
                )
            )
        )
        for index, (waypoint, _) in enumerate(command_trace[:first_edge_index])
    )
    if incompatible_before:
        raise RuntimeError("METHOD_V3_INCOMPATIBLE_CONNECTOR_BEFORE_SELECTED")

    connector_rows = tuple(
        command_trace[first_edge_index : first_edge_index + interval_length]
    )
    maneuver_name = _option_token(connector_rows[0][1])[0]
    if maneuver_name != expected_command_name:
        if mismatch_observer is not None:
            mismatch_observer(
                {
                    "reason_code": "METHOD_V3_SELECTED_EDGE_COMMAND_DIRECTION_MISMATCH",
                    "expected_roadoption": expected_command_name,
                    "actual_roadoption": maneuver_name,
                    "first_edge_trace_index": first_edge_index,
                    "connector_waypoint_id": _waypoint_id(connector_rows[0][0]),
                    "connector_row_offset": 0,
                }
            )
        raise RuntimeError("METHOD_V3_SELECTED_EDGE_COMMAND_DIRECTION_MISMATCH")
    internal_lanefollow = False
    for index, (_, option) in enumerate(connector_rows[1:], start=1):
        name = _option_token(option)[0]
        if name == "LANEFOLLOW":
            internal_lanefollow = True
            continue
        if name != expected_command_name:
            if name in _MANEUVER_COMMAND_NAMES:
                if mismatch_observer is not None:
                    mismatch_observer(
                        {
                            "reason_code": "METHOD_V3_SELECTED_EDGE_COMMAND_DIRECTION_MISMATCH",
                            "expected_roadoption": expected_command_name,
                            "actual_roadoption": name,
                            "first_edge_trace_index": first_edge_index,
                            "connector_waypoint_id": _waypoint_id(
                                connector_rows[index][0]
                            ),
                            "connector_row_offset": index,
                        }
                    )
                raise RuntimeError("METHOD_V3_SELECTED_EDGE_COMMAND_DIRECTION_MISMATCH")
            raise RuntimeError("METHOD_V3_SELECTED_EDGE_COMMAND_AMBIGUOUS")
    return first_edge_index, connector_rows, internal_lanefollow


def _identity_value(owner: Any, field: str) -> Any:
    if isinstance(owner, Mapping):
        return owner.get(field)
    return getattr(owner, field, None)


def _waypoint_matches_lane_identity(waypoint: Any, identity: Any) -> bool:
    try:
        return bool(
            int(waypoint.road_id) == int(_identity_value(identity, "road_id"))
            and int(waypoint.section_id)
            == int(_identity_value(identity, "section_id"))
            and int(waypoint.lane_id) == int(_identity_value(identity, "lane_id"))
            and _junction_id(waypoint)
            == int(_identity_value(identity, "junction_id"))
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _waypoint_matches_nonjunction_lane_identity(
    waypoint: Any, identity: Any
) -> bool:
    try:
        return bool(
            int(waypoint.road_id) == int(_identity_value(identity, "road_id"))
            and int(waypoint.section_id)
            == int(_identity_value(identity, "section_id"))
            and int(waypoint.lane_id) == int(_identity_value(identity, "lane_id"))
            and not bool(getattr(waypoint, "is_junction", False))
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _make_location(template: Any, xyz: Sequence[float]) -> Any:
    values = tuple(float(value) for value in xyz[:3])
    location_type = type(template)
    try:
        return location_type(x=values[0], y=values[1], z=values[2])
    except (TypeError, ValueError):
        try:
            return location_type(values[0], values[1], values[2])
        except (TypeError, ValueError):
            return SimpleNamespace(x=values[0], y=values[1], z=values[2])


def _resolve_selected_junction_pair(
    map_object: Any,
    topology_owner: Any,
    planner_object: Any,
) -> tuple[Any, Any, int]:
    del map_object
    entry_identity = _identity_value(topology_owner, "selected_lane_link_entry")
    exit_identity = _identity_value(topology_owner, "selected_lane_link_exit")
    if entry_identity is None or exit_identity is None:
        raise RuntimeError("METHOD_V3_SELECTED_TOPOLOGY_OWNER_INVALID")
    entry_id = int(_identity_value(entry_identity, "waypoint_id"))
    exit_id = int(_identity_value(exit_identity, "waypoint_id"))
    topology = getattr(planner_object, "_topology", None)
    if not isinstance(topology, (tuple, list)):
        raise RuntimeError("METHOD_V3_EXISTING_GRP_TOPOLOGY_UNAVAILABLE")
    topology_matches = tuple(
        segment.get("entry")
        for segment in topology
        if isinstance(segment, Mapping)
        and segment.get("entry") is not None
        and _waypoint_matches_lane_identity(segment["entry"], entry_identity)
    )
    if len(topology_matches) != 1:
        raise RuntimeError("METHOD_V3_SELECTED_EDGE_NOT_IN_EXISTING_GRP_TOPOLOGY")
    topology_entry = topology_matches[0]
    junction = topology_entry.get_junction()
    if junction is None:
        raise RuntimeError("METHOD_V3_SELECTED_JUNCTION_MISSING")
    pairs = tuple(
        junction.get_waypoints(getattr(topology_entry, "lane_type", None))
    )
    matches = tuple(
        (entry, exit_waypoint)
        for entry, exit_waypoint in pairs
        if _waypoint_id(entry) == entry_id and _waypoint_id(exit_waypoint) == exit_id
    )
    if len(matches) != 1:
        raise RuntimeError("METHOD_V3_SELECTED_JUNCTION_PAIR_NOT_UNIQUE")
    entry, exit_waypoint = matches[0]
    junction_id = int(junction.id)
    if _junction_id(entry) != junction_id or _junction_id(exit_waypoint) != junction_id:
        raise RuntimeError("METHOD_V3_SELECTED_JUNCTION_PAIR_IDENTITY_MISMATCH")
    return entry, exit_waypoint, junction_id


def _resolve_existing_grp_topology_edge(
    planner_object: Any,
    entry: Any,
    exit_waypoint: Any,
    topology_owner: Any,
) -> tuple[Any, ...]:
    topology = getattr(planner_object, "_topology", None)
    if not isinstance(topology, (tuple, list)):
        raise RuntimeError("METHOD_V3_EXISTING_GRP_TOPOLOGY_UNAVAILABLE")
    sampling_resolution = float(
        getattr(planner_object, "_sampling_resolution", math.nan)
    )
    if not math.isfinite(sampling_resolution) or sampling_resolution <= 0.0:
        raise RuntimeError("METHOD_V3_SELECTED_EDGE_ORIENTATION_INVALID")
    downstream_identities = tuple(
        _identity_value(topology_owner, "downstream_lanes") or ()
    )
    matches = []
    for segment in topology:
        if not isinstance(segment, Mapping):
            continue
        segment_entry = segment.get("entry")
        segment_exit = segment.get("exit")
        try:
            path = tuple(segment.get("path") or ())
            connector_lane_rows = (segment_entry,) + path
            connector_exit_identity_present = bool(
                _waypoint_matches_lane_identity(segment_exit, exit_waypoint)
                or (
                    connector_lane_rows
                    and _waypoint_matches_lane_identity(
                        connector_lane_rows[-1], exit_waypoint
                    )
                )
            )
            terminal_identity_valid = bool(
                _waypoint_matches_lane_identity(segment_exit, exit_waypoint)
                or any(
                    _waypoint_matches_nonjunction_lane_identity(
                        segment_exit, identity
                    )
                    for identity in downstream_identities
                )
            )
            matched = bool(
                int(segment_entry.road_id) == int(entry.road_id)
                and int(segment_entry.section_id) == int(entry.section_id)
                and int(segment_entry.lane_id) == int(entry.lane_id)
                and _junction_id(segment_entry) == _junction_id(entry)
                and connector_exit_identity_present
                and terminal_identity_valid
                and _distance(segment_exit, exit_waypoint) <= sampling_resolution
            )
        except RuntimeError:
            matched = False
        if matched:
            matches.append((segment_entry,) + path + (segment_exit,))
    if len(matches) != 1:
        raise RuntimeError("METHOD_V3_SELECTED_EDGE_NOT_IN_EXISTING_GRP_TOPOLOGY")
    edge = matches[0]
    if (
        len(edge) < 2
        or not math.isfinite(sampling_resolution)
        or sampling_resolution <= 0.0
        or _distance(edge[0], entry) > sampling_resolution
        or _distance(edge[-1], exit_waypoint) > sampling_resolution
    ):
        raise RuntimeError("METHOD_V3_SELECTED_EDGE_ORIENTATION_INVALID")
    for left, right in zip(edge, edge[1:]):
        left_direction = _forward_xy(left)
        right_direction = _forward_xy(right)
        if left_direction[0] * right_direction[0] + left_direction[1] * right_direction[1] <= 0.0:
            raise RuntimeError("METHOD_V3_SELECTED_EDGE_ORIENTATION_INVALID")
    return edge


def _snapshot_active_route(route_owner: Any) -> tuple[Any, ...] | None:
    public_snapshot = getattr(route_owner, "snapshot_active_route", None)
    if callable(public_snapshot):
        rows = tuple(public_snapshot())
        return rows or None
    route_planner = getattr(route_owner, "_route_planner", None)
    route = getattr(route_planner, "route", None)
    if route is None:
        return None
    lock = getattr(route_planner, "_route_lock", None)
    if lock is None:
        return tuple(route) or None
    with lock:
        return tuple(
            (tuple(float(value) for value in position), option)
            for position, option in route
        ) or None


def _active_prefix_to_selected_entry(
    route_owner: Any,
    map_object: Any,
    selected_entry: Any,
    selected_junction_id: int,
) -> tuple[Any, ...] | None:
    active = _snapshot_active_route(route_owner)
    if not active:
        return None
    template = selected_entry.transform.location
    prefix = []
    selected_junction_seen = False
    for position, option in active:
        try:
            xyz = (float(position[0]), float(position[1]), float(position[2]))
        except (IndexError, TypeError, ValueError):
            location = getattr(position, "location", position)
            xyz = (float(location.x), float(location.y), float(location.z))
        waypoint = map_object.get_waypoint(
            _make_location(template, xyz), project_to_road=True
        )
        if _junction_id(waypoint) == selected_junction_id:
            selected_junction_seen = True
            break
        prefix.append((waypoint, option))
    if not selected_junction_seen or not prefix:
        return None
    return tuple(prefix)


def _trace_prefix_fallback(
    planner_capability: Any,
    live_ego_endpoint: Any,
    selected_entry: Any,
    selected_junction_id: int,
    *,
    map_object: Any,
    route_owner: Any,
) -> tuple[Any, ...]:
    traced = _trace_existing_grp_with_fresh_turn_context(
        planner_capability,
        live_ego_endpoint,
        selected_entry.transform.location,
    )
    prefix = []
    for row in traced:
        if _junction_id(row[0]) == selected_junction_id:
            break
        prefix.append(row)
    if not prefix:
        live_waypoint = map_object.get_waypoint(
            live_ego_endpoint, project_to_road=True
        )
        if _junction_id(live_waypoint) == selected_junction_id:
            raise RuntimeError("METHOD_V3_PREFIX_TO_SELECTED_ENTRY_UNAVAILABLE")
        active = _snapshot_active_route(route_owner)
        command = active[0][1] if active else traced[0][1]
        prefix.append((live_waypoint, command))
    return tuple(prefix)


def _append_exact_boundary_deduplicated(
    combined: list[Any], segment: Sequence[Any]
) -> int:
    rows = list(segment)
    removed = 0
    while combined and rows and _waypoint_id(combined[-1][0]) == _waypoint_id(rows[0][0]):
        rows.pop(0)
        removed += 1
    combined.extend(rows)
    return removed


def _route_continuity(rows: Sequence[Any], maximum_gap_m: float) -> tuple[bool, float]:
    gaps = [_distance(left[0], right[0]) for left, right in zip(rows, rows[1:])]
    maximum = max(gaps, default=0.0)
    return bool(math.isfinite(maximum) and maximum <= maximum_gap_m), maximum


def _active_route_row_xyz(row: Any) -> tuple[float, float, float]:
    position = row[0]
    location = getattr(getattr(position, "transform", None), "location", None)
    if location is None:
        location = getattr(position, "location", position)
    try:
        values = (
            float(location.x),
            float(location.y),
            float(getattr(location, "z", 0.0)),
        )
    except AttributeError:
        values = tuple(float(value) for value in location[:3])
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise RuntimeError("METHOD_V3_POSTINSTALL_ROUTE_ROW_INVALID")
    return values


def _world_to_route_planner_xyz(
    route_owner: Any, world_xyz: Sequence[float]
) -> tuple[float, float, float]:
    converter = getattr(route_owner, "world_to_route_planner_xyz", None)
    if callable(converter):
        converted = tuple(float(value) for value in converter(world_xyz))
    else:
        converted = tuple(float(value) for value in world_xyz)
    if len(converted) != 3 or not all(math.isfinite(value) for value in converted):
        raise RuntimeError("METHOD_V3_ROUTE_COORDINATE_CONVERSION_INVALID")
    return converted


def _postinstall_connector_readback(
    route_owner: Any,
    *,
    connector_start: int,
    topology_edge: Sequence[Any],
    command_tokens: Sequence[tuple[str, Any]],
) -> tuple[bool, int]:
    active = _snapshot_active_route(route_owner)
    if active is None:
        raise RuntimeError("METHOD_V3_POSTINSTALL_ACTIVE_ROUTE_UNAVAILABLE")
    connector_end = connector_start + len(topology_edge)
    if connector_end > len(active):
        raise RuntimeError("METHOD_V3_POSTINSTALL_MANDATORY_CONNECTOR_MISSING")
    installed_rows = active[connector_start:connector_end]
    geometry_matches = all(
        all(
            math.isclose(observed, expected, rel_tol=0.0, abs_tol=1.0e-6)
            for observed, expected in zip(
                _active_route_row_xyz(row),
                _world_to_route_planner_xyz(
                    route_owner, _location_xyz(waypoint)
                ),
            )
        )
        for row, waypoint in zip(installed_rows, topology_edge)
    )
    commands_match = tuple(_option_token(row[1]) for row in installed_rows) == tuple(
        command_tokens
    )
    if not geometry_matches or not commands_match:
        raise RuntimeError("METHOD_V3_POSTINSTALL_MANDATORY_CONNECTOR_MISSING")
    return True, len(active)


@dataclass(frozen=True)
class SelectedRouteBindingReceiptV3:
    selected_obligation_identity: str
    selected_branch_identity: str
    selected_junction_identity: str
    selected_entry_waypoint_id: int
    selected_entry_road_id: int
    selected_entry_lane_id: int
    selected_exit_waypoint_id: int
    selected_exit_road_id: int
    selected_exit_lane_id: int
    selected_connector_waypoint_ids: tuple[int, ...]
    selected_connector_road_option_name: str
    selected_connector_road_option_value: Any
    selected_connector_road_option_sequence: tuple[tuple[str, Any], ...]
    authoritative_maneuver_decision_trace_index: int
    authoritative_maneuver_decision_route_index: int
    internal_connector_lanefollow_present: bool
    global_destination_identity_before: str
    global_destination_identity_after: str
    existing_planner_provenance: str
    existing_planner_object_identity: str
    existing_planner_call_count: int
    prefix_source: str
    prefix_row_count: int
    connector_row_count: int
    selected_exit_to_destination_row_count: int
    installed_route_row_count: int
    mandatory_connector_start_index: int
    mandatory_connector_end_index: int
    composed_existing_trace_digest: str
    selected_exit_terminal_lane_match: bool
    mandatory_connector_present: bool
    mandatory_connector_order_valid: bool
    incompatible_connector_before_selected: bool
    connector_direction_valid: bool
    route_continuity_valid: bool
    route_maximum_gap_m: float
    route_continuity_owner_threshold_m: float
    duplicate_boundary_row_count_removed: int
    route_commands_preserved: bool
    route_owner_acknowledged: bool
    route_install_attempted: bool
    route_install_accepted: bool
    active_route_owner_before: str
    active_route_owner_after: str
    active_route_identity_before: str
    active_route_identity_after: str
    active_route_changed: bool
    route_transaction_identity: str
    route_installation_event_identity: str | None
    route_generation_before: int | None
    route_generation_after: int | None
    candidate_generation_identity: str | None
    selected_plan_transaction_identity: str | None
    query_identity: str | None
    answer_identity: str | None
    fresh_plan_source_frame_id: str | None
    fresh_plan_source_observation_id: str | None
    post_install_active_route_row_count: int
    post_install_mandatory_connector_present: bool
    installed_route_identity: str
    next_tick_consumed_route_identity: str | None
    planning_effective_k: int
    target_window_contract_identity: str
    target_window_route_catalog: tuple[InstalledRouteRowV3, ...]
    target_window_route_catalog_digest: str
    target_window_connector_terminal_identity: InstalledRouteRowV3
    target_window_compatible_suffix_original_indices: tuple[int, ...]
    target_window_catalog_bound: bool
    installed_route_coordinate_domain: str = "SIMLINGO_ROUTE_PLANNER"
    connector_projection_coordinate_domain: str = "SIMLINGO_ROUTE_PLANNER"
    source_topology_coordinate_domain: str = "CARLA_WORLD"
    world_to_route_planner_translation_xyz_m: tuple[float, float, float] = (
        0.0,
        0.0,
        0.0,
    )
    new_global_planner_count: int = 0
    synthetic_trajectory_point_count: int = 0
    new_pid_count: int = 0
    new_vehiclecontrol_writer_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update(
            {
                "schema_version": "driveclarify.method_v3.exact_lanelink_binding.r2.v1",
                "same_original_global_destination_preserved": (
                    self.global_destination_identity_before
                    == self.global_destination_identity_after
                ),
                "authoritative_navigation_switch_atomic": True,
                "route_rows_are_existing_carla_topology_or_grp_outputs_only": True,
                "mandatory_connector_is_navigation_metadata_not_vla_trajectory": True,
                "MANDATORY_CONNECTOR_PRESENT": self.mandatory_connector_present,
                "MANDATORY_CONNECTOR_ORDER_VALID": self.mandatory_connector_order_valid,
                "INCOMPATIBLE_CONNECTOR_BEFORE_SELECTED": (
                    self.incompatible_connector_before_selected
                ),
                "ROUTE_INSTALL_ATTEMPTED": self.route_install_attempted,
                "ROUTE_INSTALL_ACCEPTED": self.route_install_accepted,
                "POST_INSTALL_MANDATORY_CONNECTOR_PRESENT": (
                    self.post_install_mandatory_connector_present
                ),
            }
        )
        return value


class SelectedBranchRouteBindingBridgeV3:
    """Splice one exact existing CARLA lane-link into the active route."""

    def __init__(self, mission: MissionNavigationContext, planner_capability: Any, route_owner: Any):
        if not isinstance(mission, MissionNavigationContext):
            raise TypeError("METHOD_V3_MISSION_INVALID")
        mission.assert_endpoint_unchanged()
        if not getattr(planner_capability, "available", False):
            raise RuntimeError("METHOD_V3_EXISTING_PLANNER_UNAVAILABLE")
        if not callable(getattr(planner_capability, "trace_route", None)):
            raise TypeError("METHOD_V3_EXISTING_PLANNER_INVALID")
        if not callable(getattr(route_owner, "install_reconnected_route", None)):
            raise TypeError("METHOD_V3_ROUTE_OWNER_INVALID")
        self.mission = mission
        self.planner_capability = planner_capability
        self.route_owner = route_owner

    def bind(
        self,
        obligation: CandidateLocalNavigationObligation,
        live_ego_planner_endpoint: Any,
        *,
        map_object: Any,
        selected_topology_owner: Any,
        preinstall_observer: Callable[[Mapping[str, Any]], None] | None = None,
        installation_event_identity: str | None = None,
        selected_maneuver_direction: str | None = None,
        transaction_provenance: Mapping[str, Any] | None = None,
    ) -> SelectedRouteBindingReceiptV3:
        if not isinstance(obligation, CandidateLocalNavigationObligation):
            raise TypeError("METHOD_V3_SELECTED_OBLIGATION_INVALID")
        if obligation.qualification_status is not QualificationStatus.QUALIFIED:
            raise RuntimeError("METHOD_V3_SELECTED_OBLIGATION_NOT_QUALIFIED")
        if obligation.global_destination_identity != self.mission.global_destination_identity:
            raise RuntimeError("METHOD_V3_GLOBAL_DESTINATION_IDENTITY_CHANGED")
        if obligation.mission_context_digest != self.mission.mission_context_digest:
            raise RuntimeError("METHOD_V3_MISSION_CONTEXT_CHANGED")
        self.mission.assert_endpoint_unchanged()

        selected_entry, selected_exit, junction_id = _resolve_selected_junction_pair(
            map_object,
            selected_topology_owner,
            self.planner_capability.planner_object,
        )
        topology_edge = _resolve_existing_grp_topology_edge(
            self.planner_capability.planner_object,
            selected_entry,
            selected_exit,
            selected_topology_owner,
        )
        edge_ids = tuple(_waypoint_id(waypoint) for waypoint in topology_edge)

        active_prefix = _active_prefix_to_selected_entry(
            self.route_owner,
            map_object,
            selected_entry,
            junction_id,
        )
        if active_prefix is None:
            prefix = _trace_prefix_fallback(
                self.planner_capability,
                live_ego_planner_endpoint,
                selected_entry,
                junction_id,
                map_object=map_object,
                route_owner=self.route_owner,
            )
            prefix_source = (
                "EXISTING_GRP_EGO_TO_SELECTED_ENTRY"
                if _waypoint_id(prefix[-1][0]) != _waypoint_id(
                    map_object.get_waypoint(
                        live_ego_planner_endpoint, project_to_road=True
                    )
                )
                else "LIVE_EGO_CARLA_WAYPOINT_WITH_EXISTING_ROUTE_OR_GRP_COMMAND"
            )
            planner_call_count = 1
        else:
            prefix = active_prefix
            prefix_source = "EXISTING_ACTIVE_ROUTE_PREFIX_TO_SELECTED_ENTRY"
            planner_call_count = 0

        sampling_resolution = float(
            getattr(
                self.planner_capability.planner_object,
                "_sampling_resolution",
                math.nan,
            )
        )
        if not math.isfinite(sampling_resolution) or sampling_resolution <= 0.0:
            raise RuntimeError("METHOD_V3_SELECTED_EDGE_ORIENTATION_INVALID")
        provenance = dict(transaction_provenance or {})
        expected_direction = str(
            obligation.maneuver_direction
            if selected_maneuver_direction is None
            else selected_maneuver_direction
        ).strip().upper()
        expected_direction_source = (
            "CandidateLocalNavigationObligation.maneuver_direction"
            if selected_maneuver_direction is None
            else "selected opportunity representative.maneuver_direction"
        )
        expected_commands = {
            "LEFT": "LEFT",
            "RIGHT": "RIGHT",
            "STRAIGHT": "STRAIGHT",
            "GO_STRAIGHT": "STRAIGHT",
        }
        if expected_direction not in expected_commands:
            if preinstall_observer is not None:
                preinstall_observer(
                    {
                        "schema_version": (
                            "driveclarify.method_v3.preinstall_route_gate_failure.v1"
                        ),
                        "selected_semantic_obligation": obligation.obligation_identity,
                        "transaction_provenance": provenance,
                        "failed_gate": {
                            "id": "G09",
                            "name": "MANEUVER_COMMAND_OWNERSHIP",
                            "result": "FAIL",
                            "left": expected_direction,
                            "right": sorted(expected_commands),
                            "expected_roadoption": "NOT_RESOLVED",
                            "actual_roadoption": "NOT_CONSUMED",
                            "expected_direction_source": expected_direction_source,
                            "reason_code": (
                                "METHOD_V3_SELECTED_EDGE_COMMAND_DIRECTION_MISMATCH"
                            ),
                        },
                        "route_install_attempted": False,
                        "persisted_before_route_mutation": True,
                    }
                )
            raise RuntimeError("METHOD_V3_SELECTED_EDGE_COMMAND_DIRECTION_MISMATCH")

        # Keep the approach predecessor required by CARLA's contextual
        # _turn_decision. An exact-entry trace starts at route index zero and
        # can expose only the selected graph edge's stored LANEFOLLOW type.
        command_trace = _trace_existing_grp_with_fresh_turn_context(
            self.planner_capability,
            prefix[-1][0].transform.location,
            topology_edge[-1].transform.location,
        )
        planner_call_count += 1
        def observe_command_mismatch(value: Mapping[str, Any]) -> None:
            if preinstall_observer is None:
                return
            preinstall_observer(
                {
                    "schema_version": (
                        "driveclarify.method_v3.preinstall_route_gate_failure.v1"
                    ),
                    "selected_semantic_obligation": obligation.obligation_identity,
                    "selected_semantic_maneuver": expected_commands[
                        expected_direction
                    ],
                    "expected_direction_source": expected_direction_source,
                    "transaction_provenance": provenance,
                    "failed_gate": {
                        "id": "G10",
                        "name": "SEMANTIC_COMMAND_COMPATIBILITY",
                        "result": "FAIL",
                        **dict(value),
                    },
                    "route_install_attempted": False,
                    "persisted_before_route_mutation": True,
                }
            )

        first_edge_index, connector_rows, internal_lanefollow = (
            _selected_edge_command_context(
                command_trace,
                topology_edge,
                selected_entry=selected_entry,
                selected_junction_id=junction_id,
                expected_command_name=expected_commands[expected_direction],
                mismatch_observer=observe_command_mismatch,
            )
        )
        command_tokens = tuple(_option_token(row[1]) for row in connector_rows)
        command_token = command_tokens[0]
        incompatible_before = False

        seam = command_trace[:first_edge_index]
        suffix = _trace_existing_grp_with_fresh_turn_context(
            self.planner_capability,
            topology_edge[-1].transform.location,
            self.mission.global_destination_planner_endpoint,
        )
        planner_call_count += 1

        proposed: list[Any] = list(prefix)
        duplicate_count = _append_exact_boundary_deduplicated(proposed, seam)
        if proposed and _waypoint_id(proposed[-1][0]) == _waypoint_id(selected_entry):
            proposed.pop()
            duplicate_count += 1
        connector_start = len(proposed)
        proposed.extend(connector_rows)
        connector_end = len(proposed) - 1
        duplicate_count += _append_exact_boundary_deduplicated(proposed, suffix)

        combined_ids = tuple(_waypoint_id(row[0]) for row in proposed)
        connector_present = (
            combined_ids[connector_start : connector_end + 1] == edge_ids
        )
        connector_order_valid = bool(
            connector_present
            and connector_start < connector_end
            and connector_end < len(proposed)
        )
        if not connector_present or not connector_order_valid:
            raise RuntimeError("METHOD_V3_MANDATORY_CONNECTOR_ADMISSION_FAILED")
        continuity_threshold = float(
            getattr(self.route_owner, "continuity_threshold_m", math.nan)
        )
        if not math.isfinite(continuity_threshold) or continuity_threshold <= 0.0:
            raise RuntimeError("METHOD_V3_ROUTE_CONTINUITY_OWNER_UNAVAILABLE")
        continuity_valid, maximum_gap = _route_continuity(
            proposed, continuity_threshold
        )
        if not continuity_valid:
            raise RuntimeError("METHOD_V3_COMBINED_ROUTE_DISCONTINUITY")

        terminal_waypoint = topology_edge[-1]
        downstream_identities = tuple(
            _identity_value(selected_topology_owner, "downstream_lanes") or ()
        )
        connector_exit_lane_present = bool(
            _waypoint_matches_lane_identity(terminal_waypoint, selected_exit)
            or (
                len(topology_edge) >= 2
                and _waypoint_matches_lane_identity(
                    topology_edge[-2], selected_exit
                )
            )
        )
        terminal_identity_valid = bool(
            _waypoint_matches_lane_identity(terminal_waypoint, selected_exit)
            or any(
                _waypoint_matches_nonjunction_lane_identity(
                    terminal_waypoint, identity
                )
                for identity in downstream_identities
            )
        )
        lane_match = bool(
            connector_exit_lane_present
            and terminal_identity_valid
            and math.isfinite(sampling_resolution)
            and _distance(terminal_waypoint, selected_exit) <= sampling_resolution
        )
        if not lane_match:
            raise RuntimeError("METHOD_V3_SELECTED_BRANCH_NOT_REALIZED_BY_EXISTING_TOPOLOGY")
        proposed_digest = _route_digest(proposed)

        route_owner_identity = (
            type(self.route_owner).__module__
            + "."
            + type(self.route_owner).__qualname__
            + "@"
            + format(id(self.route_owner), "x")
        )
        active_identity_before = str(
            getattr(self.route_owner, "active_route_identity", "") or ""
        )
        route_planner_before = getattr(self.route_owner, "_route_planner", None)
        route_generation_before = getattr(
            route_planner_before, "online_update_generation", None
        )
        if route_generation_before is not None:
            route_generation_before = int(route_generation_before)
        transaction_identity = hashlib.sha256(
            json.dumps(
                {
                    "obligation": obligation.obligation_identity,
                    "candidate_generation_identity": provenance.get(
                        "candidate_generation_identity"
                    ),
                    "selected_plan_transaction_identity": provenance.get(
                        "selected_plan_transaction_identity"
                    ),
                    "selected_connector_waypoint_ids": edge_ids,
                    "proposed_route_digest": proposed_digest,
                    "destination": self.mission.global_destination_identity,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        preinstall_receipt = {
            "schema_version": "driveclarify.method_v3.preinstall_route.v1",
            "selected_semantic_obligation": obligation.obligation_identity,
            "selected_semantic_maneuver": expected_commands[expected_direction],
            "selected_semantic_maneuver_source": expected_direction_source,
            "obligation_semantic_constraint": obligation.maneuver_direction,
            "transaction_provenance": provenance,
            "selected_semantic_topological_class": {
                "local_branch_identity": obligation.local_branch_identity,
                "selected_junction_identity": f"junction-map-{junction_id}",
                "selected_connector_waypoint_ids": list(edge_ids),
            },
            "selected_junction_identity": f"junction-map-{junction_id}",
            "selected_entry_identity": {
                "waypoint_id": _waypoint_id(selected_entry),
                "junction_id": junction_id,
                "road_id": int(selected_entry.road_id),
                "section_id": int(selected_entry.section_id),
                "lane_id": int(selected_entry.lane_id),
                "xyz_m": list(_location_xyz(selected_entry)),
            },
            "selected_connector_identity": {
                "waypoint_ids": list(edge_ids),
                "rows": [
                    {
                        "waypoint_id": _waypoint_id(waypoint),
                        "junction_id": _junction_id(waypoint),
                        "road_id": int(waypoint.road_id),
                        "section_id": int(waypoint.section_id),
                        "lane_id": int(waypoint.lane_id),
                        "xyz_m": list(_location_xyz(waypoint)),
                    }
                    for waypoint in topology_edge
                ],
            },
            "selected_exit_identity": {
                "waypoint_id": _waypoint_id(selected_exit),
                "junction_id": junction_id,
                "road_id": int(selected_exit.road_id),
                "section_id": int(selected_exit.section_id),
                "lane_id": int(selected_exit.lane_id),
                "xyz_m": list(_location_xyz(selected_exit)),
            },
            "connector_source_provenance": "CarlaDataProvider._grp._topology",
            "prefix_provenance": prefix_source,
            "suffix_provenance": "CarlaDataProvider._grp.trace_route(SELECTED_EXIT,ORIGINAL_DESTINATION)",
            "original_destination_identity": self.mission.global_destination_identity,
            "combined_route_length": len(proposed),
            "mandatory_connector_interval_indices": [connector_start, connector_end],
            "authoritative_maneuver_decision_row": {
                "grp_trace_row_index": first_edge_index,
                "combined_route_row_index": connector_start,
                "waypoint_id": _waypoint_id(connector_rows[0][0]),
                "junction_id": _junction_id(connector_rows[0][0]),
                "road_id": int(connector_rows[0][0].road_id),
                "section_id": int(connector_rows[0][0].section_id),
                "lane_id": int(connector_rows[0][0].lane_id),
                "road_option": {
                    "name": command_token[0],
                    "value": command_token[1],
                },
                "producer": "CarlaDataProvider._grp.trace_route(RETAINED_UPSTREAM_APPROACH,SELECTED_CONNECTOR_TERMINAL)",
            },
            "authoritative_maneuver_command": {
                "name": command_token[0],
                "value": command_token[1],
            },
            "selected_semantic_vs_maneuver_command_match": True,
            "internal_connector_lanefollow_allowed_by_grp_semantics": True,
            "internal_connector_lanefollow_present": internal_lanefollow,
            "internal_connector_roadoptions": [
                {"name": name, "value": value}
                for name, value in command_tokens[1:]
            ],
            "route_commands_over_connector": [
                {
                    "connector_order_index": index,
                    "combined_route_row_index": connector_start + index,
                    "waypoint_id": _waypoint_id(waypoint),
                    "road_id": int(waypoint.road_id),
                    "section_id": int(waypoint.section_id),
                    "lane_id": int(waypoint.lane_id),
                    "junction_id": _junction_id(waypoint),
                    "road_option": {"name": token[0], "value": token[1]},
                }
                for index, ((waypoint, _), token) in enumerate(
                    zip(connector_rows, command_tokens)
                )
            ],
            "combined_route_command_context": [
                {
                    "combined_route_row_index": index,
                    "role": (
                        "AUTHORITATIVE_MANEUVER_DECISION_ROW"
                        if index == connector_start
                        else "SELECTED_CONNECTOR_INTERNAL"
                        if connector_start < index <= connector_end
                        else "APPROACH"
                        if index < connector_start
                        else "EXIT_OR_SUFFIX"
                    ),
                    "waypoint_id": _waypoint_id(waypoint),
                    "road_id": int(waypoint.road_id),
                    "section_id": int(waypoint.section_id),
                    "lane_id": int(waypoint.lane_id),
                    "junction_id": _junction_id(waypoint),
                    "road_option": {
                        "name": _option_token(option)[0],
                        "value": _option_token(option)[1],
                    },
                }
                for index, (waypoint, option) in enumerate(proposed)
                if connector_start - 3 <= index <= connector_end + 3
            ],
            "correct_orientation": True,
            "continuity": {
                "prefix_to_entry": (
                    connector_start == 0
                    or _distance(proposed[connector_start - 1][0], proposed[connector_start][0])
                    <= continuity_threshold
                ),
                "entry_to_connector": True,
                "connector_to_exit": True,
                "exit_to_suffix": (
                    connector_end + 1 >= len(proposed)
                    or _distance(proposed[connector_end][0], proposed[connector_end + 1][0])
                    <= continuity_threshold
                ),
                "combined_route": continuity_valid,
                "maximum_gap_m": maximum_gap,
                "owner_threshold_m": continuity_threshold,
            },
            "incompatible_connector_before_selected": incompatible_before,
            "mandatory_connector_present": connector_present,
            "same_destination": True,
            "transaction_admission": "PASS",
            "route_transaction_identity": transaction_identity,
            "proposed_route_digest": proposed_digest,
            "active_route_owner_before": route_owner_identity,
            "active_route_identity_before": active_identity_before,
            "route_generation_before": route_generation_before,
            "route_generation_expected_after": (
                None
                if route_generation_before is None
                else route_generation_before + 1
            ),
            "route_install_attempted": False,
            "persisted_before_route_mutation": True,
        }
        preinstall_receipt["full_preinstall_gate_matrix"] = [
            {
                "id": gate_id,
                "name": name,
                "result": "PASS",
                "operands": operands,
                "provenance": owner,
            }
            for gate_id, name, operands, owner in (
                (
                    "G01",
                    "SELECTED_ANSWER_IDENTITY",
                    provenance.get("selected_answer_identity"),
                    "persistent query lifecycle",
                ),
                (
                    "G02",
                    "SEMANTIC_RELEVANCE",
                    provenance.get("semantic_relevance"),
                    "V2.8 semantic expiry",
                ),
                (
                    "G03",
                    "OPPORTUNITY_EQUIVALENCE",
                    provenance.get("opportunity_equivalence"),
                    "resolve_selected_opportunity_equivalence_v3",
                ),
                (
                    "G04",
                    "SELECTED_JUNCTION_IDENTITY",
                    f"junction-map-{junction_id}",
                    "downstream landing",
                ),
                (
                    "G05",
                    "SELECTED_ENTRY_IDENTITY",
                    _waypoint_id(selected_entry),
                    "Junction.get_waypoints(Driving)",
                ),
                (
                    "G06",
                    "SELECTED_CONNECTOR_IDENTITY",
                    list(edge_ids),
                    "CarlaDataProvider._grp._topology",
                ),
                (
                    "G07",
                    "SELECTED_EXIT_IDENTITY",
                    _waypoint_id(selected_exit),
                    "GRP terminal reconciliation",
                ),
                (
                    "G08",
                    "CONNECTOR_ORIENTATION",
                    True,
                    "existing GRP topology forward vectors",
                ),
                (
                    "G09",
                    "MANEUVER_COMMAND_OWNERSHIP",
                    {
                        "obligation_semantic_constraint": obligation.maneuver_direction,
                        "selected_opportunity_maneuver": expected_direction,
                        "owner": expected_direction_source,
                    },
                    "selected opportunity representative",
                ),
                (
                    "G10",
                    "SEMANTIC_COMMAND_COMPATIBILITY",
                    {
                        "expected": expected_commands[expected_direction],
                        "actual": command_token[0],
                    },
                    "fresh upstream existing-GRP trace",
                ),
                (
                    "G11",
                    "PREFIX_CONTINUITY",
                    preinstall_receipt["continuity"]["prefix_to_entry"],
                    prefix_source,
                ),
                (
                    "G12",
                    "CONNECTOR_CONTINUITY",
                    preinstall_receipt["continuity"]["entry_to_connector"],
                    "existing GRP topology",
                ),
                (
                    "G13",
                    "SUFFIX_CONTINUITY",
                    preinstall_receipt["continuity"]["exit_to_suffix"],
                    preinstall_receipt["suffix_provenance"],
                ),
                (
                    "G14",
                    "MANDATORY_CONNECTOR_PRESENCE",
                    connector_present,
                    proposed_digest,
                ),
                (
                    "G15",
                    "MANDATORY_CONNECTOR_ORDERING",
                    [connector_start, connector_end, len(proposed)],
                    "V3 route composer",
                ),
                (
                    "G16",
                    "INCOMPATIBLE_CONNECTOR_BEFORE_SELECTED",
                    incompatible_before,
                    "fresh upstream existing-GRP trace",
                ),
                (
                    "G17",
                    "ROUTE_COMMAND_CONSISTENCY",
                    [name for name, _ in command_tokens],
                    "existing GRP command preservation",
                ),
                (
                    "G18",
                    "SAME_GLOBAL_DESTINATION",
                    self.mission.global_destination_identity,
                    "frozen mission",
                ),
                (
                    "G19",
                    "ROUTE_GENERATION_FRESHNESS",
                    {
                        "candidate_generation_identity": provenance.get(
                            "candidate_generation_identity"
                        ),
                        "source_frame_id": provenance.get(
                            "fresh_plan_source_frame_id"
                        ),
                        "source_observation_id": provenance.get(
                            "fresh_plan_source_observation_id"
                        ),
                        "route_generation_before": route_generation_before,
                    },
                    "V2.8 selected-plan transaction",
                ),
                (
                    "G20",
                    "TRANSACTION_IDENTITY_CONSISTENCY",
                    provenance.get("selected_plan_transaction_identity"),
                    "SelectedPlanTransactionV28",
                ),
                (
                    "G21",
                    "CURRENT_ACTIVATION_OPPORTUNITY",
                    provenance.get("current_activation_opportunity"),
                    "V2.3 preactivation feasibility",
                ),
                (
                    "G22",
                    "SELECTED_ROUTE_INSTALLATION_ELIGIBILITY",
                    "all G01..G21 PASS",
                    "preinstall conjunction",
                ),
            )
        ]
        if preinstall_observer is not None:
            preinstall_observer(preinstall_receipt)

        acknowledged = self.route_owner.install_reconnected_route(
            tuple(proposed),
            global_destination_identity=self.mission.global_destination_identity,
            global_destination_planner_endpoint=(
                self.mission.global_destination_planner_endpoint
            ),
        )
        if acknowledged is not True:
            raise RuntimeError("METHOD_V3_ROUTE_OWNER_INSTALLATION_FAILED")
        owner_receipt = getattr(self.route_owner, "last_installation_receipt", None)
        if not isinstance(owner_receipt, Mapping):
            raise RuntimeError("METHOD_V3_ROUTE_OWNER_RECEIPT_MISSING")
        installed_identity = str(owner_receipt.get("installed_route_identity") or "")
        if not installed_identity or installed_identity != str(
            owner_receipt.get("fresh_route_computed_identity") or ""
        ):
            raise RuntimeError("METHOD_V3_ROUTE_OWNER_IDENTITY_MISMATCH")
        destination_after = str(
            owner_receipt.get("global_destination_identity_after") or ""
        )
        destination_before = self.mission.global_destination_identity
        if destination_after != destination_before:
            raise RuntimeError("METHOD_V3_GLOBAL_DESTINATION_IDENTITY_CHANGED")
        active_identity_after = str(
            getattr(self.route_owner, "active_route_identity", None)
            or installed_identity
        )
        postinstall_connector_present, postinstall_row_count = (
            _postinstall_connector_readback(
                self.route_owner,
                connector_start=connector_start,
                topology_edge=topology_edge,
                command_tokens=command_tokens,
            )
        )
        route_planner = getattr(self.route_owner, "_route_planner", None)
        generation = getattr(route_planner, "online_update_generation", None)
        if generation is not None:
            generation = int(generation)
        planning_effective_k = provenance.get("planning_effective_k")
        if (
            isinstance(planning_effective_k, bool)
            or not isinstance(planning_effective_k, int)
            or planning_effective_k < 1
        ):
            raise RuntimeError("METHOD_V3_PLANNING_EFFECTIVE_K_INVALID")
        world_to_planner_translation = tuple(
            float(value)
            for value in getattr(
                self.route_owner,
                "world_to_planner_translation_xyz_m",
                (0.0, 0.0, 0.0),
            )
        )
        if len(world_to_planner_translation) != 3 or not all(
            math.isfinite(value) for value in world_to_planner_translation
        ):
            raise RuntimeError("METHOD_V3_ROUTE_COORDINATE_CONVERSION_INVALID")
        target_window_catalog = tuple(
            InstalledRouteRowV3(
                original_index=index,
                waypoint_id=_waypoint_id(waypoint),
                xyz_m=_world_to_route_planner_xyz(
                    self.route_owner, _location_xyz(waypoint)
                ),
                xyz_hex=tuple(
                    float(value).hex()
                    for value in _world_to_route_planner_xyz(
                        self.route_owner, _location_xyz(waypoint)
                    )
                ),
                carla_world_xyz_m=_location_xyz(waypoint),
                coordinate_domain="SIMLINGO_ROUTE_PLANNER",
                carla_world_coordinate_domain="CARLA_WORLD",
                road_option=BoundRoadOptionV3(*_option_token(option)),
                road_id=int(waypoint.road_id),
                section_id=int(waypoint.section_id),
                lane_id=int(waypoint.lane_id),
                junction_id=_junction_id(waypoint),
                is_junction=bool(getattr(waypoint, "is_junction", False)),
                topology_role=(
                    "SELECTED_CONNECTOR"
                    if connector_start <= index <= connector_end
                    else "SELECTED_SUFFIX"
                    if index > connector_end
                    else "PREFIX"
                ),
            )
            for index, (waypoint, option) in enumerate(proposed)
        )
        compatible_suffix_indices = tuple(
            range(connector_end + 1, len(proposed))
        )
        return SelectedRouteBindingReceiptV3(
            selected_obligation_identity=obligation.obligation_identity,
            selected_branch_identity=obligation.local_branch_identity,
            selected_junction_identity=f"junction-map-{junction_id}",
            selected_entry_waypoint_id=_waypoint_id(selected_entry),
            selected_entry_road_id=int(selected_entry.road_id),
            selected_entry_lane_id=int(selected_entry.lane_id),
            selected_exit_waypoint_id=_waypoint_id(selected_exit),
            selected_exit_road_id=int(selected_exit.road_id),
            selected_exit_lane_id=int(selected_exit.lane_id),
            selected_connector_waypoint_ids=edge_ids,
            selected_connector_road_option_name=command_token[0],
            selected_connector_road_option_value=command_token[1],
            selected_connector_road_option_sequence=command_tokens,
            authoritative_maneuver_decision_trace_index=first_edge_index,
            authoritative_maneuver_decision_route_index=connector_start,
            internal_connector_lanefollow_present=internal_lanefollow,
            global_destination_identity_before=destination_before,
            global_destination_identity_after=destination_after,
            existing_planner_provenance=str(self.planner_capability.provenance),
            existing_planner_object_identity=(
                type(self.planner_capability.planner_object).__module__
                + "."
                + type(self.planner_capability.planner_object).__qualname__
                + "@"
                + format(id(self.planner_capability.planner_object), "x")
            ),
            existing_planner_call_count=planner_call_count,
            prefix_source=prefix_source,
            prefix_row_count=connector_start,
            connector_row_count=len(connector_rows),
            selected_exit_to_destination_row_count=len(suffix),
            installed_route_row_count=len(proposed),
            mandatory_connector_start_index=connector_start,
            mandatory_connector_end_index=connector_end,
            composed_existing_trace_digest=proposed_digest,
            selected_exit_terminal_lane_match=lane_match,
            mandatory_connector_present=connector_present,
            mandatory_connector_order_valid=connector_order_valid,
            incompatible_connector_before_selected=incompatible_before,
            connector_direction_valid=True,
            route_continuity_valid=continuity_valid,
            route_maximum_gap_m=maximum_gap,
            route_continuity_owner_threshold_m=continuity_threshold,
            duplicate_boundary_row_count_removed=duplicate_count,
            route_commands_preserved=True,
            route_owner_acknowledged=True,
            route_install_attempted=True,
            route_install_accepted=True,
            active_route_owner_before=route_owner_identity,
            active_route_owner_after=route_owner_identity,
            active_route_identity_before=active_identity_before,
            active_route_identity_after=active_identity_after,
            active_route_changed=(
                bool(active_identity_before)
                and active_identity_before != active_identity_after
            ),
            route_transaction_identity=transaction_identity,
            route_installation_event_identity=installation_event_identity,
            route_generation_before=route_generation_before,
            route_generation_after=generation,
            candidate_generation_identity=(
                None
                if provenance.get("candidate_generation_identity") is None
                else str(provenance["candidate_generation_identity"])
            ),
            selected_plan_transaction_identity=(
                None
                if provenance.get("selected_plan_transaction_identity") is None
                else str(provenance["selected_plan_transaction_identity"])
            ),
            query_identity=(
                None
                if provenance.get("query_identity") is None
                else str(provenance["query_identity"])
            ),
            answer_identity=(
                None
                if provenance.get("answer_identity") is None
                else str(provenance["answer_identity"])
            ),
            fresh_plan_source_frame_id=(
                None
                if provenance.get("fresh_plan_source_frame_id") is None
                else str(provenance["fresh_plan_source_frame_id"])
            ),
            fresh_plan_source_observation_id=(
                None
                if provenance.get("fresh_plan_source_observation_id") is None
                else str(provenance["fresh_plan_source_observation_id"])
            ),
            post_install_active_route_row_count=postinstall_row_count,
            post_install_mandatory_connector_present=(
                postinstall_connector_present
            ),
            installed_route_identity=installed_identity,
            next_tick_consumed_route_identity=None,
            planning_effective_k=planning_effective_k,
            target_window_contract_identity=(
                "DriveClarify V3 Connector-Phase Target Ownership Contract R2"
            ),
            target_window_route_catalog=target_window_catalog,
            target_window_route_catalog_digest=target_window_catalog_digest(
                target_window_catalog
            ),
            target_window_connector_terminal_identity=(
                target_window_catalog[connector_end]
            ),
            target_window_compatible_suffix_original_indices=(
                compatible_suffix_indices
            ),
            target_window_catalog_bound=True,
            installed_route_coordinate_domain="SIMLINGO_ROUTE_PLANNER",
            connector_projection_coordinate_domain="SIMLINGO_ROUTE_PLANNER",
            source_topology_coordinate_domain="CARLA_WORLD",
            world_to_route_planner_translation_xyz_m=(
                world_to_planner_translation
            ),
        )


def materialize_route_derived_forward_binding_v3(
    obligation: CandidateLocalNavigationObligation,
    *,
    planning_observation_id: str,
    planning_frame_id: int,
    route_derived_target_points: Any,
) -> CandidateForwardNavigationBinding:
    """Digest, but do not calculate, the target pair produced by LingoAgent.tick."""

    try:
        rows = tuple(route_derived_target_points)
        wire_points = tuple(tuple(float(value) for value in row) for row in rows)
    except (TypeError, ValueError) as error:
        raise RuntimeError("METHOD_V3_ROUTE_DERIVED_TARGET_PAIR_INVALID") from error
    points = canonical_navigation_projection(wire_points)
    return CandidateForwardNavigationBinding(
        candidate_id=obligation.candidate_id,
        interpretation_id=obligation.interpretation_id,
        planning_observation_id=planning_observation_id,
        planning_frame_id=planning_frame_id,
        obligation_identity=obligation.obligation_identity,
        obligation_digest=obligation.obligation_digest,
        branch_digest=obligation.branch_digest,
        target_digest=obligation.target_digest,
        global_destination_identity=obligation.global_destination_identity,
        mission_context_digest=obligation.mission_context_digest,
        target_points_ego_local_xy_m=points,
        projection_digest=navigation_projection_digest(points),
    )
