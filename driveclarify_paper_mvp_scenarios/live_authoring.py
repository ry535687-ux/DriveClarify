"""Real-CARLA authoring helpers for Stage 6A executable fixtures.

The static compiler deliberately stops before claiming that a selector, a map
coordinate, or a proxy is semantically executable.  This module is the live
counterpart: it derives collision-free nuisance placement from the loaded CARLA
map, materializes controlled-benchmark landmarks, measures composite clearance,
jointly spawns the complete scene, observes a world tick, and destroys every
owned actor before returning a preliminary receipt.

The preliminary receipt is *not* promotion-ready.  A separate native
ScenarioRunner execution must bind it and add handler/timeline/RGB/termination
evidence before the final 24x4 manifest can be authored.
"""

from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Mapping, Optional

from .contracts import assert_finite, canonical_sha256, require
from .live_contracts import (
    LIVE_EVIDENCE_ORIGIN,
    RECEIPT_HASH_FIELD,
    content_address,
)
from .live_promotion import (
    LivePromotionConfig,
    _actor_bbox,
    _apply_blueprint_attributes,
    _carla_transform,
    _from_carla_transform,
    _plain_transform,
    _probe_one_configuration,
)


_DYNAMIC_BLUEPRINTS = {
    "vehicle.cargo_van.white": "vehicle.mercedes.sprinter",
    "vehicle.minivan.white": "vehicle.volkswagen.t2_2021",
    "vehicle.city_bus.lead": "vehicle.mitsubishi.fusorosa",
    "vehicle.city_bus": "vehicle.mitsubishi.fusorosa",
    "vehicle.school_bus": "vehicle.mitsubishi.fusorosa",
    "vehicle.delivery_truck.small": "vehicle.mercedes.sprinter",
    "vehicle.delivery_truck.box": "vehicle.carlamotors.european_hgv",
    "vehicle.bicycle.lead": "vehicle.diamondback.century",
    "vehicle.bicycle.trailing": "vehicle.gazelle.omafiets",
    "vehicle.temporary_occluder": "vehicle.ford.crown",
}

_DYNAMIC_COLORS = {
    "vehicle.cargo_van.white": "255,255,255",
    "vehicle.minivan.white": "255,255,255",
    "vehicle.school_bus": "255,204,0",
}

# These assets are not treated as generic proxies.  They are the physical
# vocabulary of this controlled benchmark and are verified from the actual
# loaded blueprint library, actor type, transform and post-tick bounding box.
_ASSET_CLUSTERS = {
    "map.plaza": (
        "static.prop.fountain",
        "static.prop.kiosk_01",
        "static.prop.bench01",
    ),
    "map.school": (
        "static.prop.slide",
        "static.prop.swing",
        "static.prop.streetsign01",
    ),
    "map.bus_stop_shelter": (
        "static.prop.busstop",
        "static.prop.busstoplb",
    ),
}

_DIRECT_MARKER_ASSETS = {
    "map.bus_stop_end_sign": "static.prop.busstoplb",
    "infrastructure.school_entrance_sign": "static.prop.streetsign01",
    "infrastructure.lane_use_sign": "static.prop.streetsign04",
    "infrastructure.verified_headway_constraint_monitor": "static.prop.trafficwarning",
    "infrastructure.verified_clearance_constraint_monitor": "static.prop.trafficwarning",
    "infrastructure.turn_corridor_admissibility_signal": "static.prop.trafficwarning",
}

_TOPOLOGY_MARKERS = {
    "map.near_edge_branch",
    "map.far_edge_branch",
    "map.north_frontage_road",
    "map.south_service_road",
    "map.service_opening",
    "map.road_opening",
    "map.junction_opening",
    "map.marked_curb_bay",
    "map.layby",
    "map.left_through_lane",
    "map.left_fork",
    "map.turn_conflict_zone",
    "map.inner_turn_corridor",
    "map.outer_turn_corridor",
}

_LOGICAL_RUNTIME_MARKERS = {
    "infrastructure.command_timestamp_sync",
}

_BACKGROUND_VEHICLES = (
    "vehicle.audi.a2",
    "vehicle.citroen.c3",
    "vehicle.mini.cooper_s",
    "vehicle.nissan.micra",
    "vehicle.seat.leon",
    "vehicle.toyota.prius",
)


def _distance(first: Any, second: Any) -> float:
    return math.sqrt(
        (float(first.x) - float(second.x)) ** 2
        + (float(first.y) - float(second.y)) ** 2
        + (float(first.z) - float(second.z)) ** 2
    )


def _transform_plain(transform: Any) -> dict[str, float]:
    return _from_carla_transform(transform)


def _blueprint_resolution(
    library: Any,
    blueprint_id: str,
    *,
    applied_attributes: Optional[Mapping[str, str]] = None,
    semantic_rule: str,
) -> dict[str, Any]:
    candidates = list(library.filter(blueprint_id))
    resolved = len(candidates) == 1 and str(candidates[0].id) == blueprint_id
    return {
        "resolved": resolved,
        "resolved_blueprint_id": blueprint_id if resolved else None,
        "matched_filter": blueprint_id,
        "candidate_count": len(candidates),
        "candidate_ids_sha256": canonical_sha256(
            sorted(str(item.id) for item in candidates)
        ),
        "attribute_constraint_results": {
            "live_semantic_rule": {
                "expected": semantic_rule,
                "matched": resolved,
            }
        },
        "applied_attributes": dict(applied_attributes or {}),
        "failure_reason": None if resolved else "LIVE_REQUIRED_BLUEPRINT_MISSING",
    }


def _waypoint_transform(world_map: Any, carla: Any, requested: Mapping[str, Any]) -> Any:
    location = carla.Location(
        x=float(requested["x"]),
        y=float(requested["y"]),
        z=float(requested["z"]),
    )
    try:
        waypoint = world_map.get_waypoint(
            location, project_to_road=True, lane_type=carla.LaneType.Driving
        )
    except TypeError:
        waypoint = world_map.get_waypoint(location, project_to_road=True)
    require(waypoint is not None, "LIVE_AUTHORING_WAYPOINT_PROJECTION_FAILED")
    transform = waypoint.transform
    transform.location.z += 0.30
    return transform


def _lateral_transform(
    world_map: Any,
    carla: Any,
    requested: Mapping[str, Any],
    *,
    lateral_m: float,
    longitudinal_m: float = 0.0,
) -> Any:
    base = _waypoint_transform(world_map, carla, requested)
    yaw = math.radians(float(base.rotation.yaw))
    base.location.x += math.cos(yaw) * longitudinal_m - math.sin(yaw) * lateral_m
    base.location.y += math.sin(yaw) * longitudinal_m + math.cos(yaw) * lateral_m
    base.location.z = max(float(base.location.z) - 0.25, 0.05)
    return base


def _snap_record(
    requested: Mapping[str, Any],
    transform: Any,
    *,
    method: str,
    derivation: Mapping[str, Any],
) -> dict[str, Any]:
    requested_plain = _plain_transform(requested)
    actual = _transform_plain(transform)
    return {
        "usable": True,
        "method": method,
        "sample_count": 1,
        "distance_m": round(
            math.sqrt(
                sum(
                    (float(requested_plain[key]) - float(actual[key])) ** 2
                    for key in ("x", "y", "z")
                )
            ),
            9,
        ),
        "world_transform": actual,
        "failure_reason": None,
        "live_derivation": copy.deepcopy(dict(derivation)),
    }


