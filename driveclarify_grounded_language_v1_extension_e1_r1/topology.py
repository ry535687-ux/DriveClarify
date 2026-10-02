"""Runtime route/map topology to executable maneuver opportunity grounding."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from .contracts import canonical_sha256


def _xy(value: Any) -> Optional[tuple[float, float]]:
    try:
        return float(value.x), float(value.y)
    except (AttributeError, TypeError, ValueError):
        try:
            return float(value[0]), float(value[1])
        except (IndexError, TypeError, ValueError):
            return None


def _option(value: Any) -> str:
    return str(getattr(value, "name", value)).upper().rsplit(".", 1)[-1]


def _angle(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


@dataclass(frozen=True)
class ManeuverOpportunity:
    junction_id: str
    branch_id: str
    target_id: str
    maneuver_direction: str
    route_order_index: int
    route_opportunity_index: int
    anchor_xy: tuple[float, float]
    distance_or_progress: float
    availability: bool
    route_reachable: bool
    source_provenance: str
    entry_road_id: int | None = None
    entry_lane_id: int | None = None
    exit_road_id: int | None = None
    exit_lane_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "junction_id": self.junction_id,
            "branch_id": self.branch_id,
            "target_id": self.target_id,
            "maneuver_direction": self.maneuver_direction,
            "route_order_index": self.route_order_index,
            "route_opportunity_index": self.route_opportunity_index,
            "anchor_xy": list(self.anchor_xy),
            "distance_or_progress": self.distance_or_progress,
            "availability": self.availability,
            "route_reachable": self.route_reachable,
            "source_provenance": self.source_provenance,
            "entry_road_id": self.entry_road_id,
            "entry_lane_id": self.entry_lane_id,
            "exit_road_id": self.exit_road_id,
            "exit_lane_id": self.exit_lane_id,
        }


class RuntimeMapTopologyEnumerator:
    """Enumerate legal RIGHT branches from route order plus the live CARLA map.

    Inputs are limited to the already-owned navigation route and HD map.  No
    actor transform, scenario trigger, evaluator annotation, or target label is
    accepted by this API.
    """

    implementation_id = "E1R1_ROUTE_DEQUE_PLUS_LIVE_CARLA_MAP_TOPOLOGY_V1"

    @staticmethod
    def _route_rows(route: Any) -> list[tuple[float, float, str]]:
        rows: list[tuple[float, float, str]] = []
        try:
            iterator = iter(route)
        except TypeError:
            return rows
        for item in iterator:
            try:
                point = _xy(item[0])
                option = _option(item[1])
            except (IndexError, TypeError):
                continue
            if point is not None:
                rows.append((point[0], point[1], option))
        return rows

    @staticmethod
    def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
        return math.hypot(left[0] - right[0], left[1] - right[1])

    @staticmethod
    def _location(map_object: Any, x: float, y: float) -> Any:
        try:
            import carla

            return carla.Location(x=float(x), y=float(y), z=0.0)
        except ImportError:
            return type("Location", (), {"x": float(x), "y": float(y), "z": 0.0})()

    def enumerate(self, route: Any, map_object: Any) -> tuple[ManeuverOpportunity, ...]:
        rows = self._route_rows(route)
        if len(rows) < 2 or map_object is None:
            return ()
        projected = []
        for index, (x_value, y_value, option) in enumerate(rows):
            try:
                waypoint = map_object.get_waypoint(
                    self._location(map_object, x_value, y_value), project_to_road=True
                )
            except TypeError:
                waypoint = map_object.get_waypoint(self._location(map_object, x_value, y_value))
            if waypoint is not None:
                projected.append((index, (x_value, y_value), option, waypoint))
        if not projected:
            return ()

        groups: list[tuple[int, tuple[float, float], Any, Any]] = []
        previous_junction = None
        previous_waypoint = projected[0][3]
        for index, point, _, waypoint in projected:
            is_junction = bool(getattr(waypoint, "is_junction", False))
            junction_identity = getattr(waypoint, "junction_id", None) if is_junction else None
            if is_junction and junction_identity != previous_junction:
                groups.append((index, point, previous_waypoint, waypoint))
                previous_junction = junction_identity
            elif not is_junction:
                previous_junction = None
                previous_waypoint = waypoint

        opportunities = []
        progress = 0.0
        previous_point = rows[0][:2]
        progress_by_index = {0: 0.0}
        for index, row in enumerate(rows[1:], start=1):
            point = row[:2]
            progress += self._distance(previous_point, point)
            progress_by_index[index] = progress
            previous_point = point
        for _, (route_index, point, entry_hint, junction_waypoint) in enumerate(groups):
            junction = junction_waypoint.get_junction()
            if junction is None:
                continue
            entry_yaw = float(entry_hint.transform.rotation.yaw)
            entry_point = _xy(entry_hint.transform.location) or point
            branch_rows = []
            try:
                import carla

                driving_lane_type = carla.LaneType.Driving
            except (AttributeError, ImportError):
                # Lightweight contract tests provide a duck-typed junction.
                driving_lane_type = 1
            for entry, exit_waypoint in junction.get_waypoints(driving_lane_type):
                candidate_entry = _xy(entry.transform.location)
                candidate_exit = _xy(exit_waypoint.transform.location)
                if candidate_entry is None or candidate_exit is None:
                    continue
                entry_distance = self._distance(entry_point, candidate_entry)
                yaw_delta = _angle(float(exit_waypoint.transform.rotation.yaw) - entry_yaw)
                # CARLA/Unreal positive yaw is the right-hand branch for the
                # benchmark approaches selected in the prefreeze map audit.
                if yaw_delta < 35.0:
                    continue
                branch_rows.append((entry_distance, abs(yaw_delta - 90.0), entry, exit_waypoint, candidate_exit))
            if not branch_rows:
                continue
            _, _, entry, exit_waypoint, exit_point = min(branch_rows, key=lambda item: (item[0], item[1]))
            raw_junction = int(getattr(junction_waypoint, "junction_id", getattr(junction, "id", -1)))
            junction_id = "junction-map-{}".format(raw_junction)
            branch_projection = {
                "junction": junction_id,
                "direction": "RIGHT",
                "entry_road": int(getattr(entry, "road_id", -1)),
                "entry_lane": int(getattr(entry, "lane_id", 0)),
                "exit_road": int(getattr(exit_waypoint, "road_id", -1)),
                "exit_lane": int(getattr(exit_waypoint, "lane_id", 0)),
                "anchor": [round(exit_point[0], 3), round(exit_point[1], 3)],
            }
            branch_id = "branch-right-" + canonical_sha256(branch_projection)[:16]
            order = len(opportunities) + 1
            opportunities.append(
                ManeuverOpportunity(
                    junction_id=junction_id,
                    branch_id=branch_id,
                    target_id="target-" + canonical_sha256({"junction": junction_id, "branch": branch_id})[:20],
                    maneuver_direction="RIGHT",
                    route_order_index=order,
                    route_opportunity_index=int(route_index),
                    anchor_xy=(round(exit_point[0], 3), round(exit_point[1], 3)),
                    distance_or_progress=round(progress_by_index.get(route_index, 0.0), 3),
                    availability=True,
                    route_reachable=True,
                    source_provenance="ONLINE_ROUTE_DEQUE_PLUS_LIVE_CARLA_HD_MAP_TOPOLOGY",
                    entry_road_id=int(getattr(entry, "road_id", -1)),
                    entry_lane_id=int(getattr(entry, "lane_id", 0)),
                    exit_road_id=int(getattr(exit_waypoint, "road_id", -1)),
                    exit_lane_id=int(getattr(exit_waypoint, "lane_id", 0)),
                )
            )
        return tuple(opportunities)


__all__ = ["ManeuverOpportunity", "RuntimeMapTopologyEnumerator"]
