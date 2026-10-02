"""Fail-closed, CARLA-free validation for generated Stage 6A fixtures."""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping

from .compiler import (
    DEFAULT_OUTPUT_DIR,
    HASH_MANIFEST,
    PRIVATE_MANIFEST,
    ROUTE_DIR,
    SCHEMA_FILE,
    TOP_MANIFEST,
    expected_documents,
)
from .contracts import (
    CATALOG_PATH,
    EXPECTED_CATALOG_FILE_SHA256,
    EXPECTED_SCENARIO_DEFINITION_SHA256,
    EXPECTED_SCENARIO_PAYLOAD_SHA256,
    EXPECTED_SPLIT_FILE_SHA256,
    PRIVATE_SCHEMA_VERSION,
    ROOT,
    ROUTE_SCENARIO_TYPE,
    RUNTIME_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SPLIT_PATH,
    STATIC_STATUS,
    ScenarioFixtureContractError,
    assert_finite,
    file_sha256,
    load_json,
    walk_keys,
)
from .live_contracts import (
    HANDLER_EXECUTION_RECEIPT_SCHEMA_VERSION,
    HANDLER_RECEIPT_HASH_FIELD,
    LIVE_EVIDENCE_ORIGIN,
    LIVE_MANIFEST_SCHEMA_VERSION,
    LIVE_PROMOTION_MANIFEST,
    LIVE_RECEIPT_SCHEMA_VERSION,
    MANIFEST_HASH_FIELD,
    RECEIPT_HASH_FIELD,
    RUNTIME_FORBIDDEN_KEYS,
    SEMANTIC_FIDELITY_STATUSES,
    TEST_DOUBLE_EVIDENCE_ORIGIN,
    verify_content_address,
)


class ScenarioFixtureValidationError(RuntimeError):
    """A generated fixture violates the frozen static contract."""


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise ScenarioFixtureValidationError(reason)


def _safe_artifact(root: Path, relative: Any) -> Path:
    _check(isinstance(relative, str) and bool(relative), "ARTIFACT_PATH_INVALID")
    value = Path(relative)
    _check(not value.is_absolute(), f"ARTIFACT_PATH_ABSOLUTE:{relative}")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ScenarioFixtureValidationError(
            f"ARTIFACT_PATH_ESCAPES_OUTPUT:{relative}"
        ) from exc
    _check(candidate.is_file() and not candidate.is_symlink(), f"ARTIFACT_MISSING:{relative}")
    return candidate


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _transform(value: Any, context: str) -> None:
    _check(isinstance(value, Mapping), f"WORLD_TRANSFORM_NOT_OBJECT:{context}")
    _check(
        set(value) == {"x", "y", "z", "yaw", "pitch", "roll"},
        f"WORLD_TRANSFORM_KEYS_INVALID:{context}",
    )
    for key, number in value.items():
        _check(
            isinstance(number, (int, float))
            and not isinstance(number, bool)
            and math.isfinite(float(number)),
            f"WORLD_TRANSFORM_NONFINITE:{context}:{key}",
        )


def _vector3(value: Any, context: str) -> None:
    _check(isinstance(value, Mapping), f"VECTOR3_NOT_OBJECT:{context}")
    _check(set(value) == {"x", "y", "z"}, f"VECTOR3_KEYS_INVALID:{context}")
    for key, number in value.items():
        _check(
            isinstance(number, (int, float))
            and not isinstance(number, bool)
            and math.isfinite(float(number)),
            f"VECTOR3_NONFINITE:{context}:{key}",
        )


def _bounding_box(value: Any, context: str) -> None:
    _check(isinstance(value, Mapping), f"BOUNDING_BOX_NOT_OBJECT:{context}")
    _check(
        set(value) == {"location", "extent", "rotation"},
        f"BOUNDING_BOX_KEYS_INVALID:{context}",
    )
    _vector3(value["location"], context + ":location")
    _vector3(value["extent"], context + ":extent")
    rotation = value["rotation"]
    _check(isinstance(rotation, Mapping), f"BOUNDING_BOX_ROTATION_NOT_OBJECT:{context}")
    _check(
        set(rotation) == {"pitch", "yaw", "roll"},
        f"BOUNDING_BOX_ROTATION_KEYS_INVALID:{context}",
    )
    for key, number in rotation.items():
        _check(
            isinstance(number, (int, float))
            and not isinstance(number, bool)
            and math.isfinite(float(number)),
            f"BOUNDING_BOX_ROTATION_NONFINITE:{context}:{key}",
        )


def _map_name(value: Any) -> str:
    result = str(value).rsplit("/", 1)[-1]
    return result[:-4] if result.endswith("_Opt") else result


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _selector(value: Any, context: str) -> None:
    _check(isinstance(value, Mapping), f"BLUEPRINT_SELECTOR_NOT_OBJECT:{context}")
    _check(
        isinstance(value.get("primary_filter"), str) and bool(value["primary_filter"]),
        f"BLUEPRINT_PRIMARY_FILTER_MISSING:{context}",
    )
    fallbacks = value.get("fallback_filters")
    _check(
        isinstance(fallbacks, list)
        and bool(fallbacks)
        and all(isinstance(item, str) and item for item in fallbacks),
        f"BLUEPRINT_FALLBACK_FILTERS_MISSING:{context}",
    )
    _check(value.get("resolved_blueprint_id") is None, f"BLUEPRINT_STATICALLY_RESOLVED:{context}")
    _check(
        value.get("live_resolution_receipt_required") is True,
        f"BLUEPRINT_LIVE_RECEIPT_NOT_REQUIRED:{context}",
    )
    _check(
        value.get("static_compiler_claims_blueprint_exists") is False,
        f"BLUEPRINT_EXISTENCE_CLAIM_INVALID:{context}",
    )


def _xml_projection(element: ET.Element | None) -> Any:
    if element is None:
        return None
    return (
        element.tag,
        tuple(sorted(element.attrib.items())),
        tuple(_xml_projection(child) for child in list(element)),
    )