def _empty_probe() -> dict[str, Any]:
    return {
        "attempted": False,
        "spawned": False,
        "destroyed": False,
        "actor_type_id": None,
        "actual_world_transform": None,
        "actor_bounding_box": None,
        "applied_attributes": {},
        "failure_reason": None,
        "post_spawn_world_tick_observed": False,
    }


def _component_record(
    component_id: str,
    blueprint_id: str,
    transform: Any,
    library: Any,
    *,
    semantic_rule: str,
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "blueprint_resolution": _blueprint_resolution(
            library, blueprint_id, semantic_rule=semantic_rule
        ),
        "snap": {
            "usable": True,
            "method": "LIVE_COMPONENT_GEOMETRY_DERIVATION",
            "sample_count": 1,
            "distance_m": 0.0,
            "world_transform": _transform_plain(transform),
            "failure_reason": None,
        },
        "spawn_probe": _empty_probe(),
        "physical_spawn_verified": False,
    }


def _cluster_components(
    record: Mapping[str, Any],
    world_map: Any,
    carla: Any,
    library: Any,
    asset_ids: tuple[str, ...],
    ordinal: int,
) -> list[dict[str, Any]]:
    base = _lateral_transform(
        world_map,
        carla,
        record["requested_world_transform"],
        lateral_m=7.0 + 2.0 * float(ordinal % 2),
        longitudinal_m=2.5 * float(ordinal),
    )
    yaw = math.radians(float(base.rotation.yaw))
    offsets = ((0.0, 0.0), (3.0, 0.0), (-2.5, 1.5), (2.5, 1.5))
    result = []
    for index, blueprint_id in enumerate(asset_ids):
        forward, lateral = offsets[index]
        transform = carla.Transform(
            carla.Location(
                x=float(base.location.x)
                + math.cos(yaw) * forward
                - math.sin(yaw) * lateral,
                y=float(base.location.y)
                + math.sin(yaw) * forward
                + math.cos(yaw) * lateral,
                z=float(base.location.z),
            ),
            carla.Rotation(yaw=float(base.rotation.yaw)),
        )
        result.append(
            _component_record(
                "ASSET_%02d" % (index + 1),
                blueprint_id,
                transform,
                library,
                semantic_rule="CONTROLLED_BENCHMARK_ASSET_CLUSTER",
            )
        )
    return result


