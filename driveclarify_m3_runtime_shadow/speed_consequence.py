"""Source-bounded SimLingo speed-consequence contract V0.

The SimLingo ``speed_wps`` output is supervised as ten future ego positions in
the current ego-local CARLA BEV frame.  This module exposes only the scalar
desired-speed semantic that the existing longitudinal PID computes from that
representation.  It deliberately does not infer stop/continue, acceleration,
stopping time, TTC, collision risk, or any safety consequence.

No model, controller, planner, simulator, or CUDA runtime is imported or
invoked here.  The contract is suitable only for offline/shadow diagnostics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Any

from driveclarify_m3_offline_replay.serialization import canonical_sha256


AVAILABLE = "AVAILABLE"
NOT_CURRENTLY_AVAILABLE = "NOT_CURRENTLY_AVAILABLE"
DIAGNOSTIC_USAGE = ("DIAGNOSTIC_ONLY", "LOGGING_ONLY")

VERIFIED_FROM_SOURCE_CONTRACT = "VERIFIED_FROM_SOURCE_CONTRACT"
SUPPORTED_BY_EXISTING_CONTROLLED_TEST = "SUPPORTED_BY_EXISTING_CONTROLLED_TEST"

SPEED_REPRESENTATION_SHAPE_INVALID = "SPEED_REPRESENTATION_SHAPE_INVALID"
SPEED_REPRESENTATION_NONFINITE = "SPEED_REPRESENTATION_NONFINITE"
SPEED_FRAME_UNVERIFIED = "SPEED_FRAME_UNVERIFIED"
SPEED_UNIT_UNVERIFIED = "SPEED_UNIT_UNVERIFIED"
SPEED_TEMPORAL_BASIS_UNVERIFIED = "SPEED_TEMPORAL_BASIS_UNVERIFIED"
SPEED_TASK_SEMANTIC_NOT_IDENTIFIABLE = "SPEED_TASK_SEMANTIC_NOT_IDENTIFIABLE"
SPEED_PID_DESIRED_SPEED_CONTRACT_VERIFIED = (
    "SPEED_PID_DESIRED_SPEED_CONTRACT_VERIFIED"
)


@dataclass(frozen=True)
class SpeedContractAssertionV0:
    """One machine-readable representation-contract assertion."""

    field: str
    value: Any
    evidence_level: str
    source_basis: tuple[str, ...]


@dataclass(frozen=True)
class SpeedRepresentationContractV0:
    """Verified metadata needed to interpret one candidate's speed payload."""

    schema_version: str
    representation_type: str
    model_output_shape: tuple[str | int, ...]
    candidate_payload_shape: tuple[int, int]
    dimension_semantics: tuple[str, str]
    frame: str | None
    unit: str | None
    query_time_offsets_seconds: tuple[float, ...] | None
    ordering: str | None
    accumulation: str
    normalization: str
    pid_start_query_index: int
    pid_end_query_index: int
    pid_interval_seconds: float | None
    pid_scale_per_second: float | None
    source_basis: tuple[str, ...]

    def contract_table(self) -> tuple[SpeedContractAssertionV0, ...]:
        common = self.source_basis
        return (
            SpeedContractAssertionV0(
                "shape",
                {
                    "model_output": self.model_output_shape,
                    "candidate_payload": self.candidate_payload_shape,
                },
                VERIFIED_FROM_SOURCE_CONTRACT,
                common,
            ),
            SpeedContractAssertionV0(
                "dimension_semantics",
                self.dimension_semantics,
                VERIFIED_FROM_SOURCE_CONTRACT,
                common,
            ),
            SpeedContractAssertionV0(
                "frame", self.frame, VERIFIED_FROM_SOURCE_CONTRACT, common
            ),
            SpeedContractAssertionV0(
                "unit", self.unit, VERIFIED_FROM_SOURCE_CONTRACT, common
            ),
            SpeedContractAssertionV0(
                "temporal_basis",
                self.query_time_offsets_seconds,
                VERIFIED_FROM_SOURCE_CONTRACT,
                common,
            ),
            SpeedContractAssertionV0(
                "ordering", self.ordering, VERIFIED_FROM_SOURCE_CONTRACT, common
            ),
            SpeedContractAssertionV0(
                "accumulation",
                self.accumulation,
                VERIFIED_FROM_SOURCE_CONTRACT,
                common,
            ),
            SpeedContractAssertionV0(
                "normalization",
                self.normalization,
                VERIFIED_FROM_SOURCE_CONTRACT,
                common,
            ),
            SpeedContractAssertionV0(
                "pid_usage",
                {
                    "start_query_index": self.pid_start_query_index,
                    "end_query_index": self.pid_end_query_index,
                    "interval_seconds": self.pid_interval_seconds,
                    "scale_per_second": self.pid_scale_per_second,
                    "formula": "euclidean_norm(q_start-q_end)*scale_per_second",
                },
                VERIFIED_FROM_SOURCE_CONTRACT,
                common,
            ),
        )


