"""Final task-evidence-first relation: insufficient task evidence remains UNKNOWN."""
from __future__ import annotations


from typing import Any, Mapping


from driveclarify.core import features as features


from driveclarify.core.contracts import ContractError, TaskRelation


from driveclarify.core.signatures import ASK_MAP, RELATION_DIVERGENT, RELATION_EQUIVALENT, RELATION_UNKNOWN, trajectory_relation, signature_relation


REVISED_C_FUSION_RULE = (
    "TOPOLOGY_PRIMARY__FUTURES_ALWAYS_COMPUTED_AS_CORROBORATION__"
    "NO_ESCALATION_WITHOUT_TASK_EVIDENCE__INSUFFICIENT_CLOSES_TO_UNKNOWN"
)


REVISED_B_FUSION_RULE = (
    "CERTIFIED_TASK_SIGNATURE_PRIMARY__TRAJECTORY_COROBORATION_ONLY__"
    "NO_ESCALATION_WITHOUT_SIGNATURE__INSUFFICIENT_CLOSES_TO_UNKNOWN"
)


def _compare_grounded_tasks(
    value: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[TaskRelation, dict[str, Any], int, int]:
    """来源 C 的修订 FULL：拓扑为主证据；任务证据不可用时闭合为 UNKNOWN。

    与 `features._full` 逐行对齐，只删除 `methods.py:209-211` 的升级分支。
    候选未来仍**无条件先算**并计入代价——修订不减少已发生的推理代价，
    否则会把代价低报成修订的附带收益。
    """
    evidence: dict[str, Any] = {"fusion_rule": REVISED_C_FUSION_RULE,
                               "revision_id": "TASK_EVIDENCE_PRIMARY"}
    forward_count = grounding_count = 0

    trajectory_relation: TaskRelation | None = None
    try:
        trajectory_relation, trajectory = features._trajectory(value, config)
        evidence["trajectory"] = trajectory
        forward_count = 2
    except (ContractError, KeyError, TypeError, ValueError) as error:
        evidence["trajectory_unavailable"] = str(error)

    grounding_relation: TaskRelation | None = None
    try:
        grounding_relation, grounding = features._grounding(value, float(config["grounding_threshold"]))
        evidence["grounding"] = grounding
        grounding_count = 2
    except (ContractError, KeyError, TypeError, ValueError) as error:
        evidence["grounding_unavailable"] = str(error)

    topology_relation: TaskRelation | None = None
    try:
        topology_relation, topology = features._topology(value)
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

    # 原实现在此处有两个无任务证据的分支：
    #   (a) 轨迹与 grounding 一致 → 采纳该一致结论；
    #   (b) 任一为 DIVERGENT → 升级为确定的 DIVERGENT。
    # 修订取消 (b)。(a) 依赖 grounding 身份证据，本轮 C 的 grounding 176/176 全不可用，
    # 故 (a) 在本数据上从未触发；保留其代码等价于不保留，如实记录而不假装修订影响了它。
    evidence["revision_effect"] = {
        "cancelled_branch": "future_escalation_to_divergent_without_topology",
        "cancelled_because": "NO_TASK_EVIDENCE_TO_SUPPORT_A_DEFINITE_RELATION",
        "trajectory_relation_that_would_have_escalated": (
            None if trajectory_relation is None else trajectory_relation.value
        ),
        "grounding_available": grounding_relation is not None,
    }
    evidence["decided_by"] = "insufficient_task_evidence_closes_to_unknown_per_eq7"
    raise features.CostBearingContractError(
        "REVISED_FULL_INSUFFICIENT_TASK_EVIDENCE_CLOSES_TO_UNKNOWN",
        candidate_forward_count=forward_count,
        grounding_forward_count=grounding_count,
    )


def compare_tasks(value: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """按 `features.evaluate_method` 的闭合语义包装修订版 M5。"""
    features.validate_method_input(value)
    try:
        relation, evidence, forwards, groundings = _compare_grounded_tasks(value, config)
        reason_codes: tuple[str, ...] = ()
    except features.CostBearingContractError as error:
        relation = TaskRelation.UNKNOWN
        evidence = {"fusion_rule": REVISED_C_FUSION_RULE, "closed_reason": str(error)}
        reason_codes = (str(error),)
        forwards, groundings = error.candidate_forward_count, error.grounding_forward_count
    except ContractError as error:
        relation = TaskRelation.UNKNOWN
        evidence = {"fusion_rule": REVISED_C_FUSION_RULE, "closed_reason": str(error)}
        reason_codes = (str(error),)
        forwards = groundings = 0
    return {
        "relation": relation.value,
        "ask_recommendation": features._ask_for(relation).value,
        "reason_codes": list(reason_codes),
        "evidence": evidence,
        "candidate_forward_count_reused_not_new": forwards,
        "grounding_forward_count": groundings,
    }


def _compare_signatures(record: Mapping[str, Any], *, threshold_m: float) -> dict[str, Any]:
    """来源 B 的修订 FULL：certified 签名为主证据；签名不可用时闭合为 UNKNOWN。

    与 `judges.b_m5_full` 逐行对齐，只删除 `judges.py:131-134` 的升级分支。
    """
    evidence: dict[str, Any] = {"fusion_rule": REVISED_B_FUSION_RULE,
                                "revision_id": "TASK_EVIDENCE_PRIMARY"}
    signature = signature_relation(record)
    trajectory = trajectory_relation(record, threshold_m=threshold_m)
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
    evidence["revision_effect"] = {
        "cancelled_branch": "trajectory_escalation_without_signature",
        "cancelled_because": "NO_TASK_EVIDENCE_TO_SUPPORT_A_DEFINITE_RELATION",
        "trajectory_relation_that_would_have_escalated": trajectory["relation"],
    }
    evidence["decided_by"] = "insufficient_task_evidence_closes_to_unknown_per_eq7"
    return {"relation": RELATION_UNKNOWN,
            "reason_codes": ["REVISED_FULL_INSUFFICIENT_TASK_EVIDENCE_CLOSES_TO_UNKNOWN"],
            "evidence": evidence, "reads_given_task_signature": True}


def compare_signature_tasks(record: Mapping[str, Any], *, threshold_m: float) -> dict[str, Any]:
    """与 `judges.run_b_method` 同形，便于并列评分。"""
    result = _compare_signatures(record, threshold_m=threshold_m)
    relation = result["relation"]
    return {**result, "ask_recommendation": ASK_MAP[relation]}


