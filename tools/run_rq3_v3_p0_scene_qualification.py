#!/usr/bin/env python3
"""Prepare, run, and seal RQ3-V3 P0 scene qualification.

This entry point is restricted to engineering-only P0.  It never generates a
formal seed, starts a formal arm, or computes a DriveClarify performance effect.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import secrets
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq3_native_qualification.trace import (  # noqa: E402
    canonical_sha256,
    read_jsonl,
)


STAGE = "RQ3_V3_P0_SCENE_QUALIFICATION_V1"
SCOPE = "ENGINEERING_ONLY_NON_FORMAL"
REPORT = ROOT / "reports/driveclarify_rq3_v3_p0_scene_qualification_v1"
ASSETS = REPORT / "scene_assets"
CONFIGS = REPORT / "configs"
RUNS = REPORT / "native_runs"
PROTOCOL = ROOT / "reports/driveclarify_rq3_v3_simplified_protocol_design_v1"
HISTORICAL_ASSETS = (
    ROOT
    / "reports/driveclarify_rq3_bench2drive_closed_loop_system_validation_v1"
    / "part_b_assets"
)
USC_SOURCE_ROUTE = Path(
    "/home/buaa/wrh/simlingo/leaderboard/data/bench2drive_split/bench2drive_168.xml"
)
CHECKPOINT = (
    ROOT
    / "reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected"
    / "checkpoints/a1_selected.ckpt/pytorch_model.pt"
)
CHECKPOINT_SHA256 = "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"
SCENE_CODES = (
    "REF-EQUIVALENT",
    "REF-CRITICAL",
    "LMK-EQUIVALENT",
    "LMK-CRITICAL",
    "ORD-EQUIVALENT",
    "ORD-CRITICAL",
    "USC-EQUIVALENT",
    "USC-CRITICAL",
)
AMBIGUOUS_INSTRUCTIONS = {
    "REF": "Turn after the white van.",
    "LMK": "Turn at the school.",
    "ORD": "Take the second turn.",
    "USC": "Pull over at a suitable distance.",
}
SEMANTIC_KINDS = {
    "REF": "REFERENTIAL",
    "LMK": "LANDMARK",
    "ORD": "SPATIAL_ORDER",
    "USC": "UNDERSPECIFIED_CONSTRAINT",
}
CANONICAL_NATIVE_INSTRUCTION = (
    "Follow the assigned route to the resolved candidate-A destination."
)
OLD_USC_SCENE_ID = "RQ3-FORMAL-USC-EQUIVALENT-F07"
OLD_USC_ROUTE = HISTORICAL_ASSETS / "RQ3-FORMAL-USC-EQUIVALENT-ROUTE.xml"
SOURCE_PATHS = (
    ROOT / "driveclarify_rq3_native_qualification/__init__.py",
    ROOT / "driveclarify_rq3_native_qualification/liveness.py",
    ROOT / "driveclarify_rq3_native_qualification/trace.py",
    ROOT / "driveclarify_rq3_native_qualification/simlingo_agent.py",
    ROOT / "driveclarify_rq3/simlingo_agent.py",
    ROOT / "driveclarify_rq1_v2/simlingo_agent.py",
    ROOT / "driveclarify_rq1_v2/consequence.py",
    ROOT / "driveclarify_clear_passthrough_v11/simlingo_agent.py",
    Path("/home/buaa/wrh/simlingo/team_code/agent_simlingo.py"),
    Path("/home/buaa/wrh/simlingo/team_code/nav_planner.py"),
    Path(
        "/home/buaa/wrh/simlingo/leaderboard_autopilot/leaderboard/"
        "leaderboard_evaluator.py"
    ),
    Path(
        "/home/buaa/wrh/simlingo/leaderboard_autopilot/leaderboard/scenarios/"
        "route_scenario.py"
    ),
    Path(
        "/home/buaa/wrh/simlingo/scenario_runner_autopilot/srunner/"
        "scenariomanager/scenario_manager.py"
    ),
    ROOT
    / "reports/driveclarify_v10_clear_passthrough_safe_replan_method_development/"
    "startup_diagnostics/carla_epic_quality_launcher_v10.sh",
    ROOT / "tools/run_rq3_v3_p0_scene_qualification.py",
    ROOT / "tools/run_rq3_v3_p0_scene_qualification_episode.sh",
)
PROTECTED_TREES = (
    ROOT / "reports/driveclarify_rq1_v2_consequence_selective_clarification_v2",
    ROOT / "reports/driveclarify_rq2_t_cg_formal_48_episode_experiment_v1",
    ROOT / "reports/driveclarify_rq3_bench2drive_closed_loop_system_validation_v1",
    ROOT / "reports/driveclarify_rq3_v2_bench2drive_closed_loop_validation_v1",
    ROOT / "reports/driveclarify_rq3_v2_postmortem_engineering_diagnosis_v1",
    ROOT / "reports/driveclarify_rq3_native_execution_engineering_qualification_v1",
)
PERSISTENT_STOP_CLASSES = {
    "NATIVE_STATIONARY_COMMANDING_STOP",
    "NATIVE_STATIONARY_DESPITE_FORWARD_COMMAND",
}


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest(value: Any) -> str:
    return canonical_sha256(value)


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def hash_record(path: Path) -> Dict[str, Any]:
    return {
        "path": rel(path),
        "bytes": path.stat().st_size,
        "sha256": sha_file(path),
    }


def tree_record(path: Path) -> Dict[str, Any]:
    rows = [hash_record(child) for child in sorted(path.rglob("*")) if child.is_file()]
    return {
        "path": rel(path),
        "file_count": len(rows),
        "tree_digest": digest(rows),
    }


def source_rows() -> List[Dict[str, Any]]:
    missing = [str(path) for path in SOURCE_PATHS if not path.is_file()]
    if missing:
        raise RuntimeError("P0_SOURCE_MISSING:" + ",".join(missing))
    return [hash_record(path) for path in SOURCE_PATHS]


def route_xml(route_id: int, points: Sequence[Sequence[float]]) -> str:
    positions = "\n".join(
        '      <position x="%.12f" y="%.12f" z="%.12f" />'
        % (float(point[0]), float(point[1]), float(point[2]))
        for point in points
    )
    return f'''<routes>
  <route id="{route_id}" town="Town05">
    <waypoints>
{positions}
    </waypoints>
    <scenarios />
    <weathers>
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="0" />
      <weather cloudiness="0.0" fog_density="0.0" precipitation="0.0" precipitation_deposits="0.0" sun_altitude_angle="70.0" sun_azimuth_angle="0.0" wetness="0.0" wind_intensity="0.0" route_percentage="100" />
    </weathers>
  </route>
</routes>
'''


def parse_route(path: Path) -> Tuple[str, str, List[List[float]]]:
    route = ET.parse(str(path)).getroot().find("route")
    if route is None:
        raise RuntimeError("ROUTE_ELEMENT_MISSING:" + str(path))
    points = [
        [float(position.attrib[key]) for key in ("x", "y", "z")]
        for position in route.find("waypoints").findall("position")
    ]
    return str(route.attrib["id"]), str(route.attrib["town"]), points


def task_signature(scene_code: str, candidate: str) -> Dict[str, Any]:
    family, level = scene_code.split("-")
    if scene_code == "USC-EQUIVALENT":
        relevant = [
            "terminal_task_region",
            "terminal_road_or_corridor",
            "maneuver_obligation",
            "irreversible_branch_obligation",
            "goal_lane_or_side_obligation_if_task_relevant",
            "task_completion_region",
        ]
        values = {
            "terminal_task_region": "RQ3V3-USC-PULL-OVER-ZONE-ROAD37-S140-S155",
            "terminal_road_or_corridor": "TOWN05-ROAD37-SECTION0-WESTBOUND",
            "maneuver_obligation": "CONTINUE-THEN-STOP-IN-SHARED-PULL-OVER-ZONE",
            "irreversible_branch_obligation": "NO-BRANCH-ROAD37-CONTINUATION",
            "goal_lane_or_side_obligation_if_task_relevant": "ROAD37-LANE-MINUS3-RIGHT-OUTER-DRIVING-LANE",
            "task_completion_region": "RQ3V3-USC-SHARED-COMPLETION-POLYGON-01",
        }
    else:
        relevant = {
            "REF": [
                "terminal_task_region",
                "irreversible_branch_obligation",
                "task_completion_region",
            ],
            "LMK": [
                "terminal_task_region",
                "terminal_road_or_corridor",
                "task_completion_region",
            ],
            "ORD": [
                "maneuver_obligation",
                "irreversible_branch_obligation",
                "task_completion_region",
            ],
            "USC": [
                "goal_lane_or_side_obligation_if_task_relevant",
                "task_completion_region",
            ],
        }[family]
        prefix = "RQ3V3-P0-%s" % family
        values = {
            "terminal_task_region": prefix + "-CERTIFIED-TASK-REGION-A",
            "terminal_road_or_corridor": prefix + "-CERTIFIED-CORRIDOR-A",
            "maneuver_obligation": prefix + "-CERTIFIED-MANEUVER-A",
            "irreversible_branch_obligation": prefix + "-CERTIFIED-BRANCH-A",
            "goal_lane_or_side_obligation_if_task_relevant": prefix
            + "-CERTIFIED-SIDE-A",
            "task_completion_region": prefix + "-CERTIFIED-COMPLETION-A",
        }
        if level == "CRITICAL" and candidate == "B":
            changed = relevant[-1]
            values[changed] = prefix + "-CERTIFIED-%s-B" % changed.upper()
    return {
        "candidate_id": candidate,
        "relevant_components": relevant,
        "certified": True,
        "certificate_id": "RQ1V2-RQ3V3-P0-CERT-%s-%s" % (scene_code, candidate),
        **values,
    }


def static_semantic_check(scene: Mapping[str, Any]) -> Dict[str, Any]:
    from driveclarify_clear_passthrough_v11.ambiguity_gate import AmbiguityGate
    from driveclarify_clear_passthrough_v11.contracts import (
        GateInput,
        GroundingEvidence,
    )
    from driveclarify_rq1_v2.consequence import (
        AmbiguityStatus,
        TaskSignature,
        compare_task_signatures,
        consequence_gate,
    )

    gate = AmbiguityGate().evaluate(
        GateInput(
            instruction=scene["ambiguous_instruction"],
            grounding=GroundingEvidence(
                2,
                "VERIFIED",
                "FROZEN_CERTIFIED_CURRENT_SCENE_GROUNDING",
                100,
                100,
            ),
            runtime_evidence={
                "map_name": "Town05",
                "runtime_reasonable_option_count": 2,
                "route_owner": "SIMLINGO",
            },
        )
    )
    first, second = (
        TaskSignature.from_mapping(row) for row in scene["task_signatures"]
    )
    comparison = compare_task_signatures(first, second)
    decision = consequence_gate(
        AmbiguityStatus.AMBIGUOUS,
        comparison.relation,
        deterministic_candidate_id="A",
    )
    expected_relation = (
        "TASK_EQUIVALENT"
        if scene["consequence_level"] == "EQUIVALENT"
        else "TASK_CRITICAL"
    )
    expected_action = (
        "ACT" if scene["consequence_level"] == "EQUIVALENT" else "ASK"
    )
    result = {
        "ambiguity_gate_decision": gate.decision.value,
        "semantic_kind": gate.semantic_kind,
        "expected_semantic_kind": SEMANTIC_KINDS[scene["family"]],
        "task_relation": comparison.relation.value,
        "compared_components": list(comparison.compared_components),
        "differing_components": list(comparison.differing_components),
        "comparison_reason_codes": list(comparison.reason_codes),
        "policy_action": decision.action.value,
    }
    result["pass"] = (
        result["ambiguity_gate_decision"] == "AMBIGUOUS"
        and result["semantic_kind"] == result["expected_semantic_kind"]
        and result["task_relation"] == expected_relation
        and result["policy_action"] == expected_action
    )
    return result


def _historical_scene_assets(scene_code: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    source = load(HISTORICAL_ASSETS / (scene_code + "-CANDIDATE-ROUTES.json"))
    center = load(HISTORICAL_ASSETS / (scene_code + "-CENTER-ROUTE.json"))
    return [dict(row) for row in center["center_route"]], source


def _usc_replacement_assets() -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    source_route = ET.parse(str(USC_SOURCE_ROUTE)).getroot().find("route")
    if source_route is None or source_route.attrib.get("id") != "25381":
        raise RuntimeError("USC_SOURCE_ROUTE_IDENTITY_INVALID")
    raw = [
        [float(position.attrib[key]) for key in ("x", "y", "z")]
        for position in source_route.find("waypoints").findall("position")
    ]
    if len(raw) != 68:
        raise RuntimeError("USC_SOURCE_DENSE_ROUTE_LENGTH_INVALID")
    candidate_a_index = next(index for index, point in enumerate(raw) if point[0] == -138.9)
    candidate_b_index = next(index for index, point in enumerate(raw) if point[0] == -147.5)
    rows = [
        {"xyz": point, "road_option": "LANEFOLLOW"}
        for point in raw
    ]
    candidate_a = rows[: candidate_a_index + 1]
    candidate_b = rows[: candidate_b_index + 1]
    candidate_source = {
        "schema": "driveclarify.rq3-v3-p0.usc-equivalent-candidate-routes.v1",
        "scene_code": "USC-EQUIVALENT",
        "map": "Town05",
        "source_route": str(USC_SOURCE_ROUTE),
        "source_route_id": "25381",
        "source_route_sha256": sha_file(USC_SOURCE_ROUTE),
        "source_selection_rule": (
            "maximum chord/path ratio among Town05 Bench2Drive source routes; "
            "map geometry only; no DriveClarify or native outcome used"
        ),
        "source_chord_path_ratio": 0.9979987414134941,
        "candidate_A": candidate_a,
        "candidate_B": candidate_b,
        "candidate_A_stop_xyz": candidate_a[-1]["xyz"],
        "candidate_B_stop_xyz": candidate_b[-1]["xyz"],
        "longitudinal_separation_m": math.dist(
            candidate_a[-1]["xyz"], candidate_b[-1]["xyz"]
        ),
        "shared_completion_polygon_xyz": [
            [-136.0, -210.0, 10.0],
            [-152.5, -210.0, 10.0],
            [-152.5, -204.5, 10.0],
            [-136.0, -204.5, 10.0],
        ],
        "same_road_section_lane": {
            "road_id": 37,
            "section_id": 0,
            "lane_id": -3,
            "junction": False,
        },
        "same_side_lane_maneuver_branch_goal_category": True,
        "difference_limited_to_continuous_longitudinal_stopping_placement": True,
    }
    candidate_source["route_source_digest"] = digest(candidate_source)
    center = rows
    geometry = {
        "official_resolved_route": candidate_a,
        "candidate_A": candidate_a,
        "candidate_B": candidate_b,
        "center_route": center,
    }
    return center, candidate_source, geometry


def freeze_scenes() -> Dict[str, Any]:
    if REPORT.exists():
        raise RuntimeError("P0_REPORT_DIRECTORY_ALREADY_EXISTS")
    protocol = load(PROTOCOL / "RQ3_V3_SIMPLIFIED_PROTOCOL.json", {})
    if protocol.get("proposal_status") != (
        "READY_RQ3_V3_SIMPLIFIED_PROTOCOL_FOR_P0_AUTHORIZATION"
    ):
        raise RuntimeError("P0_PROTOCOL_ENTRY_STATE_INVALID")
    if sha_file(CHECKPOINT) != CHECKPOINT_SHA256:
        raise RuntimeError("P0_CHECKPOINT_DRIFT_BEFORE_SCENE_FREEZE")
    REPORT.mkdir(parents=True)
    ASSETS.mkdir()
    protected = [tree_record(path) for path in PROTECTED_TREES]
    scenes: List[Dict[str, Any]] = []
    usc_geometry: Optional[Dict[str, Any]] = None
    for index, scene_code in enumerate(SCENE_CODES, 1):
        family, level = scene_code.split("-")
        scene_id = "RQ3V3-P0-%s-Q%02d" % (scene_code, index)
        route_id = 997400 + index
        if scene_code == "USC-EQUIVALENT":
            center, source, usc_geometry = _usc_replacement_assets()
            route_rows = usc_geometry["official_resolved_route"]
            heritage = {
                "kind": "SOLE_PROSPECTIVE_REPLACEMENT",
                "old_scene_id": OLD_USC_SCENE_ID,
                "old_scene_ineligible": True,
            }
        else:
            center, source_old = _historical_scene_assets(scene_code)
            source = {
                key: value
                for key, value in source_old.items()
                if key not in {"route_source_digest", "designation", "fresh_rq3_identity"}
            }
            source["schema"] = "driveclarify.rq3-v3-p0.candidate-route-source.v1"
            source["prospective_scene_id"] = scene_id
            source["source_instance"] = (
                "accepted RQ3 Part-B concrete instance, already fresh relative "
                "to the main RQ1 formal scene set"
            )
            source["source_instance_sha256"] = sha_file(
                HISTORICAL_ASSETS / (scene_code + "-CANDIDATE-ROUTES.json")
            )
            source["route_source_digest"] = digest(source)
            _, _, old_points = parse_route(
                HISTORICAL_ASSETS / ("RQ3-FORMAL-%s-ROUTE.xml" % scene_code)
            )
            route_rows = [
                {"xyz": old_points[0], "road_option": "LANEFOLLOW"},
                {"xyz": old_points[-1], "road_option": "LANEFOLLOW"},
            ]
            heritage = {
                "kind": "NEW_PROSPECTIVE_IDENTITY_OF_ACCEPTED_RQ3_INSTANCE",
                "source_scene_code": scene_code,
                "selection_used_p0_or_driveclarify_outcome": False,
            }
        route_path = ASSETS / (scene_id + "-ROUTE.xml")
        route_points = [row["xyz"] for row in route_rows]
        write_text(route_path, route_xml(route_id, route_points))
        center_path = ASSETS / (scene_id + "-CENTER-ROUTE.json")
        center_value = {
            "schema": "driveclarify.rq3-v3-p0.center-route.v1",
            "scene_id": scene_id,
            "center_route": center,
        }
        center_value["center_route_digest"] = digest(center_value)
        write_json(center_path, center_value)
        source_path = ASSETS / (scene_id + "-CANDIDATE-ROUTES.json")
        write_json(source_path, source)
        layout_path = ASSETS / (scene_id + "-LAYOUT.json")
        layout = {
            "schema": "driveclarify.rq3-v3-p0.scene-layout.v1",
            "layout_id": scene_id + "-LAYOUT",
            "scene_id": scene_id,
            "scientific_entities": [
                {
                    "entity_id": scene_id + "-A",
                    "scientific_role": family + "_CANDIDATE_A",
                    "spawned_actor": False,
                },
                {
                    "entity_id": scene_id + "-B",
                    "scientific_role": family + "_CANDIDATE_B",
                    "spawned_actor": False,
                },
            ],
            "official_scenario_owned_actors_preserved": True,
            "official_scenario_actor_count": 0,
            "random_background_vehicle_count": 0,
            "traffic_manager_random_generation_enabled": False,
            "weather": {
                "cloudiness": 0.0,
                "fog_density": 0.0,
                "precipitation": 0.0,
                "sun_altitude_angle": 70.0,
                "wetness": 0.0,
            },
        }
        layout["layout_digest"] = digest(layout)
        write_json(layout_path, layout)
        scene = {
            "schema": "driveclarify.rq3-v3-p0.scene-contract.v1",
            "stage": STAGE,
            "scope": SCOPE,
            "scene_id": scene_id,
            "scene_code": scene_code,
            "family": family,
            "consequence_level": level,
            "machine_label": (
                "TASK_EQUIVALENT" if level == "EQUIVALENT" else "TASK_CRITICAL"
            ),
            "correct_driveclarify_behavior": "ACT" if level == "EQUIVALENT" else "ASK",
            "ambiguous_instruction": AMBIGUOUS_INSTRUCTIONS[family],
            "p0_resolved_canonical_instruction": CANONICAL_NATIVE_INSTRUCTION,
            "native_map": "Town05",
            "route_id": str(route_id),
            "native_route_path": rel(route_path),
            "center_route_source": rel(center_path),
            "candidate_route_source": rel(source_path),
            "layout_path": rel(layout_path),
            "task_signatures": [
                task_signature(scene_code, candidate) for candidate in ("A", "B")
            ],
            "reasonable_interpretation_count": 2,
            "grounding_certified_before_p0_seed_generation": True,
            "canonical_candidate": "A",
            "canonical_target_encoded_by_official_route": True,
            "route_owner_expectation": (
                "OFFICIAL_LEADERBOARD_GLOBAL_PLAN_TO_UNCHANGED_SIMLINGO_ROUTEPLANNER"
            ),
            "active_control_owner": "agent_simlingo.LingoAgent",
            "controller": "agent_simlingo.LingoAgent.control_pid",
            "random_background_vehicle_count": 0,
            "heritage": heritage,
        }
        scene["semantic_certification"] = static_semantic_check(scene)
        if not scene["semantic_certification"]["pass"]:
            raise RuntimeError("P0_SCENE_SEMANTIC_CERTIFICATION_FAILED:" + scene_code)
        scene["scene_digest"] = digest(scene)
        scene_path = ASSETS / (scene_id + "-SCENE.json")
        write_json(scene_path, scene)
        scenes.append(
            {
                **scene,
                "scene_contract_path": rel(scene_path),
                "scene_contract_sha256": sha_file(scene_path),
                "native_route_sha256": sha_file(route_path),
                "center_route_sha256": sha_file(center_path),
                "candidate_route_sha256": sha_file(source_path),
                "layout_sha256": sha_file(layout_path),
            }
        )
    manifest = {
        "schema": "driveclarify.rq3-v3-p0.scene-manifest.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "status": "EIGHT_SCENES_FIXED_BEFORE_P0_SEED_GENERATION",
        "condition_order": list(SCENE_CODES),
        "scene_count": len(scenes),
        "scientific_components_changed": [],
        "formal_seed_generation": False,
        "formal_execution": False,
        "scenes": scenes,
    }
    manifest["manifest_digest"] = digest(manifest)
    write_json(REPORT / "P0_SCENE_MANIFEST.json", manifest)

    usc_scene = next(row for row in scenes if row["scene_code"] == "USC-EQUIVALENT")
    usc_cert = {
        "schema": "driveclarify.rq3-v3-p0.usc-equivalent-replacement-certification.v1",
        "stage": STAGE,
        "status": "PASS_USC_EQUIVALENT_REPLACEMENT_CERTIFIED_BEFORE_P0_EXPOSURE",
        "scene_id": usc_scene["scene_id"],
        "old_v2_scene_id": OLD_USC_SCENE_ID,
        "old_v2_scene_excluded": True,
        "old_v2_route": hash_record(OLD_USC_ROUTE),
        "replacement_route": hash_record(ROOT / usc_scene["native_route_path"]),
        "instruction": usc_scene["ambiguous_instruction"],
        "p0_resolved_canonical_instruction": usc_scene[
            "p0_resolved_canonical_instruction"
        ],
        "ambiguity_family": "USC",
        "scientific_meaning": "CONTINUOUS_SPATIAL_UNDERSPECIFICATION",
        "candidate_interpretations": {
            "A": {
                "meaning": "nearer longitudinal stopping placement",
                "stop_xyz": usc_geometry["candidate_A"][-1]["xyz"],
                "route_point_count": len(usc_geometry["candidate_A"]),
            },
            "B": {
                "meaning": "farther longitudinal stopping placement",
                "stop_xyz": usc_geometry["candidate_B"][-1]["xyz"],
                "route_point_count": len(usc_geometry["candidate_B"]),
            },
        },
        "task_signatures": usc_scene["task_signatures"],
        "frozen_rq1_comparator_result": usc_scene["semantic_certification"],
        "task_relation": "TASK_EQUIVALENT",
        "correct_driveclarify_behavior": "ACT",
        "all_rq1_task_signature_fields_equal": all(
            usc_scene["task_signatures"][0].get(key)
            == usc_scene["task_signatures"][1].get(key)
            for key in (
                "terminal_task_region",
                "terminal_road_or_corridor",
                "maneuver_obligation",
                "irreversible_branch_obligation",
                "goal_lane_or_side_obligation_if_task_relevant",
                "task_completion_region",
            )
        ),
        "interpretations_differ_only_in_continuous_longitudinal_placement": True,
        "single_replacement_constructed": True,
        "selection_used_driveclarify_outcome": False,
        "selection_used_p0_outcome": False,
        "host_viability_basis": (
            "map-valid continuous non-junction Road 37 lane -3 geometry, clear "
            "weather, no scenario actors, and a 122 m approach to candidate A"
        ),
        "why_scientific_role_is_preserved": (
            "the wording and USC family remain unchanged; two plausible continuous "
            "longitudinal placements share corridor, task region, side/lane, "
            "maneuver, branch obligation, and completion region"
        ),
        "why_old_scene_is_excluded": (
            "the V2 scene is permanently historical and showed repeated native "
            "stationary-commanding-stop behavior; it is not reused or overwritten"
        ),
    }
    usc_cert["certification_digest"] = digest(usc_cert)
    write_json(REPORT / "USC_EQUIVALENT_REPLACEMENT_CERTIFICATION.json", usc_cert)
    write_text(
        REPORT / "USC_EQUIVALENT_REPLACEMENT_CERTIFICATION.md",
        "# USC-EQUIVALENT replacement certification\n\n"
        "Status: `PASS_USC_EQUIVALENT_REPLACEMENT_CERTIFIED_BEFORE_P0_EXPOSURE`.\n\n"
        "The sole replacement is `%s`. It keeps the exact ambiguous instruction "
        "“Pull over at a suitable distance.” and the USC continuous-spatial role. "
        "Interpretations A and B are longitudinal stop points at `%s` and `%s` on "
        "Town05 road 37, section 0, lane -3, inside one shared completion polygon. "
        "They differ only in continuous longitudinal placement.\n\n"
        "The unchanged RQ1 TaskSignature comparator returns `TASK_EQUIVALENT`; all "
        "six task fields match, and the unchanged consequence gate returns `ACT`. "
        "Geometry was selected from map validity and straightness before seed "
        "generation; no P0 or DriveClarify outcome selected it.\n\n"
        "The old `%s` scene remains byte-preserved historical stress evidence and "
        "is excluded because repeated native stationary-commanding-stop behavior "
        "made it unsuitable as the prospective V3 completion scene.\n"
        % (
            usc_scene["scene_id"],
            usc_geometry["candidate_A"][-1]["xyz"],
            usc_geometry["candidate_B"][-1]["xyz"],
            OLD_USC_SCENE_ID,
        ),
    )
    asset_paths = sorted(path for path in ASSETS.rglob("*") if path.is_file())
    freeze = {
        "schema": "driveclarify.rq3-v3-p0.scene-freeze-receipt.v1",
        "stage": STAGE,
        "status": "FROZEN_EIGHT_SCENES_BEFORE_P0_SEED_GENERATION",
        "authorization": "AUTHORIZED_RQ3_V3_P0_SCENE_QUALIFICATION_ONLY",
        "protocol_entry_status": protocol["proposal_status"],
        "manifest_digest": manifest["manifest_digest"],
        "scene_ids": [scene["scene_id"] for scene in scenes],
        "scene_assets": [hash_record(path) for path in asset_paths],
        "usc_certification": hash_record(
            REPORT / "USC_EQUIVALENT_REPLACEMENT_CERTIFICATION.json"
        ),
        "checkpoint": hash_record(CHECKPOINT),
        "checkpoint_expected_sha256": CHECKPOINT_SHA256,
        "source_rows": source_rows(),
        "source_digest": digest(source_rows()),
        "protected_tree_entry_rows": protected,
        "p0_seed_count_at_freeze": 0,
        "formal_seed_count_at_freeze": 0,
        "formal_execution_started": False,
        "scientific_components_changed": [],
        "route_owner_expectation": (
            "OFFICIAL_LEADERBOARD_GLOBAL_PLAN_TO_UNCHANGED_SIMLINGO_ROUTEPLANNER"
        ),
        "evaluator_timeout_convention": {
            "leaderboard_sensor_timeout_s": 480,
            "outer_wallclock_emergency_cap_s": 3600,
            "valid_failure_requires_authoritative_record": True,
        },
        "per_run_pass_rule": {
            "identity_binding_evidence": "ALL_PASS",
            "official_completion": "Completed or Route Completion >=95%",
            "persistent_native_stop_before_goal": "ABSENT",
            "observer_second_writer": "ABSENT",
        },
        "per_scene_pass_rule": {
            "valid_binding_and_complete_evidence": "3/3",
            "meaningful_native_execution_without_persistent_stop": ">=2/3",
            "repeated_common_binding_or_persistent_stop_defect": "ABSENT",
        },
        "seed_generation_procedure": (
            "Python 3.13.5 secrets.randbelow(2**31 - 1) + 1; ordered draws; "
            "reject historical/current textual occurrence; three identities "
            "crossed with all eight scenes"
        ),
        "run_order_rule": "canonical condition order, then seed slots 1..3",
    }
    freeze["freeze_digest"] = digest(freeze)
    write_json(REPORT / "P0_SCENE_FREEZE_RECEIPT.json", freeze)
    write_text(
        REPORT / "COMMAND_LOG.md",
        "# Command log\n\n"
        "- `python tools/run_rq3_v3_p0_scene_qualification.py freeze-scenes`\n"
        "  - fixed eight scenes before any P0 seed existed\n"
        "  - formal seeds generated: `NO`\n"
        "  - formal execution started: `NO`\n",
    )
    return freeze


def verify_scene_freeze() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    manifest = load(REPORT / "P0_SCENE_MANIFEST.json", {})
    freeze = load(REPORT / "P0_SCENE_FREEZE_RECEIPT.json", {})
    if freeze.get("status") != "FROZEN_EIGHT_SCENES_BEFORE_P0_SEED_GENERATION":
        raise RuntimeError("P0_SCENE_FREEZE_MISSING")
    if digest({key: value for key, value in manifest.items() if key != "manifest_digest"}) != manifest.get("manifest_digest"):
        raise RuntimeError("P0_SCENE_MANIFEST_DIGEST_MISMATCH")
    if digest({key: value for key, value in freeze.items() if key != "freeze_digest"}) != freeze.get("freeze_digest"):
        raise RuntimeError("P0_SCENE_FREEZE_DIGEST_MISMATCH")
    for record in freeze.get("scene_assets", []):
        path = ROOT / record["path"]
        if not path.is_file() or sha_file(path) != record["sha256"]:
            raise RuntimeError("P0_FROZEN_SCENE_ASSET_DRIFT:" + record["path"])
    return manifest, freeze


def seed_occurrences(seed: int) -> List[str]:
    completed = subprocess.run(
        ["rg", "-l", "--fixed-strings", str(seed), str(ROOT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return sorted(line for line in completed.stdout.splitlines() if line)


def generate_fresh_seeds(count: int) -> Tuple[List[int], List[Dict[str, Any]]]:
    admitted: List[int] = []
    rejected: List[Dict[str, Any]] = []
    for _ in range(1000):
        candidate = secrets.randbelow(2**31 - 1) + 1
        occurrences = seed_occurrences(candidate)
        if candidate in admitted or occurrences:
            rejected.append(
                {"candidate": candidate, "reason": "DUPLICATE_OR_PRIOR_OCCURRENCE", "occurrences": occurrences}
            )
            continue
        admitted.append(candidate)
        if len(admitted) == count:
            return admitted, rejected
    raise RuntimeError("P0_UNABLE_TO_GENERATE_FRESH_ENGINEERING_SEEDS")


def make_config(run_id: str, scene: Mapping[str, Any], seed: int) -> Dict[str, Any]:
    return {
        "schema": "driveclarify.v11.native-runtime-config.v1",
        "run_id": run_id,
        "mode": "NATIVE_SIMLINGO",
        "checkpoint": str(CHECKPOINT),
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "a1_trainable_parameters": 896,
        "training_performed": False,
        "observation_window_ticks": 10000,
        "receipt_completion_mode": "NATURAL_EVALUATOR_DESTROY",
        "nonprogress_diagnostic_window_ticks": 80,
        "qualification_liveness_simulation_window_s": 4.0,
        "qualification_liveness_wall_stall_s": 10.0,
        "qualification_actor_sample_interval_frames": 5,
        "qualification_trace_fsync_interval": 20,
        "method_input": {
            "instruction": scene["p0_resolved_canonical_instruction"],
            "grounding_evidence_status": "VERIFIED",
            "grounding_evidence_source": "FROZEN_P0_SCENE_AND_OFFICIAL_ROUTE",
            "background_traffic_policy": {
                "random_background_vehicle_count": 0,
                "traffic_manager_random_generation_enabled": False,
                "retained_scientific_actors": [],
            },
            "task_signatures": scene["task_signatures"],
            "p0_scene_binding": {
                "stage": STAGE,
                "scene_id": scene["scene_id"],
                "scene_code": scene["scene_code"],
                "scene_digest": scene["scene_digest"],
                "route_id": scene["route_id"],
                "canonical_candidate": "A",
                "driveclarify_decision_exercised": False,
            },
        },
        "engineering_qualification": {
            "stage": STAGE,
            "scope": SCOPE,
            "scene_id": scene["scene_id"],
            "scene_code": scene["scene_code"],
            "future_scientific_denominator_eligible": False,
            "permanently_formal_ineligible_seed": seed,
            "scientific_behavior_changes": [],
        },
        "scientific_seed_not_available_to_method": seed,
    }


def freeze_roster() -> Dict[str, Any]:
    manifest, scene_freeze = verify_scene_freeze()
    if (REPORT / "P0_SEED_FRESHNESS_RECEIPT.json").exists():
        raise RuntimeError("P0_SEEDS_ALREADY_GENERATED")
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("P0_SEED_GENERATOR_MUST_BE_PYTHON_3_13")
    seeds, rejected = generate_fresh_seeds(3)
    CONFIGS.mkdir()
    RUNS.mkdir()
    runs = []
    ordinal = 0
    for scene in manifest["scenes"]:
        for seed_slot, seed in enumerate(seeds, 1):
            ordinal += 1
            run_id = "RQ3V3-P0-%s-S%02d" % (scene["scene_code"], seed_slot)
            config_path = CONFIGS / (run_id + ".json")
            write_json(config_path, make_config(run_id, scene, seed))
            route_path = ROOT / scene["native_route_path"]
            runs.append(
                {
                    "ordinal": ordinal,
                    "run_id": run_id,
                    "scene_id": scene["scene_id"],
                    "scene_code": scene["scene_code"],
                    "scene_digest": scene["scene_digest"],
                    "seed_slot": seed_slot,
                    "engineering_seed": seed,
                    "runtime_mode": "NATIVE_DEFAULT",
                    "agent_mode": "NATIVE_SIMLINGO",
                    "config_path": rel(config_path),
                    "config_sha256": sha_file(config_path),
                    "route_path": rel(route_path),
                    "route_sha256": sha_file(route_path),
                    "route_id": scene["route_id"],
                    "town": "Town05",
                    "rpc_port": 31000 + (ordinal - 1) * 200,
                    "output_path": rel(RUNS / run_id),
                    "classification": SCOPE,
                    "future_scientific_denominator_eligible": False,
                }
            )
    roster = {
        "schema": "driveclarify.rq3-v3-p0.execution-roster.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "status": "FROZEN_UNEXECUTED",
        "scene_manifest_digest": manifest["manifest_digest"],
        "scene_freeze_digest": scene_freeze["freeze_digest"],
        "scene_count": 8,
        "seed_identity_count": 3,
        "planned_native_runs": 24,
        "order_rule": "canonical condition order, then seed slots 1..3",
        "runs": runs,
    }
    roster["roster_digest"] = digest(roster)
    write_json(REPORT / "P0_EXECUTION_ROSTER.json", roster)
    seed_receipt = {
        "schema": "driveclarify.rq3-v3-p0.seed-freshness-receipt.v1",
        "stage": STAGE,
        "status": "PASS_P0_ENGINEERING_SEEDS_FRESH_AND_EXCLUDED",
        "generated_after_scene_freeze": True,
        "scene_freeze_digest": scene_freeze["freeze_digest"],
        "generator_python_version": sys.version.split()[0],
        "generator": "secrets.randbelow(2**31 - 1) + 1",
        "ordered_p0_engineering_seed_identities": seeds,
        "seed_assignment": "each identity crossed with all eight P0 scenes",
        "pairwise_distinct": len(set(seeds)) == 3,
        "historical_workspace_prior_occurrences": {str(seed): [] for seed in seeds},
        "historical_scientific_and_engineering_freshness": "PASS",
        "rejected_draws": rejected,
        "permanently_excluded_from_future_formal_science": seeds,
        "future_part_a_formal_pool_overlap": "ZERO_BY_CONTRACT_NOT_GENERATED",
        "future_part_b_formal_pool_overlap": "ZERO_BY_CONTRACT_NOT_GENERATED",
        "formal_seed_generated": False,
        "formal_execution_started": False,
    }
    seed_receipt["receipt_digest"] = digest(seed_receipt)
    write_json(REPORT / "P0_SEED_FRESHNESS_RECEIPT.json", seed_receipt)
    ledger = {
        "schema": "driveclarify.rq3-v3-p0.execution-ledger.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "status": "FROZEN_UNEXECUTED",
        "roster_digest": roster["roster_digest"],
        "planned_runs": 24,
        "attempted_runs": 0,
        "entries": [
            {
                **run,
                "execution_status": "PENDING",
                "validity": None,
                "native_qualification_pass": None,
                "disposition": None,
            }
            for run in runs
        ],
    }
    ledger["ledger_digest"] = digest(ledger)
    write_json(REPORT / "P0_EXECUTION_LEDGER.json", ledger)
    integrity = {
        "schema": "driveclarify.rq3-v3-p0.source-integrity-receipt.v1",
        "stage": STAGE,
        "status": "PASS_PREEXECUTION_SOURCE_AND_CHECKPOINT_INTEGRITY",
        "frozen_source_rows": source_rows(),
        "frozen_source_digest": digest(source_rows()),
        "checkpoint": hash_record(CHECKPOINT),
        "protected_tree_entry_rows": scene_freeze["protected_tree_entry_rows"],
        "current_source_rows": source_rows(),
        "source_changes": [],
        "protected_tree_changes": [],
        "scientific_components_changed": [],
        "formal_execution_started": False,
    }
    integrity["receipt_digest"] = digest(integrity)
    write_json(REPORT / "P0_SOURCE_INTEGRITY_RECEIPT.json", integrity)
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n- `python tools/run_rq3_v3_p0_scene_qualification.py freeze-roster`\n"
            "  - generated three engineering-only identities after scene freeze\n"
            "  - froze the 24-run order before execution\n"
            "  - formal seeds generated: `NO`\n"
        )
    return roster


def official_result(path: Path) -> Dict[str, Any]:
    value = load(path, {})
    checkpoint = value.get("_checkpoint") or {}
    records = checkpoint.get("records") or []
    record = records[-1] if records else None
    global_record = checkpoint.get("global_record") or {}
    scores = (record or {}).get("scores") or global_record.get("scores_mean") or {}
    infractions = (record or {}).get("infractions") or global_record.get("infractions") or {}
    meta = (record or {}).get("meta") or global_record.get("meta") or {}
    counts = {}
    for key, value_item in infractions.items():
        if isinstance(value_item, list):
            counts[key] = len(value_item)
        else:
            counts[key] = int(float(value_item) > 0.0) if value_item is not None else 0
    return {
        "entry_status": value.get("entry_status"),
        "record_present": record is not None,
        "route_record_id": (record or {}).get("route_id"),
        "official_status": (record or {}).get("status") or global_record.get("status"),
        "driving_score": scores.get("score_composed"),
        "route_completion": scores.get("score_route"),
        "infraction_penalty": scores.get("score_penalty"),
        "infractions": counts,
        "success": bool(record and record.get("status") == "Completed"),
        "runtime_simulation_s": meta.get("duration_game"),
        "runtime_wall_s": meta.get("duration_system"),
    }


def finalize_run(args: argparse.Namespace) -> Dict[str, Any]:
    output = args.output.resolve()
    process = {
        "schema": "driveclarify.rq3-v3-p0.process-receipt.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "wrapper_exit": args.wrapper_exit,
        "evaluator_exit": args.evaluator_exit,
        "ports_released": bool(args.ports_released),
        "process_residue": bool(args.process_residue),
        "cleanup_pass": bool(args.ports_released) and not bool(args.process_residue),
        "world_observed_before_evaluator": bool(args.world_observed),
        "server_start_attempts": args.server_start_attempts,
        "preworld_startup_failures": args.preworld_failures,
        "startup_retry_boundary": "PRE_WORLD_PRE_EVALUATOR_ONLY",
        "started_epoch": args.started_epoch,
        "world_ready_epoch": args.world_ready_epoch or None,
        "evaluator_started_epoch": args.evaluator_started_epoch or None,
        "evaluator_finished_epoch": args.evaluator_finished_epoch or None,
        "finished_epoch": int(time.time()),
        "scientific_retry": False,
        "seed_replacement": False,
        "future_scientific_denominator_eligible": False,
        "runtime_mode": args.runtime_mode,
    }
    process["receipt_digest"] = digest(process)
    write_json(output / "process_job/PROCESS_RECEIPT.json", process)
    trace_path = output / "owner_evidence/NATIVE_TERMINAL_TRACE.jsonl"
    rows: List[Dict[str, Any]] = []
    trace_error = None
    if trace_path.is_file():
        try:
            rows = list(read_jsonl(trace_path))
        except Exception as error:
            trace_error = repr(error)
    required = {
        "input_identity",
        "simlingo_output",
        "native_pid",
        "controls",
        "world_native_execution",
        "clocks",
        "heartbeats",
        "liveness",
    }
    complete = [row for row in rows if required <= set(row)]
    latest = complete[-1] if complete else {}
    processed = (
        ((latest.get("input_identity") or {}).get("processed_model_input")) or {}
    )
    pid = latest.get("native_pid") or {}
    official = official_result(output / "official_checkpoint.json")
    reason = (
        "OFFICIAL_EVALUATOR_" + str(official["official_status"]).upper()
        if official["record_present"]
        else ((latest.get("liveness") or {}).get("classification") or "UNKNOWN")
    )
    checks = {
        "trace_exists": trace_path.is_file(),
        "trace_digest_valid": trace_error is None and bool(rows),
        "complete_trace_row_present": bool(complete),
        "raw_image_hash_present": any(
            any(
                sensor.get("is_image") and bool(sensor.get("sha256"))
                for sensor in ((row.get("input_identity") or {}).get("sensors") or {}).values()
            )
            for row in complete
        ),
        "processed_image_hash_present": bool((processed.get("camera_images") or {}).get("sha256")),
        "model_output_values_present": bool(
            ((latest.get("simlingo_output") or {}).get("pred_route") or {}).get("values")
        ),
        "pid_speed_indices_present": pid.get("speed_waypoint_indices_consumed") is not None,
        "pid_target_present": pid.get("target_point_consumed") is not None,
        "clock_pair_present": bool(latest.get("clocks")),
        "world_and_evaluator_present": bool(latest.get("world_native_execution")),
        "process_receipt_present": True,
    }
    summary = {
        "schema": "driveclarify.rq3-v3-p0.run-terminal-summary.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "run_id": (rows[-1].get("run_id") if rows else output.name),
        "evaluator_exit": args.evaluator_exit,
        "reason_code": reason,
        "trace_path": str(trace_path),
        "trace_rows": len(rows),
        "complete_trace_rows": len(complete),
        "trace_sha256": sha_file(trace_path) if trace_path.is_file() else None,
        "trace_error": trace_error,
        "heartbeats": latest.get("heartbeats"),
        "liveness": latest.get("liveness"),
        "official": official,
        "process": process,
        "artifact_checks": checks,
        "artifact_complete": all(checks.values()),
        "observer_issued_control_or_recovery": False,
    }
    summary["receipt_digest"] = digest(summary)
    write_json(output / "RUN_TERMINAL_SUMMARY.json", summary)
    return summary


def source_comparison(frozen: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    current = source_rows()
    before = {row["path"]: row for row in frozen}
    after = {row["path"]: row for row in current}
    changes = [
        {
            "path": path,
            "frozen_sha256": before.get(path, {}).get("sha256"),
            "current_sha256": after.get(path, {}).get("sha256"),
        }
        for path in sorted(set(before) | set(after))
        if before.get(path, {}).get("sha256") != after.get(path, {}).get("sha256")
    ]
    return current, changes


def evaluate_run(planned: Mapping[str, Any]) -> Dict[str, Any]:
    output = ROOT / planned["output_path"]
    summary = load(output / "RUN_TERMINAL_SUMMARY.json", {})
    official = summary.get("official") or official_result(output / "official_checkpoint.json")
    navigation = load(output / "owner_evidence/NATIVE_NAVIGATION_INPUT_CONTRACT.json", {})
    runtime = load(output / "owner_evidence/ENGINEERING_RUNTIME_MODE.json", {})
    agent = load(output / "owner_evidence/V11_AGENT_STATUS.json", {})
    trace_path = output / "owner_evidence/NATIVE_TERMINAL_TRACE.jsonl"
    trace_rows: List[Dict[str, Any]] = []
    trace_error = None
    if trace_path.is_file():
        try:
            trace_rows = list(read_jsonl(trace_path))
        except Exception as error:
            trace_error = repr(error)
    complete_rows = [row for row in trace_rows if row.get("simlingo_output")]
    instructions = {
        ((row.get("input_identity") or {}).get("language_hlc_input") or {}).get("instruction")
        for row in complete_rows
    }
    stop_classes = sorted(
        {
            (row.get("liveness") or {}).get("classification")
            for row in trace_rows
            if (row.get("liveness") or {}).get("classification") in PERSISTENT_STOP_CLASSES
        }
    )
    binding_checks = {
        "config_hash_matches_roster": sha_file(ROOT / planned["config_path"]) == planned["config_sha256"],
        "route_hash_matches_roster": sha_file(ROOT / planned["route_path"]) == planned["route_sha256"],
        "official_route_record_matches": official.get("route_record_id")
        == "RouteScenario_%s_rep0" % planned["route_id"],
        "native_navigation_contract_present": bool(navigation),
        "agent_mode_native_simlingo": navigation.get("mode") == "NATIVE_SIMLINGO",
        "official_plan_bound_once": navigation.get("native_set_global_plan_called_once") is True,
        "no_route_reconstruction": navigation.get("clear_path_route_reconstruction_count") == 0,
        "no_target_override": navigation.get("clear_path_target_point_override_count") == 0,
        "no_road_option_override": navigation.get("clear_path_road_option_override_count") == 0,
        "controller_frozen": navigation.get("controller") == "agent_simlingo.LingoAgent.control_pid",
        "checkpoint_frozen": navigation.get("checkpoint_sha256") == CHECKPOINT_SHA256,
        "runtime_mode_native_default": runtime.get("mode") == "NATIVE_DEFAULT",
        "runtime_flags_unchanged": runtime.get("changed_runtime_flags") == [],
        "agent_no_route_transaction": agent.get("route_transaction_count") == 0,
        "driveclarify_decision_not_exercised": agent.get("mode") == "NATIVE_SIMLINGO",
        "canonical_instruction_observed": instructions == {CANONICAL_NATIVE_INSTRUCTION},
    }
    evidence_checks = {
        "authoritative_evaluator_record_present": official.get("record_present") is True,
        "artifact_complete": summary.get("artifact_complete") is True,
        "trace_parse_valid": trace_error is None and bool(trace_rows),
        "complete_native_control_rows_present": bool(complete_rows),
        "process_cleanup_pass": (summary.get("process") or {}).get("cleanup_pass") is True,
        "world_observed_before_evaluator": (summary.get("process") or {}).get("world_observed_before_evaluator") is True,
    }
    observer_checks = {
        "no_observer_control_or_recovery": summary.get("observer_issued_control_or_recovery") is False,
        "no_second_writer": all(
            (row.get("observer") or {}).get("added_control_writers") == 0
            and (row.get("observer") or {}).get("control_mutations") == 0
            and (row.get("observer") or {}).get("scientific_state_mutations") == 0
            for row in complete_rows
        ),
    }
    valid = all(binding_checks.values()) and all(evidence_checks.values()) and all(observer_checks.values())
    completion = official.get("route_completion")
    reached_goal = bool(official.get("success")) or (
        completion is not None and float(completion) >= 95.0
    )
    native_pass = valid and reached_goal and not stop_classes
    invalid_reasons = [
        group + ":" + key
        for group, checks in (
            ("binding", binding_checks),
            ("evidence", evidence_checks),
            ("observer", observer_checks),
        )
        for key, passed in checks.items()
        if not passed
    ]
    result = {
        "run_id": planned["run_id"],
        "scene_id": planned["scene_id"],
        "scene_code": planned["scene_code"],
        "seed_slot": planned["seed_slot"],
        "engineering_seed": planned["engineering_seed"],
        "valid": valid,
        "validity": "VALID" if valid else "P0_TECHNICALLY_INVALID",
        "technical_invalidity_reasons": invalid_reasons,
        "native_qualification_pass": native_pass,
        "native_task_goal_reached": reached_goal,
        "official": official,
        "native_stall_or_noncompletion": bool(stop_classes) or not reached_goal,
        "persistent_stop_classes": stop_classes,
        "route_owner_binding_checks": binding_checks,
        "evidence_checks": evidence_checks,
        "observer_integrity_checks": observer_checks,
        "terminal_reason": summary.get("reason_code"),
        "artifacts": {
            "run_terminal_summary": rel(output / "RUN_TERMINAL_SUMMARY.json"),
            "official_checkpoint": rel(output / "official_checkpoint.json"),
            "native_trace": rel(trace_path),
        },
    }
    result["run_result_digest"] = digest(result)
    write_json(output / "P0_RUN_RESULT.json", result)
    return result


def update_ledger(ledger: Dict[str, Any]) -> None:
    value = {key: item for key, item in ledger.items() if key != "ledger_digest"}
    value["ledger_digest"] = digest(value)
    write_json(REPORT / "P0_EXECUTION_LEDGER.json", value)


def run_campaign() -> Dict[str, Any]:
    roster = load(REPORT / "P0_EXECUTION_ROSTER.json", {})
    ledger = load(REPORT / "P0_EXECUTION_LEDGER.json", {})
    integrity = load(REPORT / "P0_SOURCE_INTEGRITY_RECEIPT.json", {})
    if roster.get("status") != "FROZEN_UNEXECUTED" or ledger.get("status") != "FROZEN_UNEXECUTED":
        raise RuntimeError("P0_FROZEN_UNEXECUTED_ROSTER_REQUIRED")
    if digest({key: value for key, value in roster.items() if key != "roster_digest"}) != roster.get("roster_digest"):
        raise RuntimeError("P0_ROSTER_DIGEST_MISMATCH")
    verify_scene_freeze()
    ledger["status"] = "RUNNING"
    ledger["execution_started_epoch"] = int(time.time())
    update_ledger(ledger)
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n- `python tools/run_rq3_v3_p0_scene_qualification.py run`\n"
            "  - began frozen 24-cell native roster\n"
        )
    for index, planned in enumerate(roster["runs"]):
        current_sources, source_changes = source_comparison(integrity["frozen_source_rows"])
        if source_changes or sha_file(CHECKPOINT) != CHECKPOINT_SHA256:
            ledger["status"] = "STOPPED_SOURCE_OR_INTEGRITY_FAILURE"
            ledger["stop_before_ordinal"] = planned["ordinal"]
            ledger["source_changes"] = source_changes
            update_ledger(ledger)
            print("P0 stopped before %d/24: source or checkpoint integrity" % planned["ordinal"], flush=True)
            return ledger
        command = [
            str(ROOT / "tools/run_rq3_v3_p0_scene_qualification_episode.sh"),
            str(ROOT / planned["config_path"]),
            str(ROOT / planned["route_path"]),
            str(planned["engineering_seed"]),
            str(planned["rpc_port"]),
            str(ROOT / planned["output_path"]),
        ]
        with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
            stream.write(
                "\n  - cell %02d: `%s`\n" % (planned["ordinal"], " ".join(command))
            )
        started = int(time.time())
        completed = subprocess.run(command, cwd=str(ROOT), check=False)
        run_result = evaluate_run(planned)
        entry = ledger["entries"][index]
        entry.update(
            {
                "execution_status": "ATTEMPTED",
                "started_epoch": started,
                "finished_epoch": int(time.time()),
                "wrapper_returncode": completed.returncode,
                "validity": run_result["validity"],
                "native_qualification_pass": run_result["native_qualification_pass"],
                "disposition": (
                    "P0_RUN_QUALIFIED"
                    if run_result["native_qualification_pass"]
                    else (
                        "P0_VALID_NATIVE_QUALIFICATION_FAILURE"
                        if run_result["valid"]
                        else "P0_TECHNICALLY_INVALID"
                    )
                ),
                "run_result_path": rel(
                    ROOT / planned["output_path"] / "P0_RUN_RESULT.json"
                ),
                "run_result_digest": run_result["run_result_digest"],
            }
        )
        ledger["attempted_runs"] = index + 1
        if not run_result["valid"]:
            ledger["status"] = "STOPPED_P0_TECHNICALLY_INVALID"
            ledger["technical_invalidity_ordinal"] = planned["ordinal"]
            ledger["technical_invalidity_reasons"] = run_result[
                "technical_invalidity_reasons"
            ]
            update_ledger(ledger)
            print("P0 stopped at %d/24: technical invalidity" % planned["ordinal"], flush=True)
            return ledger
        update_ledger(ledger)
        print("P0 execution progress: %d/24 valid attempts" % (index + 1), flush=True)
    ledger["status"] = "COMPLETED_24_OF_24"
    ledger["execution_finished_epoch"] = int(time.time())
    update_ledger(ledger)
    return ledger


def seal_source_integrity() -> Dict[str, Any]:
    previous = load(REPORT / "P0_SOURCE_INTEGRITY_RECEIPT.json", {})
    current, changes = source_comparison(previous.get("frozen_source_rows", []))
    scene_freeze = load(REPORT / "P0_SCENE_FREEZE_RECEIPT.json", {})
    before_trees = {
        row["path"]: row for row in scene_freeze.get("protected_tree_entry_rows", [])
    }
    after_rows = [tree_record(path) for path in PROTECTED_TREES]
    after_trees = {row["path"]: row for row in after_rows}
    tree_changes = [
        {
            "path": path,
            "entry_tree_digest": before_trees.get(path, {}).get("tree_digest"),
            "exit_tree_digest": after_trees.get(path, {}).get("tree_digest"),
        }
        for path in sorted(set(before_trees) | set(after_trees))
        if before_trees.get(path, {}).get("tree_digest")
        != after_trees.get(path, {}).get("tree_digest")
    ]
    checkpoint_valid = CHECKPOINT.is_file() and sha_file(CHECKPOINT) == CHECKPOINT_SHA256
    passed = not changes and not tree_changes and checkpoint_valid
    receipt = {
        "schema": "driveclarify.rq3-v3-p0.source-integrity-receipt.v1",
        "stage": STAGE,
        "status": (
            "PASS_P0_SOURCE_CHECKPOINT_AND_PROTECTED_HISTORY_INTEGRITY"
            if passed
            else "FAIL_P0_SOURCE_OR_INTEGRITY"
        ),
        "frozen_source_rows": previous.get("frozen_source_rows", []),
        "frozen_source_digest": previous.get("frozen_source_digest"),
        "current_source_rows": current,
        "current_source_digest": digest(current),
        "source_changes": changes,
        "checkpoint": hash_record(CHECKPOINT) if CHECKPOINT.is_file() else None,
        "checkpoint_valid": checkpoint_valid,
        "protected_tree_entry_rows": scene_freeze.get("protected_tree_entry_rows", []),
        "protected_tree_exit_rows": after_rows,
        "protected_tree_changes": tree_changes,
        "scientific_components_changed": [] if passed else changes,
        "scientific_components_changed_label": "NONE" if passed else "INTEGRITY_FAILURE",
        "formal_seeds_generated": False,
        "formal_execution_started": False,
        "pass": passed,
    }
    receipt["receipt_digest"] = digest(receipt)
    write_json(REPORT / "P0_SOURCE_INTEGRITY_RECEIPT.json", receipt)
    return receipt


def analyze() -> Dict[str, Any]:
    manifest = load(REPORT / "P0_SCENE_MANIFEST.json", {})
    roster = load(REPORT / "P0_EXECUTION_ROSTER.json", {})
    ledger = load(REPORT / "P0_EXECUTION_LEDGER.json", {})
    seed_receipt = load(REPORT / "P0_SEED_FRESHNESS_RECEIPT.json", {})
    usc = load(REPORT / "USC_EQUIVALENT_REPLACEMENT_CERTIFICATION.json", {})
    integrity = seal_source_integrity()
    by_scene: Dict[str, List[Dict[str, Any]]] = {code: [] for code in SCENE_CODES}
    for planned in roster.get("runs", []):
        path = ROOT / planned["output_path"] / "P0_RUN_RESULT.json"
        if path.is_file():
            by_scene[planned["scene_code"]].append(load(path))
    scene_rows = []
    for scene in manifest.get("scenes", []):
        runs = by_scene[scene["scene_code"]]
        valid_count = sum(row["valid"] for row in runs)
        pass_count = sum(row["native_qualification_pass"] for row in runs)
        binding_3_of_3 = len(runs) == 3 and all(
            all(row["route_owner_binding_checks"].values())
            and all(row["evidence_checks"].values())
            and all(row["observer_integrity_checks"].values())
            for row in runs
        )
        repeated_stop = len(runs) == 3 and sum(
            bool(row["persistent_stop_classes"]) for row in runs
        ) >= 2
        qualified = binding_3_of_3 and pass_count >= 2 and not repeated_stop
        collision_counts = []
        for row in runs:
            infractions = row["official"].get("infractions") or {}
            collision_counts.append(
                sum(
                    int(infractions.get(key, 0) or 0)
                    for key in (
                        "collisions_layout",
                        "collisions_pedestrian",
                        "collisions_vehicle",
                    )
                )
            )
        scene_rows.append(
            {
                "scene_id": scene["scene_id"],
                "condition": scene["scene_code"],
                "planned": 3,
                "attempted": len(runs),
                "valid": valid_count,
                "native_task_success_or_completion": pass_count,
                "route_completion_percent": [
                    row["official"].get("route_completion") for row in runs
                ],
                "native_stall_or_noncompletion_count": sum(
                    row["native_stall_or_noncompletion"] for row in runs
                ),
                "collision_counts": collision_counts,
                "relevant_official_infractions": [
                    row["official"].get("infractions") for row in runs
                ],
                "route_owner_binding_valid": binding_3_of_3,
                "repeated_common_persistent_stop_defect": repeated_stop,
                "qualification": (
                    "PASS"
                    if qualified
                    else (
                        "FAIL"
                        if len(runs) == 3 and valid_count == 3
                        else "NOT_REACHED_OR_TECHNICALLY_INVALID"
                    )
                ),
            }
        )
    attempted = sum(len(rows) for rows in by_scene.values())
    any_invalid = any(not row["valid"] for rows in by_scene.values() for row in rows)
    if not integrity["pass"]:
        final_status = "BLOCKED_RQ3_V3_P0_SOURCE_OR_INTEGRITY_FAILURE"
    elif any_invalid or ledger.get("status") == "STOPPED_P0_TECHNICALLY_INVALID" or attempted < 24:
        final_status = "BLOCKED_RQ3_V3_P0_TECHNICALLY_INVALID"
    elif all(row["qualification"] == "PASS" for row in scene_rows):
        final_status = "PASS_RQ3_V3_P0_ALL_SCENES_QUALIFIED"
    else:
        final_status = "FAIL_RQ3_V3_P0_SCENE_NOT_NATIVELY_QUALIFIED"
    results = {
        "schema": "driveclarify.rq3-v3-p0.results.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "final_status": final_status,
        "planned_runs": 24,
        "attempted_runs": attempted,
        "valid_runs": sum(row["valid"] for rows in by_scene.values() for row in rows),
        "execution_ledger_status": ledger.get("status"),
        "scene_results": scene_rows,
        "usc_replacement_task_equivalent_certified": (
            usc.get("task_relation") == "TASK_EQUIVALENT"
            and usc.get("all_rq1_task_signature_fields_equal") is True
            and usc.get("correct_driveclarify_behavior") == "ACT"
        ),
        "p0_engineering_seed_identities": seed_receipt.get(
            "ordered_p0_engineering_seed_identities", []
        ),
        "seed_freshness": seed_receipt.get("status"),
        "scientific_components_changed": [],
        "formal_seeds_generated": False,
        "formal_execution_started": False,
        "p0_results_enter_formal_denominator": False,
        "next_step": "INDEPENDENT_P0_REVIEW_ONLY",
    }
    results["results_digest"] = digest(results)
    write_json(REPORT / "P0_RESULTS.json", results)
    headers = (
        "| Condition | Scene identity | Planned | Valid | Native task pass | "
        "Route Completion (%) | Stall/noncompletion | Collisions | Binding | Qualification |\n"
        "|---|---|---:|---:|---:|---|---:|---|---|---|\n"
    )
    lines = []
    for row in scene_rows:
        completion = ", ".join(
            "null" if value is None else ("%g" % value)
            for value in row["route_completion_percent"]
        )
        lines.append(
            "| %s | `%s` | 3 | %d | %d | %s | %d | %s | %s | **%s** |"
            % (
                row["condition"],
                row["scene_id"],
                row["valid"],
                row["native_task_success_or_completion"],
                completion,
                row["native_stall_or_noncompletion_count"],
                ", ".join(str(value) for value in row["collision_counts"]),
                "PASS" if row["route_owner_binding_valid"] else "FAIL",
                row["qualification"],
            )
        )
    report = (
        "# RQ3-V3 P0 scene qualification — final report\n\n"
        "Final status: `%s`\n\n"
        "This is engineering-only Part-B scene qualification. It is not formal "
        "RQ3-V3 science and no result enters a formal denominator.\n\n"
        "## Eight-scene qualification table\n\n%s%s\n\n"
        "## Required closure facts\n\n"
        "- Planned execution status: `%d/24` attempted; ledger `%s`.\n"
        "- Eight scene identities: %s.\n"
        "- USC replacement: `%s`; unchanged RQ1 comparator `TASK_EQUIVALENT`; "
        "correct scientific behavior `ACT`; certification `%s`.\n"
        "- P0 engineering seed identities: `%s`.\n"
        "- Seed freshness: `%s`.\n"
        "- Scientific components changed: `NONE`.\n"
        "- Formal seeds generated: `NO`.\n"
        "- Formal execution started: `NO`.\n"
        "- Final RQ3-V3 campaign frozen: `NO`.\n"
        "- Next step: independent P0 review and, only if separately authorized, "
        "final formal freeze.\n"
        % (
            final_status,
            headers,
            "\n".join(lines),
            attempted,
            ledger.get("status"),
            ", ".join("`%s`" % row["scene_id"] for row in scene_rows),
            usc.get("scene_id"),
            usc.get("status"),
            ", ".join(str(seed) for seed in results["p0_engineering_seed_identities"]),
            results["seed_freshness"],
        )
    )
    write_text(REPORT / "FINAL_REPORT.md", report)
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n- `python tools/run_rq3_v3_p0_scene_qualification.py analyze`\n"
            "  - sealed source/history integrity and the eight-row result\n"
            "  - final status: `%s`\n"
            "  - formal seeds generated: `NO`; formal execution started: `NO`\n"
            % final_status
        )
    return results


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze-scenes")
    sub.add_parser("freeze-roster")
    sub.add_parser("run")
    sub.add_parser("analyze")
    finalize = sub.add_parser("finalize-run")
    finalize.add_argument("--output", type=Path, required=True)
    finalize.add_argument("--wrapper-exit", type=int, required=True)
    finalize.add_argument("--evaluator-exit", type=int, required=True)
    finalize.add_argument("--ports-released", type=int, required=True)
    finalize.add_argument("--process-residue", type=int, required=True)
    finalize.add_argument("--world-observed", type=int, required=True)
    finalize.add_argument("--server-start-attempts", type=int, required=True)
    finalize.add_argument("--preworld-failures", type=int, required=True)
    finalize.add_argument("--started-epoch", type=int, required=True)
    finalize.add_argument("--world-ready-epoch", type=int, required=True)
    finalize.add_argument("--evaluator-started-epoch", type=int, required=True)
    finalize.add_argument("--evaluator-finished-epoch", type=int, required=True)
    finalize.add_argument("--runtime-mode", required=True)
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "freeze-scenes":
        result = freeze_scenes()
    elif args.command == "freeze-roster":
        result = freeze_roster()
    elif args.command == "run":
        result = run_campaign()
    elif args.command == "analyze":
        result = analyze()
    elif args.command == "finalize-run":
        result = finalize_run(args)
    else:
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
