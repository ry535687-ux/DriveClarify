"""V4 B1/B2 composition with the unchanged V1 sufficiency authority."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256, epistemic_evidence_sufficient, full_evidence_available
from driveclarify_rq2_t_v2.method import EvidenceEnabledTemporalMethodV2
from driveclarify_rq2_t_e2_v3.memory import E2LineageMemory, E2_INVALIDATION_EVENTS
from driveclarify_rq2_t_e2_v3.method import EvidenceEnabledTrackedAssociationMethodV3

from .association import AssociationConfiguration
from .contracts import CandidateObjectSpec, METHOD_ID
from .provider import provide_grounding_e2_v4


class EvidenceEnabledDomainInvariantMethodV4(EvidenceEnabledTrackedAssociationMethodV3):
    def __init__(self, *, use_temporal_memory: bool, configuration: AssociationConfiguration) -> None:
        self.use_temporal_memory = bool(use_temporal_memory)
        self.configuration = configuration
        self.thresholds = configuration.thresholds
        self.v2 = EvidenceEnabledTemporalMethodV2(use_temporal_memory=use_temporal_memory)
        self.e2_memory = E2LineageMemory()
        self.observations = []

    def observe(
        self,
        v1_observation: Mapping[str, Any],
        *,
        candidates: Sequence[CandidateObjectSpec],
        association: Mapping[str, Any],
        tracker_snapshot: Mapping[str, Any],
        runtime_signals: Mapping[str, Any],
        history_row: Mapping[str, Any],
        invalidation_events: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        row = self.v2.observe(
            copy.deepcopy(dict(v1_observation)), runtime_signals=runtime_signals,
            history_row=history_row, invalidation_events=invalidation_events,
        )
        e2_invalidations = tuple(row for row in invalidation_events if row in E2_INVALIDATION_EVENTS)
        direct = provide_grounding_e2_v4(
            now_s=float(row["simulation_time_s"]), frame_id=row.get("source_frame_id"),
            observation_id=str(row.get("source_observation_id") or ""), candidates=candidates,
            association=association, tracker_snapshot=tracker_snapshot,
            configuration=self.configuration, active_invalidation_events=e2_invalidations,
        )
        context = {
            "episode_id": row.get("episode_id"),
            "route_version": runtime_signals.get("route_version"),
            "candidate_set_digest": runtime_signals.get("candidate_set_digest"),
            "environment_digest": runtime_signals.get("environment_digest"),
        }
        e2 = self.e2_memory.update(
            direct, now_s=float(row["simulation_time_s"]), context=context,
            invalidation_events=e2_invalidations,
        ) if self.use_temporal_memory else direct
        row["evidence_vector"]["E2_GROUNDING"] = e2
        self._apply_certified_candidate_condition(row, candidates)
        row["schema_version"] = "driveclarify.rq2_t_e2_v4.temporal_observation.v1"
        row["scene_version"] = str(row.get("scene_id")) + ":" + METHOD_ID
        row["certified_candidate_interpretations"] = [item.to_dict() for item in candidates]
        row["E2_V4_SCIENTIFIC_METHOD_CHANGES"] = [
            "DOMAIN_INVARIANT_MISSING_AWARE_REPRESENTATION",
            "CERTIFIED_FAMILY_MASKS_WITHOUT_SCENE_OR_ROUTE_SELECTION",
            "EXPLICIT_CONTRADICTION_ONLY_HARD_GATES",
            "ACQUISITION_OPPORTUNITY_TRACK_MATURITY_CLOCK",
            "SCENE_LEVEL_FINITE_CALIBRATION_WITH_BLIND_FIREWALL",
        ]
        row["EpistemicEvidenceSufficient"] = epistemic_evidence_sufficient(row)
        row["FullEvidenceAvailable"] = full_evidence_available(row)
        row["sufficiency_contract"] = "UNCHANGED_FROM_V1"
        row["temporal_memory_enabled"] = self.use_temporal_memory
        row.pop("observation_digest", None)
        row["observation_digest"] = canonical_sha256(row)
        self.observations.append(copy.deepcopy(row))
        return row


__all__ = ["EvidenceEnabledDomainInvariantMethodV4"]
