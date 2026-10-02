"""Field-specific persistent E2 V3 lineage memory."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256


E2_MEMORY_MAX_AGE_SIMULATION_S = 2.0
E2_INVALIDATION_EVENTS = frozenset(
    {
        "TRACK_CONFLICT", "ID_SWITCH_CONFLICT", "DUPLICATE_TRACK_AMBIGUITY",
        "LINEAGE_BREAK", "LOSS_GRACE_EXPIRED", "INCOMPATIBLE_REACQUISITION",
        "ROUTE_CHANGED", "ENVIRONMENT_CHANGED", "SEMANTIC_CANDIDATE_SET_CHANGED",
        "EPISODE_CHANGED", "ROUTE_LANE_BINDING_CONTRADICTION",
    }
)


class E2LineageMemory:
    def __init__(self) -> None:
        self._record: Optional[Mapping[str, Any]] = None
        self.events = []

    @staticmethod
    def _binding(field: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        groundings = field.get("value", {}).get("candidate_groundings", ()) if field.get("status") == "AVAILABLE" else ()
        return {
            "episode_id": context.get("episode_id"),
            "route_version": context.get("route_version"),
            "candidate_set_digest": context.get("candidate_set_digest"),
            "environment_digest": context.get("environment_digest"),
            "candidate_track_lineages": sorted(
                (str(row.get("candidate_id")), str(row.get("track_id")))
                for row in groundings if isinstance(row, Mapping)
            ),
        }

    def update(
        self,
        direct: Mapping[str, Any],
        *,
        now_s: float,
        context: Mapping[str, Any],
        invalidation_events: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        current = copy.deepcopy(dict(direct))
        forbidden = sorted(set(str(value) for value in invalidation_events).intersection(E2_INVALIDATION_EVENTS))
        if self._record is not None:
            record_binding = self._record["binding"]
            for key in ("episode_id", "route_version", "candidate_set_digest", "environment_digest"):
                left, right = record_binding.get(key), context.get(key)
                if left is not None and right is not None and left != right:
                    forbidden.append("BINDING_CHANGED:" + key)
            age = float(now_s) - float(self._record["acquisition_time_s"])
            if age < 0.0:
                forbidden.append("CLOCK_REGRESSION")
            if age > E2_MEMORY_MAX_AGE_SIMULATION_S + 1e-9:
                forbidden.append("MAX_AGE_EXCEEDED")
        if forbidden:
            self.events.append({
                "event": "E2_BINDING_INVALIDATED", "simulation_time_s": float(now_s),
                "reason_codes": sorted(set(forbidden)),
            })
            self._record = None
        if current.get("status") == "AVAILABLE":
            binding = self._binding(current, context)
            if not binding["episode_id"] or not binding["route_version"] or len(binding["candidate_track_lineages"]) < 2:
                raise ValueError("E2_V3_MEMORY_BINDING_INCOMPLETE")
            acquisition = float(now_s) if self._record is None else float(self._record["acquisition_time_s"])
            current["retention"] = {
                "state": "DIRECTLY_OBSERVED", "acquisition_time_s": acquisition,
                "latest_validation_time_s": float(now_s), "age_simulation_s": 0.0,
                "maximum_age_simulation_s": E2_MEMORY_MAX_AGE_SIMULATION_S,
                "binding": copy.deepcopy(binding),
                "invalidation_events": sorted(E2_INVALIDATION_EVENTS),
            }
            current["freshness_age_simulation_s"] = 0.0
            current.pop("evidence_digest", None)
            current["evidence_digest"] = canonical_sha256(current)
            self._record = {
                "field": copy.deepcopy(current), "binding": binding,
                "acquisition_time_s": acquisition, "latest_validation_time_s": float(now_s),
            }
            return current
        if self._record is not None:
            retained = copy.deepcopy(dict(self._record["field"]))
            age = float(now_s) - float(self._record["acquisition_time_s"])
            retained["simulation_timestamp_s"] = float(now_s)
            retained["freshness_age_simulation_s"] = age
            retained["evidence_grade"] = "RETAINED_RUNTIME_OBSERVED"
            retained["reason_codes"] = ["RETAINED_PREVIOUSLY_ESTABLISHED_E2_V3_BINDING"]
            retained["retention"] = {
                "state": "RETAINED", "acquisition_time_s": self._record["acquisition_time_s"],
                "latest_validation_time_s": self._record["latest_validation_time_s"],
                "age_simulation_s": age,
                "maximum_age_simulation_s": E2_MEMORY_MAX_AGE_SIMULATION_S,
                "binding": copy.deepcopy(self._record["binding"]),
                "invalidation_events": sorted(E2_INVALIDATION_EVENTS),
            }
            retained.pop("evidence_digest", None)
            retained["evidence_digest"] = canonical_sha256(retained)
            self.events.append({
                "event": "E2_BINDING_RETAINED", "simulation_time_s": float(now_s),
                "age_simulation_s": age,
            })
            return retained
        if forbidden:
            current["reason_codes"] = list(current.get("reason_codes") or ()) + [
                "E2_MEMORY_INVALIDATED:" + reason for reason in sorted(set(forbidden))
            ]
            current.pop("evidence_digest", None)
            current["evidence_digest"] = canonical_sha256(current)
        return current

    def snapshot(self) -> Mapping[str, Any]:
        return {
            "schema_version": "driveclarify.e2_v3.memory_snapshot.v1",
            "retained": self._record is not None,
            "record": copy.deepcopy(self._record), "events": copy.deepcopy(self.events),
        }


__all__ = ["E2LineageMemory", "E2_INVALIDATION_EVENTS", "E2_MEMORY_MAX_AGE_SIMULATION_S"]
