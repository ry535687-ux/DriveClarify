"""Fail-closed E2 V3 provider; raw detector score can never be identity confidence."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256

from .association import AssociationThresholds
from .contracts import CandidateObjectSpec


def _unknown(*, now_s: float, frame_id: Any, observation_id: str, reasons: Sequence[str]) -> Mapping[str, Any]:
    value = {
        "field_id": "E2_GROUNDING", "status": "UNKNOWN", "value": None,
        "units": None, "frame": None, "simulation_timestamp_s": float(now_s),
        "source_frame_id": frame_id, "source_observation_id": str(observation_id),
        "owner": "E2_TRACKED_ASSOCIATION_V3_PROVIDER",
        "evidence_grade": "UNKNOWN_PRESERVED",
        "allowed_usage_purpose": "RQ2_T_V2_PRE_SCIENCE_ENGINEERING_ONLY",
        "freshness_age_simulation_s": 0.0, "dependencies": [
            "certified candidate interpretations", "runtime camera detections",
            "persistent visual tracks", "unique one-to-one association",
        ],
        "reason_codes": list(reasons), "retention": None,
    }
    value["evidence_digest"] = canonical_sha256(value)
    return value


def provide_grounding_e2_v3(
    *,
    now_s: float,
    frame_id: Any,
    observation_id: str,
    candidates: Sequence[CandidateObjectSpec],
    association: Mapping[str, Any],
    tracker_snapshot: Mapping[str, Any],
    thresholds: AssociationThresholds,
    active_invalidation_events: Sequence[str] = (),
) -> Mapping[str, Any]:
    reasons = []
    if active_invalidation_events:
        reasons.extend("ACTIVE_INVALIDATION:" + str(value) for value in active_invalidation_events)
    if len(candidates) < 2:
        reasons.append("CERTIFIED_CANDIDATE_SET_LT_2")
    if not association.get("unique_one_to_one_assignment"):
        reasons.extend(association.get("qualification_reason_codes") or ("ASSOCIATION_NOT_UNIQUE",))
    assignment = association.get("selected_assignment") or {}
    tracks = {row.get("track_id"): row for row in tracker_snapshot.get("tracks", ()) if isinstance(row, Mapping)}
    normalized = []
    for candidate in candidates:
        track_id = assignment.get(candidate.candidate_id)
        track = tracks.get(track_id)
        metrics = association.get("per_candidate", {}).get(candidate.candidate_id, {})
        if not isinstance(track, Mapping):
            reasons.append(candidate.candidate_id + ":TRACK_LINEAGE_MISSING")
            continue
        if track.get("state") != "ACTIVE" or track.get("deleted_state") is True:
            reasons.append(candidate.candidate_id + ":TRACK_LINEAGE_NOT_COHERENT")
        if int(track.get("hit_count", 0)) < thresholds.minimum_track_hits:
            reasons.append(candidate.candidate_id + ":TRACK_HITS_BELOW_GATE")
        if int(track.get("age_frames", 0)) < thresholds.minimum_track_age_frames:
            reasons.append(candidate.candidate_id + ":TRACK_AGE_BELOW_GATE")
        if metrics.get("qualified") is not True:
            reasons.append(candidate.candidate_id + ":ASSOCIATION_NOT_QUALIFIED")
        normalized.append({
            "candidate_id": candidate.candidate_id,
            "interpretation_id": candidate.interpretation_id,
            "typed_candidate_spec": candidate.to_dict(),
            "track_id": track_id,
            "identity_association_score": metrics.get("top1_score"),
            "uniqueness_margin": metrics.get("uniqueness_margin"),
            "detector_support_score": track.get("latest_detector_support_score"),
            "track_coherence_score": track.get("track_coherence_score"),
            "track_age_frames": track.get("age_frames"),
            "track_hit_count": track.get("hit_count"),
            "track_miss_count": track.get("miss_count"),
            "lineage_state": track.get("state"),
            "source_frames": copy.deepcopy(track.get("source_frames") or []),
        })
    if reasons or len(normalized) != len(candidates):
        return _unknown(
            now_s=now_s, frame_id=frame_id, observation_id=observation_id,
            reasons=tuple(sorted(set(reasons or ("E2_V3_DEPENDENCY_INCOMPLETE",)))),
        )
    payload = {
        "grounding_complete": True,
        "method_id": "E2_TRACKED_ASSOCIATION_V3",
        "candidate_groundings": normalized,
        "unique_one_to_one_assignment": True,
        "candidate_track_matrix_digest": association.get("matrix_digest"),
        "identity_score_name": "identity_association_score",
        "identity_score_is_probability": False,
        "raw_detector_score_used_as_identity_confidence": False,
        "thresholds": thresholds.to_dict(),
    }
    value = {
        "field_id": "E2_GROUNDING", "status": "AVAILABLE", "value": payload,
        "units": "TRACKED_CANDIDATE_TO_TRACK_ASSOCIATION_SCORE",
        "frame": "CURRENT_RUNTIME_CAMERA_TRACK_LINEAGE",
        "simulation_timestamp_s": float(now_s), "source_frame_id": frame_id,
        "source_observation_id": str(observation_id),
        "owner": "E2_TRACKED_ASSOCIATION_V3_PROVIDER",
        "evidence_grade": "AVAILABLE_RUNTIME_OBSERVED",
        "allowed_usage_purpose": "RQ2_T_V2_PRE_SCIENCE_ENGINEERING_ONLY",
        "freshness_age_simulation_s": 0.0, "dependencies": [
            "certified candidate interpretations", "runtime camera detections",
            "persistent visual tracks", "unique one-to-one association",
        ],
        "reason_codes": [], "retention": None,
    }
    value["evidence_digest"] = canonical_sha256(value)
    return value


__all__ = ["provide_grounding_e2_v3"]