@dataclass(frozen=True)
class SpeedConsequenceEvidenceV0:
    """Fail-closed diagnostic result for one candidate speed representation."""

    status: str
    representation_type: str
    observed_shape: tuple[int, ...]
    frame: str | None
    unit: str | None
    temporal_basis: tuple[float, ...] | None
    semantic_type: str | None
    semantic_value: float | None
    source_digest: str
    evidence_basis: tuple[str, ...]
    allowed_usage_purposes: tuple[str, ...]
    authorization_eligible: bool
    safety_critical_eligible: bool
    reason_codes: tuple[str, ...]


SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0 = SpeedRepresentationContractV0(
    schema_version="driveclarify.simlingo_speed_representation_contract.v0",
    representation_type="ORDERED_EGO_LOCAL_PLANAR_FUTURE_POSITION_SEQUENCE",
    model_output_shape=("BATCH", 10, 2),
    candidate_payload_shape=(10, 2),
    dimension_semantics=("EGO_FORWARD", "EGO_RIGHT"),
    frame="CURRENT_EGO_LOCAL_CARLA_BEV_AT_PREDICTION_TIME",
    unit="METRE",
    query_time_offsets_seconds=(
        0.25,
        0.50,
        0.75,
        1.00,
        1.25,
        1.50,
        1.75,
        2.00,
        2.25,
        2.50,
    ),
    ordering="STRICTLY_INCREASING_FIXED_FUTURE_TIME",
    accumulation=(
        "LINEAR_HEAD_OUTPUTS_ARE_CUMSUMMED_ALONG_QUERY_DIMENSION_AND_"
        "SUPERVISED_AS_FUTURE_EGO_POSITIONS"
    ),
    normalization="NONE",
    pid_start_query_index=0,
    pid_end_query_index=2,
    pid_interval_seconds=0.5,
    pid_scale_per_second=2.0,
    source_basis=(
        "simlingo_training/dataloader/dataset_base.py:BaseDataset.load_current_and_future_measurements",
        "simlingo_training/dataloader/dataset_base.py:BaseDataset.load_waypoints",
        "simlingo_training/dataloader/dataset_base.py:BaseDataset.get_waypoints",
        "simlingo_training/dataloader/datamodule.py:DataModule.dl_collate_fn",
        "simlingo_training/models/adaptors/adaptors.py:DrivingAdaptor.compute_loss",
        "simlingo_training/models/adaptors/adaptors.py:DrivingAdaptor.get_predictions",
        "simlingo_training/models/driving.py:DrivingModel.forward",
        "leaderboard/leaderboard/leaderboard_evaluator.py:LeaderboardEvaluator._setup_simulation",
        "team_code/config.py:GlobalConfig.__init__",
        "team_code/data_agent.py:DataAgent.run_step",
        "team_code/autopilot.py:AutoPilot.save",
        "team_code/agent_simlingo.py:LingoAgent.control_pid",
    ),
)


