"""Compile frozen metadata into additive Stage 6A scenario fixtures.

Compilation is deterministic and purely static.  The result has the exact XML
shape consumed by Bench2Drive's route parser, but remains launch-blocked until
CARLA blueprint resolution, map occupancy, and semantic scene checks have
produced a separate live receipt.
"""

from __future__ import annotations

import copy
import hashlib
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import (
    CATALOG_PATH,
    EXPECTED_CATALOG_FILE_SHA256,
    EXPECTED_SCENARIO_DEFINITION_SHA256,
    EXPECTED_SCENARIO_PAYLOAD_SHA256,
    EXPECTED_SPLIT_FILE_SHA256,
    FREEZE_EVIDENCE_PATH,
    PRIVATE_SCHEMA_VERSION,
    ROOT,
    ROUTE_SCENARIO_TYPE,
    RUNTIME_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SPLIT_PATH,
    STATIC_STATUS,
    assert_finite,
    canonical_sha256,
    file_sha256,
    load_json,
    pretty_json_bytes,
    relative_to_root,
    require,
    verify_embedded_sha256,
    walk_keys,
)
from .geometry import relation_branch, route_spawn_transform, sample_signed_station


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "generated"
RUNTIME_DIR = "runtime_visible"
ROUTE_DIR = "routes"
PRIVATE_MANIFEST = "EVALUATOR_PRIVATE_MANIFEST.json"
TOP_MANIFEST = "STAGE6A_SCENARIO_MANIFEST.json"
SCHEMA_FILE = "SCENARIO_EXECUTION_SCHEMA.json"
HASH_MANIFEST = "HASH_MANIFEST.json"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _load_authorities() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    catalog = load_json(CATALOG_PATH)
    split = load_json(SPLIT_PATH)
    evidence = load_json(FREEZE_EVIDENCE_PATH)
    require(
        file_sha256(CATALOG_PATH) == EXPECTED_CATALOG_FILE_SHA256,
        "FROZEN_CATALOG_FILE_SHA256_MISMATCH",
    )
    require(
        file_sha256(SPLIT_PATH) == EXPECTED_SPLIT_FILE_SHA256,
        "FROZEN_SPLIT_FILE_SHA256_MISMATCH",
    )
    require(
        catalog.get("scenario_payload_sha256") == EXPECTED_SCENARIO_PAYLOAD_SHA256,
        "FROZEN_SCENARIO_PAYLOAD_SHA256_MISMATCH",
    )
    require(
        catalog.get("scenario_definition_payload_sha256")
        == EXPECTED_SCENARIO_DEFINITION_SHA256,
        "FROZEN_SCENARIO_DEFINITION_SHA256_MISMATCH",
    )
    require(
        evidence.get("artifacts", {}).get("SCENARIO_CATALOG.json", {}).get("sha256")
        == EXPECTED_CATALOG_FILE_SHA256,
        "FREEZE_EVIDENCE_CATALOG_PIN_MISMATCH",
    )
    require(len(catalog.get("scenarios", [])) == 24, "FROZEN_SCENARIO_COUNT_NOT_24")
    require(
        split.get("catalog_scenario_payload_sha256") == EXPECTED_SCENARIO_PAYLOAD_SHA256,
        "FROZEN_SPLIT_CATALOG_PAYLOAD_PIN_MISMATCH",
    )
    require(
        split.get("freeze_id") == catalog.get("freeze_id"),
        "FROZEN_SPLIT_FREEZE_ID_MISMATCH",
    )
    scenarios_by_id = {
        str(item.get("scenario_id")): item for item in catalog.get("scenarios", [])
    }
    require(len(scenarios_by_id) == 24, "FROZEN_SCENARIO_IDS_NOT_UNIQUE")
    split_ids: list[str] = []
    for split_name in ("train", "dev", "test"):
        rows = split.get("splits", {}).get(split_name, {}).get("scenario_ids", [])
        require(len(rows) == 8, f"FROZEN_SPLIT_COUNT_INVALID:{split_name}")
        for scenario_id in rows:
            scenario_id = str(scenario_id)
            require(
                scenarios_by_id.get(scenario_id, {}).get("split") == split_name,
                f"FROZEN_SPLIT_ASSIGNMENT_MISMATCH:{scenario_id}",
            )
            split_ids.append(scenario_id)
    require(
        len(split_ids) == len(set(split_ids)) == 24
        and set(split_ids) == set(scenarios_by_id),
        "FROZEN_SPLIT_PARTITION_INVALID",
    )
    return catalog, split, evidence


def _parse_source_route(path: Path) -> tuple[ET.ElementTree, ET.Element, list[list[float]]]:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"SOURCE_ROUTE_XML_INVALID:{path}") from exc
    root = tree.getroot()
    routes = list(root.findall("route"))
    require(root.tag == "routes" and len(routes) == 1, "SOURCE_ROUTE_COUNT_INVALID")
    route = routes[0]
    scenarios = route.find("scenarios")
    require(scenarios is not None, "SOURCE_ROUTE_SCENARIOS_CONTAINER_MISSING")
    require(len(list(scenarios)) == 0, "SOURCE_ROUTE_NOT_SCENARIO_FREE")
    positions = route.findall("./waypoints/position")
    require(len(positions) >= 3, "SOURCE_ROUTE_WAYPOINTS_INSUFFICIENT")
    route_points = [
        [float(position.attrib[axis]) for axis in ("x", "y", "z")]
        for position in positions
    ]
    require(
        all(math.isfinite(value) for point in route_points for value in point),
        "SOURCE_ROUTE_WAYPOINT_NONFINITE",
    )
    return tree, route, route_points


