"""本轮独立离线修订副本：取消「没有任务证据支持的轨迹升级条款」。

修订动机（来自 §3.2 语义检查）：正文式 (7)（`eq:mask-unknown-cn`，
`method_cn.tex` 第 171--179 行）规定
`Insufficient(C_cmp) ⟹ R_t = Unknown`，
而冻结实现在任务证据（拓扑 / certified 签名）不可用、仅剩候选轨迹且超阈值时
给出**确定的** `TASK_DIVERGENT`。本文件只取消该升级条款，使实现与式 (7) 一致。

**这是独立离线副本，不改任何公共 live 实现。**
`driveclarify_rq1_grounded_relation_v3.methods`（含 `FULL_FUSION_RULE` 与
`methods.py:205-211` 的升级分支）与 `judges.b_m5_full` 原样保留，
两版在同一份保存输入上并列评分，覆盖率代价如实报告。

修订**只**改一件事：升级条款。不改阈值、不改投影字段、不改标签、
不改任务等价定义、不改 ASK 映射、不改代价计数。
"""

from __future__ import annotations

from typing import Any, Mapping

from driveclarify_rq1_grounded_relation_v3 import methods as v3_methods
from driveclarify_rq1_grounded_relation_v3.contracts import ContractError, TaskRelation

from .judges import (
    ASK_MAP,
    RELATION_DIVERGENT,
    RELATION_EQUIVALENT,
    RELATION_UNKNOWN,
    b_m3_trajectory_only,
    b_m4_task_signature_only,
)

# 修订后的融合规则常量。与原规则的差别只在最后一段。
REVISED_C_FUSION_RULE = (
    "TOPOLOGY_PRIMARY__FUTURES_ALWAYS_COMPUTED_AS_CORROBORATION__"
    "NO_ESCALATION_WITHOUT_TASK_EVIDENCE__INSUFFICIENT_CLOSES_TO_UNKNOWN"
)
REVISED_B_FUSION_RULE = (
    "CERTIFIED_TASK_SIGNATURE_PRIMARY__TRAJECTORY_COROBORATION_ONLY__"
    "NO_ESCALATION_WITHOUT_SIGNATURE__INSUFFICIENT_CLOSES_TO_UNKNOWN"
)

#: 任务证据 = 可判定「候选最终要完成什么任务」的证据。
#: 候选轨迹是短期行为后果，按式 (6) 属 `C_VLA`，不是任务义务 `C_obl`，故不算任务证据。
TASK_EVIDENCE_COMPONENTS = ("runtime_task_state_topology", "certified_task_signature")
CORROBORATION_ONLY_COMPONENTS = ("candidate_future_trajectories",)

REVISION_SPEC = {
    "revision_id": "RQ1_CONDITIONAL_SUPPLEMENT_20260911_NO_UNSUPPORTED_TRAJECTORY_ESCALATION_V1",
    "scope": "OFFLINE_INDEPENDENT_COPY_ONLY",
    "public_live_implementation_modified": False,
    "original_kept_side_by_side": True,
    "motivated_by": {
        "paper_clause": "eq:mask-unknown-cn",
        "paper_equation_number_in_compiled_preview": 7,
        "paper_file": "deliverables/driveclarify_method_latex_v1/method_cn.tex",
        "paper_lines": "171-179",
        "paper_subsection_in_compiled_preview": "6.3.2 同观测候选未来",
        "clause_text": "Insufficient(C_cmp) ⟹ R_t = Unknown",
    },
    "removed_branches": {
        "C_v3": {
            "location": "driveclarify_rq1_grounded_relation_v3/methods.py:209-211",
            "removed": "future_escalation_to_divergent_without_topology",
        },
        "B_offline_equivalent": {
            "location": "driveclarify_rq1_conditional_supplement/judges.py:131-134",
            "removed": "trajectory_escalation_without_signature",
        },
    },
    "deliberately_unchanged": [
        "冻结阈值（C 0.10 m / B 0.27119792945561905 m）",
        "任务结构投影字段（不含 referring_expression_id）",
        "标签与真值定义",
        "任务等价 / 分歧的语义定义",
        "ASK 映射（DIVERGENT→ASK_RECOMMENDED 等）",
        "候选未来无条件先算并计入代价的规定",
        "轨迹比较的等时网格与坐标系校验",
    ],
    "single_fixed_regression": True,
    "rule_not_retuned_for_favorable_outcome": True,
}


