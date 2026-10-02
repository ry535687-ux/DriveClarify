"""从 P0/P1/P2 只读观测构造 JSONL 记录。

核心原则：缺失字段写 null 并在 quality.unknown_fields 记录明确 reason，
绝不静默填 0 / false / SAFE / Green。simulation clock 与 monotonic clock
是不同字段，互相不得冒充。
"""

from __future__ import annotations

import time
from typing import Any

from . import schema
from .schema import (
    PRED_ROUTE_EXPECTED_POINTS,
    PRED_SPEED_EXPECTED_POINTS,
    SCHEMA_VERSION,
    TensorCopyError,
    UNIT_STATUS_UNRESOLVED,
    readonly_tensor_summary,
)


class RecordBuilder:
    """累积一条 tick 记录；提供 null+reason 的显式 UNKNOWN 语义。"""

    def __init__(self, run_id: str, record_seq: int) -> None:
        self._data: dict[str, Any] = schema.empty_record_sections()
        self._data["schema_version"] = SCHEMA_VERSION
        self._data["run_id"] = run_id
        self._data["record_seq"] = record_seq
        self._data["observation_id"] = None
        self._unknown: list[dict[str, str]] = []

    def unknown(self, path: str, reason: str) -> None:
        """登记一个 null 字段及其 reason；verifier 会校验 null 必有 reason。"""

        self._unknown.append({"path": path, "reason": reason})

    def set_observation_id(self, observation_id: str | None, reason: str | None = None) -> None:
        self._data["observation_id"] = observation_id
        if observation_id is None:
            self.unknown("observation_id", reason or "OBSERVATION_ID_MISSING")

    def section(self, name: str) -> dict[str, Any]:
        return self._data[name]

    def build(self, quality_extra: dict[str, Any] | None = None) -> dict[str, Any]:
        quality = self._data["quality"]
        quality["unknown_fields"] = self._unknown
        quality.setdefault("frame_join_status", "UNKNOWN")
        quality.setdefault("clock_join_status", "UNKNOWN")
        if quality_extra:
            quality.update(quality_extra)
        return self._data


def build_record(
    *,
    run_id: str,
    record_seq: int,
    p0_tick: dict[str, Any] | None,
    p1_model: dict[str, Any] | None,
    p2_control: dict[str, Any] | None,
    candidate_set: dict[str, Any] | None = None,
    m2b_decision: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    clock_now_monotonic: float | None = None,
) -> dict[str, Any]:
    """把三个边界的只读观测汇成一条记录。

    p0_tick / p1_model / p2_control 均为 baseline 已产生值的浅层字典副本；
    本函数不执行 tick / model / PID / planner。
    """

    builder = RecordBuilder(run_id, record_seq)

    _fill_frame_clock(builder, p0_tick, clock_now_monotonic)
    _fill_observation(builder, p0_tick)
    _fill_model_output(builder, p1_model, p0_tick)
    _fill_candidate_set(builder, candidate_set)
    _fill_m2b_decision(builder, m2b_decision)
    _fill_baseline_control(builder, p2_control)
    _fill_provenance(builder, provenance)

    # observation_id 优先来自 p0；否则显式 UNKNOWN。
    obs_id = None
    if p0_tick is not None:
        obs_id = p0_tick.get("observation_id")
    builder.set_observation_id(obs_id, "OBSERVATION_ID_NOT_PROVIDED_BY_TICK")

    frame_join = _frame_join_status(p0_tick)
    clock_join = _clock_join_status(builder.section("clock"))
    return builder.build(
        {
            "frame_join_status": frame_join,
            "clock_join_status": clock_join,
            "unit_status": UNIT_STATUS_UNRESOLVED,
        }
    )