def _blueprint_selector(semantic_class: str, actor_kind: str) -> dict[str, Any]:
    lowered = semantic_class.lower()
    if actor_kind == "dynamic_actor":
        if "bicycle" in lowered or "cyclist" in lowered:
            primary = "vehicle.*bike*"
            fallbacks = ["vehicle.diamondback.century", "vehicle.*"]
            attributes = {"base_type": "bicycle"}
        elif "bus" in lowered:
            primary = "vehicle.*bus*"
            fallbacks = ["vehicle.*"]
            attributes = {"base_type": "car", "semantic_hint": "bus"}
        elif "truck" in lowered or "van" in lowered:
            primary = "vehicle.*"
            fallbacks = ["vehicle.*"]
            attributes = {"base_type": "car", "semantic_hint": "commercial_vehicle"}
        else:
            primary = "vehicle.*"
            fallbacks = ["vehicle.*"]
            attributes = {"base_type": "car"}
        if "white" in lowered:
            attributes["color_preference"] = "255,255,255"
        realization_hint = "SPAWNED_DYNAMIC_ACTOR"
    else:
        if "bus_stop_shelter" in lowered:
            primary = "static.prop.busstop"
            fallbacks = ["static.prop.busstoplb", "static.prop.bench*"]
            realization_hint = "BUS_STOP_ASSET_PROXY_REQUIRES_VISUAL_ATTESTATION"
        elif "bus_stop_end_sign" in lowered:
            primary = "static.prop.busstoplb"
            fallbacks = ["static.prop.streetsign*", "static.prop.trafficwarning"]
            realization_hint = "BUS_STOP_SIGN_PROXY_REQUIRES_VISUAL_ATTESTATION"
        elif lowered == "map.school":
            primary = "static.prop.slide"
            fallbacks = ["static.prop.swing", "static.prop.pergola"]
            realization_hint = (
                "PLAYGROUND_ASSET_PLUS_SEPARATE_SCHOOL_BUS_CONTEXT_REQUIRES_"
                "VISUAL_ATTESTATION"
            )
        elif lowered == "map.plaza":
            primary = "static.prop.fountain"
            fallbacks = [
                "static.prop.streetfountain",
                "static.prop.kiosk_01",
                "static.prop.bench*",
            ]
            realization_hint = "PLAZA_ASSET_CLUSTER_PROXY_REQUIRES_VISUAL_ATTESTATION"
        elif "arch_passage" in lowered or "clearance_passage" in lowered:
            primary = "static.prop.pergola"
            fallbacks = ["static.prop.container"]
            realization_hint = (
                "PHYSICAL_PROXY_ONLY_CANNOT_SUBSTANTIATE_LOW_HIGH_CLEARANCE_DIFFERENCE"
            )
        elif any(token in lowered for token in ("sign", "signal", "monitor")):
            primary = "static.prop.streetsign*"
            fallbacks = ["static.prop.trafficwarning", "static.prop.streetbarrier"]
            realization_hint = "SIGN_OR_MONITOR_PROXY_REQUIRES_VISUAL_ATTESTATION"
        else:
            primary = "static.prop.streetbarrier"
            fallbacks = ["static.prop.streetsign*", "static.prop.trafficwarning"]
            realization_hint = (
                "LOGICAL_WORLD_REGION_PREFERRED_NO_AUTOMATIC_BLUEPRINT_EQUIVALENCE"
            )
        attributes = {"semantic_hint": semantic_class}
    return {
        "strategy": "CARLA_BLUEPRINT_LIBRARY_FILTER_THEN_ATTRIBUTE_CONSTRAINTS",
        "primary_filter": primary,
        "fallback_filters": fallbacks,
        "attribute_constraints": attributes,
        "resolved_blueprint_id": None,
        "resolution_state": "REQUIRES_LIVE_BLUEPRINT_LIBRARY_RESOLUTION",
        "live_resolution_receipt_required": True,
        "static_compiler_claims_blueprint_exists": False,
        "proxy_semantic_equivalence_claimed": False,
        "semantic_fidelity_policy": "SEPARATE_LIVE_ATTESTATION_REQUIRED",
        "realization_hint": realization_hint,
    }


