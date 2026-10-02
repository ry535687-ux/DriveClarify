"""Static TRAIN-only R9 route and candidate fixture registry."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


EGO_LANE = {
    "road_id": 1,
    "section_id": 0,
    "lane_id": -1,
    "junction_id": None,
    "direction": [1.0, 0.0],
}


def _control(**changes: Any) -> dict[str, Any]:
    row = {
        "actor_id": "traffic-control-1",
        "control_type": "TRAFFIC_LIGHT",
        "state": "RED",
        "lane": deepcopy(EGO_LANE),
        "forward_distance_m": 12.0,
        "lateral_m": 0.0,
        "route_join_proven": True,
        "sweep_intersection": True,
        "negative_reason": "ADJACENT_LANE",
    }
    row.update(changes)
    return row


def _fixture(fixture_id: str, control: dict[str, Any], **changes: Any) -> dict[str, Any]:
    row = {
        "fixture_id": fixture_id,
        "split": "TRAIN",
        "source_frame_id": 410,
        "simulation_time_s": 20.5,
        "ego_lane": deepcopy(EGO_LANE),
        "route_lanes": [deepcopy(EGO_LANE)],
        "forward_horizon_m": 30.0,
        "census_complete": True,
        "controls": [control],
    }
    row.update(changes)
    return row


def route_fixtures() -> list[dict[str, Any]]:
    adjacent = {**EGO_LANE, "lane_id": -2}
    opposing = {**EGO_LANE, "lane_id": 1, "direction": [-1.0, 0.0]}
    crossing = {"road_id": 2, "section_id": 0, "lane_id": -1, "junction_id": 7, "direction": [0.0, 1.0]}
    unrelated = {"road_id": 3, "section_id": 0, "lane_id": -1, "junction_id": 99, "direction": [1.0, 0.0]}
    return [
        _fixture("ROUTE_LOCAL_SAME_LANE_NON_GREEN_POSITIVE", _control()),
        _fixture("ROUTE_LOCAL_COMPLETE_CENSUS_CLEAR_POSITIVE", _control(state="GREEN")),
        _fixture("ADJACENT_LANE", _control(lane=adjacent, lateral_m=3.5, route_join_proven=False, negative_reason="ADJACENT_LANE")),
        _fixture("OPPOSING_LANE", _control(lane=opposing, lateral_m=3.5, route_join_proven=False, negative_reason="OPPOSING_LANE")),
        _fixture("CROSSING_ROAD", _control(lane=crossing, route_join_proven=False, negative_reason="CROSSING_ROAD")),
        _fixture("UNRELATED_JUNCTION", _control(lane=unrelated, route_join_proven=False, negative_reason="UNRELATED_JUNCTION")),
        _fixture("BEHIND_EGO", _control(forward_distance_m=-3.0, sweep_intersection=False, negative_reason="BEHIND_EGO")),
        _fixture("OUTSIDE_HORIZON", _control(forward_distance_m=45.0, sweep_intersection=False, negative_reason="OUTSIDE_HORIZON")),
        _fixture("ROTATED_TRIGGER_NO_INTERSECTION", _control(yaw_deg=45.0, sweep_intersection=False, negative_reason="ROTATED_TRIGGER_NO_INTERSECTION")),
        _fixture("ACTOR_PROXIMITY_WITHOUT_ROUTE_JOIN", _control(route_join_proven=False, actor_proximity_only=True, negative_reason="ACTOR_PROXIMITY_WITHOUT_ROUTE_JOIN")),
        _fixture("INCOMPLETE_OR_STALE_CENSUS", _control(state_source_frame_id=409, missing_required_field=True), census_complete=False),
    ]


CANDIDATE_FIXTURE_IDS = (
    "CERTIFIED_PAIR_POSITIVE",
    "NOT_DISTINCT_SEMANTIC_EQUIVALENCE",
    "SEMANTIC_TRAJECTORY_CROSS_SWAP",
    "DISCONNECTED_OR_WRONG_BRANCH_TOPOLOGY",
    "KINEMATIC_BOUND_VIOLATION",
    "REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY",
)


PROMPT_A = "Move one lane towards the right."
PROMPT_B = "Continue driving on your current lane."
