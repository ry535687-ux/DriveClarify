"""Phase and equivalence contracts for Method V3."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


FEATURE_FLAG = "DRIVECLARIFY_METHOD_V3_EXISTING_ROUTE_BINDING"


class MethodV3Phase(str, Enum):
    UNRESOLVED_AMBIGUITY = "UNRESOLVED_AMBIGUITY"
    QUERY_PENDING = "QUERY_PENDING"
    ANSWER_RESOLVED = "ANSWER_RESOLVED"
    SELECTED_ROUTE_READY = "SELECTED_ROUTE_READY"
    SELECTED_ACTIVE_PRECOMMIT = "SELECTED_ACTIVE_PRECOMMIT"
    COMMITTED = "COMMITTED"
    COMPLETED = "COMPLETED"
    RECONNECTED = "RECONNECTED"
    TERMINAL_FAIL_CLOSED = "TERMINAL_FAIL_CLOSED"


_ALLOWED_PHASE_TRANSITIONS = {
    MethodV3Phase.UNRESOLVED_AMBIGUITY: {MethodV3Phase.QUERY_PENDING},
    MethodV3Phase.QUERY_PENDING: {MethodV3Phase.ANSWER_RESOLVED},
    MethodV3Phase.ANSWER_RESOLVED: {MethodV3Phase.SELECTED_ROUTE_READY},
    MethodV3Phase.SELECTED_ROUTE_READY: {
        MethodV3Phase.SELECTED_ACTIVE_PRECOMMIT
    },
    MethodV3Phase.SELECTED_ACTIVE_PRECOMMIT: {
        MethodV3Phase.COMMITTED,
        MethodV3Phase.TERMINAL_FAIL_CLOSED,
    },
    MethodV3Phase.COMMITTED: {
        MethodV3Phase.COMPLETED,
        MethodV3Phase.TERMINAL_FAIL_CLOSED,
    },
    MethodV3Phase.COMPLETED: {MethodV3Phase.RECONNECTED},
    MethodV3Phase.RECONNECTED: set(),
    MethodV3Phase.TERMINAL_FAIL_CLOSED: set(),
}


class MethodV3PhaseOwner:
    """Monotone owner preventing pre-answer evidence from leaking post-answer."""

    def __init__(self) -> None:
        self.phase = MethodV3Phase.UNRESOLVED_AMBIGUITY
        self.history: list[dict[str, str]] = []

    def transition(self, phase: MethodV3Phase, reason: str) -> None:
        if phase not in _ALLOWED_PHASE_TRANSITIONS[self.phase]:
            raise RuntimeError("METHOD_V3_PHASE_TRANSITION_INVALID")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("METHOD_V3_PHASE_REASON_INVALID")
        previous = self.phase
        self.phase = phase
        self.history.append(
            {"from": previous.value, "to": phase.value, "reason": reason}
        )

    def summary(self) -> dict[str, Any]:
        return {"phase": self.phase.value, "history": list(self.history)}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OpportunityEquivalenceResolutionV3:
    representative: dict[str, Any]
    raw_match_count: int
    equivalence_class_count: int
    equivalence_key_digest: str
    raw_provenance: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "representative": dict(self.representative),
            "raw_match_count": self.raw_match_count,
            "equivalence_class_count": self.equivalence_class_count,
            "equivalence_key_digest": self.equivalence_key_digest,
            "raw_provenance": [dict(row) for row in self.raw_provenance],
            "resolver": "SEMANTIC_TOPOLOGICAL_EQUIVALENCE_CLASS_V3",
            "arbitrary_first_match_used": False,
            "nearest_match_used": False,
        }


_IDENTITY_FIELDS = (
    "junction_id",
    "target_id",
    "branch_id",
    "entry_road_id",
    "entry_lane_id",
    "exit_road_id",
    "exit_lane_id",
    "maneuver_direction",
    "availability",
    "route_reachable",
)


def resolve_selected_opportunity_equivalence_v3(
    matches: Sequence[tuple[float, Mapping[str, Any]]],
    *,
    selected_obligation_identity: str,
    selected_maneuver_direction: str,
) -> OpportunityEquivalenceResolutionV3:
    """Resolve one semantic/topological class, never one raw list element."""

    if not matches:
        raise RuntimeError("METHOD_V3_SELECTED_OPPORTUNITY_CLASS_MISSING")
    groups: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    keys: dict[str, dict[str, Any]] = {}
    for distance, source in matches:
        row = dict(source)
        direction = str(
            row.get("maneuver_direction") or selected_maneuver_direction
        ).upper()
        key = {
            "selected_obligation_identity": str(selected_obligation_identity),
            "junction_id": row.get("junction_id"),
            "target_id": row.get("target_id"),
            "branch_id": row.get("branch_id"),
            "entry_road_id": row.get("entry_road_id"),
            "entry_lane_id": row.get("entry_lane_id"),
            "exit_road_id": row.get("exit_road_id"),
            "exit_lane_id": row.get("exit_lane_id"),
            "maneuver_direction": direction,
            "availability": row.get("availability"),
            "route_reachable": row.get("route_reachable"),
        }
        key_digest = _digest(key)
        keys[key_digest] = key
        groups.setdefault(key_digest, []).append((float(distance), row))
    if len(groups) != 1:
        raise RuntimeError("METHOD_V3_SELECTED_OPPORTUNITY_CLASS_CARDINALITY_INVALID")

    key_digest = next(iter(groups))
    members = groups[key_digest]
    # This chooses only a byte-stable representation after semantic equivalence
    # has been proven. It does not select between maneuver meanings.
    representative_source = min(
        (row for _, row in members), key=lambda row: _canonical(row)
    )
    representative = dict(representative_source)
    representative["maneuver_direction"] = keys[key_digest][
        "maneuver_direction"
    ]
    representative["equivalence_class_identity"] = key_digest
    representative["equivalent_raw_match_count"] = len(members)
    representative["equivalent_route_order_indices"] = sorted(
        {
            int(row["route_order_index"])
            for _, row in members
            if isinstance(row.get("route_order_index"), int)
            and not isinstance(row.get("route_order_index"), bool)
        }
    )
    representative["equivalent_route_opportunity_indices"] = sorted(
        {
            int(row["route_opportunity_index"])
            for _, row in members
            if isinstance(row.get("route_opportunity_index"), int)
            and not isinstance(row.get("route_opportunity_index"), bool)
        }
    )
    provenance = tuple(
        {
            "target_distance_m": distance,
            "route_order_index": row.get("route_order_index"),
            "route_opportunity_index": row.get("route_opportunity_index"),
        }
        for distance, row in sorted(
            members,
            key=lambda item: (item[0], _canonical(item[1])),
        )
    )
    return OpportunityEquivalenceResolutionV3(
        representative=representative,
        raw_match_count=len(matches),
        equivalence_class_count=1,
        equivalence_key_digest=key_digest,
        raw_provenance=provenance,
    )
