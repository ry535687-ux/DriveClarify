"""硬可行性与 D_G/D_S/D_R/D_I 结构化后果比较。"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from .models import DivergenceResult, TriValue
from .three_valued import tri_or


GOAL_FIELDS = (
    "task.target_road_id",
    "task.target_lane_id",
    "task.target_exit_id",
    "task.goal_region_match",
    "task.wrong_goal",
    "task.missed_turn_or_exit",
)
SAFETY_FIELDS = ("safety.safety_class",)
RULE_FIELDS = ("rule.severe_rule_conflict",)
IRREVERSIBILITY_FIELDS = (
    "irreversibility.recoverability_class",
    "irreversibility.crossed_branch_boundary",
    "irreversibility.crossed_solid_line",
    "irreversibility.crossed_exit_gore",
    "irreversibility.other_interpretations_still_reachable",
)


def _get(record: dict[str, Any], dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        value = value[part]
    return value


def _is_unknown(value: Any) -> bool:
    return value is None or value == "UNKNOWN"


def _group_divergence(records: list[dict[str, Any]], fields: tuple[str, ...]) -> tuple[TriValue, tuple[str, ...]]:
    unknown = tuple(
        sorted(
            {
                field
                for record in records
                for field in fields
                if _is_unknown(_get(record, field))
            }
        )
    )
    if unknown:
        return TriValue.UNKNOWN, unknown
    for left, right in combinations(records, 2):
        if any(_get(left, field) != _get(right, field) for field in fields):
            return TriValue.TRUE, ()
    return TriValue.FALSE, ()


def hard_feasibility(record: dict[str, Any]) -> tuple[TriValue, str]:
    """先处理 UNKNOWN，再排除已知不安全和已知严重违规候选。"""

    safety_class = record["safety"]["safety_class"]
    severe_rule = record["rule"]["severe_rule_conflict"]
    if safety_class == "UNKNOWN":
        return TriValue.UNKNOWN, "UNKNOWN_HARD_SAFETY"
    if severe_rule is None:
        return TriValue.UNKNOWN, "UNKNOWN_HARD_RULE"
    if safety_class == "UNSAFE":
        return TriValue.FALSE, "KNOWN_UNSAFE_CANDIDATE"
    if severe_rule is True:
        return TriValue.FALSE, "KNOWN_SEVERE_RULE_CONFLICT"
    return TriValue.TRUE, "HARD_FEASIBLE"


def first_unknown_hard_reason(records: list[dict[str, Any]]) -> tuple[str | None, tuple[str, ...]]:
    for record in sorted(records, key=lambda item: item["candidate_id"]):
        candidate_id = record["candidate_id"]
        if record["safety"]["safety_class"] == "UNKNOWN":
            return "UNKNOWN_HARD_SAFETY", (f"{candidate_id}.safety.safety_class",)
        if record["rule"]["severe_rule_conflict"] is None:
            return "UNKNOWN_HARD_RULE", (f"{candidate_id}.rule.severe_rule_conflict",)
    return None, ()


def compare_consequences(records: list[dict[str, Any]]) -> DivergenceResult:
    """比较后果字段；路径 L2/ADE/FDE 从未进入这些判定。"""

    if not records:
        raise ValueError("at least one consequence is required")
    fields_used = GOAL_FIELDS + SAFETY_FIELDS + RULE_FIELDS + IRREVERSIBILITY_FIELDS
    if len(records) == 1:
        unknown = tuple(field for field in fields_used if _is_unknown(_get(records[0], field)))
        value = TriValue.UNKNOWN if unknown else TriValue.FALSE
        equivalent = TriValue.UNKNOWN if unknown else TriValue.TRUE
        return DivergenceResult(
            value,
            value,
            value,
            value,
            value,
            equivalent,
            fields_used,
            unknown,
        )

    d_g, u_g = _group_divergence(records, GOAL_FIELDS)
    d_s, u_s = _group_divergence(records, SAFETY_FIELDS)
    d_r, u_r = _group_divergence(records, RULE_FIELDS)
    d_i, u_i = _group_divergence(records, IRREVERSIBILITY_FIELDS)
    critical = tri_or((d_g, d_s, d_r, d_i))
    unknown = tuple(sorted(set(u_g + u_s + u_r + u_i)))
    equivalent = (
        TriValue.TRUE
        if critical is TriValue.FALSE
        else TriValue.UNKNOWN
        if critical is TriValue.UNKNOWN
        else TriValue.FALSE
    )
    return DivergenceResult(
        d_g,
        d_s,
        d_r,
        d_i,
        critical,
        equivalent,
        fields_used,
        unknown,
    )


def deterministic_tie_break(records: list[dict[str, Any]]) -> str:
    """硬门全部通过后，仅执行最后一层确定性 ID tie-break。"""
    if not records:
        raise ValueError("cannot tie-break an empty candidate set")
    return min(record["candidate_id"] for record in records)
