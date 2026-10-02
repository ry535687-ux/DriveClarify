"""Typed contracts for Method V2.7 information and motion separation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


class ExternalDecisionV27(str, Enum):
    ACT = "ACT"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


class ContinuationStateV27(str, Enum):
    REVERSIBLE = "REVERSIBLE"
    COMMITMENT_IMMINENT = "COMMITMENT_IMMINENT"
    COMMITMENT_CROSSED = "COMMITMENT_CROSSED"
    UNKNOWN = "UNKNOWN"


class CommitmentFamilyV27(str, Enum):
    MANEUVER_DIRECTION = "MANEUVER_DIRECTION"
    EXECUTION_LOCATION = "EXECUTION_LOCATION"


@dataclass(frozen=True)
class SemanticCommitmentBoundaryV27:
    family: CommitmentFamilyV27
    candidate_set_digest: str
    candidate_ids: Tuple[str, ...]
    route_version: str
    environment_digest: str
    boundary_progress_m: float
    topology_event_kind: str
    topology_event_identity: str
    ordered_opportunity_identities: Tuple[str, ...] = ()
    decisive_opportunity_ordinal: Optional[int] = None
    nominal_coincident_supported: bool = False
    evidence_status: str = "AVAILABLE"
    reason_code: str = "SEMANTIC_TOPOLOGICAL_BOUNDARY_AVAILABLE"

    def to_dict(self) -> Mapping[str, Any]:
        value = asdict(self)
        value["family"] = self.family.value
        return value


@dataclass(frozen=True)
class ContinuationAssessmentV27:
    state: ContinuationStateV27
    current_progress_m: Optional[float]
    next_observation_progress_upper_m: Optional[float]
    latest_reversible_progress_m: Optional[float]
    baseline_motion_admissible: Optional[bool]
    lawful_holding_available: Optional[bool]
    reason_codes: Tuple[str, ...]


@dataclass(frozen=True)
class DecisionContextV27:
    semantic_state: str
    effective_k: int
    plausible_interpretations: int
    future_obligation_relation: str
    future_obligation_evidence_valid: bool
    answer_can_resolve_or_reduce: bool
    query_transaction_valid: bool
    candidate_identity_keyset_valid: bool
    source_identity_fresh: bool
    topology_valid: bool
    information_safety_valid: bool
    query_active: bool
    answer_pending: bool
    resolved_answer_exists: bool
    continuation: ContinuationAssessmentV27
    ambiguity_evidence_confirmed_across_observations: bool = True
    candidate_plan_semantics_realized: bool = True
    selected_plan_fresh: bool = False
    selected_motion_safety_valid: bool = False
    selected_motion_rule_valid: bool = False
    current_action_relation: str = "UNKNOWN"
    all_candidate_recoverability: str = "UNKNOWN"
    shared_action_lease_valid: bool = False
    candidate_geometry_similar: Optional[bool] = None
    route_timing_calibrated: bool = False


@dataclass(frozen=True)
class DecisionRecommendationV27:
    decision: ExternalDecisionV27
    lifecycle_state: str
    control_owner: str
    ask_grants_vehicle_motion_authority: bool
    candidate_vehicle_control_authority: bool
    relation: str
    target_type: Optional[str]
    authority_subject_type: Optional[str]
    reason_codes: Tuple[str, ...]


__all__ = [
    "CommitmentFamilyV27",
    "ContinuationAssessmentV27",
    "ContinuationStateV27",
    "DecisionContextV27",
    "DecisionRecommendationV27",
    "ExternalDecisionV27",
    "SemanticCommitmentBoundaryV27",
]
