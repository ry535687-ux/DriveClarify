"""Torch-free consequence -> existing M2B -> M3 shadow postprocessor.

The live SimLingo process necessarily has PyTorch loaded.  The existing M2B
binding deliberately rejects that execution context, so this module is run by
the project Python in a separate process after candidate inference completes.
It accepts JSON only and has no CARLA, model, planner, PID, or control object.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from driveclarify_language.interaction_contracts import (
    BindingStatus,
    CandidateSpecificTaskBinding,
    LongitudinalTaskTarget,
    LongitudinalTaskTargetType,
)
from driveclarify_m3_runtime_shadow.contracts import (
    ShadowCandidateResult,
    ShadowObservationSnapshot,
)
from driveclarify_m3_runtime_shadow.counterfactual_evidence import (
    ShadowCounterfactualMappingContext,
    bind_candidate_to_counterfactual_evidence,
    build_route_stop_counterfactual_outcome_matrix_v0,
)
from driveclarify_m3_runtime_shadow.m2b_binding import bind_candidates_to_m2b
from driveclarify_m3_runtime_shadow.m2b_to_m3_translation import (
    PASS_EXPLICIT_M2B_TO_M3_SHADOW_TRANSLATION_BOUND_TO_EXISTING_BRIDGE,
    translate_m2b_decision_to_m3_shadow_input,
)
from driveclarify_m3_shadow_bridge.bridge import M3ShadowBridge
from driveclarify_m3_shadow_bridge.contracts import ShadowTrace
from driveclarify_static_branch.mapper import VERIFIED_PLAN_FRAME, VERIFIED_PLAN_UNIT
from driveclarify_static_branch.topology import (
    TOPOLOGY_SCHEMA,
    deterministic_sha256,
    with_sha256,
)


POSTPROCESS_SCHEMA = "driveclarify.live_shadow_postprocess.v0"
PASS_POSTPROCESS = "PASS_EXISTING_CONSEQUENCE_M2B_TRANSLATION_M3_SHADOW_ISOLATED"
CONTROL_BOUNDARY = "BLOCKED_SHADOW_OUTPUT_REACHED_VEHICLE_CONTROL"


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _longitudinal_target(source_maneuver: str) -> LongitudinalTaskTarget:
    return LongitudinalTaskTarget(
        target_type=LongitudinalTaskTargetType.STOP,
        source_maneuver=source_maneuver,
        source_instruction_id="LIVE_FIXED_AMBIGUITY_FIXTURE_STOP_BRANCH_V0",
        binding_source="CANONICAL_INTENT_MANEUVER",
        provenance=("LIVE_FIXED_AMBIGUITY_FIXTURE_V0",),
    )


def _mapping_context(snapshot: ShadowObservationSnapshot) -> ShadowCounterfactualMappingContext:
    topology = with_sha256(
        {
            "schema_version": TOPOLOGY_SCHEMA,
            "branches": [
                {
                    "semantic_role": "STRAIGHT_BRANCH",
                    "evaluation_polyline_world_xyz": [
                        [0.0, 0.0, 0.0],
                        [10.0, 0.0, 0.0],
                        [20.0, 0.0, 0.0],
                    ],
                },
                {
                    "semantic_role": "RIGHT_TURN_BRANCH",
                    "evaluation_polyline_world_xyz": [
                        [0.0, 0.0, 0.0],
                        [0.0, 10.0, 0.0],
                        [0.0, 20.0, 0.0],
                    ],
                },
            ],
        }
    )
    thresholds: dict[str, Any] = {
        "schema_version": "driveclarify.mapping_threshold_provenance.v1",
        "distance_threshold_m": 1.75,
        "alignment_threshold_cosine": 0.7,
        "branch_score_margin": 0.1,
        "lane_width_reference_m": 3.5,
        "topology_separation_m": 1.75,
        "numerical_tolerance": 1e-9,
        "tail_point_count": 3,
    }
    thresholds["sha256"] = deterministic_sha256(thresholds)
    bindings = {
        "A": CandidateSpecificTaskBinding(
            source_candidate_id="candidate_A",
            task_family="CANONICAL_BRANCH_TASK",
            symbolic_target_type="BRANCH_TASK_EQUIVALENCE_CLASS",
            symbolic_target_id="STRAIGHT_TASK_CLASS",
            required_slots=("canonical_branch_task",),
            binding_status=BindingStatus.BOUND,
            binding_source="LIVE_FIXED_AMBIGUITY_FIXTURE_V0",
            frame_or_semantic_domain="TASK_BINDING",
            provenance=("LIVE_FIXED_AMBIGUITY_FIXTURE_V0",),
            reason_codes=("CANDIDATE_SPECIFIC_BINDING_BOUND",),
            longitudinal_task_target=_longitudinal_target("STRAIGHT_THEN_STOP"),
        ),
        "B": CandidateSpecificTaskBinding(
            source_candidate_id="candidate_B",
            task_family="CANONICAL_BRANCH_TASK",
            symbolic_target_type="BRANCH_TASK_EQUIVALENCE_CLASS",
            symbolic_target_id="RIGHT_TASK_CLASS",
            required_slots=("canonical_branch_task",),
            binding_status=BindingStatus.BOUND,
            binding_source="LIVE_FIXED_AMBIGUITY_FIXTURE_V0",
            frame_or_semantic_domain="TASK_BINDING",
            provenance=("LIVE_FIXED_AMBIGUITY_FIXTURE_V0",),
            reason_codes=("CANDIDATE_SPECIFIC_BINDING_BOUND",),
            longitudinal_task_target=_longitudinal_target("RIGHT_THEN_STOP"),
        ),
    }
    return ShadowCounterfactualMappingContext(
        source_observation_id=snapshot.observation_id,
        source_frame_id=snapshot.frame_id,
        frozen_topology=topology,
        mapping_threshold_contract=thresholds,
        ego_transform={
            "source_frame": "CARLA_WORLD",
            "target_frame": VERIFIED_PLAN_FRAME,
            "location_xy_world_m": [0.0, 0.0],
            "yaw_degrees": 0.0,
            "evidence_status": "VERIFIED",
        },
        plan_frame=VERIFIED_PLAN_FRAME,
        plan_unit=VERIFIED_PLAN_UNIT,
        frame_evidence_status="VERIFIED",
        unit_evidence_status="VERIFIED",
        branch_task_equivalence_classes={
            "STRAIGHT_BRANCH": "STRAIGHT_TASK_CLASS",
            "RIGHT_TURN_BRANCH": "RIGHT_TASK_CLASS",
        },
        interpretation_task_bindings=bindings,
        mapping_provenance=("LIVE_FIXED_AMBIGUITY_FIXTURE_V0",),
        source_artifacts=(
            "LIVE_FIXED_EGO_LOCAL_BRANCH_TOPOLOGY_V0",
            "VERIFIED_EGO_LOCAL_X_FORWARD_Y_RIGHT_METRE_CONTRACT",
        ),
    )


def _matrix_stop(matrix: Mapping[str, Any], candidate_id: str) -> str:
    for cell in matrix.get("cells", ()):
        if (
            cell.get("action_candidate_id") == candidate_id
            and cell.get("hypothesis_candidate_id") == candidate_id
        ):
            return str(cell.get("longitudinal_task_outcome") or "NOT_APPLICABLE")
    return "UNKNOWN"


def _candidate(value: Mapping[str, Any]) -> ShadowCandidateResult:
    return ShadowCandidateResult(
        candidate_id=str(value["candidate_id"]),
        interpretation_id=str(value["interpretation_id"]),
        model_forward_sequence_id=str(value["model_forward_sequence_id"]),
        source_observation_id=str(value["source_observation_id"]),
        source_frame_id=int(value["source_frame_id"]),
        route=tuple(tuple(float(axis) for axis in point) for point in value["route"]),
        speed=tuple(tuple(float(axis) for axis in point) for point in value["speed"]),
        language=tuple(str(item) for item in value.get("language", ())),
        candidate_input_digest=str(value["candidate_input_digest"]),
        candidate_output_digest=str(value["candidate_output_digest"]),
        latency=float(value["latency"]),
    )


def process_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run only the existing post-inference APIs in a torch-free process."""

    if "torch" in sys.modules:
        raise RuntimeError("LIVE_SHADOW_POSTPROCESS_TORCH_PRELOADED")
    snapshot_value = payload["snapshot"]
    snapshot = ShadowObservationSnapshot(
        observation_id=str(snapshot_value["observation_id"]),
        frame_id=int(snapshot_value["frame_id"]),
        simulation_time=float(snapshot_value["simulation_time"]),
        speed=tuple(snapshot_value["speed"]),
        target_point=dict(snapshot_value["target_point"]),
        route_context=dict(snapshot_value["route_context"]),
        instruction=str(snapshot_value["instruction"]),
        model_input_snapshot=dict(snapshot_value["model_input_snapshot"]),
        source_digest=str(snapshot_value["source_digest"]),
    )
    candidates = tuple(_candidate(value) for value in payload["candidates"])
    if len(candidates) != 2:
        raise RuntimeError("LIVE_SHADOW_POSTPROCESS_REQUIRES_TWO_CANDIDATES")
    if any(
        candidate.source_observation_id != snapshot.observation_id
        or candidate.source_frame_id != snapshot.frame_id
        for candidate in candidates
    ):
        raise RuntimeError("BLOCKED_LIVE_SHADOW_CANDIDATE_SOURCE_IDENTITY_MISMATCH")

    context = _mapping_context(snapshot)
    consequence_started = time.monotonic_ns()
    evidence = tuple(
        bind_candidate_to_counterfactual_evidence(candidate, context)
        for candidate in candidates
    )
    matrix = build_route_stop_counterfactual_outcome_matrix_v0(
        tuple(candidate.candidate_id for candidate in candidates),
        {item.candidate_id: item.counterfactual_plan_evidence for item in evidence},
        {item.candidate_id: item.interpretation_task_binding for item in evidence},
        {item.candidate_id: 1.0 for item in evidence},
        {candidate.candidate_id: candidate.speed for candidate in candidates},
        provenance=("LIVE_CARLA_SHADOW_V0", "DIAGNOSTIC_ONLY"),
    )
    matrix_dict = matrix.to_dict()
    consequence_end = time.monotonic_ns()

    m2b_started = time.monotonic_ns()
    m2b = bind_candidates_to_m2b(
        snapshot,
        candidates[0],
        candidates[1],
        mapping_context=context,
    )
    m2b_end = time.monotonic_ns()
    translation_started = time.monotonic_ns()
    translation = translate_m2b_decision_to_m3_shadow_input(m2b)
    translation_end = time.monotonic_ns()
    bridge_started = time.monotonic_ns()
    trace = M3ShadowBridge().run(translation.shadow_input)
    bridge_end = time.monotonic_ns()
    if not isinstance(trace, ShadowTrace):
        raise RuntimeError("EXISTING_M3_SHADOW_BRIDGE_REJECTED_LIVE_INPUT")
    deltas = {
        "m3_control_write_delta": trace.m3_control_write_delta,
        "low_level_output_count": trace.low_level_output_count,
        "model_forward_delta": trace.model_forward_delta,
        "planner_call_delta": trace.planner_call_delta,
        "pid_call_delta": trace.pid_call_delta,
    }
    if any(deltas.values()):
        raise RuntimeError(CONTROL_BOUNDARY)
    if "torch" in sys.modules:
        raise RuntimeError("LIVE_SHADOW_POSTPROCESS_IMPORTED_TORCH")

    final_state = dict(trace.final_state)
    lease = final_state.get("holding_lease")
    candidate_annotations = []
    for candidate, item in zip(candidates, evidence):
        candidate_annotations.append(
            {
                "candidate_id": candidate.candidate_id,
                "route_semantic": item.route.mapped_branch or "UNKNOWN",
                "stop_status": _matrix_stop(matrix_dict, candidate.candidate_id),
                "pid_desired_speed_mps": item.speed.semantic_value,
                "evidence": item.to_dict(),
            }
        )
    return {
        "schema_version": POSTPROCESS_SCHEMA,
        "status": PASS_POSTPROCESS,
        "execution_isolation": {
            "torch_loaded_before": False,
            "torch_loaded_after": False,
            "model_objects_received": False,
            "carla_objects_received": False,
            "control_objects_received": False,
        },
        "candidate_set_id": m2b.candidate_set_id,
        "candidate_annotations": candidate_annotations,
        "counterfactual_matrix": matrix_dict,
        "m2b": {
            "producer_action": m2b.producer_action,
            "selected_candidate_id": m2b.selected_candidate_id,
            "reason_codes": list(m2b.reason_codes),
            "counterfactual_matrix_status": m2b.counterfactual_matrix_status,
            "used_for_control": m2b.used_for_control,
            "control_write_count": m2b.control_write_count,
            "m2b_latency_ns": m2b.m2b_latency_ns,
        },
        "m3": {
            "integration_status": PASS_EXPLICIT_M2B_TO_M3_SHADOW_TRANSLATION_BOUND_TO_EXISTING_BRIDGE,
            "translated_semantic": translation.action_translation.lifecycle_action,
            "lifecycle_action": translation.action_translation.lifecycle_action,
            "transition_id": trace.transition_ids[0] if trace.transition_ids else None,
            "current_state": final_state.get("lifecycle_state"),
            "query_active": final_state.get("query_active"),
            "lease": "PRESENT" if lease else "ABSENT",
            "authority": final_state.get("authority"),
            "control_writes": trace.m3_control_write_delta,
            "trace_deltas": deltas,
        },
        "performance": {
            "consequence_ms": round((consequence_end - consequence_started) / 1_000_000.0, 3),
            "m2b_binding_total_ms": round((m2b_end - m2b_started) / 1_000_000.0, 3),
            "m2b_ms": round(m2b.m2b_latency_ns / 1_000_000.0, 3),
            "translation_ms": round((translation_end - translation_started) / 1_000_000.0, 3),
            "m3_bridge_ms": round((bridge_end - bridge_started) / 1_000_000.0, 3),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    with args.input.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    _atomic_json(args.output, process_payload(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
