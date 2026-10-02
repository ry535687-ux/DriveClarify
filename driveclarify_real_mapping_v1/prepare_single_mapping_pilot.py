"""Build the data-only specification for one unexecuted M3B pilot.

This module deliberately contains no CARLA, evaluator, torch, checkpoint, PID, planner,
or control entry point.  Repository artifacts are checked in separately; this function is
provided only for deterministic schema validation by CPU tests and future authorized tooling.
"""

from __future__ import annotations

from typing import Any

from .candidate_schedule_identity import build_candidate_schedule
from .maneuver_branch_mapper_v1 import MappingThresholds
from .plan_frame_trace import SUPPORTED_PLAN_FRAME, SUPPORTED_PLAN_UNIT


M3B_R2_CANDIDATE_ORDER = ("A3", "A1", "B1", "B2", "A2", "B3")
M3B_R2_RANDOMIZATION = {
    "algorithm": "PYTHON_RANDOM_SHUFFLE_WITH_256_BIT_OS_ENTROPY_SEED",
    "generated_at_preparation": True,
    "generated_before_candidate_outputs": True,
    "seed_hex": "87865b563e18ecfd2f8724cd6137a62a5d6897f8721ffcafb88b0760a6c9dee1",
}


def build_frozen_candidate_schedule(run_id: str) -> dict[str, Any]:
    """Build R2's unchanged schedule with the shared runtime identity helper."""

    return build_candidate_schedule(
        run_id=run_id,
        candidate_order=M3B_R2_CANDIDATE_ORDER,
        randomization=M3B_R2_RANDOMIZATION,
    )


def build_run_spec(run_id: str) -> dict[str, Any]:
    if not run_id.startswith("DC-RPSM-M3B-"):
        raise ValueError("M3B_RUN_ID_INVALID")
    return {
        "schema_version": "driveclarify.m3b.single_real_mapping_run_spec.v1",
        "run_id": run_id,
        "run_authorized": False,
        "automatic_continuation": False,
        "town": "Town03",
        "route_id": "26950",
        "scenario_name": "OppositeVehicleRunningRedLight_1",
        "scenario_type": "OppositeVehicleRunningRedLight",
        "observation_selection": "FIRST_MODEL_READY_OBSERVATION_ONLY",
        "frozen_observation_count": 1,
        "model_instance_count": 1,
        "candidates": {"A": 3, "B": 3, "semantics_unchanged": True},
        "candidate_side_effect_ceiling": {
            "pid": 0,
            "planner_advance": 0,
            "control": 0,
            "additional_world_tick": 0,
        },
        "mapping_contract": {
            "plan_frame": SUPPORTED_PLAN_FRAME,
            "plan_unit": SUPPORTED_PLAN_UNIT,
            "mapper": "ManeuverBranchPlanMapperV1",
            "thresholds": MappingThresholds.town03_route_26950().to_dict(),
            "candidate_name_may_determine_branch": False,
            "candidate_output_may_define_branch_geometry": False,
        },
        "retry_count": 0,
        "cleanup_mandatory": True,
    }


def build_capture_plan(run_id: str) -> dict[str, Any]:
    spec = build_run_spec(run_id)
    return {
        "schema_version": "driveclarify.m3b.capture_plan.v1",
        "run_id": run_id,
        "run_authorized": False,
        "capture_before_candidate_output": [
            "coordinate_semantic_evidence",
            "observation_to_ego_linkage",
            "scenario_actor_identity",
            "trigger_linkage",
            "plan_frame_unit_evidence",
            "scenario_runtime_fairness_verdict",
            "frozen_observation",
            "candidate_specific_branch_polylines",
        ],
        "capture_after_upstream_gates": ["A1", "A2", "A3", "B1", "B2", "B3", "mapper_output"],
        "fail_closed": {
            "any_upstream_evidence_failure": "CANDIDATE_MAPPING_UNKNOWN",
            "automatic_retry": False,
        },
        "side_effect_ceiling": spec["candidate_side_effect_ceiling"],
    }
