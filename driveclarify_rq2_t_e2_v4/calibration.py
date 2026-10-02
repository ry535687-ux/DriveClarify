"""Scene-level, blind-firewalled finite calibration for E2 V4."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .association import (
    WEIGHT_PROFILE_GRID,
    AssociationConfiguration,
    AssociationThresholdsV4,
)
from .contracts import canonical_sha256


ASSOCIATION_THRESHOLD_GRID = (0.66, 0.70, 0.74, 0.78)
UNIQUENESS_MARGIN_GRID = (0.08, 0.12, 0.16, 0.20)
MINIMUM_HITS_GRID = (2, 3, 4)
MINIMUM_AGE_ACQUISITIONS_GRID = (2, 3, 4)
STOPPING_RULE = "ONE_EXHAUSTIVE_EVALUATION_OF_FROZEN_SCENE_LEVEL_FINITE_GRID"


def candidate_configurations() -> tuple[AssociationConfiguration, ...]:
    return tuple(
        AssociationConfiguration(profile, AssociationThresholdsV4(score, margin, hits, age))
        for profile in WEIGHT_PROFILE_GRID
        for score in ASSOCIATION_THRESHOLD_GRID
        for margin in UNIQUENESS_MARGIN_GRID
        for hits in MINIMUM_HITS_GRID
        for age in MINIMUM_AGE_ACQUISITIONS_GRID
    )


def assert_scene_level_split(
    calibration_scene_ids: Sequence[str], excluded_scene_ids: Sequence[str]
) -> Mapping[str, Any]:
    calibration = [str(row) for row in calibration_scene_ids]
    excluded = {str(row) for row in excluded_scene_ids}
    if len(set(calibration)) != len(calibration):
        raise ValueError("E2_V4_CALIBRATION_SCENE_DUPLICATE")
    overlap = sorted(set(calibration).intersection(excluded))
    if overlap:
        raise ValueError("E2_V4_CALIBRATION_SCENE_SPLIT_NOT_DISJOINT:" + ",".join(overlap))
    return {
        "split_unit": "SCENE_ROUTE_ACTOR_CONFIGURATION",
        "calibration_scene_count": len(calibration),
        "excluded_scene_count": len(excluded),
        "overlap": [],
        "scene_level_disjoint": True,
        "frame_level_split_used": False,
    }


def _passes(row: Mapping[str, Any], config: AssociationConfiguration) -> bool:
    profiles = row.get("profiles") or {}
    metrics = profiles.get(config.weight_profile) or {}
    thresholds = config.thresholds
    return bool(
        metrics.get("assignment_complete") is True
        and float(metrics.get("minimum_association_score", -1.0)) >= thresholds.association_score_threshold
        and float(metrics.get("minimum_uniqueness_margin", -1.0)) >= thresholds.uniqueness_margin_threshold
        and int(row.get("minimum_track_hits", 0)) >= thresholds.minimum_track_hits
        and int(row.get("minimum_track_age_acquisitions", 0)) >= thresholds.minimum_track_age_acquisitions
        and not row.get("active_invalidation_events")
    )


def select_configuration(
    rows: Sequence[Mapping[str, Any]],
    *,
    calibration_scene_ids: Sequence[str],
    excluded_scene_ids: Sequence[str],
    blind_outcomes: Any = None,
) -> Mapping[str, Any]:
    if blind_outcomes is not None:
        raise PermissionError("E2_V4_CALIBRATION_CANNOT_READ_FRESH_BLIND_OUTCOMES")
    split = assert_scene_level_split(calibration_scene_ids, excluded_scene_ids)
    if not rows:
        raise ValueError("E2_V4_CALIBRATION_ROWS_EMPTY")
    row_scenes = {str(row.get("scene_id")) for row in rows}
    if row_scenes != set(str(row) for row in calibration_scene_ids):
        raise ValueError("E2_V4_CALIBRATION_ROW_SCENE_SET_MISMATCH")
    positive = sum(1 for row in rows if row.get("label") == "CORRECT_POST_REVEAL_UNIQUE")
    negative = len(rows) - positive
    if positive == 0 or negative == 0:
        raise ValueError("E2_V4_CALIBRATION_REQUIRES_POSITIVE_AND_NEGATIVE_ROWS")
    results = []
    for config in candidate_configurations():
        predicted = [_passes(row, config) for row in rows]
        true_unique = sum(1 for row, value in zip(rows, predicted) if value and row.get("label") == "CORRECT_POST_REVEAL_UNIQUE")
        false_unique = sum(1 for row, value in zip(rows, predicted) if value and row.get("label") != "CORRECT_POST_REVEAL_UNIQUE")
        predicted_count = sum(predicted)
        results.append({
            "configuration": config.to_dict(),
            "true_unique_bindings": true_unique,
            "false_unique_bindings": false_unique,
            "false_negative_bindings": positive - true_unique,
            "precision": true_unique / float(max(1, predicted_count)),
            "recall": true_unique / float(positive),
            "abstention_rate": 1.0 - predicted_count / float(len(rows)),
            "eligible": false_unique == 0,
        })
    eligible = [row for row in results if row["eligible"] and row["recall"] > 0.0]
    if not eligible:
        return {
            "status": "E2_V4_CALIBRATION_NOT_DEFENSIBLE",
            "selected_configuration": None,
            "scene_split_proof": split,
            "reason_codes": ["ZERO_FALSE_BINDING_AND_USEFUL_RECALL_CANNOT_COEXIST"],
            "configuration_results_digest": canonical_sha256(results),
            "configuration_results": results,
        }
    complexity = {"RANK_DOMINANT_SIMPLE": 0, "BALANCED_INVARIANT": 1, "SEMANTIC_CONSERVATIVE": 2}
    eligible.sort(key=lambda row: (
        -row["recall"],
        row["abstention_rate"],
        complexity[row["configuration"]["weight_profile"]],
        -row["configuration"]["thresholds"]["association_score_threshold"],
        -row["configuration"]["thresholds"]["uniqueness_margin_threshold"],
        -row["configuration"]["thresholds"]["minimum_track_hits"],
        -row["configuration"]["thresholds"]["minimum_track_age_acquisitions"],
    ))
    selected = eligible[0]
    return {
        "status": "PASS_E2_V4_CALIBRATION_DEFENSIBLE",
        "selection_objective": [
            "ZERO_FALSE_UNIQUE_BINDINGS_PRE_REVEAL_NONREVEAL_WRONG_PAIR_UNRESOLVED",
            "MAXIMIZE_CORRECT_POST_REVEAL_UNIQUE_RECALL",
            "LOWER_ABSTENTION",
            "SIMPLER_REPRESENTATION_FEWER_ACTIVE_COMPONENTS",
            "MORE_CONSERVATIVE_THRESHOLD",
        ],
        "stopping_rule": STOPPING_RULE,
        "scene_split_proof": split,
        "selected_configuration": selected["configuration"],
        "selected_metrics": {key: selected[key] for key in (
            "precision", "recall", "abstention_rate", "false_unique_bindings",
            "true_unique_bindings", "false_negative_bindings",
        )},
        "configuration_results_digest": canonical_sha256(results),
        "configuration_results": results,
    }


__all__ = [
    "ASSOCIATION_THRESHOLD_GRID", "MINIMUM_AGE_ACQUISITIONS_GRID",
    "MINIMUM_HITS_GRID", "STOPPING_RULE", "UNIQUENESS_MARGIN_GRID",
    "assert_scene_level_split", "candidate_configurations", "select_configuration",
]
