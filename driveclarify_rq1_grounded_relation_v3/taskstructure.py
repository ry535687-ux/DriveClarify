"""非 oracle 运行时任务结构（v3）：候选指代表达 + 公开地图属性 → 任务义务投影。

允许读取：候选解释文本、公开 OpenDRIVE 派生的分支几何/属性、观测包内自车状态与导航路线。
绝不读取 TASK_BINDING 的 candidate_bindings、真实任务关系、HIGH/LOW、正确绑定。

与 v2 的差别：v2 从候选文本词法直接取出机动方向（left/right/straight），
使真值成为候选文本对的确定性函数（同义反复，见 diagnosis/LEXICAL_SUFFICIENCY.json）。
v3 的候选文本不含方向词，分支身份必须由公开地图属性解析得到。
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_static_branch import topology as topology_module

from .contracts import ContractError
from .referring import RESOLVER_VERSION, resolve_to_branch

ALLOWED_SOURCES = frozenset({"sensor", "frozen_perception", "public_map", "parser_rule", "observation_history"})
OBLIGATION_FIELDS = ("maneuver", "public_topology_target", "ordering", "constraint", "completion_predicate")
TASK_STRUCTURE_VERSION = "driveclarify.rq1_v3_runtime_task_structure.v1"

_ORDERING_PATTERNS = (
    ("FIRST", re.compile(r"\b(first|nearest|immediately)\b")),
    ("SECOND", re.compile(r"\b(second|after the next|following)\b")),
)
_MAP_CACHE: dict[str, ET.Element] = {}


def _map_root(opendrive_path: str) -> ET.Element:
    key = str(opendrive_path)
    if key not in _MAP_CACHE:
        _MAP_CACHE[key] = ET.parse(key).getroot()
    return _MAP_CACHE[key]


def _road(root: ET.Element, road_id: str) -> ET.Element | None:
    for road in root.findall("road"):
        if road.get("id") == str(road_id):
            return road
    return None


def _heading_change_abs_deg(root: ET.Element, connector_road_id: str, lane_id: int) -> float | None:
    """连接器道路首尾切向变化角的绝对值；纯公开几何量。"""
    road = _road(root, connector_road_id)
    if road is None:
        return None
    length = float(road.get("length", 0.0))
    if length <= 0.2:
        return None
    try:
        start = topology_module.lane_center_at(road, lane_id, 0.02)
        early = topology_module.lane_center_at(road, lane_id, min(0.2, length * 0.1))
        late = topology_module.lane_center_at(road, lane_id, max(length - 0.2, length * 0.9))
        end = topology_module.lane_center_at(road, lane_id, length - 0.02)
    except Exception:
        return None
    first = math.atan2(early[1] - start[1], early[0] - start[0])
    final = math.atan2(end[1] - late[1], end[0] - late[0])
    delta = math.degrees(final - first)
    while delta > 180.0:
        delta -= 360.0
    while delta < -180.0:
        delta += 360.0
    return round(abs(delta), 4)


def branch_attributes(topology: Mapping[str, Any]) -> list[dict[str, Any]]:
    """从公开 OpenDRIVE 为每个分支派生指代解析所需属性。不读任何候选绑定。"""
    connections = topology.get("branch_connections")
    if not isinstance(connections, Sequence) or len(connections) < 2:
        raise ContractError("PUBLIC_TOPOLOGY_BRANCHES_UNAVAILABLE")
    identity = topology.get("map_identity") or {}
    opendrive_path = identity.get("opendrive_path")
    if not opendrive_path or not Path(str(opendrive_path)).is_file():
        raise ContractError("PUBLIC_OPENDRIVE_UNAVAILABLE")
    root = _map_root(str(opendrive_path))
    rows: list[dict[str, Any]] = []
    for connection in connections:
        role = str(connection.get("semantic_role") or "")
        successor_road_id = str(connection.get("successor_road_id"))
        connector_road_id = str(connection.get("connecting_road_id"))
        successor = _road(root, successor_road_id)
        rows.append({
            "semantic_role": role,
            "successor_road_id": successor_road_id,
            "successor_lane_id": int(connection.get("successor_lane_id")),
            "connector_road_id": connector_road_id,
            "connector_lane_id": int(connection.get("connecting_lane_id")),
            "successor_length_m": None if successor is None else round(float(successor.get("length", 0.0)), 4),
            "connector_heading_change_abs_deg": _heading_change_abs_deg(root, connector_road_id, int(connection.get("connecting_lane_id"))),
            "attribute_source": "public_map",
        })
    return rows


def _junction_ordinal(navigation_state: Mapping[str, Any]) -> tuple[int | None, str]:
    members = navigation_state.get("members")
    route = members.get("route") if isinstance(members, Mapping) else None
    if not isinstance(route, Sequence) or not route:
        return None, "observation_history"
    for index, row in enumerate(route):
        if int(row.get("command", 4)) != 4:
            return 1 if index <= 2 else 2, "observation_history"
    return None, "observation_history"


def project_obligation(
    candidate_text: str,
    topology: Mapping[str, Any],
    ego_state: Mapping[str, Any],
    navigation_state: Mapping[str, Any],
    *,
    attributes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """把一个候选指代表达落到公开地图分支上，产生任务义务投影。

    解析失败（表达不可识别、所需属性不在允许输入内、属性缺失、并列）时 available=False，
    由上层闭合为 UNKNOWN；绝不回退去读正确绑定。
    """
    junction_id = str(topology.get("junction_id") or "")
    if not junction_id:
        return {"available": False, "reason_code": "PUBLIC_TOPOLOGY_JUNCTION_MISSING"}
    try:
        rows = list(attributes) if attributes is not None else branch_attributes(topology)
    except ContractError as error:
        return {"available": False, "reason_code": str(error)}

    resolved = resolve_to_branch(candidate_text, rows)
    if not resolved.get("available"):
        return {"available": False, "reason_code": str(resolved.get("reason_code")),
                "referring_evidence": {key: resolved[key] for key in resolved if key != "available"}}

    branch = next((row for row in rows if row["semantic_role"] == resolved["semantic_role"]), None)
    if branch is None:
        return {"available": False, "reason_code": "RESOLVED_ROLE_NOT_IN_TOPOLOGY"}

    waypoint = ego_state.get("map_waypoint")
    if not isinstance(waypoint, Mapping) or "road_id" not in waypoint:
        return {"available": False, "reason_code": "EGO_MAP_WAYPOINT_UNAVAILABLE"}

    ordinal, _ = _junction_ordinal(navigation_state)
    lowered = str(candidate_text).casefold()
    explicit = next((name for name, pattern in _ORDERING_PATTERNS if pattern.search(lowered)), None)
    if explicit:
        ordering, ordering_source = explicit, "parser_rule"
    else:
        ordering, ordering_source = f"UPCOMING_JUNCTION_ON_CURRENT_LANE:{junction_id}", "public_map"

    # maneuver 由公开地图解析出的分支决定，不由候选文本词法给出。
    return {
        "available": True,
        "maneuver": f"BRANCH_ROLE:{branch['semantic_role']}",
        "public_topology_target": f"junction:{junction_id}:connector_road:{branch['connector_road_id']}:lane:{branch['connector_lane_id']}",
        "ordering": ordering,
        "constraint": None,
        "completion_predicate": f"road:{branch['successor_road_id']}:lane:{branch['successor_lane_id']}",
        "field_sources": {
            "maneuver": "public_map",
            "public_topology_target": "public_map",
            "ordering": ordering_source,
            "constraint": "parser_rule",
            "completion_predicate": "public_map",
        },
        "binding_evidence": {
            "resolver_version": RESOLVER_VERSION,
            "referring_expression_id": resolved["referring_expression_id"],
            "resolved_by_attribute": resolved["attribute"],
            "attribute_select": resolved["select"],
            "attribute_values": resolved["attribute_values"],
            "candidate_text_contains_maneuver_direction_word": False,
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
    attributes = None
    try:
        attributes = branch_attributes(topology)
    except ContractError:
        attributes = None
    rows = [project_obligation(text, topology, ego_state, navigation_state, attributes=attributes) for text in candidate_texts]
    for row in rows:
        if row.get("available"):
            if set(row["field_sources"]) != set(OBLIGATION_FIELDS):
                raise ContractError("OBLIGATION_FIELD_SOURCE_INCOMPLETE")
            if not set(row["field_sources"].values()) <= ALLOWED_SOURCES:
                raise ContractError("OBLIGATION_FIELD_SOURCE_FORBIDDEN")
    return {
        "candidate_obligations": rows,
        "task_structure_version": TASK_STRUCTURE_VERSION,
        "public_branch_attributes": [] if attributes is None else [
            {key: row[key] for key in ("semantic_role", "successor_length_m", "connector_heading_change_abs_deg", "attribute_source")}
            for row in attributes
        ],
    }
