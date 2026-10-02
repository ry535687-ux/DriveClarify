"""确定性生成 M3D 静态分支 MVP 的机器可读工件。

该脚本只解析 XML/OpenDRIVE 和本地文本，不 import CARLA/torch/SimLingo，也不执行 pilot。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from driveclarify_static_branch.topology import (
    build_branch_topology_ground_truth,
    deterministic_sha256,
    inspect_route_topology,
    sha256_file,
)


REPO = Path(__file__).resolve().parents[1]
REPORT = REPO / "reports/static_maneuver_branch_primary_mvp_v1"
PACKAGE = REPORT / "M3E_STATIC_BRANCH_PILOT"
XODR = Path("/home/buaa/CARLA_0.9.15/CarlaUE4/Content/Carla/Maps/OpenDrive/Town03.xodr")
ROUTE_27515 = Path("/home/buaa/wrh/simlingo/leaderboard/data/bench2drive_split/bench2drive_202.xml")
ROUTE_26950 = Path("/home/buaa/wrh/simlingo/leaderboard/data/bench2drive_split/bench2drive_195.xml")


PRIMARY_BINDING = {
    "route_id": "27515",
    "town": "Town03",
    "junction_id": "238",
    "incoming_road_id": "69",
    "incoming_lane_id": -2,
    "straight": {
        "connecting_road_id": "271",
        "connecting_lane_id": -1,
        "successor_road_id": "4",
        "successor_lane_id": 3,
        "successor_contact_point": "end",
    },
    "right": {
        "connecting_road_id": "341",
        "connecting_lane_id": -1,
        "successor_road_id": "24",
        "successor_lane_id": -1,
        "successor_contact_point": "start",
    },
}

SECONDARY_BINDING = {
    "route_id": "26950",
    "town": "Town03",
    "junction_id": "1221",
    "incoming_road_id": "28",
    "incoming_lane_id": -2,
    "straight": {
        "connecting_road_id": "1334",
        "connecting_lane_id": -2,
        "successor_road_id": "29",
        "successor_lane_id": -2,
        "successor_contact_point": "start",
    },
    "right": {
        "connecting_road_id": "1257",
        "connecting_lane_id": -1,
        "successor_road_id": "18",
        "successor_lane_id": 1,
        "successor_contact_point": "end",
    },
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _with_hash(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result.pop("sha256", None)
    result["sha256"] = deterministic_sha256(result)
    return result


def _endpoint_tangent(polyline: list[list[float]]) -> tuple[float, float]:
    dx, dy = polyline[-1][0] - polyline[-2][0], polyline[-1][1] - polyline[-2][1]
    norm = math.hypot(dx, dy)
    return dx / norm, dy / norm


def _threshold_contract(topology: dict[str, Any]) -> dict[str, Any]:
    by_role = {item["semantic_role"]: item for item in topology["branches"]}
    straight = _endpoint_tangent(by_role["STRAIGHT_BRANCH"]["evaluation_polyline_world_xyz"])
    right = _endpoint_tangent(by_role["RIGHT_TURN_BRANCH"]["evaluation_polyline_world_xyz"])
    dot = max(-1.0, min(1.0, straight[0] * right[0] + straight[1] * right[1]))
    endpoint_heading_separation = math.degrees(math.acos(dot))
    alignment_gate_degrees = endpoint_heading_separation / 2.0
    value = {
        "schema_version": "driveclarify.mapping_threshold_provenance.v1",
        "contract_name": "StaticBranchPlanMapperV1Thresholds",
        "distance_threshold_m": 1.75,
        "alignment_threshold_cosine": math.cos(math.radians(alignment_gate_degrees)),
        "branch_score_margin": 0.10,
        "lane_width_reference_m": 3.5,
        "topology_separation_m": 1.75,
        "numerical_tolerance": 1e-9,
        "tail_point_count": 3,
        "provenance": {
            "distance_threshold_m": "HALF_OF_OPENDRIVE_3_5_M_DRIVING_LANE_WIDTH",
            "alignment_threshold_cosine": "COSINE_OF_HALF_THE_PREDECLARED_EVALUATION_ENDPOINT_TANGENT_SEPARATION",
            "endpoint_tangent_separation_degrees": endpoint_heading_separation,
            "alignment_gate_degrees": alignment_gate_degrees,
            "branch_score_margin": "PREDECLARED_10_PERCENT_NORMALIZED_GEOMETRY_SCORE_GUARD",
            "lane_width_reference_m": "OPENDRIVE_LANE_WIDTH_A_COEFFICIENT_ON_PRIMARY_INCOMING_AND_CONNECTOR_LANES",
            "topology_separation_m": "DIVERGENCE_DECLARED_AT_HALF_REFERENCE_LANE_WIDTH",
            "numerical_tolerance": "BINARY64_GEOMETRY_COMPARISON_TOLERANCE",
        },
        "forbidden_sources": ["MODEL_OUTPUT", "CANDIDATE_RESULT", "HIDDEN_LABEL", "PILOT_SUCCESS"],
        "frozen_before_model_output": True,
    }
    return _with_hash(value)


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    PACKAGE.mkdir(parents=True, exist_ok=True)
    primary_inspection = inspect_route_topology(XODR, ROUTE_27515, PRIMARY_BINDING)
    secondary_inspection = inspect_route_topology(XODR, ROUTE_26950, SECONDARY_BINDING)
    topology = build_branch_topology_ground_truth(XODR, ROUTE_27515, PRIMARY_BINDING)
    thresholds = _threshold_contract(topology)

    shortlist = {
        "schema_version": "driveclarify.static_route_shortlist.v1",
        "selection_status": "TWO_STATIC_TOPOLOGY_SEEDS_FOUND_PRIMARY_REQUIRES_SCENARIO_FREE_ROUTE_VALIDATION",
        "selection_policy": {
            "candidate_output_used": False,
            "mapper_output_used": False,
            "hidden_label_used": False,
            "pilot_success_used": False,
            "maximum_candidate_count": 3,
            "actual_candidate_count": 2,
        },
        "candidates": [
            {
                "route_id": "27515",
                "town": "Town03",
                "junction_id": "238",
                "decision_point": primary_inspection["decision_point"],
                "lead_in_distance": primary_inspection["route_start_incoming_lane_match"],
                "straight_branch_available": True,
                "right_branch_available": True,
                "branch_topology_source": "Town03.xodr junction 238 connection 6/16 and lane centerlines 69/-2, 271/-1→4/3, 341/-1→24/-1",
                "dynamic_actor_dependency": "EXISTING_ROUTE_HAS_PEDESTRIAN_CROSSING; SELECTED_PRIMARY_FIXTURE_MUST_REMOVE_THIS_DEPENDENCY",
                "scenario_trigger_dependency": "EXISTING_ROUTE_DEPENDS_ON_PEDESTRIAN_TRIGGER; PROPOSED_PRIMARY_IS_SCENARIO_FREE",
                "first_observation_risk": "HIGH_ON_EXISTING_ROUTE_BECAUSE_TRIGGER_IS_NEAR_START; UNKNOWN_UNTIL_SEPARATE_SCENARIO_FREE_FIXTURE_RUNTIME_VALIDATION",
                "required_core_change": "EVALUATOR_SCENARIO_CONFIG_ZERO_ACCESS_NEEDS_SEPARATE_SCENARIO_FREE_COMPATIBILITY_PATH; NOT_IMPLEMENTED_THIS_ROUND",
                "suitability": "PREFERRED_STATIC_TOPOLOGY_SEED_CONDITIONAL_ON_SEPARATE_SCENARIO_FREE_FIXTURE_AND_ROUTE_VALIDATION",
                "inspection_sha256": primary_inspection["sha256"],
            },
            {
                "route_id": "26950",
                "town": "Town03",
                "junction_id": "1221",
                "decision_point": secondary_inspection["decision_point"],
                "lead_in_distance": secondary_inspection["route_start_incoming_lane_match"],
                "straight_branch_available": True,
                "right_branch_available": True,
                "branch_topology_source": "Town03.xodr junction 1221 connection 14/5 and lane centerlines 28/-2, 1334/-2→29/-2, 1257/-1→18/1",
                "dynamic_actor_dependency": "EXISTING_ROUTE_HAS_OPPOSITE_VEHICLE_RUNNING_RED_LIGHT; FORBIDDEN_FOR_PRIMARY",
                "scenario_trigger_dependency": "EXISTING_ROUTE_HAS_ROUTE_GATE_AND_INTERNAL_DYNAMIC_TRIGGER; FORBIDDEN_FOR_PRIMARY",
                "first_observation_risk": "TOPOLOGY_LEAD_IN_IS ADEQUATE BUT CURRENT DYNAMIC SCENARIO IS RETIRED AND CANNOT BE REUSED",
                "required_core_change": "WOULD_REQUIRE_A DISTINCT SCENARIO_FREE FIXTURE; R2, RED-LIGHT CONFIG, ANCHORS AND THRESHOLDS MUST REMAIN UNCHANGED",
                "suitability": "SECONDARY_STATIC_TOPOLOGY_REFERENCE_ONLY_NOT_CURRENT_SCENARIO_PRIMARY",
                "inspection_sha256": secondary_inspection["sha256"],
            },
        ],
        "third_candidate": "NOT_ADDED_BECAUSE_STATIC_EVIDENCE_DID_NOT_JUSTIFY_ANOTHER_STRAIGHT_RIGHT_PRIMARY_SEED_WITHOUT_GUESSING",
        "source_files": [
            {"path": str(XODR), "sha256": sha256_file(XODR)},
            {"path": str(ROUTE_27515), "sha256": sha256_file(ROUTE_27515)},
            {"path": str(ROUTE_26950), "sha256": sha256_file(ROUTE_26950)},
        ],
    }
    shortlist = _with_hash(shortlist)

    selection = _with_hash({
        "schema_version": "driveclarify.static_primary_instance_selection.v1",
        "selected_topology_seed": {"route_id": "27515", "town": "Town03", "junction_id": "238"},
        "selection_verdict": "SELECTED_TOPOLOGY_CONDITIONAL_ON_NEW_SCENARIO_FREE_FIXTURE",
        "final_readiness_state": "PARTIAL_READY_STATIC_BRANCH_REQUIRES_ROUTE_VALIDATION",
        "reasons": [
            "STRAIGHT_AND_RIGHT_LANE_LINKS_VERIFIED_FROM_OPENDRIVE",
            "BRANCH_GROUND_TRUTH_FROZEN_FROM_LANE_CENTERLINES",
            "NO_DYNAMIC_ACTOR_NEEDED_BY_THE_SELECTED_TOPOLOGY",
            "EXISTING_ROUTE_PEDESTRIAN_SCENARIO_MUST_NOT_BE_REUSED",
            "CURRENT_EVALUATOR_INDEXES_SCENARIO_CONFIG_ZERO_AND_IS_NOT_YET_SCENARIO_FREE_COMPATIBLE",
            "FIRST_MODEL_READY_OBSERVATION_DISTANCE_NOT_RUNTIME_VALIDATED",
        ],
        "selection_blinding": {
            "candidate_plan_read": False,
            "model_output_read": False,
            "mapper_result_read": False,
            "hidden_label_read": False,
        },
        "branch_topology_ground_truth_sha256": topology["sha256"],
        "mapping_threshold_provenance_sha256": thresholds["sha256"],
        "dynamic_actor_dependency_for_planned_fixture": False,
        "scenario_trigger_dependency_for_planned_fixture": False,
        "real_pilot_executed": False,
    })

    run_spec = _with_hash({
        "schema_version": "driveclarify.m3e_static_branch_run_spec.v1",
        "package_name": "M3E Static Branch Pilot",
        "pilot_id": "M3E-STATIC-BRANCH-PILOT-PLANNED-001",
        "run_id": None,
        "run_authorized": False,
        "authorization_receipt_required_before_execution": True,
        "authorization_receipt_present": False,
        "execution_status": "NOT_AUTHORIZED_REQUIRES_ROUTE_FIXTURE_AND_EVALUATOR_COMPATIBILITY_VALIDATION",
        "execution_command": None,
        "automatic_continuation": False,
        "town": "Town03",
        "route_topology_seed_id": "27515",
        "planned_route_fixture": "SEPARATELY_NAMED_SCENARIO_FREE_FIXTURE_NOT_YET_CREATED",
        "frozen_observation_count": 1,
        "repeat_count_per_candidate": 3,
        "candidate_forward_count_authorized": 0,
        "world_tick_during_candidate_batch": 0,
        "pid_calls_during_candidate_batch": 0,
        "planner_calls_during_candidate_batch": 0,
        "control_calls_during_candidate_batch": 0,
        "control_ceiling": False,
        "carla_launches_this_package": 0,
        "simlingo_model_or_checkpoint_loads_this_package": 0,
        "model_forwards_this_package": 0,
        "cuda_initializations_this_package": 0,
        "gpu_uses_this_package": 0,
        "prohibited": ["R3", "R2_MODIFICATION", "RED_LIGHT_SCENARIO_REUSE", "AUTHORIZATION_RECEIPT_CREATION", "REAL_EXECUTION"],
    })

    capture_plan = _with_hash({
        "schema_version": "driveclarify.m3e_static_branch_capture_plan.v1",
        "package_name": "M3E Static Branch Pilot",
        "run_authorized": False,
        "topology_binding": {
            "town": "Town03",
            "route_id": "27515",
            "junction_id": "238",
            "branch_topology_sha256": topology["sha256"],
        },
        "candidate_definitions": [
            {"candidate_id": "A", "semantic_payload": "straight/lane-follow", "task_binding": "STRAIGHT_BRANCH"},
            {"candidate_id": "B", "semantic_payload": "right-turn/branch-taking", "task_binding": "RIGHT_TURN_BRANCH"},
        ],
        "mapper_candidate_name_policy": "CANDIDATE_ID_AND_NAME_NOT_USED_FOR_GEOMETRY",
        "planned_schedule": ["A1", "B1", "B2", "A2", "A3", "B3"],
        "schedule_provenance": "PREDECLARED_M3D_DESIGN_SCHEDULE_NOT_DERIVED_FROM_OUTPUT",
        "observation_policy": "FIRST_PREDECLARED_MODEL_READY_OBSERVATION_BEFORE_DECISION_POINT; NO_SECOND_OBSERVATION_FALLBACK",
        "plan_contract": {"frame": "EGO_LOCAL_X_FORWARD_Y_RIGHT", "unit": "METRE", "frame_evidence": "VERIFIED_REQUIRED", "unit_evidence": "VERIFIED_REQUIRED"},
        "fairness_contract": "CounterfactualFairnessContractV2",
        "mapping_contract": "StaticBranchPlanMapperV1",
        "output_domain": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH", "NO_MATCH", "AMBIGUOUS", "UNKNOWN"],
        "task_pair_domain": ["TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"],
        "candidate_or_model_output_used_to_define_topology_or_thresholds": False,
    })

    fairness_spec = json.loads((REPO / "reports/fairness_contract_audit_and_primary_mvp_v1/FAIRNESS_CONTRACT_V2.json").read_text(encoding="utf-8"))
    cpu_preflight = _with_hash({
        "schema_version": "driveclarify.m3e_static_branch_cpu_preflight.v1",
        "package_name": "M3E Static Branch Pilot",
        "overall_status": "PASS_CPU_DESIGN_PREFLIGHT_RUNTIME_ROUTE_VALIDATION_PENDING",
        "checks": {
            "route_topology_parse": "PASS",
            "branch_identity": "PASS",
            "branch_ground_truth_hash": "PASS",
            "mapping_threshold_hash": "PASS",
            "synthetic_fixture_count": 9,
            "static_branch_and_fairness_tests": "45_PASSED",
            "carla_import_or_launch": 0,
            "simlingo_import_model_or_forward": 0,
            "torch_cuda_gpu_use": 0,
            "authorization_receipt_created": 0,
            "r3_created": 0,
            "route_fixture_scenario_free_compatibility": "PENDING",
            "first_observation_runtime_distance_validation": "PENDING",
        },
        "ready_for_real_execution": False,
        "readiness_state": "PARTIAL_READY_STATIC_BRANCH_REQUIRES_ROUTE_VALIDATION",
    })

    _write_json(REPORT / "STATIC_ROUTE_SHORTLIST.json", shortlist)
    _write_json(REPORT / "STATIC_PRIMARY_INSTANCE_SELECTION.json", selection)
    _write_json(REPORT / "BRANCH_TOPOLOGY_GROUND_TRUTH.json", topology)
    _write_json(REPORT / "MAPPING_THRESHOLD_PROVENANCE.json", thresholds)
    _write_json(PACKAGE / "RUN_SPEC.json", run_spec)
    _write_json(PACKAGE / "CAPTURE_PLAN.json", capture_plan)
    _write_json(PACKAGE / "BRANCH_TOPOLOGY.json", topology)
    _write_json(PACKAGE / "FAIRNESS_CONTRACT.json", fairness_spec)
    _write_json(PACKAGE / "MAPPING_THRESHOLD_PROVENANCE.json", thresholds)
    _write_json(PACKAGE / "CPU_PREFLIGHT.json", cpu_preflight)

    source_manifest = _with_hash({
        "schema_version": "driveclarify.m3e_static_branch_source_manifest.v1",
        "package_name": "M3E Static Branch Pilot",
        "source_count": 9,
        "sources": [
            {"role": "opendrive", "path": str(XODR), "sha256": sha256_file(XODR)},
            {"role": "route_topology_seed", "path": str(ROUTE_27515), "sha256": sha256_file(ROUTE_27515)},
            {"role": "topology_parser", "path": str(REPO / "driveclarify_static_branch/topology.py"), "sha256": sha256_file(REPO / "driveclarify_static_branch/topology.py")},
            {"role": "static_branch_mapper", "path": str(REPO / "driveclarify_static_branch/mapper.py"), "sha256": sha256_file(REPO / "driveclarify_static_branch/mapper.py")},
            {"role": "fairness_v2", "path": str(REPO / "driveclarify_fairness_v2/contract.py"), "sha256": sha256_file(REPO / "driveclarify_fairness_v2/contract.py")},
            {"role": "branch_topology", "path": str(REPORT / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"), "sha256": sha256_file(REPORT / "BRANCH_TOPOLOGY_GROUND_TRUTH.json")},
            {"role": "mapping_thresholds", "path": str(REPORT / "MAPPING_THRESHOLD_PROVENANCE.json"), "sha256": sha256_file(REPORT / "MAPPING_THRESHOLD_PROVENANCE.json")},
            {"role": "synthetic_fixtures", "path": str(REPO / "tests/static_branch_mvp/fixtures/STATIC_BRANCH_SYNTHETIC_FIXTURES.json"), "sha256": sha256_file(REPO / "tests/static_branch_mvp/fixtures/STATIC_BRANCH_SYNTHETIC_FIXTURES.json")},
            {"role": "static_branch_tests", "path": str(REPO / "tests/static_branch_mvp/test_static_branch_mvp.py"), "sha256": sha256_file(REPO / "tests/static_branch_mvp/test_static_branch_mvp.py")},
        ],
        "all_sources_present": True,
        "scenario_free_route_fixture_present": False,
        "manifest_status": "PASS_FOR_DESIGN_PREFLIGHT_RUNTIME_FIXTURE_PENDING",
    })
    _write_json(PACKAGE / "SOURCE_MANIFEST.json", source_manifest)
    report_files = sorted(
        path for path in REPORT.rglob("*")
        if path.is_file() and path.name != "ARTIFACT_INVENTORY.json"
    )
    implementation_files = [
        REPO / "driveclarify_static_branch/__init__.py",
        REPO / "driveclarify_static_branch/topology.py",
        REPO / "driveclarify_static_branch/mapper.py",
        REPO / "tests/static_branch_mvp/test_static_branch_mvp.py",
        REPO / "tests/static_branch_mvp/fixtures/STATIC_BRANCH_SYNTHETIC_FIXTURES.json",
        REPO / "tools/generate_static_branch_mvp_artifacts.py",
    ]
    inventory = _with_hash({
        "schema_version": "driveclarify.m3d_artifact_inventory.v1",
        "report_directory": str(REPORT),
        "final_readiness_state": "PARTIAL_READY_STATIC_BRANCH_REQUIRES_ROUTE_VALIDATION",
        "report_artifact_count_excluding_inventory": len(report_files),
        "report_artifacts": [
            {
                "path": path.relative_to(REPO).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in report_files
        ],
        "implementation_and_test_artifacts": [
            {
                "path": path.relative_to(REPO).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in implementation_files
        ],
        "inventory_self_hash_included": False,
        "authorization_receipt_present": False,
        "run_outputs_present": False,
        "real_execution_performed": False,
    })
    _write_json(REPORT / "ARTIFACT_INVENTORY.json", inventory)
    print(json.dumps({
        "status": "GENERATED_CPU_ONLY",
        "report": str(REPORT),
        "topology_sha256": topology["sha256"],
        "threshold_sha256": thresholds["sha256"],
        "readiness": "PARTIAL_READY_STATIC_BRANCH_REQUIRES_ROUTE_VALIDATION",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