def _observed_shape(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    if not value:
        return (0,)
    if all(isinstance(item, (list, tuple)) for item in value):
        widths = {len(item) for item in value}
        if len(widths) == 1:
            return (len(value), widths.pop())
    return (len(value),)


def _validated_points(
    value: Any, expected_shape: tuple[int, int]
) -> tuple[tuple[tuple[float, float], ...] | None, str | None]:
    if _observed_shape(value) != expected_shape:
        return None, SPEED_REPRESENTATION_SHAPE_INVALID
    points: list[tuple[float, float]] = []
    for point in value:
        converted: list[float] = []
        for component in point:
            if isinstance(component, bool) or not isinstance(component, Real):
                return None, SPEED_REPRESENTATION_SHAPE_INVALID
            number = float(component)
            if not math.isfinite(number):
                return None, SPEED_REPRESENTATION_NONFINITE
            converted.append(number)
        points.append((converted[0], converted[1]))
    return tuple(points), None


def _trace_digest(value: Any) -> str:
    """Preserve a deterministic trace even for rejected non-finite fixtures."""

    try:
        return canonical_sha256(value)
    except (TypeError, ValueError):
        return canonical_sha256(
            {
                "rejected_speed_representation_type": type(value).__name__,
                "rejected_speed_representation_repr": repr(value),
            }
        )


def _unavailable(
    representation: Any,
    contract: SpeedRepresentationContractV0,
    observed_shape: tuple[int, ...],
    reasons: tuple[str, ...],
) -> SpeedConsequenceEvidenceV0:
    return SpeedConsequenceEvidenceV0(
        status=NOT_CURRENTLY_AVAILABLE,
        representation_type=contract.representation_type,
        observed_shape=observed_shape,
        frame=contract.frame,
        unit=contract.unit,
        temporal_basis=contract.query_time_offsets_seconds,
        semantic_type=None,
        semantic_value=None,
        source_digest=_trace_digest(representation),
        evidence_basis=contract.source_basis,
        allowed_usage_purposes=DIAGNOSTIC_USAGE,
        authorization_eligible=False,
        safety_critical_eligible=False,
        reason_codes=tuple(
            dict.fromkeys((*reasons, SPEED_TASK_SEMANTIC_NOT_IDENTIFIABLE))
        ),
    )


def evaluate_pid_desired_speed_v0(
    representation: Any,
    *,
    contract: SpeedRepresentationContractV0 = SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0,
) -> SpeedConsequenceEvidenceV0:
    """Apply the existing PID desired-speed formula without invoking the PID.

    The input is the candidate payload after the runtime batch dimension has
    been removed.  Any missing physical metadata, malformed shape, or
    non-finite component returns structured unavailable evidence.
    """

    shape = _observed_shape(representation)
    reasons: list[str] = []
    if not contract.frame:
        reasons.append(SPEED_FRAME_UNVERIFIED)
    if contract.unit != "METRE":
        reasons.append(SPEED_UNIT_UNVERIFIED)
    offsets = contract.query_time_offsets_seconds
    start = contract.pid_start_query_index
    end = contract.pid_end_query_index
    interval = contract.pid_interval_seconds
    scale = contract.pid_scale_per_second
    if (
        offsets is None
        or len(offsets) != contract.candidate_payload_shape[0]
        or contract.ordering != "STRICTLY_INCREASING_FIXED_FUTURE_TIME"
        or any(right <= left for left, right in zip(offsets, offsets[1:]))
        or start < 0
        or end <= start
        or end >= len(offsets)
        or interval is None
        or interval <= 0.0
        or not math.isclose(offsets[end] - offsets[start], interval)
        or scale is None
        or not math.isclose(scale, 1.0 / interval)
    ):
        reasons.append(SPEED_TEMPORAL_BASIS_UNVERIFIED)

    points, point_error = _validated_points(
        representation, contract.candidate_payload_shape
    )
    if point_error:
        reasons.append(point_error)
    if reasons or points is None:
        return _unavailable(representation, contract, shape, tuple(reasons))

    delta_forward = points[start][0] - points[end][0]
    delta_right = points[start][1] - points[end][1]
    desired_speed_mps = math.hypot(delta_forward, delta_right) * scale
    return SpeedConsequenceEvidenceV0(
        status=AVAILABLE,
        representation_type=contract.representation_type,
        observed_shape=shape,
        frame=contract.frame,
        unit="METRE_PER_SECOND",
        temporal_basis=offsets,
        semantic_type="PID_DESIRED_SPEED_MPS",
        semantic_value=desired_speed_mps,
        source_digest=_trace_digest(representation),
        evidence_basis=(
            *contract.source_basis,
            SUPPORTED_BY_EXISTING_CONTROLLED_TEST,
        ),
        allowed_usage_purposes=DIAGNOSTIC_USAGE,
        authorization_eligible=False,
        safety_critical_eligible=False,
        reason_codes=(SPEED_PID_DESIRED_SPEED_CONTRACT_VERIFIED,),
    )


__all__ = [
    "AVAILABLE",
    "DIAGNOSTIC_USAGE",
    "NOT_CURRENTLY_AVAILABLE",
    "SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0",
    "SPEED_FRAME_UNVERIFIED",
    "SPEED_PID_DESIRED_SPEED_CONTRACT_VERIFIED",
    "SPEED_REPRESENTATION_NONFINITE",
    "SPEED_REPRESENTATION_SHAPE_INVALID",
    "SPEED_TASK_SEMANTIC_NOT_IDENTIFIABLE",
    "SPEED_TEMPORAL_BASIS_UNVERIFIED",
    "SPEED_UNIT_UNVERIFIED",
    "SUPPORTED_BY_EXISTING_CONTROLLED_TEST",
    "VERIFIED_FROM_SOURCE_CONTRACT",
    "SpeedConsequenceEvidenceV0",
    "SpeedContractAssertionV0",
    "SpeedRepresentationContractV0",
    "evaluate_pid_desired_speed_v0",
]
