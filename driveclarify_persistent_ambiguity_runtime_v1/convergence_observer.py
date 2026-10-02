"""Runtime-observable K2-to-K1 convergence wiring for Method V1.

The observer reuses the frozen Grounding DINO and ByteTrack primitives.  It
never reads scenario actors, evaluator state, an expected survivor, or a
decision label.  A convergence event is emitted only when one original track
is LOST and a fresh same-frame Grounding DINO reacquisition associates exactly
one other original track as ACTIVE.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional, Sequence

from driveclarify_temporal_grounding_v1.contracts import TrackLifecycleState
from driveclarify_temporal_grounding_v1.tracker import (
    ByteTrackAdapter,
    Detection,
    ImageMotionProposal,
)

from .method_v1_decision import (
    CandidateConvergenceEvidence,
    ConvergenceEvidenceKind,
)


CONVERGENCE_OBSERVER_ENV = "DRIVECLARIFY_METHOD_V1_CONVERGENCE_OBSERVER"


def _track_dict(value: Any) -> dict[str, Any]:
    method = getattr(value, "to_dict", None)
    if callable(method):
        return dict(method())
    return {
        "track_id": getattr(value, "track_id", None),
        "source_grounding_id": getattr(value, "source_grounding_id", None),
        "lifecycle_state": getattr(
            getattr(value, "lifecycle_state", None), "value", None
        ),
        "association_source": getattr(value, "association_source", None),
        "source_frame_id": getattr(value, "frame_id", None),
    }


class RuntimeGroundingConvergenceObserver:
    """Label-free event producer for the already-frozen convergence contract."""

    def __init__(
        self,
        *,
        detector: Any,
        phrase: str,
        tracker: Optional[Any] = None,
        motion: Optional[Any] = None,
    ) -> None:
        self.detector = detector
        self.phrase = str(phrase)
        self.tracker = tracker if tracker is not None else ByteTrackAdapter()
        self.motion = motion if motion is not None else ImageMotionProposal()
        self._tracks: Sequence[Any] = ()
        self._candidate_by_track: dict[str, str] = {}
        self._initialized = False
        self._reacquisition_attempted = False
        self._emitted = False
        self._dino_reacquisition_frames: list[int] = []
        self._timeline: list[dict[str, Any]] = []
        self._fresh_grounding: Optional[dict[str, Any]] = None

    def initialize(
        self,
        *,
        candidate_rows: Sequence[Mapping[str, Any]],
        selected_referents: Sequence[Any],
        image: Any,
        frame_id: int,
        simulation_time: float,
    ) -> None:
        if self._initialized:
            return
        candidate_by_grounding = {
            str(row.get("referent_id")): str(row.get("candidate_id"))
            for row in candidate_rows
            if row.get("referent_id") and row.get("candidate_id")
        }
        if (
            len(candidate_by_grounding) != 2
            or len(set(candidate_by_grounding.values())) != 2
        ):
            raise RuntimeError("CONVERGENCE_OBSERVER_REQUIRES_TWO_DISTINCT_CANDIDATES")
        detections = tuple(
            Detection(
                bbox_xyxy=tuple(float(value) for value in item.bbox_xyxy),
                confidence=float(item.detector_confidence),
                phrase=str(item.phrase),
                source_grounding_id=str(item.local_object_id),
                source="GROUNDING_DINO_INITIAL",
            )
            for item in selected_referents
            if str(item.local_object_id) in candidate_by_grounding
        )
        if (
            len(detections) != 2
            or len({item.source_grounding_id for item in detections}) != 2
        ):
            raise RuntimeError("CONVERGENCE_OBSERVER_REQUIRES_TWO_MATCHED_DETECTIONS")
        bootstrap = getattr(
            self.tracker, "initialize_from_accepted_detections", None
        )
        if callable(bootstrap):
            self._tracks = bootstrap(
                detections,
                frame_id=int(frame_id),
                simulation_time=float(simulation_time),
            )
        else:
            # Injected deterministic trackers predate the production bootstrap
            # seam and retain their explicit first-update fixture behavior.
            self._tracks = self.tracker.update(
                detections,
                frame_id=int(frame_id),
                simulation_time=float(simulation_time),
            )
        self._candidate_by_track = {
            str(track.track_id): candidate_by_grounding[str(track.source_grounding_id)]
            for track in self._tracks
            if str(track.source_grounding_id) in candidate_by_grounding
        }
        if len(self._candidate_by_track) != 2:
            raise RuntimeError("CONVERGENCE_OBSERVER_REQUIRES_TWO_TRACKED_CANDIDATES")
        self.motion.initialize(image, self._tracks)
        self._initialized = True
        self._timeline.append(
            {
                "event": "INITIAL_K2_TRACK_IDENTITY_BOUND",
                "source_frame_id": int(frame_id),
                "tracks": [_track_dict(row) for row in self._tracks],
                "candidate_by_track": dict(sorted(self._candidate_by_track.items())),
            }
        )

    def observe(
        self,
        *,
        image: Any,
        frame_id: int,
        observation_id: str,
        simulation_time: float,
    ) -> Optional[CandidateConvergenceEvidence]:
        if not self._initialized or self._emitted or self._reacquisition_attempted:
            return None
        proposals = self.motion.propose(image, self._tracks)
        self._tracks = self.tracker.update(
            proposals,
            frame_id=int(frame_id),
            simulation_time=float(simulation_time),
        )
        original = tuple(
            row
            for row in self._tracks
            if str(getattr(row, "track_id", "")) in self._candidate_by_track
        )
        lost = tuple(
            row
            for row in original
            if row.lifecycle_state is TrackLifecycleState.LOST
        )
        if not lost:
            return None

        self._reacquisition_attempted = True
        grounding = self.detector.ground(
            image,
            self.phrase,
            frame_id=int(frame_id),
            observation_id=str(observation_id),
            captured_monotonic=time.monotonic(),
        )
        self._dino_reacquisition_frames.append(int(frame_id))
        self._fresh_grounding = grounding.to_dict()
        detections = tuple(
            Detection(
                bbox_xyxy=tuple(float(value) for value in item.bbox_xyxy),
                confidence=float(item.detector_confidence),
                phrase=str(item.phrase),
                source_grounding_id=str(item.local_object_id),
                source="GROUNDING_DINO_REACQUISITION",
            )
            for item in grounding.selected_referents
        )
        self._tracks = self.tracker.update(
            detections,
            frame_id=int(frame_id),
            simulation_time=float(simulation_time),
        )
        original = tuple(
            row
            for row in self._tracks
            if str(getattr(row, "track_id", "")) in self._candidate_by_track
        )
        active = tuple(
            row
            for row in original
            if row.lifecycle_state is TrackLifecycleState.ACTIVE
            and str(getattr(row, "association_source", ""))
            == "GROUNDING_DINO_REACQUISITION"
        )
        rejected_tracks = tuple(
            row
            for row in original
            if row.lifecycle_state is TrackLifecycleState.LOST
        )
        self._timeline.append(
            {
                "event": "FRESH_GROUNDING_REACQUISITION",
                "source_observation_id": str(observation_id),
                "source_frame_id": int(frame_id),
                "fresh_effective_k": int(grounding.effective_k),
                "tracks": [_track_dict(row) for row in self._tracks],
                "active_original_track_ids": [str(row.track_id) for row in active],
                "rejected_original_track_ids": [
                    str(row.track_id) for row in rejected_tracks
                ],
            }
        )
        if not (
            int(grounding.effective_k) == 1
            and len(active) == 1
            and len(rejected_tracks) == 1
        ):
            return None
        rejected_candidates = tuple(
            self._candidate_by_track[str(row.track_id)] for row in rejected_tracks
        )
        evidence = CandidateConvergenceEvidence.create(
            evidence_id="runtime-grounding-convergence-{}-{}".format(
                observation_id, frame_id
            ),
            evidence_kind=ConvergenceEvidenceKind.RUNTIME_GROUNDING,
            source_observation_id=str(observation_id),
            source_frame_id=int(frame_id),
            observed_monotonic_time=time.monotonic(),
            rejected_candidate_ids=rejected_candidates,
            provenance=(
                ConvergenceEvidenceKind.RUNTIME_GROUNDING.value,
                ConvergenceEvidenceKind.RUNTIME_OBSERVATION.value,
            ),
            visibility="RUNTIME_OBSERVABLE",
            privileged_authorization_reads=0,
        )
        self._emitted = True
        self._timeline.append(
            {
                "event": "CANDIDATE_CONVERGENCE_EVIDENCE_EMITTED",
                "source_observation_id": str(observation_id),
                "source_frame_id": int(frame_id),
                "rejected_candidate_ids": list(rejected_candidates),
                "evidence_digest": evidence.evidence_digest,
            }
        )
        return evidence

    def audit(self) -> dict[str, Any]:
        return {
            "schema_version": "driveclarify.runtime_grounding_convergence_observer.v1",
            "status": (
                "CONVERGENCE_EVIDENCE_EMITTED"
                if self._emitted
                else "REACQUISITION_DID_NOT_ESTABLISH_UNIQUE_ORIGINAL_TRACK"
                if self._reacquisition_attempted
                else "OBSERVING_RUNTIME_TRACKS"
                if self._initialized
                else "NOT_INITIALIZED"
            ),
            "implementation": "FROZEN_GROUNDING_DINO_PLUS_BYTETRACK_EVENT_WIRING",
            "runtime_inputs": [
                "real_rgb_0",
                "Grounding_DINO_boxes",
                "ByteTrack_identity_and_lifecycle",
                "current_observation_id",
                "current_source_frame_id",
            ],
            "forbidden_input_reads": {
                "expected_surviving_candidate": 0,
                "gold_target": 0,
                "expected_decision": 0,
                "scenario_evaluator_answer": 0,
                "carla_actor_truth": 0,
                "dev": 0,
                "test": 0,
            },
            "tracker_implementation": getattr(
                self.tracker, "implementation_id", type(self.tracker).__name__
            ),
            "initial_identity_source": "UPSTREAM_ACCEPTED_GROUNDING_DETECTIONS",
            "initial_identity_count": len(self._candidate_by_track),
            "motion_implementation": getattr(
                self.motion, "implementation_id", type(self.motion).__name__
            ),
            "reacquisition_attempted": self._reacquisition_attempted,
            "dino_reacquisition_forward_count": len(
                self._dino_reacquisition_frames
            ),
            "dino_reacquisition_frames": list(self._dino_reacquisition_frames),
            "candidate_by_track": dict(sorted(self._candidate_by_track.items())),
            "fresh_grounding": self._fresh_grounding,
            "timeline": list(self._timeline),
        }


__all__ = [
    "CONVERGENCE_OBSERVER_ENV",
    "RuntimeGroundingConvergenceObserver",
]
