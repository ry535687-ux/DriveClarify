"""决策样本构建：布局 × 任务关系配置 × 记录初态 × 语义保持表述。

样本的方法侧载荷（method input）只含：原始歧义指令、两个候选解释文本、真实观测身份、
自车状态、公开地图派生任务结构、路线语境。真实任务关系由 labels.py 独立产生，
不经此处写入方法输入。

"种子"必须对应实际记录的不同观测（不同预置初态）。本模块按观测包实际数量生成样本，
绝不靠 seed 名称制造样本量。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import ContractError, assert_label_firewall, digest
from .taskstructure import build_runtime_context

# 关系配置：DIVERGENT 让两个候选落到不同公开分支；EQUIVALENT 让两个候选落到同一义务。
RELATION_CONFIGS = ("CFG_DIVERGENT_BRANCH", "CFG_EQUIVALENT_SAME_BRANCH")
PHRASINGS = ("P1", "P2")
FAMILIES = ("REF", "LMK", "ORD", "USC")

# 每个族的原始歧义指令与两种语义保持表述。候选文本只表达语言层解释，
# 不写入分支 ID、连接器道路号、正确执行位置或任务关系。
_TEMPLATES: dict[str, dict[str, Any]] = {
    "REF": {
        "raw_instruction": {"P1": "Turn at the intersection.", "P2": "Make your turn at the crossing."},
        "candidate_text": {
            ("CFG_DIVERGENT_BRANCH", "P1"): ("Go straight at the intersection.", "Turn right at the intersection."),
            ("CFG_DIVERGENT_BRANCH", "P2"): ("Continue straight at the crossing.", "Make a right at the crossing."),
            ("CFG_EQUIVALENT_SAME_BRANCH", "P1"): ("Turn right at the intersection.", "Make a right at the intersection."),
            ("CFG_EQUIVALENT_SAME_BRANCH", "P2"): ("Take the right turn at the crossing.", "Turn to the right at the crossing."),
        },
    },
    "LMK": {
        "raw_instruction": {"P1": "Turn after the parked car.", "P2": "Make your turn once you pass the parked car."},
        "candidate_text": {
            ("CFG_DIVERGENT_BRANCH", "P1"): ("Go straight after the parked car.", "Turn right after the parked car."),
            ("CFG_DIVERGENT_BRANCH", "P2"): ("Continue straight once you pass the parked car.", "Make a right once you pass the parked car."),
            ("CFG_EQUIVALENT_SAME_BRANCH", "P1"): ("Turn right after the parked car.", "Make a right after the parked car."),
            ("CFG_EQUIVALENT_SAME_BRANCH", "P2"): ("Take the right turn once you pass the parked car.", "Turn to the right once you pass the parked car."),
        },
    },
    "ORD": {
        "raw_instruction": {"P1": "Turn right at the next junction.", "P2": "Take a right at the coming junction."},
        "candidate_text": {
            # 执行位置分歧：同一转向，不同序数 → 不同规定执行位置。
            ("CFG_DIVERGENT_BRANCH", "P1"): ("Turn right at the first junction.", "Turn right at the second junction."),
            ("CFG_DIVERGENT_BRANCH", "P2"): ("Make a right at the nearest junction.", "Make a right at the following junction."),
            ("CFG_EQUIVALENT_SAME_BRANCH", "P1"): ("Turn right at the first junction.", "Turn right at the nearest junction."),
            ("CFG_EQUIVALENT_SAME_BRANCH", "P2"): ("Make a right at the first junction.", "Make a right at the nearest junction."),
        },
    },
    "USC": {
        "raw_instruction": {"P1": "Head onward at the intersection.", "P2": "Keep going at the crossing."},
        "candidate_text": {
            ("CFG_DIVERGENT_BRANCH", "P1"): ("Proceed straight at the intersection.", "Bear right at the intersection."),
            ("CFG_DIVERGENT_BRANCH", "P2"): ("Keep going straight at the crossing.", "Take the right at the crossing."),
            ("CFG_EQUIVALENT_SAME_BRANCH", "P1"): ("Proceed straight at the intersection.", "Keep going straight at the intersection."),
            ("CFG_EQUIVALENT_SAME_BRANCH", "P2"): ("Continue straight at the crossing.", "Carry on straight at the crossing."),
        },
    },
}


def family_for_layout(index: int) -> str:
    """按冻结顺序为布局分配歧义族；与任何预测或标签无关。"""
    return FAMILIES[index % len(FAMILIES)]


def candidate_texts(family: str, config: str, phrasing: str) -> tuple[str, str]:
    template = _TEMPLATES.get(family)
    if template is None:
        raise ContractError("UNKNOWN_FAMILY:" + family)
    value = template["candidate_text"].get((config, phrasing))
    if value is None:
        raise ContractError(f"NO_TEMPLATE:{family}:{config}:{phrasing}")
    return value


def raw_instruction(family: str, phrasing: str) -> str:
    return _TEMPLATES[family]["raw_instruction"][phrasing]


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
    first, second = candidate_texts(family, config, phrasing)
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
        "raw_instruction": raw_instruction(family, phrasing),
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
    # 方法输入必须过标签防火墙：任何答案字段或答案化取值都在此处硬失败。
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
