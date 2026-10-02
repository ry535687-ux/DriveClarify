"""Generic CARLA-topology owner for a selected branch's downstream landing."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from driveclarify_method_revision_v2 import (
    BranchCommitmentContract,
    LiveManeuverObservation,
    SelectedNavigationIdentity,
    canonical_identity_digest,
)


# This is a graph-boundary probe resolution, not a completion distance or a
# stable-landing threshold. The selected CARLA Junction pair already terminates
# at the lane boundary; the probe asks Waypoint.next() for its adjacent node.
_BOUNDARY_SUCCESSOR_PROBE_M = 0.001


def _identifier(value: Any, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name}_MISSING")
    return normalized


def _finite(value: Any, name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}_NOT_FINITE") from error
    if not math.isfinite(normalized):
        raise ValueError(f"{name}_NOT_FINITE")
    return normalized


def _lane_type_name(value: Any) -> str:
    normalized = str(value).strip().rsplit(".", 1)[-1]
    return _identifier(normalized, "LANE_TYPE")


def _forward_xy(waypoint: Any) -> tuple[float, float]:
    vector = waypoint.transform.get_forward_vector()
    x_value = _finite(vector.x, "FORWARD_X")
    y_value = _finite(vector.y, "FORWARD_Y")
    magnitude = math.hypot(x_value, y_value)
    if magnitude <= 1e-9:
        raise ValueError("WAYPOINT_DIRECTION_INVALID")
    return x_value / magnitude, y_value / magnitude


@dataclass(frozen=True)
class LaneTopologyIdentity:
    waypoint_id: int
    road_id: int
    section_id: int
    lane_id: int
    junction_id: int | None
    is_junction: bool
    lane_type: str
    lane_width_m: float
    xyz_m: tuple[float, float, float]
    travel_direction_unit_xy: tuple[float, float]

    @classmethod
    def from_waypoint(cls, waypoint: Any) -> "LaneTopologyIdentity":
        location = waypoint.transform.location
        is_junction = bool(waypoint.is_junction)
        raw_junction_id = getattr(waypoint, "junction_id", None)
        return cls(
            waypoint_id=int(waypoint.id),
            road_id=int(waypoint.road_id),
            section_id=int(waypoint.section_id),
            lane_id=int(waypoint.lane_id),
            junction_id=(int(raw_junction_id) if is_junction else None),
            is_junction=is_junction,
            lane_type=_lane_type_name(waypoint.lane_type),
            lane_width_m=_finite(waypoint.lane_width, "LANE_WIDTH"),
            xyz_m=(
                _finite(location.x, "WAYPOINT_X"),
                _finite(location.y, "WAYPOINT_Y"),
                _finite(location.z, "WAYPOINT_Z"),
            ),
            travel_direction_unit_xy=_forward_xy(waypoint),
        )

    @property
    def lane_key(self) -> tuple[int, int, int]:
        return self.road_id, self.section_id, self.lane_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlternativeBranchTopologyIdentity:
    candidate_id: str
    obligation_digest: str
    branch_identity: str
    branch_digest: str
    junction_identity: str
    lane_link_entry: LaneTopologyIdentity
    lane_link_exit: LaneTopologyIdentity
    downstream_lanes: tuple[LaneTopologyIdentity, ...]

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "obligation_digest",
            "branch_identity",
            "branch_digest",
            "junction_identity",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name.upper()))
        if not self.downstream_lanes:
            raise ValueError("ALTERNATIVE_DOWNSTREAM_LANES_MISSING")
        if any(item.is_junction for item in self.downstream_lanes):
            raise ValueError("ALTERNATIVE_DOWNSTREAM_MUST_BE_NON_JUNCTION")
        if any(item.lane_type != "Driving" for item in self.downstream_lanes):
            raise ValueError("ALTERNATIVE_DOWNSTREAM_NOT_DRIVING")

    @property
    def identity_digest(self) -> str:
        return canonical_identity_digest(asdict(self))

    @property
    def downstream_lane_keys(self) -> tuple[tuple[int, int, int], ...]:
        return tuple(item.lane_key for item in self.downstream_lanes)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["identity_digest"] = self.identity_digest
        return value


@dataclass(frozen=True)
class SelectedBranchDownstreamLandingIdentity:
    selected_candidate_id: str
    selected_obligation_identity: str
    selected_obligation_digest: str
    selected_branch_identity: str
    selected_branch_digest: str
    selected_navigation_context_identity: str
    selected_junction_identity: str
    selected_lane_link_entry: LaneTopologyIdentity
    selected_lane_link_exit: LaneTopologyIdentity
    downstream_lanes: tuple[LaneTopologyIdentity, ...]
    alternative_branches: tuple[AlternativeBranchTopologyIdentity, ...]
    global_destination_identity: str
    map_name: str
    map_opendrive_sha256: str
    derivation_owner: str = "FROZEN_SELECTED_BRANCH_PLUS_CARLA_WAYPOINT_TOPOLOGY"
    boundary_successor_probe_m: float = _BOUNDARY_SUCCESSOR_PROBE_M

    def __post_init__(self) -> None:
        for name in (
            "selected_candidate_id",
            "selected_obligation_identity",
            "selected_obligation_digest",
            "selected_branch_identity",
            "selected_branch_digest",
            "selected_navigation_context_identity",
            "selected_junction_identity",
            "global_destination_identity",
            "map_name",
            "map_opendrive_sha256",
            "derivation_owner",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name.upper()))
        if self.selected_obligation_identity != self.selected_obligation_digest:
            raise ValueError("DOWNSTREAM_OWNER_OBLIGATION_IDENTITY_MISMATCH")
        if not self.selected_lane_link_entry.is_junction:
            raise ValueError("SELECTED_LANE_LINK_ENTRY_NOT_JUNCTION")
        if not self.selected_lane_link_exit.is_junction:
            raise ValueError("SELECTED_LANE_LINK_EXIT_NOT_JUNCTION")
        if not self.downstream_lanes:
            raise ValueError("DOWNSTREAM_LANDING_LANES_MISSING")
        if any(item.is_junction for item in self.downstream_lanes):
            raise ValueError("DOWNSTREAM_LANDING_MUST_BE_NON_JUNCTION")
        if any(item.lane_type != "Driving" for item in self.downstream_lanes):
            raise ValueError("DOWNSTREAM_LANDING_NOT_DRIVING_LANE")
        if not self.alternative_branches:
            raise ValueError("ALTERNATIVE_BRANCH_TOPOLOGY_OWNERS_MISSING")
        if any(
            item.branch_digest == self.selected_branch_digest
            or item.obligation_digest == self.selected_obligation_digest
            or item.candidate_id == self.selected_candidate_id
            for item in self.alternative_branches
        ):
            raise ValueError("ALTERNATIVE_BRANCH_NOT_SEMANTICALLY_DISTINCT")

    @property
    def identity_digest(self) -> str:
        return canonical_identity_digest(self.to_dict(include_digest=False))

    @property
    def downstream_lane_keys(self) -> tuple[tuple[int, int, int], ...]:
        return tuple(item.lane_key for item in self.downstream_lanes)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value.update(
            {
                "schema_version": "driveclarify.method_v2_6.downstream_landing_identity.v1",
                "derived_before_native_exposure": True,
                "target_outcome_used": False,
                "new_planner_count": 0,
                "stable_landing_fixed_tick_or_distance_dependency": False,
            }
        )
        if include_digest:
            value["identity_digest"] = self.identity_digest
        return value


@dataclass(frozen=True)
class DownstreamLandingEvidence:
    observation_id: str
    frame_id: int
    owner_identity_digest: str
    map_name: str
    map_opendrive_sha256: str
    evidence_status: str
    current_lane: LaneTopologyIdentity | None
    junction_exited: bool
    topology_match: bool
    lane_semantics_compatible: bool
    travel_direction_dot: float | None
    travel_direction_compatible: bool
    stable_landing: bool
    reason_code: str
    alternative_branch_count: int = 0
    matching_alternative_branch_digests: tuple[str, ...] = ()
    alternative_topology_separated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _map_identity(map_object: Any) -> tuple[str, str]:
    map_name = _identifier(getattr(map_object, "name", ""), "CARLA_MAP_NAME")
    opendrive = map_object.to_opendrive()
    if not isinstance(opendrive, str) or not opendrive:
        raise ValueError("CARLA_MAP_OPENDRIVE_UNKNOWN")
    return map_name, hashlib.sha256(opendrive.encode("utf-8")).hexdigest()


def _connector_junction(map_object: Any, connector: Mapping[str, Any], carla: Any) -> Any:
    """Resolve a connector's junction at its exit or entry boundary."""

    for point in (
        connector["polyline_xy_m"][-1],
        connector["polyline_xy_m"][0],
    ):
        seed = map_object.get_waypoint(
            carla.Location(x=float(point[0]), y=float(point[1]), z=0.0),
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        junction = seed.get_junction()
        if junction is not None:
            return junction
    raise RuntimeError("CONNECTOR_JUNCTION_BOUNDARY_UNKNOWN")


def derive_alternative_branch_topology(
    map_object: Any,
    connector: Mapping[str, Any],
    *,
    candidate_id: str,
    obligation_digest: str,
    branch_identity: str,
    branch_digest: str,
) -> AlternativeBranchTopologyIdentity:
    """Pre-bind one non-selected semantic alternative's lane topology."""

    try:
        import carla

        junction = _connector_junction(map_object, connector, carla)
        pairs = tuple(junction.get_waypoints(carla.LaneType.Driving))
        expected_entry = (
            int(connector["entry_road_id"]),
            int(connector["entry_lane_id"]),
        )
        expected_exit = (
            int(connector["exit_road_id"]),
            int(connector["exit_lane_id"]),
        )
        matches = tuple(
            (entry, exit_waypoint)
            for entry, exit_waypoint in pairs
            if (int(entry.road_id), int(entry.lane_id)) == expected_entry
            and (int(exit_waypoint.road_id), int(exit_waypoint.lane_id))
            == expected_exit
        )
        if len(matches) != 1:
            raise RuntimeError("ALTERNATIVE_BRANCH_JUNCTION_PAIR_NOT_UNIQUE")
        entry, exit_waypoint = matches[0]
        successors = tuple(exit_waypoint.next(_BOUNDARY_SUCCESSOR_PROBE_M))
        if len(successors) != 1:
            raise RuntimeError("ALTERNATIVE_BRANCH_SUCCESSOR_NOT_UNIQUE")
        downstream = LaneTopologyIdentity.from_waypoint(successors[0])
        if downstream.is_junction or downstream.lane_type != "Driving":
            raise RuntimeError("ALTERNATIVE_BRANCH_DOWNSTREAM_INVALID")
        entry_identity = LaneTopologyIdentity.from_waypoint(entry)
        exit_identity = LaneTopologyIdentity.from_waypoint(exit_waypoint)
        direction_dot = sum(
            left * right
            for left, right in zip(
                exit_identity.travel_direction_unit_xy,
                downstream.travel_direction_unit_xy,
            )
        )
        if not math.isfinite(direction_dot) or direction_dot <= 0.0:
            raise RuntimeError("ALTERNATIVE_BRANCH_DIRECTION_INCOMPATIBLE")
    except (AttributeError, ImportError, IndexError, KeyError, RuntimeError, TypeError, ValueError) as error:
        raise RuntimeError("ALTERNATIVE_BRANCH_TOPOLOGY_UNKNOWN") from error
    return AlternativeBranchTopologyIdentity(
        candidate_id=candidate_id,
        obligation_digest=obligation_digest,
        branch_identity=branch_identity,
        branch_digest=branch_digest,
        junction_identity=f"junction-map-{int(junction.id)}",
        lane_link_entry=entry_identity,
        lane_link_exit=exit_identity,
        downstream_lanes=(downstream,),
    )


def derive_selected_branch_downstream_landing(
    map_object: Any,
    connector: Mapping[str, Any],
    identity: SelectedNavigationIdentity,
    contract: BranchCommitmentContract,
    *,
    alternative_branches: tuple[AlternativeBranchTopologyIdentity, ...],
) -> SelectedBranchDownstreamLandingIdentity:
    """Bind the unique immediate non-junction successor of a selected lane-link."""

    try:
        import carla

        junction = _connector_junction(map_object, connector, carla)
        pairs = tuple(junction.get_waypoints(carla.LaneType.Driving))
    except (AttributeError, ImportError, IndexError, KeyError, RuntimeError, TypeError, ValueError) as error:
        raise RuntimeError("SELECTED_BRANCH_JUNCTION_PAIR_TOPOLOGY_UNKNOWN") from error

    expected_entry = (
        int(connector["entry_road_id"]),
        int(connector["entry_lane_id"]),
    )
    expected_exit = (
        int(connector["exit_road_id"]),
        int(connector["exit_lane_id"]),
    )
    matches = tuple(
        (entry, exit_waypoint)
        for entry, exit_waypoint in pairs
        if (int(entry.road_id), int(entry.lane_id)) == expected_entry
        and (int(exit_waypoint.road_id), int(exit_waypoint.lane_id)) == expected_exit
    )
    if len(matches) != 1:
        raise RuntimeError("SELECTED_BRANCH_JUNCTION_PAIR_NOT_UNIQUE")
    entry, exit_waypoint = matches[0]
    entry_identity = LaneTopologyIdentity.from_waypoint(entry)
    exit_identity = LaneTopologyIdentity.from_waypoint(exit_waypoint)
    actual_junction_identity = f"junction-map-{int(junction.id)}"
    if actual_junction_identity != contract.selected_junction_identity:
        raise RuntimeError("SELECTED_BRANCH_JUNCTION_IDENTITY_MISMATCH")
    if entry_identity.lane_key[:1] + entry_identity.lane_key[2:] != expected_entry:
        raise RuntimeError("SELECTED_BRANCH_ENTRY_IDENTITY_MISMATCH")
    if exit_identity.lane_key[:1] + exit_identity.lane_key[2:] != expected_exit:
        raise RuntimeError("SELECTED_BRANCH_EXIT_IDENTITY_MISMATCH")

    successors = tuple(exit_waypoint.next(_BOUNDARY_SUCCESSOR_PROBE_M))
    if len(successors) != 1:
        raise RuntimeError("SELECTED_BRANCH_DOWNSTREAM_SUCCESSOR_NOT_UNIQUE")
    downstream = LaneTopologyIdentity.from_waypoint(successors[0])
    if downstream.is_junction:
        raise RuntimeError("SELECTED_BRANCH_FIRST_SUCCESSOR_STILL_JUNCTION")
    if downstream.lane_type != "Driving":
        raise RuntimeError("SELECTED_BRANCH_DOWNSTREAM_NOT_DRIVING")
    direction_dot = sum(
        left * right
        for left, right in zip(
            exit_identity.travel_direction_unit_xy,
            downstream.travel_direction_unit_xy,
        )
    )
    if not math.isfinite(direction_dot) or direction_dot <= 0.0:
        raise RuntimeError("SELECTED_BRANCH_DOWNSTREAM_DIRECTION_INCOMPATIBLE")

    map_name, map_digest = _map_identity(map_object)
    return SelectedBranchDownstreamLandingIdentity(
        selected_candidate_id=identity.candidate_id,
        selected_obligation_identity=identity.obligation_identity,
        selected_obligation_digest=identity.obligation_digest,
        selected_branch_identity=identity.branch_identity,
        selected_branch_digest=identity.branch_digest,
        selected_navigation_context_identity=identity.navigation_context_identity,
        selected_junction_identity=contract.selected_junction_identity,
        selected_lane_link_entry=entry_identity,
        selected_lane_link_exit=exit_identity,
        downstream_lanes=(downstream,),
        alternative_branches=alternative_branches,
        global_destination_identity=identity.global_destination_identity,
        map_name=map_name,
        map_opendrive_sha256=map_digest,
    )


def build_live_downstream_landing_evidence(
    owner: SelectedBranchDownstreamLandingIdentity,
    observation: LiveManeuverObservation,
    map_object: Any,
    waypoint: Any,
) -> DownstreamLandingEvidence:
    """Validate one current CARLA waypoint against the pre-derived owner."""

    try:
        current = LaneTopologyIdentity.from_waypoint(waypoint)
        map_name, map_opendrive_sha256 = _map_identity(map_object)
        if (
            current.road_id != observation.road_id
            or current.lane_id != observation.lane_id
            or current.is_junction != observation.is_junction
        ):
            raise ValueError("LIVE_WAYPOINT_OBSERVATION_IDENTITY_MISMATCH")
        matching_owner_lane = next(
            (item for item in owner.downstream_lanes if item.lane_key == current.lane_key),
            None,
        )
        direction_dot = (
            None
            if matching_owner_lane is None
            else sum(
                left * right
                for left, right in zip(
                    matching_owner_lane.travel_direction_unit_xy,
                    current.travel_direction_unit_xy,
                )
            )
        )
        map_matches = bool(
            map_name == owner.map_name
            and map_opendrive_sha256 == owner.map_opendrive_sha256
        )
        topology_match = bool(
            map_matches and matching_owner_lane is not None and not current.is_junction
        )
        lane_compatible = bool(
            topology_match
            and current.lane_type == matching_owner_lane.lane_type == "Driving"
        )
        direction_compatible = bool(
            direction_dot is not None
            and math.isfinite(direction_dot)
            and direction_dot > 0.0
        )
        stable = bool(
            not observation.is_junction
            and topology_match
            and lane_compatible
            and direction_compatible
        )
        matching_alternatives = tuple(
            sorted(
                branch.branch_digest
                for branch in owner.alternative_branches
                if current.lane_key in branch.downstream_lane_keys
            )
        )
        alternative_separated = bool(
            stable
            and len(owner.alternative_branches) > 0
            and not matching_alternatives
        )
        return DownstreamLandingEvidence(
            observation_id=observation.observation_id,
            frame_id=observation.frame_id,
            owner_identity_digest=owner.identity_digest,
            map_name=map_name,
            map_opendrive_sha256=map_opendrive_sha256,
            evidence_status="AVAILABLE" if map_matches else "INVALID",
            current_lane=current,
            junction_exited=not observation.is_junction,
            topology_match=topology_match,
            lane_semantics_compatible=lane_compatible,
            travel_direction_dot=direction_dot,
            travel_direction_compatible=direction_compatible,
            stable_landing=stable,
            reason_code=(
                "TOPOLOGY_DERIVED_DOWNSTREAM_LANDING_MATCH"
                if stable
                else (
                    "CURRENT_WAYPOINT_NOT_SELECTED_DOWNSTREAM_LANDING"
                    if map_matches
                    else "CARLA_MAP_IDENTITY_CHANGED"
                )
            ),
            alternative_branch_count=len(owner.alternative_branches),
            matching_alternative_branch_digests=matching_alternatives,
            alternative_topology_separated=alternative_separated,
        )
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        return DownstreamLandingEvidence(
            observation_id=observation.observation_id,
            frame_id=observation.frame_id,
            owner_identity_digest=owner.identity_digest,
            map_name=str(getattr(map_object, "name", "UNKNOWN")),
            map_opendrive_sha256="UNKNOWN",
            evidence_status="UNKNOWN",
            current_lane=None,
            junction_exited=False,
            topology_match=False,
            lane_semantics_compatible=False,
            travel_direction_dot=None,
            travel_direction_compatible=False,
            stable_landing=False,
            reason_code=f"DOWNSTREAM_LANDING_EVIDENCE_UNKNOWN:{type(error).__name__}",
            alternative_branch_count=len(owner.alternative_branches),
        )
