"""StaticBranchPlanMapperV1：拓扑约束、UNKNOWN-preserving 的静态分支 mapper。"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from .topology import TOPOLOGY_SCHEMA, deterministic_sha256, verify_sha256


MAPPER_SCHEMA = "driveclarify.static_branch_plan_mapping.v1"
MAPPING_LABELS = frozenset({"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH", "NO_MATCH", "AMBIGUOUS", "UNKNOWN"})
VERIFIED_PLAN_FRAME = "EGO_LOCAL_X_FORWARD_Y_RIGHT"
VERIFIED_PLAN_UNIT = "METRE"


def _finite_pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    pair = float(value[0]), float(value[1])
    return pair if all(math.isfinite(item) for item in pair) else None


def _plan_points(value: Any) -> list[tuple[float, float]] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    points = [_finite_pair(item) for item in value]
    if any(item is None for item in points):
        return None
    return [item for item in points if item is not None]


def _unit(vector: Sequence[float]) -> tuple[float, float] | None:
    norm = math.hypot(float(vector[0]), float(vector[1]))
    if not math.isfinite(norm) or norm <= 1e-12:
        return None
    return float(vector[0]) / norm, float(vector[1]) / norm


def _world_to_ego(point: Sequence[float], location: Sequence[float], yaw_deg: float) -> tuple[float, float]:
    dx, dy = float(point[0]) - float(location[0]), float(point[1]) - float(location[1])
    yaw = math.radians(float(yaw_deg))
    c, s = math.cos(yaw), math.sin(yaw)
    return c * dx + s * dy, -s * dx + c * dy


def _project_segment(point: Sequence[float], left: Sequence[float], right: Sequence[float]) -> tuple[float, tuple[float, float], tuple[float, float]]:
    vx, vy = float(right[0]) - float(left[0]), float(right[1]) - float(left[1])
    denom = vx * vx + vy * vy
    if denom <= 1e-15:
        projection = float(left[0]), float(left[1])
        tangent = (0.0, 0.0)
    else:
        t = max(0.0, min(1.0, ((float(point[0]) - float(left[0])) * vx + (float(point[1]) - float(left[1])) * vy) / denom))
        projection = float(left[0]) + t * vx, float(left[1]) + t * vy
        tangent = _unit((vx, vy)) or (0.0, 0.0)
    return math.dist((float(point[0]), float(point[1])), projection), projection, tangent


def _nearest_projection(point: Sequence[float], polyline: Sequence[Sequence[float]]) -> tuple[float, tuple[float, float], tuple[float, float], int]:
    values = [(*_project_segment(point, left, right), index) for index, (left, right) in enumerate(zip(polyline, polyline[1:]))]
    return min(values, key=lambda item: (item[0], item[3]))


def _unknown(plan: Mapping[str, Any], reasons: Sequence[str], evidence_status: str = "INSUFFICIENT") -> dict[str, Any]:
    result = {
        "schema_version": MAPPER_SCHEMA,
        "mapper_name": "StaticBranchPlanMapperV1",
        "candidate_id": plan.get("candidate_id"),
        "mapping_label": "UNKNOWN",
        "projection_distance_m": None,
        "alignment_cosine": None,
        "branch_score": None,
        "score_margin": None,
        "per_branch_evidence": [],
        "evidence_status": evidence_status,
        "reason_codes": sorted(set(str(item) for item in reasons)),
        "candidate_id_used_for_geometry": False,
        "candidate_name_default_used": False,
        "control_authorized": False,
    }
    result["evidence_sha256"] = deterministic_sha256(result)
    return result


class StaticBranchPlanMapperV1:
    """将已验证的 ego-local metre plan 与冻结分支 topology 比较。"""

    def __init__(self, threshold_contract: Mapping[str, Any]) -> None:
        self._thresholds = copy.deepcopy(dict(threshold_contract))
        recorded = self._thresholds.get("sha256")
        unsigned = copy.deepcopy(self._thresholds)
        unsigned.pop("sha256", None)
        if not isinstance(recorded, str) or recorded != deterministic_sha256(unsigned):
            raise ValueError("MAPPING_THRESHOLD_PROVENANCE_HASH_INVALID")

    @property
    def thresholds(self) -> dict[str, Any]:
        return copy.deepcopy(self._thresholds)

    def map_plan(
        self,
        topology: Mapping[str, Any],
        plan: Mapping[str, Any],
        ego_transform: Mapping[str, Any],
    ) -> dict[str, Any]:
        frozen_topology = copy.deepcopy(dict(topology))
        frozen_plan = copy.deepcopy(dict(plan))
        frozen_transform = copy.deepcopy(dict(ego_transform))
        reasons: list[str] = []
        if frozen_topology.get("schema_version") != TOPOLOGY_SCHEMA or not verify_sha256(frozen_topology):
            reasons.append("BRANCH_TOPOLOGY_GROUND_TRUTH_INVALID")
        if frozen_plan.get("plan_frame") != VERIFIED_PLAN_FRAME:
            reasons.append("PLAN_FRAME_MISSING_OR_UNVERIFIED")
        if frozen_plan.get("plan_unit") != VERIFIED_PLAN_UNIT:
            reasons.append("PLAN_UNIT_MISSING_OR_UNVERIFIED")
        if frozen_plan.get("frame_evidence_status") != "VERIFIED":
            reasons.append("PLAN_FRAME_EVIDENCE_NOT_VERIFIED")
        if frozen_plan.get("unit_evidence_status") != "VERIFIED":
            reasons.append("PLAN_UNIT_EVIDENCE_NOT_VERIFIED")
        if frozen_plan.get("topology_sha256") != frozen_topology.get("sha256"):
            reasons.append("PLAN_TOPOLOGY_BINDING_MISMATCH")
        location = frozen_transform.get("location_xy_world_m")
        yaw = frozen_transform.get("yaw_degrees")
        if frozen_transform.get("source_frame") != "CARLA_WORLD" or frozen_transform.get("target_frame") != VERIFIED_PLAN_FRAME:
            reasons.append("EGO_TRANSFORM_FRAME_UNVERIFIED")
        if frozen_transform.get("evidence_status") != "VERIFIED":
            reasons.append("EGO_TRANSFORM_EVIDENCE_NOT_VERIFIED")
        if _finite_pair(location) is None or isinstance(yaw, bool) or not isinstance(yaw, (int, float)) or not math.isfinite(float(yaw)):
            reasons.append("EGO_TRANSFORM_VALUE_INVALID")
        points = _plan_points(frozen_plan.get("plan_points"))
        if points is None:
            reasons.append("PLAN_POINTS_MISSING_OR_INVALID")
        elif frozen_plan.get("plan_sha256") != deterministic_sha256(frozen_plan.get("plan_points")):
            reasons.append("PLAN_HASH_MISMATCH")
        if reasons:
            return _unknown(frozen_plan, reasons)

        assert points is not None and location is not None and yaw is not None
        distance_threshold = float(self._thresholds["distance_threshold_m"])
        alignment_threshold = float(self._thresholds["alignment_threshold_cosine"])
        margin_threshold = float(self._thresholds["branch_score_margin"])
        tolerance = float(self._thresholds["numerical_tolerance"])
        tail_count = int(self._thresholds["tail_point_count"])
        plan_tail = points[-min(tail_count, len(points)):]
        plan_direction = _unit((plan_tail[-1][0] - plan_tail[0][0], plan_tail[-1][1] - plan_tail[0][1]))
        if plan_direction is None:
            return _unknown(frozen_plan, ("PLAN_TAIL_DIRECTION_UNAVAILABLE",))

        per_branch: list[dict[str, Any]] = []
        branches = frozen_topology.get("branches")
        if not isinstance(branches, list) or len(branches) != 2:
            return _unknown(frozen_plan, ("BRANCH_TOPOLOGY_COUNT_NOT_TWO",))
        for branch in branches:
            if not isinstance(branch, Mapping):
                return _unknown(frozen_plan, ("BRANCH_TOPOLOGY_RECORD_INVALID",))
            role = str(branch.get("semantic_role"))
            world_polyline = branch.get("evaluation_polyline_world_xyz")
            if role not in {"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"} or not isinstance(world_polyline, list) or len(world_polyline) < 2:
                return _unknown(frozen_plan, ("BRANCH_EVALUATION_POLYLINE_INVALID",))
            local_polyline = [_world_to_ego(point, location, float(yaw)) for point in world_polyline]
            projections = [_nearest_projection(point, local_polyline) for point in plan_tail]
            mean_distance = sum(item[0] for item in projections) / len(projections)
            endpoint_distance, endpoint_projection, tangent, segment_index = projections[-1]
            alignment = plan_direction[0] * tangent[0] + plan_direction[1] * tangent[1]
            distance_quality = max(0.0, 1.0 - mean_distance / distance_threshold)
            alignment_quality = max(0.0, min(1.0, (alignment - alignment_threshold) / (1.0 - alignment_threshold)))
            score = 0.5 * distance_quality + 0.5 * alignment_quality
            eligible = mean_distance <= distance_threshold + tolerance and alignment >= alignment_threshold - tolerance
            per_branch.append({
                "semantic_role": role,
                "projection_distance_m": mean_distance,
                "endpoint_projection_distance_m": endpoint_distance,
                "endpoint_projection_ego_xy_m": list(endpoint_projection),
                "alignment_cosine": alignment,
                "branch_score": score,
                "eligible": eligible,
                "nearest_segment_index": segment_index,
            })
        per_branch.sort(key=lambda item: (-item["branch_score"], item["semantic_role"]))
        eligible = [item for item in per_branch if item["eligible"]]
        if not eligible:
            label, reason_codes, evidence_status = "NO_MATCH", ["NO_BRANCH_PASSES_DISTANCE_AND_ALIGNMENT_GATES"], "COMPLETE_NO_MATCH"
            best = per_branch[0]
            margin = per_branch[0]["branch_score"] - per_branch[1]["branch_score"]
        else:
            best = eligible[0]
            second_score = eligible[1]["branch_score"] if len(eligible) > 1 else per_branch[1]["branch_score"]
            margin = best["branch_score"] - second_score
            if len(eligible) > 1 and margin < margin_threshold - tolerance:
                label, reason_codes, evidence_status = "AMBIGUOUS", ["MULTIPLE_BRANCHES_PASS_WITH_INSUFFICIENT_SCORE_MARGIN"], "COMPLETE_AMBIGUOUS"
            else:
                label, reason_codes, evidence_status = best["semantic_role"], ["UNIQUE_TOPOLOGY_GROUNDED_BRANCH_MATCH"], "COMPLETE_MATCH"
        result = {
            "schema_version": MAPPER_SCHEMA,
            "mapper_name": "StaticBranchPlanMapperV1",
            "candidate_id": frozen_plan.get("candidate_id"),
            "mapping_label": label,
            "projection_distance_m": best["projection_distance_m"],
            "alignment_cosine": best["alignment_cosine"],
            "branch_score": best["branch_score"],
            "score_margin": margin,
            "per_branch_evidence": per_branch,
            "evidence_status": evidence_status,
            "reason_codes": reason_codes,
            "threshold_contract_sha256": self._thresholds["sha256"],
            "topology_sha256": frozen_topology["sha256"],
            "candidate_id_used_for_geometry": False,
            "candidate_name_default_used": False,
            "control_authorized": False,
        }
        if result["mapping_label"] not in MAPPING_LABELS:
            raise AssertionError("MAPPER_OUTPUT_DOMAIN_INVALID")
        result["evidence_sha256"] = deterministic_sha256(result)
        return result


def evaluate_task_pair(
    mapping_by_candidate: Mapping[str, Mapping[str, Any]],
    candidate_task_bindings: Mapping[str, str],
    task_equivalence_classes: Mapping[str, str],
) -> dict[str, Any]:
    """由实际 mapping × 独立 Task binding 生成 RQ2 pair label。"""

    statuses: dict[str, str] = {}
    mapped_roles: dict[str, str] = {}
    reasons: list[str] = []
    for candidate_id in sorted(candidate_task_bindings):
        mapping = mapping_by_candidate.get(candidate_id)
        target = candidate_task_bindings[candidate_id]
        if not isinstance(mapping, Mapping):
            statuses[candidate_id] = "UNKNOWN"
            reasons.append("MAPPING_MISSING:" + candidate_id)
            continue
        label = str(mapping.get("mapping_label"))
        if label not in {"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"}:
            statuses[candidate_id] = "UNKNOWN"
            reasons.append("MAPPING_NOT_KNOWN:" + candidate_id)
            continue
        mapped_roles[candidate_id] = label
        statuses[candidate_id] = "PASS" if label == target else "FAIL"
    if len(statuses) != 2 or "UNKNOWN" in statuses.values() or len(mapped_roles) != 2:
        pair_class = "UNKNOWN"
        reasons.append("PAIR_REQUIRED_EVIDENCE_UNKNOWN")
    else:
        classes = [task_equivalence_classes.get(mapped_roles[candidate_id]) for candidate_id in sorted(mapped_roles)]
        if any(item is None for item in classes):
            pair_class = "UNKNOWN"
            reasons.append("TASK_EQUIVALENCE_CLASS_MISSING")
        elif classes[0] == classes[1]:
            pair_class = "TASK_EQUIVALENT"
            reasons.append("MAPPED_OUTCOMES_SHARE_TASK_EQUIVALENCE_CLASS")
        else:
            pair_class = "TASK_CRITICAL"
            reasons.append("MAPPED_OUTCOMES_HAVE_DISTINCT_TASK_EQUIVALENCE_CLASSES")
    result = {
        "schema_version": "driveclarify.static_branch_task_pair.v1",
        "candidate_task_status": statuses,
        "mapped_branches": mapped_roles,
        "pair_class": pair_class,
        "reason_codes": sorted(set(reasons)),
        "candidate_binding_used": True,
        "unknown_preserved": True,
        "control_authorized": False,
    }
    result["evidence_sha256"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return result
