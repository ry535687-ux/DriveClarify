"""Versioned tagged-subject extension of the sole live ACT authority.

This extends subject identity from one unique candidate to either a unique
candidate or a shared equivalence class.  It preserves the existing final
owner enum and priority order and contains no planner, PID, or control writer.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple

from driveclarify_m3_minimal_core import (
    ControlAuthority,
    MinimalM3State,
    ReductionResult,
    canonical_sha256,
    lease_temporally_valid,
)

from .contracts import (
    AuthorityReason,
    FinalExecutionAuthority,
    FinalExecutionAuthorityDecision,
)
from .resolver import _m3_act_gate


AUTHORITY_VERSION_V1 = "DRIVECLARIFY_LIVE_ACT_AUTHORITY_TAGGED_SUBJECT_V1"
MAX_SHARED_CONTROL_TICKS_V1 = 1
_HEX = frozenset("0123456789abcdef")


class ActAuthoritySubjectType(str, Enum):
    UNIQUE_CANDIDATE = "UNIQUE_CANDIDATE"
    SHARED_EQUIVALENCE_CLASS = "SHARED_EQUIVALENCE_CLASS"


@dataclass(frozen=True)
class ActiveAuthorityMember:
    candidate_id: str
    interpretation_id: str
    semantic_sha256: str
    target_obligation_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "interpretation_id": self.interpretation_id,
            "semantic_sha256": self.semantic_sha256,
            "target_obligation_digest": self.target_obligation_digest,
        }


@dataclass(frozen=True)
class ActAuthoritySubjectV1:
    subject_type: ActAuthoritySubjectType
    ambiguity_episode_id: Optional[str]
    candidate_set_id: str
    candidate_id: Optional[str]
    resolved_interpretation_id: Optional[str]
    active_members: Tuple[ActiveAuthorityMember, ...]
    active_member_set_digest: Optional[str]
    shared_action_window_id: Optional[str]
    shared_action_class: Optional[str]
    source_observation_id: str
    source_frame_id: str
    route_digest: str
    speed_digest: str
    issued_monotonic_time: float
    valid_until_monotonic: float
    max_control_ticks: int
    preserve_unresolved_semantics: bool
    current_action_equivalence_evidence_digest: Optional[str]
    decision_window_digest: Optional[str]
    route_version: str
    environment_digest: str
    m3_act_contract_digest: str
    identity_digest: str

    def identity_payload(self) -> dict[str, Any]:
        common = {
            "schema_version": "driveclarify.act_authority_subject.v1",
            "subject_type": self.subject_type.value,
            "candidate_set_id": self.candidate_set_id,
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "route_digest": self.route_digest,
            "speed_digest": self.speed_digest,
            "issued_monotonic_time": self.issued_monotonic_time,
            "valid_until_monotonic": self.valid_until_monotonic,
            "max_control_ticks": self.max_control_ticks,
            "m3_act_contract_digest": self.m3_act_contract_digest,
        }
        if self.subject_type is ActAuthoritySubjectType.UNIQUE_CANDIDATE:
            return {
                **common,
                "candidate_id": self.candidate_id,
                "resolved_interpretation_id": self.resolved_interpretation_id,
            }
        return {
            **common,
            "ambiguity_episode_id": self.ambiguity_episode_id,
            "candidate_id": None,
            "resolved_interpretation_id": None,
            "active_members": [row.to_dict() for row in self.active_members],
            "active_member_set_digest": self.active_member_set_digest,
            "shared_action_window_id": self.shared_action_window_id,
            "shared_action_class": self.shared_action_class,
            "preserve_unresolved_semantics": self.preserve_unresolved_semantics,
            "current_action_equivalence_evidence_digest": self.current_action_equivalence_evidence_digest,
            "decision_window_digest": self.decision_window_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "identity_digest": self.identity_digest}


@dataclass(frozen=True)
class ActAuthorityRequestV1:
    subject: ActAuthoritySubjectV1


@dataclass(frozen=True)
class ActExecutionContextV1:
    subject_identity_digest: str
    current_source_observation_id: str
    current_source_frame_id: str
    current_route_digest: str
    current_speed_digest: str
    current_route_version: str
    current_environment_digest: str
    episode_unresolved: bool
    active_member_set_digest: Optional[str]
    current_action_relation: str
    evidence_fresh: bool
    plan_coverage_verified: bool
    alignment_verified: bool
    latest_safe_slack_positive: bool
    recoverable: bool
    active_query: bool
    active_holding_lease: bool
    independent_safety_guard_active: bool
    baseline_available: bool
    simulation_runtime: str
    current_monotonic_time: float


@dataclass(frozen=True)
class ActAuthorityReceiptV1:
    receipt_id: str
    subject: ActAuthoritySubjectV1
    authority_version: str
    receipt_digest: str

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject.to_dict(),
            "authority_version": self.authority_version,
            "execution_context_binding": {
                "route_version": self.subject.route_version,
                "environment_digest": self.subject.environment_digest,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            **self.unsigned_payload(),
            "receipt_digest": self.receipt_digest,
        }


@dataclass(frozen=True)
class ActAuthorityDecisionV1:
    authorized: bool
    reason_code: AuthorityReason
    receipt: Optional[ActAuthorityReceiptV1] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorized": self.authorized,
            "reason_code": self.reason_code.value,
            "receipt": None if self.receipt is None else self.receipt.to_dict(),
        }


def _nonempty(value: Any) -> bool:
    return type(value) is str and bool(value)


def _digest(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(char in _HEX for char in value)


def build_shared_subject(
    *,
    ambiguity_episode_id: str,
    candidate_set_id: str,
    active_members: Tuple[ActiveAuthorityMember, ...],
    shared_action_window_id: str,
    shared_action_class: str,
    source_observation_id: str,
    source_frame_id: str,
    route_digest: str,
    speed_digest: str,
    issued_monotonic_time: float,
    valid_until_monotonic: float,
    current_action_equivalence_evidence_digest: str,
    decision_window_digest: str,
    route_version: str,
    environment_digest: str,
    m3_act_contract_digest: str,
) -> ActAuthoritySubjectV1:
    members = tuple(sorted(active_members, key=lambda row: (row.candidate_id, row.interpretation_id)))
    member_digest = canonical_sha256([row.to_dict() for row in members])
    provisional = ActAuthoritySubjectV1(
        subject_type=ActAuthoritySubjectType.SHARED_EQUIVALENCE_CLASS,
        ambiguity_episode_id=ambiguity_episode_id,
        candidate_set_id=candidate_set_id,
        candidate_id=None,
        resolved_interpretation_id=None,
        active_members=members,
        active_member_set_digest=member_digest,
        shared_action_window_id=shared_action_window_id,
        shared_action_class=shared_action_class,
        source_observation_id=source_observation_id,
        source_frame_id=source_frame_id,
        route_digest=route_digest,
        speed_digest=speed_digest,
        issued_monotonic_time=float(issued_monotonic_time),
        valid_until_monotonic=float(valid_until_monotonic),
        max_control_ticks=MAX_SHARED_CONTROL_TICKS_V1,
        preserve_unresolved_semantics=True,
        current_action_equivalence_evidence_digest=current_action_equivalence_evidence_digest,
        decision_window_digest=decision_window_digest,
        route_version=route_version,
        environment_digest=environment_digest,
        m3_act_contract_digest=m3_act_contract_digest,
        identity_digest="",
    )
    return ActAuthoritySubjectV1(
        **{
            **provisional.__dict__,
            "identity_digest": canonical_sha256(provisional.identity_payload()),
        }
    )


def build_unique_subject(
    *,
    candidate_set_id: str,
    candidate_id: str,
    resolved_interpretation_id: str,
    source_observation_id: str,
    source_frame_id: str,
    route_digest: str,
    speed_digest: str,
    issued_monotonic_time: float,
    valid_until_monotonic: float,
    route_version: str,
    environment_digest: str,
    m3_act_contract_digest: str,
) -> ActAuthoritySubjectV1:
    provisional = ActAuthoritySubjectV1(
        subject_type=ActAuthoritySubjectType.UNIQUE_CANDIDATE,
        ambiguity_episode_id=None,
        candidate_set_id=candidate_set_id,
        candidate_id=candidate_id,
        resolved_interpretation_id=resolved_interpretation_id,
        active_members=(),
        active_member_set_digest=None,
        shared_action_window_id=None,
        shared_action_class=None,
        source_observation_id=source_observation_id,
        source_frame_id=source_frame_id,
        route_digest=route_digest,
        speed_digest=speed_digest,
        issued_monotonic_time=float(issued_monotonic_time),
        valid_until_monotonic=float(valid_until_monotonic),
        max_control_ticks=1,
        preserve_unresolved_semantics=False,
        current_action_equivalence_evidence_digest=None,
        decision_window_digest=None,
        route_version=route_version,
        environment_digest=environment_digest,
        m3_act_contract_digest=m3_act_contract_digest,
        identity_digest="",
    )
    return ActAuthoritySubjectV1(
        **{
            **provisional.__dict__,
            "identity_digest": canonical_sha256(provisional.identity_payload()),
        }
    )


def validate_subject(subject: Any) -> bool:
    if not isinstance(subject, ActAuthoritySubjectV1):
        return False
    if not all(
        _nonempty(getattr(subject, name))
        for name in (
            "candidate_set_id", "source_observation_id", "source_frame_id",
            "route_version", "environment_digest",
        )
    ):
        return False
    if not all(
        _digest(getattr(subject, name))
        for name in ("route_digest", "speed_digest", "m3_act_contract_digest")
    ):
        return False
    if not (
        math.isfinite(subject.issued_monotonic_time)
        and math.isfinite(subject.valid_until_monotonic)
        and subject.issued_monotonic_time < subject.valid_until_monotonic
        and subject.max_control_ticks == 1
        and subject.identity_digest == canonical_sha256(subject.identity_payload())
    ):
        return False
    if subject.subject_type is ActAuthoritySubjectType.UNIQUE_CANDIDATE:
        return bool(
            _nonempty(subject.candidate_id)
            and _nonempty(subject.resolved_interpretation_id)
            and subject.ambiguity_episode_id is None
            and not subject.active_members
            and subject.active_member_set_digest is None
            and subject.shared_action_window_id is None
            and subject.shared_action_class is None
            and subject.preserve_unresolved_semantics is False
            and subject.current_action_equivalence_evidence_digest is None
            and subject.decision_window_digest is None
        )
    members = [row.to_dict() for row in subject.active_members]
    return bool(
        _nonempty(subject.ambiguity_episode_id)
        and subject.candidate_id is None
        and subject.resolved_interpretation_id is None
        and len(members) >= 2
        and members == sorted(members, key=lambda row: (row["candidate_id"], row["interpretation_id"]))
        and len({row["candidate_id"] for row in members}) == len(members)
        and all(_digest(row["semantic_sha256"]) and _digest(row["target_obligation_digest"]) for row in members)
        and subject.active_member_set_digest == canonical_sha256(members)
        and _nonempty(subject.shared_action_window_id)
        and _nonempty(subject.shared_action_class)
        and subject.preserve_unresolved_semantics is True
        and _digest(subject.current_action_equivalence_evidence_digest)
        and _digest(subject.decision_window_digest)
    )


class LiveActAuthorityResolverV1:
    """The versioned tagged-subject implementation of the existing authority."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self._issued: dict[str, str] = {}
        self._consumed: set[str] = set()
        self._revoked: dict[str, AuthorityReason] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _reject(reason: AuthorityReason) -> ActAuthorityDecisionV1:
        return ActAuthorityDecisionV1(False, reason, None)

    def _context_gate(
        self, subject: ActAuthoritySubjectV1, context: Any
    ) -> Optional[AuthorityReason]:
        if not isinstance(context, ActExecutionContextV1):
            return AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
        if not math.isfinite(float(context.current_monotonic_time)):
            return AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
        if context.simulation_runtime != "CARLA":
            return AuthorityReason.SIMULATION_SCOPE_REQUIRED
        if context.subject_identity_digest != subject.identity_digest:
            return AuthorityReason.CANDIDATE_IDENTITY_MISMATCH
        if (
            context.current_source_observation_id != subject.source_observation_id
            or context.current_source_frame_id != subject.source_frame_id
            or context.current_route_digest != subject.route_digest
            or context.current_speed_digest != subject.speed_digest
            or context.current_route_version != subject.route_version
            or context.current_environment_digest != subject.environment_digest
        ):
            return AuthorityReason.CANDIDATE_IDENTITY_MISMATCH
        if context.active_query:
            return AuthorityReason.ACTIVE_QUERY_BLOCKS_ACT
        if context.active_holding_lease:
            return AuthorityReason.ACTIVE_HOLDING_LEASE_BLOCKS_ACT
        if context.independent_safety_guard_active:
            return AuthorityReason.HIGHER_PRIORITY_AUTHORITY_ACTIVE
        if subject.subject_type is ActAuthoritySubjectType.SHARED_EQUIVALENCE_CLASS:
            positive_shared_relation = context.current_action_relation in {
                "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT",
                "CURRENT_AND_FUTURE_EQUIVALENT",
                # V3 append-only compatibility label.  The upstream V3 M2B
                # has already proved the current shared lease, candidate-wise
                # recoverability and pre-commitment refresh guarantee; future
                # UNKNOWN is retained rather than mislabeled as divergent.
                "CURRENT_ACTION_SHARED_FUTURE_UNKNOWN",
            }
            clarification_timing_valid = bool(
                context.current_action_relation == "CURRENT_AND_FUTURE_EQUIVALENT"
                or context.latest_safe_slack_positive
            )
            shared = (
                context.episode_unresolved
                and context.active_member_set_digest == subject.active_member_set_digest
                and positive_shared_relation
                and context.evidence_fresh
                and context.plan_coverage_verified
                and context.alignment_verified
                and clarification_timing_valid
                and context.recoverable
            )
            if not shared:
                return AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
        return None

    def issue(
        self, request: Any, existing_m3_result: Any, context: Any
    ) -> ActAuthorityDecisionV1:
        if not self.enabled:
            return self._reject(AuthorityReason.CANDIDATE_ACT_FEATURE_DISABLED)
        if not isinstance(request, ActAuthorityRequestV1) or not validate_subject(request.subject):
            return self._reject(AuthorityReason.CANDIDATE_IDENTITY_MISMATCH)
        subject = request.subject
        reason = self._context_gate(subject, context)
        if reason is not None:
            return self._reject(reason)
        accepted, digest = _m3_act_gate(
            existing_m3_result,
            candidate_set_id=subject.candidate_set_id,
            expected_digest=subject.m3_act_contract_digest,
        )
        if not accepted or digest is None:
            return self._reject(AuthorityReason.M3_ACT_CONTRACT_NOT_ACCEPTED)
        if float(context.current_monotonic_time) >= subject.valid_until_monotonic:
            return self._reject(AuthorityReason.RECEIPT_EXPIRED)
        provisional = ActAuthorityReceiptV1(
            receipt_id="",
            subject=subject,
            authority_version=AUTHORITY_VERSION_V1,
            receipt_digest="",
        )
        receipt_digest = canonical_sha256(provisional.unsigned_payload())
        receipt = ActAuthorityReceiptV1(
            receipt_id="live-act-receipt-v1-" + receipt_digest[:24],
            subject=subject,
            authority_version=AUTHORITY_VERSION_V1,
            receipt_digest=receipt_digest,
        )
        with self._lock:
            self._issued[receipt.receipt_id] = receipt.receipt_digest
        return ActAuthorityDecisionV1(
            True, AuthorityReason.CANDIDATE_ACT_AUTHORITY_GRANTED, receipt
        )

    def revoke(
        self, receipt: Optional[ActAuthorityReceiptV1], reason: AuthorityReason
    ) -> None:
        if isinstance(receipt, ActAuthorityReceiptV1):
            with self._lock:
                self._revoked.setdefault(receipt.receipt_id, reason)

    def consume(
        self, receipt: Any, existing_m3_result: Any, context: Any
    ) -> ActAuthorityDecisionV1:
        if not isinstance(receipt, ActAuthorityReceiptV1):
            return self._reject(AuthorityReason.RECEIPT_INTEGRITY_FAILURE)
        with self._lock:
            if receipt.receipt_id in self._consumed:
                return self._reject(AuthorityReason.RECEIPT_ALREADY_CONSUMED)
            if receipt.receipt_id in self._revoked:
                return self._reject(AuthorityReason.RECEIPT_REVOKED)
            if (
                receipt.authority_version != AUTHORITY_VERSION_V1
                or receipt.receipt_digest != canonical_sha256(receipt.unsigned_payload())
                or self._issued.get(receipt.receipt_id) != receipt.receipt_digest
                or not validate_subject(receipt.subject)
            ):
                self.revoke(receipt, AuthorityReason.RECEIPT_INTEGRITY_FAILURE)
                return self._reject(AuthorityReason.RECEIPT_INTEGRITY_FAILURE)
            reason = self._context_gate(receipt.subject, context)
            if reason is not None:
                self.revoke(receipt, reason)
                return self._reject(reason)
            if float(context.current_monotonic_time) >= receipt.subject.valid_until_monotonic:
                self.revoke(receipt, AuthorityReason.RECEIPT_EXPIRED)
                return self._reject(AuthorityReason.RECEIPT_EXPIRED)
            accepted, _ = _m3_act_gate(
                existing_m3_result,
                candidate_set_id=receipt.subject.candidate_set_id,
                expected_digest=receipt.subject.m3_act_contract_digest,
            )
            if not accepted:
                self.revoke(receipt, AuthorityReason.M3_ACT_CONTRACT_NOT_ACCEPTED)
                return self._reject(AuthorityReason.M3_ACT_CONTRACT_NOT_ACCEPTED)
            self._consumed.add(receipt.receipt_id)
        return ActAuthorityDecisionV1(
            True, AuthorityReason.CANDIDATE_ACT_AUTHORITY_GRANTED, receipt
        )


