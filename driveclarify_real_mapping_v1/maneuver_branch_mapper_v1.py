"""Deterministic metric ego-local candidate-plan to map-branch projection."""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .plan_frame_trace import SUPPORTED_PLAN_FRAME, SUPPORTED_PLAN_UNIT


MAPPING_STATUSES = frozenset(
    {"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH", "NO_MATCH", "AMBIGUOUS", "UNKNOWN"}
)


@dataclass(frozen=True)
class MappingThresholds:
    endpoint_max_distance_metres: float
    tail_mean_max_distance_metres: float
    minimum_candidate_to_branch_margin_metres: float
    tail_fraction: float
    lane_width_metres: float
    frozen_before_candidate_output: bool
    provenance: tuple[str, ...]

    @classmethod
    def town03_route_26950(cls) -> "MappingThresholds":
        lane_width = 3.5
        return cls(
            endpoint_max_distance_metres=0.75 * lane_width,
            tail_mean_max_distance_metres=0.75 * lane_width,
            minimum_candidate_to_branch_margin_metres=0.5 * lane_width,
            tail_fraction=0.5,
            lane_width_metres=lane_width,
            frozen_before_candidate_output=True,
            provenance=(
                "Town03.xodr road 30/184/213 lane -1 width a=3.5m",
                "endpoint/tail corridor=0.75*predeclared lane width",
                "separation margin=0.5*predeclared lane width",
                "not derived from candidate output",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["provenance"] = list(self.provenance)
        return value


def _point(value: Sequence[float]) -> tuple[float, float]:
    if len(value) != 2:
        raise ValueError("PLAN_POINT_REQUIRES_XY")
    point = float(value[0]), float(value[1])
    if not all(math.isfinite(item) for item in point):
        raise ValueError("PLAN_POINT_NONFINITE")
    return point


def transform_point(matrix: Sequence[Sequence[float]], point: Sequence[float]) -> tuple[float, float]:
    if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        raise ValueError("PLANAR_TRANSFORM_REQUIRES_3X3")
    x, y = _point(point)
    result_x = float(matrix[0][0]) * x + float(matrix[0][1]) * y + float(matrix[0][2])
    result_y = float(matrix[1][0]) * x + float(matrix[1][1]) * y + float(matrix[1][2])
    scale = float(matrix[2][0]) * x + float(matrix[2][1]) * y + float(matrix[2][2])
    if not math.isfinite(scale) or abs(scale) < 1e-12:
        raise ValueError("PLANAR_TRANSFORM_SCALE_INVALID")
    return result_x / scale, result_y / scale


def actor_planar_transforms(origin_world_xy: Sequence[float], yaw_degrees: float) -> dict[str, Any]:
    ox, oy = _point(origin_world_xy)
    yaw = math.radians(float(yaw_degrees))
    c, s = math.cos(yaw), math.sin(yaw)
    world_to_ego = ((c, s, -(c * ox + s * oy)), (-s, c, s * ox - c * oy), (0.0, 0.0, 1.0))
    ego_to_world = ((c, -s, ox), (s, c, oy), (0.0, 0.0, 1.0))
    return {"world_to_ego": world_to_ego, "ego_to_world": ego_to_world}


def _project_point_to_polyline(point: tuple[float, float], polyline: Sequence[Sequence[float]]) -> dict[str, float]:
    points = [_point(item) for item in polyline]
    if len(points) < 2:
        raise ValueError("BRANCH_POLYLINE_REQUIRES_TWO_POINTS")
    best: tuple[float, float, float, float] | None = None
    progress_before = 0.0
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            continue
        t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (length * length)))
        px, py = start[0] + t * dx, start[1] + t * dy
        distance = math.hypot(point[0] - px, point[1] - py)
        candidate = distance, progress_before + t * length, px, py
        if best is None or candidate[:2] < best[:2]:
            best = candidate
        progress_before += length
    if best is None:
        raise ValueError("BRANCH_POLYLINE_ZERO_LENGTH")
    return {
        "distance_metres": best[0],
        "progress_along_branch_metres": best[1],
        "projected_x": best[2],
        "projected_y": best[3],
        "branch_length_metres": progress_before,
    }


def _summary(candidate: list[tuple[float, float]], polyline: Sequence[Sequence[float]], tail_fraction: float) -> dict[str, Any]:
    projections = [_project_point_to_polyline(point, polyline) for point in candidate]
    distances = [item["distance_metres"] for item in projections]
    tail_count = max(1, int(math.ceil(len(candidate) * tail_fraction)))
    tail = distances[-tail_count:]
    ordered = sorted(distances)
    p95 = ordered[min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered))) - 1)]
    return {
        "endpoint_projection": projections[-1],
        "distance_summary_metres": {
            "mean": sum(distances) / len(distances),
            "maximum": max(distances),
            "p95": p95,
            "tail_mean": sum(tail) / len(tail),
        },
        "endpoint_progress_fraction": projections[-1]["progress_along_branch_metres"]
        / max(projections[-1]["branch_length_metres"], 1e-12),
    }