def _entity_record(
    placement: Mapping[str, Any],
    topology: Mapping[str, Any],
    eligibility: Mapping[str, Any],
    scenario_variant_setup_id: str,
) -> dict[str, Any]:
    anchor = placement.get("anchor", {})
    require(anchor.get("frame") == "DECISION_POINT_FRENET", "ENTITY_ANCHOR_FRAME_INVALID")
    semantic_class = str(placement.get("blueprint_or_class", ""))
    require(bool(semantic_class), "ENTITY_SEMANTIC_CLASS_MISSING")
    actor_kind = "dynamic_actor" if semantic_class.startswith("vehicle.") else "semantic_marker"
    branch_role, relation_status = relation_branch(str(anchor.get("lateral_relation", "")))
    z_offset = 0.35 if actor_kind == "dynamic_actor" else 0.05
    transform = sample_signed_station(
        topology,
        eligibility,
        branch_role,
        float(anchor["longitudinal_s_m"]),
        z_offset_m=z_offset,
    )
    realization_contract: dict[str, Any]
    if "arch_passage" in semantic_class or "clearance_passage" in semantic_class:
        equal_clearance = scenario_variant_setup_id == "PASSAGES_REJOIN_WITH_AMPLE_EQUAL_CLEARANCE"
        target_clearance_m = 4.5 if equal_clearance or "high_clearance" in semantic_class else 3.2
        realization_contract = {
            "mode": "COMPOSITE_MEASURED_CLEARANCE_GATEWAY",
            "automatic_semantic_verification_eligible": True,
            "scene_group": "CLEARANCE_PASSAGE_PAIR",
            "target_clearance_m": target_clearance_m,
            "target_opening_width_m": 4.5,
            "minimum_ego_clearance_margin_m": 0.5,
            "support_blueprint_selector": {
                "primary_filter": "static.prop.container",
                "fallback_filters": ["static.prop.streetbarrier"],
            },
            "overhead_blueprint_selector": {
                "primary_filter": "static.prop.ironplank",
                "fallback_filters": ["static.prop.container"],
            },
            "verification_requirements": [
                "SPAWNED_COMPONENT_BOUNDING_BOXES",
                "MEASURED_LOWEST_OVERHEAD_Z",
                "MEASURED_EGO_BOUNDING_BOX_HEIGHT",
                "STATIC_TRY_SPAWN_COLLISION_SWEEP",
                "PAIRWISE_CLEARANCE_RELATION_MATCHES_PHYSICAL_SCENE_CONTRACT",
            ],
            "single_asset_semantic_equivalence_claimed": False,
        }
    else:
        realization_contract = {
            "mode": (
                "SPAWNED_DYNAMIC_ACTOR"
                if actor_kind == "dynamic_actor"
                else "PROXY_OR_EXISTING_WORLD_FEATURE_REQUIRES_ATTESTATION"
            ),
            "automatic_semantic_verification_eligible": False,
            "single_asset_semantic_equivalence_claimed": False,
        }
    return {
        "entity_id": str(placement["entity_id"]),
        "kind": actor_kind,
        "semantic_class": semantic_class,
        "source_anchor": {
            "frame": "DECISION_POINT_FRENET",
            "longitudinal_s_m": float(anchor["longitudinal_s_m"]),
            "lateral_relation": str(anchor["lateral_relation"]),
        },
        "geometric_binding": {
            "branch_role": branch_role,
            "binding_state": relation_status,
            "projection_method": (
                "FROZEN_BRANCH_POLYLINE_SIGNED_STATION_WITH_TERMINAL_TANGENT_"
                "EXTRAPOLATION_WHEN_REQUIRED"
            ),
            "live_map_occupancy_receipt_required": True,
            "world_transform": transform,
        },
        "blueprint_selector": _blueprint_selector(semantic_class, actor_kind),
        "realization_contract": realization_contract,
        "spawn_state": {
            "physics_enabled": False,
            "collision_enabled": actor_kind == "dynamic_actor",
            "autopilot_enabled": False,
            "role_name": f"driveclarify_{placement['entity_id'].lower()}",
            "state": "PRETRIGGER_STATIC_PENDING_LIVE_BEHAVIOR_BINDING",
        },
    }


def _background_slots(
    background: Mapping[str, Any],
    topology: Mapping[str, Any],
    eligibility: Mapping[str, Any],
) -> list[dict[str, Any]]:
    vehicle_count = int(background.get("background_vehicle_count", 0))
    pedestrian_count = int(background.get("background_pedestrian_count", 0))
    require(vehicle_count >= 0 and pedestrian_count >= 0, "BACKGROUND_COUNT_NEGATIVE")
    slots: list[dict[str, Any]] = []
    for index in range(vehicle_count):
        branch_role = "STRAIGHT_BRANCH" if index % 2 == 0 else "RIGHT_TURN_BRANCH"
        lateral = 3.4 if index % 4 < 2 else -3.4
        station = 12.0 + 3.25 * index
        slots.append(
            {
                "slot_id": f"BGV{index + 1:02d}",
                "kind": "background_vehicle",
                "world_transform": sample_signed_station(
                    topology,
                    eligibility,
                    branch_role,
                    station,
                    lateral_offset_m=lateral,
                    z_offset_m=0.35,
                ),
                "blueprint_selector": _blueprint_selector("vehicle.background", "dynamic_actor"),
                "occupancy_state": "REQUIRES_LIVE_MAP_AND_COLLISION_SWEEP",
            }
        )
    for index in range(pedestrian_count):
        station = 18.0 + 7.0 * index
        lateral = 5.0 if index % 2 == 0 else -5.0
        slots.append(
            {
                "slot_id": f"BGP{index + 1:02d}",
                "kind": "background_pedestrian",
                "world_transform": sample_signed_station(
                    topology,
                    eligibility,
                    "STRAIGHT_BRANCH",
                    station,
                    lateral_offset_m=lateral,
                    z_offset_m=0.1,
                ),
                "blueprint_selector": {
                    "strategy": "CARLA_BLUEPRINT_LIBRARY_FILTER_THEN_ATTRIBUTE_CONSTRAINTS",
                    "primary_filter": "walker.pedestrian.*",
                    "fallback_filters": ["walker.pedestrian.*"],
                    "attribute_constraints": {},
                    "resolved_blueprint_id": None,
                    "resolution_state": "REQUIRES_LIVE_BLUEPRINT_LIBRARY_RESOLUTION",
                    "live_resolution_receipt_required": True,
                    "static_compiler_claims_blueprint_exists": False,
                    "proxy_semantic_equivalence_claimed": False,
                    "semantic_fidelity_policy": "NOT_APPLICABLE_NUISANCE_ACTOR",
                    "realization_hint": "SPAWNED_BACKGROUND_PEDESTRIAN",
                },
                "occupancy_state": "REQUIRES_LIVE_NAVMESH_AND_COLLISION_SWEEP",
            }
        )
    return slots


