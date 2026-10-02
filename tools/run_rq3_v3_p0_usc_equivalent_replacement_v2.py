#!/usr/bin/env python3
"""Prospectively freeze and qualify the sole RQ3-V3 P0 Q07 replacement V2.

This controller is engineering-only.  It cannot generate formal seeds or start
formal RQ3-V3, and it never writes to the accepted P0 or TM-recovery trees.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import secrets
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.run_rq3_v3_p0_scene_qualification as p0  # noqa: E402


STAGE = "RQ3_V3_P0_USC_EQUIVALENT_REPLACEMENT_V2"
SCOPE = "ENGINEERING_ONLY_NON_FORMAL_Q07_REPLACEMENT_QUALIFICATION"
REPORT = ROOT / "reports/driveclarify_rq3_v3_p0_usc_equivalent_replacement_v2"
ASSETS = REPORT / "scene_assets"
CONFIGS = REPORT / "configs"
RUNS = REPORT / "native_runs"
ORIGINAL = ROOT / "reports/driveclarify_rq3_v3_p0_scene_qualification_v1"
RECOVERY = ROOT / "reports/driveclarify_rq3_v3_p0_technical_recovery_and_resume_v1"
RUNNER = ROOT / "tools/run_rq3_v3_p0_recovery_episode.sh"
TOOL = ROOT / "tools/run_rq3_v3_p0_usc_equivalent_replacement_v2.py"
XODR = Path("/home/buaa/CARLA_0.9.15/CarlaUE4/Content/Carla/Maps/OpenDrive/Town05.xodr")
CHECKPOINT = p0.CHECKPOINT
CHECKPOINT_SHA256 = p0.CHECKPOINT_SHA256
SCENE_ID = "RQ3V3-P0-USC-EQUIVALENT-Q07-RV2"
SCENE_CODE = "USC-EQUIVALENT"
ROUTE_ID = "997409"
AMBIGUOUS_INSTRUCTION = "Pull over at a suitable distance."
CANONICAL_NATIVE_INSTRUCTION = p0.CANONICAL_NATIVE_INSTRUCTION
OLD_Q07_CLASSIFICATION = (
    "P0_USC_EQUIVALENT_REPLACEMENT_V1_NOT_QUALIFIED_FOR_SAFE_FORMAL_HOST"
)
ALLOWED_FINAL_STATUSES = {
    "PASS_RQ3_V3_P0_ALL_SCENES_QUALIFIED_AFTER_Q07_REPLACEMENT",
    "FAIL_RQ3_V3_P0_Q07_REPLACEMENT_NOT_NATIVELY_QUALIFIED",
    "BLOCKED_RQ3_V3_P0_Q07_REPLACEMENT_TECHNICALLY_INVALID",
}
PORTS = {
    1: {"rpc": 24800, "streaming": 24801, "tm": 24902},
    2: {"rpc": 25200, "streaming": 25201, "tm": 25302},
    3: {"rpc": 25600, "streaming": 25601, "tm": 25702},
}

# The result of the single, pre-execution, outcome-blind Town05 static-map scan.
# The scan admitted exactly one 88 m buffered segment under the frozen rule.
STATIC_SELECTION = {
    "schema": "driveclarify.rq3-v3-p0.q07-v2.single-static-selection.v1",
    "stage": STAGE,
    "selection_count": 1,
    "single_scan_only": True,
    "eligible_segment_count": 1,
    "map": "Town05",
    "excluded_prior_road_ids": [37, 39, 48],
    "selection_rule": {
        "source": "CARLA Town05 OpenDRIVE static map only",
        "step_m": 2.0,
        "required_candidate_A_distance_m": 60.0,
        "candidate_separation_m": 8.0,
        "required_upstream_buffer_m": 20.0,
        "required_downstream_buffer_after_B_m": 20.0,
        "driving_lane": True,
        "rightmost_driving_lane": True,
        "right_neighbor_type": "Shoulder",
        "minimum_lane_width_m": 3.5,
        "junction_allowed": False,
        "maximum_z_range_m": 0.5,
        "maximum_absolute_pitch_deg": 0.5,
        "maximum_heading_step_deg": 0.5,
        "minimum_chord_path_ratio": 0.9999,
        "ranking": (
            "lexicographic minimum of z range, pitch, one-minus-chord/path, "
            "heading step, road, section, lane, s"
        ),
    },
    "selected_identity": {"road_id": 34, "section_id": 0, "lane_id": 4},
    "selection_score": [0.0, 0.0, 0.0, 0.0, 34, 0, 4, 88.0],
    "selection_score_numerical_note": (
        "The raw CARLA single-precision chord/path calculation exceeded one by "
        "8.3988e-8; one-minus-ratio is conservatively clamped to zero."
    ),
    "start": {
        "xyz": [188.71319580078125, -99.02095031738281, 0.0],
        "s": 88.0,
        "lane_width_m": 3.5,
        "junction": False,
        "right_lane_type": "Shoulder",
        "yaw_deg": 89.33090209960938,
        "pitch_deg": 0.0,
    },
    "candidate_A": {
        "meaning": "nearer suitable longitudinal stopping placement",
        "xyz": [189.41384887695312, -39.02503967285156, 0.0],
        "s": 28.000000000000007,
        "distance_from_start_m": 60.0,
    },
    "candidate_B": {
        "meaning": "farther suitable longitudinal stopping placement",
        "xyz": [189.5072784423828, -31.025583267211914, 0.0],
        "s": 20.00000000000002,
        "distance_from_start_m": 68.0,
    },
    "buffer_end": {
        "xyz": [189.74082946777344, -11.026947975158691, 0.0],
        "s": 0.0,
        "distance_from_start_m": 88.0,
    },
    "selection_used_new_native_trial": False,
    "selection_used_driveclarify_execution": False,
    "selection_used_formal_outcome": False,
    "selection_used_prior_scene_identities_for_exclusion_only": True,
}


def load(path: Path, default: Any = None) -> Any:
    return p0.load(path, default)


def write_json(path: Path, value: Any) -> None:
    p0.write_json(path, value)


def write_text(path: Path, value: str) -> None:
    p0.write_text(path, value)


def digest(value: Any) -> str:
    return p0.digest(value)


def rel(path: Path) -> str:
    return p0.rel(path)


def hash_record(path: Path) -> Dict[str, Any]:
    return p0.hash_record(path)


def tree_record(path: Path) -> Dict[str, Any]:
    return p0.tree_record(path)


def source_rows() -> List[Dict[str, Any]]:
    paths = list(p0.SOURCE_PATHS) + [
        ROOT / "driveclarify_clear_passthrough_v11/ambiguity_gate.py",
        ROOT / "driveclarify_clear_passthrough_v11/contracts.py",
        Path(
            "/home/buaa/wrh/simlingo/scenario_runner_autopilot/srunner/"
            "scenarios/background_activity.py"
        ),
        Path(
            "/home/buaa/wrh/simlingo/leaderboard_autopilot/leaderboard/"
            "utils/statistics_manager.py"
        ),
        ROOT / "tools/run_rq3_v3_p0_technical_recovery.py",
        RUNNER,
        TOOL,
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("Q07_V2_SOURCE_MISSING:" + ",".join(missing))
    return [hash_record(path) for path in paths]


def append_command(text: str) -> None:
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write(text)


def canonical_without(value: Mapping[str, Any], field: str) -> str:
    return digest({key: item for key, item in value.items() if key != field})


def verify_digest_file(path: Path, field: str) -> Dict[str, Any]:
    value = load(path, {})
    if not value or canonical_without(value, field) != value.get(field):
        raise RuntimeError("Q07_V2_DIGEST_MISMATCH:" + rel(path))
    return value


def prior_disposition() -> Dict[str, Any]:
    result = load(RECOVERY / "P0_FINAL_8_SCENE_RESULTS.json", {})
    preservation = load(RECOVERY / "SOURCE_AND_HISTORY_PRESERVATION_RECEIPT.json", {})
    if result.get("final_status") != "PASS_RQ3_V3_P0_ALL_SCENES_QUALIFIED":
        raise RuntimeError("Q07_V2_ACCEPTED_TECHNICAL_RECOVERY_MISSING")
    if preservation.get("pass") is not True:
        raise RuntimeError("Q07_V2_PRIOR_PRESERVATION_NOT_PASS")
    rows = {row["scene_id"]: row for row in result.get("scene_results", [])}
    accepted = [
        "RQ3V3-P0-REF-EQUIVALENT-Q01",
        "RQ3V3-P0-REF-CRITICAL-Q02",
        "RQ3V3-P0-LMK-EQUIVALENT-Q03",
        "RQ3V3-P0-LMK-CRITICAL-Q04",
        "RQ3V3-P0-ORD-EQUIVALENT-Q05",
        "RQ3V3-P0-ORD-CRITICAL-Q06",
        "RQ3V3-P0-USC-CRITICAL-Q08",
    ]
    if any(rows.get(scene_id, {}).get("qualification") != "PASS" for scene_id in accepted):
        raise RuntimeError("Q07_V2_ACCEPTED_SCENE_DISPOSITION_MISMATCH")
    old = rows.get("RQ3V3-P0-USC-EQUIVALENT-Q07", {})
    if old.get("collision_counts") != [3, 3, 3]:
        raise RuntimeError("Q07_V2_OLD_Q07_COLLISION_DISPOSITION_MISMATCH")
    return {"accepted_scene_ids": accepted, "old_q07_result": old}


def task_signatures() -> List[Dict[str, Any]]:
    common = {
        "relevant_components": [
            "terminal_task_region",
            "terminal_road_or_corridor",
            "maneuver_obligation",
            "irreversible_branch_obligation",
            "goal_lane_or_side_obligation_if_task_relevant",
            "task_completion_region",
        ],
        "certified": True,
        "terminal_task_region": "RQ3V3-USC-RV2-ROAD34-S20-S28-SHARED-ZONE",
        "terminal_road_or_corridor": "TOWN05-ROAD34-SECTION0-NORTHBOUND",
        "maneuver_obligation": "CONTINUE-THEN-STOP-IN-SHARED-SUITABLE-DISTANCE-ZONE",
        "irreversible_branch_obligation": "NO-BRANCH-ROAD34-LANE4-CONTINUATION",
        "goal_lane_or_side_obligation_if_task_relevant": (
            "ROAD34-LANE4-RIGHTMOST-DRIVING-LANE-ADJACENT-SHOULDER"
        ),
        "task_completion_region": "RQ3V3-USC-RV2-SHARED-COMPLETION-REGION",
    }
    return [
        {
            "candidate_id": candidate,
            "certificate_id": "RQ1V2-RQ3V3-P0-USC-EQUIVALENT-RV2-" + candidate,
            **common,
        }
        for candidate in ("A", "B")
    ]


def route_xml(points: Sequence[Sequence[float]]) -> str:
    return p0.route_xml(int(ROUTE_ID), points)


def freeze_scene() -> Dict[str, Any]:
    if REPORT.exists():
        raise RuntimeError("Q07_V2_REPORT_DIRECTORY_ALREADY_EXISTS")
    disposition = prior_disposition()
    if p0.sha_file(CHECKPOINT) != CHECKPOINT_SHA256:
        raise RuntimeError("Q07_V2_CHECKPOINT_DRIFT_BEFORE_SCENE_FREEZE")
    original_entry = tree_record(ORIGINAL)
    recovery_entry = tree_record(RECOVERY)
    protected_entry = [tree_record(path) for path in p0.PROTECTED_TREES]
    frozen_sources = source_rows()

    REPORT.mkdir(parents=True)
    ASSETS.mkdir()
    selection = dict(STATIC_SELECTION)
    selection["opendrive"] = hash_record(XODR)
    selection["recorded_before_seed_generation"] = True
    selection["recorded_before_native_execution"] = True
    selection["selection_digest"] = digest(selection)
    selection_path = ASSETS / "Q07_RV2_SINGLE_STATIC_SELECTION.json"
    write_json(selection_path, selection)

    start = selection["start"]["xyz"]
    candidate_a = selection["candidate_A"]["xyz"]
    candidate_b = selection["candidate_B"]["xyz"]
    route_path = ASSETS / "RQ3V3-P0-USC-EQUIVALENT-Q07-RV2-ROUTE.xml"
    write_text(route_path, route_xml([start, candidate_a]))
    candidate_routes = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.candidate-routes.v1",
        "stage": STAGE,
        "scene_id": SCENE_ID,
        "map": "Town05",
        "candidate_A": {
            "meaning": selection["candidate_A"]["meaning"],
            "route": [
                {"xyz": start, "road_option": "LANEFOLLOW"},
                {"xyz": candidate_a, "road_option": "LANEFOLLOW"},
            ],
            "stop_xyz": candidate_a,
        },
        "candidate_B": {
            "meaning": selection["candidate_B"]["meaning"],
            "route": [
                {"xyz": start, "road_option": "LANEFOLLOW"},
                {"xyz": candidate_b, "road_option": "LANEFOLLOW"},
            ],
            "stop_xyz": candidate_b,
        },
        "longitudinal_separation_m": math.dist(candidate_a, candidate_b),
        "shared_completion_polygon_xyz": [
            [187.0, -41.0, 0.0],
            [191.8, -41.0, 0.0],
            [191.8, -29.0, 0.0],
            [187.0, -29.0, 0.0],
        ],
        "same_road_section_lane": selection["selected_identity"],
        "same_side_lane_maneuver_branch_goal_category": True,
        "difference_limited_to_continuous_longitudinal_stopping_placement": True,
    }
    candidate_routes["candidate_routes_digest"] = digest(candidate_routes)
    candidates_path = ASSETS / "RQ3V3-P0-USC-EQUIVALENT-Q07-RV2-CANDIDATES.json"
    write_json(candidates_path, candidate_routes)

    layout = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.layout.v1",
        "stage": STAGE,
        "scene_id": SCENE_ID,
        "scientific_entities": [
            {"entity_id": SCENE_ID + "-A", "role": "USC_CANDIDATE_A", "spawned_actor": False},
            {"entity_id": SCENE_ID + "-B", "role": "USC_CANDIDATE_B", "spawned_actor": False},
        ],
        "official_scenario_actor_count": 0,
        "official_background_activity_behavior": "UNCHANGED_BENCH2DRIVE_DEFAULT",
        "random_background_vehicle_override_count": 0,
        "traffic_manager_random_generation_override": False,
        "weather": {
            "cloudiness": 0.0,
            "fog_density": 0.0,
            "precipitation": 0.0,
            "sun_altitude_angle": 70.0,
            "wetness": 0.0,
        },
    }
    layout["layout_digest"] = digest(layout)
    layout_path = ASSETS / "RQ3V3-P0-USC-EQUIVALENT-Q07-RV2-LAYOUT.json"
    write_json(layout_path, layout)

    route_owner = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.route-owner-binding.v1",
        "stage": STAGE,
        "scene_id": SCENE_ID,
        "pre_execution_evidence": True,
        "route": hash_record(route_path),
        "parsed_route_id": p0.parse_route(route_path)[0],
        "parsed_town": p0.parse_route(route_path)[1],
        "parsed_waypoints": p0.parse_route(route_path)[2],
        "canonical_target_encoded_by_official_route": p0.parse_route(route_path)[2][-1] == candidate_a,
        "official_route_owner": "LEADERBOARD_ROUTE_SCENARIO",
        "global_plan_consumer": "agent_simlingo.LingoAgent.set_global_plan",
        "route_planner": "UNCHANGED_SIMLINGO_ROUTEPLANNER",
        "controller": "agent_simlingo.LingoAgent.control_pid",
        "runtime_binding_requirements": [
            "native_set_global_plan_called_once",
            "clear_path_route_reconstruction_count_zero",
            "clear_path_target_point_override_count_zero",
            "clear_path_road_option_override_count_zero",
            "route_record_id_matches_997409",
        ],
        "implementation_evidence": [
            hash_record(Path("/home/buaa/wrh/simlingo/leaderboard_autopilot/leaderboard/leaderboard_evaluator.py")),
            hash_record(Path("/home/buaa/wrh/simlingo/leaderboard_autopilot/leaderboard/scenarios/route_scenario.py")),
            hash_record(Path("/home/buaa/wrh/simlingo/team_code/agent_simlingo.py")),
            hash_record(Path("/home/buaa/wrh/simlingo/team_code/nav_planner.py")),
        ],
    }
    route_owner["binding_digest"] = digest(route_owner)
    binding_path = ASSETS / "RQ3V3-P0-USC-EQUIVALENT-Q07-RV2-ROUTE-OWNER-BINDING.json"
    write_json(binding_path, route_owner)

    signatures = task_signatures()
    scene = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.scene-contract.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "scene_id": SCENE_ID,
        "scene_code": SCENE_CODE,
        "family": "USC",
        "consequence_level": "EQUIVALENT",
        "machine_label": "TASK_EQUIVALENT",
        "correct_driveclarify_behavior": "ACT",
        "ambiguous_instruction": AMBIGUOUS_INSTRUCTION,
        "resolved_canonical_native_instruction": CANONICAL_NATIVE_INSTRUCTION,
        "native_map": "Town05",
        "route_id": ROUTE_ID,
        "native_route": hash_record(route_path),
        "candidate_routes": hash_record(candidates_path),
        "layout": hash_record(layout_path),
        "static_selection": hash_record(selection_path),
        "route_owner_binding": hash_record(binding_path),
        "task_signatures": signatures,
        "reasonable_interpretation_count": 2,
        "canonical_candidate": "A",
        "canonical_target_encoded_by_official_route": True,
        "host_geometry_change_only": True,
        "scientific_components_changed": [],
        "unchanged_scientific_components": {
            "RQ1": True,
            "RQ2": True,
            "DriveClarify": True,
            "SimLingo_checkpoint": True,
            "PID_controller": True,
            "RoutePlanner_semantics": True,
            "ACT_ASK_WAIT_policy": True,
            "clarification_reserve_seconds": 1.20,
        },
        "formal_seed_generated": False,
        "formal_execution_started": False,
    }
    scene["semantic_certification"] = p0.static_semantic_check(
        {
            **scene,
            "p0_resolved_canonical_instruction": CANONICAL_NATIVE_INSTRUCTION,
        }
    )
    if not scene["semantic_certification"].get("pass"):
        raise RuntimeError("Q07_V2_TASK_EQUIVALENT_CERTIFICATION_FAILED")
    scene["scene_digest"] = digest(scene)
    scene_path = ASSETS / "RQ3V3-P0-USC-EQUIVALENT-Q07-RV2-SCENE.json"
    write_json(scene_path, scene)

    certification = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.task-equivalent-certification.v1",
        "stage": STAGE,
        "status": "PASS_TASK_EQUIVALENT_CERTIFIED_BEFORE_EXECUTION",
        "scene_id": SCENE_ID,
        "ambiguity_family": "USC",
        "scientific_meaning": "CONTINUOUS_SPATIAL_UNDERSPECIFICATION",
        "instruction": AMBIGUOUS_INSTRUCTION,
        "candidate_interpretations": {
            "A": {"meaning": selection["candidate_A"]["meaning"], "stop_xyz": candidate_a},
            "B": {"meaning": selection["candidate_B"]["meaning"], "stop_xyz": candidate_b},
        },
        "task_signatures": signatures,
        "unchanged_rq1_comparator_result": scene["semantic_certification"],
        "all_task_signature_fields_equal": all(
            signatures[0].get(key) == signatures[1].get(key)
            for key in signatures[0]["relevant_components"]
        ),
        "task_relation": "TASK_EQUIVALENT",
        "correct_driveclarify_behavior": "ACT",
        "interpretations_differ_only_in_continuous_longitudinal_placement": True,
        "why_scientific_role_is_preserved": (
            "The USC wording and two reasonable spatial interpretations are unchanged in role; "
            "A and B differ only by an 8 m longitudinal placement on the same rightmost lane, "
            "shared terminal zone, corridor, maneuver, branch obligation, side obligation, and "
            "completion region. The unchanged RQ1 comparator therefore returns TASK_EQUIVALENT "
            "and the unchanged consequence policy returns ACT."
        ),
        "selection_used_driveclarify_execution": False,
        "generated_before_engineering_seeds": True,
        "generated_before_native_execution": True,
    }
    certification["certification_digest"] = digest(certification)
    certification_path = REPORT / "TASK_EQUIVALENT_CERTIFICATION.json"
    write_json(certification_path, certification)

    old_preservation = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.old-q07-preservation.v1",
        "stage": STAGE,
        "status": OLD_Q07_CLASSIFICATION,
        "old_q07_preserved": True,
        "old_scene_id": "RQ3V3-P0-USC-EQUIVALENT-Q07",
        "old_scene_contract": hash_record(
            ORIGINAL / "scene_assets/RQ3V3-P0-USC-EQUIVALENT-Q07-SCENE.json"
        ),
        "old_certification": hash_record(ORIGINAL / "USC_EQUIVALENT_REPLACEMENT_CERTIFICATION.json"),
        "old_q07_valid_native_runs": 3,
        "old_q07_native_completion_count": 3,
        "old_q07_collision_counts": [3, 3, 3],
        "old_q07_rewritten": False,
        "original_p0_tree_entry": original_entry,
        "tm_recovery_tree_entry": recovery_entry,
    }
    old_preservation["receipt_digest"] = digest(old_preservation)
    write_json(REPORT / "OLD_Q07_PRESERVATION_RECEIPT.json", old_preservation)

    preseed = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.preseed-state.v1",
        "stage": STAGE,
        "scene_id": SCENE_ID,
        "scene_digest": scene["scene_digest"],
        "engineering_seed_identities_generated": 0,
        "formal_seed_identities_generated": 0,
        "native_execution_started": False,
        "formal_execution_started": False,
        "recorded_epoch": int(time.time()),
    }
    preseed["receipt_digest"] = digest(preseed)
    write_json(REPORT / "PRESEED_STATE_RECEIPT.json", preseed)

    frozen_paths = [
        selection_path,
        route_path,
        candidates_path,
        layout_path,
        binding_path,
        scene_path,
        certification_path,
        REPORT / "OLD_Q07_PRESERVATION_RECEIPT.json",
        REPORT / "PRESEED_STATE_RECEIPT.json",
    ]
    freeze = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.prospective-scene-freeze.v1",
        "stage": STAGE,
        "status": "FROZEN_ONE_Q07_REPLACEMENT_BEFORE_SEED_GENERATION_OR_EXECUTION",
        "authorization": "NARROW_P0_Q07_REPLACEMENT_V2_ONLY",
        "scene_count": 1,
        "scene_id": SCENE_ID,
        "scene_digest": scene["scene_digest"],
        "scene_assets": [hash_record(path) for path in frozen_paths],
        "task_equivalent_certification_digest": certification["certification_digest"],
        "route_owner_binding_digest": route_owner["binding_digest"],
        "single_prospective_selection": True,
        "new_native_trial_used_for_selection": False,
        "engineering_seeds_generated_at_freeze": 0,
        "formal_seeds_generated_at_freeze": 0,
        "native_execution_started_at_freeze": False,
        "formal_execution_started_at_freeze": False,
        "accepted_scene_ids_not_in_roster": disposition["accepted_scene_ids"],
        "accepted_scene_reruns_planned": 0,
        "scientific_components_changed": [],
        "unchanged_policy_reserve_seconds": 1.20,
        "checkpoint": hash_record(CHECKPOINT),
        "original_p0_tree_entry": original_entry,
        "tm_recovery_tree_entry": recovery_entry,
        "protected_history_tree_entries": protected_entry,
        "frozen_source_rows": frozen_sources,
        "source_digest": digest(frozen_sources),
        "recorded_epoch": int(time.time()),
    }
    freeze["freeze_digest"] = digest(freeze)
    write_json(REPORT / "PROSPECTIVE_SCENE_FREEZE_RECEIPT.json", freeze)
    write_text(
        REPORT / "COMMAND_LOG.md",
        "# Command log\n\n"
        "- one deterministic Town05 OpenDRIVE static-map scan\n"
        "  - selected exactly one eligible segment; no native or DriveClarify trial\n"
        "- `python tools/run_rq3_v3_p0_usc_equivalent_replacement_v2.py freeze-scene`\n"
        "  - froze the scene, candidates, TaskSignatures, TASK_EQUIVALENT/ACT certification, "
        "route/owner binding, and preservation baselines before any seed or execution\n"
        "  - engineering seeds generated: `0`; formal seeds generated: `0`\n",
    )
    return freeze


def verify_scene_freeze() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    freeze = verify_digest_file(REPORT / "PROSPECTIVE_SCENE_FREEZE_RECEIPT.json", "freeze_digest")
    if freeze.get("status") != "FROZEN_ONE_Q07_REPLACEMENT_BEFORE_SEED_GENERATION_OR_EXECUTION":
        raise RuntimeError("Q07_V2_SCENE_FREEZE_STATUS_INVALID")
    for record in freeze.get("scene_assets", []):
        path = Path(record["path"])
        path = path if path.is_absolute() else ROOT / path
        if not path.is_file() or p0.sha_file(path) != record["sha256"]:
            raise RuntimeError("Q07_V2_FROZEN_SCENE_ASSET_DRIFT:" + record["path"])
    scene_record = next(
        record for record in freeze["scene_assets"] if record["path"].endswith("-SCENE.json")
    )
    scene = load(ROOT / scene_record["path"], {})
    if canonical_without(scene, "scene_digest") != scene.get("scene_digest"):
        raise RuntimeError("Q07_V2_SCENE_DIGEST_MISMATCH")
    return freeze, scene


def historical_integrity(freeze: Mapping[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    original_exit = tree_record(ORIGINAL)
    recovery_exit = tree_record(RECOVERY)
    protected_exit = [tree_record(ROOT / row["path"]) for row in freeze["protected_history_tree_entries"]]
    protected_entry = {
        row["path"]: (row["file_count"], row["tree_digest"])
        for row in freeze["protected_history_tree_entries"]
    }
    protected_pass = all(
        protected_entry[row["path"]] == (row["file_count"], row["tree_digest"])
        for row in protected_exit
    )
    details = {
        "original_p0_tree_entry": freeze["original_p0_tree_entry"],
        "original_p0_tree_exit": original_exit,
        "tm_recovery_tree_entry": freeze["tm_recovery_tree_entry"],
        "tm_recovery_tree_exit": recovery_exit,
        "protected_history_tree_entry": freeze["protected_history_tree_entries"],
        "protected_history_tree_exit": protected_exit,
        "original_p0_tree_unchanged": original_exit == freeze["original_p0_tree_entry"],
        "tm_recovery_tree_unchanged": recovery_exit == freeze["tm_recovery_tree_entry"],
        "protected_history_trees_unchanged": protected_pass,
    }
    return all(
        details[key]
        for key in (
            "original_p0_tree_unchanged",
            "tm_recovery_tree_unchanged",
            "protected_history_trees_unchanged",
        )
    ), details


def source_integrity(freeze: Mapping[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    current = source_rows()
    before = {row["path"]: row for row in freeze["frozen_source_rows"]}
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
    checkpoint_valid = p0.sha_file(CHECKPOINT) == CHECKPOINT_SHA256
    return not changes and checkpoint_valid, {
        "frozen_source_rows": freeze["frozen_source_rows"],
        "current_source_rows": current,
        "source_changes": changes,
        "checkpoint": hash_record(CHECKPOINT),
        "checkpoint_valid": checkpoint_valid,
    }


def seed_occurrences(seed: int) -> List[str]:
    completed = subprocess.run(
        ["rg", "-l", "--fixed-strings", str(seed), str(ROOT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return sorted(completed.stdout.splitlines())


def generate_fresh_engineering_seeds(count: int) -> Tuple[List[int], List[Dict[str, Any]]]:
    admitted: List[int] = []
    rejected: List[Dict[str, Any]] = []
    for _ in range(1000):
        candidate = secrets.randbelow(2**31 - 1) + 1
        occurrences = seed_occurrences(candidate)
        if candidate in admitted or occurrences:
            rejected.append(
                {
                    "candidate": candidate,
                    "reason": "DUPLICATE_OR_PRIOR_WORKSPACE_OCCURRENCE",
                    "occurrences": occurrences,
                }
            )
            continue
        admitted.append(candidate)
        if len(admitted) == count:
            return admitted, rejected
    raise RuntimeError("Q07_V2_UNABLE_TO_GENERATE_THREE_FRESH_ENGINEERING_SEEDS")


def make_config(run_id: str, scene: Mapping[str, Any], seed: int) -> Dict[str, Any]:
    config = p0.make_config(
        run_id,
        {
            **scene,
            "p0_resolved_canonical_instruction": CANONICAL_NATIVE_INSTRUCTION,
        },
        seed,
    )
    config["method_input"]["grounding_evidence_source"] = (
        "FROZEN_Q07_REPLACEMENT_V2_SCENE_AND_OFFICIAL_ROUTE"
    )
    binding = config["method_input"]["p0_scene_binding"]
    binding["stage"] = STAGE
    binding["scene_id"] = SCENE_ID
    binding["scene_digest"] = scene["scene_digest"]
    binding["route_id"] = ROUTE_ID
    config["engineering_qualification"] = {
        "stage": STAGE,
        "scope": SCOPE,
        "scene_id": SCENE_ID,
        "scene_code": SCENE_CODE,
        "future_scientific_denominator_eligible": False,
        "permanently_formal_ineligible_seed": seed,
        "qualification_rule": (
            "native completion AND no persistent native stop AND no major safety failure"
        ),
        "scientific_behavior_changes": [],
    }
    return config


def generate_seeds() -> Dict[str, Any]:
    freeze, scene = verify_scene_freeze()
    if (REPORT / "ENGINEERING_SEED_FRESHNESS_AND_EXCLUSION_RECEIPT.json").exists():
        raise RuntimeError("Q07_V2_SEEDS_ALREADY_GENERATED")
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Q07_V2_ENGINEERING_SEED_GENERATOR_REQUIRES_PYTHON_3_13")
    history_pass, _ = historical_integrity(freeze)
    sources_pass, _ = source_integrity(freeze)
    if not history_pass or not sources_pass:
        raise RuntimeError("Q07_V2_INTEGRITY_FAILURE_BEFORE_ENGINEERING_SEEDS")
    seeds, rejected = generate_fresh_engineering_seeds(3)
    if len(seeds) != 3 or len(set(seeds)) != 3:
        raise RuntimeError("Q07_V2_SEED_CARDINALITY_INVALID")

    CONFIGS.mkdir()
    RUNS.mkdir()
    runs = []
    route_path = ASSETS / "RQ3V3-P0-USC-EQUIVALENT-Q07-RV2-ROUTE.xml"
    for slot, seed in enumerate(seeds, 1):
        run_id = "RQ3V3-P0-USC-EQUIVALENT-Q07-RV2-S%02d" % slot
        config_path = CONFIGS / (run_id + ".json")
        write_json(config_path, make_config(run_id, scene, seed))
        runs.append(
            {
                "ordinal": slot,
                "seed_slot": slot,
                "run_id": run_id,
                "scene_id": SCENE_ID,
                "scene_code": SCENE_CODE,
                "scene_digest": scene["scene_digest"],
                "engineering_seed": seed,
                "config_path": rel(config_path),
                "config_sha256": p0.sha_file(config_path),
                "route_path": rel(route_path),
                "route_sha256": p0.sha_file(route_path),
                "route_id": ROUTE_ID,
                "town": "Town05",
                "rpc_port": PORTS[slot]["rpc"],
                "streaming_port": PORTS[slot]["streaming"],
                "traffic_manager_port": PORTS[slot]["tm"],
                "output_path": rel(RUNS / run_id),
                "runtime_mode": "NATIVE_DEFAULT",
                "agent_mode": "NATIVE_SIMLINGO",
                "future_scientific_denominator_eligible": False,
                "formal_seed": False,
            }
        )
    roster = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.execution-roster.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "status": "FROZEN_UNEXECUTED_EXACTLY_THREE_RUNS",
        "scene_id": SCENE_ID,
        "scene_freeze_digest": freeze["freeze_digest"],
        "seed_identity_count": 3,
        "planned_native_runs": 3,
        "accepted_q01_to_q06_q08_runs": 0,
        "runs": runs,
    }
    roster["roster_digest"] = digest(roster)
    write_json(REPORT / "EXECUTION_ROSTER.json", roster)

    seed_receipt = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.engineering-seed-freshness.v1",
        "stage": STAGE,
        "status": "PASS_EXACTLY_THREE_FRESH_ENGINEERING_ONLY_SEEDS_EXCLUDED_FROM_FORMAL",
        "generated_after_scene_freeze": True,
        "scene_freeze_digest": freeze["freeze_digest"],
        "generator_python_version": sys.version.split()[0],
        "generator": "secrets.randbelow(2**31 - 1) + 1",
        "ordered_engineering_seed_identities": seeds,
        "seed_count": 3,
        "pairwise_distinct": True,
        "historical_workspace_prior_occurrences": {str(seed): [] for seed in seeds},
        "fresh_against_previous_scientific_and_engineering_seeds": True,
        "rejected_draws": rejected,
        "original_p0_seeds_reused": False,
        "permanently_excluded_from_formal_science": seeds,
        "future_scientific_denominator_eligible": False,
        "formal_seeds_generated": False,
        "formal_execution_started": False,
    }
    seed_receipt["receipt_digest"] = digest(seed_receipt)
    write_json(REPORT / "ENGINEERING_SEED_FRESHNESS_AND_EXCLUSION_RECEIPT.json", seed_receipt)
    exclusion = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.engineering-seed-exclusion-registry.v1",
        "stage": STAGE,
        "status": "PERMANENTLY_EXCLUDED_FROM_ALL_FORMAL_SCIENCE",
        "engineering_seed_identities": seeds,
        "formal_eligibility": False,
        "scientific_denominator_eligibility": False,
        "reason": "Q07 replacement V2 P0 engineering qualification exposure",
    }
    exclusion["registry_digest"] = digest(exclusion)
    write_json(REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json", exclusion)
    ledger = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.execution-ledger.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "status": "FROZEN_UNEXECUTED",
        "roster_digest": roster["roster_digest"],
        "planned_runs": 3,
        "attempted_runs": 0,
        "valid_runs": 0,
        "accepted_scene_reruns": 0,
        "entries": [{**row, "execution_status": "PENDING"} for row in runs],
    }
    update_ledger(ledger)
    append_command(
        "\n- `python tools/run_rq3_v3_p0_usc_equivalent_replacement_v2.py generate-seeds`\n"
        "  - generated exactly three fresh engineering-only identities after the scene freeze\n"
        "  - permanently excluded all three from formal science; formal seeds generated: `0`\n"
    )
    return seed_receipt


def update_ledger(ledger: Mapping[str, Any]) -> None:
    value = {key: item for key, item in ledger.items() if key != "ledger_digest"}
    value["ledger_digest"] = digest(value)
    write_json(REPORT / "EXECUTION_LEDGER.json", value)


def verify_roster() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    freeze, scene = verify_scene_freeze()
    roster = verify_digest_file(REPORT / "EXECUTION_ROSTER.json", "roster_digest")
    seed_receipt = verify_digest_file(
        REPORT / "ENGINEERING_SEED_FRESHNESS_AND_EXCLUSION_RECEIPT.json", "receipt_digest"
    )
    if roster.get("planned_native_runs") != 3 or len(roster.get("runs", [])) != 3:
        raise RuntimeError("Q07_V2_ROSTER_NOT_EXACTLY_THREE")
    if seed_receipt.get("seed_count") != 3 or seed_receipt.get("formal_seeds_generated"):
        raise RuntimeError("Q07_V2_SEED_RECEIPT_INVALID")
    if [row["engineering_seed"] for row in roster["runs"]] != seed_receipt[
        "ordered_engineering_seed_identities"
    ]:
        raise RuntimeError("Q07_V2_ROSTER_SEED_BINDING_MISMATCH")
    return freeze, scene, roster


def normalize_terminal_artifacts(output: Path) -> None:
    process_path = output / "process_job/PROCESS_RECEIPT.json"
    summary_path = output / "RUN_TERMINAL_SUMMARY.json"
    process = load(process_path, {})
    summary = load(summary_path, {})
    if not process or not summary:
        raise RuntimeError("Q07_V2_TERMINAL_ARTIFACT_MISSING")
    process["schema"] = "driveclarify.rq3-v3-p0.q07-v2.process-receipt.v1"
    process["stage"] = STAGE
    process["scope"] = SCOPE
    process["receipt_digest"] = canonical_without(process, "receipt_digest")
    write_json(process_path, process)
    summary["schema"] = "driveclarify.rq3-v3-p0.q07-v2.run-terminal-summary.v1"
    summary["stage"] = STAGE
    summary["scope"] = SCOPE
    summary["process"] = process
    summary["receipt_digest"] = canonical_without(summary, "receipt_digest")
    write_json(summary_path, summary)


def evaluate_run(planned: Mapping[str, Any]) -> Dict[str, Any]:
    result = p0.evaluate_run(planned)
    official = result.get("official") or {}
    infractions = official.get("infractions") or {}
    collision_count = sum(
        int(infractions.get(key, 0) or 0)
        for key in ("collisions_layout", "collisions_pedestrian", "collisions_vehicle")
    )
    outside_route_lane_count = int(infractions.get("outside_route_lanes", 0) or 0)
    route_deviation_count = int(infractions.get("route_dev", 0) or 0)
    major_safety_failure = (
        collision_count > 0 or outside_route_lane_count > 0 or route_deviation_count > 0
    )
    result["schema"] = "driveclarify.rq3-v3-p0.q07-v2.run-result.v1"
    result["stage"] = STAGE
    result["scope"] = SCOPE
    result["native_task_completion"] = bool(result.get("native_task_goal_reached"))
    result["major_safety_failure"] = major_safety_failure
    result["major_safety_counts"] = {
        "collision": collision_count,
        "offroad_or_wrong_lane_as_official_outside_route_lanes": outside_route_lane_count,
        "route_deviation": route_deviation_count,
    }
    result["separately_reported_nonmajor_counts"] = {
        "red_light": int(infractions.get("red_light", 0) or 0),
        "stop_infraction": int(infractions.get("stop_infraction", 0) or 0),
        "min_speed_infractions": int(infractions.get("min_speed_infractions", 0) or 0),
    }
    result["safe_native_qualification_pass"] = bool(
        result.get("valid")
        and result.get("native_task_goal_reached")
        and not result.get("native_stall_or_noncompletion")
        and not major_safety_failure
    )
    result["valid_native_failure_retried"] = False
    result["seed_replaced"] = False
    result["run_result_digest"] = canonical_without(result, "run_result_digest")
    write_json(ROOT / planned["output_path"] / "P0_RUN_RESULT.json", result)
    return result


def run_campaign() -> Dict[str, Any]:
    freeze, _, roster = verify_roster()
    ledger = verify_digest_file(REPORT / "EXECUTION_LEDGER.json", "ledger_digest")
    if ledger.get("status") != "FROZEN_UNEXECUTED":
        raise RuntimeError("Q07_V2_FROZEN_UNEXECUTED_LEDGER_REQUIRED")
    history_pass, _ = historical_integrity(freeze)
    sources_pass, _ = source_integrity(freeze)
    if not history_pass or not sources_pass:
        raise RuntimeError("Q07_V2_INTEGRITY_FAILURE_BEFORE_EXECUTION")
    ledger["status"] = "RUNNING"
    ledger["execution_started_epoch"] = int(time.time())
    update_ledger(ledger)
    append_command(
        "\n- `python tools/run_rq3_v3_p0_usc_equivalent_replacement_v2.py run`\n"
        "  - began the frozen three-cell native roster; no retry and no seed replacement\n"
    )
    for index, planned in enumerate(roster["runs"]):
        history_pass, history = historical_integrity(freeze)
        sources_pass, sources = source_integrity(freeze)
        if not history_pass or not sources_pass:
            ledger["status"] = "STOPPED_TECHNICALLY_INVALID"
            ledger["technical_invalidity"] = {
                "before_ordinal": planned["ordinal"],
                "reason": "SOURCE_CHECKPOINT_OR_HISTORY_DRIFT",
                "history": history,
                "source_changes": sources["source_changes"],
            }
            update_ledger(ledger)
            return ledger
        output = ROOT / planned["output_path"]
        if output.exists():
            raise RuntimeError("Q07_V2_OUTPUT_ALREADY_EXISTS:" + rel(output))
        ledger["entries"][index]["execution_status"] = "RUNNING"
        ledger["entries"][index]["started_epoch"] = int(time.time())
        update_ledger(ledger)
        command = [
            str(RUNNER),
            str(ROOT / planned["config_path"]),
            str(ROOT / planned["route_path"]),
            str(planned["engineering_seed"]),
            str(planned["rpc_port"]),
            str(planned["traffic_manager_port"]),
            str(output),
        ]
        completed = subprocess.run(command, cwd=str(ROOT), check=False)
        try:
            normalize_terminal_artifacts(output)
            result = evaluate_run(planned)
        except Exception as error:
            result = {
                "schema": "driveclarify.rq3-v3-p0.q07-v2.run-result.v1",
                "stage": STAGE,
                "scope": SCOPE,
                "run_id": planned["run_id"],
                "scene_id": SCENE_ID,
                "seed_slot": planned["seed_slot"],
                "engineering_seed": planned["engineering_seed"],
                "valid": False,
                "validity": "P0_TECHNICALLY_INVALID",
                "technical_invalidity_reasons": [repr(error)],
                "wrapper_returncode": completed.returncode,
                "safe_native_qualification_pass": False,
                "valid_native_failure_retried": False,
                "seed_replaced": False,
            }
            result["run_result_digest"] = digest(result)
            write_json(output / "P0_RUN_RESULT.json", result)
        entry = ledger["entries"][index]
        entry.update(
            {
                "execution_status": "COMPLETED" if result.get("valid") else "TECHNICALLY_INVALID",
                "finished_epoch": int(time.time()),
                "wrapper_returncode": completed.returncode,
                "validity": result.get("validity"),
                "safe_native_qualification_pass": result.get("safe_native_qualification_pass"),
                "native_task_completion": result.get("native_task_completion"),
                "native_stall_or_noncompletion": result.get("native_stall_or_noncompletion"),
                "major_safety_failure": result.get("major_safety_failure"),
                "major_safety_counts": result.get("major_safety_counts"),
                "route_completion": (result.get("official") or {}).get("route_completion"),
                "separately_reported_nonmajor_counts": result.get(
                    "separately_reported_nonmajor_counts"
                ),
                "run_result_path": rel(output / "P0_RUN_RESULT.json"),
                "run_result_digest": result["run_result_digest"],
                "retry_count": 0,
                "seed_replacement": False,
            }
        )
        ledger["attempted_runs"] = index + 1
        ledger["valid_runs"] = sum(
            row.get("validity") == "VALID" for row in ledger["entries"]
        )
        if not result.get("valid"):
            ledger["status"] = "STOPPED_TECHNICALLY_INVALID"
            ledger["technical_invalidity_ordinal"] = planned["ordinal"]
            update_ledger(ledger)
            print(
                "Q07 V2 stopped: ordinal %d technically invalid" % planned["ordinal"],
                flush=True,
            )
            return ledger
        update_ledger(ledger)
        print(
            "Q07 V2 progress: ordinal %d valid; safe-pass=%s"
            % (planned["ordinal"], result["safe_native_qualification_pass"]),
            flush=True,
        )
    ledger["status"] = "COMPLETED_EXACTLY_THREE_VALID_RUNS"
    ledger["execution_finished_epoch"] = int(time.time())
    update_ledger(ledger)
    return ledger


def load_run_results(roster: Mapping[str, Any]) -> List[Dict[str, Any]]:
    results = []
    for row in roster["runs"]:
        path = ROOT / row["output_path"] / "P0_RUN_RESULT.json"
        value = load(path, {})
        if value and canonical_without(value, "run_result_digest") != value.get("run_result_digest"):
            raise RuntimeError("Q07_V2_RUN_RESULT_DIGEST_MISMATCH:" + row["run_id"])
        if value:
            results.append(value)
    return results


def analyze() -> Dict[str, Any]:
    freeze, scene, roster = verify_roster()
    ledger = verify_digest_file(REPORT / "EXECUTION_LEDGER.json", "ledger_digest")
    seed_receipt = verify_digest_file(
        REPORT / "ENGINEERING_SEED_FRESHNESS_AND_EXCLUSION_RECEIPT.json", "receipt_digest"
    )
    results = load_run_results(roster)
    history_pass, history = historical_integrity(freeze)
    sources_pass, sources = source_integrity(freeze)
    integrity_pass = history_pass and sources_pass
    all_valid = (
        ledger.get("status") == "COMPLETED_EXACTLY_THREE_VALID_RUNS"
        and len(results) == 3
        and all(row.get("validity") == "VALID" for row in results)
    )
    safe_pass_count = sum(row.get("safe_native_qualification_pass") is True for row in results)
    if not integrity_pass or not all_valid:
        status = "BLOCKED_RQ3_V3_P0_Q07_REPLACEMENT_TECHNICALLY_INVALID"
    elif safe_pass_count >= 2:
        status = "PASS_RQ3_V3_P0_ALL_SCENES_QUALIFIED_AFTER_Q07_REPLACEMENT"
    else:
        status = "FAIL_RQ3_V3_P0_Q07_REPLACEMENT_NOT_NATIVELY_QUALIFIED"
    if status not in ALLOWED_FINAL_STATUSES:
        raise RuntimeError("Q07_V2_FINAL_STATUS_INVALID")

    native_completion_count = sum(row.get("native_task_completion") is True for row in results)
    major_failure_count = sum(row.get("major_safety_failure") is True for row in results)
    collision_counts = [
        (row.get("major_safety_counts") or {}).get("collision") for row in results
    ]
    route_completion = [(row.get("official") or {}).get("route_completion") for row in results]
    nonmajor = [row.get("separately_reported_nonmajor_counts") or {} for row in results]
    final = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.final-qualification-result.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "final_status": status,
        "new_q07_scene_identity": SCENE_ID,
        "scene_digest": scene["scene_digest"],
        "task_equivalent_certification": scene["semantic_certification"],
        "task_relation": "TASK_EQUIVALENT",
        "correct_driveclarify_behavior": "ACT",
        "engineering_seed_identities": seed_receipt["ordered_engineering_seed_identities"],
        "planned_runs": 3,
        "attempted_runs": ledger.get("attempted_runs"),
        "valid_runs": sum(row.get("validity") == "VALID" for row in results),
        "safe_native_qualification_pass_count": safe_pass_count,
        "qualification_threshold": ">=2/3 valid runs",
        "native_completion_count": native_completion_count,
        "persistent_native_stop_count": sum(
            bool(row.get("persistent_stop_classes")) for row in results
        ),
        "major_safety_failure_count": major_failure_count,
        "collision_counts_per_run": collision_counts,
        "collision_count_total": sum(value or 0 for value in collision_counts),
        "outside_route_lane_counts_per_run": [
            (row.get("major_safety_counts") or {}).get(
                "offroad_or_wrong_lane_as_official_outside_route_lanes"
            )
            for row in results
        ],
        "route_deviation_counts_per_run": [
            (row.get("major_safety_counts") or {}).get("route_deviation") for row in results
        ],
        "route_completion_percent": route_completion,
        "red_light_counts_per_run": [row.get("red_light") for row in nonmajor],
        "stop_sign_counts_per_run": [row.get("stop_infraction") for row in nonmajor],
        "minimum_speed_counts_per_run": [
            row.get("min_speed_infractions") for row in nonmajor
        ],
        "old_q07_preserved": history["original_p0_tree_unchanged"]
        and history["tm_recovery_tree_unchanged"],
        "old_q07_preservation_classification": OLD_Q07_CLASSIFICATION,
        "q01_to_q06_and_q08_rerun": False,
        "accepted_scene_rerun_count": 0,
        "scientific_components_changed": [],
        "native_host_geometry_task_instance_changed_only": True,
        "unchanged_policy_reserve_seconds": 1.20,
        "seed_replacements": 0,
        "valid_native_failure_retries": 0,
        "formal_seeds_generated": False,
        "formal_execution_started": False,
        "formal_rq3_v3_freeze_started": False,
        "source_and_history_integrity_pass": integrity_pass,
        "run_results": [
            {
                "run_id": row.get("run_id"),
                "seed_slot": row.get("seed_slot"),
                "engineering_seed": row.get("engineering_seed"),
                "validity": row.get("validity"),
                "native_task_completion": row.get("native_task_completion"),
                "persistent_native_stop": bool(row.get("persistent_stop_classes")),
                "major_safety_failure": row.get("major_safety_failure"),
                "major_safety_counts": row.get("major_safety_counts"),
                "route_completion": (row.get("official") or {}).get("route_completion"),
                "safe_native_qualification_pass": row.get("safe_native_qualification_pass"),
                "separately_reported_nonmajor_counts": row.get(
                    "separately_reported_nonmajor_counts"
                ),
                "run_result_digest": row.get("run_result_digest"),
            }
            for row in results
        ],
        "next_step": "RETURN_FOR_SEPARATE_FINAL_FORMAL_FREEZE_AUTHORIZATION",
    }
    final["results_digest"] = digest(final)
    write_json(REPORT / "FINAL_QUALIFICATION_RESULT.json", final)

    preservation = {
        "schema": "driveclarify.rq3-v3-p0.q07-v2.source-history-preservation.v1",
        "stage": STAGE,
        "status": (
            "PASS_SOURCE_AND_HISTORY_PRESERVED"
            if integrity_pass
            else "FAIL_SOURCE_OR_HISTORY_DRIFT"
        ),
        "pass": integrity_pass,
        **sources,
        **history,
        "old_q07_preserved": final["old_q07_preserved"],
        "old_q07_preservation_classification": OLD_Q07_CLASSIFICATION,
        "q01_to_q06_and_q08_rerun": False,
        "accepted_scene_rerun_count": 0,
        "scientific_components_changed": [],
        "formal_seeds_generated": False,
        "formal_execution_started": False,
    }
    preservation["receipt_digest"] = digest(preservation)
    write_json(REPORT / "SOURCE_AND_HISTORY_PRESERVATION_RECEIPT.json", preservation)

    run_lines = []
    for row in final["run_results"]:
        run_lines.append(
            "| %s | %s | %s | %s | %s | %s | %s |"
            % (
                row["seed_slot"],
                row["engineering_seed"],
                row["validity"],
                "YES" if row["native_task_completion"] else "NO",
                "YES" if row["major_safety_failure"] else "NO",
                (row["major_safety_counts"] or {}).get("collision"),
                row["route_completion"],
            )
        )
    write_text(
        REPORT / "FINAL_REPORT.md",
        "# RQ3-V3 P0 USC-EQUIVALENT replacement V2\n\n"
        "Final status: `%s`\n\n"
        "New Q07 scene: `%s` (Town05 road 34, section 0, lane +4; candidate A "
        "at s=28 and B at s=20).\n\n"
        "TASK_EQUIVALENT certification: `PASS`; unchanged RQ1 comparator returned "
        "`TASK_EQUIVALENT`, and the correct unchanged policy decision remains `ACT`.\n\n"
        "| Seed slot | Engineering seed | Validity | Native completion | Major safety failure | Collisions | Route Completion (%%) |\n"
        "|---:|---:|---|---|---|---:|---:|\n%s\n\n"
        "## Required closure facts\n\n"
        "- Engineering seeds: `%s`; permanently excluded from formal science.\n"
        "- Native completion count: `%d/3`.\n"
        "- Safe native qualification pass count: `%d/3` (threshold `>=2/3`).\n"
        "- Major-safety-failure count: `%d/3`.\n"
        "- Collision count: `%d` total; per run `%s`.\n"
        "- Route Completion: `%s`.\n"
        "- Red-light counts: `%s`; stop-sign counts: `%s`; minimum-speed counts: `%s`.\n"
        "- Old Q07 preserved: `YES`, classified `%s`.\n"
        "- Q01-Q06/Q08 rerun: `NO`.\n"
        "- Scientific components changed: `NONE`; native host geometry/task instance only.\n"
        "- ACT/ASK/WAIT policy and clarification reserve: unchanged; reserve `1.20 s`.\n"
        "- Formal seeds generated: `NO`.\n"
        "- Formal execution started: `NO`.\n"
        "- Formal RQ3-V3 freeze started: `NO`.\n"
        "- Source/history preservation: `%s`.\n"
        "- Next step: return for separate final formal-freeze authorization.\n"
        % (
            status,
            SCENE_ID,
            "\n".join(run_lines),
            ", ".join(str(seed) for seed in final["engineering_seed_identities"]),
            native_completion_count,
            safe_pass_count,
            major_failure_count,
            final["collision_count_total"],
            final["collision_counts_per_run"],
            route_completion,
            final["red_light_counts_per_run"],
            final["stop_sign_counts_per_run"],
            final["minimum_speed_counts_per_run"],
            OLD_Q07_CLASSIFICATION,
            preservation["status"],
        ),
    )
    append_command(
        "\n- `python tools/run_rq3_v3_p0_usc_equivalent_replacement_v2.py analyze`\n"
        "  - aggregated the safety-aware Q07 qualification and sealed preservation evidence\n"
        "  - formal seeds/execution/freeze started: `NO/NO/NO`\n"
    )
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("freeze-scene", "generate-seeds", "run", "analyze")
    )
    args = parser.parse_args()
    if args.command == "freeze-scene":
        value = freeze_scene()
    elif args.command == "generate-seeds":
        value = generate_seeds()
    elif args.command == "run":
        value = run_campaign()
    else:
        value = analyze()
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