class ManeuverBranchPlanMapperV1:
    def __init__(self, thresholds: MappingThresholds | None = None) -> None:
        self.thresholds = thresholds or MappingThresholds.town03_route_26950()

    def _unknown(self, reasons: Sequence[str]) -> dict[str, Any]:
        return {
            "schema_version": "driveclarify.maneuver_branch_plan_mapper.v1",
            "status": "UNKNOWN",
            "mapping_confidence_status": "NOT_EVALUABLE",
            "candidate_to_branch_margin_metres": None,
            "branch_scores": [],
            "reason_codes": list(reasons),
            "thresholds": self.thresholds.to_dict(),
        }

    def map_plan(
        self,
        candidate_route_points: Sequence[Sequence[float]],
        *,
        plan_frame: str,
        plan_unit: str,
        branch_polylines_ego: Mapping[str, Sequence[Sequence[float]]],
        mapping_provenance: Sequence[str],
    ) -> dict[str, Any]:
        original_candidate = copy.deepcopy(candidate_route_points)
        original_branches = copy.deepcopy(branch_polylines_ego)
        if plan_frame != SUPPORTED_PLAN_FRAME:
            return self._unknown(("UNSUPPORTED_PLAN_FRAME",))
        if plan_unit != SUPPORTED_PLAN_UNIT:
            return self._unknown(("UNSUPPORTED_PLAN_UNIT",))
        if not mapping_provenance:
            return self._unknown(("MAPPING_PROVENANCE_MISSING",))
        if set(branch_polylines_ego) != {"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"}:
            return self._unknown(("BRANCH_ANCHOR_SET_INVALID",))
        try:
            candidate = [_point(item) for item in candidate_route_points]
            if len(candidate) < 2:
                raise ValueError("CANDIDATE_ROUTE_REQUIRES_TWO_POINTS")
            scores = []
            for branch_name in sorted(branch_polylines_ego):
                summary = _summary(candidate, branch_polylines_ego[branch_name], self.thresholds.tail_fraction)
                endpoint = summary["endpoint_projection"]["distance_metres"]
                tail_mean = summary["distance_summary_metres"]["tail_mean"]
                score = endpoint + tail_mean
                scores.append({"branch": branch_name, "score_metres": score, **summary})
        except (TypeError, ValueError, IndexError, ZeroDivisionError) as exc:
            return self._unknown((str(exc),))
        if candidate_route_points != original_candidate or branch_polylines_ego != original_branches:
            raise RuntimeError("MAPPER_INPUT_MUTATION_DETECTED")
        scores.sort(key=lambda item: (item["score_metres"], item["branch"]))
        best, second = scores
        margin = second["score_metres"] - best["score_metres"]
        eligible = (
            best["endpoint_projection"]["distance_metres"] <= self.thresholds.endpoint_max_distance_metres
            and best["distance_summary_metres"]["tail_mean"] <= self.thresholds.tail_mean_max_distance_metres
        )
        if not eligible:
            status, confidence, reasons = "NO_MATCH", "REJECTED", ["NO_BRANCH_WITHIN_PREDECLARED_CORRIDOR"]
        elif margin < self.thresholds.minimum_candidate_to_branch_margin_metres:
            status, confidence, reasons = "AMBIGUOUS", "AMBIGUOUS", ["CANDIDATE_TO_BRANCH_MARGIN_BELOW_PREDECLARED_MINIMUM"]
        else:
            status = best["branch"]
            confidence = "UNIQUE_GEOMETRIC_MATCH"
            reasons = ["METRIC_EGO_LOCAL_POLYLINE_PROJECTION", "NO_PHYSICAL_SAFETY_INFERENCE"]
        assert status in MAPPING_STATUSES
        return {
            "schema_version": "driveclarify.maneuver_branch_plan_mapper.v1",
            "status": status,
            "mapping_confidence_status": confidence,
            "candidate_to_branch_margin_metres": margin,
            "branch_scores": scores,
            "mapped_branch": best["branch"] if status in {"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"} else None,
            "reason_codes": reasons,
            "thresholds": self.thresholds.to_dict(),
            "mapping_provenance": list(mapping_provenance),
            "candidate_name_used": False,
            "candidate_output_used_to_define_branch_geometry": False,
            "collision_ttc_or_safety_inference_performed": False,
        }
