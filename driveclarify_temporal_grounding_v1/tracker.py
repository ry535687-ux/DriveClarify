"""Image-motion proposals plus ByteTrack's two-stage IoU association contract.

Grounding DINO supplies initial/reacquisition boxes. Between those expensive
events, sparse pyramidal-LK image motion produces lightweight real-RGB box
proposals. ByteTrack owns persistent identity association; it does not infer
semantic events.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .contracts import TrackLifecycleState, TrackObservation


@dataclass(frozen=True)
class Detection:
    bbox_xyxy: Tuple[float, float, float, float]
    confidence: float
    phrase: str
    source_grounding_id: str
    source: str


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return 0.0 if union <= 0.0 else intersection / union


@dataclass
class _Track:
    track_id: str
    source_grounding_id: str
    phrase: str
    bbox: Tuple[float, float, float, float]
    confidence: float
    first_frame: int
    last_frame: int
    age_frames: int = 1
    time_since_seen: int = 0
    hits: int = 1
    state: TrackLifecycleState = TrackLifecycleState.ACTIVE
    velocity: Tuple[float, float] = (0.0, 0.0)
    association_source: str = "GROUNDING_DINO_INITIAL"
    association_iou: Optional[float] = None

    def predicted_box(self) -> Tuple[float, float, float, float]:
        dx, dy = self.velocity
        x0, y0, x1, y1 = self.bbox
        return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


class ByteTrackAdapter:
    """Deterministic high-score then low-score association, fail-closed on loss."""

    implementation_id = "DRIVECLARIFY_BYTETRACK_ASSOCIATION_V1"

    def __init__(
        self,
        *,
        high_score_threshold: float = 0.5,
        low_score_threshold: float = 0.1,
        match_iou_threshold: float = 0.2,
        low_match_iou_threshold: float = 0.1,
        occlusion_buffer_frames: int = 2,
        lost_buffer_frames: int = 8,
    ) -> None:
        self.high_score_threshold = float(high_score_threshold)
        self.low_score_threshold = float(low_score_threshold)
        self.match_iou_threshold = float(match_iou_threshold)
        self.low_match_iou_threshold = float(low_match_iou_threshold)
        self.occlusion_buffer_frames = int(occlusion_buffer_frames)
        self.lost_buffer_frames = int(lost_buffer_frames)
        self._tracks: Dict[str, _Track] = {}
        self._next_id = 1
        self.id_switch_count = 0
        self.track_loss_count = 0
        self.reacquisition_count = 0
        self.latencies_seconds: List[float] = []

    @property
    def tracks(self) -> Tuple[TrackObservation, ...]:
        return ()

    def initialize_from_accepted_detections(
        self,
        detections: Sequence[Detection],
        *,
        frame_id: int,
        simulation_time: float,
    ) -> Tuple[TrackObservation, ...]:
        """Seed identities for detections already accepted upstream.

        This is deliberately separate from ordinary ByteTrack ``update``:
        low-score detections can associate an existing identity but do not
        normally birth one.  An upstream candidate set must not silently lose
        an accepted hypothesis while the convergence observer binds its two
        original identities.
        """

        started = time.monotonic()
        if self._tracks:
            raise RuntimeError("BYTETRACK_ACCEPTED_INITIALIZATION_REQUIRES_EMPTY_STATE")
        grounding_ids = tuple(str(item.source_grounding_id) for item in detections)
        if any(not value for value in grounding_ids):
            raise RuntimeError("BYTETRACK_ACCEPTED_INITIALIZATION_REQUIRES_SOURCE_IDS")
        if len(set(grounding_ids)) != len(grounding_ids):
            raise RuntimeError("BYTETRACK_ACCEPTED_INITIALIZATION_REQUIRES_DISTINCT_SOURCE_IDS")
        for detection in detections:
            track_id = "bt-{:04d}".format(self._next_id)
            self._next_id += 1
            self._tracks[track_id] = _Track(
                track_id=track_id,
                source_grounding_id=detection.source_grounding_id,
                phrase=detection.phrase,
                bbox=detection.bbox_xyxy,
                confidence=detection.confidence,
                first_frame=int(frame_id),
                last_frame=int(frame_id),
                association_source=detection.source,
            )
        observations = tuple(
            self._observation(track, frame_id, simulation_time)
            for _, track in sorted(self._tracks.items())
        )
        self.latencies_seconds.append(time.monotonic() - started)
        return observations

    def update(
        self,
        detections: Sequence[Detection],
        *,
        frame_id: int,
        simulation_time: float,
    ) -> Tuple[TrackObservation, ...]:
        started = time.monotonic()
        active_ids = [
            key
            for key, value in sorted(self._tracks.items())
            if value.state is not TrackLifecycleState.EXPIRED
        ]
        high = [item for item in detections if item.confidence >= self.high_score_threshold]
        low = [
            item
            for item in detections
            if self.low_score_threshold <= item.confidence < self.high_score_threshold
        ]
        unmatched_tracks = list(active_ids)
        matched_detection_ids = set()
        for pool, threshold in (
            (high, self.match_iou_threshold),
            (low, self.low_match_iou_threshold),
        ):
            pairs = self._match(unmatched_tracks, pool, threshold)
            matched_track_ids = set()
            for track_id, detection_index, score in pairs:
                detection = pool[detection_index]
                self._apply_detection(
                    self._tracks[track_id], detection, frame_id, score
                )
                matched_track_ids.add(track_id)
                matched_detection_ids.add(id(detection))
            unmatched_tracks = [
                track_id for track_id in unmatched_tracks if track_id not in matched_track_ids
            ]

        for track_id in unmatched_tracks:
            track = self._tracks[track_id]
            track.age_frames += 1
            track.time_since_seen += 1
            if track.time_since_seen <= self.occlusion_buffer_frames:
                track.state = TrackLifecycleState.TEMPORARILY_OCCLUDED
            elif track.time_since_seen <= self.lost_buffer_frames:
                if track.state is not TrackLifecycleState.LOST:
                    self.track_loss_count += 1
                track.state = TrackLifecycleState.LOST
            else:
                track.state = TrackLifecycleState.EXPIRED

        for detection in high:
            if id(detection) in matched_detection_ids:
                continue
            # Re-grounded boxes only reuse an identity through the ordinary
            # association gate above. Otherwise a new identity is mandatory.
            track_id = "bt-{:04d}".format(self._next_id)
            self._next_id += 1
            self._tracks[track_id] = _Track(
                track_id=track_id,
                source_grounding_id=detection.source_grounding_id,
                phrase=detection.phrase,
                bbox=detection.bbox_xyxy,
                confidence=detection.confidence,
                first_frame=int(frame_id),
                last_frame=int(frame_id),
                association_source=detection.source,
            )

        observations = tuple(
            self._observation(track, frame_id, simulation_time)
            for _, track in sorted(self._tracks.items())
            if track.state is not TrackLifecycleState.EXPIRED
        )
        self.latencies_seconds.append(time.monotonic() - started)
        return observations

    def _match(
        self,
        track_ids: Sequence[str],
        detections: Sequence[Detection],
        threshold: float,
    ) -> List[Tuple[str, int, float]]:
        if not track_ids or not detections:
            return []
        import numpy as np
        from scipy.optimize import linear_sum_assignment

        scores = np.asarray(
            [
                [_iou(self._tracks[track_id].predicted_box(), detection.bbox_xyxy) for detection in detections]
                for track_id in track_ids
            ],
            dtype=np.float64,
        )
        rows, columns = linear_sum_assignment(1.0 - scores)
        result = []
        for row, column in zip(rows.tolist(), columns.tolist()):
            score = float(scores[row, column])
            if score >= threshold:
                result.append((track_ids[row], int(column), score))
        return result

    def _apply_detection(
        self, track: _Track, detection: Detection, frame_id: int, score: float
    ) -> None:
        old_x = (track.bbox[0] + track.bbox[2]) / 2.0
        old_y = (track.bbox[1] + track.bbox[3]) / 2.0
        new_x = (detection.bbox_xyxy[0] + detection.bbox_xyxy[2]) / 2.0
        new_y = (detection.bbox_xyxy[1] + detection.bbox_xyxy[3]) / 2.0
        was_lost = track.state in {
            TrackLifecycleState.LOST,
            TrackLifecycleState.REACQUIRE_PENDING,
        }
        track.velocity = (new_x - old_x, new_y - old_y)
        track.bbox = detection.bbox_xyxy
        track.confidence = detection.confidence
        track.last_frame = int(frame_id)
        track.age_frames += 1
        track.time_since_seen = 0
        track.hits += 1
        track.state = TrackLifecycleState.ACTIVE
        track.association_source = detection.source
        track.association_iou = float(score)
        if was_lost:
            self.reacquisition_count += 1

    @staticmethod
    def _observation(
        track: _Track, frame_id: int, simulation_time: float
    ) -> TrackObservation:
        return TrackObservation(
            track_id=track.track_id,
            source_grounding_id=track.source_grounding_id,
            phrase=track.phrase,
            bbox_xyxy=track.bbox,
            frame_id=int(frame_id),
            simulation_time=float(simulation_time),
            age_frames=track.age_frames,
            time_since_seen_frames=track.time_since_seen,
            confidence=track.confidence,
            velocity_proxy_xy=track.velocity,
            lifecycle_state=track.state,
            freshness_frames=max(0, int(frame_id) - track.last_frame),
            association_source=track.association_source,
            association_iou=track.association_iou,
        )


class ImageMotionProposal:
    """Pyramidal-LK propagation on detached RGB copies; no semantic decision."""

    implementation_id = "SPARSE_PYRAMIDAL_LK_BOX_PROPOSAL_V1"

    def __init__(self, *, minimum_points: int = 5, max_corners: int = 80) -> None:
        self.minimum_points = int(minimum_points)
        self.max_corners = int(max_corners)
        self._previous_gray: Any = None
        self._boxes: Dict[str, Tuple[float, float, float, float]] = {}
        self._reference_sizes: Dict[str, Tuple[float, float]] = {}

    def initialize(self, image: Any, observations: Sequence[TrackObservation]) -> None:
        import cv2
        import numpy as np

        value = np.ascontiguousarray(image[:, :, :3]).copy()
        self._previous_gray = cv2.cvtColor(value, cv2.COLOR_BGR2GRAY)
        self._boxes = {item.track_id: item.bbox_xyxy for item in observations}
        self._reference_sizes = {
            item.track_id: (
                item.bbox_xyxy[2] - item.bbox_xyxy[0],
                item.bbox_xyxy[3] - item.bbox_xyxy[1],
            )
            for item in observations
        }

    def propose(
        self, image: Any, observations: Sequence[TrackObservation]
    ) -> Tuple[Detection, ...]:
        import cv2
        import numpy as np

        value = np.ascontiguousarray(image[:, :, :3]).copy()
        current = cv2.cvtColor(value, cv2.COLOR_BGR2GRAY)
        if self._previous_gray is None:
            self._previous_gray = current
            return ()
        height, width = current.shape[:2]
        proposals: List[Detection] = []
        for observation in observations:
            if observation.lifecycle_state not in {
                TrackLifecycleState.ACTIVE,
                TrackLifecycleState.TEMPORARILY_OCCLUDED,
            }:
                continue
            bbox = self._boxes.get(observation.track_id, observation.bbox_xyxy)
            x0, y0, x1, y1 = bbox
            mask = np.zeros_like(self._previous_gray)
            left, top = max(0, int(x0)), max(0, int(y0))
            right, bottom = min(width, int(math.ceil(x1))), min(height, int(math.ceil(y1)))
            if right - left < 4 or bottom - top < 4:
                continue
            mask[top:bottom, left:right] = 255
            before = cv2.goodFeaturesToTrack(
                self._previous_gray,
                mask=mask,
                maxCorners=self.max_corners,
                qualityLevel=0.01,
                minDistance=3,
                blockSize=5,
            )
            if before is None or len(before) < self.minimum_points:
                continue
            after, status, error = cv2.calcOpticalFlowPyrLK(
                self._previous_gray,
                current,
                before,
                None,
                winSize=(21, 21),
                maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
            )
            if after is None or status is None:
                continue
            valid = status.reshape(-1).astype(bool)
            if error is not None:
                valid &= error.reshape(-1) < 25.0
            # Reject background/edge drift with the standard forward-backward
            # consistency gate before estimating object motion.
            backward, backward_status, _ = cv2.calcOpticalFlowPyrLK(
                current,
                self._previous_gray,
                after,
                None,
                winSize=(21, 21),
                maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
            )
            if backward is None or backward_status is None:
                continue
            valid &= backward_status.reshape(-1).astype(bool)
            forward_backward_error = np.linalg.norm(
                backward.reshape(-1, 2) - before.reshape(-1, 2), axis=1
            )
            valid &= forward_backward_error < 1.5
            old = before.reshape(-1, 2)[valid]
            new = after.reshape(-1, 2)[valid]
            if len(new) < self.minimum_points:
                continue
            affine, inlier_mask = cv2.estimateAffinePartial2D(
                old,
                new,
                method=cv2.RANSAC,
                ransacReprojThreshold=2.5,
                maxIters=500,
                confidence=0.99,
                refineIters=10,
            )
            if inlier_mask is not None:
                inliers = inlier_mask.reshape(-1).astype(bool)
                if int(inliers.sum()) >= self.minimum_points:
                    old, new = old[inliers], new[inliers]
            delta = np.median(new - old, axis=0)
            raw_scale = 1.0
            if affine is not None:
                raw_scale = float(
                    math.sqrt(float(affine[0, 0]) ** 2 + float(affine[0, 1]) ** 2)
                )
            # Per-frame and initial-size bounds prevent compound LK scale drift
            # from manufacturing occupancy/clearance evidence.
            scale = max(0.97, min(1.03, raw_scale))
            reference_w, reference_h = self._reference_sizes.get(
                observation.track_id, (x1 - x0, y1 - y0)
            )
            cx = (x0 + x1) / 2.0 + float(delta[0])
            cy = (y0 + y1) / 2.0 + float(delta[1])
            proposed_w = max(0.50 * reference_w, min(1.50 * reference_w, (x1 - x0) * scale))
            proposed_h = max(0.50 * reference_h, min(1.50 * reference_h, (y1 - y0) * scale))
            half_w = proposed_w / 2.0
            half_h = proposed_h / 2.0
            moved = (
                max(0.0, cx - half_w),
                max(0.0, cy - half_h),
                min(float(width), cx + half_w),
                min(float(height), cy + half_h),
            )
            if moved[2] - moved[0] < 3 or moved[3] - moved[1] < 3:
                continue
            quality = min(1.0, len(new) / float(max(self.minimum_points, len(before))))
            confidence = max(0.15, min(0.95, observation.confidence * (0.75 + 0.25 * quality)))
            proposals.append(
                Detection(
                    bbox_xyxy=moved,
                    confidence=confidence,
                    phrase=observation.phrase,
                    source_grounding_id=observation.source_grounding_id,
                    source="REAL_RGB_LK_MOTION_PROPOSAL",
                )
            )
            self._boxes[observation.track_id] = moved
        self._previous_gray = current
        return tuple(proposals)


__all__ = ["ByteTrackAdapter", "Detection", "ImageMotionProposal"]
