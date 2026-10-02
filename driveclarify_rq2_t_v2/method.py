"""Composition of V1 observations, V2 providers, and typed evidence memory."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional, Sequence

from driveclarify_rq2_t.measurement import (
    canonical_sha256,
    epistemic_evidence_sufficient,
    full_evidence_available,
)
from driveclarify_rq2_t.types import EVIDENCE_FIELD_IDS

from .memory import TemporalEvidenceMemory
from .providers import (
    assert_runtime_payload_has_no_oracle,
    provide_grounding_e2,
    provide_holding_safety_e7,
    provide_topology_e5,
)


class EvidenceEnabledTemporalMethodV2:
    """Default-detached observational method; owns no model or control APIs."""

    def __init__(self, *, use_temporal_memory: bool = True) -> None:
        self.use_temporal_memory = bool(use_temporal_memory)
        self.memory = TemporalEvidenceMemory()
        self.observations: list[dict[str, Any]] = []

    def observe(
        self,
        v1_observation: Mapping[str, Any],
        *,
        runtime_signals: Optional[Mapping[str, Any]] = None,
        history_row: Optional[Mapping[str, Any]] = None,
        invalidation_events: Sequence[str] = (),
    ) -> dict[str, Any]:
        signals = {} if runtime_signals is None else dict(runtime_signals)
        assert_runtime_payload_has_no_oracle(signals)
        row = copy.deepcopy(dict(v1_observation))
        now_s = float(row["simulation_time_s"])
        fields = copy.deepcopy(dict(row["evidence_vector"]))
        if set(fields) != set(EVIDENCE_FIELD_IDS):
            raise ValueError("RQ2_T_V2_REQUIRES_COMPLETE_V1_EVIDENCE_VECTOR")
        source_frame = row.get("source_frame_id")
        source_observation = str(row.get("source_observation_id") or "")
        candidate_ids = tuple(str(value) for value in row.get("interpretation_ids", ()) if value)

        fields["E2_GROUNDING"] = provide_grounding_e2(
            now_s=now_s,
            active_candidate_ids=candidate_ids,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            history_row=history_row,
            grounding_signal=signals.get("grounding"),
        )
        fields["E5_ROUTE_LANE_TOPOLOGY_RELATION"] = provide_topology_e5(
            now_s=now_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            route_version=signals.get("route_version"),
            environment_digest=signals.get("environment_digest"),
            topology_signal=signals.get("topology"),
        )
        fields["E7_SAFETY_RULE_HOLDING"] = provide_holding_safety_e7(
            now_s=now_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            history_row=history_row,
            safety_signal=signals.get("safety"),
        )

        context = {
            "episode_id": row.get("episode_id"),
            "candidate_set_digest": signals.get("candidate_set_digest")
            or canonical_sha256(candidate_ids),
            "instruction_digest": signals.get("instruction_digest"),
            "route_version": signals.get("route_version"),
            "environment_digest": signals.get("environment_digest"),
            "topology_boundary_id": signals.get("topology_boundary_id"),
            "safety_state_digest": signals.get("safety_state_digest"),
            "holding_lease_id": signals.get("holding_lease_id"),
            "dynamics_state_digest": signals.get("dynamics_state_digest"),
            "source_frame_id": source_frame,
            "actor_binding_digest": signals.get("actor_binding_digest"),
        }
        if self.use_temporal_memory:
            fields = self.memory.update(
                fields,
                now_s=now_s,
                context=context,
                invalidation_events=invalidation_events,
            )
        row["schema_version"] = "driveclarify.rq2_t_v2.temporal_observation.v1"
        row["scene_version"] = str(row.get("scene_id")) + ":RQ2_T_V2_METHOD_V1"
        row["evidence_vector"] = fields
        row["V2_SCIENTIFIC_METHOD_CHANGES"] = [
            "DECOUPLED_E2_FROM_AGGREGATE_FUTURE_TOPOLOGY_STATUS",
            "LOCAL_DEPLOYABLE_E5_ORDER_TOPOLOGY_PROVIDER",
            "EXPLICIT_E7_HOLDING_OR_SHARED_ACTION_ADMISSIBILITY",
            "FIELD_TYPED_TEMPORAL_MEMORY_AND_INVALIDATION",
            "OBSERVATION_RESOLVABILITY_CLASSIFICATION",
        ]
        row["EpistemicEvidenceSufficient"] = epistemic_evidence_sufficient(row)
        row["FullEvidenceAvailable"] = full_evidence_available(row)
        row["sufficiency_contract"] = "UNCHANGED_FROM_V1"
        row["temporal_memory_enabled"] = self.use_temporal_memory
        row.pop("observation_digest", None)
        row["observation_digest"] = canonical_sha256(row)
        self.observations.append(copy.deepcopy(row))
        return row


__all__ = ["EvidenceEnabledTemporalMethodV2"]
