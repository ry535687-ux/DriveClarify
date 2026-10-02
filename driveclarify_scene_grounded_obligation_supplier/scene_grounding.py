"""Scene layer: bounded local branches read from the existing map/topology.

Every function here answers "does this already-stated semantic constraint have a
matching bounded local route?".  None of them produces an interpretation, and
none performs cross-junction search or cost minimisation, so this stays a scene
query rather than a planner.  The junction API used is exactly the one the
existing ``RuntimeMapTopologyEnumerator`` already uses.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from driveclarify_candidate_local_navigation_bridge import (
    LocalRouteElement,
    NavigationPoint,
    SceneGroundedLocalBranch,
    canonical_navigation_digest,
)

from .contracts import (
    DEFAULT_LOCAL_HORIZON_MARGIN_M,
    DirectionBranchCandidate,
    STRAIGHT_YAW_TOLERANCE_DEG,
)


# A junction-internal entry lane whose approach heading differs from the ego's
# incoming heading by more than this is a different leg of the junction, not the
# leg the ego is on.  Distinct legs of a real junction differ by ~90 degrees.
EGO_APPROACH_HEADING_TOLERANCE_DEG = 45.0
# An entry lane further than this from the ego's incoming waypoint belongs to a
# different approach of the same junction.
EGO_APPROACH_MAX_DISTANCE_M = 25.0
# How far back the junction-internal entry lane is followed to find the road it
# comes from outside the junction.
EGO_APPROACH_PREDECESSOR_PROBE_M = 2.0


def branch_identity(prefix: str, payload: Mapping[str, Any]) -> str:
    return prefix + "-" + canonical_navigation_digest(dict(payload))[:20]


def _normalize_yaw(value: float) -> float:
    """Fold a yaw onto (-180, 180].

    CARLA reports the same physical heading as e.g. 129.28 on one internal road
    and -230.72 on another; unnormalised these look like two different legs.
    """

    return (float(value) + 180.0) % 360.0 - 180.0


def _heading_delta_deg(first: float, second: float) -> float:
    return abs(_normalize_yaw(float(first) - float(second)))


def _xy(value: Any) -> Optional[tuple[float, float]]:
    try:
        return float(value.x), float(value.y)
    except (AttributeError, TypeError, ValueError):
        try:
            return float(value[0]), float(value[1])
        except (IndexError, TypeError, ValueError):
            return None


def _bounded_segment(
    entry_xy: tuple[float, float], exit_xy: tuple[float, float]
) -> Optional[tuple[NavigationPoint, NavigationPoint, float]]:
    """Two distinct existing points plus the polyline horizon they imply."""

    entry = NavigationPoint(float(entry_xy[0]), float(entry_xy[1]))
    exit_point = NavigationPoint(float(exit_xy[0]), float(exit_xy[1]))
    span = math.hypot(exit_point.x_m - entry.x_m, exit_point.y_m - entry.y_m)
    if span <= 0.0:
        return None
    return entry, exit_point, span + DEFAULT_LOCAL_HORIZON_MARGIN_M


def local_branch(
    *,
    branch_identity_value: str,
    entry_xy: tuple[float, float],
    exit_xy: tuple[float, float],
    exit_planner_endpoint: object,
    road_option: str,
    changes_nominal_route: bool,
) -> Optional[SceneGroundedLocalBranch]:
    """Package two existing scene points as one frozen bounded local branch."""

    bounded = _bounded_segment(entry_xy, exit_xy)
    if bounded is None or exit_planner_endpoint is None:
        return None
    entry, exit_point, horizon = bounded
    points = (entry, exit_point)
    return SceneGroundedLocalBranch(
        local_branch_identity=branch_identity_value,
        availability=True,
        local_target=exit_point,
        local_route_segment=tuple(
            LocalRouteElement(point, road_option) for point in points
        ),
        model_target_points_world=points,
        changes_nominal_route=changes_nominal_route,
        local_horizon_m=horizon,
        branch_exit_planner_endpoint=exit_planner_endpoint,
    )


def classify_junction_exit_direction(
    entry_yaw_deg: float, exit_yaw_deg: float
) -> str:
    """Classify one junction exit using the existing enumerator's convention."""

    delta = (float(exit_yaw_deg) - float(entry_yaw_deg) + 180.0) % 360.0 - 180.0
    if delta >= STRAIGHT_YAW_TOLERANCE_DEG:
        return "RIGHT"
    if delta <= -STRAIGHT_YAW_TOLERANCE_DEG:
        return "LEFT"
    return "STRAIGHT"