def _fill_frame_clock(
    builder: RecordBuilder,
    p0: dict[str, Any] | None,
    now_monotonic: float | None,
) -> None:
    frame = builder.section("frame")
    clock = builder.section("clock")

    # monotonic：probe 读取时刻。总是可测（真实运行时）。
    clock["probe_read_monotonic_s"] = (
        float(now_monotonic) if now_monotonic is not None else _monotonic()
    )

    if p0 is None:
        for f in ("carla_snapshot_frame", "game_time_frame"):
            frame[f] = None
            builder.unknown(f"frame.{f}", "TICK_NOT_OBSERVED")
        for f in schema.SIMULATION_CLOCK_FIELDS:
            clock[f] = None
            builder.unknown(f"clock.{f}", "TICK_NOT_OBSERVED")
        clock["calendar_utc"] = _calendar_utc()
        return

    # frame ids（simulation domain）
    frame["carla_snapshot_frame"] = _get_or_unknown(
        builder, p0, "carla_snapshot_frame", "frame.carla_snapshot_frame"
    )
    frame["game_time_frame"] = _get_or_unknown(
        builder, p0, "game_time_frame", "frame.game_time_frame"
    )

    # simulation clock（不得用 monotonic 填充）
    for f in schema.SIMULATION_CLOCK_FIELDS:
        clock[f] = _get_or_unknown(builder, p0, f, f"clock.{f}")

    # sensor frames
    sensor_frames = builder.section("sensor_frames")
    provided = p0.get("sensor_frames")
    if isinstance(provided, dict):
        sensor_frames.update(provided)
    else:
        builder.unknown("sensor_frames", "SENSOR_FRAMES_NOT_OBSERVED")

    clock["calendar_utc"] = _calendar_utc()


def _fill_observation(builder: RecordBuilder, p0: dict[str, Any] | None) -> None:
    obs = builder.section("observation")
    if p0 is None:
        builder.unknown("observation", "TICK_NOT_OBSERVED")
        return
    obs_src = p0.get("observation")
    if isinstance(obs_src, dict):
        obs.update(obs_src)
    else:
        # 允许 p0 直接携带若干标量（speed 等）；无则 UNKNOWN。
        for key in ("speed", "target_point_ego", "high_level_command"):
            if key in p0:
                obs[key] = p0[key]
        if not obs:
            builder.unknown("observation", "OBSERVATION_FIELDS_NOT_PROVIDED")


def _fill_model_output(
    builder: RecordBuilder,
    p1: dict[str, Any] | None,
    p0: dict[str, Any] | None,
) -> None:
    mo = builder.section("model_output")
    if p1 is None:
        builder.unknown("model_output", "MODEL_OUTPUT_NOT_OBSERVED")
        mo["pred_route"] = None
        mo["pred_speed_wps"] = None
        return

    # route / speed 只读 tensor 摘要（fail-open）。
    mo["pred_route"] = _tensor_or_unknown(
        builder, p1.get("pred_route"), "model_output.pred_route",
        PRED_ROUTE_EXPECTED_POINTS,
    )
    mo["pred_speed_wps"] = _tensor_or_unknown(
        builder, p1.get("pred_speed_wps"), "model_output.pred_speed_wps",
        PRED_SPEED_EXPECTED_POINTS,
    )

    mo["source_observation_id"] = p1.get("source_observation_id")
    if mo["source_observation_id"] is None and p0 is not None:
        mo["source_observation_id"] = p0.get("observation_id")
    if mo["source_observation_id"] is None:
        builder.unknown("model_output.source_observation_id", "SOURCE_OBS_ID_MISSING")

    # 这两个字段表达"同一次 forward 且被 baseline PID 使用"，由 hook 传入事实值。
    mo["generated_from_same_forward"] = p1.get("generated_from_same_forward")
    if mo["generated_from_same_forward"] is None:
        builder.unknown(
            "model_output.generated_from_same_forward", "SAME_FORWARD_FLAG_NOT_SET"
        )
    mo["used_by_baseline_pid"] = p1.get("used_by_baseline_pid")
    if mo["used_by_baseline_pid"] is None:
        builder.unknown(
            "model_output.used_by_baseline_pid", "USED_BY_PID_FLAG_NOT_SET"
        )
    mo["unit_status"] = UNIT_STATUS_UNRESOLVED

    # model latency（monotonic domain），由 hook 显式提供。
    clock = builder.section("clock")
    for f in ("model_start_monotonic_s", "model_end_monotonic_s"):
        val = p1.get(f)
        clock[f] = val
        if val is None:
            builder.unknown(f"clock.{f}", "MODEL_TIMESTAMP_NOT_SET")


def _fill_candidate_set(builder: RecordBuilder, p3: dict[str, Any] | None) -> None:
    section = builder.section("candidate_set")
    if p3 is None:
        section["present"] = False
        builder.unknown("candidate_set", "CANDIDATE_SET_NOT_OBSERVED")
        return
    if isinstance(p3, dict):
        section.update(p3)
        section.setdefault("present", True)
        return
    section["present"] = False
    section["type"] = str(type(p3).__name__)
    builder.unknown("candidate_set", "CANDIDATE_SET_NOT_DICT")