class FinalExecutionAuthorityResolverV1:
    """Same owner enum and safety > holding > live receipt > baseline priority."""

    def __init__(self, live_resolver: LiveActAuthorityResolverV1) -> None:
        self.live_resolver = live_resolver

    @staticmethod
    def _decision(
        owner: FinalExecutionAuthority,
        authorized: bool,
        reason: AuthorityReason,
        receipt: Optional[ActAuthorityReceiptV1],
        bind: bool,
    ) -> FinalExecutionAuthorityDecision:
        subject = receipt.subject if bind and receipt is not None else None
        payload = {
            "owner": owner.value,
            "authorized": authorized,
            "reason_code": reason.value,
            "receipt_id": None if receipt is None else receipt.receipt_id,
            "subject_identity_digest": None if subject is None else subject.identity_digest,
        }
        return FinalExecutionAuthorityDecision(
            owner=owner,
            authorized=authorized,
            reason_code=reason,
            candidate_receipt_id=None if receipt is None else receipt.receipt_id,
            control_window_id=None if subject is None else subject.shared_action_window_id,
            candidate_id=None if subject is None else subject.candidate_id,
            candidate_set_id=None if subject is None else subject.candidate_set_id,
            resolved_interpretation_id=None if subject is None else subject.resolved_interpretation_id,
            source_observation_id=None if subject is None else subject.source_observation_id,
            source_frame_id=None if subject is None else subject.source_frame_id,
            route_digest=None if subject is None else subject.route_digest,
            speed_digest=None if subject is None else subject.speed_digest,
            decision_digest=canonical_sha256(payload),
        )

    def resolve(
        self,
        *,
        existing_m3_result: Any,
        live_authority_receipt: Optional[ActAuthorityReceiptV1],
        current_execution_context: Any,
    ) -> FinalExecutionAuthorityDecision:
        state = (
            existing_m3_result.state
            if isinstance(existing_m3_result, ReductionResult)
            and isinstance(existing_m3_result.state, MinimalM3State)
            else None
        )
        context = current_execution_context
        if state is None or not isinstance(context, ActExecutionContextV1):
            return self._decision(
                FinalExecutionAuthority.NO_CONTROL_AUTHORITY,
                False,
                AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED,
                live_authority_receipt,
                False,
            )
        safety = (
            context.independent_safety_guard_active
            or state.safety_guard_active
            or state.authority is ControlAuthority.INDEPENDENT_SAFETY_GUARD
        )
        if safety:
            self.live_resolver.revoke(
                live_authority_receipt, AuthorityReason.HIGHER_PRIORITY_AUTHORITY_ACTIVE
            )
            return self._decision(
                FinalExecutionAuthority.INDEPENDENT_SAFETY_GUARD,
                True,
                AuthorityReason.SAFETY_GUARD_SELECTED,
                live_authority_receipt,
                False,
            )
        holding = (
            state.authority is ControlAuthority.M3_HOLDING_CONTROL
            and state.holding_lease is not None
            and lease_temporally_valid(state.holding_lease, context.current_monotonic_time)
        )
        if holding or context.active_holding_lease:
            self.live_resolver.revoke(
                live_authority_receipt, AuthorityReason.ACTIVE_HOLDING_LEASE_BLOCKS_ACT
            )
            return self._decision(
                FinalExecutionAuthority.M3_HOLDING_CONTROL if holding else FinalExecutionAuthority.NO_CONTROL_AUTHORITY,
                holding,
                AuthorityReason.M3_HOLDING_CONTROL_SELECTED if holding else AuthorityReason.ACTIVE_HOLDING_LEASE_BLOCKS_ACT,
                live_authority_receipt,
                False,
            )
        if live_authority_receipt is not None:
            consumed = self.live_resolver.consume(
                live_authority_receipt, existing_m3_result, context
            )
            if consumed.authorized:
                return self._decision(
                    FinalExecutionAuthority.DRIVECLARIFY_CANDIDATE_CONTROL,
                    True,
                    AuthorityReason.CANDIDATE_ACT_AUTHORITY_GRANTED,
                    live_authority_receipt,
                    True,
                )
        if context.baseline_available and state.authority is ControlAuthority.BASELINE_CONTROL:
            return self._decision(
                FinalExecutionAuthority.BASELINE_CONTROL,
                True,
                AuthorityReason.BASELINE_CONTROL_SELECTED,
                live_authority_receipt,
                False,
            )
        return self._decision(
            FinalExecutionAuthority.NO_CONTROL_AUTHORITY,
            False,
            AuthorityReason.NO_CONTROL_AUTHORITY_AVAILABLE,
            live_authority_receipt,
            False,
        )


__all__ = [
    "AUTHORITY_VERSION_V1",
    "ActAuthorityDecisionV1",
    "ActAuthorityReceiptV1",
    "ActAuthorityRequestV1",
    "ActAuthoritySubjectType",
    "ActAuthoritySubjectV1",
    "ActExecutionContextV1",
    "ActiveAuthorityMember",
    "FinalExecutionAuthorityResolverV1",
    "LiveActAuthorityResolverV1",
    "build_shared_subject",
    "build_unique_subject",
    "validate_subject",
]
