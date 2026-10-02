"""Prospective offline common-prefix comparison contract."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Tuple


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PrefixEvidence:
    """Already-authoritative anchor evidence from one official rerun."""

    input_history_hash: str
    relative_sensor_timing_hash: str
    semantic_actor_set_hash: str
    actor_state_hash: str
    actor_spawn_manifest_hash: str
    traffic_light_state_hash: str
    stop_sign_state_hash: str
    weather_hash: str
    seed_manifest_hash: str
    runtime_source_hash: str
    pdm_config_hash: str
    preanchor_planner_hash: str
    anchor_identity: str
    ukf_state_hash: str
    physics_state_hash: str
    control_state_hash: str
    anchor_tick_index: int
    simulation_timestamp_s: float
    ego_xyz: Tuple[float, float, float]
    ego_velocity_xyz: Tuple[float, float, float]
    ego_angular_velocity_xyz: Tuple[float, float, float]
    ego_acceleration_xyz: Tuple[float, float, float]
    ego_yaw_deg: float
    speed_mps: float

    def __post_init__(self) -> None:
        hash_fields = (
            "input_history_hash", "relative_sensor_timing_hash",
            "semantic_actor_set_hash", "actor_state_hash", "actor_spawn_manifest_hash",
            "traffic_light_state_hash", "stop_sign_state_hash", "weather_hash",
            "seed_manifest_hash", "runtime_source_hash", "pdm_config_hash",
            "preanchor_planner_hash", "anchor_identity", "ukf_state_hash",
            "physics_state_hash", "control_state_hash",
        )
        if any(_SHA256_RE.fullmatch(getattr(self, field)) is None for field in hash_fields):
            raise ValueError("PREFIX_EVIDENCE_HASH_INVALID")
        if not isinstance(self.anchor_tick_index, int) or self.anchor_tick_index < 0:
            raise ValueError("ANCHOR_TICK_INDEX_INVALID")
        vectors = (
            self.ego_xyz, self.ego_velocity_xyz, self.ego_angular_velocity_xyz,
            self.ego_acceleration_xyz,
        )
        if any(len(vector) != 3 for vector in vectors):
            raise ValueError("PREFIX_VECTOR_SHAPE_INVALID")
        numeric = (
            (self.simulation_timestamp_s, self.ego_yaw_deg, self.speed_mps)
            + tuple(value for vector in vectors for value in vector)
        )
        if not all(
                isinstance(value, (int, float)) and math.isfinite(float(value))
                for value in numeric):
            raise ValueError("PREFIX_EVIDENCE_NONFINITE")


@dataclass(frozen=True)
class PrefixComparison:
    passed: bool
    mode: str
    tick_delta: int
    timestamp_delta_s: float
    translation_m: float
    yaw_delta_deg: float
    speed_delta_mps: float
    reasons: Tuple[str, ...]


def compare_common_prefix(old: PrefixEvidence, selected: PrefixEvidence) -> PrefixComparison:
    """Apply the frozen exact-or-bounded-near-same pre-action contract."""
    if not isinstance(old, PrefixEvidence) or not isinstance(selected, PrefixEvidence):
        raise TypeError("PREFIX_EVIDENCE_REQUIRED")
    exact_fields = (
        "input_history_hash", "relative_sensor_timing_hash",
        "semantic_actor_set_hash", "actor_state_hash", "actor_spawn_manifest_hash",
        "traffic_light_state_hash", "stop_sign_state_hash", "weather_hash",
        "seed_manifest_hash", "runtime_source_hash", "pdm_config_hash",
        "preanchor_planner_hash", "anchor_identity", "ukf_state_hash",
        "physics_state_hash", "control_state_hash", "ego_velocity_xyz",
        "ego_angular_velocity_xyz", "ego_acceleration_xyz",
    )
    reasons = tuple(
        field + "_MISMATCH"
        for field in exact_fields
        if getattr(old, field) != getattr(selected, field)
    )
    tick_delta = abs(old.anchor_tick_index - selected.anchor_tick_index)
    timestamp_delta = abs(old.simulation_timestamp_s - selected.simulation_timestamp_s)
    translation = math.sqrt(sum(
        (float(a) - float(b)) ** 2 for a, b in zip(old.ego_xyz, selected.ego_xyz)
    ))
    yaw = abs((float(old.ego_yaw_deg) - float(selected.ego_yaw_deg) + 180.0) % 360.0 - 180.0)
    speed = abs(float(old.speed_mps) - float(selected.speed_mps))
    if tick_delta > 1:
        reasons += ("ANCHOR_TICK_DELTA_EXCEEDS_ONE",)
    if timestamp_delta > 0.05 + 1.0e-9:
        reasons += ("SIMULATION_TIMESTAMP_DELTA_EXCEEDS_ONE_TICK",)
    if translation > 0.25:
        reasons += ("EGO_TRANSLATION_EXCEEDS_0_25_M",)
    if yaw > 1.0:
        reasons += ("EGO_YAW_EXCEEDS_1_DEG",)
    if speed > 0.25:
        reasons += ("EGO_SPEED_EXCEEDS_0_25_MPS",)
    exact = (
        not reasons and tick_delta == 0 and timestamp_delta == 0.0
        and translation == 0.0 and yaw == 0.0 and speed == 0.0
    )
    return PrefixComparison(
        passed=not reasons,
        mode="EXACT" if exact else ("FROZEN_NEAR_SAME" if not reasons else "FAIL"),
        tick_delta=tick_delta,
        timestamp_delta_s=timestamp_delta,
        translation_m=translation,
        yaw_delta_deg=yaw,
        speed_delta_mps=speed,
        reasons=reasons,
    )
