"""DriveClarify temporal event estimator over tracked real-RGB geometry."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .contracts import (
    EventObservation,
    EventRegion,
    EventState,
    TrackLifecycleState,
    TrackObservation,
)


def _intersection_fraction(
    bbox: Tuple[float, float, float, float],
    region: Tuple[float, float, float, float],
) -> float:
    left, top = max(bbox[0], region[0]), max(bbox[1], region[1])
    right, bottom = min(bbox[2], region[2]), min(bbox[3], region[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
    return 0.0 if area <= 0.0 else intersection / area


@dataclass
class _EventMemory:
    event_id: str
    state: EventState = EventState.UNKNOWN
    first_evidence_frame: Optional[int] = None
    transitions: List[Tuple[str, int]] = None  # type: ignore[assignment]
    occupied_frames: int = 0
    motion_evidence_frames: int = 0
    outside_frames: int = 0
    cleared_candidate_frame: Optional[int] = None
    cleared_confirmed_frame: Optional[int] = None
    previous_bbox: Optional[Tuple[float, float, float, float]] = None

    def __post_init__(self) -> None:
        if self.transitions is None:
            self.transitions = []


class TemporalEventEstimator:
    """Hysteretic `CLEARED`; track loss is a distinct fail-closed state."""

    implementation_id = "DRIVECLARIFY_IMAGE_REGION_CLEARANCE_ESTIMATOR_V1"

    def __init__(
        self,
        *,
        occupying_overlap_fraction: float = 0.35,
        outside_overlap_fraction: float = 0.05,
        occupying_persistence_frames: int = 2,
        motion_persistence_frames: int = 2,
        cleared_persistence_frames: int = 3,
        minimum_upward_motion_pixels: float = 0.25,
        minimum_area_reduction_fraction: float = 0.002,
    ) -> None:
        self.occupying_overlap_fraction = float(occupying_overlap_fraction)
        self.outside_overlap_fraction = float(outside_overlap_fraction)
        self.occupying_persistence_frames = int(occupying_persistence_frames)
        self.motion_persistence_frames = int(motion_persistence_frames)
        self.cleared_persistence_frames = int(cleared_persistence_frames)
        self.minimum_upward_motion_pixels = float(minimum_upward_motion_pixels)
        self.minimum_area_reduction_fraction = float(minimum_area_reduction_fraction)
        self._memory: Dict[str, _EventMemory] = {}
        self.latencies_seconds: List[float] = []

    def update(
        self, track: TrackObservation, region: EventRegion
    ) -> EventObservation:
        started = time.monotonic()
        event_id = track.track_id + ":FULL_BBOX_CLEARS:" + region.region_id
        memory = self._memory.setdefault(event_id, _EventMemory(event_id=event_id))
        overlap = _intersection_fraction(track.bbox_xyxy, region.bbox_xyxy)
        reasons: List[str] = []

        if track.lifecycle_state in {
            TrackLifecycleState.LOST,
            TrackLifecycleState.REACQUIRE_PENDING,
            TrackLifecycleState.EXPIRED,
        }:
            self._transition(memory, EventState.TRACK_LOST, track.frame_id)
            memory.outside_frames = 0
            reasons.append("TRACK_LOST_NEVER_COUNTS_AS_CLEARED")
            result = self._result(memory, track, region, overlap, reasons)
            self.latencies_seconds.append(time.monotonic() - started)
            return result
        if track.lifecycle_state is TrackLifecycleState.TEMPORARILY_OCCLUDED:
            reasons.append("TEMPORARY_OCCLUSION_FAILS_CLOSED")
            result = self._result(memory, track, region, overlap, reasons)
            self.latencies_seconds.append(time.monotonic() - started)
            return result

        bbox = track.bbox_xyxy
        if memory.first_evidence_frame is None:
            memory.first_evidence_frame = track.frame_id
            self._transition(memory, EventState.APPROACHING, track.frame_id)
        if overlap >= self.occupying_overlap_fraction:
            memory.occupied_frames += 1
            memory.outside_frames = 0
        else:
            memory.occupied_frames = 0

        if memory.previous_bbox is not None:
            old = memory.previous_bbox
            old_area = max(1.0, (old[2] - old[0]) * (old[3] - old[1]))
            new_area = max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
            upward = old[3] - bbox[3]
            reduction = (old_area - new_area) / old_area
            if (
                upward >= self.minimum_upward_motion_pixels
                or reduction >= self.minimum_area_reduction_fraction
            ):
                memory.motion_evidence_frames += 1
            else:
                memory.motion_evidence_frames = max(0, memory.motion_evidence_frames - 1)
        memory.previous_bbox = bbox

        if memory.state is EventState.CLEARED:
            reasons.append("CLEARED_LATCHED_FOR_EVENT_IDENTITY")
        elif memory.occupied_frames >= self.occupying_persistence_frames:
            self._transition(memory, EventState.OCCUPYING_RELEVANT_REGION, track.frame_id)
            reasons.append("PERSISTENT_REGION_OCCUPANCY")
        elif (
            any(state == EventState.OCCUPYING_RELEVANT_REGION.value for state, _ in memory.transitions)
            and memory.motion_evidence_frames >= self.motion_persistence_frames
        ):
            self._transition(memory, EventState.CLEARING, track.frame_id)
            reasons.append("IDENTITY_PRESERVED_DIRECTIONAL_CLEARING_EVIDENCE")

        was_occupied = any(
            state == EventState.OCCUPYING_RELEVANT_REGION.value
            for state, _ in memory.transitions
        )
        if (
            was_occupied
            and memory.motion_evidence_frames >= self.motion_persistence_frames
            and overlap <= self.outside_overlap_fraction
        ):
            memory.outside_frames += 1
            if memory.cleared_candidate_frame is None:
                memory.cleared_candidate_frame = track.frame_id
            if (
                memory.outside_frames >= self.cleared_persistence_frames
                and memory.cleared_confirmed_frame is None
            ):
                memory.cleared_confirmed_frame = track.frame_id
                self._transition(memory, EventState.CLEARED, track.frame_id)
                reasons.append("ACTIVE_TRACK_FULL_BOX_OUTSIDE_REGION_WITH_HYSTERESIS")
        elif overlap > self.outside_overlap_fraction:
            memory.outside_frames = 0
            memory.cleared_candidate_frame = None

        result = self._result(memory, track, region, overlap, reasons)
        self.latencies_seconds.append(time.monotonic() - started)
        return result

    @staticmethod
    def _transition(memory: _EventMemory, state: EventState, frame_id: int) -> None:
        if memory.state is state:
            return
        memory.state = state
        memory.transitions.append((state.value, int(frame_id)))

    @staticmethod
    def _result(
        memory: _EventMemory,
        track: TrackObservation,
        region: EventRegion,
        overlap: float,
        reasons: List[str],
    ) -> EventObservation:
        latency = (
            None
            if memory.cleared_candidate_frame is None
            or memory.cleared_confirmed_frame is None
            else memory.cleared_confirmed_frame - memory.cleared_candidate_frame
        )
        confidence = min(
            1.0,
            0.35 * min(1.0, memory.motion_evidence_frames / 3.0)
            + 0.35 * min(1.0, memory.outside_frames / 3.0)
            + 0.30 * track.confidence,
        )
        return EventObservation(
            event_id=memory.event_id,
            track_id=track.track_id,
            state=memory.state,
            frame_id=track.frame_id,
            simulation_time=track.simulation_time,
            first_evidence_frame=memory.first_evidence_frame,
            transition_frames=tuple(memory.transitions),
            cleared_candidate_frame=memory.cleared_candidate_frame,
            cleared_confirmed_frame=memory.cleared_confirmed_frame,
            event_latency_frames=latency,
            track_age_frames=track.age_frames,
            region_id=region.region_id,
            overlap_fraction=float(overlap),
            outside_persistence_frames=memory.outside_frames,
            motion_evidence_frames=memory.motion_evidence_frames,
            confidence=confidence,
            reason_codes=tuple(reasons) or ("TEMPORAL_EVIDENCE_ACCUMULATING",),
        )


__all__ = ["TemporalEventEstimator"]
