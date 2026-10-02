"""Candidate-to-track scoring and unique one-to-one assignment for E2 V3."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contracts import CandidateObjectSpec, UNSPECIFIED, canonical_sha256
from .tracker import TrackState


FEATURE_WEIGHTS = {
    "object_class_compatibility": 0.18,
    "color_support": 0.15,
    "motion_state_compatibility": 0.08,
    "lane_relation_compatibility": 0.10,
    "relative_order_compatibility": 0.22,
    "left_right_compatibility": 0.08,
    "detector_support_score": 0.04,
    "track_persistence_score": 0.08,
    "temporal_coherence_score": 0.07,
}

VEHICLE_CLASSES = {"vehicle", "car", "van", "truck", "bus", "motorcycle"}
LANDMARK_CLASSES = {"kiosk", "shop", "store", "cafe", "school", "hospital", "station", "building", "bus stop", "landmark"}


@dataclass(frozen=True)
class AssociationThresholds:
    association_score_threshold: float
    uniqueness_margin_threshold: float
    minimum_track_hits: int
    minimum_track_age_frames: int

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "association_score_threshold": float(self.association_score_threshold),
            "uniqueness_margin_threshold": float(self.uniqueness_margin_threshold),
            "minimum_track_hits": int(self.minimum_track_hits),
            "minimum_track_age_frames": int(self.minimum_track_age_frames),
            "score_semantics": "NON_PROBABILISTIC_WEIGHTED_ASSOCIATION_SCORE",
        }


PRECALIBRATION_THRESHOLDS = AssociationThresholds(0.70, 0.15, 2, 5)


def _class_compatible(candidate: str, track: str) -> bool:
    if candidate == UNSPECIFIED:
        return True
    return candidate == track or (candidate in VEHICLE_CLASSES and track in VEHICLE_CLASSES) or (
        candidate in LANDMARK_CLASSES and track in LANDMARK_CLASSES
    )


def _track_area(track: TrackState) -> float:
    x0, y0, x1, y1 = track.bbox_xyxy
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _bottom_clipped_ego_hood(track: TrackState, image_width: int, image_height: int) -> bool:
    x0, _y0, x1, y1 = track.bbox_xyxy
    area_fraction = _track_area(track) / float(max(1, image_width * image_height))
    width_fraction = max(0.0, x1 - x0) / float(max(1, image_width))
    return bool(
        y1 >= 0.98 * float(image_height)
        and (area_fraction >= 0.05 or width_fraction >= 0.30)
    )


def _motion_label(track: TrackState) -> str:
    speed = math.hypot(*track.velocity_proxy_xy)
    return "MOVING" if speed >= 2.0 else "PARKED"


def _horizontal_label(track: TrackState, image_width: int) -> str:
    center = (track.bbox_xyxy[0] + track.bbox_xyxy[2]) / (2.0 * float(image_width))
    return "LEFT" if center < 0.4 else "RIGHT" if center > 0.6 else "CENTER"


def _lane_label(track: TrackState, image_width: int) -> str:
    label = _horizontal_label(track, image_width)
    return "LEFT_LANE" if label == "LEFT" else "RIGHT_LANE" if label == "RIGHT" else "EGO_LANE"


def _rank_maps(tracks: Sequence[TrackState]) -> Tuple[Mapping[str, int], Mapping[str, int]]:
    near = {track.track_id: index for index, track in enumerate(sorted(tracks, key=lambda row: (-_track_area(row), row.track_id)), 1)}
    horizontal = {track.track_id: index for index, track in enumerate(sorted(tracks, key=lambda row: (row.bbox_xyxy[0], row.track_id)), 1)}
    return near, horizontal


def _attribute_score(expected: str, observed: str) -> float:
    if expected == UNSPECIFIED:
        # An absent language constraint is not half-incompatible.  It imposes
        # no restriction on this pair, so the compatibility component is
        # neutral at one while the separately recorded feature remains
        # explicitly UNSPECIFIED in the typed candidate.
        return 1.0
    return 1.0 if expected == observed else 0.0


def score_pair(
    candidate: CandidateObjectSpec,
    track: TrackState,
    *,
    near_rank: int,
    horizontal_rank: int,
    track_count: int,
    image_width: int,
    image_height: int,
) -> Mapping[str, Any]:
    gates: List[str] = []
    if track.state != "ACTIVE":
        gates.append("TRACK_NOT_ACTIVE:" + track.state)
    if not _class_compatible(candidate.object_class, track.object_class):
        gates.append("OBJECT_CLASS_INCOMPATIBLE")
    if candidate.color != UNSPECIFIED and track.color not in {candidate.color, UNSPECIFIED}:
        gates.append("COLOR_INCOMPATIBLE")
    x0, y0, x1, y1 = track.bbox_xyxy
    area_fraction = _track_area(track) / float(max(1, image_width * image_height))
    if _bottom_clipped_ego_hood(track, image_width, image_height):
        gates.append("BOTTOM_CLIPPED_EGO_HOOD_OR_NON_REFERENT")
    expected_rank = None
    if candidate.apparent_near_far == "NEARER" or candidate.relative_longitudinal_order in {"FRONT", "FIRST"} or candidate.local_ordinal == "FIRST":
        expected_rank = 1
    elif candidate.apparent_near_far == "FARTHER" or candidate.relative_longitudinal_order in {"REAR", "SECOND"} or candidate.local_ordinal == "SECOND":
        expected_rank = 2
    if expected_rank is not None and near_rank != expected_rank:
        gates.append("APPARENT_ORDER_INCOMPATIBLE")
    explicit_horizontal = candidate.left_right_relation
    expected_horizontal = explicit_horizontal
    if expected_horizontal == UNSPECIFIED and candidate.lane_relation in {"LEFT_LANE", "RIGHT_LANE"}:
        expected_horizontal = "LEFT" if candidate.lane_relation == "LEFT_LANE" else "RIGHT"
    absolute_horizontal = _horizontal_label(track, image_width)
    horizontal = (
        "LEFT" if track_count >= 2 and horizontal_rank == 1
        else "RIGHT" if track_count >= 2 and horizontal_rank == track_count
        else absolute_horizontal
    )
    lane = _lane_label(track, image_width)
    motion = _motion_label(track)
    if explicit_horizontal != UNSPECIFIED and horizontal != explicit_horizontal:
        gates.append("LEFT_RIGHT_RELATION_INCOMPATIBLE")
    order_score = 0.5 if expected_rank is None else (1.0 if near_rank == expected_rank else 0.0)
    components = {
        "object_class_compatibility": 1.0 if _class_compatible(candidate.object_class, track.object_class) else 0.0,
        "color_support": _attribute_score(candidate.color, track.color),
        "motion_state_compatibility": _attribute_score(candidate.parked_vs_moving, motion),
        "lane_relation_compatibility": _attribute_score(candidate.lane_relation, lane),
        "relative_order_compatibility": order_score,
        "left_right_compatibility": _attribute_score(expected_horizontal, horizontal),
        "detector_support_score": max(0.0, min(1.0, float(track.latest_detector_support_score))),
        "track_persistence_score": max(0.0, min(1.0, float(track.hit_count) / 3.0)),
        "temporal_coherence_score": (
            max(0.0, min(1.0, sum(track.coherence_ious) / len(track.coherence_ious)))
            if track.coherence_ious else 0.5
        ),
    }
    score = sum(FEATURE_WEIGHTS[name] * value for name, value in components.items())
    return {
        "candidate_id": candidate.candidate_id,
        "track_id": track.track_id,
        "hard_gate_pass": not gates,
        "hard_gate_reasons": gates,
        "component_scores": components,
        "candidate_track_association_score": None if gates else float(score),
        "detector_support_score": components["detector_support_score"],
        "track_coherence_score": components["temporal_coherence_score"],
        "near_rank": int(near_rank),
        "horizontal_rank": int(horizontal_rank),
        "track_count": int(track_count),
        "observed_attributes": {
            "object_class": track.object_class, "color": track.color,
            "motion_state": motion, "lane_relation": lane,
            "apparent_near_far": "NEARER" if near_rank == 1 else "FARTHER" if near_rank == 2 else "OTHER",
            "left_right_relation": horizontal,
        },
    }


def associate_candidates_to_tracks(
    candidates: Sequence[CandidateObjectSpec],
    tracks: Sequence[TrackState],
    *,
    thresholds: AssociationThresholds,
    image_width: int,
    image_height: int,
    distinct_objects_required: bool = True,
) -> Mapping[str, Any]:
    # Bottom-clipped large boxes are the ego hood in the qualified rgb_0
    # configuration.  Exclude them before apparent-order ranks are computed;
    # otherwise a non-referent could change "nearer/farther" semantics.
    # LOST lineages remain in the tracker so that later detections can be
    # deterministically reacquired.  They are not observable objects on this
    # exact frame, however, and therefore cannot define apparent-order ranks
    # or occupy a candidate assignment.
    active = [
        track for track in tracks
        if track.state == "ACTIVE"
        and not track.deleted_state
        and not _bottom_clipped_ego_hood(track, image_width, image_height)
    ]
    near_ranks, horizontal_ranks = _rank_maps(active)
    matrix: Dict[str, Dict[str, Optional[float]]] = {}
    details: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for candidate in candidates:
        matrix[candidate.candidate_id] = {}
        details[candidate.candidate_id] = {}
        for track in active:
            detail = score_pair(
                candidate, track, near_rank=near_ranks[track.track_id],
                horizontal_rank=horizontal_ranks[track.track_id], track_count=len(active),
                image_width=image_width, image_height=image_height,
            )
            details[candidate.candidate_id][track.track_id] = detail
            matrix[candidate.candidate_id][track.track_id] = detail["candidate_track_association_score"]
    candidate_ids = [row.candidate_id for row in candidates]
    track_ids = [row.track_id for row in active]
    selected: Dict[str, str] = {}
    if candidate_ids and track_ids:
        import numpy as np
        from scipy.optimize import linear_sum_assignment

        values = np.full((len(candidate_ids), len(track_ids)), -1.0, dtype=np.float64)
        for row_index, candidate_id in enumerate(candidate_ids):
            for column_index, track_id in enumerate(track_ids):
                value = matrix[candidate_id][track_id]
                if value is not None:
                    values[row_index, column_index] = float(value)
        rows, columns = linear_sum_assignment(-values)
        for row_index, column_index in zip(rows.tolist(), columns.tolist()):
            if values[row_index, column_index] >= 0.0:
                selected[candidate_ids[row_index]] = track_ids[column_index]
    per_candidate: Dict[str, Mapping[str, Any]] = {}
    qualification_reasons: List[str] = []
    for candidate_id in candidate_ids:
        ranked = sorted(
            ((float(value), track_id) for track_id, value in matrix[candidate_id].items() if value is not None),
            key=lambda item: (-item[0], item[1]),
        )
        top1_score = ranked[0][0] if ranked else None
        top1_track = ranked[0][1] if ranked else None
        top2_score = ranked[1][0] if len(ranked) >= 2 else 0.0 if ranked else None
        margin = None if top1_score is None or top2_score is None else top1_score - top2_score
        assignment = selected.get(candidate_id)
        selected_track = next((track for track in active if track.track_id == assignment), None)
        reasons = []
        if assignment is None:
            reasons.append("NO_ONE_TO_ONE_ASSIGNMENT")
        if top1_track is not None and assignment != top1_track:
            reasons.append("GLOBAL_ASSIGNMENT_NOT_CANDIDATE_TOP1")
        if top1_score is None or top1_score < thresholds.association_score_threshold:
            reasons.append("ASSOCIATION_SCORE_BELOW_THRESHOLD")
        if margin is None or margin < thresholds.uniqueness_margin_threshold:
            reasons.append("UNIQUENESS_MARGIN_BELOW_THRESHOLD")
        if selected_track is None or selected_track.hit_count < thresholds.minimum_track_hits:
            reasons.append("TRACK_PERSISTENCE_HITS_INSUFFICIENT")
        if selected_track is None or selected_track.age_frames < thresholds.minimum_track_age_frames:
            reasons.append("TRACK_AGE_INSUFFICIENT")
        if selected_track is not None and selected_track.state != "ACTIVE":
            reasons.append("TRACK_LINEAGE_NOT_ACTIVE")
        per_candidate[candidate_id] = {
            "selected_track_id": assignment,
            "top1_track_id": top1_track,
            "top1_score": top1_score,
            "top2_score": top2_score,
            "uniqueness_margin": margin,
            "qualified": not reasons,
            "reason_codes": reasons,
        }
        qualification_reasons.extend(candidate_id + ":" + reason for reason in reasons)
    if distinct_objects_required and len(set(selected.values())) != len(candidate_ids):
        qualification_reasons.append("DISTINCT_CANDIDATES_NOT_ASSIGNED_TO_DISTINCT_TRACKS")
    complete = bool(
        len(selected) == len(candidate_ids)
        and len(set(selected.values())) == len(candidate_ids)
        and all(row["qualified"] for row in per_candidate.values())
    )
    result = {
        "schema_version": "driveclarify.e2_v3.candidate_track_matrix.v1",
        "feature_weights": dict(FEATURE_WEIGHTS),
        "candidate_ids": candidate_ids,
        "track_ids": track_ids,
        "matrix": matrix,
        "pair_details": details,
        "selected_assignment": selected,
        "per_candidate": per_candidate,
        "distinct_objects_required": bool(distinct_objects_required),
        "unique_one_to_one_assignment": complete,
        "qualification_reason_codes": sorted(set(qualification_reasons)),
        "thresholds": thresholds.to_dict(),
        "score_is_probability": False,
        "score_name": "identity_association_score",
    }
    result["matrix_digest"] = canonical_sha256(result)
    return result


__all__ = [
    "AssociationThresholds", "FEATURE_WEIGHTS", "PRECALIBRATION_THRESHOLDS",
    "associate_candidates_to_tracks", "score_pair",
]
