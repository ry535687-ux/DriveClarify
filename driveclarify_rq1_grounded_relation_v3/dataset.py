"""决策样本构建（v3）：布局 × 关系配置 × 记录初态 × 语义保持表述。

与 v2 的关键差别
----------------
v2 的候选文本直接含机动方向词，(候选文本对) → 真值 是确定性函数，纯词法基线即达 1.0
（diagnosis/LEXICAL_SUFFICIENCY.json）。v3 的候选文本只含不指定方向的指代表达。

配置设计使真值随布局变化：
  CFG_LONGER_VS_STRAIGHTER = (R_LONGER_ROAD, R_STRAIGHTER_CONNECTOR)
  CFG_LONGER_VS_SHARPER    = (R_LONGER_ROAD, R_SHARPER_CONNECTOR)
"更长的路"落在哪个分支由该布局的公开几何决定；"弯度最小/最大"是互补的两个分支。
因此每个布局中这两个配置恰好一个等价一个分歧，而具体是哪一个只能由地图解析得出，
不能由候选文本读出。同一文本对在不同布局真值不同，D1/D2 词法充分性被打破。

  CFG_SAME_EXPRESSION      = (R_LONGER_ROAD, R_LONGER_ROAD) 两种表面形式 — 恒等价对照
  CFG_EVIDENCE_INSUFFICIENT= (R_LONGER_ROAD, R_UNRESOLVABLE_APPEARANCE) — 证据不足子集

样本的方法侧载荷只含：原始歧义指令、两个候选解释文本、真实观测身份、自车状态、
公开地图派生任务结构、路线语境。真实任务关系由 labels.py 独立产生，不经此处写入。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import ContractError, assert_label_firewall, digest
from .taskstructure import build_runtime_context

RELATION_CONFIGS = (
    "CFG_LONGER_VS_STRAIGHTER",
    "CFG_LONGER_VS_SHARPER",
    "CFG_SAME_EXPRESSION",
    "CFG_EVIDENCE_INSUFFICIENT",
)
PHRASINGS = ("P1", "P2")
FAMILIES = ("REF", "LMK", "ORD", "USC")

# 两种表面形式承载同一指代表达，用于语义保持配对。表面形式不同、语义义务相同。
_SURFACE: dict[str, dict[str, str]] = {
    "R_LONGER_ROAD": {
        "P1": "Take the exit that leads onto the longer road.",
        "P2": "Take the exit onto the road that runs longer.",
    },
    "R_SHORTER_ROAD": {
        "P1": "Take the exit that leads onto the shorter road.",
        "P2": "Take the exit onto the road that runs a shorter distance.",
    },
    "R_STRAIGHTER_CONNECTOR": {
        "P1": "Take the exit whose connecting lane bends least.",
        "P2": "Take the exit that changes your heading least.",
    },
    "R_SHARPER_CONNECTOR": {
        "P1": "Take the exit whose connecting lane bends most.",
        "P2": "Take the exit that changes your heading most.",
    },
    "R_UNRESOLVABLE_APPEARANCE": {
        "P1": "Take the exit toward the taller building.",
        "P2": "Take the exit toward the building with the brighter facade.",
    },
}

# 配置 → 两个候选各自使用的指代表达。CFG_SAME_EXPRESSION 用同一表达的两种表面形式。
_CONFIG_EXPRESSIONS: dict[str, tuple[str, str]] = {
    "CFG_LONGER_VS_STRAIGHTER": ("R_LONGER_ROAD", "R_STRAIGHTER_CONNECTOR"),
    "CFG_LONGER_VS_SHARPER": ("R_LONGER_ROAD", "R_SHARPER_CONNECTOR"),
    "CFG_SAME_EXPRESSION": ("R_LONGER_ROAD", "R_LONGER_ROAD"),
    "CFG_EVIDENCE_INSUFFICIENT": ("R_LONGER_ROAD", "R_UNRESOLVABLE_APPEARANCE"),
}

# 原始歧义指令：不指定分支，也不含方向词。两种表述语义保持。
_RAW_INSTRUCTION = {
    "P1": "Take the exit at the intersection.",
    "P2": "Leave the intersection by one of the exits.",
}

EVIDENCE_INSUFFICIENT_CONFIG = "CFG_EVIDENCE_INSUFFICIENT"


def family_for_layout(index: int) -> str:
    """按冻结顺序为布局分配歧义族，仅用于分层报告；与任何预测或标签无关。"""
    return FAMILIES[index % len(FAMILIES)]


def candidate_texts(config: str, phrasing: str) -> tuple[str, str]:
    expressions = _CONFIG_EXPRESSIONS.get(config)
    if expressions is None:
        raise ContractError("UNKNOWN_CONFIG:" + str(config))
    if phrasing not in PHRASINGS:
        raise ContractError("UNKNOWN_PHRASING:" + str(phrasing))
    if config == "CFG_SAME_EXPRESSION":
        # 同一表达的两种表面形式：P1 组合 (P1,P2)，P2 组合 (P2,P1)，保证两候选文本不同。
        first, second = ("P1", "P2") if phrasing == "P1" else ("P2", "P1")
        return _SURFACE[expressions[0]][first], _SURFACE[expressions[1]][second]
    return _SURFACE[expressions[0]][phrasing], _SURFACE[expressions[1]][phrasing]


def referring_expression_ids(config: str) -> tuple[str, str]:
    expressions = _CONFIG_EXPRESSIONS.get(config)
    if expressions is None:
        raise ContractError("UNKNOWN_CONFIG:" + str(config))
    return expressions


def raw_instruction(phrasing: str) -> str:
    return _RAW_INSTRUCTION[phrasing]


def sample_id(unit_id: str, config: str, observation_key: str, phrasing: str) -> str:
    return f"{unit_id}|{config}|{observation_key}|{phrasing}"


def build_sample(
    *,
    unit_id: str,
    layout_id: str,
    family: str,
    config: str,
    phrasing: str,
    observation: Mapping[str, Any],
    topology: Mapping[str, Any],
    ego_state: Mapping[str, Any],
    navigation_state: Mapping[str, Any],
) -> dict[str, Any]:
    """产生单个决策样本的无标签方法输入。"""
    if config not in RELATION_CONFIGS or phrasing not in PHRASINGS:
        raise ContractError("UNKNOWN_CONFIG_OR_PHRASING")
    first, second = candidate_texts(config, phrasing)
    if first == second:
        raise ContractError("CANDIDATE_TEXTS_IDENTICAL:" + config)
    observation_key = str(observation["observation_key"])
    runtime_context = build_runtime_context((first, second), topology, ego_state, navigation_state)
    waypoint = ego_state.get("map_waypoint") or {}
    pose = (ego_state.get("pose") or {}).get("location_xyz")
    value = {
        "sample_id": sample_id(unit_id, config, observation_key, phrasing),
        "layout_id": layout_id,
        "unit_id": unit_id,
        "family": family,
        "relation_config_id": config,
        "phrasing_id": phrasing,
        "observation_key": observation_key,
        "raw_instruction": raw_instruction(phrasing),
        "observation": {
            "sha256": str(observation["observation_sha256"]),
            "package_content_sha256": str(observation["package_content_sha256"]),
            "source_frame": observation.get("source_frame"),
            "camera_images_sha256": str(observation["camera_images_sha256"]),
            "real_carla_render": True,
        },
        "candidates": [
            {"candidate_id": "K1", "text": first},
            {"candidate_id": "K2", "text": second},
        ],
        "ego_state": {
            "speed_mps": observation.get("speed_mps"),
            "location_xyz": pose,
            "map_road_id": str(waypoint.get("road_id")),
            "map_lane_id": waypoint.get("lane_id"),
            "map_s_m": waypoint.get("s_m"),
        },
        "route_context": {
            "route_id": str(topology.get("route_id")),
            "town": str(topology.get("town")),
            "public_map_sha256": str((topology.get("map_identity") or {}).get("opendrive_sha256")),
            "route_command_sequence": [int(row.get("command", 4)) for row in ((navigation_state.get("members") or {}).get("route") or [])][:8],
        },
        "runtime_context": runtime_context,
    }
    assert_label_firewall(value)
    value["method_input_sha256"] = digest(value)
    return value


def build_layout_samples(
    *,
    unit_id: str,
    layout_id: str,
    family: str,
    observations: Sequence[Mapping[str, Any]],
    topology: Mapping[str, Any],
    ego_states: Sequence[Mapping[str, Any]],
    navigation_states: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按实际观测数量生成该布局的全部样本：配置 × 观测 × 表述。"""
    if not observations or len(observations) != len(ego_states) or len(observations) != len(navigation_states):
        raise ContractError("OBSERVATION_STATE_COUNT_MISMATCH")
    keys = [str(row["observation_key"]) for row in observations]
    if len(set(keys)) != len(keys):
        raise ContractError("OBSERVATION_KEYS_NOT_DISTINCT")
    digests = [str(row["observation_sha256"]) for row in observations]
    rows = []
    for config in RELATION_CONFIGS:
        for index, observation in enumerate(observations):
            for phrasing in PHRASINGS:
                rows.append(build_sample(
                    unit_id=unit_id, layout_id=layout_id, family=family, config=config, phrasing=phrasing,
                    observation=observation, topology=topology, ego_state=ego_states[index],
                    navigation_state=navigation_states[index],
                ))
    for row in rows:
        row["observation_independence"] = {
            "observation_count": len(observations),
            "distinct_observation_digest_count": len(set(digests)),
            "duplicate_observation_content": len(set(digests)) != len(digests),
        }
    return rows
