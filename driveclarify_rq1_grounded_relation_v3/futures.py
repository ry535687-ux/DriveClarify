"""候选未来轨迹：把冻结候选前向记录转成等时间网格轨迹。

坐标/单位/时域来源（全部由 SimLingo 生产代码而非本轮假设决定）：

- 速度头 `raw_speed` 形状 [1,10,2]，`speed_wps_mode: 2d`，是**等时间**二维路点。
  时间间隔由生产 PID 权威给出：team_code/agent_simlingo.py:1027
  `one_second = carla_fps // (wp_dilation * data_save_freq) = 20 // (1*5) = 4`
  即索引 4 对应 1.0 s ⇒ 每步 0.25 s，10 点 ⇒ 2.5 s 时域。
- 路线头 `plan_points` 形状 [1,20,2]，`predict_route_as_wps: true`，实测相邻弧长≈1.0 m，
  是**等距离**几何路径，不能当等时间点使用。

已知源码不一致：simlingo_training/dataloader/datamodule.py:314 注释写 "11 future waypoints
0.2s apart"，与生产 PID 的 0.25 s 不符。本轮采用生产 PID 值，因为被测系统实际按它行动；
该不一致如实记入 PROTOCOL.md，不静默择一。

坐标系：`plan_frame = EGO_LOCAL_X_FORWARD_Y_RIGHT`，单位 METRE。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .contracts import ContractError

SPEED_WAYPOINT_STEP_S = 0.25
SPEED_WAYPOINT_STEP_SOURCE = "team_code/agent_simlingo.py:1027 carla_fps//(wp_dilation*data_save_freq)=4_per_second"
COORDINATE_FRAME = "CARLA_EGO_X_FORWARD_Y_RIGHT_METRES"
PLAN_FRAME_EXPECTED = "EGO_LOCAL_X_FORWARD_Y_RIGHT"


def _points(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, Sequence) or not value:
        raise ContractError("PLAN_POINTS_EMPTY")
    rows = value[0] if isinstance(value[0], Sequence) and value[0] and isinstance(value[0][0], Sequence) else value
    points = []
    for row in rows:
        if not isinstance(row, Sequence) or len(row) != 2:
            raise ContractError("PLAN_POINT_SHAPE_INVALID")
        x, y = float(row[0]), float(row[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ContractError("PLAN_POINT_NONFINITE")
        points.append((x, y))
    return points


def candidate_future_from_plan(
    plan: Mapping[str, Any],
    *,
    nonlanguage_context_sha256: str,
) -> dict[str, Any]:
    """把一条冻结 plan 记录转成 trajectory.compare 可用的等时间轨迹。

    速度头缺失或非有限时返回 valid=False，由方法侧闭合为 UNKNOWN；绝不用等距离
    路线点冒充等时间点，也不把无效轨迹当作零分歧。
    """
    if plan.get("completion_status") != "COMPLETE":
        return {"valid": False, "reason_code": "CANDIDATE_FORWARD_NOT_COMPLETE"}
    if plan.get("plan_frame") != PLAN_FRAME_EXPECTED or plan.get("plan_unit") != "METRE":
        return {"valid": False, "reason_code": "PLAN_FRAME_OR_UNIT_UNEXPECTED"}
    checks = plan.get("finite_value_checks") or {}
    if any(bool(checks.get(key)) for key in ("speed_nan", "speed_inf", "route_nan", "route_inf")):
        return {"valid": False, "reason_code": "PLAN_NONFINITE_VALUES_REPORTED"}
    try:
        speed_points = _points(plan.get("raw_speed"))
    except ContractError as error:
        return {"valid": False, "reason_code": str(error)}
    times = [round((index + 1) * SPEED_WAYPOINT_STEP_S, 10) for index in range(len(speed_points))]
    # t=0 处自车在本体坐标原点；显式补上，使等时间网格从 0 开始。
    times = [0.0] + times
    points = [(0.0, 0.0)] + speed_points
    return {
        "valid": True,
        "relative_times_s": times,
        "xy_m": [list(point) for point in points],
        "coordinate_frame": COORDINATE_FRAME,
        "source_frame": plan.get("source_frame"),
        "source_time_s": float(plan.get("source_frame") or 0) / 20.0,
        "nonlanguage_context_sha256": nonlanguage_context_sha256,
        "horizon_s": times[-1],
        "step_s": SPEED_WAYPOINT_STEP_S,
        "step_source": SPEED_WAYPOINT_STEP_SOURCE,
        "equal_time_head": "raw_speed",
        "equal_distance_head_not_used_as_time": "plan_points",
        "candidate_id": plan.get("candidate_id"),
        "observation_hash": plan.get("observation_hash"),
        "checkpoint_sha256": plan.get("checkpoint_sha256"),
        "inference_latency_seconds": plan.get("inference_latency_seconds"),
        "route_plan_hash": plan.get("route_plan_hash"),
        "speed_plan_hash": plan.get("speed_plan_hash"),
    }


def geometric_path_from_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """等距离路线头，仅作几何路径描述与分歧诊断，不用于等时间比较。"""
    try:
        points = _points(plan.get("plan_points"))
    except ContractError as error:
        return {"valid": False, "reason_code": str(error)}
    arc = [0.0]
    for index in range(1, len(points)):
        arc.append(arc[-1] + math.dist(points[index - 1], points[index]))
    return {
        "valid": True,
        "xy_m": [list(point) for point in points],
        "arc_length_m": arc,
        "spacing_kind": "APPROXIMATELY_EQUAL_DISTANCE_NOT_EQUAL_TIME",
        "coordinate_frame": COORDINATE_FRAME,
    }
