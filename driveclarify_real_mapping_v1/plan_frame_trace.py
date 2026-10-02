"""Static, source-addressed SimLingo plan-frame evidence contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


SUPPORTED_PLAN_FRAME = "EGO_LOCAL_ACTOR_X_FORWARD_Y_RIGHT"
SUPPORTED_PLAN_UNIT = "METRE"


@dataclass(frozen=True)
class PlanFrameTrace:
    raw_route_shape: tuple[str, int, int]
    raw_speed_waypoint_shape: tuple[str, int, int]
    plan_frame: str
    plan_unit: str
    origin: str
    orientation: str
    route_and_speed_share_frame: bool
    output_postprocess: tuple[str, ...]
    source_evidence: tuple[str, ...]
    runtime_verification_status: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("raw_route_shape", "raw_speed_waypoint_shape", "output_postprocess", "source_evidence", "reason_codes"):
            value[key] = list(value[key])
        return value


def simlingo_static_plan_frame_trace() -> PlanFrameTrace:
    return PlanFrameTrace(
        raw_route_shape=("B", 20, 2),
        raw_speed_waypoint_shape=("B", 10, 2),
        plan_frame=SUPPORTED_PLAN_FRAME,
        plan_unit=SUPPORTED_PLAN_UNIT,
        origin="EGO_ACTOR_TRANSFORM_AT_SOURCE_OBSERVATION",
        orientation="PLUS_X_FORWARD_PLUS_Y_RIGHT_USING_PREPROCESSED_IMU_COMPASS_CARLA_YAW",
        route_and_speed_share_frame=True,
        output_postprocess=(
            "LINEAR_HEAD_PREDICTS_2D_DELTAS",
            "CUMSUM_ALONG_WAYPOINT_AXIS",
            "FLOAT_CAST_ONLY_IN_AGENT",
            "NO_SCALE_OR_NORMALIZATION_AFTER_HEAD",
        ),
        source_evidence=(
            "simlingo_training/models/adaptors/adaptors.py:110-136,163-180",
            "simlingo_training/models/adaptors/adaptors.py:189-209",
            "simlingo_training/dataloader/dataset_base.py:392-415,785-811",
            "simlingo_training/dataloader/dataset_base.py:419-440,680-687",
            "Bench2Drive/leaderboard/team_code/autopilot.py:288-319,947-970,977-990",
            "team_code/transfuser_utils.py:147-160",
            "team_code/agent_simlingo.py:702-707,786,828-852",
            "team_code/transfuser_utils.py:133-144,369-376",
            "outputs/simlingo/.hydra/config.yaml:20-21",
        ),
        runtime_verification_status="NOT_YET_VERIFIED_FROM_CONTROLLED_M3B_RUNTIME",
        reason_codes=(
            "SUPPORTED_FROM_SIMLINGO_SOURCE",
            "METRE_UNIT_INHERITED_FROM_CARLA_TRANSFORM_TRANSLATIONS",
            "ROUTE_ORIGIN_FROM_EGO_ACTOR_LOCATION_AND_ORIENTATION_FROM_PREPROCESSED_IMU_COMPASS",
            "SPEED_WAYPOINTS_USE_EGO_ACTOR_MATRIX_AND_SHARE_THE_NOMINAL_EGO_AXES",
            "RUNTIME_SHAPE_VALUE_AND_OBSERVATION_LINKAGE_STILL_TO_CAPTURE",
        ),
    )


def validate_plan_frame_trace(trace: Mapping[str, Any]) -> tuple[str, ...]:
    reasons: list[str] = []
    if trace.get("plan_frame") != SUPPORTED_PLAN_FRAME:
        reasons.append("UNSUPPORTED_PLAN_FRAME")
    if trace.get("plan_unit") != SUPPORTED_PLAN_UNIT:
        reasons.append("UNSUPPORTED_PLAN_UNIT")
    if trace.get("origin") != "EGO_ACTOR_TRANSFORM_AT_SOURCE_OBSERVATION":
        reasons.append("PLAN_ORIGIN_UNSUPPORTED")
    if trace.get("route_and_speed_share_frame") is not True:
        reasons.append("ROUTE_SPEED_FRAME_LINKAGE_UNSUPPORTED")
    if not trace.get("source_evidence"):
        reasons.append("PLAN_FRAME_SOURCE_TRACE_MISSING")
    return tuple(reasons)
