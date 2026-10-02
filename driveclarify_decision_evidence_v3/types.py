"""Append-only evidence types for Decision Evidence Architecture V3.1."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


CONTRACT_VERSION = "3.1"
FEATURE_FLAG = "DRIVECLARIFY_DECISION_EVIDENCE_ARCHITECTURE_V3"


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class EvidenceAvailabilityV3(StringEnum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    INVALID = "INVALID"


class CurrentActionRelationV3(StringEnum):
    SHARED = "SHARED"
    DIVERGENT = "DIVERGENT"
    UNKNOWN = "UNKNOWN"


class FutureObligationRelationV3(StringEnum):
    EQUIVALENT = "EQUIVALENT"
    DIVERGENT = "DIVERGENT"
    UNKNOWN = "UNKNOWN"


class ClarificationStateV3(StringEnum):
    NOT_NEEDED_YET = "NOT_NEEDED_YET"
    CLARIFY_NOW = "CLARIFY_NOW"
    TOO_LATE = "TOO_LATE"
    UNKNOWN = "UNKNOWN"


class RefreshGuaranteeStateV3(StringEnum):
    GUARANTEED = "GUARANTEED"
    NOT_GUARANTEED = "NOT_GUARANTEED"
    UNKNOWN = "UNKNOWN"


class RecoverabilityStatusV3(StringEnum):
    RECOVERABLE = "RECOVERABLE"
    NOT_RECOVERABLE = "NOT_RECOVERABLE"
    UNKNOWN = "UNKNOWN"


class CompatibilityRelationshipV3(StringEnum):
    CURRENT_AND_FUTURE_EQUIVALENT = "CURRENT_AND_FUTURE_EQUIVALENT"
    CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT = (
        "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
    )
    CURRENT_ACTION_SHARED_FUTURE_UNKNOWN = "CURRENT_ACTION_SHARED_FUTURE_UNKNOWN"
    CURRENT_ACTION_DIVERGENT = "CURRENT_ACTION_DIVERGENT"
    UNKNOWN_OR_INSUFFICIENT_EVIDENCE = "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class SourceBindingV3:
    source_observation_id: str
    source_frame_id: Any
    route_version: str
    environment_digest: str
    observed_monotonic_time: float
    clock_domain: str = "MONOTONIC"
    visibility: str = "RUNTIME_OBSERVABLE"


@dataclass(frozen=True)
class FutureObligationRowV3:
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
    physical_connector_available: Optional[bool]
    visibility: str
    privileged: bool
    authorization_purpose: bool


@dataclass(frozen=True)
class CurrentActionEvidenceV3:
    availability: EvidenceAvailabilityV3
    relation: CurrentActionRelationV3
    source: SourceBindingV3
    candidate_ids: Tuple[str, ...]
    candidate_bundle_id: str
    active_member_set_digest: str
    lease_start_progress_m: float
    proposed_lease_end_progress_m: Optional[float]
    local_plan_support_end_by_plan_m: Mapping[str, float]
    endpoint_boundaries_m: Mapping[str, Optional[float]]
    maximum_fixed_time_geometry_deviation_m: Optional[float]
    maximum_fixed_time_control_deviation_m: Optional[float]
    lane_action_compatible: Optional[bool]
    branch_compatible_within_lease: Optional[bool]
    geometry_control_compatible: Optional[bool]
    local_coverage_available: bool
    source_identity_checks: Mapping[str, Optional[bool]]
    non_authoritative_transform_diagnostics: Mapping[str, Optional[float]]
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class FutureObligationEvidenceV3:
    availability: EvidenceAvailabilityV3
    relation: FutureObligationRelationV3
    source: SourceBindingV3
    rows: Tuple[FutureObligationRowV3, ...]
    common_continuation_authorized: bool
    privileged_authorization_reads: int
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class PreCommitmentRefreshGuaranteeV3:
    state: RefreshGuaranteeStateV3
    current_progress_m: float
    next_refresh_progress_upper_m: Optional[float]
    earliest_commitment_lower_m: Optional[float]
    maximum_normal_planning_interval_s: Optional[float]
    speed_upper_bound_mps: Optional[float]
    calibrated_uncertainty_m: Optional[float]
    strict_before_commitment: Optional[bool]
    source_runtime_config_bound: bool
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class CandidateRecoverabilityV3:
    candidate_id: str
    lease_endpoint_progress_m: Optional[float]
    commitment_progress_m: Optional[float]
    topology_reachable: Optional[bool]
    lane_reachable: Optional[bool]
    braking_lateral_feasible: Optional[bool]
    remaining_commitment_margin_positive: Optional[bool]
    rule_valid: Optional[bool]
    current_physical_safety_valid: Optional[bool]
    latency_budget_valid: Optional[bool]
    route_identity_verified: Optional[bool]
    semantic_route_binding_sufficient: Optional[bool]
    fresh: Optional[bool]
    status: RecoverabilityStatusV3
    reason_codes: Tuple[str, ...]


@dataclass(frozen=True)
class RecoverabilityEvidenceV3:
    status: RecoverabilityStatusV3
    candidate_rows: Tuple[CandidateRecoverabilityV3, ...]
    active_candidate_keyset_complete: bool
    all_candidates_recoverable: bool
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class SharedActionLeaseV3:
    valid: bool
    source: SourceBindingV3
    lease_id: str
    subject_type: str
    lease_start_progress_m: float
    lease_end_progress_m: Optional[float]
    lease_expiry_monotonic: Optional[float]
    source_planning_event: str
    candidate_bundle_id: str
    active_member_set_digest: str
    endpoint_boundaries_m: Mapping[str, Optional[float]]
    binding_boundaries: Tuple[str, ...]
    lease_reason: str
    preserve_unresolved_semantics: bool
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    lease_digest: str

    @property
    def start_progress_m(self) -> float:
        return self.lease_start_progress_m

    @property
    def end_progress_m(self) -> Optional[float]:
        return self.lease_end_progress_m

    @property
    def valid_until_monotonic(self) -> Optional[float]:
        return self.lease_expiry_monotonic


@dataclass(frozen=True)
class ClarificationEvidenceV3:
    state: ClarificationStateV3
    source: SourceBindingV3
    commitment_time_lower_bound_monotonic: Optional[float]
    clarification_start_deadline_monotonic: Optional[float]
    answer_deadline_monotonic: Optional[float]
    answer_latency_upper_bound_s: Optional[float]
    post_answer_latency_upper_bound_s: Optional[float]
    bounded_unknown_safe_until_refresh: bool
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class DecisionEvidenceBundleV3:
    schema_version: str
    contract_version: str
    source: SourceBindingV3
    current_action: CurrentActionEvidenceV3
    future_obligation: FutureObligationEvidenceV3
    clarification: ClarificationEvidenceV3
    shared_action_lease: SharedActionLeaseV3
    refresh_guarantee: PreCommitmentRefreshGuaranteeV3
    recoverability: RecoverabilityEvidenceV3
    compatibility_relationship: CompatibilityRelationshipV3
    authorization_eligible: bool
    reason_codes: Tuple[str, ...]
    decision_evidence_digest: str

    def to_dict(self) -> Mapping[str, Any]:
        return asdict(self)


__all__ = [name for name in tuple(globals()) if name.endswith("V3") or name in {
    "CONTRACT_VERSION", "FEATURE_FLAG"
}]
