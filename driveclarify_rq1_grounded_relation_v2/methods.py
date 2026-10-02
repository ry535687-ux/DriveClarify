"""M0--M6 的标签盲三态判断。"""

from __future__ import annotations

import time
from typing import Any, Mapping

from .contracts import AskDecision, ContractError, MethodResult, TaskRelation, validate_method_input
from .trajectory import compare as compare_trajectories

METHODS = ("M0_ALWAYS_ASK", "M1_NEVER_ASK", "M2_GROUNDING_CONFIDENCE", "M3_TRAJECTORY_ONLY", "M4_TASK_STATE_TOPOLOGY_ONLY", "M5_DRIVECLARIFY_FULL", "M6_PRIVILEGED_TASK_SIGNATURE_REFERENCE")

# M2 本轮状态：NOT_IMPLEMENTED。理由如实记录，不用随机数或常数冒充强基线。
# 本项目当前没有可复用的冻结 grounding 打分器：既有冻结模型是 SimLingo 驾驶策略，
# 它不输出"候选指代对象的可信度分数"，只输出轨迹；而唯一能区分候选的对象级信息在
# CARLA ground-truth 位置里，那是被 §3 明确禁止的评价侧真值。若用轨迹构造分数，
# 该方法就等于 M3 而非独立的 grounding 基线。因此本轮不实现，不占据主表强基线位置。
M2_IMPLEMENTATION_STATUS = {
    "method": "M2_GROUNDING_CONFIDENCE",
    "status": "NOT_IMPLEMENTED",
    "reason_code": "NO_FROZEN_GROUNDING_SCORER_WITHOUT_ORACLE_OBJECT_TRUTH",
    "reason_zh": "无可复用冻结 grounding 打分器；唯一候选级对象信息来自被禁止的 CARLA 真值位置；用轨迹构造分数会退化为 M3。",
    "score_fabricated_from_constants_or_random": False,
    "score_constructed_from_high_low_or_correct_binding": False,
    "counted_as_strong_baseline": False,
}


def _grounding(value: Mapping[str, Any], threshold: float) -> tuple[TaskRelation, dict[str, Any]]:
    rows = value.get("grounding", {}).get("candidates", [])
    if len(rows) != 2 or any(row.get("available") is not True for row in rows):
        raise ContractError("GROUNDING_EVIDENCE_UNAVAILABLE")
    scores = [float(row["plausibility_score"]) for row in rows]
    if min(scores) < threshold:
        raise ContractError("GROUNDING_BELOW_THRESHOLD")
    identities = [str(row["grounded_identity"]) for row in rows]
    relation = TaskRelation.EQUIVALENT if identities[0] == identities[1] else TaskRelation.DIVERGENT
    return relation, {"identities_equal": identities[0] == identities[1], "minimum_plausibility_score": min(scores), "threshold": threshold}


def _topology(value: Mapping[str, Any]) -> tuple[TaskRelation, dict[str, Any]]:
    rows = value.get("runtime_context", {}).get("candidate_obligations", [])
    if len(rows) != 2 or any(row.get("available") is not True for row in rows):
        raise ContractError("RUNTIME_TASK_STATE_UNAVAILABLE")
    allowed = ("maneuver", "public_topology_target", "ordering", "constraint", "completion_predicate")
    source_kinds = {"sensor", "frozen_perception", "public_map", "parser_rule", "observation_history"}
    for row in rows:
        if any(key not in row for key in allowed):
            raise ContractError("RUNTIME_TASK_STATE_FIELD_MISSING")
        sources = row.get("field_sources")
        if not isinstance(sources, Mapping) or set(sources) != set(allowed):
            raise ContractError("RUNTIME_TASK_STATE_SOURCE_MISSING")
        if any(source not in source_kinds for source in sources.values()):
            raise ContractError("RUNTIME_TASK_STATE_SOURCE_FORBIDDEN")
    projections = [{key: row[key] for key in allowed} for row in rows]
    relation = TaskRelation.EQUIVALENT if projections[0] == projections[1] else TaskRelation.DIVERGENT
    return relation, {"projection_fields": list(allowed), "projections_equal": projections[0] == projections[1], "field_sources": [dict(row["field_sources"]) for row in rows]}