def _fill_baseline_control(builder: RecordBuilder, p2: dict[str, Any] | None) -> None:
    bc = builder.section("baseline_control")
    clock = builder.section("clock")
    if p2 is None:
        builder.unknown("baseline_control", "CONTROL_NOT_OBSERVED")
        clock["control_ready_monotonic_s"] = None
        builder.unknown("clock.control_ready_monotonic_s", "CONTROL_NOT_OBSERVED")
        return
    for field_name in ("steer", "throttle", "brake", "hand_brake", "reverse", "manual_gear_shift"):
        if field_name in p2:
            bc[field_name] = p2[field_name]
        else:
            bc[field_name] = None
            builder.unknown(f"baseline_control.{field_name}", "CONTROL_FIELD_MISSING")
    # 只读记录 override / stuck 标志（若 hook 提供）。
    for opt in ("initial_frame_override", "stuck_recovery", "desired_speed", "gt_velocity"):
        if opt in p2:
            bc[opt] = p2[opt]
    clock["control_ready_monotonic_s"] = p2.get("control_ready_monotonic_s")
    if clock["control_ready_monotonic_s"] is None:
        builder.unknown("clock.control_ready_monotonic_s", "CONTROL_READY_TS_NOT_SET")


def _fill_m2b_decision(builder: RecordBuilder, p4: dict[str, Any] | None) -> None:
    section = builder.section("m2b_decision")
    if p4 is None:
        section["present"] = False
        builder.unknown("m2b_decision", "M2B_DECISION_NOT_OBSERVED")
        return
    if isinstance(p4, dict):
        section.update(p4)
        section.setdefault("present", True)
        return
    section["present"] = False
    section["type"] = str(type(p4).__name__)
    builder.unknown("m2b_decision", "M2B_DECISION_NOT_DICT")


def _fill_provenance(builder: RecordBuilder, provenance: dict[str, Any] | None) -> None:
    quality = builder.section("quality")
    quality["provenance"] = provenance or {}
    for key in ("code_commit", "config_hash", "checkpoint_hash", "worktree_dirty_summary"):
        if not provenance or key not in provenance:
            builder.unknown(f"quality.provenance.{key}", "PROVENANCE_NOT_PROVIDED")


# ---- helpers ----

def _tensor_or_unknown(
    builder: RecordBuilder,
    tensor: Any,
    path: str,
    expected_points: int,
) -> dict[str, Any]:
    try:
        summary = readonly_tensor_summary(tensor, expected_points=expected_points)
    except TensorCopyError as exc:
        builder.unknown(path, f"TENSOR_COPY_FAILED:{exc}")
        return {
            "present": None,
            "shape": None,
            "dtype": None,
            "finite": None,
            "values": None,
            "reason": "TENSOR_COPY_FAILED",
        }
    if summary.get("present") is False:
        builder.unknown(path, summary.get("reason") or "TENSOR_ABSENT")
    elif summary.get("finite") is False:
        builder.unknown(path, "TENSOR_NON_FINITE")
    return summary


def _get_or_unknown(
    builder: RecordBuilder,
    src: dict[str, Any],
    key: str,
    path: str,
) -> Any:
    if key in src and src[key] is not None:
        return src[key]
    builder.unknown(path, f"FIELD_NOT_OBSERVED:{key}")
    return None


def _frame_join_status(p0: dict[str, Any] | None) -> str:
    if p0 is None:
        return "UNKNOWN"
    snap = p0.get("carla_snapshot_frame")
    game = p0.get("game_time_frame")
    sensor = p0.get("sensor_frames")
    if snap is None or game is None:
        return "UNKNOWN"
    frames = {snap, game}
    if isinstance(sensor, dict):
        frames.update(v for v in sensor.values() if v is not None)
    return "CONSISTENT" if len(frames) == 1 else "MISMATCH"


def _clock_join_status(clock: dict[str, Any]) -> str:
    sim = [clock.get(f) for f in schema.SIMULATION_CLOCK_FIELDS]
    mono = clock.get("probe_read_monotonic_s")
    if mono is None:
        return "UNKNOWN"
    if all(v is None for v in sim):
        return "SIM_UNKNOWN"
    return "SEPARATED"


def _monotonic() -> float:
    return time.monotonic()


def _calendar_utc() -> str:
    # 仅审计展示，不参与任何 expiry / latency 计算。
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
