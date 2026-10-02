"""各来源的 CPU 判断器。

来源 C 直接复用 v3 冻结实现（`driveclarify_rq1_grounded_relation_v3.methods`）。
来源 B 的 live 判断器写在 native runtime 内，本轮无法在不启动 CARLA 的前提下调用，
因此在此按 B 的 COORDINATE_TIME_CONTRACT 与实际 decision receipt 重写等价 CPU 版本，
并用 B 记录的 `trajectory_metric_m` 做逐样本保真核验（见 fidelity_check）。
不新增融合算法：M5_B 的规则取自 B 实际 receipt 表现（任务签名优先）。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

RELATION_EQUIVALENT = "TASK_EQUIVALENT"
RELATION_DIVERGENT = "TASK_DIVERGENT"
RELATION_UNKNOWN = "UNKNOWN"

ASK_RECOMMENDED = "ASK_RECOMMENDED"
NO_QUERY_NEEDED = "NO_QUERY_NEEDED"
UNRESOLVED = "UNRESOLVED"

#: 条件化询问建议映射，事前固定；这是建议，不是实际发问。
ASK_MAP = {
    RELATION_DIVERGENT: ASK_RECOMMENDED,
    RELATION_EQUIVALENT: NO_QUERY_NEEDED,
    RELATION_UNKNOWN: UNRESOLVED,
}

#: B 的融合规则，取自实际 receipt：任务签名（预给定）优先，轨迹仅作旁证。
B_FUSION_RULE = ("CERTIFIED_TASK_SIGNATURE_PRIMARY__TRAJECTORY_COROBORATION_ONLY__"
                 "TRAJECTORY_MAY_ONLY_ESCALATE_WHEN_SIGNATURE_UNAVAILABLE")


# ---------------------------------------------------------------- 来源 B

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


def b_m3_trajectory_only(record: Mapping[str, Any], *, threshold_m: float) -> dict[str, Any]:
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


def b_m4_task_signature_only(record: Mapping[str, Any]) -> dict[str, Any]:
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


def b_m5_full(record: Mapping[str, Any], *, threshold_m: float) -> dict[str, Any]:
    """B 的实际 FULL 规则：任务签名优先，轨迹仅旁证；签名不可用时轨迹可升级。"""
    evidence: dict[str, Any] = {"fusion_rule": B_FUSION_RULE}
    signature = b_m4_task_signature_only(record)
    trajectory = b_m3_trajectory_only(record, threshold_m=threshold_m)
    evidence["component_relations"] = {
        "task_signature": signature["relation"],
        "trajectory": trajectory["relation"],
    }
    evidence["signature_evidence"] = signature["evidence"]
    evidence["trajectory_evidence"] = trajectory["evidence"]
    if signature["relation"] != RELATION_UNKNOWN:
        evidence["decided_by"] = "certified_task_signature"
        evidence["trajectory_agrees_with_signature"] = (
            None if trajectory["relation"] == RELATION_UNKNOWN
            else trajectory["relation"] == signature["relation"]
        )
        evidence["incremental_value_of_futures_over_signature"] = "NONE_BY_FUSION_RULE_SIGNATURE_IS_PRIMARY"
        return {"relation": signature["relation"], "reason_codes": signature["reason_codes"],
                "evidence": evidence, "reads_given_task_signature": True}
    if trajectory["relation"] == RELATION_DIVERGENT:
        evidence["decided_by"] = "trajectory_escalation_without_signature"
        return {"relation": RELATION_DIVERGENT, "reason_codes": ["TRAJECTORY_ESCALATION_WITHOUT_SIGNATURE"],
                "evidence": evidence, "reads_given_task_signature": True}
    evidence["decided_by"] = "insufficient_evidence"
    return {"relation": RELATION_UNKNOWN, "reason_codes": ["FULL_EVIDENCE_INSUFFICIENT_OR_CONFLICTING"],
            "evidence": evidence, "reads_given_task_signature": True}


def b_m0_always_ask(_record: Mapping[str, Any]) -> dict[str, Any]:
    return {"relation": RELATION_UNKNOWN, "reason_codes": ["POLICY_BASELINE_NO_RELATION_INFERENCE"],
            "evidence": {}, "forced_ask": ASK_RECOMMENDED, "reads_given_task_signature": False}


def b_m1_never_ask(_record: Mapping[str, Any]) -> dict[str, Any]:
    return {"relation": RELATION_UNKNOWN, "reason_codes": ["POLICY_BASELINE_NO_RELATION_INFERENCE"],
            "evidence": {}, "forced_ask": NO_QUERY_NEEDED, "reads_given_task_signature": False}


B_METHODS = {
    "M0_ALWAYS_ASK": b_m0_always_ask,
    "M1_NEVER_ASK": b_m1_never_ask,
    "M3_TRAJECTORY_ONLY": b_m3_trajectory_only,
    "M4_TASK_SIGNATURE_ONLY_GIVEN": b_m4_task_signature_only,
    "M5_FULL_B_ARM_RULE": b_m5_full,
}


def run_b_method(name: str, record: Mapping[str, Any], *, threshold_m: float) -> dict[str, Any]:
    handler = B_METHODS[name]
    if name in ("M3_TRAJECTORY_ONLY", "M5_FULL_B_ARM_RULE"):
        result = handler(record, threshold_m=threshold_m)
    else:
        result = handler(record)
    relation = result["relation"]
    result["ask_recommendation"] = result.get("forced_ask") or ASK_MAP[relation]
    result["method"] = name
    return result


def fidelity_check(record: Mapping[str, Any]) -> dict[str, Any]:
    """用 B 记录的 trajectory_metric_m 核验本轮重算的一致性。"""
    recorded = record.get("recorded_decision") or {}
    metric = b_trajectory_metric(record["trajectories"])
    recorded_value = recorded.get("trajectory_metric_m")
    recomputed = metric.get("max_aligned_distance_m") if metric["valid"] else None
    if recorded_value is None or recomputed is None:
        return {"comparable": False, "recorded_metric_m": recorded_value, "recomputed_metric_m": recomputed,
                "recorded_sample_count": (recorded.get("relation_details") or {}).get("sample_count"),
                "recomputed_sample_count": metric.get("sample_count")}
    delta = abs(float(recorded_value) - float(recomputed))
    return {
        "comparable": True,
        "recorded_metric_m": float(recorded_value),
        "recomputed_metric_m": float(recomputed),
        "absolute_difference_m": delta,
        "matches_within_1e_9": delta <= 1e-9,
        "recorded_sample_count": (recorded.get("relation_details") or {}).get("sample_count"),
        "recomputed_sample_count": metric.get("sample_count"),
        "recorded_frame": recorded.get("frame"),
    }
