"""M0--M6 与词法对照 M_LEX 的标签盲三态判断（v3）。

新增 M_LEX_CONTROL：只读两个候选字符串，不读观测、地图、自车状态、路线、候选未来。
它是 v2 缺陷的常设检测器——若任何一轮的 M4/M5 分数与 M_LEX 相同，即说明该轮真值
可由词法读出，主结果无 grounding 含义。M_LEX 必须始终出现在主表中。
"""

from __future__ import annotations

import re
import time
from typing import Any, Mapping

from .contracts import AskDecision, ContractError, MethodResult, TaskRelation, validate_method_input
from .trajectory import compare as compare_trajectories


class CostBearingContractError(ContractError):
    """闭合为 UNKNOWN，但把已经发生的推理代价带给调用方。

    融合规则规定候选未来无条件先算并计入代价。若证据不足时直接抛普通 ContractError，
    `evaluate_method` 的 except 分支会用初始值 0 覆盖真实前向次数，使 M5 的代价被低报。
    """

    def __init__(self, message: str, *, candidate_forward_count: int = 0, grounding_forward_count: int = 0) -> None:
        super().__init__(message)
        self.candidate_forward_count = int(candidate_forward_count)
        self.grounding_forward_count = int(grounding_forward_count)

METHODS = (
    "M0_ALWAYS_ASK",
    "M1_NEVER_ASK",
    "M2_GROUNDING_CONFIDENCE",
    "M3_TRAJECTORY_ONLY",
    "M4_TASK_STATE_TOPOLOGY_ONLY",
    "M5_DRIVECLARIFY_FULL",
    "M6_PRIVILEGED_TASK_SIGNATURE_REFERENCE",
    "M_LEX_CONTROL",
)

# M2 本轮状态：NOT_IMPLEMENTED，理由如实记录，不用随机数或常数冒充强基线。
M2_IMPLEMENTATION_STATUS = {
    "method": "M2_GROUNDING_CONFIDENCE",
    "status": "NOT_IMPLEMENTED",
    "reason_code": "NO_FROZEN_GROUNDING_SCORER_FOR_MAP_ATTRIBUTE_REFERRING_EXPRESSIONS",
    "reason_zh": ("本轮候选是公开地图属性指代表达（更长的路 / 弯度更小的连接器）。"
                  "现有冻结 SimLingo 只输出轨迹，不输出候选指代对象的可信度分数；"
                  "唯一对象级真值来自被 §3 禁止的 CARLA 真值位置。用轨迹构造分数会退化为 M3。"),
    "score_fabricated_from_constants_or_random": False,
    "score_constructed_from_high_low_or_correct_binding": False,
    "counted_as_strong_baseline": False,
}

# M_LEX 自带最小词法表，与 taskstructure/referring/labels 三处均分开书写。
_LEX_DIRECTION = {"LEFT": r"\bleft\b", "RIGHT": r"\bright\b", "STRAIGHT": r"\bstraight\b"}
_LEX_ORDINAL = {"FIRST": r"\b(first|nearest|immediately)\b", "SECOND": r"\b(second|following)\b"}
M_LEX_STATUS = {
    "method": "M_LEX_CONTROL",
    "role": "TAUTOLOGY_DETECTOR_NOT_A_SCIENTIFIC_BASELINE",
    "inputs_read": ["candidate_texts"],
    "inputs_not_read": ["observation_rgb", "public_map_topology", "ego_state", "route_context",
                        "runtime_task_state", "candidate_future_trajectories"],
    "interpretation": "若其平衡正确率与 M4/M5 相同，则该轮真值可由候选文本词法读出，主结果不具 grounding 含义。",
}


def _lex_tag(text: str, table: Mapping[str, str], default: str) -> str:
    lowered = str(text).casefold()
    hits = sorted(name for name, pattern in table.items() if re.search(pattern, lowered))
    return hits[0] if len(hits) == 1 else default


