"""CPU-only RouteScenario and V3 runtime-fairness contract preflight.

The module deliberately does not import CARLA, torch, NumPy, the leaderboard parser, or
ScenarioRunner.  It validates the frozen XML/Python-source contract before a supervisor is
allowed to launch CARLA or load the model.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET

from .maneuver_branch import with_hash


PREFLIGHT_SCHEMA = "driveclarify.maneuver_branch.route_scenario_preflight.v3"
S1_RUN_ID = "DC-MB-S1-20260731T225500Z"


class RouteScenarioPreflightError(RuntimeError):
    """Fail-closed preflight rejection with a stable reason code."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RouteScenarioPreflightError(reason)


def _float_attribute(element: ET.Element, name: str) -> float:
    value = element.get(name)
    _require(value is not None, f"XML_ATTRIBUTE_MISSING:{element.tag}:{name}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RouteScenarioPreflightError(
            f"XML_ATTRIBUTE_NOT_FLOAT:{element.tag}:{name}"
        ) from exc


def _normalize_degrees(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _segment_heading_degrees(first: ET.Element, second: ET.Element) -> float:
    dx = _float_attribute(second, "x") - _float_attribute(first, "x")
    dy = _float_attribute(second, "y") - _float_attribute(first, "y")
    _require(math.hypot(dx, dy) > 1e-6, "ROUTE_ZERO_LENGTH_SEGMENT")
    return math.degrees(math.atan2(dy, dx))


def _road_end_heading_degrees(road: ET.Element) -> tuple[float, float]:
    geometries = road.findall("./planView/geometry")
    _require(bool(geometries), f"OPENDRIVE_ROAD_GEOMETRY_MISSING:{road.get('id')}")
    first_heading = math.degrees(_float_attribute(geometries[0], "hdg"))
    last = geometries[-1]
    final_heading = _float_attribute(last, "hdg")
    arc = last.find("arc")
    if arc is not None:
        final_heading += _float_attribute(arc, "curvature") * _float_attribute(last, "length")
    return first_heading, math.degrees(final_heading)


def _class_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _parse_route(route_path: Path, expected_route_id: str, expected_town: str) -> dict[str, Any]:
    try:
        root = ET.parse(route_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise RouteScenarioPreflightError("ROUTE_XML_PARSE_FAILED") from exc
    routes = list(root.iter("route"))
    route_ids = [item.get("id") for item in routes]
    _require(all(route_ids), "ROUTE_ID_MISSING")
    _require(len(route_ids) == len(set(route_ids)), "ROUTE_ID_NOT_UNIQUE")
    selected = [item for item in routes if item.get("id") == expected_route_id]
    _require(len(selected) == 1, "EXPECTED_ROUTE_ID_NOT_UNIQUE_OR_MISSING")
    route = selected[0]
    _require(len(routes) == 1, "DEDICATED_ROUTE_FILE_MUST_CONTAIN_ONE_ROUTE")
    _require(route.get("town") == expected_town, "ROUTE_TOWN_MISMATCH")
    waypoints = route.findall("./waypoints/position")
    _require(len(waypoints) >= 3, "ROUTE_WAYPOINTS_INSUFFICIENT")
    scenarios_container = route.find("scenarios")
    _require(scenarios_container is not None, "SCENARIOS_CONTAINER_MISSING")
    scenarios = scenarios_container.findall("scenario")
    _require(bool(scenarios), "SCENARIO_CONFIGS_EMPTY_UNSUPPORTED_BY_EVALUATOR")

    parsed_scenarios = []
    for scenario in scenarios:
        name = scenario.get("name")
        scenario_type = scenario.get("type")
        triggers = scenario.findall("trigger_point")
        _require(bool(name), "SCENARIO_NAME_MISSING")
        _require(bool(scenario_type), "SCENARIO_TYPE_MISSING")
        _require(bool(triggers), "SCENARIO_TRIGGER_POINTS_EMPTY")
        trigger_rows = []
        for trigger in triggers:
            trigger_rows.append(
                {
                    key: _float_attribute(trigger, key)
                    for key in ("x", "y", "z", "yaw")
                }
            )
        parsed_scenarios.append(
            {
                "name": name,
                "type": scenario_type,
                "trigger_points": trigger_rows,
                "other_actor_count_from_xml": len(scenario.findall("other_actor")),
                "other_parameters": {
                    child.tag: dict(child.attrib)
                    for child in scenario
                    if child.tag not in {"trigger_point", "other_actor"}
                },
            }
        )

    approach_heading = _segment_heading_degrees(waypoints[0], waypoints[1])
    exit_heading = _segment_heading_degrees(waypoints[-2], waypoints[-1])
    route_turn = _normalize_degrees(exit_heading - approach_heading)
    return {
        "route_count": len(routes),
        "route_ids": route_ids,
        "selected_route_id": expected_route_id,
        "town": expected_town,
        "waypoint_count": len(waypoints),
        "first_waypoint_xyz": [
            _float_attribute(waypoints[0], key) for key in ("x", "y", "z")
        ],
        "last_waypoint_xyz": [
            _float_attribute(waypoints[-1], key) for key in ("x", "y", "z")
        ],
        "approach_heading_degrees": approach_heading,
        "exit_heading_degrees": exit_heading,
        "route_signed_turn_degrees": route_turn,
        "route_statically_takes_right_branch": 45.0 <= route_turn <= 135.0,
        "scenario_count": len(parsed_scenarios),
        "scenario_configs": parsed_scenarios,
        "config_zero_contract": {
            "exists": True,
            "name_nonempty": bool(parsed_scenarios[0]["name"]),
            "type_nonempty": bool(parsed_scenarios[0]["type"]),
            "trigger_points_nonempty": bool(parsed_scenarios[0]["trigger_points"]),
        },
    }


def _parse_branch_topology(xodr_path: Path, binding: Mapping[str, Any]) -> dict[str, Any]:
    try:
        root = ET.parse(xodr_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise RouteScenarioPreflightError("OPENDRIVE_PARSE_FAILED") from exc
    junction_id = str(binding["junction_id"])
    incoming_road_id = str(binding["incoming_road_id"])
    incoming_lane_id = str(binding["incoming_lane_id"])
    junction = root.find(f"./junction[@id='{junction_id}']")
    _require(junction is not None, "OPENDRIVE_JUNCTION_MISSING")
    incoming_road = root.find(f"./road[@id='{incoming_road_id}']")
    _require(incoming_road is not None, "OPENDRIVE_INCOMING_ROAD_MISSING")
    incoming_start, incoming_end = _road_end_heading_degrees(incoming_road)

    branch_rows = []
    for role in ("straight", "right"):
        expected = binding[role]
        road_id = str(expected["connecting_road_id"])
        connection = next(
            (
                item
                for item in junction.findall("connection")
                if item.get("incomingRoad") == incoming_road_id
                and item.get("connectingRoad") == road_id
            ),
            None,
        )
        _require(connection is not None, f"OPENDRIVE_{role.upper()}_CONNECTION_MISSING")
        lane_link = next(
            (
                item
                for item in connection.findall("laneLink")
                if item.get("from") == incoming_lane_id
            ),
            None,
        )
        _require(lane_link is not None, f"OPENDRIVE_{role.upper()}_LANE_LINK_MISSING")
        _require(
            lane_link.get("to") == str(expected["connecting_lane_id"]),
            f"OPENDRIVE_{role.upper()}_LANE_LINK_TARGET_MISMATCH",
        )
        road = root.find(f"./road[@id='{road_id}']")
        _require(road is not None, f"OPENDRIVE_{role.upper()}_ROAD_MISSING")
        predecessor = road.find("./link/predecessor")
        successor = road.find("./link/successor")
        _require(
            predecessor is not None and predecessor.get("elementId") == incoming_road_id,
            f"OPENDRIVE_{role.upper()}_PREDECESSOR_MISMATCH",
        )
        _require(
            successor is not None
            and successor.get("elementId") == str(expected["successor_road_id"]),
            f"OPENDRIVE_{role.upper()}_SUCCESSOR_MISMATCH",
        )
        _, branch_end = _road_end_heading_degrees(road)
        heading_delta = _normalize_degrees(branch_end - incoming_end)
        if role == "straight":
            _require(abs(heading_delta) <= 25.0, "STATIC_STRAIGHT_HEADING_GATE_FAILED")
        else:
            _require(30.0 <= heading_delta <= 120.0, "STATIC_RIGHT_HEADING_GATE_FAILED")
        branch_rows.append(
            {
                "semantic_role": role.upper(),
                "junction_connection_id": connection.get("id"),
                "incoming_road_id": incoming_road_id,
                "incoming_lane_id": incoming_lane_id,
                "connecting_road_id": road_id,
                "connecting_lane_id": lane_link.get("to"),
                "successor_road_id": successor.get("elementId"),
                "signed_heading_delta_degrees": heading_delta,
            }
        )
    return {
        "junction_id": junction_id,
        "incoming_road_id": incoming_road_id,
        "incoming_lane_id": incoming_lane_id,
        "incoming_start_heading_degrees": incoming_start,
        "incoming_end_heading_degrees": incoming_end,
        "branches": branch_rows,
        "straight_and_right_statically_reachable": True,
    }


def _source_contract(plan: Mapping[str, Any]) -> dict[str, Any]:
    paths = plan["source_contract"]
    evaluator_path = Path(paths["evaluator_path"])
    parser_path = Path(paths["route_parser_path"])
    route_scenario_path = Path(paths["route_scenario_path"])
    scenario_manager_path = Path(paths["scenario_manager_path"])
    scenario_class_path = Path(paths["scenario_class_path"])
    capture_agent_path = Path(paths["capture_agent_path"])
    evaluator = evaluator_path.read_text(encoding="utf-8")
    parser = parser_path.read_text(encoding="utf-8")
    route_scenario = route_scenario_path.read_text(encoding="utf-8")
    scenario_manager = scenario_manager_path.read_text(encoding="utf-8")
    scenario_class = scenario_class_path.read_text(encoding="utf-8")
    capture_agent = capture_agent_path.read_text(encoding="utf-8")

    _require("config.scenario_configs[0].name" in evaluator, "EVALUATOR_CONFIG_ZERO_ACCESS_NOT_FOUND")
    _require(
        "for scenario in route.find('scenarios').iter('scenario'):" in parser,
        "ROUTE_PARSER_SCENARIO_GENERATION_NOT_FOUND",
    )
    _require(
        "route_config.scenario_configs = scenario_configs" in parser,
        "ROUTE_PARSER_SCENARIO_ASSIGNMENT_NOT_FOUND",
    )
    _require(
        "scenario_config.trigger_points[0]" in route_scenario,
        "ROUTE_SCENARIO_TRIGGER_ZERO_ACCESS_NOT_FOUND",
    )
    agent_call = scenario_manager.find("ego_action = self._agent_wrapper()")
    tree_tick = scenario_manager.find("self.scenario_tree.tick_once()", agent_call)
    _require(agent_call >= 0 and tree_tick > agent_call, "FIRST_AGENT_CALL_NOT_BEFORE_SCENARIO_TREE_TICK")
    _require(
        "source_transform.location - carla.Location(z=500)" in scenario_class
        and "set_simulate_physics(enabled=False)" in scenario_class
        and "ActorTransformSetter(self.other_actors[0], self._spawn_location)" in scenario_class,
        "SCENARIO_PRETRIGGER_ACTOR_ISOLATION_CONTRACT_MISSING",
    )
    scenario_type = plan["scenario_binding"]["type"]
    _require(scenario_type in _class_names(scenario_class_path), "SCENARIO_TYPE_CLASS_UNSUPPORTED")
    branch_index = capture_agent.find("branch_evidence = discover_straight_right_branches(")
    frozen_index = capture_agent.find("atomic_write_json(frozen_path, frozen)")
    candidate_index = capture_agent.find(
        "for sequence_position, candidate_id in enumerate(self._candidate_order, start=1):"
    )
    _require(
        branch_index >= 0 and frozen_index > branch_index and candidate_index > frozen_index,
        "CANDIDATE_SELECTION_NOT_FROZEN_BEFORE_OUTPUT",
    )
    runtime_fairness_required = plan.get("runtime_fairness_gate_required") is True
    if runtime_fairness_required:
        scenario_source_pin = plan["scenario_binding"].get("scenario_source_sha256")
        _require(
            scenario_source_pin == sha256_file(scenario_class_path),
            "SCENARIO_TRIGGER_CONTRACT_SOURCE_PIN_MISMATCH",
        )
        observer_index = capture_agent.find("def _install_route_scenario_read_only_observer()")
        observer_call_index = capture_agent.find("\n_install_route_scenario_read_only_observer()", observer_index + 1)
        evidence_index = capture_agent.find("scenario_runtime_evidence = build_scenario_runtime_evidence(")
        package_index = capture_agent.find("frozen = build_frozen_package(", evidence_index)
        validation_index = capture_agent.find("frozen_errors = validate_frozen_package(persisted_frozen)")
        candidate_forward_index = capture_agent.find("self._pilot_backend.run_candidate(")
        _require(
            observer_index >= 0 and observer_call_index > observer_index,
            "ROUTE_SCENARIO_READ_ONLY_OBSERVER_MISSING",
        )
        observer_source = capture_agent[observer_index:observer_call_index]
        _require(
            "original_init(instance, *args, **kwargs)" in observer_source
            and "_RUNTIME_ROUTE_SCENARIO = instance" in observer_source
            and all(
                forbidden not in observer_source
                for forbidden in ("set_transform", "set_simulate_physics", "apply_control", "world.tick", "wait_for_tick")
            ),
            "ROUTE_SCENARIO_OBSERVER_NOT_READ_ONLY",
        )
        _require(
            0 <= evidence_index < package_index < frozen_index < validation_index < candidate_forward_index,
            "SCENARIO_FAIRNESS_EVIDENCE_NOT_VALIDATED_PREOUTPUT",
        )
        _require(
            "py_trees.blackboard.Blackboard().get(route_variable)" in capture_agent,
            "ROUTE_BLACKBOARD_TRIGGER_VALUE_READ_MISSING",
        )
    return {
        "evaluator_config_zero_access": True,
        "parser_generates_scenario_configs_from_xml_scenario_elements": True,
        "route_scenario_requires_trigger_points_zero": True,
        "scenario_type_class_supported": True,
        "first_agent_call_precedes_first_scenario_tree_tick": True,
        "scenario_actor_before_first_tree_tick": {
            "spawned_by_runtime_class": True,
            "xml_other_actor_required": False,
            "initial_vertical_offset_metres": -500,
            "physics_enabled": False,
            "activation_requires_actor_transform_setter_tree_tick": True,
        },
        "candidate_branch_selection_before_frozen_persistence": True,
        "frozen_persistence_before_first_candidate_output": True,
        "runtime_scenario_fairness_evidence_gate": runtime_fairness_required,
        "exact_route_scenario_instance_observer": runtime_fairness_required,
        "scenario_evidence_validated_after_atomic_persistence_before_candidate_forward": runtime_fairness_required,
        "route_blackboard_trigger_value_read_only_capture": runtime_fairness_required,
    }


def evaluate_preflight(
    run_spec: Mapping[str, Any],
    capture_plan: Mapping[str, Any],
    *,
    require_output_absence: bool = True,
) -> dict[str, Any]:
    run_id = str(run_spec.get("run_id", ""))
    _require(run_id and run_id != S1_RUN_ID, "RUN_ID_MISSING_OR_REUSES_S1")
    _require(capture_plan.get("run_id") == run_id, "CAPTURE_PLAN_RUN_ID_MISMATCH")
    _require(run_spec.get("run_authorized") is False, "PREPARATION_RUN_MUST_BE_UNAUTHORIZED")
    _require(run_spec.get("automatic_continuation") is False, "AUTOMATIC_CONTINUATION_FORBIDDEN")
    _require(run_spec.get("fresh_observation_count") == 1, "FRESH_OBSERVATION_COUNT_NOT_ONE")
    _require(run_spec.get("repeat_count_per_candidate") == 3, "CANDIDATE_REPEAT_COUNT_NOT_THREE")

    route_binding = capture_plan["route_binding"]
    route_path = Path(route_binding["route_xml_path"])
    xodr_path = Path(capture_plan["map_binding"]["opendrive_path"])
    _require(sha256_file(route_path) == route_binding["route_xml_sha256"], "ROUTE_XML_PIN_MISMATCH")
    _require(
        sha256_file(xodr_path) == capture_plan["map_binding"]["opendrive_sha256"],
        "OPENDRIVE_PIN_MISMATCH",
    )
    route_contract = _parse_route(route_path, route_binding["route_id"], route_binding["town"])
    _require(route_contract["route_statically_takes_right_branch"], "ROUTE_DOES_NOT_TAKE_RIGHT_BRANCH")
    scenario = route_contract["scenario_configs"][0]
    expected_scenario = capture_plan["scenario_binding"]
    _require(scenario["name"] == expected_scenario["name"], "SCENARIO_NAME_MISMATCH")
    _require(scenario["type"] == expected_scenario["type"], "SCENARIO_TYPE_MISMATCH")
    _require(
        scenario["trigger_points"] == [expected_scenario["trigger_point"]],
        "SCENARIO_TRIGGER_MISMATCH",
    )
    branch_contract = _parse_branch_topology(xodr_path, capture_plan["static_branch_contract"])
    source_contract = _source_contract(capture_plan)

    pin_rows = []
    for pin in run_spec.get("source_pins", []):
        path = Path(pin["path"])
        actual = sha256_file(path)
        _require(actual == pin["sha256"], f"SOURCE_PIN_MISMATCH:{pin['role']}")
        pin_rows.append({"role": pin["role"], "path": str(path), "sha256": actual})
    _require(bool(pin_rows), "SOURCE_PINS_EMPTY")

    output_dir = Path(run_spec["runtime_output_directory"])
    output_targets = [Path(item) for item in run_spec["runtime_output_targets"]]
    if require_output_absence:
        _require(output_dir.is_dir(), "RUNTIME_OUTPUT_DIRECTORY_MISSING")
        _require(not any(output_dir.iterdir()), "RUNTIME_OUTPUT_DIRECTORY_NOT_EMPTY")
        existing = [str(path) for path in output_targets if path.exists()]
        _require(not existing, "RUNTIME_OUTPUT_TARGET_ALREADY_EXISTS:" + ",".join(existing))
        supervisor_status = Path(run_spec["supervisor_status_path"])
        _require(not supervisor_status.exists(), "SUPERVISOR_STATUS_ALREADY_EXISTS")

    schedule = capture_plan["candidate_schedule"]
    order = schedule["candidate_order"]
    _require(len(order) == 6, "CANDIDATE_SCHEDULE_LENGTH_NOT_SIX")
    _require(sorted(item for item in order if item.startswith("A")) == ["A1", "A2", "A3"], "A_SCHEDULE_INVALID")
    _require(sorted(item for item in order if item.startswith("B")) == ["B1", "B2", "B3"], "B_SCHEDULE_INVALID")
    regenerated = ["A1", "A2", "A3", "B1", "B2", "B3"]
    random.Random(int(schedule["randomization"]["seed_hex"], 16)).shuffle(regenerated)
    _require(regenerated == order, "CANDIDATE_SCHEDULE_SEED_REPLAY_MISMATCH")
    schedule_evidence = with_hash(
        {
            "schema_version": "driveclarify.maneuver_branch.candidate_schedule.v1",
            "run_id": run_id,
            "candidate_order": list(order),
            "randomization": copy.deepcopy(schedule["randomization"]),
            "frozen_before_runtime": True,
            "candidate_outputs_read": False,
        }
    )
    _require(
        schedule_evidence["evidence_sha256"] == schedule["schedule_sha256"],
        "CANDIDATE_SCHEDULE_HASH_MISMATCH",
    )

    result = {
        "schema_version": PREFLIGHT_SCHEMA,
        "run_id": run_id,
        "status": "PASS_ROUTE_SCENARIO_PREFLIGHT",
        "runtime_launches": 0,
        "model_loads": 0,
        "gpu_contexts_created": 0,
        "route_contract": route_contract,
        "branch_contract": branch_contract,
        "source_contract": source_contract,
        "scenario_non_interference_before_capture": {
            "status": "PASS_STATIC_SOURCE_ORDER",
            "scenario_is_real_supported_type": True,
            "scenario_trigger_tree_ticked_before_first_agent_call": False,
            "runtime_actor_visible_before_first_tree_tick": False,
            "candidate_comparison_uses_one_image_and_no_extra_tick": True,
            "limitation": "Complete RouteScenario construction remains a CARLA-runtime fact for the separately authorized run.",
        },
        "source_pins": pin_rows,
        "source_pin_count": len(pin_rows),
        "output_absence": {
            "required": require_output_absence,
            "runtime_output_directory": str(output_dir),
            "directory_empty": not any(output_dir.iterdir()) if output_dir.is_dir() else False,
            "target_count": len(output_targets),
            "all_targets_absent": not any(path.exists() for path in output_targets),
            "supervisor_status_absent": not Path(run_spec["supervisor_status_path"]).exists(),
        },
        "candidate_selection_reads_model_output": False,
        "new_run_id_distinct_from_s1": True,
    }
    return with_hash(result)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"JSON_ROOT_NOT_OBJECT:{path}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-spec", required=True, type=Path)
    parser.add_argument("--capture-plan", required=True, type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    result = evaluate_preflight(_load_json(args.run_spec), _load_json(args.capture_plan))
    if args.evidence is not None:
        recorded = _load_json(args.evidence)
        _require(recorded == result, "PREPARED_PREFLIGHT_EVIDENCE_MISMATCH")
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
