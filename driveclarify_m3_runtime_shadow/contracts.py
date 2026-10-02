"""Contracts for the shadow-only online candidate + M2B + M3 runtime harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from driveclarify_m3_shadow_bridge.contracts import ShadowBridgeRejection, ShadowTrace

PASS_SHADOW_ONLY_ONLINE_CANDIDATE_M2B_M3_PIPELINE_READY_FOR_BOUNDED_CARLA_SHADOW = (
    "PASS_SHADOW_ONLY_ONLINE_CANDIDATE_M2B_M3_PIPELINE_READY_FOR_BOUNDED_CARLA_SHADOW"
)
BLOCKED_SHADOW_CANDIDATE_EXECUTION_NOT_ISOLATABLE = (
    "BLOCKED_SHADOW_CANDIDATE_EXECUTION_NOT_ISOLATABLE"
)
BLOCKED_ONLINE_CANDIDATE_TO_M2B_INTERFACE_GAP = (
    "BLOCKED_ONLINE_CANDIDATE_TO_M2B_INTERFACE_GAP"
)


@dataclass(frozen=True)
class ShadowObservationSnapshot:
    """Baseline observation capture projected into a deterministic shadow input."""

    observation_id: str
    frame_id: int | None
    simulation_time: float | int
    speed: Any
    target_point: Any
    route_context: Mapping[str, Any]
    instruction: str
    model_input_snapshot: Mapping[str, Any]
    source_digest: str


@dataclass(frozen=True)
class ShadowCandidateResult:
    """One candidate forward output in shadow runtime."""

    candidate_id: str
    interpretation_id: str
    model_forward_sequence_id: str
    source_observation_id: str
    source_frame_id: int | str | None
    route: Any
    speed: Any
    language: tuple[str, ...]
    candidate_input_digest: str
    candidate_output_digest: str
    latency: float
    isolated: bool = True


@dataclass(frozen=True)
class DriveClarifyShadowDecisionResult:
    """Output bundle from the online shadow candidate + M2B + M3 path."""

    snapshot_id: str
    status: str
    candidate_results: tuple[ShadowCandidateResult, ...]
    candidate_set: tuple[str, ...]
    m2b_input: Mapping[str, Any]
    m2b_selected_action: str
    m2b_selected_candidate_id: str | None
    m2b_reason_codes: tuple[str, ...]
    m3_shadow_result: ShadowTrace | ShadowBridgeRejection | None
    forward_count: int
    control_write_count: int
    baseline_model_forward_count: int
    baseline_control_write_count: int
    shadow_candidate_forward_count: int
    block_reason: str | None = None

    @property
    def m3_trace(self) -> ShadowTrace:
        if isinstance(self.m3_shadow_result, ShadowTrace):
            return self.m3_shadow_result
        raise TypeError("m3_shadow_result is not a ShadowTrace")