def _timeline(
    scenario: Mapping[str, Any],
    trigger: Mapping[str, Any],
) -> list[dict[str, Any]]:
    runtime_context = scenario["traffic_configuration"]["runtime_context_setup"]
    events: list[dict[str, Any]] = [
        {
            "event_id": "EV00_CONSTRUCT",
            "condition": {"kind": "ROUTE_SCENARIO_CONSTRUCTION"},
            "actions": ["RESOLVE_BLUEPRINT_RECEIPT", "SPAWN_PRETRIGGER_ENTITIES"],
            "binding_state": "LIVE_RESOLUTION_REQUIRED",
        },
        {
            "event_id": "EV01_INSTRUCTION_ISSUE",
            "condition": {
                "kind": "EGO_SIGNED_STATION_REACHED",
                "signed_station_m": float(
                    scenario["traffic_configuration"]["physical_setup_projection"]
                    ["instruction_issue_anchor"]["longitudinal_s_m"]
                ),
            },
            "actions": ["DELIVER_RAW_INSTRUCTION_TO_POLICY_INPUT"],
            "binding_state": "STATICALLY_BOUND",
        },
        {
            "event_id": "EV02_SCENARIO_TRIGGER",
            "condition": {
                "kind": "EGO_WITHIN_TRIGGER_RADIUS",
                "radius_m": float(trigger["radius_m"]),
                "world_transform": copy.deepcopy(trigger["world_transform"]),
            },
            "actions": ["ACTIVATE_SCENE_BEHAVIOR_TREE"],
            "binding_state": "ROUTE_XML_BOUND",
        },
        {
            "event_id": "EV03_OBSERVABLE_CONDITION",
            "condition": {"kind": "AFTER_SCENARIO_TRIGGER"},
            "actions": ["REALIZE_FROZEN_PHYSICAL_OBSERVABLE_CONDITION"],
            "observable_condition": (
                scenario["traffic_configuration"]["physical_setup_projection"]
                ["observable_condition_setup"]
            ),
            "binding_state": "REQUIRES_LIVE_SEMANTIC_SCENE_VALIDATION",
        },
    ]
    scheduled = runtime_context.get("future_information_event")
    if scheduled is not None:
        require(isinstance(scheduled, Mapping), "SCHEDULED_RUNTIME_SIGNAL_INVALID")
        events.append(
            {
                "event_id": "EV04_SCHEDULED_RUNTIME_SIGNAL",
                "condition": {
                    "kind": "SIM_TIME_FROM_DECISION",
                    "seconds": float(scheduled["arrival_seconds_from_decision"]),
                    "clock": "CARLA_SIMULATION_TIME",
                },
                "actions": ["DELIVER_SEED_BOUND_RUNTIME_SIGNAL_AT_OR_AFTER_DUE_TIME"],
                "signal_source": str(scheduled["source"]),
                "delivery_by_allowed_seed": copy.deepcopy(
                    scheduled["delivery_schedule_by_seed"]
                ),
                "binding_state": "SCHEDULE_FROZEN_PHYSICAL_CHANNEL_REQUIRES_LIVE_BINDING",
            }
        )
    events.append(
        {
            "event_id": "EV99_ROUTE_TERMINAL",
            "condition": {"kind": "ROUTE_COMPLETION_OR_TIMEOUT_OR_TERMINATING_FAILURE"},
            "actions": ["STOP_SCENARIO_BEHAVIOR", "DESTROY_SCENARIO_ENTITIES"],
            "binding_state": "GLOBAL_ROUTE_CRITERIA_BOUND",
        }
    )
    return events


