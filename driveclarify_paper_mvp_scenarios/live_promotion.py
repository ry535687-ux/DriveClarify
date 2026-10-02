"""Lazy-CARLA physical promotion of the 24 static Stage 6A fixtures.

The live path reads only opaque runtime-visible manifests and their derived
route XML.  It never opens the frozen catalog or evaluator-private manifest.
CARLA is imported only inside :func:`connect_existing_server`.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .compiler import DEFAULT_OUTPUT_DIR
from .contracts import (
    RUNTIME_SCHEMA_VERSION,
    assert_finite,
    canonical_sha256,
    file_sha256,
    load_json,
    pretty_json_bytes,
    require,
    walk_keys,
)
from .live_contracts import (
    DEFAULT_LIVE_PROMOTION_DIR,
    LIVE_EVIDENCE_ORIGIN,
    LIVE_MANIFEST_SCHEMA_VERSION,
    LIVE_PROMOTION_MANIFEST,
    LIVE_RECEIPT_DIR,
    LIVE_RECEIPT_SCHEMA_VERSION,
    MANIFEST_HASH_FIELD,
    RECEIPT_HASH_FIELD,
    REALIZATION_MODES,
    RUNTIME_FORBIDDEN_KEYS,
    SEMANTIC_ATTESTATION_SCHEMA_VERSION,
    TEST_DOUBLE_EVIDENCE_ORIGIN,
    content_address,
    verify_content_address,
)


class LivePromotionError(RuntimeError):
    """Fail-closed live promotion error with a stable reason code."""


@dataclass(frozen=True)
class LivePromotionConfig:
    runtime_root: Path = DEFAULT_OUTPUT_DIR
    output_dir: Path = DEFAULT_LIVE_PROMOTION_DIR
    host: str = "127.0.0.1"
    port: int = 2000
    timeout_seconds: float = 20.0
    allow_world_load: bool = False
    navmesh_samples: int = 64
    maximum_vehicle_snap_distance_m: float = 8.0
    maximum_walker_snap_distance_m: float = 20.0
    semantic_attestation_path: Path | None = None
    selected_towns: tuple[str, ...] = ()


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


def connect_existing_server(
    config: LivePromotionConfig,
    *,
    carla_module: Any | None = None,
) -> tuple[Any, Any]:
    """Import CARLA lazily and connect; this function never starts a server."""

    carla = carla_module or importlib.import_module("carla")
    client = carla.Client(config.host, int(config.port))
    client.set_timeout(float(config.timeout_seconds))
    try:
        world = client.get_world()
        client.get_server_version()
    except Exception as exc:  # CARLA exceptions differ across releases.
        raise LivePromotionError(
            f"CARLA_EXISTING_SERVER_CONNECTION_FAILED:{config.host}:{config.port}:{exc}"
        ) from exc
    return client, world


def _runtime_inputs(runtime_root: Path) -> list[dict[str, Any]]:
    visible_dir = runtime_root / "runtime_visible"
    require(visible_dir.is_dir(), f"RUNTIME_VISIBLE_DIRECTORY_MISSING:{visible_dir}")
    paths = sorted(visible_dir.glob("dc-runtime-*.json"))
    require(len(paths) == 24, f"RUNTIME_VISIBLE_COUNT_INVALID:{len(paths)}")
    result: list[dict[str, Any]] = []
    fixture_ids: set[str] = set()
    for path in paths:
        runtime = load_json(path)
        require(runtime.get("schema_version") == RUNTIME_SCHEMA_VERSION, "LIVE_RUNTIME_SCHEMA_INVALID")
        runtime_id = str(runtime.get("runtime_fixture_id", ""))
        require(runtime_id and runtime_id not in fixture_ids, f"LIVE_RUNTIME_ID_INVALID:{runtime_id}")
        require(path.stem == runtime_id, f"LIVE_RUNTIME_FILENAME_BINDING_INVALID:{runtime_id}")
        fixture_ids.add(runtime_id)
        overlap = sorted(set(walk_keys(runtime)) & RUNTIME_FORBIDDEN_KEYS)
        require(not overlap, "LIVE_RUNTIME_FORBIDDEN_KEY:" + ",".join(overlap))
        seeds = runtime.get("allowed_seed_values")
        require(
            isinstance(seeds, list)
            and len(seeds) == 4
            and len(set(seeds)) == 4
            and all(isinstance(seed, int) and not isinstance(seed, bool) for seed in seeds),
            f"LIVE_RUNTIME_SEEDS_INVALID:{runtime_id}",
        )
        require(
            runtime.get("launch_gate", {}).get("launch_ready") is False,
            f"LIVE_INPUT_MUST_BE_STATIC_GATED:{runtime_id}",
        )
        route_relative = runtime.get("route_binding", {}).get("derived_route_path")
        require(isinstance(route_relative, str), f"LIVE_ROUTE_PATH_INVALID:{runtime_id}")
        route_path = (runtime_root / route_relative).resolve()
        try:
            route_path.relative_to(runtime_root.resolve())
        except ValueError as exc:
            raise LivePromotionError(f"LIVE_ROUTE_PATH_ESCAPE:{runtime_id}") from exc
        require(route_path.is_file(), f"LIVE_ROUTE_MISSING:{runtime_id}")
        route = ET.parse(route_path).getroot().find("route")
        require(route is not None, f"LIVE_ROUTE_ELEMENT_MISSING:{runtime_id}")
        scenarios = route.findall("./scenarios/scenario")
        require(len(scenarios) == 1, f"LIVE_ROUTE_SCENARIO_COUNT_INVALID:{runtime_id}")
        fixture = scenarios[0].find("fixture")
        require(fixture is not None, f"LIVE_ROUTE_FIXTURE_BINDING_MISSING:{runtime_id}")
        runtime_sha256 = file_sha256(path)
        require(
            fixture.get("runtime_fixture_id") == runtime_id
            and fixture.get("runtime_manifest_sha256") == runtime_sha256,
            f"LIVE_ROUTE_FIXTURE_BINDING_INVALID:{runtime_id}",
        )
        require(
            route.get("town") == runtime.get("route_binding", {}).get("town"),
            f"LIVE_ROUTE_TOWN_BINDING_INVALID:{runtime_id}",
        )
        assert_finite(runtime)
        result.append(
            {
                "path": path,
                "runtime": runtime,
                "runtime_sha256": runtime_sha256,
                "route_path": route_path,
                "route_sha256": file_sha256(route_path),
            }
        )
    return result


def _load_semantic_attestations(path: Path | None) -> tuple[dict[tuple[str, str], dict[str, Any]], str | None]:
    if path is None:
        return {}, None
    document = load_json(path)
    require(
        document.get("schema_version") == SEMANTIC_ATTESTATION_SCHEMA_VERSION,
        "SEMANTIC_ATTESTATION_SCHEMA_INVALID",
    )
    require(
        verify_content_address(document, "attestation_payload_sha256"),
        "SEMANTIC_ATTESTATION_CONTENT_HASH_INVALID",
    )
    overlap = sorted(set(walk_keys(document)) & RUNTIME_FORBIDDEN_KEYS)
    require(not overlap, "SEMANTIC_ATTESTATION_FORBIDDEN_KEY:" + ",".join(overlap))
    records = document.get("attestations")
    require(isinstance(records, list), "SEMANTIC_ATTESTATIONS_NOT_LIST")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        require(isinstance(record, Mapping), "SEMANTIC_ATTESTATION_NOT_OBJECT")
        runtime_id = str(record.get("runtime_fixture_id", ""))
        binding_id = str(record.get("binding_id", ""))
        status = record.get("status")
        mode = record.get("realization_mode")
        require(status in {"VERIFIED", "BLOCKED"}, "SEMANTIC_ATTESTATION_STATUS_INVALID")
        require(mode in REALIZATION_MODES - {"NOT_APPLICABLE"}, "SEMANTIC_ATTESTATION_MODE_INVALID")
        require(
            isinstance(record.get("semantic_class"), str)
            and isinstance(record.get("evidence_kind"), str)
            and isinstance(record.get("evidence_reference"), str)
            and bool(record["evidence_reference"]),
            "SEMANTIC_ATTESTATION_EVIDENCE_INVALID",
        )
        key = (runtime_id, binding_id)
        require(key not in result and all(key), "SEMANTIC_ATTESTATION_KEY_INVALID")
        result[key] = copy.deepcopy(dict(record))
    return result, file_sha256(path)


def _plain_transform(value: Mapping[str, Any]) -> dict[str, float]:
    result = {key: float(value[key]) for key in ("x", "y", "z", "yaw", "pitch", "roll")}
    require(all(math.isfinite(item) for item in result.values()), "LIVE_TRANSFORM_NONFINITE")
    return {key: round(item, 9) for key, item in result.items()}


def _carla_transform(carla: Any, value: Mapping[str, Any]) -> Any:
    return carla.Transform(
        carla.Location(x=float(value["x"]), y=float(value["y"]), z=float(value["z"])),
        carla.Rotation(
            yaw=float(value["yaw"]),
            pitch=float(value.get("pitch", 0.0)),
            roll=float(value.get("roll", 0.0)),
        ),
    )


def _from_carla_transform(value: Any) -> dict[str, float]:
    return {
        "x": round(float(value.location.x), 9),
        "y": round(float(value.location.y), 9),
        "z": round(float(value.location.z), 9),
        "yaw": round(float(value.rotation.yaw), 9),
        "pitch": round(float(value.rotation.pitch), 9),
        "roll": round(float(value.rotation.roll), 9),
    }


def _distance(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    return math.sqrt(
        sum((float(first[key]) - float(second[key])) ** 2 for key in ("x", "y", "z"))
    )


def _map_name(value: str) -> str:
    result = value.rsplit("/", 1)[-1]
    return result[:-4] if result.endswith("_Opt") else result


def _actor_bbox(actor: Any) -> dict[str, Any] | None:
    bbox = getattr(actor, "bounding_box", None)
    if bbox is None:
        return None
    location = getattr(bbox, "location", None)
    extent = getattr(bbox, "extent", None)
    rotation = getattr(bbox, "rotation", None)
    if location is None or extent is None:
        return None
    return {
        "location": {
            "x": float(location.x),
            "y": float(location.y),
            "z": float(location.z),
        },
        "extent": {
            "x": float(extent.x),
            "y": float(extent.y),
            "z": float(extent.z),
        },
        "rotation": {
            "pitch": float(getattr(rotation, "pitch", 0.0)),
            "yaw": float(getattr(rotation, "yaw", 0.0)),
            "roll": float(getattr(rotation, "roll", 0.0)),
        },
    }


def _blueprint_attribute(blueprint: Any, name: str) -> tuple[str | None, list[str]]:
    if not hasattr(blueprint, "has_attribute") or not blueprint.has_attribute(name):
        return None, []
    attribute = blueprint.get_attribute(name)
    recommendations = [str(value) for value in getattr(attribute, "recommended_values", [])]
    return str(attribute), recommendations


def _resolve_blueprint(
    library: Any,
    selector: Mapping[str, Any],
    *,
    runtime_id: str,
    binding_id: str,
    seed: int,
    vary_by_seed: bool,
) -> dict[str, Any]:
    filters = [selector.get("primary_filter"), *selector.get("fallback_filters", [])]
    chosen_filter = None
    candidates: list[Any] = []
    for pattern in filters:
        if not isinstance(pattern, str) or not pattern:
            continue
        candidates = sorted(list(library.filter(pattern)), key=lambda item: str(item.id))
        if candidates:
            chosen_filter = pattern
            break
    if not candidates:
        return {
            "resolved": False,
            "resolved_blueprint_id": None,
            "matched_filter": None,
            "candidate_count": 0,
            "candidate_ids_sha256": canonical_sha256([]),
            "attribute_constraint_results": {},
            "applied_attributes": {},
            "failure_reason": "NO_BLUEPRINT_MATCHED_SELECTOR_OR_FALLBACKS",
        }
    constraints = selector.get("attribute_constraints", {})

    def score(blueprint: Any) -> tuple[int, str]:
        points = 0
        base_type = constraints.get("base_type") if isinstance(constraints, Mapping) else None
        if base_type is not None:
            actual, _ = _blueprint_attribute(blueprint, "base_type")
            if actual == str(base_type):
                points += 10
        color = constraints.get("color_preference") if isinstance(constraints, Mapping) else None
        if color is not None:
            _, recommendations = _blueprint_attribute(blueprint, "color")
            if str(color) in recommendations:
                points += 4
        salt_seed = seed if vary_by_seed else 0
        tie = hashlib.sha256(
            f"{runtime_id}:{binding_id}:{salt_seed}:{blueprint.id}".encode("utf-8")
        ).hexdigest()
        return (-points, tie)

    candidates.sort(key=score)
    blueprint = candidates[0]
    applied: dict[str, str] = {}
    assessments: dict[str, Any] = {}
    if isinstance(constraints, Mapping):
        for key, expected in constraints.items():
            if key == "semantic_hint":
                assessments[key] = {
                    "expected": str(expected),
                    "status": "ADVISORY_ONLY_NOT_A_BLUEPRINT_ATTRIBUTE_PROOF",
                }
                continue
            attribute_name = "color" if key == "color_preference" else str(key)
            actual, recommendations = _blueprint_attribute(blueprint, attribute_name)
            match = actual == str(expected) or str(expected) in recommendations
            assessments[key] = {
                "expected": str(expected),
                "actual": actual,
                "recommended_values": recommendations,
                "matched": match,
            }
            if key == "color_preference" and recommendations:
                chosen_color = (
                    str(expected)
                    if str(expected) in recommendations
                    else recommendations[
                        int(
                            hashlib.sha256(
                                f"{runtime_id}:{binding_id}:{seed}:color".encode("utf-8")
                            ).hexdigest(),
                            16,
                        )
                        % len(recommendations)
                    ]
                )
                applied["color"] = chosen_color
    return {
        "resolved": True,
        "resolved_blueprint_id": str(blueprint.id),
        "matched_filter": chosen_filter,
        "candidate_count": len(candidates),
        "candidate_ids_sha256": canonical_sha256([str(item.id) for item in candidates]),
        "attribute_constraint_results": assessments,
        "applied_attributes": applied,
        "failure_reason": None,
    }


def _snap_transform(
    world: Any,
    carla: Any,
    requested: Mapping[str, Any],
    kind: str,
    *,
    seed: int,
    navmesh_samples: int,
    maximum_vehicle_distance: float,
    maximum_walker_distance: float,
) -> dict[str, Any]:
    requested_plain = _plain_transform(requested)
    world_map = world.get_map()
    location = carla.Location(
        x=requested_plain["x"], y=requested_plain["y"], z=requested_plain["z"]
    )
    if kind == "background_pedestrian":
        if hasattr(world, "set_pedestrians_seed"):
            world.set_pedestrians_seed(int(seed))
        locations = []
        for _ in range(int(navmesh_samples)):
            candidate = world.get_random_location_from_navigation()
            if candidate is not None:
                locations.append(candidate)
        if not locations:
            return {
                "usable": False,
                "method": "CARLA_NAVMESH_NEAREST_OF_SEEDED_SAMPLES",
                "sample_count": 0,
                "distance_m": None,
                "world_transform": None,
                "failure_reason": "NAVMESH_RETURNED_NO_LOCATION",
            }
        chosen = min(
            locations,
            key=lambda item: math.sqrt(
                (float(item.x) - requested_plain["x"]) ** 2
                + (float(item.y) - requested_plain["y"]) ** 2
                + (float(item.z) - requested_plain["z"]) ** 2
            ),
        )
        snapped = dict(requested_plain)
        snapped.update(
            {
                "x": round(float(chosen.x), 9),
                "y": round(float(chosen.y), 9),
                "z": round(float(chosen.z) + 0.1, 9),
            }
        )
        distance_m = _distance(requested_plain, snapped)
        return {
            "usable": distance_m <= maximum_walker_distance,
            "method": "CARLA_NAVMESH_NEAREST_OF_SEEDED_SAMPLES",
            "sample_count": len(locations),
            "distance_m": round(distance_m, 9),
            "world_transform": snapped,
            "failure_reason": (
                None if distance_m <= maximum_walker_distance else "NAVMESH_SNAP_DISTANCE_EXCEEDED"
            ),
        }
    lane_type = None
    lane_enum = getattr(carla, "LaneType", None)
    if lane_enum is not None:
        lane_type = getattr(
            lane_enum,
            "Driving" if kind in {"ego", "dynamic_actor", "background_vehicle"} else "Any",
            None,
        )
    try:
        if lane_type is None:
            waypoint = world_map.get_waypoint(location, project_to_road=True)
        else:
            waypoint = world_map.get_waypoint(
                location, project_to_road=True, lane_type=lane_type
            )
    except TypeError:
        waypoint = world_map.get_waypoint(location, project_to_road=True)
    if waypoint is None:
        return {
            "usable": False,
            "method": "CARLA_MAP_PROJECT_TO_ROAD",
            "sample_count": 1,
            "distance_m": None,
            "world_transform": None,
            "failure_reason": "MAP_WAYPOINT_PROJECTION_FAILED",
        }
    snapped = _from_carla_transform(waypoint.transform)
    snapped["z"] = round(
        snapped["z"]
        + (0.25 if kind in {"ego", "dynamic_actor", "background_vehicle"} else 0.05),
        9,
    )
    distance_m = _distance(requested_plain, snapped)
    return {
        "usable": distance_m <= maximum_vehicle_distance,
        "method": "CARLA_MAP_PROJECT_TO_ROAD",
        "sample_count": 1,
        "distance_m": round(distance_m, 9),
        "world_transform": snapped,
        "failure_reason": (
            None if distance_m <= maximum_vehicle_distance else "MAP_SNAP_DISTANCE_EXCEEDED"
        ),
    }


def _binding_specs(runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = [
        {
            "binding_id": "ego",
            "kind": "ego",
            "semantic_class": "route_scenario_ego",
            "requested_transform": runtime["ego_spawn"]["world_transform"],
            "selector": runtime["ego_spawn"]["blueprint_selector"],
            "role_name": "hero",
            "physics_enabled": True,
            "autopilot_enabled": False,
            "handler_spawns_actor": False,
            "physical_spawn_required": True,
            "realization_contract": {"mode": "NOT_APPLICABLE"},
        }
    ]
    for entity in runtime["entities"]:
        kind = str(entity["kind"])
        realization = copy.deepcopy(entity.get("realization_contract", {}))
        composite = realization.get("mode") == "COMPOSITE_MEASURED_CLEARANCE_GATEWAY"
        result.append(
            {
                "binding_id": str(entity["entity_id"]),
                "kind": kind,
                "semantic_class": str(entity["semantic_class"]),
                "requested_transform": entity["geometric_binding"]["world_transform"],
                "selector": entity["blueprint_selector"],
                "role_name": str(entity["spawn_state"]["role_name"]),
                "physics_enabled": bool(entity["spawn_state"]["physics_enabled"]),
                "autopilot_enabled": bool(entity["spawn_state"]["autopilot_enabled"]),
                "handler_spawns_actor": kind == "dynamic_actor" or composite,
                "physical_spawn_required": kind == "dynamic_actor" or composite,
                "realization_contract": realization,
            }
        )
    for slot in runtime["background"]["spawn_slots"]:
        kind = str(slot["kind"])
        result.append(
            {
                "binding_id": str(slot["slot_id"]),
                "kind": kind,
                "semantic_class": "nuisance_background",
                "requested_transform": slot["world_transform"],
                "selector": slot["blueprint_selector"],
                "role_name": "driveclarify_" + str(slot["slot_id"]).lower(),
                "physics_enabled": True,
                "autopilot_enabled": kind == "background_vehicle",
                "handler_spawns_actor": True,
                "physical_spawn_required": True,
                "realization_contract": {"mode": "NOT_APPLICABLE"},
            }
        )
    # Required actors first; physical marker proxies last so a proxy collision
    # cannot mask whether the actual handler-owned actors are spawnable.
    return sorted(
        result,
        key=lambda item: (
            not item["physical_spawn_required"],
            item["kind"] == "semantic_marker",
            item["binding_id"],
        ),
    )


def _semantic_fidelity(
    spec: Mapping[str, Any],
    runtime_id: str,
    attestations: Mapping[tuple[str, str], Mapping[str, Any]],
    attestation_sha256: str | None,
) -> dict[str, Any]:
    if spec["kind"] in {"ego", "background_vehicle", "background_pedestrian"}:
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
    key = (runtime_id, str(spec["binding_id"]))
    attestation = attestations.get(key)
    if spec["realization_contract"].get("mode") == "COMPOSITE_MEASURED_CLEARANCE_GATEWAY":
        return {
            "status": (
                "BLOCKED"
                if attestation is not None and attestation.get("status") == "BLOCKED"
                else "UNKNOWN"
            ),
            "realization_mode": "COMPOSITE_MEASURED_CLEARANCE_GATEWAY",
            "evidence_kind": "PENDING_COMPOSITE_BBOX_AND_COLLISION_SWEEP",
            "evidence_reference": (
                attestation.get("evidence_reference") if attestation is not None else None
            ),
            "attestation_file_sha256": (
                attestation_sha256 if attestation is not None else None
            ),
            "external_self_attestation_used_as_gate": False,
            "promotion_authority": "NONE",
            "reason": (
                "A_SEMANTIC_ATTESTATION_ALONE_CANNOT_REPLACE_PAIRWISE_CLEARANCE_"
                "MEASUREMENT"
            ),
        }
    if attestation is not None:
        require(
            attestation.get("semantic_class") == spec["semantic_class"],
            f"SEMANTIC_ATTESTATION_CLASS_MISMATCH:{runtime_id}:{spec['binding_id']}",
        )
        return {
            # A self-authored, content-addressed assertion is useful diagnostic
            # evidence, but it is not an authenticated live-world verifier.
            # Preserve the claim without ever allowing it to open the formal
            # promotion gate by itself.
            "status": (
                "BLOCKED" if attestation["status"] == "BLOCKED" else "UNKNOWN"
            ),
            "attested_status": str(attestation["status"]),
            "realization_mode": str(attestation["realization_mode"]),
            "evidence_kind": str(attestation["evidence_kind"]),
            "evidence_reference": str(attestation["evidence_reference"]),
            "attestation_file_sha256": attestation_sha256,
            "external_self_attestation_used_as_gate": False,
            "promotion_authority": "EXTERNAL_SELF_ATTESTATION_NON_PROMOTING",
            "reason": (
                "EXTERNAL_SELF_ATTESTATION_RECORDED_BUT_NOT_AUTHORIZED_TO_PROMOTE"
            ),
        }
    return {
        "status": "UNKNOWN" if spec["kind"] == "dynamic_actor" else "BLOCKED",
        "realization_mode": (
            "SPAWNED_BLUEPRINT_ACTOR"
            if spec["kind"] == "dynamic_actor"
            else "EXISTING_WORLD_FEATURE"
        ),
        "evidence_kind": "NO_SEMANTIC_ATTESTATION",
        "evidence_reference": None,
        "attestation_file_sha256": None,
        "external_self_attestation_used_as_gate": False,
        "promotion_authority": "NONE",
        "reason": (
            "BLUEPRINT_EXISTENCE_AND_SPAWNABILITY_DO_NOT_PROVE_CATALOG_SEMANTICS"
        ),
    }


def _apply_blueprint_attributes(blueprint: Any, attributes: Mapping[str, str], role_name: str) -> dict[str, str]:
    applied: dict[str, str] = {}
    desired = dict(attributes)
    desired["role_name"] = role_name
    if hasattr(blueprint, "has_attribute") and blueprint.has_attribute("is_invincible"):
        desired.setdefault("is_invincible", "false")
    for key, value in desired.items():
        if hasattr(blueprint, "has_attribute") and blueprint.has_attribute(key):
            blueprint.set_attribute(key, str(value))
            applied[key] = str(value)
    return applied


def _existing_dynamic_actor_ids(world: Any) -> list[int]:
    actors = world.get_actors()
    result = []
    for actor in actors:
        type_id = str(getattr(actor, "type_id", ""))
        if (
            bool(getattr(actor, "is_alive", True))
            and (
                type_id.startswith("vehicle.")
                or type_id.startswith("walker.pedestrian.")
            )
        ):
            result.append(int(actor.id))
    return sorted(result)


def _probe_one_configuration(
    runtime_input: Mapping[str, Any],
    seed: int,
    world: Any,
    client: Any,
    carla: Any,
    config: LivePromotionConfig,
    attestations: Mapping[tuple[str, str], Mapping[str, Any]],
    attestation_sha256: str | None,
    evidence_origin: str,
) -> dict[str, Any]:
    runtime = runtime_input["runtime"]
    runtime_id = str(runtime["runtime_fixture_id"])
    world_map = world.get_map()
    actual_town = _map_name(str(world_map.name))
    expected_town = _map_name(str(runtime["route_binding"]["town"]))
    require(actual_town == expected_town, f"LIVE_WORLD_TOWN_MISMATCH:{actual_town}:{expected_town}")
    existing = _existing_dynamic_actor_ids(world)
    require(not existing, "LIVE_WORLD_NOT_EMPTY_DYNAMIC_ACTORS:" + ",".join(map(str, existing)))
    library = world.get_blueprint_library()
    blueprint_ids = sorted(str(item.id) for item in library)
    map_opendrive = world_map.to_opendrive() if hasattr(world_map, "to_opendrive") else str(world_map.name)
    prepared: list[dict[str, Any]] = []
    for spec in _binding_specs(runtime):
        resolution = _resolve_blueprint(
            library,
            spec["selector"],
            runtime_id=runtime_id,
            binding_id=str(spec["binding_id"]),
            seed=int(seed),
            vary_by_seed=spec["kind"] in {"background_vehicle", "background_pedestrian"},
        )
        snap = _snap_transform(
            world,
            carla,
            spec["requested_transform"],
            str(spec["kind"]),
            seed=int(seed),
            navmesh_samples=config.navmesh_samples,
            maximum_vehicle_distance=config.maximum_vehicle_snap_distance_m,
            maximum_walker_distance=config.maximum_walker_snap_distance_m,
        )
        prepared.append(
            {
                "binding_id": spec["binding_id"],
                "kind": spec["kind"],
                "semantic_class": spec["semantic_class"],
                "requested_world_transform": _plain_transform(spec["requested_transform"]),
                "snap": snap,
                "blueprint_resolution": resolution,
                "role_name": spec["role_name"],
                "physics_enabled": spec["physics_enabled"],
                "autopilot_enabled": spec["autopilot_enabled"],
                "handler_spawns_actor": spec["handler_spawns_actor"],
                "physical_spawn_required": spec["physical_spawn_required"],
                "realization_contract": spec["realization_contract"],
                "semantic_fidelity": _semantic_fidelity(
                    spec, runtime_id, attestations, attestation_sha256
                ),
                "spawn_probe": {
                    "attempted": False,
                    "spawned": False,
                    "destroyed": False,
                    "actor_type_id": None,
                    "actual_world_transform": None,
                    "actor_bounding_box": None,
                    "applied_attributes": {},
                    "failure_reason": None,
                },
                "physical_spawn_verified": False,
            }
        )
    live_actors: list[tuple[Any, dict[str, Any]]] = []
    for record in prepared:
        resolution = record["blueprint_resolution"]
        snap = record["snap"]
        probe = record["spawn_probe"]
        if (
            record["realization_contract"].get("mode")
            == "COMPOSITE_MEASURED_CLEARANCE_GATEWAY"
        ):
            probe["failure_reason"] = (
                "COMPOSITE_GATEWAY_COMPONENT_BBOX_AND_COLLISION_SWEEP_NOT_VERIFIED"
            )
            continue
        if not resolution["resolved"]:
            probe["failure_reason"] = "BLUEPRINT_NOT_RESOLVED"
            continue
        if not snap["usable"] or snap["world_transform"] is None:
            probe["failure_reason"] = "WORLD_TRANSFORM_NOT_USABLE"
            continue
        probe["attempted"] = True
        try:
            blueprint = library.find(resolution["resolved_blueprint_id"])
            applied = _apply_blueprint_attributes(
                blueprint,
                resolution["applied_attributes"],
                str(record["role_name"]),
            )
            actor = world.try_spawn_actor(
                blueprint, _carla_transform(carla, snap["world_transform"])
            )
            if actor is None:
                probe["failure_reason"] = "CARLA_TRY_SPAWN_RETURNED_NONE"
                continue
            probe["spawned"] = True
            # Register ownership immediately.  Any later transform/bbox/physics
            # exception must still flow through the joint cleanup loop.
            live_actors.append((actor, record))
            probe["actor_type_id"] = str(actor.type_id)
            probe["actual_world_transform"] = _from_carla_transform(actor.get_transform())
            probe["actor_bounding_box"] = _actor_bbox(actor)
            probe["applied_attributes"] = applied
            if hasattr(actor, "set_simulate_physics"):
                actor.set_simulate_physics(False)
        except Exception as exc:
            probe["failure_reason"] = f"CARLA_SPAWN_EXCEPTION:{type(exc).__name__}:{exc}"
    if (
        evidence_origin == LIVE_EVIDENCE_ORIGIN
        and live_actors
        and hasattr(client, "apply_batch_sync")
        and hasattr(carla, "command")
    ):
        try:
            reversed_live = list(reversed(live_actors))
            responses = client.apply_batch_sync(
                [carla.command.DestroyActor(int(actor.id)) for actor, _ in reversed_live],
                True,
            )
            require(
                len(responses) == len(reversed_live),
                "CARLA_DESTROY_BATCH_RESPONSE_COUNT_INVALID",
            )
            for response, (_actor, record) in zip(responses, reversed_live):
                failed = bool(response.has_error())
                record["spawn_probe"]["destroyed"] = not failed
                if failed:
                    record["spawn_probe"]["failure_reason"] = (
                        "CARLA_DESTROY_BATCH_ERROR:" + str(response.error)
                    )
        except Exception as exc:
            for _actor, record in live_actors:
                record["spawn_probe"]["failure_reason"] = (
                    f"CARLA_DESTROY_EXCEPTION:{type(exc).__name__}:{exc}"
                )
    else:
        for actor, record in reversed(live_actors):
            try:
                destroyed = actor.destroy()
                record["spawn_probe"]["destroyed"] = destroyed is not False
                if destroyed is False:
                    record["spawn_probe"]["failure_reason"] = "CARLA_DESTROY_RETURNED_FALSE"
            except Exception as exc:
                record["spawn_probe"]["failure_reason"] = (
                    f"CARLA_DESTROY_EXCEPTION:{type(exc).__name__}:{exc}"
                )
    for record in prepared:
        record["physical_spawn_verified"] = bool(
            record["spawn_probe"]["spawned"] and record["spawn_probe"]["destroyed"]
        )
    required = [record for record in prepared if record["physical_spawn_required"]]
    physical_verified = bool(required) and all(
        record["physical_spawn_verified"] for record in required
    )
    semantic_records = [
        record
        for record in prepared
        if record["kind"] in {"dynamic_actor", "semantic_marker"}
    ]
    semantic_verified = bool(semantic_records) and all(
        record["semantic_fidelity"]["status"] == "VERIFIED"
        for record in semantic_records
    )
    handler_execution_verified = False
    live_evidence = evidence_origin == LIVE_EVIDENCE_ORIGIN
    promotion_ready = bool(
        physical_verified
        and semantic_verified
        and handler_execution_verified
        and live_evidence
    )
    if not live_evidence:
        final_status = "BLOCKED_TEST_DOUBLE_EVIDENCE"
    elif not physical_verified:
        final_status = "BLOCKED_LIVE_PHYSICAL_SPAWN"
    elif not semantic_verified:
        final_status = "BLOCKED_LIVE_SEMANTIC_FIDELITY"
    elif not handler_execution_verified:
        final_status = "BLOCKED_HANDLER_EXECUTION_NOT_VERIFIED"
    else:  # pragma: no cover - requires a separate real handler execution author.
        final_status = "PASS_LIVE_PROMOTION_READY"
    receipt_unsigned = {
        "schema_version": LIVE_RECEIPT_SCHEMA_VERSION,
        "provenance": {
            "evidence_origin": evidence_origin,
            "formal_promotion_eligible": live_evidence,
        },
        "input_scope": {
            "runtime_visible_manifest_read": True,
            "derived_route_xml_read": True,
            "frozen_catalog_read": False,
            "evaluator_private_manifest_read": False,
            "evaluation_label_access": False,
        },
        "runtime_fixture_id": runtime_id,
        "runtime_manifest_sha256": runtime_input["runtime_sha256"],
        "derived_route_sha256": runtime_input["route_sha256"],
        "selected_seed": int(seed),
        "server_identity": {
            "client_version": str(client.get_client_version()),
            "server_version": str(client.get_server_version()),
            "map_name": str(world_map.name),
            "opendrive_sha256": hashlib.sha256(map_opendrive.encode("utf-8")).hexdigest(),
            "blueprint_inventory_sha256": canonical_sha256(blueprint_ids),
            "no_rendering_mode": bool(getattr(world.get_settings(), "no_rendering_mode", False)),
        },
        "probe_contract": {
            "connected_to_preexisting_server": live_evidence,
            "server_started_by_tool": False,
            "world_load_permitted": bool(config.allow_world_load),
            "navmesh_samples": int(config.navmesh_samples),
            "spawn_probe": "JOINT_TRY_SPAWN_THEN_DESTROY_WITHOUT_WORLD_TICK",
            "existing_dynamic_actor_count": 0,
        },
        "actor_bindings": prepared,
        "joint_spawn_probe": {
            "required_binding_count": len(required),
            "required_spawned_count": sum(
                1 for record in required if record["spawn_probe"]["spawned"]
            ),
            "required_destroyed_count": sum(
                1 for record in required if record["spawn_probe"]["destroyed"]
            ),
            "all_required_physical_spawns_verified": physical_verified,
        },
        "physical_spawn_verified": physical_verified,
        "semantic_fidelity": {
            "status": "VERIFIED" if semantic_verified else "BLOCKED",
            "all_catalog_entities_verified": semantic_verified,
            "spawnability_used_as_semantic_proof": False,
            "external_self_attestation_used_as_gate": False,
            "unknown_binding_ids": [
                record["binding_id"]
                for record in semantic_records
                if record["semantic_fidelity"]["status"] != "VERIFIED"
            ],
        },
        "handler_execution_receipt": {
            "status": "MISSING",
            "verified": handler_execution_verified,
            "receipt_path": None,
            "receipt_payload_sha256": None,
        },
        "gate_receipts": {
            "live_blueprint_resolution": all(
                record["blueprint_resolution"]["resolved"] for record in required
            ),
            "live_map_navmesh_snap": all(record["snap"]["usable"] for record in required),
            "live_spawn_destroy": physical_verified,
            "live_semantic_scene_realization": semantic_verified,
            "scenario_class_registration": handler_execution_verified,
            "handler_spawn_tick_cleanup": handler_execution_verified,
            "route_and_goal_nonleakage": True,
        },
        "promotion_ready": promotion_ready,
        "final_status": final_status,
    }
    assert_finite(receipt_unsigned)
    receipt, _ = content_address(receipt_unsigned, RECEIPT_HASH_FIELD)
    return receipt


def _ensure_town(
    client: Any,
    world: Any,
    expected_town: str,
    allow_world_load: bool,
) -> Any:
    if _map_name(str(world.get_map().name)) == _map_name(expected_town):
        return world
    if not allow_world_load:
        raise LivePromotionError(
            f"CARLA_WORLD_LOAD_REQUIRED:{world.get_map().name}:{expected_town}"
        )
    load_name = "Town10HD_Opt" if _map_name(expected_town) == "Town10HD" else expected_town
    try:
        loaded = client.load_world(load_name)
    except Exception as exc:
        raise LivePromotionError(f"CARLA_WORLD_LOAD_FAILED:{load_name}:{exc}") from exc
    if _map_name(str(loaded.get_map().name)) != _map_name(expected_town):
        raise LivePromotionError(
            f"CARLA_WORLD_LOAD_RETURNED_WRONG_MAP:{loaded.get_map().name}:{expected_town}"
        )
    return loaded


def run_live_promotion(
    config: LivePromotionConfig,
    *,
    client: Any | None = None,
    world: Any | None = None,
    carla_module: Any | None = None,
) -> dict[str, Any]:
    """Probe all requested runtime/seed pairs on an already-running server."""

    require(config.navmesh_samples > 0, "LIVE_NAVMESH_SAMPLE_COUNT_INVALID")
    injected_runtime = client is not None or world is not None
    require(
        (client is None) == (world is None),
        "LIVE_INJECTED_CLIENT_WORLD_MUST_BE_PAIRED",
    )
    evidence_origin = (
        TEST_DOUBLE_EVIDENCE_ORIGIN if injected_runtime else LIVE_EVIDENCE_ORIGIN
    )
    runtime_inputs = _runtime_inputs(Path(config.runtime_root))
    if config.selected_towns:
        selected = {_map_name(value) for value in config.selected_towns}
        runtime_inputs = [
            item
            for item in runtime_inputs
            if _map_name(item["runtime"]["route_binding"]["town"]) in selected
        ]
        require(bool(runtime_inputs), "LIVE_SELECTED_TOWNS_MATCH_NO_RUNTIME")
    attestations, attestation_sha256 = _load_semantic_attestations(
        config.semantic_attestation_path
    )
    if client is None:
        client, connected_world = connect_existing_server(
            config, carla_module=carla_module
        )
        world = connected_world
    else:
        require(world is not None, "LIVE_INJECTED_CLIENT_REQUIRES_WORLD")
    if carla_module is None:
        carla_module = importlib.import_module("carla")
    records: list[dict[str, Any]] = []
    output = Path(config.output_dir)
    current_world = world
    for runtime_input in sorted(
        runtime_inputs,
        key=lambda item: (
            str(item["runtime"]["route_binding"]["town"]),
            str(item["runtime"]["runtime_fixture_id"]),
        ),
    ):
        runtime = runtime_input["runtime"]
        town = str(runtime["route_binding"]["town"])
        current_world = _ensure_town(
            client, current_world, town, config.allow_world_load
        )
        for seed in runtime["allowed_seed_values"]:
            receipt = _probe_one_configuration(
                runtime_input,
                int(seed),
                current_world,
                client,
                carla_module,
                config,
                attestations,
                attestation_sha256,
                evidence_origin,
            )
            receipt_hash = receipt[RECEIPT_HASH_FIELD]
            relative = f"{LIVE_RECEIPT_DIR}/{receipt_hash}.json"
            _atomic_write(output / relative, pretty_json_bytes(receipt))
            records.append(
                {
                    "runtime_fixture_id": receipt["runtime_fixture_id"],
                    "runtime_manifest_sha256": receipt["runtime_manifest_sha256"],
                    "selected_seed": receipt["selected_seed"],
                    "receipt_path": relative,
                    "receipt_payload_sha256": receipt_hash,
                    "evidence_origin": receipt["provenance"]["evidence_origin"],
                    "physical_spawn_verified": receipt["physical_spawn_verified"],
                    "semantic_fidelity_status": receipt["semantic_fidelity"]["status"],
                    "promotion_ready": receipt["promotion_ready"],
                    "final_status": receipt["final_status"],
                }
            )
    expected_count = len(runtime_inputs) * 4
    require(len(records) == expected_count, "LIVE_RECEIPT_COUNT_INTERNAL_MISMATCH")
    physical_count = sum(1 for record in records if record["physical_spawn_verified"])
    semantic_count = sum(
        1 for record in records if record["semantic_fidelity_status"] == "VERIFIED"
    )
    ready_count = sum(1 for record in records if record["promotion_ready"])
    live_origin_count = sum(
        1 for record in records if record["evidence_origin"] == LIVE_EVIDENCE_ORIGIN
    )
    test_double_count = sum(
        1
        for record in records
        if record["evidence_origin"] == TEST_DOUBLE_EVIDENCE_ORIGIN
    )
    manifest_unsigned = {
        "schema_version": LIVE_MANIFEST_SCHEMA_VERSION,
        "input_scope": {
            "runtime_visible_directory": "runtime_visible",
            "frozen_catalog_read": False,
            "evaluator_private_manifest_read": False,
            "evaluation_label_access": False,
        },
        "receipt_store": {
            "content_addressed": True,
            "hash_algorithm": "SHA-256_CANONICAL_JSON_UNSIGNED_PAYLOAD",
            "receipt_directory": LIVE_RECEIPT_DIR,
        },
        "runtime_fixture_count": len(runtime_inputs),
        "seed_configuration_count": expected_count,
        "records": sorted(
            records,
            key=lambda item: (item["runtime_fixture_id"], item["selected_seed"]),
        ),
        "summary": {
            "physical_spawn_verified_count": physical_count,
            "semantic_fidelity_verified_count": semantic_count,
            "promotion_ready_count": ready_count,
            "live_evidence_origin_count": live_origin_count,
            "test_double_evidence_count": test_double_count,
            "all_physical_spawn_verified": physical_count == expected_count,
            "all_semantic_fidelity_verified": semantic_count == expected_count,
            "all_promotion_ready": ready_count == expected_count,
            "all_receipts_live_evidence": live_origin_count == expected_count,
            "carla_server_started_by_tool": False,
        },
        "final_status": (
            "PASS_LIVE_PROMOTION_ALL_CONFIGURATIONS_READY"
            if ready_count == expected_count
            else "BLOCKED_LIVE_PROMOTION_RECEIPTS_NOT_ALL_READY"
        ),
    }
    manifest, _ = content_address(manifest_unsigned, MANIFEST_HASH_FIELD)
    _atomic_write(output / LIVE_PROMOTION_MANIFEST, pretty_json_bytes(manifest))
    return manifest
