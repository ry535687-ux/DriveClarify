"""Future live spectator chase-camera (default-OFF, fail-open). NOT run live this round.

The ONLY live action it may ever take is spectator.set_transform(...) to place CARLA's
built-in spectator behind/above the ego for a human chase view. It never creates an actor,
never adds a second RGB sensor, never mutates ego/control/weather, never calls world.tick,
never adds a forward/PID/planner step. Any failure disables it silently.

SPECTATOR VIEW != MODEL INPUT. This round ships the interface + unit/static-contract tests
only; live validation needs a future authorized CARLA run.
"""

from __future__ import annotations

import math
from typing import Any


class SpectatorChaseConfig:
    def __init__(self, enabled: bool = False, back_m: float = 8.0, up_m: float = 3.5,
                 pitch_deg: float = -15.0) -> None:
        self.enabled = enabled              # DEFAULT OFF
        self.back_m = back_m
        self.up_m = up_m
        self.pitch_deg = pitch_deg


# Method names the chase module is FORBIDDEN to ever call (asserted by tests via a fake).
FORBIDDEN_WORLD_CALLS = (
    "tick", "apply_settings", "spawn_actor", "try_spawn_actor",
)
FORBIDDEN_ACTOR_CALLS = ("apply_control", "set_transform")  # ego/actor must not be moved


class SpectatorChase:
    """Places ONLY the spectator. Read/observe everything else."""

    def __init__(self, config: SpectatorChaseConfig | None = None) -> None:
        self.config = config or SpectatorChaseConfig()
        self.applied = 0
        self.errors = 0
        self.disabled_reason = None if self.config.enabled else "DEFAULT_OFF"

    def compute_transform(self, ego_location_xyz, ego_yaw_deg):
        """Pure geometry: chase pose behind+above ego. Returns (loc_xyz, rot_pyr_deg)."""
        yr = math.radians(ego_yaw_deg)
        bx = ego_location_xyz[0] - self.config.back_m * math.cos(yr)
        by = ego_location_xyz[1] - self.config.back_m * math.sin(yr)
        bz = ego_location_xyz[2] + self.config.up_m
        return [bx, by, bz], [self.config.pitch_deg, 0.0, ego_yaw_deg]

    def maybe_apply(self, world: Any, ego_location_xyz, ego_yaw_deg) -> bool:
        """If enabled, set ONLY the spectator transform. Fail-open. Returns applied?.

        Deliberately touches world.get_spectator().set_transform and NOTHING else. Never
        ticks, spawns, or applies control. Any exception disables the chase and is counted.
        """
        if not self.config.enabled:
            self.disabled_reason = "DEFAULT_OFF"
            return False
        try:
            import carla  # local import; not imported at module load (no CARLA this round)
            spectator = world.get_spectator()
            loc, rot = self.compute_transform(ego_location_xyz, ego_yaw_deg)
            tf = carla.Transform(
                carla.Location(x=loc[0], y=loc[1], z=loc[2]),
                carla.Rotation(pitch=rot[0], yaw=rot[2], roll=rot[1]))
            spectator.set_transform(tf)
            self.applied += 1
            return True
        except Exception as e:  # noqa: BLE001 - fail-open, never break the sim
            self.errors += 1
            self.disabled_reason = f"DISABLED_ON_ERROR:{type(e).__name__}"
            return False

    def stats(self) -> dict[str, Any]:
        return {"enabled": self.config.enabled, "applied": self.applied,
                "errors": self.errors, "disabled_reason": self.disabled_reason,
                "note": "SPECTATOR VIEW != MODEL INPUT; live validation needs future authorization"}
