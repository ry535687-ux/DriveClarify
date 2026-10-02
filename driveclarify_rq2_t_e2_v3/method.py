"""B1/B2 evidence composition with unchanged V1 sufficiency authority."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256, epistemic_evidence_sufficient, full_evidence_available
from driveclarify_rq2_t_v2.method import EvidenceEnabledTemporalMethodV2

from .association import AssociationThresholds
from .contracts import CandidateObjectSpec
from .memory import E2LineageMemory, E2_INVALIDATION_EVENTS
from .provider import provide_grounding_e2_v3


class EvidenceEnabledTrackedAssociationMethodV3:
    def __init__(self, *, use_temporal_memory: bool, thresholds: AssociationThresholds) -> None:
        self.use_temporal_memory = bool(use_temporal_memory)
        self.thresholds = thresholds
        self.v2 = EvidenceEnabledTemporalMethodV2(use_temporal_memory=use_temporal_memory)
        self.e2_memory = E2LineageMemory()
        self.observations = []

    @staticmethod
    def _available_field(
        template: Mapping[str, Any], *, value: Mapping[str, Any], owner: str,
        units: str, frame: str, dependencies: Sequence[str],
    ) -> Mapping[str, Any]:
        field = copy.deepcopy(dict(template))
        field.update({
            "status": "AVAILABLE", "value": copy.deepcopy(dict(value)),
            "units": units, "frame": frame, "owner": owner,
            "evidence_grade": "AVAILABLE_RUNTIME_OBSERVED_CERTIFIED_CANDIDATE_CONDITION",
            "allowed_usage_purpose": "RQ2_T_V2_PRE_SCIENCE_ENGINEERING_ONLY",
            "freshness_age_simulation_s": 0.0, "dependencies": list(dependencies),
            "reason_codes": [], "retention": None,
        })
        field.pop("evidence_digest", None)
        field["evidence_digest"] = canonical_sha256(field)
        return field

    def _apply_certified_candidate_condition(
        self, row: Mapping[str, Any], candidates: Sequence[CandidateObjectSpec],
    ) -> None:
        fields = row["evidence_vector"]
        distinct = bool(
            len(candidates) >= 2
            and len({item.candidate_id for item in candidates}) == len(candidates)
            and len({item.interpretation_id for item in candidates}) == len(candidates)
            and len({canonical_sha256(item.typed_projection()) for item in candidates}) == len(candidates)
        )
        if distinct:
            fields["E1_INTERPRETATION_VALIDITY"] = self._available_field(
                fields["E1_INTERPRETATION_VALIDITY"],
                value={
                    "semantic_state": "UNRESOLVED_CERTIFIED_CANDIDATE_CONDITION",
                    "candidate_ids": [item.candidate_id for item in candidates],
                    "active_candidate_count": len(candidates),
                    "multiple_reasonable_interpretations": True,
                    "planning_relevant": True, "planning_effective_k": len(candidates),
                },
                owner="E2_V3_CERTIFIED_CANDIDATE_INTERPRETATION_OWNER",
                units="STRUCTURED_CATEGORICAL_AND_COUNT",
                frame="CERTIFIED_CANDIDATE_SET_AT_SOURCE_OBSERVATION",
                dependencies=("prospectively certified typed candidate interpretations",),
            )
        e2 = fields["E2_GROUNDING"]
        if not distinct or e2.get("status") != "AVAILABLE":
            return
        groundings = e2.get("value", {}).get("candidate_groundings", ())
        if len(groundings) != len(candidates):
            return
        obligations = [
            canonical_sha256({
                "candidate_id": item.get("candidate_id"),
                "interpretation_id": item.get("interpretation_id"),
                "track_id": item.get("track_id"),
                "typed_candidate_spec": item.get("typed_candidate_spec"),
            })
            for item in groundings if isinstance(item, Mapping)
        ]
        if len(obligations) != len(candidates) or len(set(obligations)) < 2:
            return
        fields["E4_FUTURE_OBLIGATION_RELATION"] = self._available_field(
            fields["E4_FUTURE_OBLIGATION_RELATION"],
            value={
                "relation": "DIVERGENT", "obligation_digests": obligations,
                "authorization_eligible": True,
                "condition": "CERTIFIED_CANDIDATE_INTERPRETATIONS_WITH_QUALIFIED_E2_BINDINGS",
            },
            owner="E2_V3_CERTIFIED_CANDIDATE_FUTURE_OBLIGATION_OWNER",
            units="CATEGORICAL_RELATION_AND_DIGEST_IDENTITIES",
            frame="CERTIFIED_CANDIDATE_FUTURE_OBLIGATION_SET",
            dependencies=("E1_INTERPRETATION_VALIDITY", "E2_GROUNDING"),
        )
        fields["E6_CANDIDATE_CONSEQUENCE_DIVERGENCE"] = self._available_field(
            fields["E6_CANDIDATE_CONSEQUENCE_DIVERGENCE"],
            value={
                "material_divergence": True,
                "candidate_relationship": "DISTINCT_REFERENT_CONDITIONED_FUTURE_OBLIGATIONS",
                "distinct_obligation_count": len(set(obligations)),
            },
            owner="E2_V3_CERTIFIED_CANDIDATE_CONSEQUENCE_OWNER",
            units="BOOLEAN_AND_CATEGORICAL_RELATION",
            frame="CERTIFIED_CANDIDATE_FUTURE_OBLIGATION_SET",
            dependencies=("E4_FUTURE_OBLIGATION_RELATION",),
        )
        fields["E9_ANSWER_CHANGES_ACTION"] = self._available_field(
            fields["E9_ANSWER_CHANGES_ACTION"],
            value={
                "answer_changes_next_meaningful_decision": True,
                "basis": "CHOOSING_BETWEEN_DISTINCT_REFERENT_CONDITIONED_OBLIGATIONS",
            },
            owner="E2_V3_CERTIFIED_CANDIDATE_ANSWER_ACTION_OWNER",
            units="BOOLEAN", frame="CERTIFIED_CANDIDATE_FUTURE_OBLIGATION_SET",
            dependencies=("E1_INTERPRETATION_VALIDITY", "E4_FUTURE_OBLIGATION_RELATION"),
        )

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
        e2_invalidations = tuple(
            value for value in invalidation_events if value in E2_INVALIDATION_EVENTS
        )
        direct = provide_grounding_e2_v3(
            now_s=float(row["simulation_time_s"]), frame_id=row.get("source_frame_id"),
            observation_id=str(row.get("source_observation_id") or ""), candidates=candidates,
            association=association, tracker_snapshot=tracker_snapshot,
            thresholds=self.thresholds, active_invalidation_events=e2_invalidations,
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
        row["schema_version"] = "driveclarify.rq2_t_e2_v3.temporal_observation.v1"
        row["scene_version"] = str(row.get("scene_id")) + ":E2_TRACKED_ASSOCIATION_V3"
        row["certified_candidate_interpretations"] = [item.to_dict() for item in candidates]
        row["E2_V3_SCIENTIFIC_METHOD_CHANGES"] = [
            "CERTIFIED_CANDIDATE_INTERPRETATIONS",
            "REPEATED_RUNTIME_GROUNDING_ACQUISITION",
            "PERSISTENT_MULTI_OBJECT_TRACKING",
            "TYPED_CANDIDATE_TO_TRACK_MATRIX",
            "UNIQUE_ONE_TO_ONE_ASSIGNMENT",
            "NON_PROBABILISTIC_IDENTITY_ASSOCIATION_SCORE",
            "PERSISTENT_LINEAGE_MEMORY_AND_FAIL_CLOSED_INVALIDATION",
            "CERTIFIED_CANDIDATE_CONDITIONED_OBLIGATION_RELATION",
        ]
        row["EpistemicEvidenceSufficient"] = epistemic_evidence_sufficient(row)
        row["FullEvidenceAvailable"] = full_evidence_available(row)
        row["sufficiency_contract"] = "UNCHANGED_FROM_V1"
        row["temporal_memory_enabled"] = self.use_temporal_memory
        row.pop("observation_digest", None)
        row["observation_digest"] = canonical_sha256(row)
        self.observations.append(copy.deepcopy(row))
        return row


__all__ = ["EvidenceEnabledTrackedAssociationMethodV3"]
