"""Read-only V11 non-progress diagnostics.

The detector never changes route authority or VehicleControl.  It converts generic
runtime observations into attributable diagnostic states for durable heartbeats and
engineering regression tests; it receives no case, family, seed, or gold label.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Deque, Mapping, Optional, Sequence


@dataclass(frozen=True)
class ProgressSample:
    frame: int
    xyz: Sequence[float]
    speed_mps: float
    target_point: Optional[Sequence[float]]
    route_progress: Optional[float]
    desired_speed_mps: Optional[float]
    brake: float
    model_forward_count: int
    control_return_count: int
    defer_pending: bool


class NonProgressMonitor:
    """Classify sustained liveness/progress invariants over a bounded window."""

    def __init__(self, window_ticks: int = 80):
        if int(window_ticks) < 2:
            raise ValueError("NONPROGRESS_WINDOW_REQUIRES_TWO_TICKS")
        self.window_ticks = int(window_ticks)
        self._samples: Deque[ProgressSample] = deque(maxlen=self.window_ticks)

    def observe(self, sample: ProgressSample) -> Mapping[str, object]:
        self._samples.append(sample)
        if len(self._samples) < self.window_ticks:
            return {
                "classification": "INSUFFICIENT_WINDOW",
                "window_tick_count": len(self._samples),
            }
        first, last = self._samples[0], self._samples[-1]
        displacement = math.dist(first.xyz[:2], last.xyz[:2])
        target_delta = self._target_delta(first.target_point, last.target_point)
        route_delta = self._optional_delta(first.route_progress, last.route_progress)
        forward_delta = last.model_forward_count - first.model_forward_count
        control_delta = last.control_return_count - first.control_return_count
        stationary = displacement <= 0.25 and max(s.speed_mps for s in self._samples) <= 0.15
        target_frozen = target_delta is not None and target_delta <= 0.05
        route_frozen = route_delta is not None and route_delta <= 0.01
        positive_prediction = any(
            s.desired_speed_mps is not None and s.desired_speed_mps >= 0.8
            for s in self._samples
        )
        model_requested_stop = all(
            s.desired_speed_mps is not None and s.desired_speed_mps < 0.4
            for s in self._samples
        ) and all(s.brake >= 0.99 for s in self._samples)
        indefinite_defer = all(s.defer_pending for s in self._samples)

        if control_delta > 0 and forward_delta == 0:
            classification = "MODEL_LOOP_DEAD_CONTROL_LOOP_LIVE"
        elif indefinite_defer and stationary:
            classification = "INDEFINITE_DEFER_NONPROGRESS"
        elif stationary and positive_prediction:
            classification = "POSITIVE_PREDICTED_SPEED_EGO_STATIONARY"
        elif stationary and model_requested_stop:
            classification = "MODEL_REQUESTED_STOP"
        elif stationary and route_frozen and target_frozen:
            classification = "ROUTE_AND_TARGET_PROGRESS_FROZEN"
        else:
            classification = "PROGRESS_OR_NO_GENERIC_FAULT"
        return {
            "classification": classification,
            "window_tick_count": len(self._samples),
            "frame_interval": [first.frame, last.frame],
            "xy_displacement_m": displacement,
            "target_delta_m": target_delta,
            "route_progress_delta": route_delta,
            "model_forward_delta": forward_delta,
            "control_return_delta": control_delta,
            "stationary": stationary,
            "target_frozen": target_frozen,
            "route_progress_frozen": route_frozen,
            "defer_pending": indefinite_defer,
        }

    @staticmethod
    def _target_delta(first, last):
        if first is None or last is None:
            return None
        left = list(first)
        right = list(last)
        while left and isinstance(left[0], (tuple, list)):
            left = list(left[0])
        while right and isinstance(right[0], (tuple, list)):
            right = list(right[0])
        if len(left) < 2 or len(right) < 2:
            return None
        return math.dist(left[:2], right[:2])

    @staticmethod
    def _optional_delta(first, last):
        if first is None or last is None:
            return None
        return abs(float(last) - float(first))


__all__ = ["NonProgressMonitor", "ProgressSample"]
