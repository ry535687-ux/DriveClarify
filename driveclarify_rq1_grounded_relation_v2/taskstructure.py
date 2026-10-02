"""非 oracle 运行时任务结构：候选文本 + 公开地图 + 路线/自车状态 → 任务义务投影。

本模块只允许读取：候选解释文本、公开 OpenDRIVE 几何派生的分支拓扑、观测包内的
自车状态与导航路线。它绝不读取 TASK_BINDING.json 的 candidate_bindings、真实任务
关系、HIGH/LOW 或任何评价侧正确绑定；那些字段是标注与 M6 的专属输入。

每个输出字段都带 field_sources，取值只能来自：
sensor / frozen_perception / public_map / parser_rule / observation_history。
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .contracts import ContractError

# 语言层：方向与序数词元。取值空间是词的性质，先于任何地图查询确定。
# 只有真正的机动方向词进入方向词表。"ahead"/"through"/"onward" 描述路口相对位置，
# 不是机动方向；把它们当方向词会让同一候选同时命中两个方向而无谓失败。
_DIRECTION_PATTERNS = (
    ("LEFT", re.compile(r"\bleft\b")),
    ("RIGHT", re.compile(r"\bright\b")),
    ("STRAIGHT", re.compile(r"\bstraight\b")),
)
_ORDERING_PATTERNS = (
    ("FIRST", re.compile(r"\b(first|nearest|immediately)\b")),
    ("SECOND", re.compile(r"\b(second|after the next|following)\b")),
    ("NEXT", re.compile(r"\bnext\b")),
)
# 公开地图分支语义角色 → 语言方向。二者都不含正确答案，只是同一公共几何的两种命名。
_ROLE_TO_DIRECTION = {
    "STRAIGHT_BRANCH": "STRAIGHT",
    "RIGHT_TURN_BRANCH": "RIGHT",
    "LEFT_TURN_BRANCH": "LEFT",
}
ALLOWED_SOURCES = frozenset({"sensor", "frozen_perception", "public_map", "parser_rule", "observation_history"})
OBLIGATION_FIELDS = ("maneuver", "public_topology_target", "ordering", "constraint", "completion_predicate")


def parse_candidate_text(text: str) -> dict[str, Any]:
    """仅按解析规则从候选文本抽取方向与执行位置序数；不查地图。"""
    if not isinstance(text, str) or not text.strip():
        raise ContractError("CANDIDATE_TEXT_EMPTY")
    lowered = text.casefold()
    directions = [name for name, pattern in _DIRECTION_PATTERNS if pattern.search(lowered)]
    ordering = next((name for name, pattern in _ORDERING_PATTERNS if pattern.search(lowered)), None)
    return {
        "direction": directions[0] if len(directions) == 1 else None,
        "direction_value_space": tuple(directions) if directions else ("LEFT", "RIGHT", "STRAIGHT"),
        "ordering": ordering,
        "parser_rule_version": "driveclarify.rq1_v2_candidate_text_parser.v1",
    }


def public_branches(topology: Mapping[str, Any]) -> list[dict[str, Any]]:
    """从公开地图派生的分支拓扑读出可绑定分支；不读任何候选绑定。"""
    rows = topology.get("branch_connections")
    if not isinstance(rows, Sequence) or not rows:
        raise ContractError("PUBLIC_TOPOLOGY_BRANCHES_UNAVAILABLE")
    branches = []
    for row in rows:
        role = str(row.get("semantic_role") or "")
        direction = _ROLE_TO_DIRECTION.get(role)
        if direction is None:
            continue
        branches.append({
            "direction": direction,
            "semantic_role": role,
            "connector_road_id": str(row.get("connecting_road_id")),
            "connector_lane_id": int(row.get("connecting_lane_id")),
            "successor_road_id": str(row.get("successor_road_id")),
            "successor_lane_id": int(row.get("successor_lane_id")),
        })
    if not branches:
        raise ContractError("PUBLIC_TOPOLOGY_NO_NAMED_BRANCH")
    return branches


def _junction_ordinal(navigation_state: Mapping[str, Any]) -> tuple[int | None, str]:
    """由观测包内真实导航路线判断决策路口是路线上的第几个转向机会。"""
    members = navigation_state.get("members")
    route = members.get("route") if isinstance(members, Mapping) else None
    if not isinstance(route, Sequence) or not route:
        return None, "observation_history"
    # command 4 = LANEFOLLOW；其余为转向类指令。第一个非 4 即最近的转向机会。
    for index, row in enumerate(route):
        if int(row.get("command", 4)) != 4:
            return 1 if index <= 2 else 2, "observation_history"
    return None, "observation_history"


def project_obligation(
    candidate_text: str,
    topology: Mapping[str, Any],
    ego_state: Mapping[str, Any],
    navigation_state: Mapping[str, Any],
) -> dict[str, Any]:
    """把一个候选解释文本落到公开地图分支上，产生任务义务投影。

    绑定失败（方向未解析、公开地图无匹配分支、匹配不唯一）时 available=False，
    由上层闭合为 UNKNOWN；绝不回退去读正确绑定。
    """
    parsed = parse_candidate_text(candidate_text)
    junction_id = str(topology.get("junction_id") or "")
    if not junction_id:
        return {"available": False, "reason_code": "PUBLIC_TOPOLOGY_JUNCTION_MISSING"}
    try:
        branches = public_branches(topology)
    except ContractError as error:
        return {"available": False, "reason_code": str(error)}

    direction = parsed["direction"]
    if direction is None:
        return {"available": False, "reason_code": "CANDIDATE_DIRECTION_UNRESOLVED_BY_LANGUAGE"}
    matched = [row for row in branches if row["direction"] == direction]
    if len(matched) != 1:
        return {"available": False, "reason_code": "PUBLIC_MAP_BRANCH_MATCH_NOT_UNIQUE" if matched else "PUBLIC_MAP_BRANCH_ABSENT_FOR_DIRECTION"}
    branch = matched[0]

    waypoint = ego_state.get("map_waypoint")
    if not isinstance(waypoint, Mapping) or "road_id" not in waypoint:
        return {"available": False, "reason_code": "EGO_MAP_WAYPOINT_UNAVAILABLE"}
    # 执行位置：候选文本含序数词时由解析规则决定；不含时，规定执行位置就是自车当前
    # 车道前方的那个路口，由公开地图与自车车道唯一确定，不需要路线转向指令。
    ordinal, ordinal_source = _junction_ordinal(navigation_state)
    if parsed["ordering"]:
        ordering, ordering_source = parsed["ordering"], "parser_rule"
    else:
        ordering, ordering_source = f"UPCOMING_JUNCTION_ON_CURRENT_LANE:{junction_id}", "public_map"

    return {
        "available": True,
        "maneuver": direction,
        "public_topology_target": f"junction:{junction_id}:connector_road:{branch['connector_road_id']}:lane:{branch['connector_lane_id']}",
        "ordering": ordering,
        "constraint": None,
        "completion_predicate": f"road:{branch['successor_road_id']}:lane:{branch['successor_lane_id']}",
        "field_sources": {
            "maneuver": "parser_rule",
            "public_topology_target": "public_map",
            "ordering": ordering_source,
            "constraint": "parser_rule",
            "completion_predicate": "public_map",
        },
        "binding_evidence": {
            "parser_rule_version": parsed["parser_rule_version"],
            "language_direction_value_space": list(parsed["direction_value_space"]),
            "public_branch_directions": sorted(row["direction"] for row in branches),
            "matched_semantic_role": branch["semantic_role"],
            "ego_road_id": str(waypoint.get("road_id")),
            "ego_lane_id": int(waypoint.get("lane_id")),
            "route_junction_ordinal": ordinal,
            "privileged_task_signature_read": False,
        },
    }


def build_runtime_context(
    candidate_texts: Sequence[str],
    topology: Mapping[str, Any],
    ego_state: Mapping[str, Any],
    navigation_state: Mapping[str, Any],
) -> dict[str, Any]:
    """为两个候选生成 runtime_context.candidate_obligations（M4/M5 的共同任务状态）。"""
    if len(candidate_texts) != 2:
        raise ContractError("EXACTLY_TWO_CANDIDATE_TEXTS_REQUIRED")
    rows = [project_obligation(text, topology, ego_state, navigation_state) for text in candidate_texts]
    for row in rows:
        if row.get("available") and set(row["field_sources"]) != set(OBLIGATION_FIELDS):
            raise ContractError("OBLIGATION_FIELD_SOURCE_INCOMPLETE")
        if row.get("available") and not set(row["field_sources"].values()) <= ALLOWED_SOURCES:
            raise ContractError("OBLIGATION_FIELD_SOURCE_FORBIDDEN")
    return {"candidate_obligations": rows, "task_structure_version": "driveclarify.rq1_v2_runtime_task_structure.v1"}
