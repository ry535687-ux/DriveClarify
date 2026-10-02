"""独立任务关系标注 authority（v3）。

本模块位于评分侧。它**不导入** methods.py、taskstructure.py、referring.py 或任何被测
方法，也不读取任何方法输出；因此标签不可能由 FULL 反推。

独立性的实现方式：本模块自带一套与方法侧完全分开写的指代解析实现
(`_independent_resolve`)，直接从 OpenDRIVE 重新量取几何，不复用 taskstructure 的
`branch_attributes`，也不复用 referring 的表达表。二者的缺陷因此不共享。

必须如实声明的机制重叠：方法侧 M4/M5 与本标注侧都从同一份公开 OpenDRIVE 计算
"哪条后继路更长 / 哪个连接器弯度更小"。这是本轮设计的性质——真值被定义为允许输入
的函数，方法原则上可以答对。因此 M4/M5 的高分表示其解析实现正确，
**不表示视觉场景 grounding 能力**。该重叠在 INDEPENDENCE_AUDIT 中逐项列出。
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

TRUTH_VALUES = ("TASK_EQUIVALENT", "TASK_DIVERGENT")
LABEL_AUTHORITY_VERSION = "driveclarify.rq1_v3_label_authority.v1"

# 标注侧自带的表面形式 → 语义义务表。与 dataset/_SURFACE 和 referring/REFERRING_EXPRESSIONS
# 分开书写；此处按语义描述而非正则表复述，故意不共享实现。
_LABEL_SIDE_EXPRESSIONS = (
    ("EXTENT_MAX", ("longer road", "runs longer")),
    ("EXTENT_MIN", ("shorter road", "shorter distance")),
    ("CURVATURE_MIN", ("bends least", "heading least")),
    ("CURVATURE_MAX", ("bends most", "heading most")),
    ("APPEARANCE_NOT_IN_MAP", ("taller building", "brighter facade")),
)
_ORDINAL_WORDS = {"FIRST": ("first", "nearest", "immediately"), "SECOND": ("second", "following")}
_LABEL_MAP_CACHE: dict[str, ET.Element] = {}


class LabelError(RuntimeError):
    pass


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", str(text).casefold())


def _label_side_expression(text: str) -> str | None:
    lowered = str(text).casefold()
    hits = sorted(name for name, phrases in _LABEL_SIDE_EXPRESSIONS if any(phrase in lowered for phrase in phrases))
    return hits[0] if len(hits) == 1 else None


def _independent_ordinal(text: str) -> str:
    words = set(_words(text))
    found = sorted(name for name, tokens in _ORDINAL_WORDS.items() if words & set(tokens))
    return found[0] if len(found) == 1 else "UNSPECIFIED"


def _label_map_root(path: str) -> ET.Element:
    if path not in _LABEL_MAP_CACHE:
        _LABEL_MAP_CACHE[path] = ET.parse(path).getroot()
    return _LABEL_MAP_CACHE[path]


def _measure_branches(topology: Mapping[str, Any]) -> list[dict[str, Any]]:
    """标注侧独立量取分支几何。实现与 taskstructure.branch_attributes 分开书写。"""
    identity = topology.get("map_identity") or {}
    path = str(identity.get("opendrive_path") or "")
    if not path or not Path(path).is_file():
        raise LabelError("LABEL_SIDE_OPENDRIVE_UNAVAILABLE")
    root = _label_map_root(path)
    roads = {road.get("id"): road for road in root.findall("road")}
    rows: list[dict[str, Any]] = []
    for connection in topology.get("branch_connections") or ():
        role = str(connection.get("semantic_role") or "")
        successor = roads.get(str(connection.get("successor_road_id")))
        connector = roads.get(str(connection.get("connecting_road_id")))
        extent = None if successor is None else float(successor.get("length", 0.0))
        curvature = None
        if connector is not None:
            # 标注侧用 geometry 元素的 hdg 首尾差量取转向幅度；方法侧用车道中心线采样。
            # 两种量法不同实现、不同数据路径，同一几何事实。
            geometries = connector.findall("planView/geometry")
            if geometries:
                first = float(geometries[0].get("hdg", 0.0))
                last_element = geometries[-1]
                last = float(last_element.get("hdg", 0.0))
                arc = last_element.find("arc")
                if arc is not None:
                    last += float(arc.get("curvature", 0.0)) * float(last_element.get("length", 0.0))
                delta = math.degrees(last - first)
                while delta > 180.0:
                    delta -= 360.0
                while delta < -180.0:
                    delta += 360.0
                curvature = abs(delta)
        rows.append({"semantic_role": role, "extent_m": extent, "curvature_abs_deg": curvature})
    if len(rows) < 2:
        raise LabelError("LABEL_SIDE_INSUFFICIENT_BRANCHES")
    return rows


def _independent_resolve(text: str, branches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """标注侧独立解析：指代表达 → 分支 semantic_role。"""
    expression = _label_side_expression(text)
    if expression is None:
        return {"resolved": False, "reason": "LABEL_SIDE_EXPRESSION_NOT_RECOGNISED"}
    if expression == "APPEARANCE_NOT_IN_MAP":
        return {"resolved": False, "reason": "LABEL_SIDE_ATTRIBUTE_NOT_DERIVABLE_FROM_MAP",
                "expression": expression, "requires_evidence": ["roadside_building_appearance"]}
    field = "extent_m" if expression.startswith("EXTENT") else "curvature_abs_deg"
    pick_max = expression.endswith("MAX")
    values = [(row["semantic_role"], row.get(field)) for row in branches]
    if any(value is None for _, value in values):
        return {"resolved": False, "reason": "LABEL_SIDE_ATTRIBUTE_MISSING:" + field, "expression": expression}
    numbers = [float(value) for _, value in values]
    target = max(numbers) if pick_max else min(numbers)
    winners = [role for role, value in values if abs(float(value) - target) < 1e-9]
    if len(winners) != 1:
        return {"resolved": False, "reason": "LABEL_SIDE_TIED:" + field, "expression": expression}
    return {"resolved": True, "semantic_role": winners[0], "expression": expression, "attribute": field,
            "attribute_values": {str(role): float(value) for role, value in values}}


def derive_truth(
    candidate_texts: Sequence[str],
    *,
    topology: Mapping[str, Any],
    declared_config: str,
) -> dict[str, Any]:
    """产生真实任务关系。

    两候选都解析成功时，真值 = (分支等价类, 执行位置) 元组是否相等。
    任一候选在允许输入下无法解析（如需要建筑外观）时，该样本的真值标记为
    LABEL_UNDEFINED_UNDER_ALLOWED_INPUTS，进入证据不足子集，不进入主评价总体。
    """
    if len(candidate_texts) != 2:
        raise LabelError("EXACTLY_TWO_CANDIDATE_TEXTS_REQUIRED")
    branches = _measure_branches(topology)
    resolutions = [_independent_resolve(text, branches) for text in candidate_texts]
    ordinals = [_independent_ordinal(text) for text in candidate_texts]

    if not all(row.get("resolved") for row in resolutions):
        return {
            "truth": None,
            "truth_status": "LABEL_UNDEFINED_UNDER_ALLOWED_INPUTS",
            "resolutions": resolutions,
            "execution_location_ordinals": ordinals,
            "declared_config": declared_config,
            "annotation_dispute": False,
            "unresolvable_reasons": [row.get("reason") for row in resolutions if not row.get("resolved")],
            "derived_from": ["PUBLIC_OPENDRIVE_INDEPENDENT_LABEL_SIDE_MEASUREMENT",
                             "LABEL_SIDE_INDEPENDENT_REFERRING_RESOLUTION"],
            "method_output_read": False,
            "full_relation_function_called": False,
            "label_authority_version": LABEL_AUTHORITY_VERSION,
        }

    same_branch = resolutions[0]["semantic_role"] == resolutions[1]["semantic_role"]
    same_location = ordinals[0] == ordinals[1]
    truth = "TASK_EQUIVALENT" if (same_branch and same_location) else "TASK_DIVERGENT"
    return {
        "truth": truth,
        "truth_status": "DEFINED",
        "resolutions": resolutions,
        "execution_location_ordinals": ordinals,
        "same_branch_task_equivalence_class": same_branch,
        "same_execution_location": same_location,
        "declared_config": declared_config,
        # v3 的配置不预先声明真值（真值随布局几何变化），所以没有"与声明不符"的争议概念；
        # 唯一恒定预期是 CFG_SAME_EXPRESSION 必须等价，违反即为标注争议。
        "annotation_dispute": bool(declared_config == "CFG_SAME_EXPRESSION" and truth != "TASK_EQUIVALENT"),
        "declared_config_expected_truth": "TASK_EQUIVALENT" if declared_config == "CFG_SAME_EXPRESSION" else "LAYOUT_DEPENDENT_NOT_PREDECLARED",
        "derived_from": ["PUBLIC_OPENDRIVE_INDEPENDENT_LABEL_SIDE_MEASUREMENT",
                         "LABEL_SIDE_INDEPENDENT_REFERRING_RESOLUTION"],
        "method_output_read": False,
        "full_relation_function_called": False,
        "label_authority_version": LABEL_AUTHORITY_VERSION,
    }


def privileged_task_signatures(
    candidate_texts: Sequence[str],
    *,
    topology: Mapping[str, Any],
) -> dict[str, Any]:
    """M6 专用：预认证任务签名。绝不进入 M0–M5 的方法输入。"""
    branches = _measure_branches(topology)
    signatures = []
    for text in candidate_texts:
        resolution = _independent_resolve(text, branches)
        signatures.append({
            "branch_task_equivalence_class": resolution.get("semantic_role") if resolution.get("resolved") else None,
            "execution_location_ordinal": _independent_ordinal(text),
            "resolved": bool(resolution.get("resolved")),
        })
    return {"task_signatures": signatures, "privileged": True, "not_deployable_performance": True}


def label_row(
    sample: Mapping[str, Any],
    *,
    topology: Mapping[str, Any],
    evidence_condition: str = "FULL_EVIDENCE",
    expected_decidability: str = "DECIDABLE_FROM_ALLOWED_INPUTS",
    missing_evidence: Sequence[str] = (),
) -> dict[str, Any]:
    """产生一条评分侧标签记录；与预测文件物理分离，锁定后才关联。"""
    texts = [row["text"] for row in sample["candidates"]]
    derived = derive_truth(texts, topology=topology, declared_config=str(sample["relation_config_id"]))
    unresolvable = derived["truth_status"] != "DEFINED"
    return {
        "sample_id": str(sample["sample_id"]),
        "layout_id": str(sample["layout_id"]),
        "unit_id": str(sample["unit_id"]),
        "family": str(sample["family"]),
        "truth": derived["truth"],
        "truth_status": derived["truth_status"],
        "relation_config_id": str(sample["relation_config_id"]),
        "phrasing_id": str(sample["phrasing_id"]),
        "observation_key": str(sample["observation_key"]),
        "semantic_preservation_pair_id": f"{sample['unit_id']}|{sample['relation_config_id']}|{sample['observation_key']}",
        "relation_flip_pair_id": f"{sample['unit_id']}|{sample['observation_key']}|{sample['phrasing_id']}",
        "evidence_condition": "EVIDENCE_INSUFFICIENT" if unresolvable else evidence_condition,
        "expected_decidability": "NOT_DECIDABLE_FROM_ALLOWED_INPUTS" if unresolvable else expected_decidability,
        "missing_evidence": sorted({item for row in derived["resolutions"] for item in (row.get("requires_evidence") or ())}) or list(missing_evidence),
        "annotation": derived,
        "human_double_annotation_completed": False,
        "annotation_version": "AUTOMATED_LABEL_V1_PENDING_HUMAN_REVIEW",
    }
