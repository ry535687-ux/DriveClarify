"""公开地图属性的指代表达解析（方法侧）。

v2 缺陷：候选文本直接含机动方向词（"Turn right at the intersection."），
于是 (候选文本对) → 真值 成为确定性函数，纯词法基线即达 1.0，
M4/M5 的满分不构成任何 grounding 证据。证据见
reports/driveclarify_rq1_grounded_relation_v3_20260910/diagnosis/LEXICAL_SUFFICIENCY.json。

v3 修法：候选文本只含**不指定机动方向**的指代表达，例如"通向更长那条路的出口"。
候选要求哪个分支，必须靠公开 OpenDRIVE 的实际几何/属性解析；同一文本对在不同布局
解析到相同或不同分支，因此真值随布局变化，词法不再充分。

本模块只读公开地图派生的分支属性。绝不读 TASK_BINDING 的 candidate_bindings、
真实任务关系、HIGH/LOW 或任何评价侧正确绑定。
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .contracts import ContractError

RESOLVER_VERSION = "driveclarify.rq1_v3_referring_resolver.v1"

# 指代表达 → 所依据的公开地图属性与比较方向。
# 关键设计：没有任何一项提到 left/right/straight；分支身份必须由属性值决定。
REFERRING_EXPRESSIONS: dict[str, dict[str, Any]] = {
    "R_LONGER_ROAD": {
        "attribute": "successor_length_m", "select": "max",
        "surface_forms": (
            r"\bthe exit that leads onto the longer road\b",
            r"\bthe exit onto the road that runs (?:for )?longer\b",
        ),
    },
    "R_SHORTER_ROAD": {
        "attribute": "successor_length_m", "select": "min",
        "surface_forms": (
            r"\bthe exit that leads onto the shorter road\b",
            r"\bthe exit onto the road that runs (?:for )?a shorter (?:distance|stretch)\b",
        ),
    },
    "R_STRAIGHTER_CONNECTOR": {
        "attribute": "connector_heading_change_abs_deg", "select": "min",
        "surface_forms": (
            r"\bthe exit whose connecting lane bends least\b",
            r"\bthe exit that changes your heading least\b",
        ),
    },
    "R_SHARPER_CONNECTOR": {
        "attribute": "connector_heading_change_abs_deg", "select": "max",
        "surface_forms": (
            r"\bthe exit whose connecting lane bends most\b",
            r"\bthe exit that changes your heading most\b",
        ),
    },
    # 证据不足条件专用：所依据的属性不在允许输入中（需要观测里的对象/建筑外观）。
    # 解析必然失败 → UNKNOWN，用于检验方法是否如实报告不可判定而不是瞎猜。
    "R_UNRESOLVABLE_APPEARANCE": {
        "attribute": "roadside_building_appearance", "select": "max",
        "surface_forms": (
            r"\bthe exit toward the taller building\b",
            r"\bthe exit toward the building with the brighter facade\b",
        ),
    },
}

ATTRIBUTE_SOURCES = {
    "successor_length_m": "public_map",
    "connector_heading_change_abs_deg": "public_map",
    "roadside_building_appearance": "MISSING_NOT_IN_ALLOWED_INPUTS",
}


def classify_candidate_text(text: str) -> dict[str, Any]:
    """识别候选文本使用了哪个指代表达；同时硬性拒绝机动方向词。

    含 left/right/straight 的候选在 v3 是构造错误（会重新引入 v2 的词法充分性），
    因此直接抛错而不是静默接受。
    """
    if not isinstance(text, str) or not text.strip():
        raise ContractError("CANDIDATE_TEXT_EMPTY")
    lowered = text.casefold()
    leaked = sorted(word for word in ("left", "right", "straight") if re.search(rf"\b{word}\b", lowered))
    if leaked:
        raise ContractError("CANDIDATE_TEXT_CONTAINS_MANEUVER_DIRECTION_WORD:" + ",".join(leaked))
    matched = sorted(
        name for name, spec in REFERRING_EXPRESSIONS.items()
        if any(re.search(pattern, lowered) for pattern in spec["surface_forms"])
    )
    if len(matched) != 1:
        return {"referring_expression_id": None, "resolver_version": RESOLVER_VERSION,
                "reason_code": "REFERRING_EXPRESSION_NOT_RECOGNISED" if not matched else "REFERRING_EXPRESSION_AMBIGUOUS"}
    return {"referring_expression_id": matched[0], "resolver_version": RESOLVER_VERSION, "reason_code": None}


def resolve_to_branch(text: str, branch_attributes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """把一个指代表达解析到具体分支。

    branch_attributes 每项须含 semantic_role 及本模块所需属性；由 taskstructure 从
    公开地图派生后传入。解析不唯一/属性缺失/表达不可识别时 available=False，
    由上层闭合为 UNKNOWN；绝不回退去读正确绑定。
    """
    classified = classify_candidate_text(text)
    expression_id = classified["referring_expression_id"]
    if expression_id is None:
        return {"available": False, "reason_code": classified["reason_code"], "resolver_version": RESOLVER_VERSION}
    spec = REFERRING_EXPRESSIONS[expression_id]
    attribute = spec["attribute"]
    if ATTRIBUTE_SOURCES.get(attribute) != "public_map":
        return {"available": False, "reason_code": "REFERRING_ATTRIBUTE_NOT_IN_ALLOWED_INPUTS:" + attribute,
                "referring_expression_id": expression_id, "resolver_version": RESOLVER_VERSION,
                "missing_evidence": [attribute]}
    values = [(row.get("semantic_role"), row.get(attribute)) for row in branch_attributes]
    if len(values) < 2 or any(value is None or role is None for role, value in values):
        return {"available": False, "reason_code": "BRANCH_ATTRIBUTE_UNAVAILABLE:" + attribute,
                "referring_expression_id": expression_id, "resolver_version": RESOLVER_VERSION}
    numbers = [float(value) for _, value in values]
    target = max(numbers) if spec["select"] == "max" else min(numbers)
    winners = [role for role, value in values if abs(float(value) - target) < 1e-9]
    if len(winners) != 1:
        return {"available": False, "reason_code": "REFERRING_RESOLUTION_TIED:" + attribute,
                "referring_expression_id": expression_id, "resolver_version": RESOLVER_VERSION,
                "tied_roles": sorted(winners)}
    return {
        "available": True,
        "semantic_role": winners[0],
        "referring_expression_id": expression_id,
        "attribute": attribute,
        "select": spec["select"],
        "attribute_values": {str(role): float(value) for role, value in values},
        "attribute_source": ATTRIBUTE_SOURCES[attribute],
        "resolver_version": RESOLVER_VERSION,
        "privileged_task_signature_read": False,
    }
