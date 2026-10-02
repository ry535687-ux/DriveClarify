"""Fail-closed E2 V4 provider with acquisition-clock maturity."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256

from .association import AssociationConfiguration
from .contracts import CandidateObjectSpec, METHOD_ID


def _unknown(*, now_s: float, frame_id: Any, observation_id: str, reasons: Sequence[str]) -> Mapping[str, Any]:
    value = {
        "field_id": "E2_GROUNDING", "status": "UNKNOWN", "value": None,
        "units": None, "frame": None, "simulation_timestamp_s": float(now_s),
        "source_frame_id": frame_id, "source_observation_id": str(observation_id),
        "owner": "E2_V4_DOMAIN_INVARIANT_ASSOCIATION_PROVIDER",
        "evidence_grade": "UNKNOWN_PRESERVED",
        "allowed_usage_purpose": "RQ2_T_V2_FINAL_PRE_SCIENCE_ENGINEERING_ONLY",
        "freshness_age_simulation_s": 0.0,
        "dependencies": [
            "certified semantic family", "certified candidate interpretations",
            "runtime camera detections", "persistent acquisition-clock tracks",
            "unique one-to-one association",
        ],
        "reason_codes": list(reasons), "retention": None,
    }
    value["evidence_digest"] = canonical_sha256(value)
    return value


def provide_grounding_e2_v4(
    *,
    now_s: float,
    frame_id: Any,
    observation_id: str,
    candidates: Sequence[CandidateObjectSpec],
    association: Mapping[str, Any],
    tracker_snapshot: Mapping[str, Any],
    configuration: AssociationConfiguration,
    active_invalidation_events: Sequence[str] = (),
) -> Mapping[str, Any]:
    reasons = ["ACTIVE_INVALIDATION:" + str(row) for row in active_invalidation_events]
    if association.get("family") in {"ORDER", "UNDERSPECIFIED_CONSTRAINT"}:
        reasons.append("FAMILY_DOES_NOT_AUTHORIZE_E2_OBJECT_ASSOCIATION")
    if len(candidates) < 2:
        reasons.append("CERTIFIED_CANDIDATE_SET_LT_2")
    if association.get("unique_one_to_one_assignment") is not True:
        reasons.extend(association.get("qualification_reason_codes") or ("ASSOCIATION_NOT_UNIQUE",))
    assignment = association.get("selected_assignment") or {}
    tracks = {str(row.get("track_id")): row for row in tracker_snapshot.get("tracks", ()) if isinstance(row, Mapping)}
    normalized = []
    thresholds = configuration.thresholds
    for candidate in candidates:
        track_id = assignment.get(candidate.candidate_id)
        track = tracks.get(str(track_id))
        metrics = association.get("per_candidate", {}).get(candidate.candidate_id, {})
        if track is None:
            reasons.append(candidate.candidate_id + ":TRACK_LINEAGE_MISSING")
            continue
        if track.get("state") != "ACTIVE" or track.get("deleted_state") is True:
            reasons.append(candidate.candidate_id + ":LINEAGE_INVALID")
        if int(track.get("hit_count", 0)) < thresholds.minimum_track_hits:
            reasons.append(candidate.candidate_id + ":TRACK_HITS_INSUFFICIENT")
        if int(track.get("age_acquisitions", 0)) < thresholds.minimum_track_age_acquisitions:
            reasons.append(candidate.candidate_id + ":TRACK_AGE_INSUFFICIENT")
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
            "track_age_acquisitions": track.get("age_acquisitions"),
            "track_hit_count": track.get("hit_count"),
            "track_miss_count": track.get("miss_count"),
            "lineage_state": track.get("state"),
            "source_frames": copy.deepcopy(track.get("source_frames") or []),
        })
    if reasons or len(normalized) != len(candidates):
        return _unknown(
            now_s=now_s, frame_id=frame_id, observation_id=observation_id,
            reasons=tuple(sorted(set(reasons or ("E2_V4_DEPENDENCY_INCOMPLETE",)))),
        )
    payload = {
        "grounding_complete": True,
        "method_id": METHOD_ID,
        "family": association.get("family"),
        "family_mask": association.get("family_mask"),
        "candidate_groundings": normalized,
        "unique_one_to_one_assignment": True,
        "candidate_track_matrix_digest": association.get("matrix_digest"),
        "identity_score_name": "missing_aware_domain_invariant_association_score",
        "identity_score_is_probability": False,
        "raw_detector_score_used_as_identity_confidence": False,
        "configuration": configuration.to_dict(),
    }
    value = {
        "field_id": "E2_GROUNDING", "status": "AVAILABLE", "value": payload,
        "units": "DOMAIN_INVARIANT_TRACKED_CANDIDATE_ASSOCIATION_SCORE",
        "frame": "EGO_ROUTE_AND_CANDIDATE_SET_RELATIVE_RUNTIME_FRAME",
        "simulation_timestamp_s": float(now_s), "source_frame_id": frame_id,
        "source_observation_id": str(observation_id),
        "owner": "E2_V4_DOMAIN_INVARIANT_ASSOCIATION_PROVIDER",
        "evidence_grade": "AVAILABLE_RUNTIME_OBSERVED",
        "allowed_usage_purpose": "RQ2_T_V2_FINAL_PRE_SCIENCE_ENGINEERING_ONLY",
        "freshness_age_simulation_s": 0.0,
        "dependencies": [
            "certified semantic family", "certified candidate interpretations",
            "persistent acquisition-clock tracks", "unique one-to-one association",
        ],
        "reason_codes": [], "retention": None,
    }
    value["evidence_digest"] = canonical_sha256(value)
    return value


__all__ = ["provide_grounding_e2_v4"]
