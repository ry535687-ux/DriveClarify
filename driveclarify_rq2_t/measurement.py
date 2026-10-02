"""Pure RQ2-T evidence adaptation and post-episode temporal joins.

The scientific time domain in this module is CARLA simulation time.  Historic
monotonic timestamps may be retained as provenance but are never used to
compute TTCmt, the clarification deadline, or an opportunity window.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Sequence

from .types import EVIDENCE_FIELD_IDS, EvidenceStatus, OpportunityWindowStatus


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    ).hexdigest()


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


@dataclass(frozen=True)
class DeadlineContract:
    answer_latency_simulation_s: float
    answer_to_first_eligible_action_ticks: int
    control_reserve_ticks: int
    fixed_delta_seconds: float = 0.05
    clock_domain: str = "CARLA_SIMULATION_TIME"
    answer_term_classification: str = "PROSPECTIVELY_PREVIOUSLY_FIXED"
    action_term_classification: str = "EMPIRICALLY_MEASURED"
    control_term_classification: str = "PROSPECTIVELY_PREVIOUSLY_FIXED"
    version: str = "driveclarify.rq2_t.deadline.v2"

    def __post_init__(self) -> None:
        if self.clock_domain != "CARLA_SIMULATION_TIME":
            raise ValueError("RQ2_T_SCIENTIFIC_CLOCK_MUST_BE_CARLA_SIMULATION_TIME")
        if not (
            _finite(self.answer_latency_simulation_s)
            and float(self.answer_latency_simulation_s) >= 0.0
            and _finite(self.fixed_delta_seconds)
            and float(self.fixed_delta_seconds) > 0.0
            and isinstance(self.answer_to_first_eligible_action_ticks, int)
            and not isinstance(self.answer_to_first_eligible_action_ticks, bool)
            and self.answer_to_first_eligible_action_ticks >= 0
            and isinstance(self.control_reserve_ticks, int)
            and not isinstance(self.control_reserve_ticks, bool)
            and self.control_reserve_ticks >= 0
        ):
            raise ValueError("RQ2_T_DEADLINE_TERM_INVALID")

    @property
    def answer_to_first_eligible_action_simulation_s(self) -> float:
        return float(
            self.answer_to_first_eligible_action_ticks * self.fixed_delta_seconds
        )

    @property
    def control_reserve_simulation_s(self) -> float:
        return float(self.control_reserve_ticks * self.fixed_delta_seconds)

    @property
    def total_reserved_simulation_s(self) -> float:
        return float(
            self.answer_latency_simulation_s
            + self.answer_to_first_eligible_action_simulation_s
            + self.control_reserve_simulation_s
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["answer_to_first_eligible_action_simulation_s"] = (
            self.answer_to_first_eligible_action_simulation_s
        )
        value["control_reserve_simulation_s"] = self.control_reserve_simulation_s
        value["total_reserved_simulation_s"] = self.total_reserved_simulation_s
        value["contract_digest"] = canonical_sha256(value)
        return value


def _field(
    field_id: str,
    *,
    status: EvidenceStatus,
    value: Any,
    simulation_time_s: float,
    source_frame_id: Any,
    source_observation_id: str,
    owner: str,
    evidence_grade: str,
    allowed_usage_purpose: str = "RQ2_T_OBSERVATIONAL_MEASUREMENT_ONLY",
    units: Optional[str] = None,
    frame: Optional[str] = None,
    freshness_age_simulation_s: Optional[float] = 0.0,
    dependencies: Sequence[str] = (),
    reason_codes: Sequence[str] = (),
) -> dict[str, Any]:
    if field_id not in EVIDENCE_FIELD_IDS:
        raise ValueError("RQ2_T_UNKNOWN_EVIDENCE_FIELD:" + field_id)
    if status is EvidenceStatus.AVAILABLE and value is None:
        raise ValueError("RQ2_T_AVAILABLE_VALUE_MISSING:" + field_id)
    if status is not EvidenceStatus.AVAILABLE and value is not None:
        raise ValueError("RQ2_T_UNAVAILABLE_VALUE_MUST_BE_NULL:" + field_id)
    if status is not EvidenceStatus.AVAILABLE and not tuple(reason_codes):
        raise ValueError("RQ2_T_UNAVAILABLE_REASON_REQUIRED:" + field_id)
    result = {
        "field_id": field_id,
        "status": status.value,
        "value": value,
        "units": units,
        "frame": frame,
        "simulation_timestamp_s": float(simulation_time_s),
        "source_frame_id": source_frame_id,
        "source_observation_id": source_observation_id,
        "owner": owner,
        "evidence_grade": evidence_grade,
        "allowed_usage_purpose": allowed_usage_purpose,
        "freshness_age_simulation_s": freshness_age_simulation_s,
        "dependencies": list(dependencies),
        "reason_codes": list(reason_codes),
    }
    result["evidence_digest"] = canonical_sha256(result)
    return result


def _unknown(
    field_id: str,
    *,
    simulation_time_s: float,
    source_frame_id: Any,
    source_observation_id: str,
    owner: str,
    reason_codes: Sequence[str],
    dependencies: Sequence[str] = (),
) -> dict[str, Any]:
    return _field(
        field_id,
        status=EvidenceStatus.UNKNOWN,
        value=None,
        simulation_time_s=simulation_time_s,
        source_frame_id=source_frame_id,
        source_observation_id=source_observation_id,
        owner=owner,
        evidence_grade="UNKNOWN_PRESERVED",
        dependencies=dependencies,
        reason_codes=reason_codes,
    )


def adapt_production_history_row(
    row: Mapping[str, Any],
    *,
    simulation_time_s: float,
    ambiguity_type: str,
    scene_id: str,
    episode_id: str,
    seed: int,
    map_name: str,
    route_identity: str,
    commitment_certificate_sha256: str,
) -> dict[str, Any]:
    """Translate an already-computed production history row without inference.

    The adapter consumes existing V3/Method-V2.7 facts.  It never calls the
    model, candidate supplier, planner, controller, PID, or a control writer.
    """

    if not _finite(simulation_time_s):
        raise ValueError("RQ2_T_SIMULATION_TIMESTAMP_INVALID")
    m2b = row.get("m2b_inputs")
    if not isinstance(m2b, Mapping):
        raise ValueError("RQ2_T_M2B_INPUTS_MISSING")
    evidence = m2b.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("RQ2_T_V3_EVIDENCE_MISSING")
    source = evidence.get("source") if isinstance(evidence.get("source"), Mapping) else {}
    source_frame = source.get("source_frame_id")
    source_observation = str(source.get("source_observation_id") or "")
    if source_frame is None or not source_observation:
        raise ValueError("RQ2_T_SOURCE_IDENTITY_MISSING")
    current = evidence.get("current_action") if isinstance(evidence.get("current_action"), Mapping) else {}
    future = evidence.get("future_obligation") if isinstance(evidence.get("future_obligation"), Mapping) else {}
    recoverability = evidence.get("recoverability") if isinstance(evidence.get("recoverability"), Mapping) else {}
    lease = evidence.get("shared_action_lease") if isinstance(evidence.get("shared_action_lease"), Mapping) else {}

    fields: dict[str, dict[str, Any]] = {}
    candidate_ids = tuple(str(value) for value in row.get("candidate_ids", ()) if value)
    semantic_state = str(m2b.get("semantic_state") or "")
    active_count = m2b.get("active_candidate_count")
    if candidate_ids and isinstance(active_count, int) and semantic_state:
        fields["E1_INTERPRETATION_VALIDITY"] = _field(
            "E1_INTERPRETATION_VALIDITY",
            status=EvidenceStatus.AVAILABLE,
            value={
                "semantic_state": semantic_state,
                "candidate_ids": list(candidate_ids),
                "active_candidate_count": active_count,
                "multiple_reasonable_interpretations": bool(
                    m2b.get("multiple_plausible_interpretations") is True
                    and active_count >= 2
                ),
                "planning_relevant": bool(
                    row.get("current_planning_ambiguity_active") is True
                ),
                "planning_effective_k": row.get("planning_effective_k"),
            },
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="PersistentAmbiguityRuntimeV1+MethodV2.7.semantic_state",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="STRUCTURED_CATEGORICAL_AND_COUNT",
            frame="SEMANTIC_CANDIDATE_SET_AT_SOURCE_OBSERVATION",
            dependencies=("semantic candidate set", "cross-observation confirmation"),
        )
    else:
        fields["E1_INTERPRETATION_VALIDITY"] = _unknown(
            "E1_INTERPRETATION_VALIDITY",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="PersistentAmbiguityRuntimeV1.semantic_state",
            reason_codes=("INTERPRETATION_SET_OR_SEMANTIC_STATE_MISSING",),
        )

    future_rows = future.get("rows") if isinstance(future.get("rows"), list) else []
    grounding_known = bool(
        future.get("availability") == "AVAILABLE"
        and future_rows
        and future.get("privileged_authorization_reads") == 0
    )
    if grounding_known:
        grounding_complete = all(
            isinstance(candidate, Mapping)
            and candidate.get("topology_binding_available") is True
            and candidate.get("semantic_fresh") is True
            and candidate.get("active_unresolved") is True
            and candidate.get("visibility") == "RUNTIME_OBSERVABLE"
            and candidate.get("privileged") is False
            and bool(candidate.get("referent_lineage_id"))
            and bool(candidate.get("semantic_sha256"))
            for candidate in future_rows
        )
        fields["E2_GROUNDING"] = _field(
            "E2_GROUNDING",
            status=EvidenceStatus.AVAILABLE,
            value={
                "grounding_complete": grounding_complete,
                "candidate_groundings": [
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "interpretation_id": candidate.get("interpretation_id"),
                        "referent_lineage_id": candidate.get("referent_lineage_id"),
                        "referent_description": candidate.get("referent_description"),
                        "source_kinds": candidate.get("source_kinds"),
                    }
                    for candidate in future_rows
                ],
            },
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3.FutureObligationRowV3",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="STRUCTURED_GROUNDING_IDENTITIES",
            frame="SOURCE_SENSOR_OBSERVATION_AND_ACTIVE_ROUTE",
            dependencies=("runtime RGB grounding", "semantic candidate identity"),
        )
    else:
        fields["E2_GROUNDING"] = _unknown(
            "E2_GROUNDING",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3.FutureObligationEvidenceV3",
            reason_codes=tuple(future.get("reason_codes") or ("GROUNDING_NOT_AVAILABLE",)),
        )

    if current.get("availability") == "AVAILABLE" and current.get("relation") in {
        "SHARED", "DIVERGENT"
    }:
        fields["E3_CURRENT_ACTION_RELATION"] = _field(
            "E3_CURRENT_ACTION_RELATION",
            status=EvidenceStatus.AVAILABLE,
            value={
                "relation": current.get("relation"),
                "local_coverage_available": current.get("local_coverage_available"),
                "lease_valid": lease.get("valid"),
                "lease_end_progress_m": lease.get("lease_end_progress_m"),
            },
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3.CurrentActionEvidenceV3",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="RELATION_AND_ROUTE_PROGRESS_M",
            frame="ACTIVE_ROUTE_PROGRESS",
            dependencies=("same-frame candidate plans", "authorized baseline plan"),
        )
    else:
        fields["E3_CURRENT_ACTION_RELATION"] = _unknown(
            "E3_CURRENT_ACTION_RELATION",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3.CurrentActionEvidenceV3",
            reason_codes=tuple(current.get("reason_codes") or ("CURRENT_ACTION_RELATION_UNKNOWN",)),
        )

    future_relation = future.get("relation")
    if future.get("availability") == "AVAILABLE" and future_relation in {
        "EQUIVALENT", "DIVERGENT"
    }:
        fields["E4_FUTURE_OBLIGATION_RELATION"] = _field(
            "E4_FUTURE_OBLIGATION_RELATION",
            status=EvidenceStatus.AVAILABLE,
            value={
                "relation": future_relation,
                "obligation_digests": [candidate.get("obligation_digest") for candidate in future_rows],
                "authorization_eligible": future.get("authorization_eligible"),
            },
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3.FutureObligationEvidenceV3",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="CATEGORICAL_RELATION_AND_DIGEST_IDENTITIES",
            frame="ACTIVE_ROUTE_FUTURE_OBLIGATION",
            dependencies=("qualified future obligations",),
        )
    else:
        fields["E4_FUTURE_OBLIGATION_RELATION"] = _unknown(
            "E4_FUTURE_OBLIGATION_RELATION",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3.FutureObligationEvidenceV3",
            reason_codes=tuple(future.get("reason_codes") or ("FUTURE_OBLIGATION_RELATION_UNKNOWN",)),
        )

    topology_dependencies_known = bool(
        future_rows
        and all(
            isinstance(candidate, Mapping)
            and candidate.get("topology_binding_available") is True
            and candidate.get("physical_connector_available") is not None
            and bool(candidate.get("route_version"))
            and bool(candidate.get("junction_id"))
            and bool(candidate.get("branch_id"))
            for candidate in future_rows
        )
    )
    if topology_dependencies_known and source.get("route_version") and source.get("environment_digest"):
        fields["E5_ROUTE_LANE_TOPOLOGY_RELATION"] = _field(
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            status=EvidenceStatus.AVAILABLE,
            value={
                "route_version": source.get("route_version"),
                "environment_digest": source.get("environment_digest"),
                "all_physical_connectors_available": all(
                    candidate.get("physical_connector_available") is True
                    for candidate in future_rows
                ),
                "candidate_topology": [
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "junction_id": candidate.get("junction_id"),
                        "branch_id": candidate.get("branch_id"),
                        "route_order_index": candidate.get("route_order_index"),
                    }
                    for candidate in future_rows
                ],
            },
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="LIVE_CARLA_HD_MAP_TOPOLOGY+active route owner",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="STRUCTURED_TOPOLOGY_IDENTITIES",
            frame="ACTIVE_ROUTE_TOPOLOGY",
            dependencies=("live map topology", "route/environment identity"),
        )
    else:
        fields["E5_ROUTE_LANE_TOPOLOGY_RELATION"] = _unknown(
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="LIVE_CARLA_HD_MAP_TOPOLOGY+active route owner",
            reason_codes=("ROUTE_LANE_TOPOLOGY_DEPENDENCY_UNKNOWN",),
        )

    obligation_digests = [
        candidate.get("obligation_digest")
        for candidate in future_rows
        if candidate.get("obligation_digest")
    ]
    if fields["E4_FUTURE_OBLIGATION_RELATION"]["status"] == "AVAILABLE":
        fields["E6_CANDIDATE_CONSEQUENCE_DIVERGENCE"] = _field(
            "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE",
            status=EvidenceStatus.AVAILABLE,
            value={
                "material_divergence": bool(
                    future_relation == "DIVERGENT"
                    and len(set(obligation_digests)) >= 2
                ),
                "candidate_relationship": row.get("candidate_relationship"),
                "distinct_obligation_count": len(set(obligation_digests)),
            },
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3 compatibility/future-obligation owners",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="BOOLEAN_AND_CATEGORICAL_RELATION",
            frame="CANDIDATE_FUTURE_OBLIGATION_SET",
            dependencies=("E4_FUTURE_OBLIGATION_RELATION",),
        )
    else:
        fields["E6_CANDIDATE_CONSEQUENCE_DIVERGENCE"] = _unknown(
            "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3 compatibility owner",
            reason_codes=("CONSEQUENCE_DIVERGENCE_DEPENDS_ON_UNKNOWN_E4",),
            dependencies=("E4_FUTURE_OBLIGATION_RELATION",),
        )

    safety = m2b.get("hard_safety_gate")
    rule = m2b.get("hard_rule_gate")
    safe_holding = m2b.get("safe_holding_available")
    if safety in (True, False) and rule in (True, False) and safe_holding in (True, False):
        fields["E7_SAFETY_RULE_HOLDING"] = _field(
            "E7_SAFETY_RULE_HOLDING",
            status=EvidenceStatus.AVAILABLE,
            value={
                "information_action_safe": safety,
                "motion_rule_admissible": rule,
                "safe_holding_available": safe_holding,
                "shared_action_lease_valid": lease.get("valid") is True,
                "safe_holding_or_shared_action_admissible": bool(
                    safety is True
                    and (safe_holding is True or lease.get("valid") is True)
                ),
            },
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="NativeCarlaRouteLocalEvidenceProvider+existing holding owner",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="BOOLEAN_CERTIFICATE_STATES",
            frame="SOURCE_CARLA_WORLD_AND_ACTIVE_ROUTE",
            dependencies=("same-frame hard safety", "rule", "holding authority"),
        )
    else:
        fields["E7_SAFETY_RULE_HOLDING"] = _unknown(
            "E7_SAFETY_RULE_HOLDING",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="NativeCarlaRouteLocalEvidenceProvider+existing holding owner",
            reason_codes=("SAFETY_RULE_OR_HOLDING_DEPENDENCY_UNKNOWN",),
        )

    recoverability_state = recoverability.get("status")
    if recoverability_state in {"RECOVERABLE", "NOT_RECOVERABLE"}:
        fields["E8_RECOVERABILITY"] = _field(
            "E8_RECOVERABILITY",
            status=EvidenceStatus.AVAILABLE,
            value={
                "status": recoverability_state,
                "all_candidates_recoverable": recoverability.get("all_candidates_recoverable"),
                "candidate_rows": recoverability.get("candidate_rows"),
            },
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3.RecoverabilityEvidenceV3",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="CATEGORICAL_AND_PER_CANDIDATE_STATES",
            frame="ACTIVE_ROUTE_AND_LANE_TOPOLOGY",
            dependencies=("topology", "lane", "dynamics", "freshness"),
        )
    else:
        fields["E8_RECOVERABILITY"] = _unknown(
            "E8_RECOVERABILITY",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="DecisionEvidenceV3.RecoverabilityEvidenceV3",
            reason_codes=tuple(recoverability.get("reason_codes") or ("RECOVERABILITY_UNKNOWN",)),
        )

    answer_changes = m2b.get("answer_changes_decision")
    if answer_changes in (True, False):
        fields["E9_ANSWER_CHANGES_ACTION"] = _field(
            "E9_ANSWER_CHANGES_ACTION",
            status=EvidenceStatus.AVAILABLE,
            value={"answer_changes_next_meaningful_decision": answer_changes},
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="PersistentAmbiguityRuntimeV1 target-obligation digest relation",
            evidence_grade="AVAILABLE_RUNTIME_OBSERVED",
            units="BOOLEAN",
            frame="CANDIDATE_TARGET_OBLIGATION_SET",
            dependencies=("candidate interpretation set", "target obligations"),
        )
    else:
        fields["E9_ANSWER_CHANGES_ACTION"] = _unknown(
            "E9_ANSWER_CHANGES_ACTION",
            simulation_time_s=simulation_time_s,
            source_frame_id=source_frame,
            source_observation_id=source_observation,
            owner="PersistentAmbiguityRuntimeV1 target-obligation digest relation",
            reason_codes=("ANSWER_ACTION_RELATION_UNKNOWN",),
        )

    if tuple(sorted(fields)) != tuple(sorted(EVIDENCE_FIELD_IDS)):
        raise AssertionError("RQ2_T_EVIDENCE_VECTOR_INCOMPLETE")
    result = {
        "schema_version": "driveclarify.rq2_t.temporal_observation.v2",
        "episode_id": episode_id,
        "scene_id": scene_id,
        "scene_version": scene_id + ":RQ2_T_MEASUREMENT_V1",
        "seed": int(seed),
        "ambiguity_type": ambiguity_type,
        "simulation_time_s": float(simulation_time_s),
        "source_frame_id": source_frame,
        "source_observation_id": source_observation,
        "ego_state": {"route_progress_m": row.get("current_progress_m")},
        "map": map_name,
        "route_identity": route_identity,
        "route_topology_owner": "active route+LIVE_CARLA_HD_MAP_TOPOLOGY",
        "interpretation_ids": list(candidate_ids),
        "evidence_vector": fields,
        "production_full_plan_coverage": bool(row.get("full_plan_coverage") is True),
        "production_decision": row.get("decision"),
        "production_compute_accounting": row.get("cumulative_compute_accounting"),
        "commitment_certificate_sha256": commitment_certificate_sha256,
        "commitment_state": "PRECOMMITMENT_UNRESOLVED",
        "TTCmt_s": None,
        "clarification_deadline_simulation_time_s": None,
        "remaining_decision_margin_s": None,
        "EpistemicEvidenceSufficient": False,
        "FullEvidenceAvailable": False,
        "ClarificationActionable": False,
        "ClarificationOpportunity": False,
        "temporal_join_status": "PENDING_POST_EPISODE_COMMITMENT_JOIN",
    }
    result["observation_digest"] = canonical_sha256(result)
    return result


def epistemic_evidence_sufficient(row: Mapping[str, Any]) -> bool:
    """Return evidence-semantic sufficiency without any timing or action gate."""

    fields = row["evidence_vector"]

    def available(field_id: str) -> bool:
        return fields[field_id]["status"] == EvidenceStatus.AVAILABLE.value

    if not all(
        available(field_id)
        for field_id in (
            "E1_INTERPRETATION_VALIDITY",
            "E2_GROUNDING",
            "E4_FUTURE_OBLIGATION_RELATION",
            "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE",
            "E9_ANSWER_CHANGES_ACTION",
        )
    ):
        return False
    e1 = fields["E1_INTERPRETATION_VALIDITY"]["value"]
    e2 = fields["E2_GROUNDING"]["value"]
    e4 = fields["E4_FUTURE_OBLIGATION_RELATION"]["value"]
    e6 = fields["E6_CANDIDATE_CONSEQUENCE_DIVERGENCE"]["value"]
    e9 = fields["E9_ANSWER_CHANGES_ACTION"]["value"]
    return bool(
        e1["multiple_reasonable_interpretations"]
        and e1["planning_relevant"]
        and isinstance(e1.get("planning_effective_k"), int)
        and e1["planning_effective_k"] >= 2
        and e2["grounding_complete"]
        and e4["relation"] == "DIVERGENT"
        and e4["authorization_eligible"] is True
        and e6["material_divergence"]
        and e9["answer_changes_next_meaningful_decision"] is True
    )


def clarification_actionable(
    row: Mapping[str, Any], *, clarification_deadline_simulation_s: Optional[float]
) -> bool:
    """Return deadline/lifecycle/holding actionability, never evidence sufficiency."""

    if not _finite(clarification_deadline_simulation_s):
        return False
    now = row.get("simulation_time_s")
    if not _finite(now) or float(now) > float(clarification_deadline_simulation_s):
        return False
    fields = row["evidence_vector"]
    e1 = fields["E1_INTERPRETATION_VALIDITY"]
    e7 = fields["E7_SAFETY_RULE_HOLDING"]
    if (
        e1["status"] != EvidenceStatus.AVAILABLE.value
        or e7["status"] != EvidenceStatus.AVAILABLE.value
    ):
        return False
    semantic = e1["value"]
    behavior = e7["value"]
    query_lifecycle_available = bool(
        semantic.get("semantic_state") == "UNRESOLVED"
        and isinstance(semantic.get("active_candidate_count"), int)
        and semantic["active_candidate_count"] >= 2
    )
    return bool(
        query_lifecycle_available
        and behavior.get("information_action_safe") is True
        and behavior.get("safe_holding_or_shared_action_admissible") is True
    )


# Compatibility name for pre-revision analysis callers.  Its meaning is now
# explicitly epistemic-only and it never reads a deadline/actionability field.
evidence_ready_without_deadline = epistemic_evidence_sufficient


def full_evidence_available(row: Mapping[str, Any]) -> bool:
    fields = row["evidence_vector"]
    all_fields_available = all(
        value["status"] == EvidenceStatus.AVAILABLE.value
        for value in fields.values()
    )
    if not all_fields_available or row.get("production_full_plan_coverage") is not True:
        return False
    recoverability = fields["E8_RECOVERABILITY"]["value"]
    current = fields["E3_CURRENT_ACTION_RELATION"]["value"]
    return bool(
        recoverability.get("status") in {"RECOVERABLE", "NOT_RECOVERABLE"}
        and current.get("relation") in {"SHARED", "DIVERGENT"}
    )


def finalize_episode(
    observations: Sequence[Mapping[str, Any]],
    *,
    deadline_contract: DeadlineContract,
    commitment_time_simulation_s: Optional[float],
    commitment_reached: bool,
    right_censored: bool = False,
    invalid_engineering_evidence: bool = False,
    execution_terminal_state: Optional[str] = None,
    simulation_elapsed_s: Optional[float] = None,
    wall_timeout_observed: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not observations:
        raise ValueError("RQ2_T_EPISODE_HAS_NO_OBSERVATIONS")
    ordered = sorted((dict(row) for row in observations), key=lambda row: float(row["simulation_time_s"]))
    if any(
        not _finite(row.get("simulation_time_s"))
        or (index and float(row["simulation_time_s"]) <= float(ordered[index - 1]["simulation_time_s"]))
        for index, row in enumerate(ordered)
    ):
        raise ValueError("RQ2_T_SIMULATION_TIME_NOT_STRICTLY_INCREASING")
    if commitment_reached and not _finite(commitment_time_simulation_s):
        raise ValueError("RQ2_T_COMMITMENT_TIME_REQUIRED")
    if not commitment_reached and commitment_time_simulation_s is not None:
        raise ValueError("RQ2_T_UNREACHED_COMMITMENT_MUST_NOT_HAVE_TIME")

    deadline = (
        None
        if commitment_time_simulation_s is None
        else float(commitment_time_simulation_s)
        - deadline_contract.total_reserved_simulation_s
    )
    first_epistemic = None
    first_opportunity = None
    for row in ordered:
        now = float(row["simulation_time_s"])
        epistemic = epistemic_evidence_sufficient(row)
        full = full_evidence_available(row)
        actionable = clarification_actionable(
            row, clarification_deadline_simulation_s=deadline
        )
        opportunity = bool(epistemic and actionable)
        if epistemic and first_epistemic is None:
            first_epistemic = now
        if opportunity and first_opportunity is None:
            first_opportunity = now
        row["TTCmt_s"] = (
            None
            if commitment_time_simulation_s is None
            else float(commitment_time_simulation_s) - now
        )
        row["clarification_deadline_simulation_time_s"] = deadline
        row["remaining_decision_margin_s"] = None if deadline is None else deadline - now
        row.pop("evidence_ready_without_deadline", None)
        row.pop("DecisionSufficientEvidence", None)
        row.pop("clarification_actionable", None)
        row["EpistemicEvidenceSufficient"] = epistemic
        row["FullEvidenceAvailable"] = full
        row["ClarificationActionable"] = actionable
        row["ClarificationOpportunity"] = opportunity
        row["commitment_state"] = (
            "COMMITTED"
            if commitment_time_simulation_s is not None and now >= float(commitment_time_simulation_s)
            else "PRECOMMITMENT"
        )
        row["temporal_join_status"] = (
            "AVAILABLE_POST_EPISODE_JOIN"
            if deadline is not None
            else "UNKNOWN_COMMITMENT_NOT_OBSERVED"
        )
        row.pop("observation_digest", None)
        row["observation_digest"] = canonical_sha256(row)

    terminal_classification = None
    uses_2a_terminal_contract = bool(
        execution_terminal_state is not None
        or simulation_elapsed_s is not None
        or wall_timeout_observed
    )
    if uses_2a_terminal_contract:
        # Local import avoids making the observational package's legacy path depend
        # on the prospective 2A runtime module.
        from .experiment_2a import (  # noqa: PLC0415
            H2PrimaryCategory,
            classify_primary_terminal,
            classify_ttcmt_stratum,
        )

        terminal_elapsed = (
            float(simulation_elapsed_s)
            if _finite(simulation_elapsed_s)
            else float(ordered[-1]["simulation_time_s"])
        )
        natural_state = (
            execution_terminal_state
            if execution_terminal_state
            not in {
                "COMMITMENT_OBSERVED",
                "MAX_2A_SIMULATED_HORIZON_REACHED",
            }
            else None
        )
        terminal_classification = classify_primary_terminal(
            engineering_integrity_valid=not invalid_engineering_evidence,
            commitment_time_simulation_s=commitment_time_simulation_s,
            first_epistemic_sufficient_time_simulation_s=first_epistemic,
            clarification_deadline_simulation_s=deadline,
            simulation_elapsed_s=terminal_elapsed,
            natural_terminal_state=natural_state,
            wall_timeout_observed=wall_timeout_observed,
        )
        status = OpportunityWindowStatus(
            terminal_classification.h2_primary_category
        )
        duration = terminal_classification.opportunity_duration_simulation_s
        ttcmt_stratum = (
            None
            if terminal_classification.ttcmt_simulation_s is None
            else classify_ttcmt_stratum(
                terminal_classification.ttcmt_simulation_s
            ).value
        )
        # Defensive assertion: the two enums are intentionally bound one-to-one.
        assert status.value in {item.value for item in H2PrimaryCategory}
    else:
        if invalid_engineering_evidence:
            status = OpportunityWindowStatus.INVALID_ENGINEERING_EVIDENCE
        elif right_censored:
            status = OpportunityWindowStatus.RIGHT_CENSORED
        elif not commitment_reached:
            status = OpportunityWindowStatus.COMMITMENT_NOT_REACHED
        elif first_epistemic is not None and deadline is not None and first_epistemic <= deadline:
            status = OpportunityWindowStatus.WINDOW_OBSERVED
        elif first_epistemic is not None:
            status = OpportunityWindowStatus.NO_WINDOW_EVIDENCE_TOO_LATE
        else:
            status = OpportunityWindowStatus.NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT
        duration = (
            None
            if first_epistemic is None or deadline is None or first_epistemic > deadline
            else deadline - first_epistemic
        )
        ttcmt_stratum = None
    summary = {
        "schema_version": "driveclarify.rq2_t.episode_window.v2",
        "episode_id": ordered[0]["episode_id"],
        "scene_id": ordered[0]["scene_id"],
        "seed": ordered[0]["seed"],
        "ambiguity_type": ordered[0]["ambiguity_type"],
        "scientific_clock_domain": "CARLA_SIMULATION_TIME",
        "observation_count": len(ordered),
        "first_epistemic_sufficient_time_simulation_s": first_epistemic,
        "first_clarification_opportunity_time_simulation_s": first_opportunity,
        "window_start_simulation_s": (
            first_epistemic
            if status is OpportunityWindowStatus.WINDOW_OBSERVED
            else None
        ),
        "window_end_simulation_s": (
            deadline if status is OpportunityWindowStatus.WINDOW_OBSERVED else None
        ),
        "clarification_deadline_simulation_time_s": deadline,
        "commitment_time_simulation_s": commitment_time_simulation_s,
        "deadline_contract": deadline_contract.to_dict(),
        "opportunity_window_status": status.value,
        "opportunity_window_duration_s": duration,
        "execution_terminal_state": execution_terminal_state,
        "execution_terminal_class": (
            None
            if terminal_classification is None
            else terminal_classification.execution_terminal_class
        ),
        "commitment_observed": (
            commitment_reached
            if terminal_classification is None
            else terminal_classification.commitment_observed
        ),
        "h1_censoring_status": (
            None
            if terminal_classification is None
            else terminal_classification.h1_censoring_status
        ),
        "h2_primary_category": status.value,
        "denominator_eligible": (
            None
            if terminal_classification is None
            else terminal_classification.denominator_eligible
        ),
        "TTCmt_simulation_s": (
            None
            if terminal_classification is None
            else terminal_classification.ttcmt_simulation_s
        ),
        "TTCmt_stratum": ttcmt_stratum,
        "wall_time_has_scientific_authority": False,
        "full_evidence_ever_available": any(row["FullEvidenceAvailable"] for row in ordered),
        "epistemic_evidence_ever_sufficient": any(
            row["EpistemicEvidenceSufficient"] for row in ordered
        ),
        "clarification_opportunity_ever_observed": any(
            row["ClarificationOpportunity"] for row in ordered
        ),
        "unknown_is_zero_imputed": False,
    }
    summary["summary_digest"] = canonical_sha256(summary)
    return ordered, summary


__all__ = [
    "DeadlineContract",
    "adapt_production_history_row",
    "canonical_sha256",
    "clarification_actionable",
    "epistemic_evidence_sufficient",
    "evidence_ready_without_deadline",
    "finalize_episode",
    "full_evidence_available",
]
