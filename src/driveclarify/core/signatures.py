"""Certified task signature and trajectory evidence helpers."""
from __future__ import annotations


import math


from typing import Any, Mapping, Sequence

ASK_RECOMMENDED = "ASK_RECOMMENDED"
NO_QUERY_NEEDED = "NO_QUERY_NEEDED"
UNRESOLVED = "UNRESOLVED"


RELATION_EQUIVALENT = "TASK_EQUIVALENT"


RELATION_DIVERGENT = "TASK_DIVERGENT"


RELATION_UNKNOWN = "UNKNOWN"


ASK_MAP = {
    RELATION_DIVERGENT: ASK_RECOMMENDED,
    RELATION_EQUIVALENT: NO_QUERY_NEEDED,
    RELATION_UNKNOWN: UNRESOLVED,
}


def b_trajectory_metric(trajectories: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """B 的等时间样本最大 L2。B 保存 9 个 0.25s 等时样本，按原协议直接逐样本比对。"""
    if len(trajectories) != 2:
        return {"valid": False, "reason": "CANDIDATE_TRAJECTORY_COUNT_NOT_TWO"}
    first, second = trajectories
    for row in (first, second):
        if row.get("valid") is not True:
            return {"valid": False, "reason": "TRAJECTORY_INVALID"}
        if row.get("coordinate_frame") != "CARLA_EGO_X_FORWARD_Y_RIGHT_METRES":
            return {"valid": False, "reason": "TRAJECTORY_COORDINATE_FRAME_INVALID"}
    if first.get("nonlanguage_context_sha256") != second.get("nonlanguage_context_sha256"):
        return {"valid": False, "reason": "TRAJECTORY_CONTEXT_MISMATCH"}
    if first.get("source_frame") != second.get("source_frame"):
        return {"valid": False, "reason": "TRAJECTORY_SOURCE_FRAME_MISMATCH"}
    times_a = [float(v) for v in first.get("relative_times_s", [])]
    times_b = [float(v) for v in second.get("relative_times_s", [])]
    if times_a != times_b or len(times_a) < 2:
        return {"valid": False, "reason": "TRAJECTORY_TIME_GRID_MISMATCH"}
    points_a = [tuple(float(v) for v in p) for p in first.get("xy_m", [])]
    points_b = [tuple(float(v) for v in p) for p in second.get("xy_m", [])]
    if len(points_a) != len(times_a) or len(points_b) != len(times_b):
        return {"valid": False, "reason": "TRAJECTORY_SHAPE_INVALID"}
    distances = [math.dist(a, b) for a, b in zip(points_a, points_b)]
    return {
        "valid": True,
        "max_aligned_distance_m": max(distances),
        "per_sample_distances_m": distances,
        "sample_count": len(distances),
        "horizon_s": times_a[-1],
        "sample_period_s": round(times_a[1] - times_a[0], 10),
    }


def trajectory_relation(record: Mapping[str, Any], *, threshold_m: float) -> dict[str, Any]:
    metric = b_trajectory_metric(record["trajectories"])
    if not metric["valid"]:
        return {"relation": RELATION_UNKNOWN, "reason_codes": [metric["reason"]], "evidence": metric,
                "reads_given_task_signature": False}
    relation = RELATION_DIVERGENT if metric["max_aligned_distance_m"] > threshold_m else RELATION_EQUIVALENT
    return {
        "relation": relation,
        "reason_codes": ["SHORT_TRAJECTORY_THRESHOLD"],
        "evidence": {**metric, "threshold_m": threshold_m},
        "reads_given_task_signature": False,
    }


def signature_relation(record: Mapping[str, Any]) -> dict[str, Any]:
    """只读预给定的 certified task signature，不读候选未来。

    注意：`task_signatures` 在 v3 契约中被列为答案字段，本方法因此不是自主恢复
    任务关系的能力证据，只能作为给定任务结构下的规则验证。
    """
    signatures = record.get("given_task_signatures") or []
    if len(signatures) != 2 or any(row.get("certified") is not True for row in signatures):
        return {"relation": RELATION_UNKNOWN, "reason_codes": ["CERTIFIED_TASK_SIGNATURE_UNAVAILABLE"],
                "evidence": {"signature_count": len(signatures)}, "reads_given_task_signature": True}
    components = sorted({component for row in signatures for component in row.get("relevant_components", [])})
    if not components:
        return {"relation": RELATION_UNKNOWN, "reason_codes": ["TASK_SIGNATURE_COMPONENTS_MISSING"],
                "evidence": {}, "reads_given_task_signature": True}
    projections = [{key: row.get(key) for key in components} for row in signatures]
    equal = projections[0] == projections[1]
    differing = sorted(key for key in components if projections[0].get(key) != projections[1].get(key))
    return {
        "relation": RELATION_EQUIVALENT if equal else RELATION_DIVERGENT,
        "reason_codes": ["CERTIFIED_TASK_OBLIGATION_MATCHES"] if equal else ["CERTIFIED_TASK_OBLIGATION_DIFFERS"],
        "evidence": {"compared_components": components, "differing_components": differing,
                     "projections_equal": equal,
                     "certificate_ids": [row.get("certificate_id") for row in signatures]},
        "reads_given_task_signature": True,
    }
