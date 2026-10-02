"""Bounded mirror of SimLingo's existing STOP task evaluation contract.

SimLingo evaluates an Action Dreaming sample whose ``mode == "stop"`` by
computing speeds between every pair of adjacent predicted ``speed_wps`` and
declaring success when the minimum is strictly below 0.1 m/s.  The executable
source is ``simlingo_training/models/driving.py`` in
``DrivingModel.on_predict_epoch_end``.

This module mirrors that formula because importing the SimLingo training model
would load model-framework dependencies.  It does not use
``PID_DESIRED_SPEED_MPS`` and does not infer CONTINUE, physical safety, or
authorization.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
try:
    from enum import StrEnum
except ImportError:  # Python 3.8 in the frozen SimLingo environment.
    from enum import Enum

    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return self.value
from numbers import Real
from typing import Any

import numpy as np

from driveclarify_language.interaction_contracts import (
    LongitudinalTaskTarget,
    LongitudinalTaskTargetType,
)
from driveclarify_m3_offline_replay.serialization import canonical_sha256

from .speed_consequence import (
    SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0,
    SpeedRepresentationContractV0,
)


MIRROR_OF_EXISTING_SIMLINGO_STOP_EVALUATION_CONTRACT = (
    "MIRROR_OF_EXISTING_SIMLINGO_STOP_EVALUATION_CONTRACT"
)
DIAGNOSTIC_USAGE = ("DIAGNOSTIC_ONLY", "LOGGING_ONLY")

STOP_TASK_SATISFIED_BY_EXISTING_SIMLINGO_EVALUATION = (
    "STOP_TASK_SATISFIED_BY_EXISTING_SIMLINGO_EVALUATION"
)
STOP_TASK_CONTRADICTED_BY_EXISTING_SIMLINGO_EVALUATION = (
    "STOP_TASK_CONTRADICTED_BY_EXISTING_SIMLINGO_EVALUATION"
)
CONTINUE_TASK_SATISFACTION_UNRESOLVED = (
    "CONTINUE_TASK_SATISFACTION_UNRESOLVED"
)
LONGITUDINAL_TASK_TARGET_NOT_APPLICABLE = (
    "LONGITUDINAL_TASK_TARGET_NOT_APPLICABLE"
)
LONGITUDINAL_TASK_TARGET_UNSUPPORTED = "LONGITUDINAL_TASK_TARGET_UNSUPPORTED"
CANDIDATE_SPEED_WAYPOINTS_MISSING = "CANDIDATE_SPEED_WAYPOINTS_MISSING"
CANDIDATE_SPEED_WAYPOINTS_SHAPE_INVALID = (
    "CANDIDATE_SPEED_WAYPOINTS_SHAPE_INVALID"
)
CANDIDATE_SPEED_WAYPOINTS_NONFINITE = "CANDIDATE_SPEED_WAYPOINTS_NONFINITE"
STOP_REPRESENTATION_TYPE_UNVERIFIED = "STOP_REPRESENTATION_TYPE_UNVERIFIED"
STOP_REPRESENTATION_SHAPE_UNVERIFIED = "STOP_REPRESENTATION_SHAPE_UNVERIFIED"
STOP_REPRESENTATION_POINT_SEMANTICS_UNVERIFIED = (
    "STOP_REPRESENTATION_POINT_SEMANTICS_UNVERIFIED"
)
STOP_REPRESENTATION_FRAME_UNVERIFIED = "STOP_REPRESENTATION_FRAME_UNVERIFIED"
STOP_REPRESENTATION_UNIT_UNVERIFIED = "STOP_REPRESENTATION_UNIT_UNVERIFIED"
STOP_REPRESENTATION_TEMPORAL_BASIS_UNVERIFIED = (
    "STOP_REPRESENTATION_TEMPORAL_BASIS_UNVERIFIED"
)
STOP_REPRESENTATION_ORDERING_UNVERIFIED = (
    "STOP_REPRESENTATION_ORDERING_UNVERIFIED"
)
STOP_REPRESENTATION_PREPROCESSING_UNVERIFIED = (
    "STOP_REPRESENTATION_PREPROCESSING_UNVERIFIED"
)


class LongitudinalTaskSatisfactionOutcome(StrEnum):
    SATISFIED = "SATISFIED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class SimLingoStopEvaluationContractV0:
    """Immutable metadata copied exactly from the existing evaluator."""

    schema_version: str
    metric_name: str
    source: str
    source_tensor: str
    source_task_selector: str
    original_purpose: str
    waypoint_count: int
    waypoint_dimensions: int
    source_wp_freq: int
    source_carla_fps: int
    adjacent_interval_seconds: float
    success_threshold_mps: float
    comparison_operator: str
    mirror_designation: str


SIMLINGO_STOP_EVALUATION_CONTRACT_V0 = SimLingoStopEvaluationContractV0(
    schema_version="driveclarify.simlingo_stop_evaluation_contract.v0",
    metric_name="MINIMUM_PREDICTED_ADJACENT_WAYPOINT_SEGMENT_SPEED_MPS",
    source=(
        "simlingo_training/models/driving.py:"
        "DrivingModel.on_predict_epoch_end"
    ),
    source_tensor="self.prediction['waypoints'] / waypoints_preds_sample[i]",
    source_task_selector="eval_infos_sample[i]['mode'] == 'stop'",
    original_purpose="SIMLINGO_ACTION_DREAMING_STOP_INSTRUCTION_SUCCESS_RATE",
    waypoint_count=10,
    waypoint_dimensions=2,
    source_wp_freq=5,
    source_carla_fps=20,
    adjacent_interval_seconds=5 / 20,
    success_threshold_mps=0.1,
    comparison_operator="<",
    mirror_designation=MIRROR_OF_EXISTING_SIMLINGO_STOP_EVALUATION_CONTRACT,
)


@dataclass(frozen=True)
class LongitudinalTaskSatisfactionResultV0:
    """Diagnostic candidate-behavior x hypothesis-target STOP result."""

    candidate_id: str
    hypothesis_id: str
    target_type: str | None
    outcome: LongitudinalTaskSatisfactionOutcome
    observed_semantic_type: str | None
    observed_minimum_segment_speed_mps: float | None
    adjacent_segment_speeds_mps: tuple[float, ...]
    comparison_operator: str
    threshold_mps: float
    raw_speed_digest: str
    contract_source: str
    evidence_basis: tuple[str, ...]
    allowed_usage_purposes: tuple[str, ...]
    authorization_eligible: bool
    safety_critical_eligible: bool
    reason_codes: tuple[str, ...]
    schema_version: str = "driveclarify.longitudinal_task_satisfaction_result.v0"

    def __post_init__(self) -> None:
        if self.allowed_usage_purposes != DIAGNOSTIC_USAGE:
            raise ValueError("LONGITUDINAL_TASK_SATISFACTION_USAGE_INVALID")
        if self.authorization_eligible or self.safety_critical_eligible:
            raise ValueError(
                "LONGITUDINAL_TASK_SATISFACTION_CANNOT_AUTHORIZE_OR_CLAIM_SAFETY"
            )
        if not self.reason_codes or not self.contract_source or not self.evidence_basis:
            raise ValueError("LONGITUDINAL_TASK_SATISFACTION_TRACE_INCOMPLETE")
        if self.outcome in (
            LongitudinalTaskSatisfactionOutcome.SATISFIED,
            LongitudinalTaskSatisfactionOutcome.CONTRADICTED,
        ):
            value = self.observed_minimum_segment_speed_mps
            if (
                self.target_type != LongitudinalTaskTargetType.STOP.value
                or value is None
                or not math.isfinite(value)
                or len(self.adjacent_segment_speeds_mps) != 9
            ):
                raise ValueError("KNOWN_STOP_SATISFACTION_RESULT_INVALID")
        elif (
            self.observed_semantic_type is not None
            or self.observed_minimum_segment_speed_mps is not None
            or self.adjacent_segment_speeds_mps
        ):
            raise ValueError("UNKNOWN_STOP_SATISFACTION_RESULT_HAS_OBSERVATION")


def _trace_digest(value: Any) -> str:
    try:
        return canonical_sha256(value)
    except (TypeError, ValueError):
        return canonical_sha256(
            {
                "rejected_candidate_speed_type": type(value).__name__,
                "rejected_candidate_speed_repr": repr(value),
            }
        )


def _target_type(target: Any) -> str | None:
    if not isinstance(target, LongitudinalTaskTarget):
        return None
    value = target.target_type
    return value.value if isinstance(value, LongitudinalTaskTargetType) else str(value)


def _unknown_result(
    *,
    candidate_id: str,
    hypothesis_id: str,
    target_type: str | None,
    outcome: LongitudinalTaskSatisfactionOutcome,
    raw_speed: Any,
    reasons: tuple[str, ...],
) -> LongitudinalTaskSatisfactionResultV0:
    contract = SIMLINGO_STOP_EVALUATION_CONTRACT_V0
    return LongitudinalTaskSatisfactionResultV0(
        candidate_id=candidate_id,
        hypothesis_id=hypothesis_id,
        target_type=target_type,
        outcome=outcome,
        observed_semantic_type=None,
        observed_minimum_segment_speed_mps=None,
        adjacent_segment_speeds_mps=(),
        comparison_operator=contract.comparison_operator,
        threshold_mps=contract.success_threshold_mps,
        raw_speed_digest=_trace_digest(raw_speed),
        contract_source=contract.source,
        evidence_basis=(
            contract.mirror_designation,
            contract.original_purpose,
            "NO_PID_DESIRED_SPEED_THRESHOLD",
            "NO_PHYSICAL_SAFETY_INFERENCE",
        ),
        allowed_usage_purposes=DIAGNOSTIC_USAGE,
        authorization_eligible=False,
        safety_critical_eligible=False,
        reason_codes=reasons,
    )


def _representation_reasons(
    contract: SpeedRepresentationContractV0,
) -> tuple[str, ...]:
    expected = SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0
    reasons: list[str] = []
    if contract.representation_type != expected.representation_type:
        reasons.append(STOP_REPRESENTATION_TYPE_UNVERIFIED)
    if (
        contract.model_output_shape != expected.model_output_shape
        or contract.candidate_payload_shape != expected.candidate_payload_shape
    ):
        reasons.append(STOP_REPRESENTATION_SHAPE_UNVERIFIED)
    if contract.dimension_semantics != expected.dimension_semantics:
        reasons.append(STOP_REPRESENTATION_POINT_SEMANTICS_UNVERIFIED)
    if contract.frame != expected.frame:
        reasons.append(STOP_REPRESENTATION_FRAME_UNVERIFIED)
    if contract.unit != expected.unit:
        reasons.append(STOP_REPRESENTATION_UNIT_UNVERIFIED)
    if contract.query_time_offsets_seconds != expected.query_time_offsets_seconds:
        reasons.append(STOP_REPRESENTATION_TEMPORAL_BASIS_UNVERIFIED)
    if contract.ordering != expected.ordering:
        reasons.append(STOP_REPRESENTATION_ORDERING_UNVERIFIED)
    if (
        contract.accumulation != expected.accumulation
        or contract.normalization != expected.normalization
    ):
        reasons.append(STOP_REPRESENTATION_PREPROCESSING_UNVERIFIED)
    return tuple(reasons)


def _validated_points(
    value: Any,
) -> tuple[tuple[tuple[float, float], ...] | None, str | None]:
    if value is None:
        return None, CANDIDATE_SPEED_WAYPOINTS_MISSING
    contract = SIMLINGO_STOP_EVALUATION_CONTRACT_V0
    if not isinstance(value, (list, tuple)) or len(value) != contract.waypoint_count:
        return None, CANDIDATE_SPEED_WAYPOINTS_SHAPE_INVALID
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != contract.waypoint_dimensions:
            return None, CANDIDATE_SPEED_WAYPOINTS_SHAPE_INVALID
        converted: list[float] = []
        for component in point:
            if isinstance(component, bool) or not isinstance(component, Real):
                return None, CANDIDATE_SPEED_WAYPOINTS_SHAPE_INVALID
            number = float(component)
            if not math.isfinite(number):
                return None, CANDIDATE_SPEED_WAYPOINTS_NONFINITE
            converted.append(number)
        points.append((converted[0], converted[1]))
    return tuple(points), None


def _mirrored_adjacent_segment_speeds_mps(
    points: tuple[tuple[float, float], ...],
) -> tuple[float, ...]:
    """Mirror SimLingo's get_1d_wps -> diff -> interval normalization."""

    waypoints = np.asarray(points, dtype=float)
    one_dimensional = [
        np.linalg.norm(waypoints[index + 1] - waypoints[index])
        for index in range(len(waypoints) - 1)
    ]
    one_dimensional = np.cumsum(one_dimensional)
    one_dimensional = [[value, 0.0] for value in one_dimensional]
    one_dimensional = np.asarray([[0.0, 0.0], *one_dimensional], dtype=float)
    interval = SIMLINGO_STOP_EVALUATION_CONTRACT_V0.adjacent_interval_seconds
    return tuple(float(value) for value in np.diff(one_dimensional[:, 0]) / interval)


