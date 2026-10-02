"""Deterministic multi-object track lifecycle for E2 V3."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def iou(left: Sequence[float], right: Sequence[float]) -> float:
    x0, y0 = max(float(left[0]), float(right[0])), max(float(left[1]), float(right[1]))
    x1, y1 = min(float(left[2]), float(right[2])), min(float(left[3]), float(right[3]))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_left = max(0.0, float(left[2]) - float(left[0])) * max(0.0, float(left[3]) - float(left[1]))
    area_right = max(0.0, float(right[2]) - float(right[0])) * max(0.0, float(right[3]) - float(right[1]))
    union = area_left + area_right - intersection
    return 0.0 if union <= 0.0 else intersection / union


@dataclass(frozen=True)
class RuntimeDetection:
    bbox_xyxy: Tuple[float, float, float, float]
    detector_support_score: float
    object_class: str
    color: str
    phrase: str
    source_frame_id: int
    source_observation_id: str
    source_detection_id: str
    source_kind: str = "GROUNDING_DINO_PERIODIC"


@dataclass
class TrackState:
    track_id: str
    object_class: str
    color: str
    bbox_xyxy: Tuple[float, float, float, float]
    created_frame_id: int
    latest_frame_id: int
    age_frames: int = 1
    hit_count: int = 1
    miss_count: int = 0
    state: str = "ACTIVE"
    lost_state: bool = False
    reacquired_state: bool = False
    deleted_state: bool = False
    velocity_proxy_xy: Tuple[float, float] = (0.0, 0.0)
    latest_detector_support_score: float = 0.0
    association_history: List[Mapping[str, Any]] = field(default_factory=list)
    source_frames: List[int] = field(default_factory=list)
    coherence_ious: List[float] = field(default_factory=list)

    def predicted_box(self) -> Tuple[float, float, float, float]:
        dx, dy = self.velocity_proxy_xy
        x0, y0, x1, y1 = self.bbox_xyxy
        return x0 + dx, y0 + dy, x1 + dx, y1 + dy

    def to_dict(self) -> Mapping[str, Any]:
        value = asdict(self)
        value["bbox_xyxy"] = list(self.bbox_xyxy)
        value["velocity_proxy_xy"] = list(self.velocity_proxy_xy)
        value["motion_estimate_px_per_acquisition"] = math.hypot(*self.velocity_proxy_xy)
        value["track_coherence_score"] = (
            sum(self.coherence_ious) / len(self.coherence_ious) if self.coherence_ious else 1.0
        )
        value["confidence_components"] = {
            "detector_support_score": self.latest_detector_support_score,
            "track_coherence_score": value["track_coherence_score"],
            "persistence_hit_count": self.hit_count,
        }
        return value


class PersistentMultiObjectTracker:
    """ByteTrack-style two-stage IoU association with explicit lineage breaks."""

    implementation_id = "E2_V3_DETERMINISTIC_BYTETRACK_STYLE_IOU_V1"

    def __init__(
        self,
        *,
        high_support_threshold: float = 0.35,
        low_support_threshold: float = 0.05,
        high_match_iou: float = 0.18,
        low_match_iou: float = 0.08,
        lost_grace_acquisitions: int = 2,
        deletion_after_misses: int = 4,
        duplicate_iou_threshold: float = 0.75,
    ) -> None:
        self.high_support_threshold = float(high_support_threshold)
        self.low_support_threshold = float(low_support_threshold)
        self.high_match_iou = float(high_match_iou)
        self.low_match_iou = float(low_match_iou)
        self.lost_grace_acquisitions = int(lost_grace_acquisitions)
        self.deletion_after_misses = int(deletion_after_misses)
        self.duplicate_iou_threshold = float(duplicate_iou_threshold)
        self._tracks: Dict[str, TrackState] = {}
        self._next_id = 1
        self.events: List[Mapping[str, Any]] = []
        self.id_switch_count = 0
        self.reacquisition_count = 0
        self.duplicate_conflict_count = 0

    @property
    def tracks(self) -> Tuple[TrackState, ...]:
        return tuple(self._tracks[key] for key in sorted(self._tracks) if not self._tracks[key].deleted_state)

    def advance_frame(self, frame_id: int) -> None:
        for track in self.tracks:
            delta = max(0, int(frame_id) - int(track.latest_frame_id))
            track.age_frames = max(track.age_frames, int(frame_id) - track.created_frame_id + 1)
            if delta > 0 and track.state == "ACTIVE":
                track.reacquired_state = False

    def update(
        self,
        detections: Sequence[RuntimeDetection],
        *,
        frame_id: int,
        simulation_time_s: float,
    ) -> Tuple[TrackState, ...]:
        self.advance_frame(frame_id)
        eligible = [track for track in self.tracks if track.state in {"ACTIVE", "TEMPORARILY_LOST", "LOST"}]
        high = [row for row in detections if row.detector_support_score >= self.high_support_threshold]
        low = [row for row in detections if self.low_support_threshold <= row.detector_support_score < self.high_support_threshold]
        unmatched_ids = [track.track_id for track in eligible]
        matched_detection_ids = set()
        for pool, threshold in ((high, self.high_match_iou), (low, self.low_match_iou)):
            matches = self._assign(unmatched_ids, pool, threshold)
            matched_tracks = set()
            for track_id, detection_index, overlap in matches:
                detection = pool[detection_index]
                self._apply(self._tracks[track_id], detection, overlap, frame_id, simulation_time_s)
                matched_tracks.add(track_id)
                matched_detection_ids.add(id(detection))
            unmatched_ids = [track_id for track_id in unmatched_ids if track_id not in matched_tracks]

        for track_id in unmatched_ids:
            track = self._tracks[track_id]
            track.miss_count += 1
            track.reacquired_state = False
            if track.miss_count <= self.lost_grace_acquisitions:
                track.state = "TEMPORARILY_LOST"
                track.lost_state = True
            elif track.miss_count <= self.deletion_after_misses:
                track.state = "LOST"
                track.lost_state = True
            else:
                track.state = "DELETED"
                track.deleted_state = True
            self.events.append({
                "event": "TRACK_MISSED" if not track.deleted_state else "TRACK_DELETED",
                "track_id": track_id,
                "frame_id": int(frame_id),
                "simulation_time_s": float(simulation_time_s),
                "miss_count": track.miss_count,
                "state": track.state,
            })

        for detection in detections:
            if id(detection) in matched_detection_ids or detection.detector_support_score < self.low_support_threshold:
                continue
            overlapping = [
                track for track in self.tracks
                if iou(track.bbox_xyxy, detection.bbox_xyxy) >= self.duplicate_iou_threshold
                and track.object_class == detection.object_class
            ]
            if overlapping:
                self.duplicate_conflict_count += 1
                self.events.append({
                    "event": "DUPLICATE_TRACK_BIRTH_REJECTED",
                    "existing_track_ids": [track.track_id for track in overlapping],
                    "frame_id": int(frame_id),
                    "source_detection_id": detection.source_detection_id,
                })
                continue
            self._create(detection, frame_id, simulation_time_s)
        return self.tracks

    def _assign(
        self, track_ids: Sequence[str], detections: Sequence[RuntimeDetection], threshold: float
    ) -> List[Tuple[str, int, float]]:
        if not track_ids or not detections:
            return []
        import numpy as np
        from scipy.optimize import linear_sum_assignment

        matrix = np.zeros((len(track_ids), len(detections)), dtype=np.float64)
        for row_index, track_id in enumerate(track_ids):
            track = self._tracks[track_id]
            for column_index, detection in enumerate(detections):
                if not self._class_compatible(track.object_class, detection.object_class):
                    continue
                matrix[row_index, column_index] = iou(track.predicted_box(), detection.bbox_xyxy)
        rows, columns = linear_sum_assignment(1.0 - matrix)
        return [
            (track_ids[row], int(column), float(matrix[row, column]))
            for row, column in zip(rows.tolist(), columns.tolist())
            if float(matrix[row, column]) >= float(threshold)
        ]

    @staticmethod
    def _class_compatible(left: str, right: str) -> bool:
        vehicles = {"vehicle", "car", "van", "truck", "bus", "motorcycle"}
        return left == right or (left in vehicles and right in vehicles)

    def _create(self, detection: RuntimeDetection, frame_id: int, simulation_time_s: float) -> None:
        track_id = "e2v3-track-{:04d}".format(self._next_id)
        self._next_id += 1
        track = TrackState(
            track_id=track_id,
            object_class=detection.object_class,
            color=detection.color,
            bbox_xyxy=detection.bbox_xyxy,
            created_frame_id=int(frame_id),
            latest_frame_id=int(frame_id),
            latest_detector_support_score=float(detection.detector_support_score),
            source_frames=[int(frame_id)],
            association_history=[{
                "frame_id": int(frame_id), "event": "TRACK_CREATED",
                "source_detection_id": detection.source_detection_id,
                "source_kind": detection.source_kind, "iou": None,
            }],
        )
        self._tracks[track_id] = track
        self.events.append({
            "event": "TRACK_CREATED", "track_id": track_id, "frame_id": int(frame_id),
            "simulation_time_s": float(simulation_time_s),
        })

    def _apply(
        self,
        track: TrackState,
        detection: RuntimeDetection,
        overlap: float,
        frame_id: int,
        simulation_time_s: float,
    ) -> None:
        old_center = ((track.bbox_xyxy[0] + track.bbox_xyxy[2]) / 2.0, (track.bbox_xyxy[1] + track.bbox_xyxy[3]) / 2.0)
        new_center = ((detection.bbox_xyxy[0] + detection.bbox_xyxy[2]) / 2.0, (detection.bbox_xyxy[1] + detection.bbox_xyxy[3]) / 2.0)
        was_lost = track.state in {"TEMPORARILY_LOST", "LOST"}
        track.velocity_proxy_xy = (new_center[0] - old_center[0], new_center[1] - old_center[1])
        track.bbox_xyxy = detection.bbox_xyxy
        track.latest_frame_id = int(frame_id)
        track.hit_count += 1
        track.miss_count = 0
        track.state = "ACTIVE"
        track.lost_state = False
        track.reacquired_state = bool(was_lost)
        track.latest_detector_support_score = float(detection.detector_support_score)
        track.source_frames.append(int(frame_id))
        track.coherence_ious.append(float(overlap))
        track.association_history.append({
            "frame_id": int(frame_id), "event": "TRACK_REACQUIRED" if was_lost else "TRACK_MATCHED",
            "source_detection_id": detection.source_detection_id,
            "source_kind": detection.source_kind, "iou": float(overlap),
        })
        if was_lost:
            self.reacquisition_count += 1
            self.events.append({
                "event": "TRACK_REACQUIRED", "track_id": track.track_id,
                "frame_id": int(frame_id), "simulation_time_s": float(simulation_time_s),
            })

    def mark_id_switch(self, track_id: str, replacement_track_id: str, *, frame_id: int) -> None:
        if track_id not in self._tracks or replacement_track_id not in self._tracks:
            raise ValueError("E2_V3_ID_SWITCH_TRACK_UNKNOWN")
        self.id_switch_count += 1
        self._tracks[track_id].state = "ID_SWITCH_CONFLICT"
        self._tracks[track_id].lost_state = True
        self.events.append({
            "event": "ID_SWITCH_CONFLICT", "track_id": track_id,
            "replacement_track_id": replacement_track_id, "frame_id": int(frame_id),
        })

    def snapshot(self) -> Mapping[str, Any]:
        return {
            "schema_version": "driveclarify.e2_v3.tracker_snapshot.v1",
            "implementation_id": self.implementation_id,
            "tracks": [track.to_dict() for track in self.tracks],
            "events": list(self.events),
            "tracks_created": self._next_id - 1,
            "id_switch_count": self.id_switch_count,
            "reacquisition_count": self.reacquisition_count,
            "duplicate_conflict_count": self.duplicate_conflict_count,
        }


__all__ = ["PersistentMultiObjectTracker", "RuntimeDetection", "TrackState", "iou"]
