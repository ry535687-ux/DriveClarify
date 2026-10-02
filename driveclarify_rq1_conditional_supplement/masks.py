"""输入缺失压力测试：先定协议，再批量评分。

这些变体是**离线输入缺失变体**，不是新场景、真实视觉遮挡、真实驾驶或新增独立样本。
真实任务标签不因屏蔽观测证据而改变。

每个变体都要清理派生字段与缓存键，避免被移除的信息从旁路读回。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

MASK_FULL = "FULL_INPUT"
MASK_NO_FUTURES = "NO_CANDIDATE_FUTURES"
MASK_NO_TASK_STRUCTURE = "NO_TASK_STRUCTURE_EVIDENCE"
MASK_NEITHER = "NO_FUTURES_AND_NO_TASK_STRUCTURE"

MASK_ORDER = (MASK_FULL, MASK_NO_FUTURES, MASK_NO_TASK_STRUCTURE, MASK_NEITHER)

#: 缺失一律用明确无效状态 + 原因，不填零值。
UNAVAILABLE_REASONS = {
    MASK_NO_FUTURES: "OFFLINE_MASK_CANDIDATE_FUTURE_TRAJECTORIES_WITHHELD",
    MASK_NO_TASK_STRUCTURE: "OFFLINE_MASK_RUNTIME_TASK_STRUCTURE_AND_PUBLIC_TOPOLOGY_WITHHELD",
}

#: 各证据组一并清理的派生字段与缓存旁路，逐项在协议中列出。
FUTURE_DERIVED_FIELDS = (
    "candidate_futures",
    "candidate_future_evidence",  # 含 geometric_paths / speed_plan_hash / cache_keys
)
TASK_STRUCTURE_DERIVED_FIELDS = (
    "runtime_context.candidate_obligations",       # 含 binding_evidence.attribute_values
    "runtime_context.public_branch_attributes",    # successor_length_m / 弯度
)

#: C 的真值由公开地图属性（后继道路长度 / 连接器弯度）确定。
#: 因此「决定性证据」= 任务结构证据组；候选未来不是决定性证据。
#: 这一需求由标签契约独立规定，不以 FULL 自身输出为准。
DECISIVE_EVIDENCE_GROUP_FOR_C = "TASK_STRUCTURE_AND_PUBLIC_TOPOLOGY"
DECISIVE_EVIDENCE_SPEC_C = {
    "label_decided_by": "PUBLIC_MAP_ATTRIBUTE__successor_length_m__OR__connector_heading_change_abs_deg",
    "decisive_group": DECISIVE_EVIDENCE_GROUP_FOR_C,
    "candidate_futures_are_decisive": False,
    "authority": "INPUT_AND_LABEL_CONTRACT + LABEL_PROVENANCE_AND_REVIEW (来源 C 原轮)",
    "not_determined_by_full_method_output": True,
}

#: 在哪些变体下，独立规定的决定性证据已被移除。
DECISIVE_EVIDENCE_REMOVED = {
    MASK_FULL: False,
    MASK_NO_FUTURES: False,
    MASK_NO_TASK_STRUCTURE: True,
    MASK_NEITHER: True,
}


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason_code": reason, "masked_offline": True}


def apply_mask_c(method_input: Mapping[str, Any], variant: str) -> dict[str, Any]:
    """对来源 C 的一份方法输入施加缺失变体。同一变体对所有方法一致。"""
    if variant not in MASK_ORDER:
        raise ValueError(f"UNKNOWN_MASK_VARIANT:{variant}")
    value = copy.deepcopy(dict(method_input))
    applied: list[str] = []

    if variant in (MASK_NO_FUTURES, MASK_NEITHER):
        reason = UNAVAILABLE_REASONS[MASK_NO_FUTURES]
        # 轨迹组：整组置为空 + 明确无效状态，派生几何路径/缓存键一并清除。
        value["candidate_futures"] = []
        value["candidate_future_evidence"] = _unavailable(reason)
        applied.extend(FUTURE_DERIVED_FIELDS)

    if variant in (MASK_NO_TASK_STRUCTURE, MASK_NEITHER):
        reason = UNAVAILABLE_REASONS[MASK_NO_TASK_STRUCTURE]
        runtime = dict(value.get("runtime_context") or {})
        # 每个候选义务置为不可用并带原因；binding_evidence 中的属性值一并移除。
        runtime["candidate_obligations"] = [
            _unavailable(reason) for _ in (runtime.get("candidate_obligations") or [])
        ]
        runtime["public_branch_attributes"] = _unavailable(reason)
        value["runtime_context"] = runtime
        applied.extend(TASK_STRUCTURE_DERIVED_FIELDS)

    value["offline_mask_variant"] = variant
    value["offline_mask_applied_fields"] = applied
    return value


def mask_receipt() -> dict[str, Any]:
    """先保存的 mask 协议回执。"""
    return {
        "schema": "driveclarify.rq1_conditional_supplement.mask_protocol.v1",
        "variants": list(MASK_ORDER),
        "variant_semantics": {
            MASK_FULL: "原完整输入，未改动",
            MASK_NO_FUTURES: "候选轨迹不可用（含 geometric_paths / cache_keys 等派生与缓存旁路）",
            MASK_NO_TASK_STRUCTURE: "任务结构与公开拓扑证据组不可用（含 binding_evidence.attribute_values）",
            MASK_NEITHER: "上述两组均不可用",
        },
        "missing_representation": "明确 available=false + reason_code，不填零值",
        "same_variant_applied_to_all_methods": True,
        "true_label_unchanged_by_masking": True,
        "variant_is_not_new_scene_or_occlusion_or_driving": True,
        "variant_adds_independent_samples": False,
        "derived_and_cache_fields_cleared": {
            MASK_NO_FUTURES: list(FUTURE_DERIVED_FIELDS),
            MASK_NO_TASK_STRUCTURE: list(TASK_STRUCTURE_DERIVED_FIELDS),
        },
        "decisive_evidence_spec": DECISIVE_EVIDENCE_SPEC_C,
        "decisive_evidence_removed_by_variant": dict(DECISIVE_EVIDENCE_REMOVED),
        "rule_note": ("不规定任何字段缺失都必须 UNKNOWN。若剩余合法证据仍足以确定关系，"
                      "确定判断仍属合理；只有在独立规定的决定性证据被移除时，"
                      "确定判断才计入『证据不足仍确定』。"),
    }


#: 各变体施加后必须整体消失的字段名（递归查找键名）。
FORBIDDEN_KEYS_AFTER_MASK = {
    MASK_NO_FUTURES: ("xy_m", "relative_times_s", "geometric_paths", "arc_length_m",
                      "speed_plan_hash", "route_plan_hash", "candidate_cache_key", "cache_keys"),
    MASK_NO_TASK_STRUCTURE: ("maneuver", "public_topology_target", "completion_predicate",
                             "binding_evidence", "attribute_values", "successor_length_m",
                             "connector_heading_change_abs_deg", "field_sources", "ordering"),
}


def _all_keys(value: Any, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(value, Mapping):
        for key, child in value.items():
            found.add(str(key))
            _all_keys(child, found)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _all_keys(child, found)
    return found


#: 证据来源分类标签（field_sources 的取值域）。这些是「这一字段来自哪类来源」的
#: 分类名，不含任何关于哪条分支更长的信息，且同名子串会出现在 `public_map_sha256`
#: 这类无关键名中，因此不作为泄漏 token。
SOURCE_KIND_TAXONOMY = frozenset({
    "sensor", "frozen_perception", "public_map", "parser_rule", "observation_history",
})

#: 解析器/版本标识同样是实现名，不含答案信息。
IMPLEMENTATION_LABEL_PREFIXES = ("driveclarify.", "resolver_version")


def _distinctive_strings(value: Any, found: set[str] | None = None) -> set[str]:
    """收集足够有辨识度的字符串标记，用于 token 级泄漏检查。

    排除：纯数字、证据来源分类标签、实现/版本标识。这些都不携带
    「哪条分支更长 / 两候选是否同一目标」的信息。
    """
    found = set() if found is None else found
    if isinstance(value, Mapping):
        for child in value.values():
            _distinctive_strings(child, found)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _distinctive_strings(child, found)
    elif isinstance(value, str) and len(value) >= 6:
        if value in SOURCE_KIND_TAXONOMY:
            return found
        if value.startswith(IMPLEMENTATION_LABEL_PREFIXES):
            return found
        try:
            float(value)
        except ValueError:
            found.add(value)
    return found


def _token_present(token: str, text: str) -> bool:
    """按 JSON 字符串字面量整体匹配，避免命中更长键名/值的子串。"""
    import json as _json

    return _json.dumps(token, ensure_ascii=False) in text


def verify_mask_removed_information(original: Mapping[str, Any], masked: Mapping[str, Any],
                                    variant: str) -> dict[str, Any]:
    """核验被移除的信息确实不再出现在 masked 输入中（防旁路读回）。

    采用结构化核验：
    1. 被移除证据组的字段名必须在整份输入中递归消失；
    2. 该组独有的有辨识度字符串标记（如 `BRANCH_ROLE:STRAIGHT_BRANCH`、
       `junction:26:connector_road:41:lane:1`）必须不再出现。

    不使用数值子串匹配：`STRAIGHT_BRANCH` 的弯度属性值本身就是 `0.0`，
    该子串会命中每一个轨迹坐标与时间戳，产生纯粹的假阳性。
    数值是否泄漏由「字段名是否消失」这一更强的结构条件覆盖。
    """
    import json

    findings: dict[str, Any] = {"variant": variant, "leaks": [], "method": "STRUCTURAL_KEY_AND_TOKEN"}
    if variant == MASK_FULL:
        findings["clean"] = True
        findings["note"] = "原完整输入，无需移除任何证据"
        return findings

    present_keys = _all_keys(masked)
    text = json.dumps(masked, ensure_ascii=False, sort_keys=True)

    groups = []
    if variant in (MASK_NO_FUTURES, MASK_NEITHER):
        groups.append((MASK_NO_FUTURES, ("candidate_futures", "candidate_future_evidence")))
    if variant in (MASK_NO_TASK_STRUCTURE, MASK_NEITHER):
        groups.append((MASK_NO_TASK_STRUCTURE, None))

    for group, _ in groups:
        for key in FORBIDDEN_KEYS_AFTER_MASK[group]:
            if key in present_keys:
                findings["leaks"].append(f"key_present:{key}")

    # token 级：该组独有字符串必须消失
    if variant in (MASK_NO_TASK_STRUCTURE, MASK_NEITHER):
        runtime = (original.get("runtime_context") or {})
        removed_tokens: set[str] = set()
        removed_tokens |= _distinctive_strings(runtime.get("candidate_obligations"))
        removed_tokens |= _distinctive_strings(runtime.get("public_branch_attributes"))
        # 允许仍出现在别处的合法公共标记（候选文本、观测哈希等）不算泄漏
        allowed = _distinctive_strings({
            "candidates": original.get("candidates"),
            "observation": original.get("observation"),
            "route_context": original.get("route_context"),
            "ego_state": original.get("ego_state"),
            "raw_instruction": original.get("raw_instruction"),
        })
        for token in sorted(removed_tokens - allowed):
            if _token_present(token, text):
                findings["leaks"].append(f"token_present:{token[:60]}")

    if variant in (MASK_NO_FUTURES, MASK_NEITHER):
        if masked.get("candidate_futures"):
            findings["leaks"].append("candidate_futures_not_empty")
        evidence = masked.get("candidate_future_evidence")
        if not isinstance(evidence, Mapping) or evidence.get("available") is not False:
            findings["leaks"].append("candidate_future_evidence_not_marked_unavailable")

    if variant in (MASK_NO_TASK_STRUCTURE, MASK_NEITHER):
        runtime = masked.get("runtime_context") or {}
        obligations = runtime.get("candidate_obligations") or []
        if any(row.get("available") is not False for row in obligations):
            findings["leaks"].append("obligation_not_marked_unavailable")
        attributes = runtime.get("public_branch_attributes")
        if not isinstance(attributes, Mapping) or attributes.get("available") is not False:
            findings["leaks"].append("public_branch_attributes_not_marked_unavailable")

    findings["leaks"] = sorted(set(findings["leaks"]))
    findings["clean"] = not findings["leaks"]
    findings["numeric_substring_matching_deliberately_not_used"] = (
        "STRAIGHT_BRANCH 的弯度属性值为 0.0，数值子串会命中全部轨迹坐标，"
        "属假阳性；改用字段名递归消失 + 组内独有字符串 token 两项结构条件。")
    return findings