def _validate_route(
    output_root: Path,
    record: Mapping[str, Any],
    runtime: Mapping[str, Any],
    catalog_scenario: Mapping[str, Any],
) -> None:
    route_relative = record.get("derived_route_path")
    _check(
        isinstance(route_relative, str) and route_relative.startswith(f"{ROUTE_DIR}/dc-runtime-"),
        f"DERIVED_ROUTE_PATH_NOT_OPAQUE:{catalog_scenario['scenario_id']}",
    )
    route_path = _safe_artifact(output_root, route_relative)
    route_payload = route_path.read_bytes()
    _check(
        _sha256_bytes(route_payload) == record.get("derived_route_sha256"),
        f"DERIVED_ROUTE_SHA256_MISMATCH:{catalog_scenario['scenario_id']}",
    )
    try:
        derived_tree = ET.parse(route_path)
    except ET.ParseError as exc:
        raise ScenarioFixtureValidationError(
            f"DERIVED_ROUTE_XML_INVALID:{catalog_scenario['scenario_id']}"
        ) from exc
    derived_root = derived_tree.getroot()
    derived_routes = list(derived_root.findall("route"))
    _check(
        derived_root.tag == "routes" and len(derived_routes) == 1,
        f"DERIVED_ROUTE_COUNT_INVALID:{catalog_scenario['scenario_id']}",
    )
    derived_route = derived_routes[0]

    source_relative = catalog_scenario["source_route"]["fixture_path"]
    source_path = ROOT / source_relative
    _check(source_path.is_file(), f"SOURCE_ROUTE_MISSING:{catalog_scenario['scenario_id']}")
    _check(
        file_sha256(source_path) == catalog_scenario["source_route"]["fixture_sha256"],
        f"SOURCE_ROUTE_SHA256_MISMATCH:{catalog_scenario['scenario_id']}",
    )
    source_route = ET.parse(source_path).getroot().find("route")
    _check(source_route is not None, f"SOURCE_ROUTE_ELEMENT_MISSING:{catalog_scenario['scenario_id']}")
    _check(
        derived_route.attrib == source_route.attrib,
        f"DERIVED_ROUTE_ATTRIBUTES_CHANGED:{catalog_scenario['scenario_id']}",
    )
    _check(
        _xml_projection(derived_route.find("waypoints"))
        == _xml_projection(source_route.find("waypoints")),
        f"DERIVED_ROUTE_WAYPOINTS_CHANGED:{catalog_scenario['scenario_id']}",
    )
    _check(
        _xml_projection(derived_route.find("weathers"))
        == _xml_projection(source_route.find("weathers")),
        f"DERIVED_ROUTE_WEATHER_CHANGED:{catalog_scenario['scenario_id']}",
    )
    source_scenarios = source_route.find("scenarios")
    _check(
        source_scenarios is not None and len(list(source_scenarios)) == 0,
        f"SOURCE_ROUTE_NOT_SCENARIO_FREE:{catalog_scenario['scenario_id']}",
    )
    scenarios = derived_route.findall("./scenarios/scenario")
    _check(len(scenarios) == 1, f"DERIVED_SCENARIO_COUNT_INVALID:{catalog_scenario['scenario_id']}")
    scenario = scenarios[0]
    _check(
        scenario.get("type") == ROUTE_SCENARIO_TYPE,
        f"DERIVED_SCENARIO_TYPE_INVALID:{catalog_scenario['scenario_id']}",
    )
    _check(
        scenario.get("name")
        == "DCPaperMVP_"
        + (
            str(runtime["runtime_fixture_id"])[len("dc-runtime-") :]
            if str(runtime["runtime_fixture_id"]).startswith("dc-runtime-")
            else str(runtime["runtime_fixture_id"])
        ),
        f"DERIVED_SCENARIO_NAME_BINDING_INVALID:{catalog_scenario['scenario_id']}",
    )
    triggers = scenario.findall("trigger_point")
    fixtures = scenario.findall("fixture")
    _check(len(triggers) == 1, f"DERIVED_TRIGGER_COUNT_INVALID:{catalog_scenario['scenario_id']}")
    _check(len(fixtures) == 1, f"DERIVED_FIXTURE_COUNT_INVALID:{catalog_scenario['scenario_id']}")
    _check(
        set(child.tag for child in list(scenario)) == {"trigger_point", "fixture"},
        f"ROUTE_PARSER_CHILD_SHAPE_INVALID:{catalog_scenario['scenario_id']}",
    )
    trigger = triggers[0]
    _check(
        set(trigger.attrib) == {"x", "y", "z", "yaw"},
        f"DERIVED_TRIGGER_ATTRIBUTES_INVALID:{catalog_scenario['scenario_id']}",
    )
    expected_transform = runtime["trigger"]["world_transform"]
    for key in ("x", "y", "z", "yaw"):
        try:
            actual = float(trigger.attrib[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ScenarioFixtureValidationError(
                f"DERIVED_TRIGGER_NUMBER_INVALID:{catalog_scenario['scenario_id']}:{key}"
            ) from exc
        _check(
            math.isfinite(actual) and abs(actual - float(expected_transform[key])) <= 1e-8,
            f"DERIVED_TRIGGER_BINDING_MISMATCH:{catalog_scenario['scenario_id']}:{key}",
        )
    fixture = fixtures[0]
    _check(
        fixture.attrib
        == {
            "runtime_fixture_id": str(runtime["runtime_fixture_id"]),
            "runtime_manifest": str(record["runtime_manifest_path"]),
            "runtime_manifest_sha256": str(record["runtime_manifest_sha256"]),
            "binding_state": "STATIC_COMPILED_LIVE_RESOLUTION_REQUIRED",
        },
        f"DERIVED_FIXTURE_BINDING_INVALID:{catalog_scenario['scenario_id']}",
    )
    # This is precisely the one-level shape RouteParser stores in
    # ScenarioConfiguration.other_parameters; no custom XML decoding is needed.
    _check(
        derived_route.get("id") == str(catalog_scenario["route_id"])
        and derived_route.get("town") == str(catalog_scenario["town"])
        and len(derived_route.findall("./waypoints/position")) >= 3,
        f"ROUTE_PARSER_REQUIRED_FIELDS_INVALID:{catalog_scenario['scenario_id']}",
    )
    route_points = [
        tuple(float(position.attrib[key]) for key in ("x", "y", "z"))
        for position in derived_route.findall("./waypoints/position")
    ]
    trigger_xyz = tuple(float(expected_transform[key]) for key in ("x", "y", "z"))
    trigger_yaw = float(expected_transform["yaw"])
    accepted_by_route_filter = False
    for index, point in enumerate(route_points):
        adjacent = route_points[index + 1] if index + 1 < len(route_points) else route_points[index - 1]
        first, second = (point, adjacent) if index + 1 < len(route_points) else (adjacent, point)
        route_yaw = math.degrees(math.atan2(second[1] - first[1], second[0] - first[0]))
        planar_distance = math.hypot(trigger_xyz[0] - point[0], trigger_xyz[1] - point[1])
        delta_yaw = (trigger_yaw - route_yaw) % 360.0
        if (
            abs(trigger_xyz[2] - point[2]) < 2.0
            and planar_distance < 2.0
            and (delta_yaw < 10.0 or delta_yaw > 350.0)
        ):
            accepted_by_route_filter = True
            break
    _check(
        accepted_by_route_filter,
        f"DERIVED_TRIGGER_REJECTED_BY_ROUTE_PARSER_FILTER:{catalog_scenario['scenario_id']}",
    )


def _validate_runtime(
    output_root: Path,
    record: Mapping[str, Any],
    catalog_scenario: Mapping[str, Any],
    denylist: set[str],
) -> dict[str, Any]:
    relative = record.get("runtime_manifest_path")
    _check(
        isinstance(relative, str) and relative.startswith("runtime_visible/dc-runtime-"),
        f"RUNTIME_MANIFEST_PATH_NOT_OPAQUE:{catalog_scenario['scenario_id']}",
    )
    path = _safe_artifact(output_root, relative)
    payload = path.read_bytes()
    _check(
        _sha256_bytes(payload) == record.get("runtime_manifest_sha256"),
        f"RUNTIME_MANIFEST_SHA256_MISMATCH:{catalog_scenario['scenario_id']}",
    )
    runtime = load_json(path)
    _check(
        runtime.get("schema_version") == RUNTIME_SCHEMA_VERSION,
        f"RUNTIME_SCHEMA_VERSION_INVALID:{catalog_scenario['scenario_id']}",
    )
    _check(
        runtime.get("runtime_fixture_id") == record.get("runtime_fixture_id"),
        f"RUNTIME_FIXTURE_ID_BINDING_INVALID:{catalog_scenario['scenario_id']}",
    )
    _check(
        str(runtime.get("runtime_fixture_id", "")).startswith("dc-runtime-")
        and catalog_scenario["scenario_id"] not in str(runtime.get("runtime_fixture_id")),
        f"RUNTIME_FIXTURE_ID_NOT_OPAQUE:{catalog_scenario['scenario_id']}",
    )
    overlap = sorted(set(walk_keys(runtime)) & denylist)
    _check(
        not overlap,
        f"RUNTIME_VISIBLE_DENYLIST_OVERLAP:{catalog_scenario['scenario_id']}:{','.join(overlap)}",
    )
    visibility = runtime.get("visibility_contract", {})
    _check(
        visibility.get("environment_builder_can_read") is True
        and visibility.get("evaluator_private_join_available") is False
        and visibility.get("policy_projection_allowlist")
        == [
            "raw_instruction",
            "standard_runtime_sensor_observations",
            "standard_runtime_route_context",
        ],
        f"RUNTIME_VISIBILITY_CONTRACT_INVALID:{catalog_scenario['scenario_id']}",
    )
    _check(
        runtime.get("raw_instruction") == catalog_scenario["instruction_text"],
        f"RUNTIME_RAW_INSTRUCTION_MISMATCH:{catalog_scenario['scenario_id']}",
    )
    _check(
        runtime.get("allowed_seed_values") == catalog_scenario["seed"],
        f"RUNTIME_ALLOWED_SEEDS_MISMATCH:{catalog_scenario['scenario_id']}",
    )
    _check(
        runtime.get("selected_seed_contract", {}).get("selected_value") is None,
        f"RUNTIME_SEED_PRESELECTED:{catalog_scenario['scenario_id']}",
    )
    _transform(runtime.get("ego_spawn", {}).get("world_transform"), f"{catalog_scenario['scenario_id']}:ego")
    _selector(runtime.get("ego_spawn", {}).get("blueprint_selector"), f"{catalog_scenario['scenario_id']}:ego")
    _transform(runtime.get("trigger", {}).get("world_transform"), f"{catalog_scenario['scenario_id']}:trigger")
    _check(
        runtime.get("trigger", {}).get("radius_m") == 2.0
        and runtime.get("trigger", {}).get("yaw_tolerance_degrees") == 10.0,
        f"RUNTIME_TRIGGER_TOLERANCE_INVALID:{catalog_scenario['scenario_id']}",
    )
    entities = runtime.get("entities")
    _check(isinstance(entities, list) and bool(entities), f"RUNTIME_ENTITIES_MISSING:{catalog_scenario['scenario_id']}")
    entity_ids: list[str] = []
    for entity in entities:
        _check(isinstance(entity, Mapping), f"RUNTIME_ENTITY_NOT_OBJECT:{catalog_scenario['scenario_id']}")
        entity_id = str(entity.get("entity_id", ""))
        _check(bool(entity_id), f"RUNTIME_ENTITY_ID_MISSING:{catalog_scenario['scenario_id']}")
        entity_ids.append(entity_id)
        _transform(
            entity.get("geometric_binding", {}).get("world_transform"),
            f"{catalog_scenario['scenario_id']}:{entity_id}",
        )
        _selector(entity.get("blueprint_selector"), f"{catalog_scenario['scenario_id']}:{entity_id}")
    _check(len(entity_ids) == len(set(entity_ids)), f"RUNTIME_ENTITY_IDS_DUPLICATE:{catalog_scenario['scenario_id']}")
    background = runtime.get("background", {}).get("spawn_slots")
    _check(isinstance(background, list), f"RUNTIME_BACKGROUND_NOT_LIST:{catalog_scenario['scenario_id']}")
    for slot in background:
        slot_id = str(slot.get("slot_id", ""))
        _transform(slot.get("world_transform"), f"{catalog_scenario['scenario_id']}:{slot_id}")
        _selector(slot.get("blueprint_selector"), f"{catalog_scenario['scenario_id']}:{slot_id}")
    timeline = runtime.get("event_timeline")
    _check(isinstance(timeline, list) and len(timeline) >= 5, f"RUNTIME_TIMELINE_INCOMPLETE:{catalog_scenario['scenario_id']}")
    event_ids = [event.get("event_id") for event in timeline if isinstance(event, Mapping)]
    _check(
        len(event_ids) == len(timeline) == len(set(event_ids))
        and event_ids[:4]
        == [
            "EV00_CONSTRUCT",
            "EV01_INSTRUCTION_ISSUE",
            "EV02_SCENARIO_TRIGGER",
            "EV03_OBSERVABLE_CONDITION",
        ]
        and event_ids[-1] == "EV99_ROUTE_TERMINAL",
        f"RUNTIME_TIMELINE_ORDER_INVALID:{catalog_scenario['scenario_id']}",
    )
    termination = runtime.get("termination")
    _check(
        isinstance(termination, Mapping)
        and isinstance(termination.get("success_condition"), Mapping)
        and isinstance(termination.get("failure_conditions"), list)
        and bool(termination["failure_conditions"])
        and isinstance(termination.get("timeout"), Mapping),
        f"RUNTIME_TERMINATION_INVALID:{catalog_scenario['scenario_id']}",
    )
    gate = runtime.get("launch_gate")
    _check(
        isinstance(gate, Mapping)
        and gate.get("launch_ready") is False
        and isinstance(gate.get("required_receipts"), list)
        and len(gate["required_receipts"]) >= 5,
        f"RUNTIME_LAUNCH_GATE_INVALID:{catalog_scenario['scenario_id']}",
    )
    try:
        assert_finite(runtime)
    except ScenarioFixtureContractError as exc:
        raise ScenarioFixtureValidationError(
            f"RUNTIME_NONFINITE:{catalog_scenario['scenario_id']}:{exc}"
        ) from exc
    return runtime


def _all_output_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_file() or path.is_symlink():
            _check(not path.is_symlink(), f"OUTPUT_SYMLINK_FORBIDDEN:{path}")
            files.add(path.relative_to(root).as_posix())
    return files


def _validate_generated(output_dir: str | Path, compare_expected: bool) -> dict[str, Any]:
    output_root = Path(output_dir)
    _check(output_root.is_dir(), f"OUTPUT_DIRECTORY_MISSING:{output_root}")
    _check(file_sha256(CATALOG_PATH) == EXPECTED_CATALOG_FILE_SHA256, "FROZEN_CATALOG_FILE_SHA256_MISMATCH")
    _check(file_sha256(SPLIT_PATH) == EXPECTED_SPLIT_FILE_SHA256, "FROZEN_SPLIT_FILE_SHA256_MISMATCH")
    catalog = load_json(CATALOG_PATH)
    split = load_json(SPLIT_PATH)
    catalog_scenarios = {
        str(scenario["scenario_id"]): scenario for scenario in catalog.get("scenarios", [])
    }
    _check(len(catalog_scenarios) == 24, "FROZEN_SCENARIO_COUNT_OR_ID_INVALID")
    denylist = set(catalog.get("label_firewall", {}).get("evaluation_only_denylist", []))
    _check(bool(denylist), "FROZEN_RUNTIME_DENYLIST_MISSING")

    top = load_json(_safe_artifact(output_root, TOP_MANIFEST))
    private = load_json(_safe_artifact(output_root, PRIVATE_MANIFEST))
    schema = load_json(_safe_artifact(output_root, SCHEMA_FILE))
    hashes = load_json(_safe_artifact(output_root, HASH_MANIFEST))
    _check(top.get("schema_version") == SCHEMA_VERSION, "TOP_MANIFEST_SCHEMA_VERSION_INVALID")
    _check(top.get("visibility") == "CONTROL_PLANE_ONLY_NOT_POLICY_INPUT", "TOP_MANIFEST_VISIBILITY_INVALID")
    _check(top.get("final_status") == STATIC_STATUS, "TOP_MANIFEST_STATUS_INVALID")
    _check(private.get("schema_version") == PRIVATE_SCHEMA_VERSION, "PRIVATE_MANIFEST_SCHEMA_VERSION_INVALID")
    _check(private.get("visibility") == "EVALUATOR_PRIVATE_NEVER_POLICY_VISIBLE", "PRIVATE_MANIFEST_VISIBILITY_INVALID")
    _check(schema.get("properties", {}).get("schema_version", {}).get("const") == SCHEMA_VERSION, "EXECUTION_SCHEMA_INVALID")
    expected_identity = {
        "catalog_file_sha256": EXPECTED_CATALOG_FILE_SHA256,
        "scenario_payload_sha256": EXPECTED_SCENARIO_PAYLOAD_SHA256,
        "scenario_definition_payload_sha256": EXPECTED_SCENARIO_DEFINITION_SHA256,
        "split_file_sha256": EXPECTED_SPLIT_FILE_SHA256,
    }
    for key, value in expected_identity.items():
        _check(top.get("frozen_identity", {}).get(key) == value, f"TOP_FROZEN_IDENTITY_INVALID:{key}")
        _check(private.get("frozen_identity", {}).get(key) == value, f"PRIVATE_FROZEN_IDENTITY_INVALID:{key}")
    _check(top.get("frozen_identity", {}).get("freeze_id") == catalog.get("freeze_id"), "TOP_FREEZE_ID_INVALID")
    _check(
        top.get("scenario_count") == top.get("runtime_fixture_count") == top.get("derived_route_count") == 24,
        "TOP_ARTIFACT_COUNTS_INVALID",
    )
    readiness = top.get("stage6a_readiness", {})
    _check(
        readiness.get("static_compilation_complete") is True
        and readiness.get("live_launch_ready") is False
        and readiness.get("test_split_consumed") is False
        and readiness.get("carla_launch_count") == 0,
        "TOP_READINESS_GATE_INVALID",
    )
    registration = top.get("scenario_class_registration", {})
    registration_source = registration.get("source_path")
    _check(
        registration.get("type") == ROUTE_SCENARIO_TYPE
        and registration.get("registered_in_live_scenario_runner") is False
        and registration.get("registration_receipt_required") is True
        and isinstance(registration_source, str)
        and (ROOT / registration_source).is_file(),
        "SCENARIO_CLASS_REGISTRATION_CONTRACT_INVALID",
    )

    records = top.get("records")
    private_records = private.get("records")
    _check(isinstance(records, list) and len(records) == 24, "TOP_RECORD_COUNT_INVALID")
    _check(isinstance(private_records, list) and len(private_records) == 24, "PRIVATE_RECORD_COUNT_INVALID")
    _check(all(isinstance(row, Mapping) for row in records), "TOP_RECORD_NOT_OBJECT")
    _check(all(isinstance(row, Mapping) for row in private_records), "PRIVATE_RECORD_NOT_OBJECT")
    ids = [str(row.get("scenario_id", "")) for row in records]
    _check(len(set(ids)) == 24 and set(ids) == set(catalog_scenarios), "TOP_SCENARIO_IDS_INVALID")
    private_by_id = {str(row.get("scenario_id", "")): row for row in private_records}
    _check(len(private_by_id) == 24 and set(private_by_id) == set(ids), "PRIVATE_SCENARIO_IDS_INVALID")

    split_binding = top.get("source_split_binding", {})
    for split_name in ("train", "dev", "test"):
        expected_ids = split.get("splits", {}).get(split_name, {}).get("scenario_ids")
        _check(split_binding.get(split_name) == expected_ids, f"TOP_SPLIT_BINDING_INVALID:{split_name}")

    runtime_ids: set[str] = set()
    for record in records:
        scenario_id = str(record["scenario_id"])
        scenario = catalog_scenarios[scenario_id]
        _check(record.get("split") == scenario["split"], f"TOP_SPLIT_INVALID:{scenario_id}")
        _check(record.get("seed") == scenario["seed"], f"TOP_SEEDS_INVALID:{scenario_id}")
        _check(record.get("live_launch_ready") is False, f"TOP_RECORD_LAUNCH_READY_INVALID:{scenario_id}")
        runtime = _validate_runtime(output_root, record, scenario, denylist)
        runtime_id = str(runtime["runtime_fixture_id"])
        _check(runtime_id not in runtime_ids, f"RUNTIME_FIXTURE_ID_DUPLICATE:{scenario_id}")
        runtime_ids.add(runtime_id)
        _validate_route(output_root, record, runtime, scenario)
        private_record = private_by_id[scenario_id]
        _check(private_record.get("split") == scenario["split"], f"PRIVATE_SPLIT_INVALID:{scenario_id}")
        _check(private_record.get("seed") == scenario["seed"], f"PRIVATE_SEEDS_INVALID:{scenario_id}")
        binding = private_record.get("runtime_binding", {})
        for key in (
            "runtime_fixture_id",
            "runtime_manifest_path",
            "runtime_manifest_sha256",
            "derived_route_path",
            "derived_route_sha256",
        ):
            _check(binding.get(key) == record.get(key), f"PRIVATE_RUNTIME_BINDING_INVALID:{scenario_id}:{key}")
        _check(binding.get("scenario_runner_type") == ROUTE_SCENARIO_TYPE, f"PRIVATE_SCENARIO_TYPE_INVALID:{scenario_id}")
        annotation = private_record.get("annotation_join_contract", {})
        _check(
            annotation.get("policy_access") is False
            and annotation.get("join_time") == "POST_EPISODE_ONLY"
            and annotation.get("expected_decision") == scenario["expected_decision_for_validation"],
            f"PRIVATE_ANNOTATION_JOIN_INVALID:{scenario_id}",
        )
        evaluation = private_record.get("expected_evaluation_metadata", {})
        _check(
            evaluation.get("expected_initial_decision")
            == scenario["expected_decision_for_validation"]
            and evaluation.get("evaluation_ready") is False,
            f"PRIVATE_EVALUATION_METADATA_INVALID:{scenario_id}",
        )

    artifact_rows = hashes.get("artifacts")
    _check(isinstance(artifact_rows, Mapping), "HASH_MANIFEST_ARTIFACTS_INVALID")
    _check(hashes.get("self_hash_excluded") is True, "HASH_MANIFEST_SELF_HASH_POLICY_INVALID")
    _check(hashes.get("artifact_count") == len(artifact_rows), "HASH_MANIFEST_COUNT_INVALID")
    for relative, row in artifact_rows.items():
        _check(isinstance(row, Mapping), f"HASH_MANIFEST_ROW_INVALID:{relative}")
        path = _safe_artifact(output_root, relative)
        payload = path.read_bytes()
        _check(row.get("bytes") == len(payload), f"HASH_MANIFEST_BYTES_INVALID:{relative}")
        _check(row.get("sha256") == _sha256_bytes(payload), f"HASH_MANIFEST_SHA256_INVALID:{relative}")
    _check(HASH_MANIFEST not in artifact_rows, "HASH_MANIFEST_SELF_HASH_PRESENT")

    expected = expected_documents()
    expected_files = set(expected)
    actual_files = _all_output_files(output_root)
    _check(actual_files == expected_files, "OUTPUT_ARTIFACT_SET_INVALID")
    if compare_expected:
        for relative, expected_payload in expected.items():
            actual_payload = (output_root / relative).read_bytes()
            _check(actual_payload == expected_payload, f"NONDETERMINISTIC_OR_MUTATED_ARTIFACT:{relative}")
    return {
        "status": "PASS_STATIC_STAGE6A_SCENARIO_VALIDATION",
        "output_dir": str(output_root.resolve()),
        "scenario_count": 24,
        "runtime_fixture_count": 24,
        "derived_route_count": 24,
        "episode_configuration_count": 96,
        "live_launch_ready": False,
        "carla_imported": False,
        "carla_launch_count": 0,
        "frozen_catalog_sha256": EXPECTED_CATALOG_FILE_SHA256,
        "frozen_split_sha256": EXPECTED_SPLIT_FILE_SHA256,
    }


def validate_generated(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    compare_expected: bool = True,
) -> dict[str, Any]:
    """Validate generated artifacts without importing or launching CARLA."""

    try:
        return _validate_generated(output_dir, compare_expected)
    except ScenarioFixtureValidationError:
        raise
    except (ScenarioFixtureContractError, KeyError, TypeError, ValueError, OSError) as exc:
        raise ScenarioFixtureValidationError(f"STATIC_VALIDATION_FAILED:{exc}") from exc


def _runtime_live_bindings(runtime: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result = {
        "ego": {
            "kind": "ego",
            "transform": runtime["ego_spawn"]["world_transform"],
            "physical_required": True,
            "semantic_required": False,
            "realization_contract_mode": "NOT_APPLICABLE",
        }
    }
    for entity in runtime["entities"]:
        composite = (
            entity.get("realization_contract", {}).get("mode")
            == "COMPOSITE_MEASURED_CLEARANCE_GATEWAY"
        )
        result[str(entity["entity_id"])] = {
            "kind": str(entity["kind"]),
            "transform": entity["geometric_binding"]["world_transform"],
            "physical_required": entity["kind"] == "dynamic_actor" or composite,
            "semantic_required": True,
            "realization_contract_mode": entity.get("realization_contract", {}).get(
                "mode"
            ),
        }
    for slot in runtime["background"]["spawn_slots"]:
        result[str(slot["slot_id"])] = {
            "kind": str(slot["kind"]),
            "transform": slot["world_transform"],
            "physical_required": True,
            "semantic_required": False,
            "realization_contract_mode": "NOT_APPLICABLE",
        }
    return result


def _validate_live_promotions(
    promotion_dir: str | Path,
    runtime_root: str | Path,
    require_ready: bool,
) -> dict[str, Any]:
    promotion_root = Path(promotion_dir)
    static_root = Path(runtime_root)
    _check(promotion_root.is_dir(), f"LIVE_PROMOTION_DIRECTORY_MISSING:{promotion_root}")
    manifest = load_json(_safe_artifact(promotion_root, LIVE_PROMOTION_MANIFEST))
    _check(
        manifest.get("schema_version") == LIVE_MANIFEST_SCHEMA_VERSION,
        "LIVE_MANIFEST_SCHEMA_INVALID",
    )
    _check(
        verify_content_address(manifest, MANIFEST_HASH_FIELD),
        "LIVE_MANIFEST_CONTENT_HASH_INVALID",
    )
    overlap = sorted(set(walk_keys(manifest)) & RUNTIME_FORBIDDEN_KEYS)
    _check(not overlap, "LIVE_MANIFEST_FORBIDDEN_KEY:" + ",".join(overlap))
    input_scope = manifest.get("input_scope", {})
    _check(
        input_scope.get("frozen_catalog_read") is False
        and input_scope.get("evaluator_private_manifest_read") is False
        and input_scope.get("evaluation_label_access") is False,
        "LIVE_MANIFEST_INPUT_SCOPE_INVALID",
    )
    runtime_paths = sorted((static_root / "runtime_visible").glob("dc-runtime-*.json"))
    _check(len(runtime_paths) == 24, "LIVE_STATIC_RUNTIME_COUNT_INVALID")
    runtimes: dict[str, tuple[dict[str, Any], Path]] = {}
    expected_pairs: set[tuple[str, int]] = set()
    for path in runtime_paths:
        runtime = load_json(path)
        runtime_id = str(runtime.get("runtime_fixture_id", ""))
        _check(path.stem == runtime_id and runtime_id not in runtimes, "LIVE_STATIC_RUNTIME_ID_INVALID")
        runtime_overlap = sorted(set(walk_keys(runtime)) & RUNTIME_FORBIDDEN_KEYS)
        _check(
            not runtime_overlap,
            f"LIVE_STATIC_RUNTIME_FORBIDDEN_KEY:{runtime_id}:{','.join(runtime_overlap)}",
        )
        runtimes[runtime_id] = (runtime, path)
        for seed in runtime.get("allowed_seed_values", []):
            expected_pairs.add((runtime_id, int(seed)))
    _check(len(expected_pairs) == 96, "LIVE_EXPECTED_SEED_CONFIGURATION_COUNT_INVALID")
    records = manifest.get("records")
    _check(isinstance(records, list), "LIVE_MANIFEST_RECORDS_NOT_LIST")
    _check(
        manifest.get("runtime_fixture_count") == 24
        and manifest.get("seed_configuration_count") == 96
        and len(records) == 96,
        "LIVE_MANIFEST_COUNTS_INVALID",
    )
    actual_pairs: set[tuple[str, int]] = set()
    physical_count = 0
    semantic_count = 0
    ready_count = 0
    live_origin_count = 0
    test_double_count = 0
    for record in records:
        _check(isinstance(record, Mapping), "LIVE_MANIFEST_RECORD_NOT_OBJECT")
        runtime_id = str(record.get("runtime_fixture_id", ""))
        seed = record.get("selected_seed")
        _check(
            isinstance(seed, int) and not isinstance(seed, bool),
            f"LIVE_SELECTED_SEED_INVALID:{runtime_id}",
        )
        pair = (runtime_id, int(seed))
        _check(pair in expected_pairs and pair not in actual_pairs, f"LIVE_RECEIPT_PAIR_INVALID:{pair}")
        actual_pairs.add(pair)
        runtime, runtime_path = runtimes[runtime_id]
        _check(
            record.get("runtime_manifest_sha256") == file_sha256(runtime_path),
            f"LIVE_RECORD_RUNTIME_SHA_INVALID:{runtime_id}:{seed}",
        )
        receipt_relative = record.get("receipt_path")
        receipt_path = _safe_artifact(promotion_root, receipt_relative)
        receipt = load_json(receipt_path)
        receipt_hash = receipt.get(RECEIPT_HASH_FIELD)
        _check(
            receipt.get("schema_version") == LIVE_RECEIPT_SCHEMA_VERSION
            and verify_content_address(receipt, RECEIPT_HASH_FIELD)
            and receipt_hash == record.get("receipt_payload_sha256")
            and receipt_path.stem == receipt_hash,
            f"LIVE_RECEIPT_CONTENT_ADDRESS_INVALID:{runtime_id}:{seed}",
        )
        receipt_overlap = sorted(set(walk_keys(receipt)) & RUNTIME_FORBIDDEN_KEYS)
        _check(
            not receipt_overlap,
            f"LIVE_RECEIPT_FORBIDDEN_KEY:{runtime_id}:{seed}:{','.join(receipt_overlap)}",
        )
        receipt_scope = receipt.get("input_scope", {})
        _check(
            receipt_scope.get("frozen_catalog_read") is False
            and receipt_scope.get("evaluator_private_manifest_read") is False
            and receipt_scope.get("evaluation_label_access") is False,
            f"LIVE_RECEIPT_INPUT_SCOPE_INVALID:{runtime_id}:{seed}",
        )
        route_path = static_root / runtime["route_binding"]["derived_route_path"]
        _check(
            receipt.get("runtime_fixture_id") == runtime_id
            and receipt.get("runtime_manifest_sha256") == file_sha256(runtime_path)
            and receipt.get("derived_route_sha256") == file_sha256(route_path)
            and receipt.get("selected_seed") == seed,
            f"LIVE_RECEIPT_STATIC_BINDING_INVALID:{runtime_id}:{seed}",
        )
        provenance = receipt.get("provenance")
        _check(
            isinstance(provenance, Mapping)
            and provenance.get("evidence_origin")
            in {LIVE_EVIDENCE_ORIGIN, TEST_DOUBLE_EVIDENCE_ORIGIN},
            f"LIVE_RECEIPT_PROVENANCE_INVALID:{runtime_id}:{seed}",
        )
        evidence_origin = provenance["evidence_origin"]
        live_evidence = evidence_origin == LIVE_EVIDENCE_ORIGIN
        _check(
            provenance.get("formal_promotion_eligible") is live_evidence
            and record.get("evidence_origin") == evidence_origin,
            f"LIVE_RECEIPT_PROVENANCE_STATUS_INVALID:{runtime_id}:{seed}",
        )
        live_origin_count += int(live_evidence)
        test_double_count += int(not live_evidence)
        server_identity = receipt.get("server_identity")
        _check(
            isinstance(server_identity, Mapping)
            and _map_name(server_identity.get("map_name"))
            == _map_name(runtime["route_binding"]["town"])
            and _is_sha256(server_identity.get("opendrive_sha256"))
            and _is_sha256(server_identity.get("blueprint_inventory_sha256"))
            and isinstance(server_identity.get("client_version"), str)
            and bool(server_identity.get("client_version"))
            and isinstance(server_identity.get("server_version"), str)
            and bool(server_identity.get("server_version")),
            f"LIVE_RECEIPT_SERVER_IDENTITY_INVALID:{runtime_id}:{seed}",
        )
        _check(
            server_identity.get("no_rendering_mode") is False,
            f"LIVE_RECEIPT_RENDERING_MODE_INVALID:{runtime_id}:{seed}",
        )
        probe_contract = receipt.get("probe_contract")
        _check(
            isinstance(probe_contract, Mapping)
            and probe_contract.get("connected_to_preexisting_server") is live_evidence
            and probe_contract.get("server_started_by_tool") is False
            and probe_contract.get("existing_dynamic_actor_count") == 0
            and probe_contract.get("spawn_probe")
            == "JOINT_TRY_SPAWN_THEN_DESTROY_WITHOUT_WORLD_TICK"
            and type(probe_contract.get("world_load_permitted")) is bool
            and type(probe_contract.get("navmesh_samples")) is int
            and probe_contract.get("navmesh_samples") > 0,
            f"LIVE_RECEIPT_PROBE_CONTRACT_INVALID:{runtime_id}:{seed}",
        )
        bindings = receipt.get("actor_bindings")
        expected_bindings = _runtime_live_bindings(runtime)
        _check(isinstance(bindings, list), f"LIVE_BINDINGS_NOT_LIST:{runtime_id}:{seed}")
        by_binding = {
            str(binding.get("binding_id", "")): binding
            for binding in bindings
            if isinstance(binding, Mapping)
        }
        _check(
            len(by_binding) == len(bindings)
            and set(by_binding) == set(expected_bindings),
            f"LIVE_BINDING_IDS_INVALID:{runtime_id}:{seed}",
        )
        required_verified = []
        semantic_verified = []
        for binding_id, expected in expected_bindings.items():
            binding = by_binding[binding_id]
            _check(
                binding.get("kind") == expected["kind"]
                and binding.get("requested_world_transform") == expected["transform"]
                and binding.get("physical_spawn_required")
                is expected["physical_required"],
                f"LIVE_BINDING_STATIC_MISMATCH:{runtime_id}:{seed}:{binding_id}",
            )
            _check(
                isinstance(binding.get("snap"), Mapping)
                and isinstance(binding.get("blueprint_resolution"), Mapping)
                and isinstance(binding.get("spawn_probe"), Mapping),
                f"LIVE_BINDING_PROBE_FIELDS_INVALID:{runtime_id}:{seed}:{binding_id}",
            )
            snap = binding["snap"]
            resolution = binding["blueprint_resolution"]
            spawn_probe = binding["spawn_probe"]
            _check(
                type(snap.get("usable")) is bool
                and type(resolution.get("resolved")) is bool
                and all(
                    type(spawn_probe.get(field)) is bool
                    for field in ("attempted", "spawned", "destroyed")
                ),
                f"LIVE_BINDING_PROBE_BOOLEAN_INVALID:{runtime_id}:{seed}:{binding_id}",
            )
            spawn_verified = bool(
                spawn_probe["attempted"]
                and spawn_probe["spawned"]
                and spawn_probe["destroyed"]
                and snap["usable"]
                and resolution["resolved"]
            )
            _check(
                binding.get("physical_spawn_verified") is spawn_verified,
                f"LIVE_BINDING_PHYSICAL_STATUS_INVALID:{runtime_id}:{seed}:{binding_id}",
            )
            if spawn_verified:
                _check(
                    isinstance(resolution.get("resolved_blueprint_id"), str)
                    and bool(resolution["resolved_blueprint_id"])
                    and spawn_probe.get("actor_type_id")
                    == resolution["resolved_blueprint_id"]
                    and isinstance(spawn_probe.get("actual_world_transform"), Mapping)
                    and isinstance(spawn_probe.get("actor_bounding_box"), Mapping),
                    f"LIVE_BINDING_SPAWN_EVIDENCE_INVALID:{runtime_id}:{seed}:{binding_id}",
                )
            if expected["physical_required"]:
                required_verified.append(spawn_verified)
            semantic = binding.get("semantic_fidelity", {})
            _check(
                semantic.get("status") in SEMANTIC_FIDELITY_STATUSES,
                f"LIVE_BINDING_SEMANTIC_STATUS_INVALID:{runtime_id}:{seed}:{binding_id}",
            )
            _check(
                semantic.get("external_self_attestation_used_as_gate") is False,
                f"LIVE_BINDING_EXTERNAL_ATTESTATION_GATE_INVALID:{runtime_id}:{seed}:{binding_id}",
            )
            if expected["kind"] in {"dynamic_actor", "semantic_marker"}:
                semantic_verified.append(semantic.get("status") == "VERIFIED")
        physical_verified = bool(required_verified) and all(required_verified)
        all_semantic_verified = bool(semantic_verified) and all(semantic_verified)
        _check(
            receipt.get("physical_spawn_verified") is physical_verified
            and receipt.get("semantic_fidelity", {}).get("all_catalog_entities_verified")
            is all_semantic_verified
            and receipt.get("semantic_fidelity", {}).get(
                "spawnability_used_as_semantic_proof"
            )
            is False
            and receipt.get("semantic_fidelity", {}).get(
                "external_self_attestation_used_as_gate"
            )
            is False,
            f"LIVE_RECEIPT_AGGREGATE_STATUS_INVALID:{runtime_id}:{seed}",
        )
        handler = receipt.get("handler_execution_receipt")
        _check(
            isinstance(handler, Mapping)
            and type(handler.get("verified")) is bool,
            f"LIVE_HANDLER_EXECUTION_RECEIPT_INVALID:{runtime_id}:{seed}",
        )
        handler_verified = handler["verified"]
        if handler_verified:
            _check(
                handler.get("status") == "VERIFIED"
                and _is_sha256(handler.get("receipt_payload_sha256"))
                and isinstance(handler.get("receipt_path"), str)
                and bool(handler.get("receipt_path")),
                f"LIVE_HANDLER_EXECUTION_EVIDENCE_INVALID:{runtime_id}:{seed}",
            )
        else:
            _check(
                handler.get("status") == "MISSING"
                and handler.get("receipt_payload_sha256") is None
                and handler.get("receipt_path") is None,
                f"LIVE_HANDLER_EXECUTION_MISSING_STATUS_INVALID:{runtime_id}:{seed}",
            )
        ready = bool(
            physical_verified
            and all_semantic_verified
            and handler_verified
            and live_evidence
        )
        gates = receipt.get("gate_receipts")
        _check(
            isinstance(gates, Mapping)
            and gates.get("live_blueprint_resolution")
            is all(
                by_binding[binding_id]["blueprint_resolution"]["resolved"]
                for binding_id, expected in expected_bindings.items()
                if expected["physical_required"]
            )
            and gates.get("live_map_navmesh_snap")
            is all(
                by_binding[binding_id]["snap"]["usable"]
                for binding_id, expected in expected_bindings.items()
                if expected["physical_required"]
            )
            and gates.get("live_spawn_destroy") is physical_verified
            and gates.get("live_semantic_scene_realization") is all_semantic_verified
            and gates.get("scenario_class_registration") is handler_verified
            and gates.get("handler_spawn_tick_cleanup") is handler_verified
            and gates.get("route_and_goal_nonleakage") is True,
            f"LIVE_RECEIPT_GATE_STATUS_INVALID:{runtime_id}:{seed}",
        )
        expected_final_status = (
            "BLOCKED_TEST_DOUBLE_EVIDENCE"
            if not live_evidence
            else (
                "BLOCKED_LIVE_PHYSICAL_SPAWN"
                if not physical_verified
                else (
                    "BLOCKED_LIVE_SEMANTIC_FIDELITY"
                    if not all_semantic_verified
                    else (
                        "BLOCKED_HANDLER_EXECUTION_NOT_VERIFIED"
                        if not handler_verified
                        else "PASS_LIVE_PROMOTION_READY"
                    )
                )
            )
        )
        _check(
            receipt.get("promotion_ready") is ready
            and record.get("physical_spawn_verified") is physical_verified
            and record.get("semantic_fidelity_status")
            == ("VERIFIED" if all_semantic_verified else "BLOCKED")
            and record.get("promotion_ready") is ready
            and receipt.get("final_status") == expected_final_status
            and record.get("final_status") == expected_final_status,
            f"LIVE_RECEIPT_PROMOTION_STATUS_INVALID:{runtime_id}:{seed}",
        )
        physical_count += int(physical_verified)
        semantic_count += int(all_semantic_verified)
        ready_count += int(ready)
        try:
            assert_finite(receipt)
        except ScenarioFixtureContractError as exc:
            raise ScenarioFixtureValidationError(
                f"LIVE_RECEIPT_NONFINITE:{runtime_id}:{seed}:{exc}"
            ) from exc
    _check(actual_pairs == expected_pairs, "LIVE_RECEIPT_PAIR_COVERAGE_INVALID")
    summary = manifest.get("summary", {})
    _check(
        summary.get("physical_spawn_verified_count") == physical_count
        and summary.get("semantic_fidelity_verified_count") == semantic_count
        and summary.get("promotion_ready_count") == ready_count
        and summary.get("all_physical_spawn_verified") is (physical_count == 96)
        and summary.get("all_semantic_fidelity_verified") is (semantic_count == 96)
        and summary.get("all_promotion_ready") is (ready_count == 96)
        and summary.get("live_evidence_origin_count") == live_origin_count
        and summary.get("test_double_evidence_count") == test_double_count
        and summary.get("all_receipts_live_evidence") is (live_origin_count == 96)
        and summary.get("carla_server_started_by_tool") is False,
        "LIVE_MANIFEST_SUMMARY_INVALID",
    )
    if require_ready:
        _check(
            ready_count == 96 and live_origin_count == 96,
            f"LIVE_PROMOTION_NOT_READY:{ready_count}_OF_96",
        )
    return {
        "status": (
            "PASS_LIVE_PROMOTION_96_OF_96_READY"
            if ready_count == 96
            else "PASS_LIVE_RECEIPT_INTEGRITY_BLOCKED_PROMOTION"
        ),
        "runtime_fixture_count": 24,
        "seed_configuration_count": 96,
        "physical_spawn_verified_count": physical_count,
        "semantic_fidelity_verified_count": semantic_count,
        "promotion_ready_count": ready_count,
        "all_promotion_ready": ready_count == 96,
        "live_evidence_origin_count": live_origin_count,
        "test_double_evidence_count": test_double_count,
        "carla_server_started_by_validator": False,
    }


def validate_live_promotions(
    promotion_dir: str | Path,
    *,
    runtime_root: str | Path = DEFAULT_OUTPUT_DIR,
    require_ready: bool = True,
) -> dict[str, Any]:
    """Validate all 24×4 live receipts, optionally requiring promotion readiness."""

    try:
        return _validate_live_promotions(promotion_dir, runtime_root, require_ready)
    except ScenarioFixtureValidationError:
        raise
    except (ScenarioFixtureContractError, KeyError, TypeError, ValueError, OSError) as exc:
        raise ScenarioFixtureValidationError(f"LIVE_VALIDATION_FAILED:{exc}") from exc