def _trajectory(value: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[TaskRelation, dict[str, Any]]:
    rows = value.get("candidate_futures", [])
    if len(rows) != 2:
        raise ContractError("CANDIDATE_FUTURES_UNAVAILABLE")
    return compare_trajectories(rows[0], rows[1], threshold_m=float(config["trajectory_threshold_m"]), horizon_s=float(config.get("trajectory_horizon_s", 2.0)), step_s=float(config.get("trajectory_step_s", 0.1)))

def _privileged(value: Mapping[str, Any] | None) -> tuple[TaskRelation, dict[str, Any]]:
    if value is None:
        raise ContractError("PRIVILEGED_INPUT_REQUIRED")
    signatures = value.get("task_signatures", [])
    if len(signatures) != 2:
        raise ContractError("PRIVILEGED_SIGNATURES_UNAVAILABLE")
    relation = TaskRelation.EQUIVALENT if signatures[0] == signatures[1] else TaskRelation.DIVERGENT
    return relation, {"signatures_equal": signatures[0] == signatures[1], "privileged_reference": True}


def _ask_for(relation: TaskRelation) -> AskDecision:
    if relation is TaskRelation.DIVERGENT:
        return AskDecision.ASK
    if relation is TaskRelation.EQUIVALENT:
        return AskDecision.NO_QUERY
    return AskDecision.UNRESOLVED


# M5 融合规则在开发期事先固定，测试期不得更改：
# 1) 任务状态拓扑投影为主证据（它是本轮唯一被证明可判定任务关系的非 oracle 证据）；
# 2) 候选未来**始终计算并记录**，作为佐证与代价计量，不因拓扑可用而跳过；
# 3) 拓扑与未来冲突时以拓扑为准，冲突如实记录，不因未来更"好看"而翻转；
# 4) 拓扑不可用时，候选未来只允许向 TASK_DIVERGENT 升级（朝询问方向 fail-closed），
#    不允许仅凭未来相近就断言等价。
FULL_FUSION_RULE = "TOPOLOGY_PRIMARY__FUTURES_ALWAYS_COMPUTED_AS_CORROBORATION__FUTURES_MAY_ONLY_ESCALATE_TO_DIVERGENT_WHEN_TOPOLOGY_UNAVAILABLE"


def _full(value: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[TaskRelation, dict[str, Any], int, int]:
    evidence: dict[str, Any] = {"fusion_rule": FULL_FUSION_RULE}
    forward_count = grounding_count = 0

    # 候选未来无条件先算：M5 声称使用候选行为证据，就必须真的前向并计入代价，
    # 不能因为拓扑先返回而从未读取候选未来（那样 M5 只是改名的 M4）。
    trajectory_relation: TaskRelation | None = None
    try:
        trajectory_relation, trajectory = _trajectory(value, config)
        evidence["trajectory"] = trajectory
        forward_count = 2
    except (ContractError, KeyError, TypeError, ValueError) as error:
        evidence["trajectory_unavailable"] = str(error)

    grounding_relation: TaskRelation | None = None
    try:
        grounding_relation, grounding = _grounding(value, float(config["grounding_threshold"]))
        evidence["grounding"] = grounding
        grounding_count = 2
    except (ContractError, KeyError, TypeError, ValueError) as error:
        evidence["grounding_unavailable"] = str(error)

    topology_relation: TaskRelation | None = None
    try:
        topology_relation, topology = _topology(value)
        evidence["topology"] = topology
    except (ContractError, KeyError, TypeError, ValueError) as error:
        evidence["topology_unavailable"] = str(error)

    evidence["depends_on"] = sorted(
        name for name, used in (
            ("runtime_task_state_topology", topology_relation is not None),
            ("candidate_future_trajectories", trajectory_relation is not None),
            ("grounding_scores", grounding_relation is not None),
        ) if used
    )
    evidence["component_relations"] = {
        "topology": None if topology_relation is None else topology_relation.value,
        "trajectory": None if trajectory_relation is None else trajectory_relation.value,
        "grounding": None if grounding_relation is None else grounding_relation.value,
    }

    if topology_relation is not None:
        evidence["decided_by"] = "runtime_task_state_topology"
        evidence["trajectory_agrees_with_topology"] = (
            None if trajectory_relation is None else trajectory_relation is topology_relation
        )
        evidence["incremental_value_of_futures_over_topology"] = "NONE_BY_FUSION_RULE_TOPOLOGY_IS_PRIMARY"
        return topology_relation, evidence, forward_count, grounding_count

    corroborations = [row for row in (trajectory_relation, grounding_relation) if row is not None]
    if len(corroborations) == 2 and corroborations[0] is corroborations[1]:
        evidence["decided_by"] = "trajectory_and_grounding_agreement_without_topology"
        return corroborations[0], evidence, forward_count, grounding_count
    if len(corroborations) >= 1 and any(row is TaskRelation.DIVERGENT for row in corroborations):
        evidence["decided_by"] = "future_escalation_to_divergent_without_topology"
        return TaskRelation.DIVERGENT, evidence, forward_count, grounding_count
    raise ContractError("FULL_EVIDENCE_INSUFFICIENT_OR_CONFLICTING")


def evaluate_method(method: str, value: Mapping[str, Any], config: Mapping[str, Any], *, privileged_input: Mapping[str, Any] | None = None) -> MethodResult:
    """统一运行一个方法；任何缺失、无效或冲突证据均闭合为 UNKNOWN。"""
    if method not in METHODS:
        raise ContractError("UNKNOWN_METHOD:" + method)
    validate_method_input(value)
    started = time.perf_counter()
    relation = TaskRelation.UNKNOWN
    evidence: dict[str, Any] = {}
    reason_codes: tuple[str, ...] = ()
    candidate_forwards = grounding_forwards = 0
    try:
        if method == "M0_ALWAYS_ASK" or method == "M1_NEVER_ASK":
            reason_codes = ("POLICY_BASELINE_NO_RELATION_INFERENCE",)
        elif method == "M2_GROUNDING_CONFIDENCE":
            # 本轮未实现：以专属 reason_code 闭合为 UNKNOWN，使其在报告中与
            # "已实现但证据不足" 的 UNKNOWN 明确区分，不冒充可运行基线。
            raise ContractError("M2_NOT_IMPLEMENTED_THIS_ROUND:" + M2_IMPLEMENTATION_STATUS["reason_code"])
        elif method == "M3_TRAJECTORY_ONLY":
            relation, evidence = _trajectory(value, config)
            candidate_forwards = 2
        elif method == "M4_TASK_STATE_TOPOLOGY_ONLY":
            relation, evidence = _topology(value)
        elif method == "M5_DRIVECLARIFY_FULL":
            relation, evidence, candidate_forwards, grounding_forwards = _full(value, config)
        else:
            relation, evidence = _privileged(privileged_input)
    except (ContractError, KeyError, TypeError, ValueError) as error:
        reason_codes = (str(error),)
        evidence = {"failed_closed": True}
        relation = TaskRelation.UNKNOWN

    ask = _ask_for(relation)
    if method == "M0_ALWAYS_ASK":
        ask = AskDecision.ASK
    elif method == "M1_NEVER_ASK":
        ask = AskDecision.NO_QUERY
    return MethodResult(method=method, sample_id=str(value["sample_id"]), relation=relation, ask=ask, reason_codes=reason_codes, evidence=evidence, inference_seconds=time.perf_counter() - started, candidate_forward_count=candidate_forwards, grounding_forward_count=grounding_forwards)

