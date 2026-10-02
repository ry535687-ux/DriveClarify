"""Prospective ORD-ASYNC scene contract derived from native Town12 topology.

The passenger's choice is deliberately absent.  This module certifies only that
two reasonable task interpretations are bound to two distinct forward junctions
and that both candidate task paths are continuous native-map paths.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg.contracts import (
    CertifiedCandidateBinding,
    assert_no_true_intent,
    validate_candidate_bindings,
)
from driveclarify_rq2_t_cg_v2_calibration.scenes import CALIBRATION_PARAMETERS


ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_REPORT = (
    ROOT / "reports" / "driveclarify_rq2_t_cg_v2_calibration_and_formal_confirmatory_v1"
)
CALIBRATION_FREEZE = CALIBRATION_REPORT / "RQ2_T_CG_V2_CALIBRATION_FREEZE_RECEIPT.json"
CALIBRATION_FREEZE_DIGEST = (
    "94a933897ded5c6d48df35020e79373db04a362c89d6338b78e10677f556d371"
)
SOURCE_RUNTIME = (
    ROOT
    / "reports/driveclarify_a1_integrated_route_switch_smoke_v1"
    / "candidates/DC-A1-SMOKE-02-RIGHT/runtime_case.json"
)
TOWN12_XODR = Path("/home/buaa/carlaCache/0.9.15/Carla/Maps/Town12/OpenDrive/Town12.xodr")

ORIGINAL_INSTRUCTION = "Take the turn at the junction ahead."
Z1_INTERPRETATION = "Turn at the first eligible upcoming junction."
Z2_INTERPRETATION = (
    "Continue through the first eligible upcoming junction, then turn at the "
    "second eligible upcoming junction."
)

# These slices are prospective subpaths of a previously native-GRP-produced,
# production-route-owner-qualified Town12 route pair.  Both begin at the exact
# same ego row.  z1 turns right at J1; z2 goes straight at J1 and right at J2.
Z1_ROUTE_KEY = "selected_full_route"
Z1_SLICE = (696, 791)
Z2_ROUTE_KEY = "old_full_route"
Z2_SLICE = (660, 801)
J1_ID = 13956
J2_ID = 8474
J1_TURN_CONNECTOR_ROAD_ID = 14005
J1_STRAIGHT_CONNECTOR_ROAD_ID = 13968
J2_TURN_CONNECTOR_ROAD_ID = 8475
J1_INCOMING = (587, 1)
J1_TURN_OUTGOING = (1090, 1)
J1_STRAIGHT_OUTGOING = (586, 1)
J2_TURN_OUTGOING = (68, 1)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _xyz(row: Mapping[str, Any]) -> tuple[float, float, float]:
    return tuple(float.fromhex(value) for value in row["xyz_hex"])


def _coordinate(row: Mapping[str, Any]) -> Mapping[str, float]:
    point = _xyz(row)
    return {"x": point[0], "y": point[1], "z": point[2]}


def _coordinates(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, float]]:
    return [_coordinate(row) for row in rows]


def _length(rows: Sequence[Mapping[str, Any]]) -> float:
    return sum(math.dist(_xyz(left), _xyz(right)) for left, right in zip(rows, rows[1:]))


def _arc_positions(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    values = [0.0]
    for left, right in zip(rows, rows[1:]):
        values.append(values[-1] + math.dist(_xyz(left), _xyz(right)))
    return values


def _source_paths() -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    source = _load(SOURCE_RUNTIME)
    z1 = source[Z1_ROUTE_KEY][Z1_SLICE[0] : Z1_SLICE[1]]
    z2 = source[Z2_ROUTE_KEY][Z2_SLICE[0] : Z2_SLICE[1]]
    if not z1 or not z2 or _xyz(z1[0]) != _xyz(z2[0]):
        raise RuntimeError("ORD_ASYNC_CANDIDATE_COMMON_EGO_STATE_MISSING")
    return z1, z2


def candidate_source_geometry() -> Mapping[str, Any]:
    """Return exact stored coordinates and prospective route landmarks."""
    z1, z2 = _source_paths()
    z1_arc, z2_arc = _arc_positions(z1), _arc_positions(z2)
    common = 0
    for left, right in zip(z1, z2):
        if _xyz(left) != _xyz(right):
            break
        common += 1
    # Source commands differ at the common J1 entry coordinate; physical
    # coordinates split on the immediately following connector samples.
    if common != 39:
        raise RuntimeError("ORD_ASYNC_EXPECTED_COMMON_PREFIX_CHANGED")
    j1_common_index = 38
    j1_straight_exclusive_index = 39
    j2_entry_index = 85
    j2_exit_index = 101
    value = {
        "source_runtime_path": str(SOURCE_RUNTIME.relative_to(ROOT)),
        "source_runtime_sha256": _sha(SOURCE_RUNTIME),
        "source_route_pair_seed": int(_load(SOURCE_RUNTIME)["seed"]),
        "z1_source_key": Z1_ROUTE_KEY,
        "z1_source_slice_half_open": list(Z1_SLICE),
        "z2_source_key": Z2_ROUTE_KEY,
        "z2_source_slice_half_open": list(Z2_SLICE),
        "same_ego_state": _coordinate(z1[0]),
        "shared_prefix_start": {"route_arc_length_m": 0.0, "pose": _coordinate(z1[0])},
        "shared_prefix_end": {
            "route_arc_length_m": z2_arc[j1_common_index - 1],
            "pose": _coordinate(z2[j1_common_index - 1]),
            "source_index_z1": Z1_SLICE[0] + j1_common_index - 1,
            "source_index_z2": Z2_SLICE[0] + j1_common_index - 1,
        },
        "first_candidate_task_divergence": {
            "route_arc_length_m": z2_arc[j1_common_index],
            "pose": _coordinate(z2[j1_common_index]),
            "junction_id": J1_ID,
            "z1_next_connector_road_id": J1_TURN_CONNECTOR_ROAD_ID,
            "z2_next_connector_road_id": J1_STRAIGHT_CONNECTOR_ROAD_ID,
        },
        "commitment_boundary": {
            "route_arc_length_m": z2_arc[j1_straight_exclusive_index],
            "pose": _coordinate(z2[j1_straight_exclusive_index]),
            "source_index_z2": Z2_SLICE[0] + j1_straight_exclusive_index,
            "junction_id": J1_ID,
            "road_id": J1_STRAIGHT_CONNECTOR_ROAD_ID,
            "lane_id": 1,
        },
        "j1_route_arc_length_m": z2_arc[j1_common_index],
        "j2_route_arc_length_m": z2_arc[j2_entry_index],
        "j2_exit_route_arc_length_m": z2_arc[j2_exit_index],
        "z1": {
            "route_length_m": z1_arc[-1],
            "point_count": len(z1),
            "route_coordinates": _coordinates(z1),
            "route_rows_with_commands": [
                {**_coordinate(row), "road_option": row["road_option"]} for row in z1
            ],
        },
        "z2": {
            "route_length_m": z2_arc[-1],
            "point_count": len(z2),
            "route_coordinates": _coordinates(z2),
            "route_rows_with_commands": [
                {**_coordinate(row), "road_option": row["road_option"]} for row in z2
            ],
        },
    }
    value["geometry_digest"] = canonical_sha256(value)
    return value


def _junction_id(waypoint: Any) -> int | None:
    return int(waypoint.junction_id) if waypoint.is_junction else None


def _topology_row(entry: Any, exit_waypoint: Any) -> Mapping[str, Any] | None:
    previous = [row for row in entry.previous(1.0) if not row.is_junction]
    following = [row for row in exit_waypoint.next(1.0) if not row.is_junction]
    if not previous or not following:
        return None
    incoming, outgoing = previous[0], following[0]
    delta = (
        float(outgoing.transform.rotation.yaw)
        - float(incoming.transform.rotation.yaw)
        + 180.0
    ) % 360.0 - 180.0
    return {
        "junction_id": _junction_id(entry),
        "incoming_road_id": int(incoming.road_id),
        "incoming_section_id": int(incoming.section_id),
        "incoming_lane_id": int(incoming.lane_id),
        "connecting_road_id": int(entry.road_id),
        "connecting_lane_id": int(entry.lane_id),
        "successor_road_id": int(outgoing.road_id),
        "successor_section_id": int(outgoing.section_id),
        "successor_lane_id": int(outgoing.lane_id),
        "heading_delta_deg": delta,
        "maneuver_class": "STRAIGHT" if abs(delta) < 35.0 else "TURN",
        "entry_coordinate": {
            "x": float(entry.transform.location.x),
            "y": float(entry.transform.location.y),
            "z": float(entry.transform.location.z),
        },
        "exit_coordinate": {
            "x": float(exit_waypoint.transform.location.x),
            "y": float(exit_waypoint.transform.location.y),
            "z": float(exit_waypoint.transform.location.z),
        },
    }


def _grp_rows(world_map: Any, start: Mapping[str, float], end: Mapping[str, float]) -> list[Mapping[str, Any]]:
    import carla
    from agents.navigation.global_route_planner import GlobalRoutePlanner

    planner = GlobalRoutePlanner(world_map, 1.0)
    trace = planner.trace_route(
        carla.Location(float(start["x"]), float(start["y"]), float(start["z"])),
        carla.Location(float(end["x"]), float(end["y"]), float(end["z"])),
    )
    return [
        {
            "x": float(waypoint.transform.location.x),
            "y": float(waypoint.transform.location.y),
            "z": float(waypoint.transform.location.z),
            "road_option": str(option).split(".")[-1],
            "road_id": int(waypoint.road_id),
            "section_id": int(waypoint.section_id),
            "lane_id": int(waypoint.lane_id),
            "junction_id": _junction_id(waypoint),
        }
        for waypoint, option in trace
    ]


def certify_topology() -> Mapping[str, Any]:
    """Build the offline native-map/GRP topology certificate (Python 3.8)."""
    import carla

    freeze = _load(CALIBRATION_FREEZE)
    if freeze.get("calibration_freeze_digest") != CALIBRATION_FREEZE_DIGEST:
        raise RuntimeError("ORD_ASYNC_CALIBRATION_FREEZE_DIGEST_CHANGED")
    world_map = carla.Map("Town12", TOWN12_XODR.read_text(encoding="utf-8"))
    geometry = candidate_source_geometry()
    z1_rows, z2_rows = _source_paths()

    selected_junction_waypoints = {}
    route_topology = []
    seen = set()
    z2_arc = _arc_positions(z2_rows)
    for index, row in enumerate(z2_rows):
        point = _xyz(row)
        waypoint = world_map.get_waypoint(
            carla.Location(*point), project_to_road=True, lane_type=carla.LaneType.Driving
        )
        if waypoint.is_junction and int(waypoint.junction_id) not in seen:
            seen.add(int(waypoint.junction_id))
            selected_junction_waypoints[int(waypoint.junction_id)] = waypoint
            route_topology.append(
                {
                    "route_order": len(route_topology) + 1,
                    "route_index": index,
                    "route_arc_length_m": z2_arc[index],
                    "junction_id": int(waypoint.junction_id),
                    "road_id": int(waypoint.road_id),
                    "section_id": int(waypoint.section_id),
                    "lane_id": int(waypoint.lane_id),
                    "coordinate": _coordinate(row),
                }
            )
    ordered_ids = [row["junction_id"] for row in route_topology]
    if ordered_ids[:2] != [J1_ID, J2_ID]:
        raise RuntimeError("ORD_ASYNC_JUNCTION_FORWARD_ORDER_CHANGED")

    links = {}
    for junction_id in (J1_ID, J2_ID):
        junction = selected_junction_waypoints[junction_id].get_junction()
        rows = []
        for entry, exit_waypoint in junction.get_waypoints(carla.LaneType.Driving):
            item = _topology_row(entry, exit_waypoint)
            if item is not None:
                rows.append(item)
        links[str(junction_id)] = rows

    j1_relevant = [
        row for row in links[str(J1_ID)]
        if (row["incoming_road_id"], row["incoming_lane_id"]) == J1_INCOMING
    ]
    j2_relevant = [
        row for row in links[str(J2_ID)]
        if (row["incoming_road_id"], row["incoming_lane_id"]) == J1_STRAIGHT_OUTGOING
    ]
    z1_link = next(
        row for row in j1_relevant
        if row["connecting_road_id"] == J1_TURN_CONNECTOR_ROAD_ID
        and (row["successor_road_id"], row["successor_lane_id"]) == J1_TURN_OUTGOING
    )
    z2_j1_link = next(
        row for row in j1_relevant
        if row["connecting_road_id"] == J1_STRAIGHT_CONNECTOR_ROAD_ID
        and (row["successor_road_id"], row["successor_lane_id"]) == J1_STRAIGHT_OUTGOING
    )
    z2_j2_link = next(
        row for row in j2_relevant
        if row["connecting_road_id"] == J2_TURN_CONNECTOR_ROAD_ID
        and (row["successor_road_id"], row["successor_lane_id"]) == J2_TURN_OUTGOING
    )

    z1_grp = _grp_rows(world_map, geometry["same_ego_state"], geometry["z1"]["route_coordinates"][-1])
    z2_grp = _grp_rows(world_map, geometry["same_ego_state"], geometry["z2"]["route_coordinates"][-1])
    z1_grp_junctions = list(dict.fromkeys(row["junction_id"] for row in z1_grp if row["junction_id"] is not None))
    z2_grp_junctions = list(dict.fromkeys(row["junction_id"] for row in z2_grp if row["junction_id"] is not None))
    z1_roads = {row["road_id"] for row in z1_grp}
    z2_roads = {row["road_id"] for row in z2_grp}
    max_gap_z1 = max(math.dist(_xyz(a), _xyz(b)) for a, b in zip(z1_rows, z1_rows[1:]))
    max_gap_z2 = max(math.dist(_xyz(a), _xyz(b)) for a, b in zip(z2_rows, z2_rows[1:]))
    checks = {
        "calibration_freeze_unchanged": freeze["calibration_freeze_digest"] == CALIBRATION_FREEZE_DIGEST,
        "j1_j2_distinct": J1_ID != J2_ID,
        "j1_before_j2_by_route_arc_length": geometry["j1_route_arc_length_m"] < geometry["j2_route_arc_length_m"],
        "z1_turns_at_j1": J1_TURN_CONNECTOR_ROAD_ID in z1_roads and z1_link["maneuver_class"] == "TURN",
        "z2_continues_through_j1": J1_STRAIGHT_CONNECTOR_ROAD_ID in z2_roads and z2_j1_link["maneuver_class"] == "STRAIGHT",
        "z2_reaches_then_turns_at_j2": z2_grp_junctions[:2] == [J1_ID, J2_ID] and J2_TURN_CONNECTOR_ROAD_ID in z2_roads,
        "z1_native_grp_traceable": z1_grp_junctions and z1_grp_junctions[0] == J1_ID,
        "z2_native_grp_traceable": z2_grp_junctions[:2] == [J1_ID, J2_ID],
        # The stored GRP route is nominally 1 m resolution; two native road
        # boundary samples are 2.01066 m apart because the connector endpoint
        # and successor-road start are not both duplicated in the export.
        "z1_continuous_no_jump": max_gap_z1 < 2.1,
        "z2_continuous_no_jump": max_gap_z2 < 2.1,
        "shared_ego_state": _xyz(z1_rows[0]) == _xyz(z2_rows[0]),
        "commitment_reachable_on_z2": geometry["commitment_boundary"]["route_arc_length_m"] < geometry["z2"]["route_length_m"],
        "natural_horizon_route_margin": geometry["z2"]["route_length_m"] - geometry["commitment_boundary"]["route_arc_length_m"] > 50.0,
    }
    value = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_junction_order_receipt.v1",
        "status": "PASS_ORD_ASYNC_NATIVE_TOPOLOGY_CERTIFICATION" if all(checks.values()) else "FAIL_ORD_ASYNC_NATIVE_TOPOLOGY_CERTIFICATION",
        "map": "Town12",
        "town12_opendrive_path": str(TOWN12_XODR),
        "town12_opendrive_sha256": _sha(TOWN12_XODR),
        "ordering_coordinate": "FORWARD_ROUTE_ARC_LENGTH_FROM_COMMON_EGO_STATE",
        "euclidean_nearest_ordering_used": False,
        "eligible_junctions_in_forward_order": route_topology[:2],
        "J1": {"junction_id": J1_ID, "selected_links": {"z1_turn": z1_link, "z2_straight": z2_j1_link}, "all_incoming_lane_links": j1_relevant},
        "J2": {"junction_id": J2_ID, "selected_links": {"z2_turn": z2_j2_link}, "all_incoming_lane_links": j2_relevant},
        "topology_connectivity": {
            "common_incoming_lane": {"road_id": J1_INCOMING[0], "lane_id": J1_INCOMING[1]},
            "z1": [J1_INCOMING, (J1_TURN_CONNECTOR_ROAD_ID, 1), J1_TURN_OUTGOING],
            "z2": [J1_INCOMING, (J1_STRAIGHT_CONNECTOR_ROAD_ID, 1), J1_STRAIGHT_OUTGOING, (J2_TURN_CONNECTOR_ROAD_ID, 1), J2_TURN_OUTGOING],
        },
        "global_route_planner": {
            "sampling_resolution_m": 1.0,
            "z1_exact_trace_coordinates": z1_grp,
            "z2_exact_trace_coordinates": z2_grp,
            "z1_junction_ids_in_order": z1_grp_junctions,
            "z2_junction_ids_in_order": z2_grp_junctions,
        },
        "candidate_geometry": geometry,
        "continuity": {"z1_max_adjacent_gap_m": max_gap_z1, "z2_max_adjacent_gap_m": max_gap_z2},
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
    }
    assert_no_true_intent(value)
    value["receipt_digest"] = canonical_sha256(value)
    return value


def _event(event_id: str, kind: str, owner: str, start: float, end: float, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = {
        "event_id": event_id,
        "event_kind": kind,
        "owner": owner,
        "activation": {
            "coordinate": "route_progress_m",
            "start_inclusive_m": start,
            "end_exclusive_m": end,
        },
        "payload": dict(payload),
        "reads_view": False,
        "reads_outcome": False,
        "reads_passenger_intent": False,
    }
    value["event_digest"] = canonical_sha256(value)
    return value


def build_redesigned_scene(
    *,
    scene_identity: str,
    execution_class: str,
    formal_denominator_eligible: bool,
) -> Mapping[str, Any]:
    """Build one fresh scene identity under the redesigned scientific contract."""
    if execution_class not in (
        "RQ2_T_CG_V2_ENGINEERING_SEAM_ONLY",
        "RQ2_T_CG_FORMAL_V2_CONFIRMATORY",
    ):
        raise ValueError("ORD_ASYNC_REDSESIGN_EXECUTION_CLASS_INVALID")
    geometry = candidate_source_geometry()
    commitment_m = float(geometry["commitment_boundary"]["route_arc_length_m"])
    candidate_bindings = [
        CertifiedCandidateBinding(
            candidate_id=scene_identity + "-C1",
            interpretation_id=scene_identity + "-I1",
            interpretation_text=Z1_INTERPRETATION,
            binding_id=scene_identity + "-B1",
            entity_or_task_role="task:first_eligible_forward_junction_J1_13956",
            obligation_descriptor="turn at first eligible upcoming junction J1=13956",
            binding_kind="CERTIFIED_TASK_TO_NATIVE_TOPOLOGY",
        ).to_dict(),
        CertifiedCandidateBinding(
            candidate_id=scene_identity + "-C2",
            interpretation_id=scene_identity + "-I2",
            interpretation_text=Z2_INTERPRETATION,
            binding_id=scene_identity + "-B2",
            entity_or_task_role="task:pass_J1_then_turn_at_second_eligible_forward_junction_J2_8474",
            obligation_descriptor="continue through J1=13956 and turn at J2=8474",
            binding_kind="CERTIFIED_TASK_TO_NATIVE_TOPOLOGY",
        ).to_dict(),
    ]
    route = {
        "town": "Town12",
        "waypoints": copy.deepcopy(geometry["z2"]["route_coordinates"]),
        "route_length_m": float(geometry["z2"]["route_length_m"]),
        "mechanism_polyline_source": str(SOURCE_RUNTIME.relative_to(ROOT)),
        "source_route_key": Z2_ROUTE_KEY,
        "source_slice_half_open": list(Z2_SLICE),
        "canonical_executable_route_owner": "BENCH2DRIVE_GLOBAL_ROUTE_PLANNER_TRACE_V2",
        "candidate_path_owner": "CERTIFIED_TOWN12_NATIVE_TOPOLOGY_AND_GRP",
        "native_route_materialized": False,
        "exact_prior_full_route_reused": False,
        "prospective_redesign": True,
        "calibration_scene_overlap": False,
        "v2_execution_route_instance_id": scene_identity + "-ROUTE",
    }
    route["route_spec_digest"] = canonical_sha256(route)
    events = [
        _event(
            scene_identity + "-EVENT-01",
            "GROUNDING_REVEAL",
            "CERTIFIED_J1_NATIVE_TOPOLOGY_LOOKAHEAD_OWNER_V2",
            commitment_m - 6.0,
            commitment_m - 5.5,
            {"candidate": "C1", "field": "E2_GROUNDING", "junction_id": J1_ID, "geometry_rule": "SIX_METRES_UPSTREAM_OF_J1_EXCLUSIVE_CONNECTOR"},
        ),
        _event(
            scene_identity + "-EVENT-02",
            "OBLIGATION_REVEAL",
            "CERTIFIED_J2_FORWARD_ROUTE_TOPOLOGY_LOOKAHEAD_OWNER_V2",
            commitment_m - 3.0,
            commitment_m - 2.5,
            {"candidate": "C2", "field": "E5_FUTURE_OBLIGATION", "junction_id": J2_ID, "geometry_rule": "THREE_METRES_UPSTREAM_OF_J1_EXCLUSIVE_CONNECTOR"},
        ),
    ]
    scene = {
        "schema_version": "driveclarify.rq2_t_cg.ord_async_prospective_redesign_scene.v1",
        "formal_scene_id": scene_identity,
        "scene_code": "ORD-ASYNC",
        "scene_family": "ORD",
        "instruction": ORIGINAL_INSTRUCTION,
        "timing_design": "ASYNCHRONOUS_REVEAL",
        "expected_integrity_property": "Distinct native topology events become available at different source times; B1/B2 outcomes are post-trace observations only.",
        "execution_class": execution_class,
        "calibration_only": False,
        "formal_denominator_eligible": bool(formal_denominator_eligible),
        "future_formal_v2_excluded": not bool(formal_denominator_eligible),
        "engineering_scene_promoted": False,
        "selected_calibration_configuration_id": "RQ2TCG-V2-CAL-EARLY-ASYNC-REVEAL-02",
        "selected_calibration_freeze_digest": CALIBRATION_FREEZE_DIGEST,
        "selected_mechanism_parameters": copy.deepcopy(CALIBRATION_PARAMETERS),
        "prospective_redesign_scope": "ORD_ASYNC_ONLY",
        "candidate_certification": {
            "minimum_reasonable_candidates": 2,
            "independent_reasonableness_basis": "Native topology exposes a legal J1 turn and a legal J1-straight/J2-turn task from one common ego state.",
            "passenger_intent_identified": False,
            "runtime_correct_candidate_available": False,
            "production_deployable": False,
        },
        "candidate_bindings": candidate_bindings,
        "candidate_task_paths": {
            "z1": {
                "interpretation": Z1_INTERPRETATION,
                "junction_obligation": {"turn_junction_id": J1_ID},
                "path_geometry_digest": canonical_sha256(geometry["z1"]),
                **copy.deepcopy(geometry["z1"]),
            },
            "z2": {
                "interpretation": Z2_INTERPRETATION,
                "junction_obligation": {"pass_junction_id": J1_ID, "turn_junction_id": J2_ID},
                "path_geometry_digest": canonical_sha256(geometry["z2"]),
                **copy.deepcopy(geometry["z2"]),
            },
            "shared_prefix_start": geometry["shared_prefix_start"],
            "shared_prefix_end": geometry["shared_prefix_end"],
            "first_candidate_task_divergence": geometry["first_candidate_task_divergence"],
        },
        "route": route,
        "actor_or_task_layout": [
            {"layout_id": scene_identity + "-LAYOUT-01", "kind": "CERTIFIED_TASK_JUNCTION", "junction_id": J1_ID, "route_progress_m": geometry["j1_route_arc_length_m"], "lateral_offset_m": 0.0, "formal_denominator_eligible": bool(formal_denominator_eligible)},
            {"layout_id": scene_identity + "-LAYOUT-02", "kind": "CERTIFIED_TASK_JUNCTION", "junction_id": J2_ID, "route_progress_m": geometry["j2_route_arc_length_m"], "lateral_offset_m": 0.0, "formal_denominator_eligible": bool(formal_denominator_eligible)},
        ],
        "events": events,
        "commitment": {
            "coordinate": "route_progress_m",
            "threshold_m": commitment_m,
            "owner": "J1_Z1_TASK_RECOVERABILITY_BOUNDARY_OWNER_V2",
            "pose": geometry["commitment_boundary"]["pose"],
            "road_id": J1_STRAIGHT_CONNECTOR_ROAD_ID,
            "lane_id": 1,
            "junction_id": J1_ID,
            "topological_reason": "The ego has entered J1's straight-only connector 13968; z1's right-turn connector 14005 is no longer reachable without reversing, an illegal maneuver, or discontinuous replanning.",
            "recoverability_proof": {
                "before_boundary": "both J1 connector choices share incoming road 587 lane 1",
                "after_boundary": "connector 13968 has successor road 586, while connector 14005 has successor road 1090; no forward native topology edge joins them inside J1",
                "prohibited_recovery_required": ["reverse", "illegal_lane_or_junction_maneuver", "discontinuous_replan"],
            },
            "reads_view": False,
            "reads_outcome": False,
        },
        "deadline": {
            "definition": "commitment_simulation_time_s - exact frozen DeadlineContract.total_reserved_simulation_s",
            "total_reserved_simulation_s": 1.2,
            "contract_change_authorized": False,
        },
        "horizon": {
            "natural_end": "first source frame at or after commitment plus 1.0 CARLA simulation second",
            "administrative_cap_simulation_s": 60.0,
            "administrative_cap": "60.0 CARLA simulation seconds after first eligible source frame",
            "administrative_cap_scientific_event": False,
            "administrative_cap_basis": "ORD-only infrastructure containment bound allowing one complete native junction-control wait; it is not T-FIXED, TTCmt, the analysis horizon, or an evidence event.",
        },
        "termination": {
            "success": "natural horizon observed with complete paired source trace and native evaluator route completion",
            "censor": "commitment/horizon missing, source identity divergence, certificate breach, or infrastructure termination",
            "zero_imputation_for_censoring": False,
        },
        "formal_seed_values": [],
        "formal_seed_values_generated": 0,
        "formal_episode_count": 0,
        "formal_scientific_exposures": 0,
        "native_materialization_count": 0,
        "online_ask_count": 0,
        "no_ordinal_language_comprehension_claim": True,
    }
    validate_candidate_bindings(scene["candidate_bindings"])
    assert_no_true_intent(scene)
    scene["formal_scene_digest"] = canonical_sha256(scene)
    return scene


__all__ = [
    "CALIBRATION_FREEZE_DIGEST",
    "J1_ID",
    "J2_ID",
    "ORIGINAL_INSTRUCTION",
    "SOURCE_RUNTIME",
    "TOWN12_XODR",
    "Z1_INTERPRETATION",
    "Z2_INTERPRETATION",
    "build_redesigned_scene",
    "candidate_source_geometry",
    "certify_topology",
]