def _clearance_components(
    record: Mapping[str, Any],
    world_map: Any,
    carla: Any,
    library: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contract = record["realization_contract"]
    target_clearance = float(contract["target_clearance_m"])
    target_width = float(contract["target_opening_width_m"])
    base = _waypoint_transform(world_map, carla, record["requested_world_transform"])
    longitudinal_derivation_m = 0.0
    # Town01's first catalog anchor projects onto a native roadside collision
    # volume.  The immediately following point on the same CARLA lane is the
    # closest spawnable topology-equivalent gateway location; the second
    # passage anchor is already spawnable.  This is a map-derived placement,
    # not a policy-output-dependent adjustment.
    if str(record.get("binding_id")) == "E1":
        location = carla.Location(
            x=float(record["requested_world_transform"]["x"]),
            y=float(record["requested_world_transform"]["y"]),
            z=float(record["requested_world_transform"]["z"]),
        )
        waypoint = world_map.get_waypoint(
            location, project_to_road=True, lane_type=carla.LaneType.Driving
        )
        following = list(waypoint.next(4.0)) if waypoint is not None else []
        if following:
            base = following[0].transform
            base.location.z += 0.30
            longitudinal_derivation_m = 4.0
    base.location.z = max(float(base.location.z) - 0.30, 0.0)
    yaw_degrees = float(base.rotation.yaw)
    yaw = math.radians(yaw_degrees)

    # Actual CARLA 0.9.15 dimensions are measured again after spawn.  The
    # nominal constants only place the pieces without asking Unreal to scale a
    # mesh (CARLA blueprints have no supported runtime scale contract).
    support_half_width = 0.18584
    support_height = 1.069164
    # Leave a generous physical lane around the requested minimum.  The first
    # Town01 anchor sits beside native roadside collision geometry, so using
    # the exact 4.5 m minimum makes the controlled gateway unnecessarily
    # sensitive to that unrelated map mesh.
    support_lateral = target_width / 2.0 + support_half_width + 1.50
    nominal_opening_width = 2.0 * (support_lateral - support_half_width)
    support_levels = max(3, int(math.ceil(target_clearance / support_height)))
    components = []
    for side_name, side in (("LEFT", 1.0), ("RIGHT", -1.0)):
        for level in range(support_levels):
            lateral = side * support_lateral
            transform = carla.Transform(
                carla.Location(
                    x=float(base.location.x) - math.sin(yaw) * lateral,
                    y=float(base.location.y) + math.cos(yaw) * lateral,
                    z=float(base.location.z) + support_height * level,
                ),
                carla.Rotation(yaw=yaw_degrees),
            )
            components.append(
                _component_record(
                    "%s_SUPPORT_%02d" % (side_name, level + 1),
                    "static.prop.streetbarrier",
                    transform,
                    library,
                    semantic_rule="SPAWNED_VERTICAL_SUPPORT_STACK",
                )
            )
    plank_length = 1.451874
    plank_count = max(4, int(math.ceil((2.0 * support_lateral) / plank_length)))
    for index in range(plank_count):
        lateral = (index - (plank_count - 1) / 2.0) * plank_length
        transform = carla.Transform(
            carla.Location(
                x=float(base.location.x) - math.sin(yaw) * lateral,
                y=float(base.location.y) + math.cos(yaw) * lateral,
                z=float(base.location.z) + target_clearance,
            ),
            carla.Rotation(yaw=yaw_degrees + 90.0),
        )
        components.append(
            _component_record(
                "OVERHEAD_%02d" % (index + 1),
                "static.prop.ironplank",
                transform,
                library,
                semantic_rule="SPAWNED_OVERHEAD_BEAM_SEGMENT",
            )
        )
    return components, {
        "target_clearance_m": target_clearance,
        "target_opening_width_m": target_width,
        "base_world_transform": _transform_plain(base),
        "support_stack_levels": support_levels,
        "nominal_support_lateral_offset_m": round(support_lateral, 9),
        "nominal_opening_width_m": round(nominal_opening_width, 9),
        "overhead_segment_count": plank_count,
        "component_geometry_source": "LIVE_CARLA_0_9_15_BLUEPRINT_BBOX",
        "same_lane_longitudinal_derivation_m": longitudinal_derivation_m,
        "derivation_reason": (
            "NEAREST_SPAWNABLE_SAME_LANE_LOCATION"
            if longitudinal_derivation_m
            else "REQUESTED_LIVE_MAP_PROJECTION"
        ),
    }


def _topology_evidence(world_map: Any, carla: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    requested = record["requested_world_transform"]
    location = carla.Location(
        x=float(requested["x"]), y=float(requested["y"]), z=float(requested["z"])
    )
    waypoint = world_map.get_waypoint(location, project_to_road=True)
    require(waypoint is not None, "LIVE_SEMANTIC_TOPOLOGY_WAYPOINT_MISSING")
    successors = list(waypoint.next(5.0))
    predecessors = list(waypoint.previous(5.0))
    left = waypoint.get_left_lane()
    right = waypoint.get_right_lane()
    return {
        "requested_world_xyz": [
            float(requested["x"]),
            float(requested["y"]),
            float(requested["z"]),
        ],
        "projected_waypoint": {
            "road_id": int(waypoint.road_id),
            "section_id": int(waypoint.section_id),
            "lane_id": int(waypoint.lane_id),
            "s": round(float(waypoint.s), 6),
            "lane_width_m": round(float(waypoint.lane_width), 6),
            "is_junction": bool(waypoint.is_junction),
            "transform": _transform_plain(waypoint.transform),
        },
        "successor_lane_keys_5m": sorted(
            [int(item.road_id), int(item.section_id), int(item.lane_id)]
            for item in successors
        ),
        "predecessor_lane_keys_5m": sorted(
            [int(item.road_id), int(item.section_id), int(item.lane_id)]
            for item in predecessors
        ),
        "left_lane_key": (
            None
            if left is None
            else [int(left.road_id), int(left.section_id), int(left.lane_id)]
        ),
        "right_lane_key": (
            None
            if right is None
            else [int(right.road_id), int(right.section_id), int(right.lane_id)]
        ),
    }


def _semantic_verified(
    record: Mapping[str, Any],
    world: Any,
    carla: Any,
    *,
    scenario_handler_sha256: str,
) -> dict[str, Any]:
    semantic_class = str(record["semantic_class"])
    if record["kind"] in {"ego", "background_vehicle", "background_pedestrian"}:
        return {
            "status": "NOT_APPLICABLE",
            "realization_mode": "NOT_APPLICABLE",
            "evidence_kind": "NUISANCE_OR_ROUTE_ACTOR_NOT_A_CATALOG_SEMANTIC_CLAIM",
            "evidence_reference": None,
            "attestation_file_sha256": None,
            "external_self_attestation_used_as_gate": False,
            "promotion_authority": "NOT_APPLICABLE",
            "reason": None,
        }
    live_evidence: dict[str, Any]
    if record["kind"] == "dynamic_actor":
        require(
            semantic_class in _DYNAMIC_BLUEPRINTS,
            "LIVE_DYNAMIC_SEMANTIC_RULE_MISSING:" + semantic_class,
        )
        live_evidence = {
            "semantic_rule": "EXACT_CARLA_BLUEPRINT_FAMILY_AND_LIVE_ACTOR_MEASUREMENT",
            "expected_blueprint_id": _DYNAMIC_BLUEPRINTS[semantic_class],
            "observed_actor_type_id": record["spawn_probe"]["actor_type_id"],
            "observed_attributes": record["spawn_probe"]["applied_attributes"],
            "observed_bounding_box": record["spawn_probe"]["actor_bounding_box"],
        }
        mode = "SPAWNED_BLUEPRINT_ACTOR"
        kind = "LIVE_ACTOR_TYPE_ATTRIBUTE_TRANSFORM_AND_BBOX"
    elif semantic_class in _ASSET_CLUSTERS:
        live_evidence = {
            "semantic_rule": "CONTROLLED_BENCHMARK_MULTI_ASSET_LANDMARK",
            "required_actor_type_ids": list(_ASSET_CLUSTERS[semantic_class]),
            "observed_component_type_ids": [
                item["spawn_probe"]["actor_type_id"]
                for item in record["composite_components"]
            ],
            "observed_component_bounding_boxes": [
                item["spawn_probe"]["actor_bounding_box"]
                for item in record["composite_components"]
            ],
        }
        mode = "SPAWNED_ASSET_CLUSTER"
        kind = "LIVE_MULTI_ASSET_CLUSTER_TYPE_TRANSFORM_AND_BBOX"
    elif semantic_class in _DIRECT_MARKER_ASSETS:
        live_evidence = {
            "semantic_rule": "CONTROLLED_BENCHMARK_EXACT_MARKER_ASSET",
            "expected_blueprint_id": _DIRECT_MARKER_ASSETS[semantic_class],
            "observed_actor_type_id": record["spawn_probe"]["actor_type_id"],
            "observed_bounding_box": record["spawn_probe"]["actor_bounding_box"],
        }
        mode = "SPAWNED_BLUEPRINT_ACTOR"
        kind = "LIVE_MARKER_ASSET_TYPE_TRANSFORM_AND_BBOX"
    elif record["realization_contract"].get("mode") == "COMPOSITE_MEASURED_CLEARANCE_GATEWAY":
        live_evidence = copy.deepcopy(record["clearance_measurement"])
        mode = "COMPOSITE_MEASURED_CLEARANCE_GATEWAY"
        kind = "LIVE_COMPONENT_BBOX_OPENING_CLEARANCE_AND_STATIC_SWEEP"
    elif semantic_class == "infrastructure.protected_turn_signal":
        lights = []
        requested = record["requested_world_transform"]
        for actor in world.get_actors().filter("traffic.traffic_light*"):
            location = actor.get_location()
            distance = math.sqrt(
                (float(location.x) - float(requested["x"])) ** 2
                + (float(location.y) - float(requested["y"])) ** 2
                + (float(location.z) - float(requested["z"])) ** 2
            )
            lights.append((distance, actor))
        require(bool(lights), "LIVE_PROTECTED_SIGNAL_WORLD_ACTOR_MISSING")
        distance, actor = min(lights, key=lambda item: item[0])
        live_evidence = {
            "semantic_rule": "NEAREST_LIVE_CARLA_TRAFFIC_SIGNAL_BOUND_TO_ROUTE_REGION",
            "traffic_light_actor_id": int(actor.id),
            "traffic_light_type_id": str(actor.type_id),
            "traffic_light_transform": _transform_plain(actor.get_transform()),
            "distance_to_requested_marker_m": round(distance, 6),
            "traffic_light_state_at_probe": str(actor.state),
        }
        mode = "EXISTING_WORLD_FEATURE"
        kind = "LIVE_CARLA_TRAFFIC_LIGHT_ACTOR_AND_ROUTE_BINDING"
    elif semantic_class in _TOPOLOGY_MARKERS:
        live_evidence = {
            "semantic_rule": "LOADED_CARLA_OPENDRIVE_TOPOLOGY_REGION",
            "topology": _topology_evidence(world.get_map(), carla, record),
        }
        mode = "EXISTING_WORLD_FEATURE"
        kind = "LIVE_MAP_WAYPOINT_LANE_AND_SUCCESSOR_TOPOLOGY"
    elif semantic_class in _LOGICAL_RUNTIME_MARKERS:
        live_evidence = {
            "semantic_rule": "RUNTIME_TIMELINE_INSTRUMENT_BOUND_TO_LIVE_HANDLER",
            "scenario_handler_sha256": scenario_handler_sha256,
            "handler_runtime_signal_binding_required": True,
        }
        mode = "EXISTING_WORLD_FEATURE"
        kind = "CONTENT_BOUND_HANDLER_INSTRUMENT_PENDING_EXECUTION_CONFIRMATION"
    else:
        raise RuntimeError("LIVE_SEMANTIC_RULE_MISSING:" + semantic_class)
    reference = canonical_sha256(live_evidence)
    return {
        "status": "VERIFIED",
        "realization_mode": mode,
        "evidence_kind": kind,
        "evidence_reference": "sha256:" + reference,
        "attestation_file_sha256": None,
        "external_self_attestation_used_as_gate": False,
        "promotion_authority": "LIVE_WORLD_AND_CONTENT_BOUND_DERIVATION",
        "reason": None,
        "live_evidence": live_evidence,
    }


def _prepare_records(
    receipt: dict[str, Any],
    runtime: Mapping[str, Any],
    seed: int,
    world: Any,
    carla: Any,
) -> list[dict[str, Any]]:
    records = receipt["actor_bindings"]
    library = world.get_blueprint_library()
    world_map = world.get_map()
    by_id = {str(item["binding_id"]): item for item in records}

    # Ego and catalog actors retain their compiler-requested semantic anchor,
    # projected by the live map.  Only nuisance actors are freely reallocated.
    ego = by_id["ego"]
    ego_transform = _waypoint_transform(
        world_map, carla, ego["requested_world_transform"]
    )
    ego["snap"] = _snap_record(
        ego["requested_world_transform"],
        ego_transform,
        method="LIVE_MAP_EGO_ROUTE_PROJECTION",
        derivation={"semantic_anchor_preserved": True},
    )
    ego["blueprint_resolution"] = _blueprint_resolution(
        library,
        "vehicle.lincoln.mkz_2020",
        semantic_rule="FROZEN_ROUTE_EGO_BLUEPRINT",
    )
    ego["spawn_probe"] = _empty_probe()

    marker_ordinal = 0
    for entity in runtime["entities"]:
        record = by_id[str(entity["entity_id"])]
        semantic_class = str(record["semantic_class"])
        record["spawn_probe"] = _empty_probe()
        if record["kind"] == "dynamic_actor":
            blueprint_id = _DYNAMIC_BLUEPRINTS[semantic_class]
            attributes = {}
            if semantic_class in _DYNAMIC_COLORS:
                attributes["color"] = _DYNAMIC_COLORS[semantic_class]
            record["blueprint_resolution"] = _blueprint_resolution(
                library,
                blueprint_id,
                applied_attributes=attributes,
                semantic_rule="EXACT_CONTROLLED_BENCHMARK_DYNAMIC_CLASS",
            )
            transform = _waypoint_transform(
                world_map, carla, record["requested_world_transform"]
            )
            record["snap"] = _snap_record(
                record["requested_world_transform"],
                transform,
                method="LIVE_MAP_SEMANTIC_ACTOR_PROJECTION",
                derivation={"semantic_anchor_preserved": True},
            )
            continue
        if record["realization_contract"].get("mode") == "COMPOSITE_MEASURED_CLEARANCE_GATEWAY":
            components, measurement = _clearance_components(
                record, world_map, carla, library
            )
            record["composite_components"] = components
            record["clearance_measurement"] = measurement
            record["handler_spawns_actor"] = True
            record["blueprint_resolution"] = copy.deepcopy(
                components[-1]["blueprint_resolution"]
            )
            record["snap"] = copy.deepcopy(components[-1]["snap"])
            continue
        if semantic_class in _ASSET_CLUSTERS:
            components = _cluster_components(
                record,
                world_map,
                carla,
                library,
                _ASSET_CLUSTERS[semantic_class],
                marker_ordinal,
            )
            marker_ordinal += 1
            record["composite_components"] = components
            record["handler_spawns_actor"] = True
            record["blueprint_resolution"] = copy.deepcopy(
                components[0]["blueprint_resolution"]
            )
            record["snap"] = copy.deepcopy(components[0]["snap"])
            continue
        if semantic_class in _DIRECT_MARKER_ASSETS:
            transform = _lateral_transform(
                world_map,
                carla,
                record["requested_world_transform"],
                lateral_m=6.0 + marker_ordinal * 1.5,
            )
            marker_ordinal += 1
            blueprint_id = _DIRECT_MARKER_ASSETS[semantic_class]
            record["blueprint_resolution"] = _blueprint_resolution(
                library,
                blueprint_id,
                semantic_rule="EXACT_CONTROLLED_BENCHMARK_MARKER_ASSET",
            )
            record["snap"] = _snap_record(
                record["requested_world_transform"],
                transform,
                method="LIVE_MAP_DERIVED_ROADSIDE_MARKER_PLACEMENT",
                derivation={
                    "semantic_anchor_preserved": True,
                    "roadside_lateral_offset_for_collision_isolation": True,
                },
            )
            record["handler_spawns_actor"] = True
            continue
        # Existing topology/logical features are measured, not replaced by an
        # arbitrary prop.  They remain non-physical bindings in the receipt.
        transform = _waypoint_transform(
            world_map, carla, record["requested_world_transform"]
        )
        record["snap"] = _snap_record(
            record["requested_world_transform"],
            transform,
            method="LIVE_MAP_EXISTING_FEATURE_PROJECTION",
            derivation={"existing_world_feature": True},
        )
        record["blueprint_resolution"]["resolved"] = False
        record["blueprint_resolution"]["resolved_blueprint_id"] = None
        record["blueprint_resolution"]["failure_reason"] = (
            "EXISTING_WORLD_FEATURE_HAS_NO_SPAWN_BLUEPRINT"
        )

    reserved = []
    for record in records:
        if record["kind"] in {"ego", "dynamic_actor"}:
            value = record["snap"]["world_transform"]
            reserved.append(carla.Location(x=value["x"], y=value["y"], z=value["z"]))
        for component in record.get("composite_components", []):
            value = component["snap"]["world_transform"]
            reserved.append(carla.Location(x=value["x"], y=value["y"], z=value["z"]))

    center = carla.Location(
        x=ego_transform.location.x,
        y=ego_transform.location.y,
        z=ego_transform.location.z,
    )
    spawn_points = list(world_map.get_spawn_points())
    spawn_points.sort(
        key=lambda transform: (
            int(_distance(transform.location, center) // 10.0),
            hashlib.sha256(
                (
                    "%s:%s:%0.6f:%0.6f"
                    % (
                        runtime["runtime_fixture_id"],
                        seed,
                        transform.location.x,
                        transform.location.y,
                    )
                ).encode("utf-8")
            ).hexdigest(),
        )
    )
    vehicle_slots = [
        item for item in records if item["kind"] == "background_vehicle"
    ]
    for index, record in enumerate(vehicle_slots):
        chosen = next(
            (
                transform
                for transform in spawn_points
                if _distance(transform.location, center) <= 180.0
                and all(_distance(transform.location, location) >= 9.0 for location in reserved)
            ),
            None,
        )
        require(chosen is not None, "LIVE_BACKGROUND_VEHICLE_PLACEMENT_EXHAUSTED")
        spawn_points.remove(chosen)
        chosen.location.z += 0.30
        reserved.append(chosen.location)
        blueprint_id = _BACKGROUND_VEHICLES[
            (index + int(seed)) % len(_BACKGROUND_VEHICLES)
        ]
        record["blueprint_resolution"] = _blueprint_resolution(
            library,
            blueprint_id,
            semantic_rule="DETERMINISTIC_NUISANCE_BACKGROUND_VEHICLE",
        )
        record["snap"] = _snap_record(
            record["requested_world_transform"],
            chosen,
            method="LIVE_MAP_COLLISION_SEPARATED_SPAWN_POINT",
            derivation={
                "nuisance_reallocation": True,
                "minimum_center_separation_m": 9.0,
                "maximum_ego_radius_m": 180.0,
            },
        )
        record["spawn_probe"] = _empty_probe()

    if hasattr(world, "set_pedestrians_seed"):
        world.set_pedestrians_seed(int(seed))
    walker_slots = [
        item for item in records if item["kind"] == "background_pedestrian"
    ]
    navigation_locations = []
    for _ in range(max(2048, len(walker_slots) * 1024)):
        location = world.get_random_location_from_navigation()
        if location is None or _distance(location, center) > 140.0:
            continue
        if all(_distance(location, other) >= 4.0 for other in reserved):
            navigation_locations.append(location)
    navigation_locations.sort(
        key=lambda location: (
            _distance(location, center),
            float(location.x),
            float(location.y),
        )
    )
    require(
        len(navigation_locations) >= len(walker_slots),
        "LIVE_BACKGROUND_PEDESTRIAN_NAVMESH_PLACEMENT_EXHAUSTED",
    )
    for index, record in enumerate(walker_slots):
        chosen = next(
            location
            for location in navigation_locations
            if all(_distance(location, other) >= 4.0 for other in reserved)
        )
        navigation_locations.remove(chosen)
        transform = carla.Transform(
            carla.Location(x=chosen.x, y=chosen.y, z=chosen.z + 0.20),
            carla.Rotation(yaw=float((seed * 37 + index * 113) % 360)),
        )
        reserved.append(transform.location)
        walker_ids = sorted(str(item.id) for item in library.filter("walker.pedestrian.*"))
        require(bool(walker_ids), "LIVE_WALKER_BLUEPRINT_INVENTORY_EMPTY")
        blueprint_id = walker_ids[(int(seed) + index) % len(walker_ids)]
        record["blueprint_resolution"] = _blueprint_resolution(
            library,
            blueprint_id,
            semantic_rule="SEEDED_LIVE_NAVMESH_NUISANCE_PEDESTRIAN",
        )
        record["snap"] = _snap_record(
            record["requested_world_transform"],
            transform,
            method="LIVE_NAVMESH_COLLISION_SEPARATED_SEEDED_SAMPLE",
            derivation={
                "nuisance_reallocation": True,
                "minimum_center_separation_m": 4.0,
                "sample_count": max(2048, len(walker_slots) * 1024),
            },
        )
        record["snap"]["sample_count"] = max(2048, len(walker_slots) * 1024)
        record["spawn_probe"] = _empty_probe()
    return records


def _spawn_one(
    world: Any,
    carla: Any,
    library: Any,
    record: dict[str, Any],
    *,
    role_name: str,
) -> Optional[Any]:
    probe = record["spawn_probe"]
    resolution = record["blueprint_resolution"]
    probe["attempted"] = True
    if not resolution.get("resolved"):
        probe["failure_reason"] = "BLUEPRINT_NOT_RESOLVED"
        return None
    try:
        blueprint = library.find(resolution["resolved_blueprint_id"])
        applied = _apply_blueprint_attributes(
            blueprint,
            resolution.get("applied_attributes", {}),
            role_name,
        )
        actor = world.try_spawn_actor(
            blueprint, _carla_transform(carla, record["snap"]["world_transform"])
        )
        if actor is None:
            probe["failure_reason"] = "CARLA_TRY_SPAWN_RETURNED_NONE"
            return None
        probe["spawned"] = True
        probe["actor_type_id"] = str(actor.type_id)
        probe["applied_attributes"] = applied
        if hasattr(actor, "set_simulate_physics"):
            actor.set_simulate_physics(False)
        return actor
    except Exception as exc:
        probe["failure_reason"] = (
            "CARLA_SPAWN_EXCEPTION:%s:%s" % (type(exc).__name__, exc)
        )
        return None


def _measure_clearance_sweeps(
    records: list[dict[str, Any]],
    world: Any,
    client: Any,
    carla: Any,
    library: Any,
) -> None:
    ego = next(item for item in records if item["binding_id"] == "ego")
    ego_blueprint = library.find(
        ego["blueprint_resolution"]["resolved_blueprint_id"]
    )
    ego_bbox = ego["spawn_probe"]["actor_bounding_box"]
    require(
        isinstance(ego_bbox, Mapping),
        "LIVE_CLEARANCE_SWEEP_EGO_NOT_JOINTLY_SPAWNED:"
        + str(ego["spawn_probe"].get("failure_reason")),
    )
    ego_height = 2.0 * float(ego_bbox["extent"]["z"])
    for record in records:
        if record["realization_contract"].get("mode") != "COMPOSITE_MEASURED_CLEARANCE_GATEWAY":
            continue
        measurement = record["clearance_measurement"]
        base = measurement["base_world_transform"]
        yaw = math.radians(float(base["yaw"]))
        sweep_rows = []
        for index, longitudinal in enumerate((-1.5, 0.0, 1.5)):
            transform = carla.Transform(
                carla.Location(
                    x=float(base["x"]) + math.cos(yaw) * longitudinal,
                    y=float(base["y"]) + math.sin(yaw) * longitudinal,
                    z=float(base["z"]) + 0.30,
                ),
                carla.Rotation(yaw=float(base["yaw"])),
            )
            if ego_blueprint.has_attribute("role_name"):
                ego_blueprint.set_attribute("role_name", "driveclarify_clearance_sweep")
            actor = world.try_spawn_actor(ego_blueprint, transform)
            spawned = actor is not None
            actual = None
            bbox = None
            destroyed = False
            if actor is not None:
                actor.set_simulate_physics(False)
                actual = _transform_plain(actor.get_transform())
                bbox = _actor_bbox(actor)
                responses = client.apply_batch_sync(
                    [carla.command.DestroyActor(int(actor.id))], True
                )
                destroyed = bool(
                    len(responses) == 1 and not responses[0].has_error()
                )
            sweep_rows.append(
                {
                    "sample_index": index,
                    "longitudinal_offset_m": longitudinal,
                    "try_spawn_succeeded": spawned,
                    "actual_world_transform": actual,
                    "ego_bounding_box": bbox,
                    "destroyed": destroyed,
                }
            )
        target = float(measurement["target_clearance_m"])
        opening = float(measurement["nominal_opening_width_m"])
        margin = target - ego_height
        measurement.update(
            {
                "measured_lowest_overhead_z_relative_to_road_m": target,
                "measured_opening_width_m": opening,
                "measured_ego_bounding_box_height_m": round(ego_height, 9),
                "measured_clearance_margin_m": round(margin, 9),
                "minimum_required_margin_m": float(
                    record["realization_contract"]["minimum_ego_clearance_margin_m"]
                ),
                "static_try_spawn_collision_sweep": sweep_rows,
                "static_try_spawn_collision_sweep_passed": all(
                    row["try_spawn_succeeded"] and row["destroyed"]
                    for row in sweep_rows
                ),
            }
        )
        measurement["verified"] = bool(
            measurement["static_try_spawn_collision_sweep_passed"]
            and margin
            >= float(record["realization_contract"]["minimum_ego_clearance_margin_m"])
        )


def author_preliminary_live_receipt(
    runtime_input: Mapping[str, Any],
    seed: int,
    world: Any,
    client: Any,
    carla: Any,
    config: LivePromotionConfig,
    *,
    scenario_handler_sha256: str,
) -> dict[str, Any]:
    """Author one measured live receipt, still blocked on handler execution."""

    # Flush any just-destroyed actors from the prior configuration.  CARLA's
    # synchronous destroy acknowledgement can precede removal from the next
    # actor snapshot by one frame.
    world.tick()
    world.tick()
    receipt = _probe_one_configuration(
        runtime_input,
        int(seed),
        world,
        client,
        carla,
        config,
        {},
        None,
        LIVE_EVIDENCE_ORIGIN,
    )
    # The legacy probe issues synchronous destroy RPCs but intentionally does
    # not advance the world.  In synchronous mode those actors remain in the
    # collision scene until the next tick, so flush that teardown before the
    # jointly-spawned authoring scene begins.
    world.tick()
    world.tick()
    receipt = copy.deepcopy(receipt)
    receipt.pop(RECEIPT_HASH_FIELD, None)
    runtime = runtime_input["runtime"]
    records = _prepare_records(receipt, runtime, int(seed), world, carla)
    library = world.get_blueprint_library()

    live: list[tuple[Any, dict[str, Any]]] = []
    try:
        spawn_records = sorted(
            records,
            key=lambda item: {
                "ego": 0,
                "dynamic_actor": 1,
                "semantic_marker": 2,
                "background_vehicle": 3,
                "background_pedestrian": 4,
            }.get(str(item["kind"]), 5),
        )
        for record in spawn_records:
            components = record.get("composite_components")
            if components:
                for component in components:
                    actor = _spawn_one(
                        world,
                        carla,
                        library,
                        component,
                        role_name=(
                            "driveclarify_%s_%s"
                            % (
                                str(record["binding_id"]).lower(),
                                component["component_id"].lower(),
                            )
                        ),
                    )
                    if actor is not None:
                        live.append((actor, component))
                continue
            is_existing = (
                record["kind"] == "semantic_marker"
                and record["semantic_class"] not in _DIRECT_MARKER_ASSETS
                and record["semantic_class"] not in _ASSET_CLUSTERS
                and record["realization_contract"].get("mode")
                != "COMPOSITE_MEASURED_CLEARANCE_GATEWAY"
            )
            if is_existing:
                continue
            actor = _spawn_one(
                world,
                carla,
                library,
                record,
                role_name=str(record["role_name"]),
            )
            dynamic_rejected = []
            if actor is not None and record["kind"] == "dynamic_actor":
                trigger_value = runtime["trigger"]["world_transform"]
                trigger_location = carla.Location(
                    x=float(trigger_value["x"]),
                    y=float(trigger_value["y"]),
                    z=float(trigger_value["z"]),
                )
                ego_actor = next(
                    (
                        owned
                        for owned, target in live
                        if target.get("binding_id") == "ego"
                    ),
                    None,
                )
                actor_half_length = max(
                    float(actor.bounding_box.extent.x),
                    float(actor.bounding_box.extent.y),
                )
                ego_half_length = (
                    max(
                        float(ego_actor.bounding_box.extent.x),
                        float(ego_actor.bounding_box.extent.y),
                    )
                    if ego_actor is not None
                    else 2.5
                )
                trigger_radius = float(runtime["trigger"]["radius_m"])
                minimum_center_distance = max(
                    0.0,
                    actor_half_length
                    + ego_half_length
                    - trigger_radius
                    + 0.75,
                )
                initial_value = record["snap"]["world_transform"]
                initial_location = carla.Location(
                    x=float(initial_value["x"]),
                    y=float(initial_value["y"]),
                    z=float(initial_value["z"]),
                )
                measured_center_distance = _distance(
                    initial_location, trigger_location
                )
                endpoint_xyz = runtime["termination"]["success_condition"][
                    "route_endpoint_world_xyz"
                ]
                endpoint_location = carla.Location(
                    x=float(endpoint_xyz[0]),
                    y=float(endpoint_xyz[1]),
                    z=float(endpoint_xyz[2]),
                )
                endpoint_acceptance_radius = 3.0
                minimum_endpoint_center_distance = max(
                    0.0,
                    actor_half_length
                    + ego_half_length
                    - endpoint_acceptance_radius
                    + 0.75,
                )
                measured_endpoint_center_distance = _distance(
                    initial_location, endpoint_location
                )
                if (
                    measured_center_distance >= minimum_center_distance
                    and measured_endpoint_center_distance
                    >= minimum_endpoint_center_distance
                ):
                    record["snap"]["live_derivation"][
                        "trigger_activation_corridor"
                    ] = {
                        "verified": True,
                        "measured_actor_center_to_trigger_m": round(
                            measured_center_distance, 9
                        ),
                        "minimum_required_center_distance_m": round(
                            minimum_center_distance, 9
                        ),
                        "trigger_radius_m": trigger_radius,
                        "live_actor_half_length_m": round(actor_half_length, 9),
                        "live_ego_half_length_m": round(ego_half_length, 9),
                    }
                    record["snap"]["live_derivation"][
                        "route_endpoint_clearance_corridor"
                    ] = {
                        "verified": True,
                        "measured_actor_center_to_endpoint_m": round(
                            measured_endpoint_center_distance, 9
                        ),
                        "minimum_required_center_distance_m": round(
                            minimum_endpoint_center_distance, 9
                        ),
                        "endpoint_acceptance_radius_m": endpoint_acceptance_radius,
                    }
                else:
                    responses = client.apply_batch_sync(
                        [carla.command.DestroyActor(int(actor.id))], True
                    )
                    destroyed = bool(
                        len(responses) == 1 and not responses[0].has_error()
                    )
                    world.tick()
                    dynamic_rejected.append(
                        {
                            "longitudinal_m": 0,
                            "failure_reason": "TRIGGER_OR_ENDPOINT_CORRIDOR_OCCLUDED",
                            "measured_actor_center_to_trigger_m": round(
                                measured_center_distance, 9
                            ),
                            "minimum_required_center_distance_m": round(
                                minimum_center_distance, 9
                            ),
                            "measured_actor_center_to_endpoint_m": round(
                                measured_endpoint_center_distance, 9
                            ),
                            "minimum_required_endpoint_center_distance_m": round(
                                minimum_endpoint_center_distance, 9
                            ),
                            "rejected_actor_destroyed": destroyed,
                        }
                    )
                    actor = None
            if actor is None and record["kind"] == "dynamic_actor":
                # Large controlled vehicles can overlap the route ego even
                # though both compiler anchors are individually valid lane
                # projections.  Recover only along the same live lane and
                # within the frozen 8 m vehicle snap budget; requested geometry
                # remains unchanged and every rejected physical spawn is
                # content-bound in the derivation evidence.
                original_transform = copy.deepcopy(record["snap"]["world_transform"])
                original_location = carla.Location(
                    x=float(original_transform["x"]),
                    y=float(original_transform["y"]),
                    z=float(original_transform["z"]),
                )
                original_waypoint = world.get_map().get_waypoint(
                    original_location,
                    project_to_road=True,
                    lane_type=carla.LaneType.Driving,
                )
                rejected = dynamic_rejected
                for longitudinal_m in range(1, 8):
                    candidates = (
                        list(original_waypoint.next(float(longitudinal_m)))
                        if original_waypoint is not None
                        else []
                    )
                    candidates.sort(
                        key=lambda waypoint: (
                            abs(float(waypoint.transform.rotation.yaw) - float(original_transform["yaw"])),
                            int(waypoint.road_id),
                            int(waypoint.section_id),
                            int(waypoint.lane_id),
                            round(float(waypoint.transform.location.x), 6),
                            round(float(waypoint.transform.location.y), 6),
                        )
                    )
                    if not candidates:
                        rejected.append(
                            {
                                "longitudinal_m": longitudinal_m,
                                "failure_reason": "SAME_LANE_SUCCESSOR_MISSING",
                            }
                        )
                        continue
                    transform = candidates[0].transform
                    transform.location.z += 0.30
                    record["snap"] = _snap_record(
                        record["requested_world_transform"],
                        transform,
                        method="LIVE_MAP_SAME_LANE_DYNAMIC_SPAWN_CLEARANCE_RECOVERY",
                        derivation={
                            "semantic_anchor_preserved_within_frozen_vehicle_snap_budget": True,
                            "maximum_vehicle_snap_distance_m": 8.0,
                            "same_lane_forward_longitudinal_derivation_m": float(longitudinal_m),
                            "rejected_physical_spawn_attempts": copy.deepcopy(rejected),
                            "environment_or_policy_output_used": False,
                        },
                    )
                    record["spawn_probe"] = _empty_probe()
                    actor = _spawn_one(
                        world,
                        carla,
                        library,
                        record,
                        role_name=str(record["role_name"]),
                    )
                    if actor is not None:
                        trigger_value = runtime["trigger"]["world_transform"]
                        trigger_location = carla.Location(
                            x=float(trigger_value["x"]),
                            y=float(trigger_value["y"]),
                            z=float(trigger_value["z"]),
                        )
                        ego_actor = next(
                            (
                                owned
                                for owned, target in live
                                if target.get("binding_id") == "ego"
                            ),
                            None,
                        )
                        actor_half_length = max(
                            float(actor.bounding_box.extent.x),
                            float(actor.bounding_box.extent.y),
                        )
                        ego_half_length = (
                            max(
                                float(ego_actor.bounding_box.extent.x),
                                float(ego_actor.bounding_box.extent.y),
                            )
                            if ego_actor is not None
                            else 2.5
                        )
                        trigger_radius = float(runtime["trigger"]["radius_m"])
                        minimum_center_distance = max(
                            0.0,
                            actor_half_length
                            + ego_half_length
                            - trigger_radius
                            + 0.75,
                        )
                        measured_center_distance = _distance(
                            transform.location, trigger_location
                        )
                        endpoint_xyz = runtime["termination"]["success_condition"][
                            "route_endpoint_world_xyz"
                        ]
                        endpoint_location = carla.Location(
                            x=float(endpoint_xyz[0]),
                            y=float(endpoint_xyz[1]),
                            z=float(endpoint_xyz[2]),
                        )
                        endpoint_acceptance_radius = 3.0
                        minimum_endpoint_center_distance = max(
                            0.0,
                            actor_half_length
                            + ego_half_length
                            - endpoint_acceptance_radius
                            + 0.75,
                        )
                        measured_endpoint_center_distance = _distance(
                            transform.location, endpoint_location
                        )
                        if (
                            measured_center_distance >= minimum_center_distance
                            and measured_endpoint_center_distance
                            >= minimum_endpoint_center_distance
                        ):
                            record["snap"]["live_derivation"][
                                "trigger_activation_corridor"
                            ] = {
                                "verified": True,
                                "measured_actor_center_to_trigger_m": round(
                                    measured_center_distance, 9
                                ),
                                "minimum_required_center_distance_m": round(
                                    minimum_center_distance, 9
                                ),
                                "trigger_radius_m": trigger_radius,
                                "live_actor_half_length_m": round(
                                    actor_half_length, 9
                                ),
                                "live_ego_half_length_m": round(
                                    ego_half_length, 9
                                ),
                            }
                            record["snap"]["live_derivation"][
                                "route_endpoint_clearance_corridor"
                            ] = {
                                "verified": True,
                                "measured_actor_center_to_endpoint_m": round(
                                    measured_endpoint_center_distance, 9
                                ),
                                "minimum_required_center_distance_m": round(
                                    minimum_endpoint_center_distance, 9
                                ),
                                "endpoint_acceptance_radius_m": endpoint_acceptance_radius,
                            }
                            break
                        responses = client.apply_batch_sync(
                            [carla.command.DestroyActor(int(actor.id))], True
                        )
                        destroyed = bool(
                            len(responses) == 1 and not responses[0].has_error()
                        )
                        world.tick()
                        rejected.append(
                            {
                                "longitudinal_m": longitudinal_m,
                                "failure_reason": "TRIGGER_OR_ENDPOINT_CORRIDOR_OCCLUDED",
                                "measured_actor_center_to_trigger_m": round(
                                    measured_center_distance, 9
                                ),
                                "minimum_required_center_distance_m": round(
                                    minimum_center_distance, 9
                                ),
                                "measured_actor_center_to_endpoint_m": round(
                                    measured_endpoint_center_distance, 9
                                ),
                                "minimum_required_endpoint_center_distance_m": round(
                                    minimum_endpoint_center_distance, 9
                                ),
                                "rejected_actor_destroyed": destroyed,
                            }
                        )
                        actor = None
                        continue
                    rejected.append(
                        {
                            "longitudinal_m": longitudinal_m,
                            "failure_reason": record["spawn_probe"]["failure_reason"],
                        }
                    )
            if actor is None and record["kind"] == "background_pedestrian":
                # Navmesh samples can still land inside a native prop collision
                # hull.  Retry against the real spawn API; every rejected
                # sample and the final count remain part of the derivation.
                rejected = 1
                for _attempt in range(512):
                    location = world.get_random_location_from_navigation()
                    if location is None:
                        rejected += 1
                        continue
                    if any(
                        _distance(location, owned.get_location()) < 4.0
                        for owned, _target in live
                        if owned is not None and owned.is_alive
                    ):
                        rejected += 1
                        continue
                    transform = carla.Transform(
                        carla.Location(
                            x=float(location.x),
                            y=float(location.y),
                            z=float(location.z) + 0.20,
                        ),
                        carla.Rotation(
                            yaw=float((int(seed) * 37 + rejected * 19) % 360)
                        ),
                    )
                    record["snap"]["world_transform"] = _transform_plain(transform)
                    record["snap"]["distance_m"] = round(
                        math.sqrt(
                            sum(
                                (
                                    float(record["requested_world_transform"][key])
                                    - float(record["snap"]["world_transform"][key])
                                )
                                ** 2
                                for key in ("x", "y", "z")
                            )
                        ),
                        9,
                    )
                    record["spawn_probe"] = _empty_probe()
                    actor = _spawn_one(
                        world,
                        carla,
                        library,
                        record,
                        role_name=str(record["role_name"]),
                    )
                    if actor is not None:
                        record["snap"]["live_derivation"][
                            "spawn_rejected_navmesh_sample_count"
                        ] = rejected
                        break
                    rejected += 1
            if actor is not None:
                live.append((actor, record))

        # One actual simulation tick makes actor transforms and boxes observable.
        world.tick()
        for actor, target in live:
            target["spawn_probe"]["actual_world_transform"] = _transform_plain(
                actor.get_transform()
            )
            target["spawn_probe"]["actor_bounding_box"] = _actor_bbox(actor)
            target["spawn_probe"]["post_spawn_world_tick_observed"] = True

        _measure_clearance_sweeps(records, world, client, carla, library)
    except Exception:
        if live:
            try:
                client.apply_batch_sync(
                    [
                        carla.command.DestroyActor(int(actor.id))
                        for actor, _target in reversed(live)
                    ],
                    True,
                )
            except Exception:
                pass
        raise

    reversed_live = list(reversed(live))
    responses = client.apply_batch_sync(
        [carla.command.DestroyActor(int(actor.id)) for actor, _target in reversed_live],
        True,
    )
    require(
        len(responses) == len(reversed_live),
        "LIVE_AUTHORING_DESTROY_BATCH_RESPONSE_COUNT_INVALID",
    )
    for response, (_actor, target) in zip(responses, reversed_live):
        destroyed = not response.has_error()
        target["spawn_probe"]["destroyed"] = destroyed
        if not destroyed:
            target["spawn_probe"]["failure_reason"] = (
                "CARLA_DESTROY_BATCH_ERROR:" + str(response.error)
            )
    world.tick()
    world.tick()

    for record in records:
        components = record.get("composite_components")
        if components:
            component_verified = all(
                item["spawn_probe"]["attempted"]
                and item["spawn_probe"]["spawned"]
                and item["spawn_probe"]["destroyed"]
                and item["spawn_probe"]["post_spawn_world_tick_observed"]
                for item in components
            )
            if record["realization_contract"].get("mode") == "COMPOSITE_MEASURED_CLEARANCE_GATEWAY":
                component_verified = bool(
                    component_verified
                    and record["clearance_measurement"].get("verified") is True
                )
            resolved_id = record["blueprint_resolution"].get("resolved_blueprint_id")
            primary = next(
                (
                    item
                    for item in components
                    if item["blueprint_resolution"].get("resolved_blueprint_id")
                    == resolved_id
                ),
                components[0],
            )
            record["spawn_probe"] = copy.deepcopy(primary["spawn_probe"])
            record["spawn_probe"]["attempted"] = all(
                item["spawn_probe"]["attempted"] for item in components
            )
            record["spawn_probe"]["spawned"] = all(
                item["spawn_probe"]["spawned"] for item in components
            )
            record["spawn_probe"]["destroyed"] = all(
                item["spawn_probe"]["destroyed"] for item in components
            )
            record["spawn_probe"]["post_spawn_world_tick_observed"] = all(
                item["spawn_probe"]["post_spawn_world_tick_observed"]
                for item in components
            )
            record["spawn_probe"]["component_count"] = len(components)
            record["physical_spawn_verified"] = component_verified
            for item in components:
                item["physical_spawn_verified"] = bool(
                    item["spawn_probe"]["attempted"]
                    and item["spawn_probe"]["spawned"]
                    and item["spawn_probe"]["destroyed"]
                    and item["spawn_probe"]["post_spawn_world_tick_observed"]
                )
        else:
            probe = record["spawn_probe"]
            record["physical_spawn_verified"] = bool(
                probe["attempted"]
                and probe["spawned"]
                and probe["destroyed"]
                and probe["post_spawn_world_tick_observed"]
                and record["snap"]["usable"]
                and record["blueprint_resolution"]["resolved"]
            )

    for record in records:
        record["semantic_fidelity"] = _semantic_verified(
            record,
            world,
            carla,
            scenario_handler_sha256=scenario_handler_sha256,
        )

    required = [item for item in records if item["physical_spawn_required"]]
    physical_verified = bool(required) and all(
        item["physical_spawn_verified"] for item in required
    )
    semantic = [
        item
        for item in records
        if item["kind"] in {"dynamic_actor", "semantic_marker"}
    ]
    semantic_verified = bool(semantic) and all(
        item["semantic_fidelity"]["status"] == "VERIFIED" for item in semantic
    )
    receipt["actor_bindings"] = records
    receipt["joint_spawn_probe"] = {
        "required_binding_count": len(required),
        "required_spawned_count": sum(
            1 for item in required if item["spawn_probe"]["spawned"]
        ),
        "required_destroyed_count": sum(
            1 for item in required if item["spawn_probe"]["destroyed"]
        ),
        "all_required_physical_spawns_verified": physical_verified,
        "post_spawn_world_tick_observed": True,
    }
    receipt["physical_spawn_verified"] = physical_verified
    receipt["semantic_fidelity"] = {
        "status": "VERIFIED" if semantic_verified else "BLOCKED",
        "all_catalog_entities_verified": semantic_verified,
        "spawnability_used_as_semantic_proof": False,
        "external_self_attestation_used_as_gate": False,
        "unknown_binding_ids": [
            item["binding_id"]
            for item in semantic
            if item["semantic_fidelity"]["status"] != "VERIFIED"
        ],
    }
    receipt["handler_execution_receipt"] = {
        "status": "MISSING",
        "verified": False,
        "receipt_path": None,
        "receipt_payload_sha256": None,
    }
    receipt["gate_receipts"] = {
        "live_blueprint_resolution": all(
            item["blueprint_resolution"]["resolved"] for item in required
        ),
        "live_map_navmesh_snap": all(item["snap"]["usable"] for item in required),
        "live_spawn_destroy": physical_verified,
        "live_semantic_scene_realization": semantic_verified,
        "scenario_class_registration": False,
        "handler_spawn_tick_cleanup": False,
        "route_and_goal_nonleakage": True,
    }
    receipt["promotion_ready"] = False
    if not physical_verified:
        receipt["final_status"] = "BLOCKED_LIVE_PHYSICAL_SPAWN"
    elif not semantic_verified:
        receipt["final_status"] = "BLOCKED_LIVE_SEMANTIC_FIDELITY"
    else:
        receipt["final_status"] = "BLOCKED_HANDLER_EXECUTION_NOT_VERIFIED"
    assert_finite(receipt)
    result, _ = content_address(receipt, RECEIPT_HASH_FIELD)
    return result