def c_m5_full_revised(
    value: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[TaskRelation, dict[str, Any], int, int]:
    """来源 C 的修订 FULL：拓扑为主证据；任务证据不可用时闭合为 UNKNOWN。

    与 `v3_methods._full` 逐行对齐，只删除 `methods.py:209-211` 的升级分支。
    候选未来仍**无条件先算**并计入代价——修订不减少已发生的推理代价，
    否则会把代价低报成修订的附带收益。
    """
    evidence: dict[str, Any] = {"fusion_rule": REVISED_C_FUSION_RULE,
                               "revision_id": REVISION_SPEC["revision_id"]}
    forward_count = grounding_count = 0

    trajectory_relation: TaskRelation | None = None
    try:
        trajectory_relation, trajectory = v3_methods._trajectory(value, config)
        evidence["trajectory"] = trajectory
        forward_count = 2
    except (ContractError, KeyError, TypeError, ValueError) as error:
        evidence["trajectory_unavailable"] = str(error)

    grounding_relation: TaskRelation | None = None
    try:
        grounding_relation, grounding = v3_methods._grounding(value, float(config["grounding_threshold"]))
        evidence["grounding"] = grounding
        grounding_count = 2
    except (ContractError, KeyError, TypeError, ValueError) as error:
        evidence["grounding_unavailable"] = str(error)

    topology_relation: TaskRelation | None = None
    try:
        topology_relation, topology = v3_methods._topology(value)
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
    raise v3_methods.CostBearingContractError(
        "REVISED_FULL_INSUFFICIENT_TASK_EVIDENCE_CLOSES_TO_UNKNOWN",
        candidate_forward_count=forward_count,
        grounding_forward_count=grounding_count,
    )


def evaluate_c_m5_revised(value: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """按 `v3_methods.evaluate_method` 的闭合语义包装修订版 M5。"""
    v3_methods.validate_method_input(value)
    try:
        relation, evidence, forwards, groundings = c_m5_full_revised(value, config)
        reason_codes: tuple[str, ...] = ()
    except v3_methods.CostBearingContractError as error:
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
        "ask_recommendation": v3_methods._ask_for(relation).value,
        "reason_codes": list(reason_codes),
        "evidence": evidence,
        "candidate_forward_count_reused_not_new": forwards,
        "grounding_forward_count": groundings,
    }


def b_m5_full_revised(record: Mapping[str, Any], *, threshold_m: float) -> dict[str, Any]:
    """来源 B 的修订 FULL：certified 签名为主证据；签名不可用时闭合为 UNKNOWN。

    与 `judges.b_m5_full` 逐行对齐，只删除 `judges.py:131-134` 的升级分支。
    """
    evidence: dict[str, Any] = {"fusion_rule": REVISED_B_FUSION_RULE,
                                "revision_id": REVISION_SPEC["revision_id"]}
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
    evidence["revision_effect"] = {
        "cancelled_branch": "trajectory_escalation_without_signature",
        "cancelled_because": "NO_TASK_EVIDENCE_TO_SUPPORT_A_DEFINITE_RELATION",
        "trajectory_relation_that_would_have_escalated": trajectory["relation"],
    }
    evidence["decided_by"] = "insufficient_task_evidence_closes_to_unknown_per_eq7"
    return {"relation": RELATION_UNKNOWN,
            "reason_codes": ["REVISED_FULL_INSUFFICIENT_TASK_EVIDENCE_CLOSES_TO_UNKNOWN"],
            "evidence": evidence, "reads_given_task_signature": True}


def run_b_m5_revised(record: Mapping[str, Any], *, threshold_m: float) -> dict[str, Any]:
    """与 `judges.run_b_method` 同形，便于并列评分。"""
    result = b_m5_full_revised(record, threshold_m=threshold_m)
    relation = result["relation"]
    return {**result, "ask_recommendation": ASK_MAP[relation]}


#: 修订只作用于 M5（融合规则）。M3 / M4 / M0 / M1 / M_LEX 两版完全相同，
#: 并列报告时必须明确这一点，避免把未改动的方法也算作修订的效果。
METHODS_AFFECTED_BY_REVISION = ("M5_DRIVECLARIFY_FULL",)
METHODS_IDENTICAL_ACROSS_VERSIONS = (
    "M0_ALWAYS_ASK", "M1_NEVER_ASK", "M3_TRAJECTORY_ONLY",
    "M4_TASK_STATE_TOPOLOGY_ONLY", "M_LEX_CONTROL",
)
RELATION_LABELS = (RELATION_EQUIVALENT, RELATION_DIVERGENT, RELATION_UNKNOWN)
