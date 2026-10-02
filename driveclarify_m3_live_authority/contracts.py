"""Immutable execution-layer contracts downstream of the frozen M3 core.

``FinalExecutionAuthority`` is deliberately a different enum from the frozen
``driveclarify_m3_minimal_core.ControlAuthority``.  M3 continues to own
lifecycle semantics; this package only resolves the source of the next
simulation control window after an M3 ACT contract has been accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


AUTHORITY_VERSION = "DRIVECLARIFY_CANDIDATE_LIVE_ACT_AUTHORITY_V0"
AUTHORITY_SCHEMA = "driveclarify.candidate_live_act_authority.v0"
FEATURE_FLAG_NAME = "DRIVECLARIFY_CANDIDATE_LIVE_ACT_AUTHORITY_V0"
MAX_CONTROL_TICKS_V0 = 1
MAX_RECEIPT_LIFETIME_S_V0 = 0.1


class FinalExecutionAuthority(str, Enum):
    """The single owner domain at the future actuator boundary."""

    BASELINE_CONTROL = "BASELINE_CONTROL"
    DRIVECLARIFY_CANDIDATE_CONTROL = "DRIVECLARIFY_CANDIDATE_CONTROL"
    M3_HOLDING_CONTROL = "M3_HOLDING_CONTROL"
    INDEPENDENT_SAFETY_GUARD = "INDEPENDENT_SAFETY_GUARD"
    NO_CONTROL_AUTHORITY = "NO_CONTROL_AUTHORITY"


class AuthorityReason(str, Enum):
    CANDIDATE_ACT_AUTHORITY_GRANTED = "CANDIDATE_ACT_AUTHORITY_GRANTED"
    CANDIDATE_ACT_FEATURE_DISABLED = "CANDIDATE_ACT_FEATURE_DISABLED"
    SIMULATION_SCOPE_REQUIRED = "SIMULATION_SCOPE_REQUIRED"
    EXECUTION_CONTEXT_UNVERIFIED = "EXECUTION_CONTEXT_UNVERIFIED"
    M3_ACT_CONTRACT_NOT_ACCEPTED = "M3_ACT_CONTRACT_NOT_ACCEPTED"
    CANDIDATE_STALE = "CANDIDATE_STALE"
    CANDIDATE_INVALIDATED = "CANDIDATE_INVALIDATED"
    CANDIDATE_IDENTITY_MISMATCH = "CANDIDATE_IDENTITY_MISMATCH"
    ACTIVE_QUERY_BLOCKS_ACT = "ACTIVE_QUERY_BLOCKS_ACT"
    ACTIVE_HOLDING_LEASE_BLOCKS_ACT = "ACTIVE_HOLDING_LEASE_BLOCKS_ACT"
    HIGHER_PRIORITY_AUTHORITY_ACTIVE = "HIGHER_PRIORITY_AUTHORITY_ACTIVE"
    CONTROL_WINDOW_INVALID = "CONTROL_WINDOW_INVALID"
    RECEIPT_EXPIRED = "RECEIPT_EXPIRED"
    RECEIPT_ALREADY_CONSUMED = "RECEIPT_ALREADY_CONSUMED"
    RECEIPT_REVOKED = "RECEIPT_REVOKED"
    CONTROL_WINDOW_EXHAUSTED = "CONTROL_WINDOW_EXHAUSTED"
    RECEIPT_INTEGRITY_FAILURE = "RECEIPT_INTEGRITY_FAILURE"
    SAFETY_GUARD_SELECTED = "SAFETY_GUARD_SELECTED"
    M3_HOLDING_CONTROL_SELECTED = "M3_HOLDING_CONTROL_SELECTED"
    BASELINE_CONTROL_SELECTED = "BASELINE_CONTROL_SELECTED"
    NO_CONTROL_AUTHORITY_AVAILABLE = "NO_CONTROL_AUTHORITY_AVAILABLE"


@dataclass(frozen=True)
class CandidateActAuthorityRequest:
    """Identity-bound request for exactly one fresh plan and control window."""

    candidate_id: Any
    candidate_set_id: Any
    resolved_interpretation_id: Any
    source_observation_id: Any
    source_frame_id: Any
    route_digest: Any
    speed_digest: Any
    control_window_id: Any
    requested_lifetime_s: Any = MAX_RECEIPT_LIFETIME_S_V0
    max_control_ticks: Any = MAX_CONTROL_TICKS_V0


@dataclass(frozen=True)
class CandidateExecutionContext:
    """Execution-time facts checked both at issuance and at consumption."""

    current_candidate_id: Any
    current_candidate_set_id: Any
    current_resolved_interpretation_id: Any
    current_source_observation_id: Any
    current_source_frame_id: Any
    current_route_digest: Any
    current_speed_digest: Any
    candidate_freshness: Any
    candidate_invalidated: Any
    active_query: Any
    active_holding_lease: Any
    independent_safety_guard_active: Any
    baseline_available: Any
    simulation_runtime: Any
    current_monotonic_time: Any


@dataclass(frozen=True)
class CandidateActAuthorityReceipt:
    receipt_id: str
    candidate_id: str
    candidate_set_id: str
    resolved_interpretation_id: str
    source_observation_id: str
    source_frame_id: str
    route_digest: str
    speed_digest: str
    issued_monotonic_time: float
    expires_monotonic_time: float
    requested_lifetime_s: float
    control_window_id: str
    max_control_ticks: int
    simulation_only: bool
    authority_version: str
    m3_transition_id: str
    m3_lifecycle_state: str
    m3_act_contract_digest: str
    identity_digest: str
    receipt_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "candidate_id": self.candidate_id,
            "candidate_set_id": self.candidate_set_id,
            "resolved_interpretation_id": self.resolved_interpretation_id,
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "route_digest": self.route_digest,
            "speed_digest": self.speed_digest,
            "issued_monotonic_time": self.issued_monotonic_time,
            "expires_monotonic_time": self.expires_monotonic_time,
            "requested_lifetime_s": self.requested_lifetime_s,
            "control_window_id": self.control_window_id,
            "max_control_ticks": self.max_control_ticks,
            "simulation_only": self.simulation_only,
            "authority_version": self.authority_version,
            "m3_transition_id": self.m3_transition_id,
            "m3_lifecycle_state": self.m3_lifecycle_state,
            "m3_act_contract_digest": self.m3_act_contract_digest,
            "identity_digest": self.identity_digest,
            "receipt_digest": self.receipt_digest,
        }


@dataclass(frozen=True)
class CandidateActAuthorityDecision:
    authorized: bool
    reason_code: AuthorityReason
    receipt: Optional[CandidateActAuthorityReceipt] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorized": self.authorized,
            "reason_code": self.reason_code.value,
            "receipt": None if self.receipt is None else self.receipt.to_dict(),
        }


@dataclass(frozen=True)
class FinalExecutionAuthorityDecision:
    owner: FinalExecutionAuthority
    authorized: bool
    reason_code: AuthorityReason
    candidate_receipt_id: Optional[str]
    control_window_id: Optional[str]
    candidate_id: Optional[str]
    candidate_set_id: Optional[str]
    resolved_interpretation_id: Optional[str]
    source_observation_id: Optional[str]
    source_frame_id: Optional[str]
    route_digest: Optional[str]
    speed_digest: Optional[str]
    decision_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner.value,
            "authorized": self.authorized,
            "reason_code": self.reason_code.value,
            "candidate_receipt_id": self.candidate_receipt_id,
            "control_window_id": self.control_window_id,
            "candidate_id": self.candidate_id,
            "candidate_set_id": self.candidate_set_id,
            "resolved_interpretation_id": self.resolved_interpretation_id,
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "route_digest": self.route_digest,
            "speed_digest": self.speed_digest,
            "decision_digest": self.decision_digest,
        }