def _runtime_fixture(
    scenario: Mapping[str, Any],
    topology: Mapping[str, Any],
    eligibility: Mapping[str, Any],
    source_route_relative: str,
    source_route_sha256: str,
    route_points: Sequence[Sequence[float]],
    runtime_fixture_id: str,
    derived_route_relative: str,
) -> dict[str, Any]:
    projection = scenario["traffic_configuration"]["physical_setup_projection"]
    entities = [
        _entity_record(
            item,
            topology,
            eligibility,
            str(projection["scenario_variant_setup_id"]),
        )
        for item in projection["entity_placements"]
    ]
    trigger_transform = sample_signed_station(
        topology, eligibility, "STRAIGHT_BRANCH", 0.0, z_offset_m=0.0
    )
    trigger = {
        "kind": "ROUTE_PROXIMITY_TRIGGER",
        "world_transform": trigger_transform,
        "radius_m": 2.0,
        "yaw_tolerance_degrees": 10.0,
        "source": "FROZEN_V3_DECISION_POINT",
    }
    background = scenario["traffic_configuration"]["physical_setup_projection"][
        "background_actor_setup"
    ]
    runtime = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "runtime_fixture_id": runtime_fixture_id,
        "visibility_contract": {
            "environment_builder_can_read": True,
            "policy_projection_allowlist": [
                "raw_instruction",
                "standard_runtime_sensor_observations",
                "standard_runtime_route_context",
            ],
            "evaluator_private_join_available": False,
        },
        "raw_instruction": scenario["instruction_text"],
        "route_binding": {
            "route_id": str(scenario["route_id"]),
            "town": str(scenario["town"]),
            "route_source_identity": {
                "path": source_route_relative,
                "sha256": source_route_sha256,
                "scenario_free_base": True,
            },
            "derived_route_path": derived_route_relative,
            "normal_route_context_only": True,
        },
        "allowed_seed_values": [int(value) for value in scenario["seed"]],
        "selected_seed_contract": {
            "exactly_one_allowed_seed_required_at_launch": True,
            "selected_value": None,
            "traffic_manager_seed_source": "SELECTED_ALLOWED_SEED",
        },
        "weather": copy.deepcopy(scenario["weather"]),
        "physical_scene_contract": {
            "scenario_variant_setup_id": str(projection["scenario_variant_setup_id"]),
            "observable_condition": str(projection["observable_condition_setup"]),
            "source": "FROZEN_ENVIRONMENT_BUILDER_PHYSICAL_SETUP_PROJECTION",
            "policy_projection_allowed": False,
        },
        "ego_spawn": {
            "world_transform": route_spawn_transform(route_points),
            "blueprint_selector": {
                "strategy": "PINNED_ROUTE_SCENARIO_REQUEST_WITH_FALLBACK_GUARD",
                "primary_filter": "vehicle.lincoln.mkz_2020",
                "fallback_filters": ["vehicle.*"],
                "resolved_blueprint_id": None,
                "resolution_state": "SOURCE_PINNED_NOT_LIVE_BLUEPRINT_VERIFIED",
                "live_resolution_receipt_required": True,
                "static_compiler_claims_blueprint_exists": False,
                "proxy_semantic_equivalence_claimed": False,
                "semantic_fidelity_policy": "NOT_APPLICABLE_EGO_ROUTE_ACTOR",
                "realization_hint": "ROUTE_SCENARIO_OWNED_EGO",
            },
            "role_name": "hero",
            "spawn_semantics": "FIRST_INTERPOLATED_ROUTE_WAYPOINT_PLUS_0_5M_Z",
        },
        "trigger": trigger,
        "entities": entities,
        "background": {
            "nuisance_only": bool(background["nuisance_only"]),
            "density": str(background["density"]),
            "spawn_geometry_method": (
                "FROZEN_BRANCH_POLYLINE_SIGNED_STATION_WITH_TERMINAL_TANGENT_"
                "EXTRAPOLATION_WHEN_REQUIRED"
            ),
            "spawn_slots": _background_slots(background, topology, eligibility),
            "spawn_order_varies_by_selected_seed": True,
            "geometry_fixed_across_allowed_seeds": True,
        },
        "event_timeline": _timeline(scenario, trigger),
        "termination": {
            "success_condition": {
                "kind": "GLOBAL_ROUTE_COMPLETION_CRITERION",
                "route_endpoint_world_xyz": [float(value) for value in route_points[-1]],
            },
            "failure_conditions": [
                "IN_ROUTE_TERMINATING_FAILURE",
                "ACTOR_BLOCKED_TERMINATING_FAILURE",
                "COLLISION_OR_RULE_CRITERION_RECORDED",
            ],
            "timeout": {
                "kind": "ROUTE_TIMEOUT_BEHAVIOR_PLUS_SCENARIO_HARD_LIMIT",
                "scenario_hard_limit_seconds": 10000.0,
                "clock": "CARLA_SIMULATION_TIME",
            },
            "cleanup": "DESTROY_ALL_SCENARIO_OWNED_ACTORS",
            "binding_state": "STATIC_GLOBAL_CRITERIA_REUSE_REQUIRES_LIVE_CONFIRMATION",
        },
        "launch_gate": {
            "launch_ready": False,
            "required_receipts": [
                "LIVE_BLUEPRINT_RESOLUTION_RECEIPT",
                "LIVE_MAP_OCCUPANCY_AND_COLLISION_SWEEP_RECEIPT",
                "LIVE_SEMANTIC_SCENE_REALIZATION_RECEIPT",
                "SCENARIO_CLASS_REGISTRATION_RECEIPT",
                "ROUTE_AND_GOAL_NONLEAKAGE_RECEIPT",
            ],
            "blocking_reason": "STATIC_FIXTURE_COMPILED_LIVE_RESOLUTION_NOT_PERFORMED",
        },
    }
    denylist = set(load_json(CATALOG_PATH)["label_firewall"]["evaluation_only_denylist"])
    overlap = sorted(set(walk_keys(runtime)) & denylist)
    require(not overlap, "RUNTIME_VISIBLE_DENYLIST_OVERLAP:" + ",".join(overlap))
    assert_finite(runtime)
    return runtime


