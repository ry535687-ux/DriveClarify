"""Controlled probe JSONL schema 常量与只读 tensor 复制合同。

本模块不依赖 torch / numpy / CARLA。tensor 复制通过 duck-typing 调用
`detach().cpu().numpy().tolist()`，因此 offline 测试可用纯 Python stub 验证。
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "driveclarify.controlled_probe.v0.1"

# JSONL 顶层 section（顺序与 CONTROLLED_PROBE_PLAN §3 一致）。
TOP_LEVEL_SECTIONS = (
    "schema_version",
    "run_id",
    "record_seq",
    "observation_id",
    "frame",
    "clock",
    "sensor_frames",
    "observation",
    "model_output",
    "candidate_set",
    "m2b_decision",
    "ego",
    "actors",
    "traffic_controls",
    "map_waypoint",
    "route_context",
    "baseline_control",
    "visualization_fields",
    "quality",
)

# clock 域必须区分 simulation / monotonic / calendar。
SIMULATION_CLOCK_FIELDS = ("sim_elapsed_s", "game_time_s", "sim_delta_s")
MONOTONIC_CLOCK_FIELDS = (
    "probe_read_monotonic_s",
    "model_start_monotonic_s",
    "model_end_monotonic_s",
    "control_ready_monotonic_s",
)
# calendar 仅审计，不参与 expiry / latency。
CALENDAR_CLOCK_FIELDS = ("calendar_utc",)

# route/speed 静态 shape 预期（B=1 当前 agent）。
PRED_ROUTE_EXPECTED_RANK = 3       # [B, 20, 2]
PRED_ROUTE_EXPECTED_POINTS = 20
PRED_SPEED_EXPECTED_RANK = 3       # [B, 10, 2]
PRED_SPEED_EXPECTED_POINTS = 10
PRED_POINT_DIM = 2

# 单位在 controlled probe 通过前不得断言为 meter。
UNIT_STATUS_UNRESOLVED = "UNRESOLVED_REQUIRES_CONTROLLED_PROBE"

# baseline control 合法范围（用于 verifier 范围检查，不用于改写值）。
CONTROL_RANGES = {
    "steer": (-1.0, 1.0),
    "throttle": (0.0, 1.0),
    "brake": (0.0, 1.0),
}


class TensorCopyError(RuntimeError):
    """只读 tensor 复制失败；调用方必须 fail-open，不得回退到修改原 tensor。"""


def readonly_tensor_summary(
    tensor: Any,
    *,
    expected_points: int | None = None,
    expected_dim: int = PRED_POINT_DIM,
) -> dict[str, Any]:
    """用只读方式抽取 tensor 摘要。

    严格遵守合同：`tensor[0].detach().cpu().numpy().tolist()`。
    - 不做 in-place 操作；
    - 不访问 `.data`；
    - 不改变 dtype/device；
    - 不把 speed_wps 压成 scalar list。

    返回 shape/dtype/finite/values；任何异常转 TensorCopyError 由上层 fail-open。
    """

    if tensor is None:
        return {
            "present": False,
            "shape": None,
            "dtype": None,
            "finite": None,
            "values": None,
            "reason": "TENSOR_ABSENT",
        }
    try:
        shape = _safe_shape(tensor)
        dtype = _safe_dtype(tensor)
        # 只读副本：batch 0 -> detach -> cpu -> numpy -> list。
        batch0 = tensor[0]
        detached = batch0.detach() if hasattr(batch0, "detach") else batch0
        on_cpu = detached.cpu() if hasattr(detached, "cpu") else detached
        as_np = on_cpu.numpy() if hasattr(on_cpu, "numpy") else on_cpu
        values = as_np.tolist() if hasattr(as_np, "tolist") else list(as_np)
        finite = _all_finite(values)
        summary = {
            "present": True,
            "shape": shape,
            "dtype": dtype,
            "finite": finite,
            "values": values,
            "reason": None,
        }
        if expected_points is not None:
            summary["expected_points"] = expected_points
            summary["expected_dim"] = expected_dim
            summary["shape_matches_expected"] = _shape_matches(
                shape, expected_points, expected_dim
            )
        return summary
    except Exception as exc:  # noqa: BLE001 - probe must never raise into baseline
        raise TensorCopyError(str(exc)) from exc


def _safe_shape(tensor: Any) -> list[int] | None:
    shape = getattr(tensor, "shape", None)
    if shape is None:
        return None
    try:
        return [int(dim) for dim in shape]
    except TypeError:
        return None


def _safe_dtype(tensor: Any) -> str | None:
    dtype = getattr(tensor, "dtype", None)
    return None if dtype is None else str(dtype)


def _shape_matches(shape: list[int] | None, points: int, dim: int) -> bool:
    if not shape:
        return False
    # 期望 [B, points, dim]；B 任意（当前预期 1）。
    return len(shape) == 3 and shape[1] == points and shape[2] == dim


def _all_finite(values: Any) -> bool:
    import math

    def walk(v: Any) -> bool:
        if isinstance(v, (list, tuple)):
            return all(walk(x) for x in v)
        if isinstance(v, bool):
            return True
        if isinstance(v, (int, float)):
            return math.isfinite(float(v))
        return False

    return walk(values)


def empty_record_sections() -> dict[str, Any]:
    """构造带全部 section 的空记录骨架（值为 None / 空容器）。"""

    return {
        "frame": {},
        "clock": {},
        "sensor_frames": {},
        "observation": {},
        "model_output": {},
        "candidate_set": {},
        "m2b_decision": {},
        "ego": {},
        "actors": [],
        "traffic_controls": {},
        "map_waypoint": {},
        "route_context": {},
        "baseline_control": {},
        "visualization_fields": {},
        "quality": {},
    }