def _lane_type_and_location(x_value: float, y_value: float) -> tuple[Any, Any]:
    try:
        import carla  # noqa: PLC0415

        return (
            carla.Location(x=x_value, y=y_value, z=0.0),
            carla.LaneType.Driving,
        )
    except (AttributeError, ImportError):
        return (
            type("Location", (), {"x": x_value, "y": y_value, "z": 0.0})(),
            1,
        )


def _project(map_object: Any, x_value: float, y_value: float) -> Any:
    location, _lane_type = _lane_type_and_location(x_value, y_value)
    try:
        return map_object.get_waypoint(location, project_to_road=True)
    except TypeError:
        return map_object.get_waypoint(location)


def _pose(waypoint: Any) -> Optional[tuple[tuple[float, float], float]]:
    transform = getattr(waypoint, "transform", None)
    point = _xy(getattr(transform, "location", None))
    if point is None:
        return None
    try:
        yaw = float(transform.rotation.yaw)
    except (AttributeError, TypeError, ValueError):
        return None
    return point, yaw


def resolve_ego_junction_entry(
    map_object: Any,
    route_rows: Sequence[tuple[float, float, str]],
    opportunity_index: int,
) -> Optional[Mapping[str, Any]]:
    """Locate the ego's incoming road/lane for one junction opportunity.

    No new ego-localizer is created: this walks the route rows production already
    computed backwards from the opportunity until the projected waypoint is no
    longer inside a junction, and reports that existing waypoint.  Returning
    ``None`` means the incoming leg is unknown, which the caller must fail closed
    on rather than guess.
    """

    if map_object is None or not route_rows:
        return None
    if opportunity_index < 0 or opportunity_index >= len(route_rows):
        return None
    for index in range(int(opportunity_index), -1, -1):
        try:
            waypoint = _project(
                map_object,
                float(route_rows[index][0]),
                float(route_rows[index][1]),
            )
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
        if waypoint is None:
            continue
        if bool(getattr(waypoint, "is_junction", False)):
            continue
        pose = _pose(waypoint)
        if pose is None:
            continue
        point, yaw = pose
        return {
            "status": "RESOLVED_FROM_ROUTE_PROGRESS",
            "route_row_index": int(index),
            "road_id": int(getattr(waypoint, "road_id", -1)),
            "lane_id": int(getattr(waypoint, "lane_id", 0)),
            "xy": [round(point[0], 3), round(point[1], 3)],
            "yaw_deg": round(_normalize_yaw(yaw), 3),
        }
    return None


