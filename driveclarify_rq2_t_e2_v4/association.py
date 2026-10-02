"""Family-aware, layout-invariant candidate-to-track association for E2 V4."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from .contracts import (
    CandidateObjectSpec,
    UNSPECIFIED,
    assert_family_selection_is_scene_independent,
    canonical_sha256,
)
from .geometry import deterministic_relative_ranks
from .tracker import TrackState


VEHICLE_CLASSES = {"vehicle", "car", "van", "truck", "bus", "motorcycle"}
LANDMARK_CLASSES = {"kiosk", "shop", "store", "cafe", "school", "hospital", "station", "building", "bus stop", "landmark"}

# Frozen before any V4 calibration outcome. Calibration selects one complete
# profile; it never fits individual frame weights.
WEIGHT_PROFILE_GRID: Mapping[str, Mapping[str, float]] = {
    "RANK_DOMINANT_SIMPLE": {
        "object_class_compatibility": 0.18,
        "color_compatibility": 0.12,
        "candidate_set_depth_rank": 0.34,
        "route_frame_lateral_relation": 0.08,
        "route_lane_topology_relation": 0.06,
        "temporal_motion_compatibility": 0.05,
        "visibility_normalized_detector_support": 0.04,
        "track_persistence": 0.07,
        "temporal_coherence": 0.06,
    },
    "BALANCED_INVARIANT": {
        "object_class_compatibility": 0.20,
        "color_compatibility": 0.14,
        "candidate_set_depth_rank": 0.26,
        "route_frame_lateral_relation": 0.10,
        "route_lane_topology_relation": 0.08,
        "temporal_motion_compatibility": 0.06,
        "visibility_normalized_detector_support": 0.05,
        "track_persistence": 0.06,
        "temporal_coherence": 0.05,
    },
    "SEMANTIC_CONSERVATIVE": {
        "object_class_compatibility": 0.24,
        "color_compatibility": 0.18,
        "candidate_set_depth_rank": 0.22,
        "route_frame_lateral_relation": 0.09,
        "route_lane_topology_relation": 0.09,
        "temporal_motion_compatibility": 0.05,
        "visibility_normalized_detector_support": 0.03,
        "track_persistence": 0.05,
        "temporal_coherence": 0.05,
    },
}

FAMILY_MASKS: Mapping[str, tuple[str, ...]] = {
    "REFERENTIAL": (
        "object_class_compatibility",
        "color_compatibility",
        "candidate_set_depth_rank",
        "route_frame_lateral_relation",
        "route_lane_topology_relation",
        "temporal_motion_compatibility",
        "visibility_normalized_detector_support",
        "track_persistence",
        "temporal_coherence",
    ),
    "LANDMARK": (
        "object_class_compatibility",
        "color_compatibility",
        "candidate_set_depth_rank",
        "route_frame_lateral_relation",
        "route_lane_topology_relation",
        "visibility_normalized_detector_support",
        "track_persistence",
        "temporal_coherence",
    ),
    "ORDER": (),
    "UNDERSPECIFIED_CONSTRAINT": (),
}


@dataclass(frozen=True)
class AssociationThresholdsV4:
    association_score_threshold: float
    uniqueness_margin_threshold: float
    minimum_track_hits: int
    minimum_track_age_acquisitions: int

    @property
    def minimum_track_age_frames(self) -> int:
        """Compatibility alias; the value is acquisitions, never native frames."""
        return self.minimum_track_age_acquisitions

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "association_score_threshold": float(self.association_score_threshold),
            "uniqueness_margin_threshold": float(self.uniqueness_margin_threshold),
            "minimum_track_hits": int(self.minimum_track_hits),
            "minimum_track_age_acquisitions": int(self.minimum_track_age_acquisitions),
            "maturity_clock_domain": "DETECTOR_ACQUISITION_OPPORTUNITIES",
            "score_semantics": "MISSING_AWARE_NORMALIZED_NONPROBABILISTIC_ASSOCIATION_SCORE",
        }


@dataclass(frozen=True)
class AssociationConfiguration:
    weight_profile: str
    thresholds: AssociationThresholdsV4

    def __post_init__(self) -> None:
        if self.weight_profile not in WEIGHT_PROFILE_GRID:
            raise ValueError("E2_V4_WEIGHT_PROFILE_NOT_IN_FROZEN_GRID")

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "weight_profile": self.weight_profile,
            "weights": dict(WEIGHT_PROFILE_GRID[self.weight_profile]),
            "thresholds": self.thresholds.to_dict(),
        }


PRECALIBRATION_CONFIGURATION = AssociationConfiguration(
    "BALANCED_INVARIANT", AssociationThresholdsV4(0.70, 0.12, 2, 2)
)


def _class_compatible(candidate: str, track: str) -> bool:
    if candidate == UNSPECIFIED or track == UNSPECIFIED:
        return True
    return candidate == track or (candidate in VEHICLE_CLASSES and track in VEHICLE_CLASSES) or (
        candidate in LANDMARK_CLASSES and track in LANDMARK_CLASSES
    )


def _area(track: TrackState) -> float:
    x0, y0, x1, y1 = track.bbox_xyxy
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _bottom_clipped_ego_hood(track: TrackState, width: int, height: int) -> bool:
    x0, _y0, x1, y1 = track.bbox_xyxy
    return y1 >= 0.98 * height and (
        _area(track) / float(max(1, width * height)) >= 0.05
        or max(0.0, x1 - x0) / float(max(1, width)) >= 0.30
    )


def _expected_rank(candidate: CandidateObjectSpec) -> Optional[int]:
    if candidate.apparent_near_far == "NEARER" or candidate.relative_longitudinal_order in {"FRONT", "FIRST"} or candidate.local_ordinal == "FIRST":
        return 1
    if candidate.apparent_near_far == "FARTHER" or candidate.relative_longitudinal_order in {"REAR", "SECOND"} or candidate.local_ordinal == "SECOND":
        return 2
    return None


def _compatibility(expected: str, observed: Optional[str]) -> Optional[float]:
    if expected == UNSPECIFIED or observed in (None, UNSPECIFIED, "UNKNOWN"):
        return None
    return 1.0 if expected == observed else 0.0


def _motion(track: TrackState) -> str:
    return "MOVING" if math.hypot(*track.velocity_proxy_xy) >= 2.0 else "PARKED"


def _image_side(track: TrackState, width: int) -> str:
    center = (track.bbox_xyxy[0] + track.bbox_xyxy[2]) / (2.0 * max(1, width))
    return "LEFT" if center < 0.4 else "RIGHT" if center > 0.6 else "CENTER"


def _normalized_score(
    components: Mapping[str, Optional[float]], mask: Sequence[str], weights: Mapping[str, float]
) -> Optional[float]:
    available = [(name, float(components[name])) for name in mask if components.get(name) is not None]
    denominator = sum(float(weights[name]) for name, _value in available)
    if denominator <= 1e-12:
        return None
    return sum(float(weights[name]) * value for name, value in available) / denominator


def score_pair_v4(
    candidate: CandidateObjectSpec,
    track: TrackState,
    *,
    family: str,
    depth_rank: int,
    image_width: int,
    image_height: int,
    invariant_observation: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    family = assert_family_selection_is_scene_independent(family)
    observation = dict(invariant_observation or {})
    gates = []
    if track.state != "ACTIVE" or track.deleted_state:
        gates.append("LINEAGE_NOT_ACTIVE")
    if not _class_compatible(candidate.object_class, track.object_class):
        gates.append("EXPLICIT_CLASS_CONTRADICTION")
    if candidate.color != UNSPECIFIED and track.color not in {candidate.color, UNSPECIFIED}:
        gates.append("EXPLICIT_COLOR_CONTRADICTION")
    if _bottom_clipped_ego_hood(track, image_width, image_height):
        gates.append("BOTTOM_CLIPPED_EGO_HOOD_OR_NON_REFERENT")
    if observation.get("lineage_compatible") is False:
        gates.append("INCOMPATIBLE_REACQUISITION")
    expected_rank = _expected_rank(candidate)
    route_side_expected = candidate.left_right_relation
    if route_side_expected == UNSPECIFIED and candidate.lane_relation in {"LEFT_LANE", "RIGHT_LANE"}:
        route_side_expected = "LEFT" if candidate.lane_relation == "LEFT_LANE" else "RIGHT"
    components: Dict[str, Optional[float]] = {
        "object_class_compatibility": None if candidate.object_class == UNSPECIFIED else (1.0 if _class_compatible(candidate.object_class, track.object_class) else 0.0),
        "color_compatibility": _compatibility(candidate.color, track.color),
        "candidate_set_depth_rank": None if expected_rank is None else (1.0 if depth_rank == expected_rank else 0.0),
        "route_frame_lateral_relation": _compatibility(route_side_expected, observation.get("route_left_right")),
        "route_lane_topology_relation": _compatibility(candidate.lane_relation, observation.get("route_lane_relation")),
        "temporal_motion_compatibility": _compatibility(candidate.parked_vs_moving, _motion(track)),
        "visibility_normalized_detector_support": max(0.0, min(1.0, float(track.latest_detector_support_score))),
        "track_persistence": max(0.0, min(1.0, float(track.hit_count) / 4.0)),
        "temporal_coherence": max(0.0, min(1.0, sum(track.coherence_ious) / len(track.coherence_ious))) if track.coherence_ious else 0.5,
    }
    profile_scores = {
        profile: _normalized_score(components, FAMILY_MASKS[family], weights)
        for profile, weights in WEIGHT_PROFILE_GRID.items()
    }
    return {
        "candidate_id": candidate.candidate_id,
        "track_id": track.track_id,
        "family": family,
        "family_mask": list(FAMILY_MASKS[family]),
        "hard_gate_pass": not gates,
        "hard_gate_reasons": gates,
        "component_scores": components,
        "component_missing_policy": "EXCLUDE_FROM_NORMALIZATION_NOT_ZERO_AND_NOT_HARD_REJECT",
        "profile_scores_before_hard_gate": profile_scores,
        "depth_rank": int(depth_rank),
        "expected_depth_rank": expected_rank,
        "observed_invariant_attributes": {
            "route_left_right": observation.get("route_left_right"),
            "route_lane_relation": observation.get("route_lane_relation"),
            "ego_longitudinal_m": observation.get("ego_longitudinal_m"),
            "ego_lateral_m": observation.get("ego_lateral_m"),
            "candidate_set_depth_rank": int(depth_rank),
            "temporal_motion_state": _motion(track),
        },
        "image_side_diagnostic_only": _image_side(track, image_width),
        "image_side_used_for_semantic_left_right": False,
    }


def associate_candidates_to_tracks_v4(
    candidates: Sequence[CandidateObjectSpec],
    tracks: Sequence[TrackState],
    *,
    family: str,
    configuration: AssociationConfiguration,
    image_width: int,
    image_height: int,
    invariant_observations: Optional[Mapping[str, Mapping[str, Any]]] = None,
    distinct_objects_required: bool = True,
) -> Mapping[str, Any]:
    family = assert_family_selection_is_scene_independent(family)
    if family in {"ORDER", "UNDERSPECIFIED_CONSTRAINT"}:
        result = {
            "schema_version": "driveclarify.e2_v4.candidate_track_matrix.v1",
            "family": family,
            "family_mask": list(FAMILY_MASKS[family]),
            "candidate_ids": [row.candidate_id for row in candidates],
            "track_ids": [],
            "matrix": {},
            "pair_details": {},
            "selected_assignment": {},
            "per_candidate": {},
            "unique_one_to_one_assignment": False,
            "qualification_reason_codes": ["FAMILY_DOES_NOT_AUTHORIZE_E2_OBJECT_ASSOCIATION"],
            "configuration": configuration.to_dict(),
        }
        result["matrix_digest"] = canonical_sha256(result)
        return result
    active = [
        row for row in tracks
        if row.state == "ACTIVE" and not row.deleted_state
        and not _bottom_clipped_ego_hood(row, image_width, image_height)
    ]
    ranks = deterministic_relative_ranks({row.track_id: _area(row) for row in active}, descending=True)
    observations = invariant_observations or {}
    candidate_ids = [row.candidate_id for row in candidates]
    track_ids = [row.track_id for row in active]
    details: Dict[str, Dict[str, Mapping[str, Any]]] = {candidate_id: {} for candidate_id in candidate_ids}
    matrix: Dict[str, Dict[str, Optional[float]]] = {candidate_id: {} for candidate_id in candidate_ids}
    for candidate in candidates:
        for track in active:
            detail = score_pair_v4(
                candidate, track, family=family, depth_rank=ranks[track.track_id],
                image_width=image_width, image_height=image_height,
                invariant_observation=observations.get(track.track_id),
            )
            details[candidate.candidate_id][track.track_id] = detail
            score = detail["profile_scores_before_hard_gate"][configuration.weight_profile]
            matrix[candidate.candidate_id][track.track_id] = score if detail["hard_gate_pass"] else None
    selected: Dict[str, str] = {}
    if candidate_ids and track_ids:
        import numpy as np
        from scipy.optimize import linear_sum_assignment

        values = np.full((len(candidate_ids), len(track_ids)), -1.0, dtype=np.float64)
        for row_index, candidate_id in enumerate(candidate_ids):
            for column_index, track_id in enumerate(track_ids):
                score = matrix[candidate_id][track_id]
                if score is not None:
                    values[row_index, column_index] = float(score)
        rows, columns = linear_sum_assignment(-values)
        for row_index, column_index in zip(rows.tolist(), columns.tolist()):
            if values[row_index, column_index] >= 0.0:
                selected[candidate_ids[row_index]] = track_ids[column_index]
    per_candidate: Dict[str, Mapping[str, Any]] = {}
    qualification = []
    for candidate_id in candidate_ids:
        ranked = sorted(
            ((float(score), track_id) for track_id, score in matrix[candidate_id].items() if score is not None),
            key=lambda row: (-row[0], row[1]),
        )
        top1 = ranked[0] if ranked else (None, None)
        top2_score = ranked[1][0] if len(ranked) >= 2 else 0.0 if ranked else None
        margin = None if top1[0] is None or top2_score is None else float(top1[0]) - float(top2_score)
        assigned = selected.get(candidate_id)
        track = next((row for row in active if row.track_id == assigned), None)
        reasons = []
        if assigned is None:
            reasons.append("ONE_TO_ONE_ASSIGNMENT_UNRESOLVED")
        if top1[1] is not None and assigned != top1[1]:
            reasons.append("GLOBAL_ASSIGNMENT_NOT_CANDIDATE_TOP1")
        if top1[0] is None or float(top1[0]) < configuration.thresholds.association_score_threshold:
            reasons.append("ASSOCIATION_SCORE_BELOW_THRESHOLD")
        if margin is None or margin < configuration.thresholds.uniqueness_margin_threshold:
            reasons.append("UNIQUENESS_MARGIN_BELOW_THRESHOLD")
        if track is None or int(track.hit_count) < configuration.thresholds.minimum_track_hits:
            reasons.append("TRACK_HITS_INSUFFICIENT")
        age = 0 if track is None else int(getattr(track, "age_acquisitions", 0))
        if age < configuration.thresholds.minimum_track_age_acquisitions:
            reasons.append("TRACK_AGE_INSUFFICIENT")
        if track is None or track.state != "ACTIVE" or track.deleted_state:
            reasons.append("LINEAGE_INVALID")
        per_candidate[candidate_id] = {
            "selected_track_id": assigned,
            "top1_track_id": top1[1],
            "top1_score": top1[0],
            "top2_score": top2_score,
            "uniqueness_margin": margin,
            "track_hits": None if track is None else track.hit_count,
            "track_age_acquisitions": age,
            "qualified": not reasons,
            "reason_codes": reasons,
        }
        qualification.extend(candidate_id + ":" + reason for reason in reasons)
    complete = bool(
        len(selected) == len(candidate_ids)
        and len(set(selected.values())) == len(candidate_ids)
        and all(row["qualified"] for row in per_candidate.values())
    )
    if distinct_objects_required and len(set(selected.values())) != len(candidate_ids):
        qualification.append("DISTINCT_CANDIDATES_NOT_ASSIGNED_TO_DISTINCT_TRACKS")
    result = {
        "schema_version": "driveclarify.e2_v4.candidate_track_matrix.v1",
        "family": family,
        "family_mask": list(FAMILY_MASKS[family]),
        "candidate_ids": candidate_ids,
        "track_ids": track_ids,
        "matrix": matrix,
        "pair_details": details,
        "selected_assignment": selected,
        "per_candidate": per_candidate,
        "distinct_objects_required": bool(distinct_objects_required),
        "unique_one_to_one_assignment": complete,
        "qualification_reason_codes": sorted(set(qualification)),
        "configuration": configuration.to_dict(),
        "score_is_probability": False,
        "score_name": "missing_aware_domain_invariant_association_score",
        "scene_id_input": False,
        "route_id_selects_parameters": False,
    }
    result["matrix_digest"] = canonical_sha256(result)
    return result


FIRST_FAILURE_PRECEDENCE = (
    "CLASS_HARD_GATE", "COLOR_HARD_GATE", "LANE_HARD_GATE",
    "LEFT_RIGHT_HARD_GATE", "MOTION_HARD_GATE", "RELATIVE_ORDER_HARD_GATE",
    "TRACK_HITS_INSUFFICIENT", "TRACK_AGE_INSUFFICIENT", "LINEAGE_INVALID",
    "ASSOCIATION_SCORE_BELOW_THRESHOLD", "UNIQUENESS_MARGIN_BELOW_THRESHOLD",
    "ONE_TO_ONE_ASSIGNMENT_UNRESOLVED", "INCOMPATIBLE_REACQUISITION",
    "PROVIDER_OR_SERIALIZATION_DEFECT", "OTHER_EXPLICIT",
)


def deterministic_first_failure(reasons: Sequence[str]) -> str:
    present = set(str(row) for row in reasons)
    for category in FIRST_FAILURE_PRECEDENCE:
        if category in present:
            return category
    return "OTHER_EXPLICIT"


__all__ = [
    "AssociationConfiguration", "AssociationThresholdsV4", "FAMILY_MASKS",
    "FIRST_FAILURE_PRECEDENCE", "PRECALIBRATION_CONFIGURATION",
    "WEIGHT_PROFILE_GRID", "associate_candidates_to_tracks_v4",
    "deterministic_first_failure", "score_pair_v4",
]
