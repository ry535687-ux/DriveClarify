"""Typed temporal evidence memory with per-field invalidation semantics."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256


@dataclass(frozen=True)
class FieldMemoryPolicy:
    max_age_simulation_s: float
    binding_keys: tuple[str, ...]
    invalidation_events: tuple[str, ...]
    retention_mode: str


FIELD_MEMORY_POLICIES = {
    "E1_INTERPRETATION_VALIDITY": FieldMemoryPolicy(
        0.50,
        ("candidate_set_digest", "instruction_digest"),
        ("SEMANTIC_CANDIDATE_SET_CHANGED", "INSTRUCTION_CHANGED", "QUERY_RESOLVED"),
        "SHORT_SEMANTIC_CONFIRMATION",
    ),
    "E2_GROUNDING": FieldMemoryPolicy(
        2.00,
        ("candidate_set_digest", "environment_digest"),
        (
            "TRACK_IDENTITY_CONFLICT",
            "ACTOR_DESTROYED",
            "SEMANTIC_CANDIDATE_SET_CHANGED",
            "ENVIRONMENT_CHANGED",
        ),
        "TRACK_IDENTITY_UNTIL_COHERENCE_BREAK",
    ),
    "E3_CURRENT_ACTION_RELATION": FieldMemoryPolicy(
        0.0,
        ("route_version", "source_frame_id"),
        ("ROUTE_CHANGED", "PLAN_CHANGED", "SOURCE_FRAME_ADVANCED"),
        "FRAME_LOCAL_NO_RETENTION",
    ),
    "E4_FUTURE_OBLIGATION_RELATION": FieldMemoryPolicy(
        2.00,
        ("candidate_set_digest", "route_version", "environment_digest", "topology_boundary_id"),
        ("ROUTE_CHANGED", "ENVIRONMENT_CHANGED", "TOPOLOGY_BOUNDARY_PASSED", "SEMANTIC_CANDIDATE_SET_CHANGED"),
        "UNTIL_ROUTE_OR_BOUNDARY_CHANGE",
    ),
    "E5_ROUTE_LANE_TOPOLOGY_RELATION": FieldMemoryPolicy(
        10.00,
        ("route_version", "environment_digest", "topology_boundary_id"),
        ("ROUTE_CHANGED", "ENVIRONMENT_CHANGED", "TOPOLOGY_BOUNDARY_PASSED", "JUNCTION_CONSUMED"),
        "UNTIL_CERTIFIED_TOPOLOGY_BOUNDARY",
    ),
    "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE": FieldMemoryPolicy(
        2.00,
        ("candidate_set_digest", "route_version", "topology_boundary_id"),
        ("SEMANTIC_CANDIDATE_SET_CHANGED", "ROUTE_CHANGED", "TOPOLOGY_BOUNDARY_PASSED"),
        "DERIVED_UNTIL_DEPENDENCY_CHANGE",
    ),
    "E7_SAFETY_RULE_HOLDING": FieldMemoryPolicy(
        0.10,
        ("environment_digest", "safety_state_digest", "holding_lease_id"),
        ("SAFETY_STATE_CHANGED", "HAZARD_OBSERVED", "HOLDING_LEASE_REVOKED", "CONTROL_OWNER_CHANGED"),
        "TWO_TICK_MAXIMUM_REVALIDATE_FREQUENTLY",
    ),
    "E8_RECOVERABILITY": FieldMemoryPolicy(
        0.25,
        ("candidate_set_digest", "route_version", "dynamics_state_digest"),
        ("ROUTE_CHANGED", "DYNAMICS_CHANGED", "SEMANTIC_CANDIDATE_SET_CHANGED"),
        "SHORT_DYNAMICS_BOUND",
    ),
    "E9_ANSWER_CHANGES_ACTION": FieldMemoryPolicy(
        60.00,
        ("candidate_set_digest", "instruction_digest"),
        ("SEMANTIC_CANDIDATE_SET_CHANGED", "INSTRUCTION_CHANGED", "QUERY_RESOLVED"),
        "SEMANTIC_EPISODE_UNTIL_CANDIDATE_CHANGE",
    ),
}


@dataclass
class _MemoryItem:
    field: dict[str, Any]
    acquired_time_s: float
    latest_revalidation_time_s: float
    binding: dict[str, Any]


class TemporalEvidenceMemory:
    """Retain valid evidence prospectively; never retain a generic forever cache."""

    def __init__(self) -> None:
        self._items: dict[str, _MemoryItem] = {}
        self.events: list[dict[str, Any]] = []
        self._lifecycle_binding: Optional[dict[str, Any]] = None

    @staticmethod
    def _required_lifecycle_binding(context: Mapping[str, Any]) -> dict[str, Any]:
        binding = {
            "episode_id": context.get("episode_id"),
            "route_version": context.get("route_version"),
            "actor_binding_digest": context.get("actor_binding_digest"),
        }
        if not binding["episode_id"] or not binding["route_version"]:
            raise ValueError("RQ2_T_V2_MEMORY_LIFECYCLE_BINDING_INCOMPLETE")
        return binding

    @staticmethod
    def _binding(policy: FieldMemoryPolicy, context: Mapping[str, Any]) -> dict[str, Any]:
        return {key: context.get(key) for key in policy.binding_keys}

    def _invalid_reasons(
        self,
        field_id: str,
        item: _MemoryItem,
        *,
        now_s: float,
        context: Mapping[str, Any],
        events: Sequence[str],
    ) -> tuple[str, ...]:
        policy = FIELD_MEMORY_POLICIES[field_id]
        reasons = []
        age = float(now_s) - item.acquired_time_s
        if age < 0.0:
            reasons.append("CLOCK_REGRESSION")
        if age > policy.max_age_simulation_s + 1e-9:
            reasons.append("MAX_AGE_EXCEEDED")
        for event in events:
            if event in policy.invalidation_events:
                reasons.append(event)
        current_binding = self._binding(policy, context)
        for key, acquired in item.binding.items():
            current = current_binding.get(key)
            if acquired is not None and current is not None and acquired != current:
                reasons.append("BINDING_CHANGED:" + key)
        return tuple(dict.fromkeys(reasons))

    def update(
        self,
        fields: Mapping[str, Mapping[str, Any]],
        *,
        now_s: float,
        context: Mapping[str, Any],
        invalidation_events: Sequence[str] = (),
    ) -> dict[str, dict[str, Any]]:
        lifecycle = self._required_lifecycle_binding(context)
        if self._lifecycle_binding is None:
            self._lifecycle_binding = lifecycle
        elif lifecycle != self._lifecycle_binding:
            # Never allow retained evidence to cross an episode, active route,
            # or actor/referent binding.  Fail closed by invalidating every item
            # before evaluating the current frame.
            for field_id in sorted(self._items):
                self.events.append(
                    {
                        "event": "EVIDENCE_INVALIDATED",
                        "field_id": field_id,
                        "simulation_time_s": float(now_s),
                        "reason_codes": ["LIFECYCLE_BINDING_CHANGED"],
                    }
                )
            self._items.clear()
            self._lifecycle_binding = lifecycle
        output: dict[str, dict[str, Any]] = {}
        for field_id in FIELD_MEMORY_POLICIES:
            direct = copy.deepcopy(dict(fields[field_id]))
            policy = FIELD_MEMORY_POLICIES[field_id]
            item = self._items.get(field_id)
            invalid = (
                ()
                if item is None
                else self._invalid_reasons(
                    field_id,
                    item,
                    now_s=now_s,
                    context=context,
                    events=invalidation_events,
                )
            )
            if item is not None and invalid:
                self.events.append(
                    {
                        "event": "EVIDENCE_INVALIDATED",
                        "field_id": field_id,
                        "simulation_time_s": float(now_s),
                        "reason_codes": list(invalid),
                    }
                )
                self._items.pop(field_id, None)
                item = None

            if direct.get("status") == "AVAILABLE":
                acquired = float(now_s) if item is None else item.acquired_time_s
                direct["retention"] = {
                    "state": "DIRECTLY_OBSERVED",
                    "acquisition_time_s": acquired,
                    "latest_revalidation_time_s": float(now_s),
                    "age_simulation_s": 0.0,
                    "retention_mode": policy.retention_mode,
                    "max_age_simulation_s": policy.max_age_simulation_s,
                    "invalidation_conditions": list(policy.invalidation_events),
                    "binding": self._binding(policy, context),
                }
                direct["freshness_age_simulation_s"] = 0.0
                direct.pop("evidence_digest", None)
                direct["evidence_digest"] = canonical_sha256(direct)
                self._items[field_id] = _MemoryItem(
                    field=copy.deepcopy(direct),
                    acquired_time_s=acquired,
                    latest_revalidation_time_s=float(now_s),
                    binding=self._binding(policy, context),
                )
                output[field_id] = direct
                continue

            if item is not None and policy.max_age_simulation_s > 0.0:
                retained = copy.deepcopy(item.field)
                age = float(now_s) - item.acquired_time_s
                retained["simulation_timestamp_s"] = float(now_s)
                retained["freshness_age_simulation_s"] = age
                retained["evidence_grade"] = "RETAINED_RUNTIME_OBSERVED"
                retained["reason_codes"] = ["RETAINED_PREVIOUSLY_ESTABLISHED_EVIDENCE"]
                retained["retention"] = {
                    "state": "RETAINED",
                    "acquisition_time_s": item.acquired_time_s,
                    "latest_revalidation_time_s": item.latest_revalidation_time_s,
                    "age_simulation_s": age,
                    "retention_mode": policy.retention_mode,
                    "max_age_simulation_s": policy.max_age_simulation_s,
                    "invalidation_conditions": list(policy.invalidation_events),
                    "binding": dict(item.binding),
                }
                retained.pop("evidence_digest", None)
                retained["evidence_digest"] = canonical_sha256(retained)
                output[field_id] = retained
                self.events.append(
                    {
                        "event": "EVIDENCE_RETAINED",
                        "field_id": field_id,
                        "simulation_time_s": float(now_s),
                        "age_simulation_s": age,
                    }
                )
                continue

            if invalid:
                direct["reason_codes"] = list(direct.get("reason_codes") or ()) + [
                    "MEMORY_INVALIDATED:" + reason for reason in invalid
                ]
                direct.pop("evidence_digest", None)
                direct["evidence_digest"] = canonical_sha256(direct)
            output[field_id] = direct
        return output

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "driveclarify.rq2_t_v2.temporal_memory_snapshot.v1",
            "retained_field_ids": sorted(self._items),
            "event_count": len(self.events),
            "events": copy.deepcopy(self.events),
            "lifecycle_binding": copy.deepcopy(self._lifecycle_binding),
        }


__all__ = ["FIELD_MEMORY_POLICIES", "FieldMemoryPolicy", "TemporalEvidenceMemory"]