def _runtime_id(scenario: Mapping[str, Any]) -> str:
    projection = {
        "route_id": scenario["route_id"],
        "town": scenario["town"],
        "raw_instruction": scenario["instruction_text"],
        "weather": scenario["weather"],
        "physical_setup": scenario["traffic_configuration"]["physical_setup_projection"],
        "catalog_payload_sha256": EXPECTED_SCENARIO_PAYLOAD_SHA256,
    }
    return "dc-runtime-" + canonical_sha256(projection)[:24]


def _xml_bytes(
    source_tree: ET.ElementTree,
    source_route: ET.Element,
    runtime_fixture_id: str,
    runtime_relative: str,
    runtime_sha256: str,
    trigger: Mapping[str, Any],
) -> bytes:
    root_copy = copy.deepcopy(source_tree.getroot())
    route = root_copy.find("route")
    require(route is not None, "DERIVED_ROUTE_MISSING")
    scenarios = route.find("scenarios")
    require(scenarios is not None and len(list(scenarios)) == 0, "DERIVED_ROUTE_BASE_NOT_EMPTY")
    runtime_suffix = (
        runtime_fixture_id[len("dc-runtime-") :]
        if runtime_fixture_id.startswith("dc-runtime-")
        else runtime_fixture_id
    )
    scenario_name = "DCPaperMVP_" + runtime_suffix
    scenario = ET.SubElement(
        scenarios,
        "scenario",
        {"name": scenario_name, "type": ROUTE_SCENARIO_TYPE},
    )
    transform = trigger["world_transform"]
    ET.SubElement(
        scenario,
        "trigger_point",
        {
            key: format(float(transform[key]), ".9f").rstrip("0").rstrip(".")
            for key in ("x", "y", "z", "yaw")
        },
    )
    ET.SubElement(
        scenario,
        "fixture",
        {
            "runtime_fixture_id": runtime_fixture_id,
            "runtime_manifest": runtime_relative,
            "runtime_manifest_sha256": runtime_sha256,
            "binding_state": "STATIC_COMPILED_LIVE_RESOLUTION_REQUIRED",
        },
    )
    if hasattr(ET, "indent"):
        ET.indent(root_copy, space="   ")
    payload = ET.tostring(root_copy, encoding="utf-8", short_empty_elements=True) + b"\n"
    # Ensure the source object was not mutated through a shared child.
    require(source_route.find("./scenarios/scenario") is None, "SOURCE_ROUTE_MUTATED_IN_MEMORY")
    return payload