def junction_lane_links(
    junction: Any, driving_lane_type: Any
) -> tuple[dict[str, Any], ...]:
    """Every raw lane-level connection of one junction, with its identity.

    This is the unchanged ``junction.get_waypoints(Driving)`` scene query.  It
    performs no cross-junction search, no cost minimisation and no planning; it
    only records what each connection is so the semantic layer above can group
    them.
    """

    try:
        pairs = list(junction.get_waypoints(driving_lane_type))
    except (AttributeError, TypeError):
        return ()
    links: list[dict[str, Any]] = []
    for entry, exit_waypoint in pairs:
        entry_pose = _pose(entry)
        exit_pose = _pose(exit_waypoint)
        if entry_pose is None or exit_pose is None:
            continue
        entry_xy, entry_yaw = entry_pose
        exit_xy, exit_yaw = exit_pose
        predecessor_roads: list[tuple[int, int]] = []
        previous_getter = getattr(entry, "previous", None)
        if callable(previous_getter):
            try:
                for previous in previous_getter(EGO_APPROACH_PREDECESSOR_PROBE_M):
                    if bool(getattr(previous, "is_junction", False)):
                        continue
                    predecessor_roads.append(
                        (
                            int(getattr(previous, "road_id", -1)),
                            int(getattr(previous, "lane_id", 0)),
                        )
                    )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                predecessor_roads = []
        links.append(
            {
                "direction": classify_junction_exit_direction(entry_yaw, exit_yaw),
                "entry_road_id": int(getattr(entry, "road_id", -1)),
                "entry_lane_id": int(getattr(entry, "lane_id", 0)),
                "exit_road_id": int(getattr(exit_waypoint, "road_id", -1)),
                "exit_lane_id": int(getattr(exit_waypoint, "lane_id", 0)),
                "entry_xy": entry_xy,
                "exit_xy": exit_xy,
                "entry_yaw_deg": round(_normalize_yaw(entry_yaw), 3),
                "exit_yaw_deg": round(_normalize_yaw(exit_yaw), 3),
                "exit_planner_endpoint": getattr(
                    getattr(exit_waypoint, "transform", None), "location", None
                ),
                "predecessor_roads_outside_junction": sorted(set(predecessor_roads)),
            }
        )
    return tuple(links)


def _entry_leg_key(link: Mapping[str, Any]) -> tuple[float, float, float]:
    """Physical identity of the junction approach leg a link starts on."""

    return (
        round(float(link["entry_xy"][0]), 1),
        round(float(link["entry_xy"][1]), 1),
        round(_normalize_yaw(float(link["entry_yaw_deg"])), 1),
    )


