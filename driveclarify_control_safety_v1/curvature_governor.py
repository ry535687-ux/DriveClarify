"""Pure, answer-blind speed governor for the frozen observation-anchor curve."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class GovernorCommand:
    active: bool
    brake_floor: float
    throttle_ceiling: float
    reason: str


def curvature_governor_command(
    *,
    speed_mps: float,
    anchor_distance_m: float,
    zone_radius_m: float = 25.0,
    target_speed_mps: float = 3.0,
) -> GovernorCommand:
    """Return a bounded override based only on ego speed and frozen-route distance."""

    values = (speed_mps, anchor_distance_m, zone_radius_m, target_speed_mps)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("NONFINITE_CURVATURE_GOVERNOR_INPUT")
    if speed_mps < 0 or anchor_distance_m < 0 or zone_radius_m <= 0 or target_speed_mps <= 0:
        raise ValueError("INVALID_CURVATURE_GOVERNOR_INPUT")
    if anchor_distance_m > zone_radius_m:
        return GovernorCommand(False, 0.0, 1.0, "OUTSIDE_ANCHOR_CURVATURE_ZONE")
    overspeed = speed_mps - target_speed_mps
    if overspeed > 0.5:
        return GovernorCommand(
            True,
            min(1.0, max(0.35, overspeed / 6.0)),
            0.0,
            "BRAKE_FOR_ANCHOR_CURVATURE",
        )
    return GovernorCommand(True, 0.0, 0.25, "LIMIT_THROTTLE_IN_ANCHOR_CURVATURE")
