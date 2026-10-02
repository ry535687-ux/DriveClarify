"""Frame binding — enforces FRAME_UNIT_POLICY_V0. Raw model plans stay in MODEL_LOCAL_RAW / RAW_UNIT.

No transform in v0 promotes a raw plan to METRE / CARLA_WORLD / EGO_LOCAL(left-right) / CAMERA /
IMAGE_PIXEL, because F2/F4/F5/F6 are unresolved. Any such request returns a structured refusal, never
a physical value. Pure; no CARLA/SimLingo/GPU.
"""

from __future__ import annotations

from .types import Frame

RAW_FRAME = Frame.MODEL_LOCAL_RAW.value
RAW_UNIT = "RAW_UNIT"
RAW_INDEX = "RAW_INDEX"

# Frames/units that are FORBIDDEN for raw model plans in v0 (unresolved F2/F4/F5/F6).
_FORBIDDEN_FRAMES = {
    Frame.CARLA_WORLD.value,
    Frame.CAMERA_FRAME.value,
    Frame.IMAGE_PIXEL.value,
    Frame.EGO_LOCAL_UNCALIBRATED.value,  # lateral/yaw sign unproven
}
_FORBIDDEN_UNITS = {"METRE", "MPS", "MPS2", "MPS3"}


def is_forbidden_frame(target_frame: str) -> bool:
    """A raw plan may not be relabeled into any calibrated/world/camera frame in v0."""
    return target_frame in _FORBIDDEN_FRAMES


def is_forbidden_unit(target_unit: str) -> bool:
    """A raw distance/speed may not be relabeled into a physical SI unit in v0."""
    return target_unit in _FORBIDDEN_UNITS


def bind_raw(target_frame: str = RAW_FRAME, target_unit: str = RAW_UNIT) -> tuple[str, str, str]:
    """Return (frame, unit, transform_id) for a raw pass-through. Refuses forbidden promotions by
    returning ('UNKNOWN', 'UNKNOWN', reason_code) instead of a physical binding."""
    if is_forbidden_frame(target_frame):
        return "UNKNOWN", "UNKNOWN", "FRAME_NOT_ESTABLISHED"
    if is_forbidden_unit(target_unit):
        return "UNKNOWN", "UNKNOWN", "UNIT_NOT_CALIBRATED"
    return target_frame, target_unit, "IDENTITY_RAW"
