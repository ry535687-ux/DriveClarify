"""Evidence projection and aligned trajectory comparison for the final method."""
from __future__ import annotations


import re


import time


from typing import Any, Mapping


from driveclarify.core.contracts import AskDecision, ContractError, MethodResult, TaskRelation, validate_method_input


from driveclarify.core.trajectory import compare as compare_trajectories


class CostBearingContractError(ContractError):
    """闭合为 UNKNOWN，但把已经发生的推理代价带给调用方。

    融合规则规定候选未来无条件先算并计入代价。若证据不足时直接抛普通 ContractError，
    `evaluate_method` 的 except 分支会用初始值 0 覆盖真实前向次数，使 M5 的代价被低报。
    """

    def __init__(self, message: str, *, candidate_forward_count: int = 0, grounding_forward_count: int = 0) -> None:
        super().__init__(message)
        self.candidate_forward_count = int(candidate_forward_count)
        self.grounding_forward_count = int(grounding_forward_count)


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


def _ask_for(relation: TaskRelation) -> AskDecision:
    if relation is TaskRelation.DIVERGENT:
        return AskDecision.ASK
    if relation is TaskRelation.EQUIVALENT:
        return AskDecision.NO_QUERY
    return AskDecision.UNRESOLVED


