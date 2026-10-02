"""Pure, answer-blind speed envelope for the full frozen shared prefix."""

from dataclasses import dataclass
import math

@dataclass(frozen=True)
class GovernorCommand:
    active: bool
    brake_floor: float
    throttle_ceiling: float
    reason: str

def prefix_governor_command(*, speed_mps: float, anchor_distance_m: float, zone_radius_m: float = 120.0, target_speed_mps: float = 3.0) -> GovernorCommand:
    values = (speed_mps, anchor_distance_m, zone_radius_m, target_speed_mps)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("NONFINITE_PREFIX_GOVERNOR_INPUT")
    if speed_mps < 0 or anchor_distance_m < 0 or zone_radius_m <= 0 or target_speed_mps <= 0:
        raise ValueError("INVALID_PREFIX_GOVERNOR_INPUT")
    if anchor_distance_m > zone_radius_m:
        return GovernorCommand(False, 0.0, 1.0, "OUTSIDE_SHARED_PREFIX_ZONE")
    overspeed = speed_mps - target_speed_mps
    if overspeed > 0.5:
        return GovernorCommand(True, min(1.0, max(0.35, overspeed / 6.0)), 0.0, "BRAKE_FOR_SHARED_PREFIX")
    ceiling = 0.45 if speed_mps < target_speed_mps - 0.5 else 0.25
    return GovernorCommand(True, 0.0, ceiling, "LIMIT_THROTTLE_IN_SHARED_PREFIX")
