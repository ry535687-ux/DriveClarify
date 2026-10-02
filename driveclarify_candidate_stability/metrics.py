"""Pure-Python numeric diagnostics for recorded route and speed trajectories."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .types import (
    CandidateResult,
    DifferenceMetrics,
    StabilityClassification,
    Trajectory,
)


def raw_shape(value: Any) -> Tuple[int, ...]:
    shape: List[int] = []
    current = value
    while isinstance(current, (list, tuple)):
        shape.append(len(current))
        if not current:
            break
        current = current[0]
    return tuple(shape)


def normalize_trajectory(value: Any) -> Trajectory:
    """Normalize JSON [1,N,2] or [N,2] arrays without changing values."""

    if not isinstance(value, (list, tuple)):
        raise ValueError("TRAJECTORY_NOT_SEQUENCE")
    points = value
    if len(points) == 1 and isinstance(points[0], (list, tuple)):
        possible = points[0]
        if possible and isinstance(possible[0], (list, tuple)):
            points = possible
    normalized = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("TRAJECTORY_POINT_SHAPE")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in point):
            raise ValueError("TRAJECTORY_POINT_NOT_NUMERIC")
        normalized.append((float(point[0]), float(point[1])))
    return tuple(normalized)


def trajectory_finite(value: Trajectory) -> bool:
    return all(math.isfinite(coordinate) for point in value for coordinate in point)


def trajectory_shape(value: Trajectory) -> Tuple[int, ...]:
    return (1, len(value), 2)


def compare_trajectories(left: Trajectory, right: Trajectory) -> DifferenceMetrics:
    left_shape = trajectory_shape(left)
    right_shape = trajectory_shape(right)
    shape_equal = left_shape == right_shape
    left_finite = trajectory_finite(left)
    right_finite = trajectory_finite(right)
    if not shape_equal or not left_finite or not right_finite or not left:
        return DifferenceMetrics(
            left_shape=left_shape,
            right_shape=right_shape,
            shape_equal=shape_equal,
            left_finite=left_finite,
            right_finite=right_finite,
            coordinate_count=0,
            exact_equal=False,
            mean_absolute_difference=None,
            l2=None,
            rmse=None,
            maximum_absolute_difference=None,
            endpoint_l2=None,
            per_waypoint_l2=(),
        )

    differences: List[float] = []
    per_waypoint: List[float] = []
    for left_point, right_point in zip(left, right):
        point_differences = [
            left_point[index] - right_point[index] for index in range(2)
        ]
        differences.extend(point_differences)
        per_waypoint.append(math.sqrt(sum(item * item for item in point_differences)))
    squared_sum = sum(item * item for item in differences)
    count = len(differences)
    return DifferenceMetrics(
        left_shape=left_shape,
        right_shape=right_shape,
        shape_equal=True,
        left_finite=True,
        right_finite=True,
        coordinate_count=count,
        exact_equal=all(item == 0.0 for item in differences),
        mean_absolute_difference=sum(abs(item) for item in differences) / count,
        l2=math.sqrt(squared_sum),
        rmse=math.sqrt(squared_sum / count),
        maximum_absolute_difference=max(abs(item) for item in differences),
        endpoint_l2=per_waypoint[-1],
        per_waypoint_l2=tuple(per_waypoint),
    )


def mean_trajectory(first: Trajectory, second: Trajectory) -> Trajectory:
    if trajectory_shape(first) != trajectory_shape(second):
        raise ValueError("MEAN_TRAJECTORY_SHAPE_MISMATCH")
    return tuple(
        (
            (first_point[0] + second_point[0]) / 2.0,
            (first_point[1] + second_point[1]) / 2.0,
        )
        for first_point, second_point in zip(first, second)
    )


def _classify(
    between: float,
    within_a: float,
    within_b: float,
    epsilon: float,
    strong_ratio: float,
) -> Tuple[StabilityClassification, float, float]:
    noise = max(within_a, within_b)
    denominator = max(noise, epsilon)
    ratio = between / denominator
    margin = between - noise
    if between <= epsilon or margin <= 0.0:
        classification = StabilityClassification.NOISE_DOMINATED
    elif ratio >= strong_ratio:
        classification = StabilityClassification.STRONGLY_SEPARATED
    else:
        classification = StabilityClassification.SEPARATED_BUT_NOISY
    return classification, ratio, margin


def _pair(
    results: Mapping[str, CandidateResult],
    left: str,
    right: str,
    channel: str,
) -> DifferenceMetrics:
    return compare_trajectories(
        getattr(results[left], channel),
        getattr(results[right], channel),
    )


def analyze_candidate_set(
    candidates: Sequence[CandidateResult],
    *,
    epsilon: float,
    strong_separation_ratio: float,
) -> Dict[str, Any]:
    """Compute complete A/B repeat and separation diagnostics.

    ``between`` is deliberately the L2 distance between the two repetition
    means.  A1/B1 and A2/B2 remain separately visible and are not substituted
    for this noise-aware comparison.
    """

    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    required = {"A1", "A2", "B1", "B2"}
    if set(by_id) != required:
        missing = sorted(required - set(by_id))
        extra = sorted(set(by_id) - required)
        raise ValueError(
            "CANDIDATE_SET_LABELS:missing={}:extra={}".format(missing, extra)
        )

    output: Dict[str, Any] = {
        "definitions": {
            "within_A_route": "L2(A1.route, A2.route)",
            "within_B_route": "L2(B1.route, B2.route)",
            "within_A_speed": "L2(A1.speed, A2.speed)",
            "within_B_speed": "L2(B1.speed, B2.speed)",
            "between_route": "L2(mean(A1.route,A2.route), mean(B1.route,B2.route))",
            "between_speed": "L2(mean(A1.speed,A2.speed), mean(B1.speed,B2.speed))",
            "separation_ratio": "between / max(within_A, within_B, epsilon)",
            "margin": "between - max(within_A, within_B)",
        },
        "epsilon": epsilon,
        "strong_separation_ratio_diagnostic": strong_separation_ratio,
        "pairs": {},
        "channels": {},
    }

    for pair_name, left, right in (
        ("A1_A2", "A1", "A2"),
        ("B1_B2", "B1", "B2"),
        ("A1_B1", "A1", "B1"),
        ("A2_B2", "A2", "B2"),
    ):
        output["pairs"][pair_name] = {
            "route": _pair(by_id, left, right, "route").to_dict(),
            "speed": _pair(by_id, left, right, "speed").to_dict(),
        }

    for channel in ("route", "speed"):
        within_a_metrics = _pair(by_id, "A1", "A2", channel)
        within_b_metrics = _pair(by_id, "B1", "B2", channel)
        if within_a_metrics.l2 is None or within_b_metrics.l2 is None:
            raise ValueError("INVALID_REPEAT_METRICS:" + channel)
        mean_a = mean_trajectory(
            getattr(by_id["A1"], channel),
            getattr(by_id["A2"], channel),
        )
        mean_b = mean_trajectory(
            getattr(by_id["B1"], channel),
            getattr(by_id["B2"], channel),
        )
        between_metrics = compare_trajectories(mean_a, mean_b)
        if between_metrics.l2 is None:
            raise ValueError("INVALID_BETWEEN_METRICS:" + channel)
        classification, ratio, margin = _classify(
            between_metrics.l2,
            within_a_metrics.l2,
            within_b_metrics.l2,
            epsilon,
            strong_separation_ratio,
        )
        output["channels"][channel] = {
            "within_A": within_a_metrics.l2,
            "within_B": within_b_metrics.l2,
            "between": between_metrics.l2,
            "between_mean_metrics": between_metrics.to_dict(),
            "separation_ratio": ratio,
            "margin": margin,
            "classification": classification.value,
        }

    output["language"] = {
        "decoded_outputs": {
            candidate.candidate_id: list(candidate.language_output)
            for candidate in candidates
        },
        "all_decoded_equal": len(
            {candidate.language_output for candidate in candidates}
        )
        == 1,
        "token_ids_recorded": all(
            candidate.language_token_ids is not None for candidate in candidates
        ),
        "token_equality": (
            len({candidate.language_token_ids for candidate in candidates}) == 1
            if all(candidate.language_token_ids is not None for candidate in candidates)
            else None
        ),
        "token_equality_unavailable_reason": (
            None
            if all(candidate.language_token_ids is not None for candidate in candidates)
            else "psplan13 persisted decoded language strings but not generated token IDs"
        ),
    }
    output["metadata"] = {
        "route_shapes": {
            candidate.candidate_id: list(trajectory_shape(candidate.route))
            for candidate in candidates
        },
        "speed_shapes": {
            candidate.candidate_id: list(trajectory_shape(candidate.speed))
            for candidate in candidates
        },
        "normalized_dtypes": {
            candidate.candidate_id: candidate.normalized_dtype
            for candidate in candidates
        },
        "all_finite": all(
            trajectory_finite(candidate.route) and trajectory_finite(candidate.speed)
            for candidate in candidates
        ),
        "candidate_order": [candidate.candidate_id for candidate in candidates],
    }
    return output