def evaluate_stop_task_satisfaction_v0(
    *,
    candidate_id: str,
    hypothesis_id: str,
    longitudinal_task_target: LongitudinalTaskTarget | None,
    candidate_speed_waypoints: Any,
    representation_contract: SpeedRepresentationContractV0 = (
        SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0
    ),
) -> LongitudinalTaskSatisfactionResultV0:
    """Evaluate one candidate trajectory under one hypothesis's STOP target."""

    target_type = _target_type(longitudinal_task_target)
    if longitudinal_task_target is None:
        return _unknown_result(
            candidate_id=candidate_id,
            hypothesis_id=hypothesis_id,
            target_type=None,
            outcome=LongitudinalTaskSatisfactionOutcome.NOT_APPLICABLE,
            raw_speed=candidate_speed_waypoints,
            reasons=(LONGITUDINAL_TASK_TARGET_NOT_APPLICABLE,),
        )
    if not isinstance(longitudinal_task_target, LongitudinalTaskTarget):
        return _unknown_result(
            candidate_id=candidate_id,
            hypothesis_id=hypothesis_id,
            target_type=None,
            outcome=LongitudinalTaskSatisfactionOutcome.UNKNOWN,
            raw_speed=candidate_speed_waypoints,
            reasons=(LONGITUDINAL_TASK_TARGET_UNSUPPORTED,),
        )
    if longitudinal_task_target.target_type is LongitudinalTaskTargetType.CONTINUE:
        return _unknown_result(
            candidate_id=candidate_id,
            hypothesis_id=hypothesis_id,
            target_type=target_type,
            outcome=LongitudinalTaskSatisfactionOutcome.UNKNOWN,
            raw_speed=candidate_speed_waypoints,
            reasons=(CONTINUE_TASK_SATISFACTION_UNRESOLVED,),
        )
    if longitudinal_task_target.target_type is not LongitudinalTaskTargetType.STOP:
        return _unknown_result(
            candidate_id=candidate_id,
            hypothesis_id=hypothesis_id,
            target_type=target_type,
            outcome=LongitudinalTaskSatisfactionOutcome.UNKNOWN,
            raw_speed=candidate_speed_waypoints,
            reasons=(LONGITUDINAL_TASK_TARGET_UNSUPPORTED,),
        )

    representation_reasons = _representation_reasons(representation_contract)
    if representation_reasons:
        return _unknown_result(
            candidate_id=candidate_id,
            hypothesis_id=hypothesis_id,
            target_type=target_type,
            outcome=LongitudinalTaskSatisfactionOutcome.UNKNOWN,
            raw_speed=candidate_speed_waypoints,
            reasons=representation_reasons,
        )

    points, invalid_reason = _validated_points(candidate_speed_waypoints)
    if points is None:
        return _unknown_result(
            candidate_id=candidate_id,
            hypothesis_id=hypothesis_id,
            target_type=target_type,
            outcome=LongitudinalTaskSatisfactionOutcome.UNKNOWN,
            raw_speed=candidate_speed_waypoints,
            reasons=(invalid_reason or CANDIDATE_SPEED_WAYPOINTS_SHAPE_INVALID,),
        )

    segment_speeds = _mirrored_adjacent_segment_speeds_mps(points)
    minimum_speed = min(segment_speeds)
    metric_contract = SIMLINGO_STOP_EVALUATION_CONTRACT_V0
    satisfied = minimum_speed < metric_contract.success_threshold_mps
    outcome = (
        LongitudinalTaskSatisfactionOutcome.SATISFIED
        if satisfied
        else LongitudinalTaskSatisfactionOutcome.CONTRADICTED
    )
    reason = (
        STOP_TASK_SATISFIED_BY_EXISTING_SIMLINGO_EVALUATION
        if satisfied
        else STOP_TASK_CONTRADICTED_BY_EXISTING_SIMLINGO_EVALUATION
    )
    return LongitudinalTaskSatisfactionResultV0(
        candidate_id=candidate_id,
        hypothesis_id=hypothesis_id,
        target_type=target_type,
        outcome=outcome,
        observed_semantic_type=metric_contract.metric_name,
        observed_minimum_segment_speed_mps=minimum_speed,
        adjacent_segment_speeds_mps=segment_speeds,
        comparison_operator=metric_contract.comparison_operator,
        threshold_mps=metric_contract.success_threshold_mps,
        raw_speed_digest=_trace_digest(candidate_speed_waypoints),
        contract_source=metric_contract.source,
        evidence_basis=(
            metric_contract.mirror_designation,
            metric_contract.original_purpose,
            metric_contract.source_task_selector,
            "PREDICTION_SPEED_WAYPOINTS_NOT_GROUND_TRUTH",
            "NO_PID_DESIRED_SPEED_THRESHOLD",
            "NO_PHYSICAL_SAFETY_INFERENCE",
        ),
        allowed_usage_purposes=DIAGNOSTIC_USAGE,
        authorization_eligible=False,
        safety_critical_eligible=False,
        reason_codes=(reason,),
    )


__all__ = [
    "CONTINUE_TASK_SATISFACTION_UNRESOLVED",
    "DIAGNOSTIC_USAGE",
    "LongitudinalTaskSatisfactionOutcome",
    "LongitudinalTaskSatisfactionResultV0",
    "MIRROR_OF_EXISTING_SIMLINGO_STOP_EVALUATION_CONTRACT",
    "SIMLINGO_STOP_EVALUATION_CONTRACT_V0",
    "SimLingoStopEvaluationContractV0",
    "evaluate_stop_task_satisfaction_v0",
]
