"""V2.7 execution overlay preserving the byte-frozen V2 commitment core."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping

from driveclarify_method_revision_v2 import ManeuverExecutionState
from driveclarify_method_v2_6 import (
    GenericDownstreamLandingManeuverExecution,
    LaneTopologyIdentity,
    derive_selected_branch_downstream_landing,
)


def _append_compatible_downstream_lane(owner: Any, candidate: Any) -> Any:
    """Extend an owner across a CARLA non-junction seam, if compatible."""

    if candidate.is_junction or candidate.lane_type != "Driving":
        raise RuntimeError("V2_7_DOWNSTREAM_SAMPLED_SUCCESSOR_INVALID")
    direction_dot = sum(
        left * right
        for left, right in zip(
            owner.selected_lane_link_exit.travel_direction_unit_xy,
            candidate.travel_direction_unit_xy,
        )
    )
    if not math.isfinite(direction_dot) or direction_dot <= 0.0:
        raise RuntimeError("V2_7_DOWNSTREAM_SAMPLED_DIRECTION_INCOMPATIBLE")
    if candidate.lane_key in owner.downstream_lane_keys:
        return owner
    return replace(
        owner,
        downstream_lanes=owner.downstream_lanes + (candidate,),
        derivation_owner=(
            "V2_7_IMMEDIATE_BOUNDARY_PLUS_EXISTING_CONNECTOR_SAMPLING_SUCCESSOR"
        ),
    )


def derive_v27_selected_branch_downstream_landing(
    map_object: Any,
    connector: Mapping[str, Any],
    identity: Any,
    contract: Any,
    *,
    alternative_branches: tuple[Any, ...],
) -> Any:
    """Accept an OpenDRIVE seam and its same-direction sampled successor.

    Town maps may insert a centimetre-scale non-junction road between a CARLA
    Junction lane-link and the durable downstream road.  V2.6 binds the exact
    immediate boundary successor.  V2.7 retains it and also binds the successor
    obtained at the connector's already-existing topology sampling resolution;
    no completion threshold or fixed tick count is introduced.
    """

    owner = derive_selected_branch_downstream_landing(
        map_object,
        connector,
        identity,
        contract,
        alternative_branches=alternative_branches,
    )
    try:
        import carla

        sampling_distance_m = float(connector["sampling_distance_m"])
        if not math.isfinite(sampling_distance_m) or sampling_distance_m <= 0.0:
            raise RuntimeError("V2_7_CONNECTOR_SAMPLING_DISTANCE_INVALID")
        junction = None
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
                break
        if junction is None:
            raise RuntimeError("V2_7_CONNECTOR_JUNCTION_UNKNOWN")
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
            for entry, exit_waypoint in junction.get_waypoints(
                carla.LaneType.Driving
            )
            if (int(entry.road_id), int(entry.lane_id)) == expected_entry
            and (int(exit_waypoint.road_id), int(exit_waypoint.lane_id))
            == expected_exit
        )
        if len(matches) != 1:
            raise RuntimeError("V2_7_SELECTED_JUNCTION_PAIR_NOT_UNIQUE")
        sampled = tuple(matches[0][1].next(sampling_distance_m))
        if len(sampled) != 1:
            raise RuntimeError("V2_7_SAMPLED_DOWNSTREAM_SUCCESSOR_NOT_UNIQUE")
        candidate = LaneTopologyIdentity.from_waypoint(sampled[0])
    except (
        AttributeError,
        ImportError,
        IndexError,
        KeyError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError("V2_7_DOWNSTREAM_SEAM_TOPOLOGY_UNKNOWN") from error
    return _append_compatible_downstream_lane(owner, candidate)


class SemanticBoundaryManeuverExecutionV27(
    GenericDownstreamLandingManeuverExecution
):
    """Count the frozen local horizon only after selected-branch entry."""

    schema_version = "driveclarify.method_v2_7.execution.v1"

    def __init__(self) -> None:
        super().__init__()
        self.branch_distance_accounting_armed = False
        self.branch_distance_accounting_entry_frame_id: int | None = None

    def _reanchor_shared_approach(self, observation: Any) -> None:
        if (
            self.state is not ManeuverExecutionState.ACTIVE
            or self.branch_distance_accounting_armed
            or self.contract is None
        ):
            return
        current_position = (float(observation.ego_x_m), float(observation.ego_y_m))
        selected_junction_now = bool(
            observation.is_junction
            and observation.junction_identity
            == self.contract.selected_junction_identity
        )
        # The inherited byte-frozen accumulator sees zero motion on the shared
        # approach. Entry becomes the origin of the candidate-local horizon.
        self._last_position = current_position
        if selected_junction_now:
            self.branch_distance_accounting_armed = True
            self.branch_distance_accounting_entry_frame_id = int(
                observation.frame_id
            )

    def observe_with_downstream_landing(self, observation: Any, evidence: Any):
        self._reanchor_shared_approach(observation)
        return super().observe_with_downstream_landing(observation, evidence)

    def summary(self) -> dict[str, Any]:
        value = super().summary()
        value.update(
            {
                "distance_accounting_owner": "V2_7_SELECTED_JUNCTION_ENTRY",
                "branch_distance_accounting_armed": (
                    self.branch_distance_accounting_armed
                ),
                "branch_distance_accounting_entry_frame_id": (
                    self.branch_distance_accounting_entry_frame_id
                ),
                "shared_approach_distance_charged_to_local_horizon": False,
                "frozen_v2_commitment_predicate_modified": False,
            }
        )
        return value


__all__ = [
    "SemanticBoundaryManeuverExecutionV27",
    "derive_v27_selected_branch_downstream_landing",
]