def filter_links_by_ego_entry(
    links: Sequence[Mapping[str, Any]],
    ego_entry: Optional[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Retain only the lane links reachable from the ego's incoming lane.

    Preferred criterion is topological: the junction-internal entry lane's
    predecessor outside the junction is the ego's incoming road/lane.  When no
    link resolves such a predecessor, the fallback is approach-pose
    compatibility.  Either way, if links from two mutually incompatible approach
    legs survive, this fails closed rather than choosing one.
    """

    evidence: dict[str, Any] = {
        "raw_link_count": len(links),
        "criterion": "NONE",
        "retained_count": 0,
        "distinct_entry_legs_raw": len({_entry_leg_key(link) for link in links}),
        "distinct_entry_legs_retained": 0,
        "status": "UNKNOWN",
    }
    if not links:
        evidence["status"] = "NO_LANE_LINKS"
        return (), evidence
    if not ego_entry:
        # Unknown incoming leg is not a licence to use every leg of the junction.
        evidence["status"] = "EGO_ENTRY_UNRESOLVED_FAIL_CLOSED"
        return (), evidence

    ego_road = int(ego_entry.get("road_id", -1))
    ego_lane = int(ego_entry.get("lane_id", 0))
    ego_xy = ego_entry.get("xy") or [0.0, 0.0]
    ego_yaw = float(ego_entry.get("yaw_deg", 0.0))

    topological = [
        link
        for link in links
        if (ego_road, ego_lane) in tuple(link.get("predecessor_roads_outside_junction") or ())
    ]
    if topological:
        retained = topological
        evidence["criterion"] = "TOPOLOGICAL_PREDECESSOR_CONNECTIVITY"
    else:
        retained = [
            link
            for link in links
            if _heading_delta_deg(link["entry_yaw_deg"], ego_yaw)
            <= EGO_APPROACH_HEADING_TOLERANCE_DEG
            and math.hypot(
                float(link["entry_xy"][0]) - float(ego_xy[0]),
                float(link["entry_xy"][1]) - float(ego_xy[1]),
            )
            <= EGO_APPROACH_MAX_DISTANCE_M
        ]
        evidence["criterion"] = "APPROACH_POSE_COMPATIBILITY"

    legs = {_entry_leg_key(link) for link in retained}
    evidence["retained_count"] = len(retained)
    evidence["distinct_entry_legs_retained"] = len(legs)
    if not retained:
        evidence["status"] = "NO_LINK_REACHABLE_FROM_EGO_ENTRY"
        return (), evidence
    if len(legs) > 1:
        # Two incompatible approach legs both matched.  Choosing the closer one
        # would be a guess about where the ego is, so this fails closed.
        evidence["status"] = "EGO_ENTRY_LEG_AMBIGUOUS_FAIL_CLOSED"
        return (), evidence
    evidence["status"] = "EGO_ENTRY_FILTERED"
    return tuple(dict(link) for link in retained), evidence


def group_semantic_branches(
    junction_identity: str, links: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    """Collapse lane-level links into semantic maneuver branches.

    A semantic branch is one (junction, semantic direction, exit road).  Several
    lane-level links that lead onto the same exit road in the same direction are
    the same future branch, so they form one group with one identity.  The group
    identity never depends on how many lane links it contains or on the order
    they were enumerated in.
    """

    grouped: dict[tuple[str, str, int], list[Mapping[str, Any]]] = {}
    for link in links:
        key = (
            str(junction_identity),
            str(link["direction"]),
            int(link["exit_road_id"]),
        )
        grouped.setdefault(key, []).append(link)

    groups: list[dict[str, Any]] = []
    for key in sorted(grouped):
        junction_key, direction, exit_road = key
        members = grouped[key]
        # Representative lane selection happens strictly INSIDE one already
        # unique semantic branch group, so it can only move bounded geometry and
        # can never change the branch identity or the semantic K.
        # Innermost lane first (CARLA numbers the lane adjacent to the centre
        # line +-1), then a total order over the remaining fields so the choice
        # is deterministic and independent of enumeration order.
        representative = min(
            members,
            key=lambda link: (
                abs(int(link["exit_lane_id"])),
                int(link["exit_lane_id"]) < 0,
                abs(int(link["entry_lane_id"])),
                int(link["entry_lane_id"]) < 0,
                round(float(link["exit_xy"][0]), 3),
                round(float(link["exit_xy"][1]), 3),
                round(float(link["entry_xy"][0]), 3),
                round(float(link["entry_xy"][1]), 3),
            ),
        )
        identity = branch_identity(
            "branch-" + direction.casefold(),
            {
                "junction": junction_key,
                "direction": direction,
                "exit_road": exit_road,
            },
        )
        groups.append(
            {
                "branch_identity": identity,
                "junction_identity": junction_key,
                "direction": direction,
                "exit_road_id": exit_road,
                "lane_link_count": len(members),
                "entry_road_ids": sorted({int(row["entry_road_id"]) for row in members}),
                "entry_lane_ids": sorted({int(row["entry_lane_id"]) for row in members}),
                "exit_lane_ids": sorted({int(row["exit_lane_id"]) for row in members}),
                "representative": representative,
            }
        )
    return tuple(groups)


def read_direction_branches(
    map_object: Any,
    route_rows: Sequence[tuple[float, float, str]],
    opportunity_index: int,
) -> tuple[DirectionBranchCandidate, ...]:
    """One semantic maneuver branch per candidate, not one per lane link.

    A scene query over the same ``junction.get_waypoints(Driving)`` API the
    existing ``RuntimeMapTopologyEnumerator`` uses, now read at the right
    granularity: lane-level connections are filtered to the ego's incoming leg
    and then grouped into semantic branches.  It still performs no cross-junction
    search, no cost minimisation and no route planning, and it does not modify
    the existing enumerator.
    """

    evidence = read_direction_branch_evidence(
        map_object, route_rows, opportunity_index
    )
    return tuple(evidence.get("branches") or ())


def read_direction_branch_evidence(
    map_object: Any,
    route_rows: Sequence[tuple[float, float, str]],
    opportunity_index: int,
) -> dict[str, Any]:
    """``read_direction_branches`` plus the durable evidence of how it decided."""

    evidence: dict[str, Any] = {
        "status": "UNKNOWN",
        "junction_identity": None,
        "junction_id": None,
        "ego_entry": None,
        "raw_lane_links": (),
        "raw_lane_link_count": 0,
        "raw_link_count_by_direction": {},
        "entry_filter": {},
        "retained_lane_links": (),
        "semantic_branch_groups": (),
        "semantic_branch_count_by_direction": {},
        "branches": (),
    }
    if map_object is None or not route_rows:
        evidence["status"] = "NO_MAP_OR_ROUTE"
        return evidence
    if opportunity_index < 0 or opportunity_index >= len(route_rows):
        evidence["status"] = "OPPORTUNITY_INDEX_OUT_OF_RANGE"
        return evidence
    x_value = float(route_rows[opportunity_index][0])
    y_value = float(route_rows[opportunity_index][1])
    _location, driving_lane_type = _lane_type_and_location(x_value, y_value)
    waypoint = _project(map_object, x_value, y_value)
    if waypoint is None:
        evidence["status"] = "OPPORTUNITY_WAYPOINT_UNRESOLVED"
        return evidence
    junction_getter = getattr(waypoint, "get_junction", None)
    junction = junction_getter() if callable(junction_getter) else None
    if junction is None:
        evidence["status"] = "NOT_A_JUNCTION"
        return evidence
    raw_junction_id = int(
        getattr(waypoint, "junction_id", getattr(junction, "id", -1))
    )
    junction_identity = "junction-map-{}".format(raw_junction_id)
    evidence["junction_id"] = raw_junction_id
    evidence["junction_identity"] = junction_identity

    links = junction_lane_links(junction, driving_lane_type)
    evidence["raw_lane_links"] = links
    evidence["raw_lane_link_count"] = len(links)
    by_direction: dict[str, int] = {}
    for link in links:
        by_direction[link["direction"]] = by_direction.get(link["direction"], 0) + 1
    evidence["raw_link_count_by_direction"] = by_direction
    if not links:
        evidence["status"] = "NO_LANE_LINKS"
        return evidence

    ego_entry = resolve_ego_junction_entry(map_object, route_rows, opportunity_index)
    evidence["ego_entry"] = ego_entry
    retained, filter_evidence = filter_links_by_ego_entry(links, ego_entry)
    evidence["entry_filter"] = filter_evidence
    evidence["retained_lane_links"] = retained
    if not retained:
        evidence["status"] = filter_evidence.get("status") or "EGO_ENTRY_FILTER_EMPTY"
        return evidence

    groups = group_semantic_branches(junction_identity, retained)
    evidence["semantic_branch_groups"] = groups
    group_counts: dict[str, int] = {}
    for group in groups:
        group_counts[group["direction"]] = group_counts.get(group["direction"], 0) + 1
    evidence["semantic_branch_count_by_direction"] = group_counts

    branches: list[DirectionBranchCandidate] = []
    for group in groups:
        representative = group["representative"]
        branches.append(
            DirectionBranchCandidate(
                branch_identity=str(group["branch_identity"]),
                direction=str(group["direction"]),
                entry_xy=representative["entry_xy"],
                exit_xy=representative["exit_xy"],
                exit_planner_endpoint=representative["exit_planner_endpoint"],
                changes_nominal_route=True,
            )
        )
    evidence["branches"] = tuple(branches)
    evidence["status"] = "SEMANTIC_BRANCHES_RESOLVED"
    return evidence


__all__ = [
    "EGO_APPROACH_HEADING_TOLERANCE_DEG",
    "EGO_APPROACH_MAX_DISTANCE_M",
    "branch_identity",
    "classify_junction_exit_direction",
    "filter_links_by_ego_entry",
    "group_semantic_branches",
    "junction_lane_links",
    "local_branch",
    "read_direction_branch_evidence",
    "read_direction_branches",
    "resolve_ego_junction_entry",
]
