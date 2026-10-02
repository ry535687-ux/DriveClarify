"""独立任务关系标注 authority。

本模块位于评分侧，允许读取世界真值：冻结场景规格、分支拓扑、TASK_BINDING 的
branch_task_equivalence_classes 与候选绑定。它**不导入** methods.py、taskstructure.py
或任何被测方法，也不读取任何方法输出；因此标签不可能由 FULL 反推。

标注独立性的实现方式：本模块自带一套与方法侧无关的词法判定（_independent_direction /
_independent_ordinal），故意与 taskstructure.py 的解析器分开实现，使二者的缺陷不共享。
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

TRUTH_VALUES = ("TASK_EQUIVALENT", "TASK_DIVERGENT")

# 标注侧独立词法表：按整词匹配，与方法侧解析器分离实现。
_DIRECTION_WORDS = {
    "LEFT": ("left",),
    "RIGHT": ("right",),
    # 与方法侧同样只收真正的机动方向词；空间位置词不构成规定的驾驶方向。
    "STRAIGHT": ("straight",),
}
_ORDINAL_WORDS = {
    "FIRST": ("first", "nearest", "immediately"),
    "SECOND": ("second", "following"),
}


class LabelError(RuntimeError):
    pass


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", str(text).casefold())


def _independent_direction(text: str) -> str | None:
    words = set(_words(text))
    found = sorted(name for name, tokens in _DIRECTION_WORDS.items() if words & set(tokens))
    return found[0] if len(found) == 1 else None


def _independent_ordinal(text: str) -> str:
    words = set(_words(text))
    found = sorted(name for name, tokens in _ORDINAL_WORDS.items() if words & set(tokens))
    if len(found) == 1:
        return found[0]
    return "UNSPECIFIED"


def _equivalence_class(direction: str, task_binding: Mapping[str, Any], topology: Mapping[str, Any]) -> str:
    """把方向映射到该布局预先声明的分支任务等价类（世界真值侧）。"""
    classes = task_binding.get("branch_task_equivalence_classes")
    if not isinstance(classes, Mapping) or not classes:
        raise LabelError("BRANCH_TASK_EQUIVALENCE_CLASSES_MISSING")
    roles = {}
    for row in topology.get("branch_connections") or ():
        role = str(row.get("semantic_role") or "")
        if role.startswith("STRAIGHT"):
            roles["STRAIGHT"] = role
        elif role.startswith("RIGHT"):
            roles["RIGHT"] = role
        elif role.startswith("LEFT"):
            roles["LEFT"] = role
    role = roles.get(direction)
    if role is None:
        raise LabelError("DIRECTION_HAS_NO_BRANCH_IN_LAYOUT:" + direction)
    value = classes.get(role)
    if not value:
        raise LabelError("EQUIVALENCE_CLASS_MISSING_FOR_ROLE:" + role)
    return str(value)


def task_outcome_tuple(
    candidate_text: str,
    *,
    task_binding: Mapping[str, Any],
    topology: Mapping[str, Any],
) -> dict[str, Any]:
    """由独立场景规格与几何产生该候选规定的任务结果元组。"""
    direction = _independent_direction(candidate_text)
    if direction is None:
        raise LabelError("LABEL_DIRECTION_NOT_DETERMINED_BY_SCENARIO_SPEC")
    equivalence_class = _equivalence_class(direction, task_binding, topology)
    return {
        "direction": direction,
        "branch_task_equivalence_class": equivalence_class,
        "execution_location_ordinal": _independent_ordinal(candidate_text),
    }


def derive_truth(
    candidate_texts: Sequence[str],
    *,
    task_binding: Mapping[str, Any],
    topology: Mapping[str, Any],
    declared_config: str,
) -> dict[str, Any]:
    """产生真实任务关系；与冻结配置意图不符时登记为标注争议，不静默择一。"""
    if len(candidate_texts) != 2:
        raise LabelError("EXACTLY_TWO_CANDIDATE_TEXTS_REQUIRED")
    outcomes = [task_outcome_tuple(text, task_binding=task_binding, topology=topology) for text in candidate_texts]
    same_class = outcomes[0]["branch_task_equivalence_class"] == outcomes[1]["branch_task_equivalence_class"]
    same_location = outcomes[0]["execution_location_ordinal"] == outcomes[1]["execution_location_ordinal"]
    truth = "TASK_EQUIVALENT" if (same_class and same_location) else "TASK_DIVERGENT"
    expected = "TASK_EQUIVALENT" if declared_config == "CFG_EQUIVALENT_SAME_BRANCH" else "TASK_DIVERGENT"
    return {
        "truth": truth,
        "outcome_tuples": outcomes,
        "same_branch_task_equivalence_class": same_class,
        "same_execution_location": same_location,
        "declared_config": declared_config,
        "declared_config_expected_truth": expected,
        "annotation_dispute": truth != expected,
        "derived_from": [
            "FROZEN_SCENARIO_SPEC_TASK_BINDING_EQUIVALENCE_CLASSES",
            "PUBLIC_OPENDRIVE_DERIVED_BRANCH_TOPOLOGY",
            "INDEPENDENT_LABEL_SIDE_LEXICAL_OBLIGATION_PARSER",
        ],
        "method_output_read": False,
        "full_relation_function_called": False,
        "label_authority_version": "driveclarify.rq1_v2_label_authority.v1",
    }


def privileged_task_signatures(
    candidate_texts: Sequence[str],
    *,
    task_binding: Mapping[str, Any],
    topology: Mapping[str, Any],
) -> dict[str, Any]:
    """M6 专用：预认证任务签名。绝不进入 M0–M5 的方法输入。"""
    outcomes = [task_outcome_tuple(text, task_binding=task_binding, topology=topology) for text in candidate_texts]
    return {"task_signatures": outcomes, "privileged": True, "not_deployable_performance": True}


def label_row(
    sample: Mapping[str, Any],
    *,
    task_binding: Mapping[str, Any],
    topology: Mapping[str, Any],
    evidence_condition: str = "FULL_EVIDENCE",
    expected_decidability: str = "DECIDABLE_FROM_ALLOWED_INPUTS",
    missing_evidence: Sequence[str] = (),
) -> dict[str, Any]:
    """产生一条评分侧标签记录；与预测文件物理分离，锁定后才关联。"""
    texts = [row["text"] for row in sample["candidates"]]
    derived = derive_truth(texts, task_binding=task_binding, topology=topology, declared_config=str(sample["relation_config_id"]))
    return {
        "sample_id": str(sample["sample_id"]),
        "layout_id": str(sample["layout_id"]),
        "family": str(sample["family"]),
        "truth": derived["truth"],
        "relation_config_id": str(sample["relation_config_id"]),
        "phrasing_id": str(sample["phrasing_id"]),
        "observation_key": str(sample["observation_key"]),
        "semantic_preservation_pair_id": f"{sample['unit_id']}|{sample['relation_config_id']}|{sample['observation_key']}",
        "relation_flip_pair_id": f"{sample['unit_id']}|{sample['observation_key']}|{sample['phrasing_id']}",
        "evidence_condition": evidence_condition,
        "expected_decidability": expected_decidability,
        "missing_evidence": list(missing_evidence),
        "annotation": derived,
        "human_double_annotation_completed": False,
        "annotation_version": "AUTOMATED_LABEL_V1_PENDING_HUMAN_REVIEW",
    }
