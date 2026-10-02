"""Prospective scene-disjoint threshold selection for E2 V3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence, Tuple

from .association import AssociationThresholds
from .contracts import canonical_sha256


ASSOCIATION_THRESHOLD_GRID = (0.65, 0.70, 0.75, 0.80)
UNIQUENESS_MARGIN_GRID = (0.10, 0.15, 0.20, 0.25)
MINIMUM_HITS_GRID = (2, 3)
MINIMUM_AGE_GRID = (5, 10)
CALIBRATOR_FAMILY = "FINITE_RULE_GRID_NO_PROBABILITY_CALIBRATOR"


def candidate_configurations() -> Tuple[AssociationThresholds, ...]:
    return tuple(
        AssociationThresholds(score, margin, hits, age)
        for score in ASSOCIATION_THRESHOLD_GRID
        for margin in UNIQUENESS_MARGIN_GRID
        for hits in MINIMUM_HITS_GRID
        for age in MINIMUM_AGE_GRID
    )


def split_is_scene_disjoint(rows: Sequence[Mapping[str, Any]]) -> bool:
    scenes_by_split = {}
    for row in rows:
        scenes_by_split.setdefault(str(row.get("split")), set()).add(str(row.get("scene_id")))
    values = list(scenes_by_split.values())
    return all(not values[left].intersection(values[right]) for left in range(len(values)) for right in range(left + 1, len(values)))


def _passes(row: Mapping[str, Any], config: AssociationThresholds) -> bool:
    return bool(
        row.get("assignment_complete") is True
        and float(row.get("minimum_association_score", -1.0)) >= config.association_score_threshold
        and float(row.get("minimum_uniqueness_margin", -1.0)) >= config.uniqueness_margin_threshold
        and int(row.get("minimum_track_hits", 0)) >= config.minimum_track_hits
        and int(row.get("minimum_track_age_frames", 0)) >= config.minimum_track_age_frames
        and not row.get("active_invalidation_events")
    )


def select_thresholds(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Require zero false unique bindings, then maximize positive-frame recall."""

    if not rows or not split_is_scene_disjoint(rows):
        raise ValueError("E2_V3_CALIBRATION_SCENE_SPLIT_NOT_DISJOINT")
    if any(str(row.get("split")) == "BLIND" for row in rows):
        raise PermissionError("E2_V3_CALIBRATION_CANNOT_READ_BLIND_OUTCOMES")
    results = []
    positive_count = sum(1 for row in rows if row.get("label") == "CORRECT_POST_REVEAL_UNIQUE")
    negative_count = len(rows) - positive_count
    if positive_count == 0 or negative_count == 0:
        raise ValueError("E2_V3_CALIBRATION_REQUIRES_POSITIVE_AND_NEGATIVE_EVIDENCE")
    for config in candidate_configurations():
        predicted = [_passes(row, config) for row in rows]
        false_unique = sum(
            1 for row, value in zip(rows, predicted)
            if value and row.get("label") != "CORRECT_POST_REVEAL_UNIQUE"
        )
        true_unique = sum(
            1 for row, value in zip(rows, predicted)
            if value and row.get("label") == "CORRECT_POST_REVEAL_UNIQUE"
        )
        false_negative = positive_count - true_unique
        precision = true_unique / float(max(1, sum(predicted)))
        recall = true_unique / float(positive_count)
        results.append({
            "configuration": config.to_dict(), "false_unique_bindings": false_unique,
            "true_unique_bindings": true_unique, "false_negative_bindings": false_negative,
            "precision": precision, "recall": recall,
            "false_association_rate": false_unique / float(negative_count),
            "abstention_rate": 1.0 - sum(predicted) / float(len(rows)),
            "eligible": false_unique == 0,
        })
    eligible = [row for row in results if row["eligible"]]
    if not eligible:
        return {
            "status": "E2_CALIBRATION_NOT_DEFENSIBLE", "selected_thresholds": None,
            "reason_codes": ["NO_ZERO_FALSE_UNIQUE_CONFIGURATION"],
            "configuration_results": results,
        }
    # Objective: recall desc; then simpler (lower score/margin, fewer hits/age);
    # final tie-break is more conservative score and margin.  Complexity is
    # constant for this family, so the deterministic conservative tuple applies.
    eligible.sort(key=lambda row: (
        -row["recall"],
        -row["configuration"]["association_score_threshold"],
        -row["configuration"]["uniqueness_margin_threshold"],
        -row["configuration"]["minimum_track_hits"],
        -row["configuration"]["minimum_track_age_frames"],
    ))
    selected = eligible[0]
    return {
        "status": "PASS_E2_V3_CALIBRATION_DEFENSIBLE",
        "calibrator_family": CALIBRATOR_FAMILY,
        "probability_calibration_claimed": False,
        "threshold_selection_objective": [
            "ZERO_FALSE_UNIQUE_ON_ALL_NEGATIVES",
            "MAXIMIZE_CORRECT_POST_REVEAL_UNIQUE_RECALL",
            "CONSERVATIVE_DETERMINISTIC_TIE_BREAK",
        ],
        "selected_thresholds": selected["configuration"],
        "selected_metrics": {key: selected[key] for key in (
            "precision", "recall", "false_association_rate", "abstention_rate",
            "false_unique_bindings", "true_unique_bindings", "false_negative_bindings",
        )},
        "scene_split_disjoint": True,
        "configuration_results_digest": canonical_sha256(results),
        "configuration_results": results,
    }


__all__ = [
    "ASSOCIATION_THRESHOLD_GRID", "CALIBRATOR_FAMILY", "MINIMUM_AGE_GRID",
    "MINIMUM_HITS_GRID", "UNIQUENESS_MARGIN_GRID", "candidate_configurations",
    "select_thresholds", "split_is_scene_disjoint",
]
