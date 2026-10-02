"""Pure-Python statistics for the lightweight candidate sensitivity pilot."""

from __future__ import annotations

import itertools
import math
import statistics
from typing import Any, Dict, List, Mapping, Sequence, Tuple


PROTOCOL = "DRIVECLARIFY_CANDIDATE_SENSITIVITY_PILOT_V1"
FINAL_PASS = "PASS_CANDIDATE_SENSITIVITY_SIGNAL_IDENTIFIED"
FINAL_NO_SIGNAL = "BLOCKED_CANDIDATE_SIGNAL_NOT_IDENTIFIABLE_OVER_REPEAT_NOISE"
FINAL_PARTIAL = "BLOCKED_PARTIAL_CANDIDATE_SIGNAL_ONLY"


def _trajectory(value: Any) -> Tuple[Tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("SENSITIVITY_TRAJECTORY_NOT_SEQUENCE")
    points = value
    if (
        len(points) == 1
        and isinstance(points[0], (list, tuple))
        and points[0]
        and isinstance(points[0][0], (list, tuple))
    ):
        points = points[0]
    output = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("SENSITIVITY_TRAJECTORY_POINT_SHAPE")
        coordinates = []
        for coordinate in point:
            if isinstance(coordinate, bool) or not isinstance(
                coordinate, (int, float)
            ):
                raise ValueError("SENSITIVITY_TRAJECTORY_NON_NUMERIC")
            numeric = float(coordinate)
            if not math.isfinite(numeric):
                raise ValueError("SENSITIVITY_TRAJECTORY_NON_FINITE")
            coordinates.append(numeric)
        output.append((coordinates[0], coordinates[1]))
    if not output:
        raise ValueError("SENSITIVITY_TRAJECTORY_EMPTY")
    return tuple(output)


def pair_metrics(left: Any, right: Any) -> Dict[str, Any]:
    """Return every requested distance statistic for one trajectory pair."""

    left_value = _trajectory(left)
    right_value = _trajectory(right)
    if len(left_value) != len(right_value):
        raise ValueError("SENSITIVITY_TRAJECTORY_SHAPE_MISMATCH")
    differences: List[float] = []
    waypoint_l2: List[float] = []
    for left_point, right_point in zip(left_value, right_value):
        point_diff = (
            left_point[0] - right_point[0],
            left_point[1] - right_point[1],
        )
        differences.extend(point_diff)
        waypoint_l2.append(math.sqrt(sum(item * item for item in point_diff)))
    squared_sum = sum(item * item for item in differences)
    count = len(differences)
    return {
        "mad": sum(abs(item) for item in differences) / count,
        "mad_definition": "MEAN_ABSOLUTE_DIFFERENCE",
        "rmse": math.sqrt(squared_sum / count),
        "flattened_l2": math.sqrt(squared_sum),
        "maximum_absolute_difference": max(abs(item) for item in differences),
        "per_waypoint_l2": waypoint_l2,
        "mean_waypoint_l2": statistics.mean(waypoint_l2),
        "max_waypoint_l2": max(waypoint_l2),
        "endpoint_l2": waypoint_l2[-1],
    }


def _summary(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        raise ValueError("SENSITIVITY_SUMMARY_EMPTY")
    return {
        "min": min(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def _centroid(values: Sequence[Any]) -> List[List[float]]:
    normalized = [_trajectory(value) for value in values]
    length = len(normalized[0])
    if any(len(item) != length for item in normalized):
        raise ValueError("SENSITIVITY_CENTROID_SHAPE_MISMATCH")
    return [
        [
            statistics.mean(item[index][0] for item in normalized),
            statistics.mean(item[index][1] for item in normalized),
        ]
        for index in range(length)
    ]


def _candidate_outliers(
    records: Sequence[Mapping[str, Any]],
    *,
    channel: str,
    epsilon: float,
) -> Dict[str, Any]:
    output: Dict[str, Any] = {"groups": {}, "candidate_ids": []}
    for group in ("A", "B"):
        group_records = [
            item for item in records if item["interpretation_id"] == group
        ]
        center = _centroid([item[channel] for item in group_records])
        distances = {
            item["candidate_id"]: pair_metrics(item[channel], center)[
                "flattened_l2"
            ]
            for item in group_records
        }
        median_distance = statistics.median(distances.values())
        median_absolute_deviation = statistics.median(
            abs(value - median_distance) for value in distances.values()
        )
        threshold = median_distance + 3.0 * max(
            median_absolute_deviation, epsilon
        )
        flagged = [
            candidate_id
            for candidate_id, value in distances.items()
            if value > threshold and value > median_distance + epsilon
        ]
        output["groups"][group] = {
            "candidate_to_group_centroid_l2": distances,
            "median_distance": median_distance,
            "median_absolute_deviation": median_absolute_deviation,
            "robust_threshold": threshold,
            "flagged": flagged,
        }
        output["candidate_ids"].extend(flagged)
    output["candidate_ids"].sort()
    output["present"] = bool(output["candidate_ids"])
    return output


def _channel_metrics(
    records: Sequence[Mapping[str, Any]],
    *,
    channel: str,
    epsilon: float,
    include_outliers: bool,
) -> Dict[str, Any]:
    groups = {
        group: [
            item for item in records if item["interpretation_id"] == group
        ]
        for group in ("A", "B")
    }
    if len(groups["A"]) < 2 or len(groups["B"]) < 2:
        raise ValueError("SENSITIVITY_GROUP_COUNT_INSUFFICIENT")
    pair_records: Dict[str, Dict[str, Any]] = {}
    within_values: Dict[str, List[float]] = {"A": [], "B": []}
    for group in ("A", "B"):
        for left, right in itertools.combinations(groups[group], 2):
            name = left["candidate_id"] + "-" + right["candidate_id"]
            metrics = pair_metrics(left[channel], right[channel])
            pair_records[name] = metrics
            within_values[group].append(metrics["flattened_l2"])
    between_values = []
    for left in groups["A"]:
        for right in groups["B"]:
            name = left["candidate_id"] + "-" + right["candidate_id"]
            metrics = pair_metrics(left[channel], right[channel])
            pair_records[name] = metrics
            between_values.append(metrics["flattened_l2"])
    within_all = within_values["A"] + within_values["B"]
    within_a = _summary(within_values["A"])
    within_b = _summary(within_values["B"])
    within_summary = _summary(within_all)
    between = _summary(between_values)
    centroid_a = _centroid([item[channel] for item in groups["A"]])
    centroid_b = _centroid([item[channel] for item in groups["B"]])
    centroid_metrics = pair_metrics(centroid_a, centroid_b)
    conservative_ratio = between["min"] / max(within_summary["max"], epsilon)
    median_ratio = between["median"] / max(within_summary["median"], epsilon)
    conservative_margin = between["min"] - within_summary["max"]
    output = {
        "pair_metrics": pair_records,
        "within_A_min": within_a["min"],
        "within_A_mean": within_a["mean"],
        "within_A_median": within_a["median"],
        "within_A_max": within_a["max"],
        "within_B_min": within_b["min"],
        "within_B_mean": within_b["mean"],
        "within_B_median": within_b["median"],
        "within_B_max": within_b["max"],
        "within_all_mean": within_summary["mean"],
        "within_all_median": within_summary["median"],
        "within_all_max": within_summary["max"],
        "between_min": between["min"],
        "between_mean": between["mean"],
        "between_median": between["median"],
        "between_max": between["max"],
        "centroid_distance": centroid_metrics["flattened_l2"],
        "centroid_metrics": centroid_metrics,
        "conservative_ratio": conservative_ratio,
        "median_ratio": median_ratio,
        "conservative_margin": conservative_margin,
        "clearly_separated": (
            conservative_ratio > 1.0 and conservative_margin > 0.0
        ),
        "strongly_separated": (
            conservative_ratio >= 2.0 and conservative_margin > 0.0
        ),
        "clearly_not_separated": (
            conservative_ratio < 0.75 and median_ratio <= 1.0
        ),
        "boundary_ratio": 0.75 <= conservative_ratio <= 1.25,
        "conservative_median_conflict": (
            (conservative_ratio > 1.0 and conservative_margin > 0.0)
            != (median_ratio > 1.0)
        ),
    }
    if include_outliers:
        output["outliers"] = _candidate_outliers(
            records, channel=channel, epsilon=epsilon
        )
    return output


def _leave_one_out_robust(
    records: Sequence[Mapping[str, Any]],
    *,
    channel: str,
    epsilon: float,
) -> Dict[str, Any]:
    cases = {}
    for removed in records:
        retained = [
            item
            for item in records
            if item["candidate_id"] != removed["candidate_id"]
        ]
        if len(
            [
                item
                for item in retained
                if item["interpretation_id"] == removed["interpretation_id"]
            ]
        ) < 2:
            continue
        metrics = _channel_metrics(
            retained,
            channel=channel,
            epsilon=epsilon,
            include_outliers=False,
        )
        cases[removed["candidate_id"]] = {
            "conservative_ratio": metrics["conservative_ratio"],
            "conservative_margin": metrics["conservative_margin"],
            "still_separated": metrics["clearly_separated"],
        }
    return {
        "cases": cases,
        "all_still_separated": bool(cases)
        and all(item["still_separated"] for item in cases.values()),
    }


def analyze_sensitivity(
    records: Sequence[Mapping[str, Any]],
    *,
    epsilon: float = 1e-12,
) -> Dict[str, Any]:
    """Analyze an exact A/B three- or five-repeat pilot batch."""

    candidate_ids = [item["candidate_id"] for item in records]
    group_counts = {
        group: len(
            [item for item in records if item["interpretation_id"] == group]
        )
        for group in ("A", "B")
    }
    if group_counts["A"] != group_counts["B"] or group_counts["A"] not in (3, 5):
        raise ValueError("SENSITIVITY_REPEAT_COUNT_MUST_BE_THREE_OR_FIVE")
    expected = [
        "{}{}".format(group, repetition)
        for repetition in range(1, group_counts["A"] + 1)
        for group in ("A", "B")
    ]
    if candidate_ids != expected:
        raise ValueError("SENSITIVITY_CANDIDATE_ORDER_INVALID")
    channels = {
        channel: _channel_metrics(
            records,
            channel=channel,
            epsilon=epsilon,
            include_outliers=True,
        )
        for channel in ("route", "speed")
    }
    repeat_count = group_counts["A"]
    automatic_continuation = False
    candidate_signal_identified = False
    if repeat_count == 3:
        clean = all(
            not channels[channel]["boundary_ratio"]
            and not channels[channel]["conservative_median_conflict"]
            and not channels[channel]["outliers"]["present"]
            for channel in ("route", "speed")
        )
        both_separated = all(
            channels[channel]["clearly_separated"]
            for channel in ("route", "speed")
        )
        both_not_separated = all(
            channels[channel]["clearly_not_separated"]
            for channel in ("route", "speed")
        )
        if clean and both_separated:
            final_status = FINAL_PASS
            candidate_signal_identified = True
        elif clean and both_not_separated:
            final_status = FINAL_NO_SIGNAL
        else:
            final_status = "BOUNDARY_CONTINUE_TO_FIVE_REPEATS"
            automatic_continuation = True
    else:
        for channel in ("route", "speed"):
            channels[channel]["leave_one_out"] = _leave_one_out_robust(
                records,
                channel=channel,
                epsilon=epsilon,
            )
            channels[channel]["final_channel_signal"] = (
                channels[channel]["clearly_separated"]
                and channels[channel]["centroid_distance"]
                > channels[channel]["within_all_median"]
                and not channels[channel]["outliers"]["present"]
                and channels[channel]["leave_one_out"]["all_still_separated"]
            )
        route_signal = channels["route"]["final_channel_signal"]
        speed_signal = channels["speed"]["final_channel_signal"]
        candidate_signal_identified = route_signal and speed_signal
        if candidate_signal_identified:
            final_status = FINAL_PASS
        elif route_signal != speed_signal:
            final_status = FINAL_PARTIAL
        else:
            final_status = FINAL_NO_SIGNAL
    return {
        "protocol": PROTOCOL,
        "candidate_order": candidate_ids,
        "repeat_count_per_group": repeat_count,
        "epsilon": epsilon,
        "channels": channels,
        "outlier_candidates": sorted(
            set(channels["route"]["outliers"]["candidate_ids"])
            | set(channels["speed"]["outliers"]["candidate_ids"])
        ),
        "candidate_signal_identified": candidate_signal_identified,
        "final_status": final_status,
        "automatic_continuation": automatic_continuation,
    }