def _evaluation_metadata(
    scenario: Mapping[str, Any],
    entities: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    entity_transforms = {
        row["entity_id"]: row["geometric_binding"]["world_transform"] for row in entities
    }
    options: dict[str, Any] = {}
    for option_name in ("candidate_A", "candidate_B"):
        annotation = scenario["candidate_interpretations"][option_name]
        grounding = [str(value) for value in annotation["grounding_entity_ids"]]
        options[option_name] = {
            "annotated_action": annotation["action"],
            "annotated_goal": annotation["goal"],
            "grounding_entity_ids": grounding,
            "grounding_world_transforms": [
                entity_transforms[value] for value in grounding if value in entity_transforms
            ],
            "geometry_semantics_state": "PROXY_ONLY_REQUIRES_LIVE_GOAL_REGION_AUTHORING",
        }
    return {
        "expected_initial_decision": scenario["expected_decision_for_validation"],
        "reference_options": options,
        "task_metrics": {
            "route_completion": "GLOBAL_ROUTE_CRITERION_RECORD",
            "goal_correctness": {
                "value": None,
                "reason_code": "LIVE_GOAL_REGION_NOT_YET_VALIDATED",
            },
            "instruction_success": {
                "value": None,
                "reason_code": "LIVE_TASK_PREDICATE_NOT_YET_VALIDATED",
            },
        },
        "safety_metrics": {
            "collision": "GLOBAL_COLLISION_CRITERION_RECORD",
            "off_road": "GLOBAL_OUTSIDE_ROUTE_AND_IN_ROUTE_RECORDS",
            "rule_violation": "GLOBAL_RED_LIGHT_AND_STOP_RECORDS",
            "ttc_threshold_seconds": None,
            "near_miss_threshold": None,
            "missing_threshold_reason": "NOT_FROZEN_BY_STAGE5_SCENARIO_PROTOCOL",
        },
        "decision_metrics": {
            "label_join": "EVALUATOR_PRIVATE_POST_EPISODE_ONLY",
            "allowed_predictions": ["ACT", "ASK", "WAIT"],
        },
        "evaluation_ready": False,
        "blocking_reason": "LIVE_GOAL_AND_SAFETY_THRESHOLD_BINDINGS_REQUIRED",
    }


def _private_record(
    scenario: Mapping[str, Any],
    runtime: Mapping[str, Any],
    runtime_relative: str,
    runtime_sha256: str,
    route_relative: str,
    route_sha256: str,
    topology_relative: str,
    topology_sha256: str,
    eligibility_relative: str,
    eligibility_sha256: str,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario["scenario_id"],
        "split": scenario["split"],
        "seed": [int(value) for value in scenario["seed"]],
        "catalog_identity": {
            "catalog_file_sha256": EXPECTED_CATALOG_FILE_SHA256,
            "scenario_payload_sha256": EXPECTED_SCENARIO_PAYLOAD_SHA256,
            "scenario_definition_payload_sha256": EXPECTED_SCENARIO_DEFINITION_SHA256,
        },
        "runtime_binding": {
            "runtime_fixture_id": runtime["runtime_fixture_id"],
            "runtime_manifest_path": runtime_relative,
            "runtime_manifest_sha256": runtime_sha256,
            "derived_route_path": route_relative,
            "derived_route_sha256": route_sha256,
            "scenario_runner_type": ROUTE_SCENARIO_TYPE,
        },
        "frozen_geometry_authorities": {
            "topology_path": topology_relative,
            "topology_sha256": topology_sha256,
            "eligibility_path": eligibility_relative,
            "eligibility_sha256": eligibility_sha256,
        },
        "expected_evaluation_metadata": _evaluation_metadata(
            scenario, runtime["entities"]
        ),
        "annotation_join_contract": {
            "join_time": "POST_EPISODE_ONLY",
            "policy_access": False,
            "expected_decision": scenario["expected_decision_for_validation"],
            "scenario_type": scenario["scenario_type"],
        },
    }


def execution_schema() -> dict[str, Any]:
    """Return the compact Draft 2020-12 schema for the top-level manifest."""

    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://driveclarify.local/schema/paper-mvp-stage6a-scenarios-v0.json",
        "title": "DriveClarify Paper MVP Stage 6A static scenario compilation",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "visibility",
            "final_status",
            "frozen_identity",
            "scenario_count",
            "runtime_fixture_count",
            "derived_route_count",
            "stage6a_readiness",
            "source_split_binding",
            "scenario_class_registration",
            "records",
        ],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "visibility": {"const": "CONTROL_PLANE_ONLY_NOT_POLICY_INPUT"},
            "final_status": {"const": STATIC_STATUS},
            "frozen_identity": {
                "type": "object",
                "required": [
                    "catalog_file_sha256",
                    "scenario_payload_sha256",
                    "scenario_definition_payload_sha256",
                    "split_file_sha256",
                ],
                "properties": {
                    "catalog_file_sha256": sha,
                    "scenario_payload_sha256": sha,
                    "scenario_definition_payload_sha256": sha,
                    "split_file_sha256": sha,
                },
            },
            "scenario_count": {"const": 24},
            "runtime_fixture_count": {"const": 24},
            "derived_route_count": {"const": 24},
            "stage6a_readiness": {
                "type": "object",
                "required": ["static_compilation_complete", "live_launch_ready"],
                "properties": {
                    "static_compilation_complete": {"const": True},
                    "live_launch_ready": {"const": False},
                },
            },
            "source_split_binding": {
                "type": "object",
                "required": ["train", "dev", "test"],
                "additionalProperties": False,
                "properties": {
                    name: {
                        "type": "array",
                        "minItems": 8,
                        "maxItems": 8,
                        "items": {"type": "string", "pattern": "^DCV0-S[0-9]{3}$"},
                    }
                    for name in ("train", "dev", "test")
                },
            },
            "scenario_class_registration": {
                "type": "object",
                "required": [
                    "type",
                    "source_path",
                    "registered_in_live_scenario_runner",
                    "registration_receipt_required",
                ],
                "properties": {
                    "type": {"const": ROUTE_SCENARIO_TYPE},
                    "source_path": {"type": "string"},
                    "registered_in_live_scenario_runner": {"const": False},
                    "registration_receipt_required": {"const": True},
                },
            },
            "records": {
                "type": "array",
                "minItems": 24,
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "required": [
                        "scenario_id",
                        "split",
                        "seed",
                        "runtime_fixture_id",
                        "runtime_manifest_path",
                        "runtime_manifest_sha256",
                        "derived_route_path",
                        "derived_route_sha256",
                    ],
                },
            },
        },
    }


