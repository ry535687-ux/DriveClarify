"""Immutable evidence types for Decision Evidence Contract V2.0."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


SCHEMA_PREFIX = "driveclarify.decision_evidence"
CONTRACT_VERSION = "2.0"


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class EvidenceAvailabilityV2(StringEnum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    INVALID = "INVALID"


class CurrentExecutableRelationV2(StringEnum):
    EQUIVALENT = "CURRENT_EXECUTABLE_EQUIVALENT"
    DIVERGENT = "CURRENT_EXECUTABLE_DIVERGENT"


class FutureObligationRelationV2(StringEnum):
    EQUIVALENT = "FUTURE_OBLIGATION_EQUIVALENT"
    DIVERGENT = "FUTURE_OBLIGATION_DIVERGENT"


class CandidateRelationshipV2(StringEnum):
    CURRENT_AND_FUTURE_EQUIVALENT = "CURRENT_AND_FUTURE_EQUIVALENT"
    CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT = (
        "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
    )
    CURRENT_ACTION_DIVERGENT = "CURRENT_ACTION_DIVERGENT"
    UNKNOWN_OR_INSUFFICIENT_EVIDENCE = "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"


class ClarificationUrgencyV2(StringEnum):
    DEFER_CLARIFICATION = "DEFER_CLARIFICATION"
    CLARIFY_NOW = "CLARIFY_NOW"
    TOO_LATE = "TOO_LATE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SourceBindingV2:
    source_observation_id: str
    source_frame_id: Any
    route_version: str
    environment_digest: str
    observed_monotonic_time: float
    clock_domain: str = "MONOTONIC"
    visibility: str = "RUNTIME_OBSERVABLE"


@dataclass(frozen=True)
class FutureObligationRowV2:
    candidate_id: str
    interpretation_id: str
    semantic_sha256: str
    obligation_digest: str
    obligation_type: str
    maneuver: str
    event_relation: str
    referent_lineage_id: str
    referent_description: str
    target_id: str
    junction_id: str
    branch_id: str
    route_order_index: int
    route_version: str
    source_observation_id: str
    source_frame_id: Any
    source_kinds: Tuple[str, ...]
    active_unresolved: bool
    semantic_fresh: bool
    topology_binding_available: bool
    visibility: str
    privileged: bool
    authorization_purpose: bool


@dataclass(frozen=True)
class CurrentExecutableCoverageEvidenceV2:
    schema_version: str
    contract_version: str
    availability: EvidenceAvailabilityV2
    relation: Optional[CurrentExecutableRelationV2]
    source: SourceBindingV2
    candidate_ids: Tuple[str, ...]
    candidate_bundle_id: str
    active_member_set_digest: str
    frame: str
    unit: str
    lease_start_progress_m: float
    proposed_lease_end_progress_m: Optional[float]
    local_plan_support_end_by_plan_m: Mapping[str, float]
    maximum_fixed_time_geometry_deviation_m: Optional[float]
    maximum_fixed_time_control_deviation_m: Optional[float]
    lane_action_compatible: Optional[bool]
    geometry_control_compatible: Optional[bool]
    local_coverage_available: bool
    future_target_coverage_required: bool
    authorization_eligible: bool
    fresh: bool
    reason_codes: Tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class FutureObligationEvidenceV2:
    schema_version: str
    contract_version: str
    availability: EvidenceAvailabilityV2
    relation: Optional[FutureObligationRelationV2]
    source: SourceBindingV2
    rows: Tuple[FutureObligationRowV2, ...]
    outside_local_plan_horizon_allowed: bool
    privileged_authorization_reads: int
    authorization_eligible: bool
    fresh: bool
    reason_codes: Tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class CandidateRecoverabilityV2:
    candidate_id: str
    lease_endpoint_progress_m: float
    commitment_progress_m: Optional[float]
    topology_reachable: Optional[bool]
    lane_reachable: Optional[bool]
    braking_lateral_feasible: Optional[bool]
    remaining_commitment_margin_positive: Optional[bool]
    rule_valid: Optional[bool]
    dynamic_safety_valid: Optional[bool]
    route_version_matches: Optional[bool]
    fresh: Optional[bool]
    recoverable: Optional[bool]
    reason_codes: Tuple[str, ...]


@dataclass(frozen=True)
class SharedActionLeaseV2:
    schema_version: str
    contract_version: str
    availability: EvidenceAvailabilityV2
    source: SourceBindingV2
    lease_id: str
    subject_type: str
    start_progress_m: float
    end_progress_m: Optional[float]
    valid_from_monotonic: float
    valid_until_monotonic: Optional[float]
    endpoint_boundaries_m: Mapping[str, Optional[float]]
    binding_boundaries: Tuple[str, ...]
    candidate_recoverability: Tuple[CandidateRecoverabilityV2, ...]
    authorization_eligible: bool
    preserve_unresolved_semantics: bool
    reason_codes: Tuple[str, ...]
    lease_digest: str


@dataclass(frozen=True)
class ClarificationWindowEvidenceV2:
    schema_version: str
    contract_version: str
    availability: EvidenceAvailabilityV2
    urgency: ClarificationUrgencyV2
    source: SourceBindingV2
    commitment_time_lower_bound_monotonic: Optional[float]
    clarification_start_deadline_monotonic: Optional[float]
    answer_deadline_monotonic: Optional[float]
    answer_latency_upper_bound_s: Optional[float]
    post_answer_latency_upper_bound_s: Optional[float]
    shared_lease_valid_until_monotonic: Optional[float]
    clock_conversion_verified: bool
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class DecisionEvidenceBundleV2:
    schema_version: str
    contract_version: str
    source: SourceBindingV2
    current_executable: CurrentExecutableCoverageEvidenceV2
    future_obligation: FutureObligationEvidenceV2
    shared_action_lease: SharedActionLeaseV2
    clarification_window: ClarificationWindowEvidenceV2
    relationship: CandidateRelationshipV2
    relationship_available: bool
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    decision_evidence_digest: str

    def to_dict(self) -> Mapping[str, Any]:
        return asdict(self)


__all__ = [
    "CONTRACT_VERSION",
    "SCHEMA_PREFIX",
    "CandidateRecoverabilityV2",
    "CandidateRelationshipV2",
    "ClarificationUrgencyV2",
    "ClarificationWindowEvidenceV2",
    "CurrentExecutableCoverageEvidenceV2",
    "CurrentExecutableRelationV2",
    "DecisionEvidenceBundleV2",
    "EvidenceAvailabilityV2",
    "FutureObligationEvidenceV2",
    "FutureObligationRelationV2",
    "FutureObligationRowV2",
    "SharedActionLeaseV2",
    "SourceBindingV2",
]

