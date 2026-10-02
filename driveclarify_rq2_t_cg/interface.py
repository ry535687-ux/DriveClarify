"""Offline controlled evidence and same-source B0/B1/B2/B3 construction."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import (
    adapt_production_history_row, canonical_sha256, clarification_actionable,
    epistemic_evidence_sufficient, full_evidence_available,
)
from driveclarify_rq2_t.types import EVIDENCE_FIELD_IDS

from .contracts import ALLOWED_USAGE, EVIDENCE_GRADE, assert_no_true_intent
from .memory import ControlledTemporalMemory, controlled_label


def _field(
    field_id: str, *, available: bool, value: Any, now_s: float, frame_id: int,
    observation_id: str, owner: str, reason: str = "CONTROLLED_EVENT_NOT_CURRENT",
) -> dict[str, Any]:
    if field_id not in EVIDENCE_FIELD_IDS:
        raise ValueError("RQ2_T_CG_UNKNOWN_FIELD:" + field_id)
    result = {
        "field_id": field_id, "status": "AVAILABLE" if available else "UNKNOWN",
        "value": value if available else None,
        "units": "CERTIFIED_CONTROLLED_EVENT_STATE" if available else None,
        "frame": "EXACT_NATIVE_SOURCE_FRAME", "simulation_timestamp_s": float(now_s),
        "source_frame_id": int(frame_id), "source_observation_id": str(observation_id),
        "owner": owner, "evidence_grade": EVIDENCE_GRADE,
        "allowed_usage": ALLOWED_USAGE, "freshness_age_simulation_s": 0.0,
        "dependencies": ["independently certified native event"],
        "reason_codes": [] if available else [reason], "retention": None,
        "production_eligible": False, "control_eligible": False, "safety_authority": False,
    }
    result["evidence_digest"] = canonical_sha256(result)
    return result


def conservative_v1_history_row(frame_id: int, observation_id: str) -> Mapping[str, Any]:
    reason = ["NO_PRODUCTION_DECISION_WINDOW_AT_SOURCE_OBSERVATION"]
    return {
        "source_frame_id": int(frame_id), "source_observation_id": str(observation_id),
        "candidate_ids": [], "current_planning_ambiguity_active": False,
        "planning_effective_k": None, "candidate_relationship": None,
        "m2b_inputs": {
            "semantic_state": None, "active_candidate_count": 0,
            "multiple_plausible_interpretations": False, "answer_changes_decision": None,
            "hard_safety_gate": None, "hard_rule_gate": None, "safe_holding_available": None,
            "evidence": {
                "source": {"source_frame_id": int(frame_id), "source_observation_id": str(observation_id)},
                "current_action": {"availability": "UNKNOWN", "reason_codes": reason},
                "future_obligation": {"availability": "UNKNOWN", "rows": [], "reason_codes": reason},
                "recoverability": {"availability": "UNKNOWN", "status": "UNKNOWN", "reason_codes": reason},
                "shared_action_lease": {"valid": False},
            },
        },
    }


def make_b0(
    *, frame_id: int, observation_id: str, now_s: float, scene_id: str,
    episode_id: str, seed: int, ambiguity_type: str, map_name: str,
    route_identity: str, commitment_certificate_sha256: str,
) -> Mapping[str, Any]:
    return adapt_production_history_row(
        conservative_v1_history_row(frame_id, observation_id),
        simulation_time_s=now_s, ambiguity_type=ambiguity_type, scene_id=scene_id,
        episode_id=episode_id, seed=seed, map_name=map_name,
        route_identity=route_identity,
        commitment_certificate_sha256=commitment_certificate_sha256,
    )


def _semantic_values(candidate_ids: Sequence[str]) -> Mapping[str, Any]:
    return {
        # This literal is part of the unchanged V1 ClarificationActionable
        # authority.  Certification establishes alternatives, not a resolution.
        "semantic_state": "UNRESOLVED",
        "candidate_ids": list(candidate_ids), "active_candidate_count": len(candidate_ids),
        "multiple_reasonable_interpretations": len(candidate_ids) >= 2,
        "planning_relevant": True, "planning_effective_k": len(candidate_ids),
    }


def controlled_current_fields(
    b0: Mapping[str, Any], *, candidate_ids: Sequence[str], facts: Mapping[str, bool],
    candidate_bindings: Sequence[Mapping[str, Any]], invalidated: bool = False,
) -> dict[str, dict[str, Any]]:
    assert_no_true_intent(candidate_bindings)
    now_s, frame_id, observation_id = (
        float(b0["simulation_time_s"]), int(b0["source_frame_id"]), str(b0["source_observation_id"])
    )
    fields = {key: controlled_label(value, view="B1") for key, value in copy.deepcopy(b0["evidence_vector"]).items()}
    e1_available = bool(facts.get("candidate_set") and not invalidated)
    fields["E1_INTERPRETATION_VALIDITY"] = _field(
        "E1_INTERPRETATION_VALIDITY", available=e1_available,
        value=_semantic_values(candidate_ids), now_s=now_s, frame_id=frame_id,
        observation_id=observation_id, owner="CG_CERTIFIED_CANDIDATE_SET_OWNER",
        reason="CANDIDATE_SET_INVALID_OR_ABSENT",
    )
    grounding_value = {
        "grounding_complete": True,
        "candidate_groundings": [
            {"candidate_id": row["candidate_id"], "interpretation_id": row["interpretation_id"], "binding_id": row["binding_id"]}
            for row in candidate_bindings
        ],
    }
    fields["E2_GROUNDING"] = _field(
        "E2_GROUNDING", available=bool(facts.get("grounding") and not invalidated),
        value=grounding_value, now_s=now_s, frame_id=frame_id,
        observation_id=observation_id, owner="CG_INDEPENDENT_BINDING_EVENT_OWNER",
        reason="CERTIFIED_BINDING_EVENT_NOT_CURRENT",
    )
    fields["E4_FUTURE_OBLIGATION_RELATION"] = _field(
        "E4_FUTURE_OBLIGATION_RELATION", available=bool(facts.get("obligation") and not invalidated),
        value={"relation": "DIVERGENT", "authorization_eligible": True,
               "obligation_digests": [canonical_sha256(row["obligation_descriptor"]) for row in candidate_bindings]},
        now_s=now_s, frame_id=frame_id, observation_id=observation_id,
        owner="CG_CERTIFIED_TASK_OBLIGATION_EVENT_OWNER", reason="TASK_OBLIGATION_EVENT_NOT_CURRENT",
    )
    fields["E6_CANDIDATE_CONSEQUENCE_DIVERGENCE"] = _field(
        "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE", available=bool(facts.get("obligation") and not invalidated),
        value={"material_divergence": True, "candidate_relationship": "DIVERGENT",
               "distinct_obligation_count": len(candidate_bindings)},
        now_s=now_s, frame_id=frame_id, observation_id=observation_id,
        owner="CG_CERTIFIED_CONSEQUENCE_EVENT_OWNER", reason="CONSEQUENCE_EVENT_NOT_CURRENT",
    )
    fields["E9_ANSWER_CHANGES_ACTION"] = _field(
        "E9_ANSWER_CHANGES_ACTION", available=bool(facts.get("answer_changes_action") and not invalidated),
        value={"answer_changes_next_meaningful_decision": True}, now_s=now_s,
        frame_id=frame_id, observation_id=observation_id,
        owner="CG_CANDIDATE_TASK_DIVERGENCE_OWNER", reason="PASSENGER_SEMANTIC_CONSTRAINT_OR_TASK_DIVERGENCE_NOT_ESTABLISHED",
    )
    fields["E7_SAFETY_RULE_HOLDING"] = _field(
        "E7_SAFETY_RULE_HOLDING", available=bool(facts.get("holding") and not invalidated),
        value={"information_action_safe": True, "motion_rule_admissible": True,
               "safe_holding_available": True, "shared_action_lease_valid": True,
               "safe_holding_or_shared_action_admissible": True},
        now_s=now_s, frame_id=frame_id, observation_id=observation_id,
        owner="CG_NATIVE_SHARED_PREFIX_HOLDING_EVENT_OWNER", reason="HOLDING_EVENT_NOT_CURRENT_OR_INVALIDATED",
    )
    fields["E5_ROUTE_LANE_TOPOLOGY_RELATION"] = _field(
        "E5_ROUTE_LANE_TOPOLOGY_RELATION", available=bool(facts.get("topology") and not invalidated),
        value={"all_physical_connectors_available": True, "candidate_topology_count": len(candidate_bindings)},
        now_s=now_s, frame_id=frame_id, observation_id=observation_id,
        owner="CG_NATIVE_ROUTE_PROGRESS_TOPOLOGY_EVENT_OWNER", reason="TOPOLOGY_EVENT_NOT_CURRENT",
    )
    return fields


def build_views_for_source(
    *, b0: Mapping[str, Any], candidate_bindings: Sequence[Mapping[str, Any]],
    facts: Mapping[str, bool], b3_facts: Mapping[str, bool], context: Mapping[str, Any],
    memory: ControlledTemporalMemory, invalidation_events: Sequence[str],
    invalidated: bool, clarification_deadline_s: float,
) -> Mapping[str, Any]:
    candidate_ids = [str(row["candidate_id"]) for row in candidate_bindings]
    b1 = copy.deepcopy(dict(b0))
    b1["evidence_vector"] = controlled_current_fields(
        b0, candidate_ids=candidate_ids, facts=facts,
        candidate_bindings=candidate_bindings, invalidated=invalidated,
    )
    b1["interpretation_ids"] = candidate_ids
    b1["controlled_interface_id"] = "CERTIFIED_CANDIDATE_EVIDENCE_INTERFACE_V1"
    b1["temporal_memory_enabled"] = False
    b1["EpistemicEvidenceSufficient"] = epistemic_evidence_sufficient(b1)
    b1["FullEvidenceAvailable"] = full_evidence_available(b1)
    b1["ClarificationActionable"] = clarification_actionable(b1, clarification_deadline_simulation_s=clarification_deadline_s)
    b1["ClarificationOpportunity"] = bool(b1["EpistemicEvidenceSufficient"] and b1["ClarificationActionable"])

    b2 = copy.deepcopy(b1)
    b2["evidence_vector"] = memory.update(
        b1["evidence_vector"], now_s=float(b0["simulation_time_s"]),
        context=context, invalidation_events=invalidation_events,
    )
    b2["temporal_memory_enabled"] = True
    b2["EpistemicEvidenceSufficient"] = epistemic_evidence_sufficient(b2)
    b2["FullEvidenceAvailable"] = full_evidence_available(b2)
    b2["ClarificationActionable"] = clarification_actionable(b2, clarification_deadline_simulation_s=clarification_deadline_s)
    b2["ClarificationOpportunity"] = bool(b2["EpistemicEvidenceSufficient"] and b2["ClarificationActionable"])

    b3 = copy.deepcopy(b1)
    b3["evidence_vector"] = controlled_current_fields(
        b0, candidate_ids=candidate_ids, facts=b3_facts,
        candidate_bindings=candidate_bindings, invalidated=invalidated,
    )
    for field_id in ("E3_CURRENT_ACTION_RELATION", "E8_RECOVERABILITY"):
        b3["evidence_vector"][field_id] = _field(
            field_id, available=not invalidated,
            value=({"relation": "SHARED"} if field_id.startswith("E3") else {"status": "RECOVERABLE"}),
            now_s=float(b0["simulation_time_s"]), frame_id=int(b0["source_frame_id"]),
            observation_id=str(b0["source_observation_id"]), owner="CG_OFFLINE_COMPLETE_EVIDENCE_UPPER_BOUND",
            reason="B3_COMPLETE_EVIDENCE_NOT_AVAILABLE_FOR_NEGATIVE_OR_INVALIDATED_SCENE",
        )
    b3["production_full_plan_coverage"] = not invalidated
    b3["offline_only"] = True
    b3["temporal_memory_enabled"] = False
    b3["EpistemicEvidenceSufficient"] = epistemic_evidence_sufficient(b3)
    b3["FullEvidenceAvailable"] = full_evidence_available(b3)
    b3["ClarificationActionable"] = clarification_actionable(b3, clarification_deadline_simulation_s=clarification_deadline_s)
    b3["ClarificationOpportunity"] = bool(b3["EpistemicEvidenceSufficient"] and b3["ClarificationActionable"])
    for view in (b1, b2, b3):
        view["TTCmt_s"] = float(context["commitment_time_s"]) - float(b0["simulation_time_s"])
        view["clarification_deadline_simulation_time_s"] = clarification_deadline_s
        view["remaining_decision_margin_s"] = clarification_deadline_s - float(b0["simulation_time_s"])
        view["sufficiency_contract"] = "UNCHANGED_FROM_V1"
        view.pop("observation_digest", None)
        view["observation_digest"] = canonical_sha256(view)
    source = {
        "source_frame_id": b0["source_frame_id"],
        "source_observation_id": b0["source_observation_id"],
        "simulation_time_s": b0["simulation_time_s"],
    }
    record = {
        "schema_version": "driveclarify.rq2_t_cg.paired_views.v1",
        "source_identity": source, "views": {"B0": b0, "B1": b1, "B2": b2, "B3": b3},
        "current_facts": dict(facts), "invalidation_events": list(invalidation_events),
        "same_native_source_identity": all(
            view["source_frame_id"] == source["source_frame_id"]
            and view["source_observation_id"] == source["source_observation_id"]
            for view in (b0, b1, b2, b3)
        ),
        "production_control_read_or_write_count": 0,
    }
    record["record_digest"] = canonical_sha256(record)
    return record


__all__ = [
    "ControlledTemporalMemory", "build_views_for_source", "conservative_v1_history_row",
    "controlled_current_fields", "make_b0",
]