def _lexical(value: Mapping[str, Any]) -> tuple[TaskRelation, dict[str, Any]]:
    texts = [str(row["text"]) for row in value["candidates"]]
    directions = [_lex_tag(text, _LEX_DIRECTION, "UNRESOLVED") for text in texts]
    if "UNRESOLVED" in directions:
        raise ContractError("LEXICAL_DIRECTION_UNRESOLVED_BY_CANDIDATE_TEXT")
    ordinals = [_lex_tag(text, _LEX_ORDINAL, "UNSPECIFIED") for text in texts]
    equal = directions[0] == directions[1] and ordinals[0] == ordinals[1]
    return (TaskRelation.EQUIVALENT if equal else TaskRelation.DIVERGENT), {
        "lexical_directions": directions, "lexical_ordinals": ordinals,
        "read_only_candidate_text": True,
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
        unavailable = [str(row.get("reason_code")) for row in rows if row.get("available") is not True]
        raise ContractError("RUNTIME_TASK_STATE_UNAVAILABLE:" + ";".join(unavailable))
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
    return relation, {
        "projection_fields": list(allowed),
        "projections_equal": projections[0] == projections[1],
        "resolved_branch_roles": [str(row["maneuver"]) for row in rows],
        "referring_expression_ids": [str((row.get("binding_evidence") or {}).get("referring_expression_id")) for row in rows],
        "field_sources": [dict(row["field_sources"]) for row in rows],
    }


def _trajectory(value: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[TaskRelation, dict[str, Any]]:
    rows = value.get("candidate_futures", [])
    if len(rows) != 2:
        raise ContractError("CANDIDATE_FUTURES_UNAVAILABLE")
    return compare_trajectories(rows[0], rows[1], threshold_m=float(config["trajectory_threshold_m"]),
                                horizon_s=float(config.get("trajectory_horizon_s", 2.0)),
                                step_s=float(config.get("trajectory_step_s", 0.1)))


def _privileged(value: Mapping[str, Any] | None) -> tuple[TaskRelation, dict[str, Any]]:
    if value is None:
        raise ContractError("PRIVILEGED_INPUT_REQUIRED")
    signatures = value.get("task_signatures", [])
    if len(signatures) != 2:
        raise ContractError("PRIVILEGED_SIGNATURES_UNAVAILABLE")
    if any(row.get("resolved") is not True for row in signatures):
        raise ContractError("PRIVILEGED_SIGNATURE_UNRESOLVED")
    relation = TaskRelation.EQUIVALENT if signatures[0] == signatures[1] else TaskRelation.DIVERGENT
    return relation, {"signatures_equal": signatures[0] == signatures[1], "privileged_reference": True}


def _ask_for(relation: TaskRelation) -> AskDecision:
    if relation is TaskRelation.DIVERGENT:
        return AskDecision.ASK
    if relation is TaskRelation.EQUIVALENT:
        return AskDecision.NO_QUERY
    return AskDecision.UNRESOLVED


# M5 融合规则在开发期事先固定，测试期不得更改。与 v2 相同规则，作用于 v3 的新证据。
FULL_FUSION_RULE = "TOPOLOGY_PRIMARY__FUTURES_ALWAYS_COMPUTED_AS_CORROBORATION__FUTURES_MAY_ONLY_ESCALATE_TO_DIVERGENT_WHEN_TOPOLOGY_UNAVAILABLE"


def _full(value: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[TaskRelation, dict[str, Any], int, int]:
    evidence: dict[str, Any] = {"fusion_rule": FULL_FUSION_RULE}
    forward_count = grounding_count = 0

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
    # 证据不足也必须把已经发生的候选未来代价带出去：融合规则声明候选未来无条件先算并计入代价，
    # 若在此处丢掉计数，M5 的 UNKNOWN 样本会被记成零前向，代价被系统性低报。
    raise CostBearingContractError(
        "FULL_EVIDENCE_INSUFFICIENT_OR_CONFLICTING",
        candidate_forward_count=forward_count,
        grounding_forward_count=grounding_count,
    )


def evaluate_method(
    method: str,
    value: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    privileged_input: Mapping[str, Any] | None = None,
) -> MethodResult:
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
        if method in ("M0_ALWAYS_ASK", "M1_NEVER_ASK"):
            reason_codes = ("POLICY_BASELINE_NO_RELATION_INFERENCE",)
        elif method == "M2_GROUNDING_CONFIDENCE":
            raise ContractError("M2_NOT_IMPLEMENTED_THIS_ROUND:" + M2_IMPLEMENTATION_STATUS["reason_code"])
        elif method == "M_LEX_CONTROL":
            relation, evidence = _lexical(value)
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
        # 已经发生的推理代价必须保留，否则闭合为 UNKNOWN 的样本会被记成零成本。
        candidate_forwards = getattr(error, "candidate_forward_count", 0)
        grounding_forwards = getattr(error, "grounding_forward_count", 0)

    ask = _ask_for(relation)
    if method == "M0_ALWAYS_ASK":
        ask = AskDecision.ASK
    elif method == "M1_NEVER_ASK":
        ask = AskDecision.NO_QUERY
    return MethodResult(
        method=method, sample_id=str(value["sample_id"]), relation=relation, ask=ask,
        reason_codes=reason_codes, evidence=evidence,
        inference_seconds=time.perf_counter() - started,
        candidate_forward_count=candidate_forwards, grounding_forward_count=grounding_forwards,
    )