def _compile_documents() -> dict[str, bytes]:
    catalog, split_doc, _ = _load_authorities()
    documents: dict[str, bytes] = {}
    private_records: list[dict[str, Any]] = []
    top_records: list[dict[str, Any]] = []
    for scenario in catalog["scenarios"]:
        scenario_id = str(scenario["scenario_id"])
        source_relative = str(scenario["source_route"]["fixture_path"])
        source_path = ROOT / source_relative
        require(source_path.is_file(), f"SOURCE_ROUTE_MISSING:{scenario_id}")
        source_sha = file_sha256(source_path)
        require(
            source_sha == scenario["source_route"]["fixture_sha256"],
            f"SOURCE_ROUTE_SHA256_MISMATCH:{scenario_id}",
        )
        unit_dir = source_path.parent
        topology_path = unit_dir / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"
        eligibility_path = unit_dir / "OBSERVATION_ELIGIBILITY_CONTRACT.json"
        topology = load_json(topology_path)
        eligibility = load_json(eligibility_path)
        require(verify_embedded_sha256(topology), f"TOPOLOGY_EMBEDDED_SHA_INVALID:{scenario_id}")
        require(
            verify_embedded_sha256(eligibility),
            f"ELIGIBILITY_EMBEDDED_SHA_INVALID:{scenario_id}",
        )
        require(
            eligibility.get("topology_sha256") == topology.get("sha256"),
            f"ELIGIBILITY_TOPOLOGY_PIN_MISMATCH:{scenario_id}",
        )
        source_tree, source_route, route_points = _parse_source_route(source_path)
        require(source_route.get("id") == str(scenario["route_id"]), f"ROUTE_ID_MISMATCH:{scenario_id}")
        require(source_route.get("town") == scenario["town"], f"ROUTE_TOWN_MISMATCH:{scenario_id}")
        runtime_id = _runtime_id(scenario)
        runtime_relative = f"{RUNTIME_DIR}/{runtime_id}.json"
        route_relative = f"{ROUTE_DIR}/{runtime_id}.xml"
        runtime = _runtime_fixture(
            scenario,
            topology,
            eligibility,
            source_relative,
            source_sha,
            route_points,
            runtime_id,
            route_relative,
        )
        runtime_bytes = pretty_json_bytes(runtime)
        runtime_sha = hashlib.sha256(runtime_bytes).hexdigest()
        route_bytes = _xml_bytes(
            source_tree,
            source_route,
            runtime_id,
            runtime_relative,
            runtime_sha,
            runtime["trigger"],
        )
        route_sha = hashlib.sha256(route_bytes).hexdigest()
        documents[runtime_relative] = runtime_bytes
        documents[route_relative] = route_bytes
        private_records.append(
            _private_record(
                scenario,
                runtime,
                runtime_relative,
                runtime_sha,
                route_relative,
                route_sha,
                relative_to_root(topology_path),
                file_sha256(topology_path),
                relative_to_root(eligibility_path),
                file_sha256(eligibility_path),
            )
        )
        top_records.append(
            {
                "scenario_id": scenario_id,
                "split": scenario["split"],
                "seed": [int(value) for value in scenario["seed"]],
                "runtime_fixture_id": runtime_id,
                "runtime_manifest_path": runtime_relative,
                "runtime_manifest_sha256": runtime_sha,
                "derived_route_path": route_relative,
                "derived_route_sha256": route_sha,
                "live_launch_ready": False,
            }
        )
    private_manifest = {
        "schema_version": PRIVATE_SCHEMA_VERSION,
        "visibility": "EVALUATOR_PRIVATE_NEVER_POLICY_VISIBLE",
        "frozen_identity": {
            "catalog_file_sha256": EXPECTED_CATALOG_FILE_SHA256,
            "scenario_payload_sha256": EXPECTED_SCENARIO_PAYLOAD_SHA256,
            "scenario_definition_payload_sha256": EXPECTED_SCENARIO_DEFINITION_SHA256,
            "split_file_sha256": EXPECTED_SPLIT_FILE_SHA256,
        },
        "records": private_records,
    }
    documents[PRIVATE_MANIFEST] = pretty_json_bytes(private_manifest)
    top_manifest = {
        "schema_version": SCHEMA_VERSION,
        "visibility": "CONTROL_PLANE_ONLY_NOT_POLICY_INPUT",
        "final_status": STATIC_STATUS,
        "frozen_identity": {
            "catalog_file_sha256": EXPECTED_CATALOG_FILE_SHA256,
            "scenario_payload_sha256": EXPECTED_SCENARIO_PAYLOAD_SHA256,
            "scenario_definition_payload_sha256": EXPECTED_SCENARIO_DEFINITION_SHA256,
            "split_file_sha256": EXPECTED_SPLIT_FILE_SHA256,
            "freeze_id": catalog["freeze_id"],
        },
        "scenario_count": 24,
        "runtime_fixture_count": 24,
        "derived_route_count": 24,
        "stage6a_readiness": {
            "static_compilation_complete": True,
            "live_launch_ready": False,
            "test_split_consumed": False,
            "carla_launch_count": 0,
            "blocking_reason": "LIVE_BLUEPRINT_MAP_SEMANTIC_AND_NONLEAKAGE_RECEIPTS_MISSING",
        },
        "source_split_binding": {
            name: copy.deepcopy(split_doc["splits"][name]["scenario_ids"])
            for name in ("train", "dev", "test")
        },
        "scenario_class_registration": {
            "type": ROUTE_SCENARIO_TYPE,
            "source_path": "driveclarify_paper_mvp_scenarios/scenario_runner_scenario.py",
            "registered_in_live_scenario_runner": False,
            "registration_receipt_required": True,
        },
        "records": top_records,
    }
    documents[TOP_MANIFEST] = pretty_json_bytes(top_manifest)
    documents[SCHEMA_FILE] = pretty_json_bytes(execution_schema())
    artifact_rows = {
        name: {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        for name, payload in sorted(documents.items())
    }
    hash_manifest = {
        "schema_version": "driveclarify.paper_mvp_stage6a_hash_manifest.v0",
        "hash_algorithm": "SHA-256",
        "self_hash_excluded": True,
        "artifact_count": len(artifact_rows),
        "artifacts": artifact_rows,
    }
    documents[HASH_MANIFEST] = pretty_json_bytes(hash_manifest)
    return documents


def compile_scenarios(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Compile and atomically write all 24 additive fixture records."""

    target = Path(output_dir)
    documents = _compile_documents()
    for relative, payload in sorted(documents.items()):
        _atomic_write(target / relative, payload)
    return {
        "status": STATIC_STATUS,
        "output_dir": str(target.resolve()),
        "artifact_count": len(documents),
        "scenario_count": 24,
        "derived_route_count": 24,
        "runtime_fixture_count": 24,
        "live_launch_ready": False,
        "carla_launch_count": 0,
    }


def expected_documents() -> dict[str, bytes]:
    """Return deterministic expected bytes without writing to disk."""

    return _compile_documents()
