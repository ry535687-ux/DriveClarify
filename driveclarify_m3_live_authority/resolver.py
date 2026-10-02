"""Fail-closed candidate receipt and final execution owner resolvers.

This module selects authority only.  It has no planner, controller, model, PID,
CARLA, steering, throttle, brake, or low-level-control dependency.
"""

from __future__ import annotations

import math
import os
import threading
from typing import Any, Mapping, Optional

from driveclarify_m3_minimal_core import (
    AuditReason,
    ControlAuthority,
    LifecycleState,
    MinimalM3State,
    ProcessingResult,
    ReductionResult,
    canonical_sha256,
    lease_temporally_valid,
)

from .contracts import (
    AUTHORITY_VERSION,
    FEATURE_FLAG_NAME,
    MAX_CONTROL_TICKS_V0,
    MAX_RECEIPT_LIFETIME_S_V0,
    AuthorityReason,
    CandidateActAuthorityDecision,
    CandidateActAuthorityReceipt,
    CandidateActAuthorityRequest,
    CandidateExecutionContext,
    FinalExecutionAuthority,
    FinalExecutionAuthorityDecision,
)


_HEX = frozenset("0123456789abcdef")


def _finite_real(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _nonempty_string(value: Any) -> bool:
    return type(value) is str and bool(value)


def _sha256_string(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _known_context_booleans(context: CandidateExecutionContext) -> bool:
    return all(
        type(getattr(context, name)) is bool
        for name in (
            "candidate_invalidated",
            "active_query",
            "active_holding_lease",
            "independent_safety_guard_active",
            "baseline_available",
        )
    )


def _m3_contract_digest(result: ReductionResult) -> str:
    return canonical_sha256(result.to_dict())


def _m3_act_gate(
    result: Any,
    *,
    candidate_set_id: str,
    expected_digest: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Validate an actual frozen-core MC-T002 result, never an ACT boolean."""

    if not isinstance(result, ReductionResult):
        return False, None
    if not isinstance(result.state, MinimalM3State):
        return False, None
    state = result.state
    digest = _m3_contract_digest(result)
    audit = result.audit[-1] if result.audit else None
    accepted = (
        result.transition_ids == ("MC-T002",)
        and result.processing_results == (ProcessingResult.PROCESSED,)
        and audit is not None
        and audit.transition_id == "MC-T002"
        and audit.result is AuditReason.ACT_CONTRACT_ACCEPTED
        and audit.mutation_applied is True
        and state.lifecycle_state is LifecycleState.RESUME_READY
        and state.authority is ControlAuthority.BASELINE_CONTROL
        and state.candidate_set_id == candidate_set_id
        and state.candidate_freshness == "FRESH"
        and state.query_active is False
        and state.safety_guard_active is False
        and state.holding_lease is None
        and state.revalidation_required is False
        and state.replan_required is False
        and state.low_level_control_outputs == ()
        and result.low_level_control_outputs == ()
    )
    if expected_digest is not None:
        accepted = accepted and digest == expected_digest
    return accepted, digest


def _request_identity_valid(request: CandidateActAuthorityRequest) -> bool:
    return (
        all(
            _nonempty_string(getattr(request, name))
            for name in (
                "candidate_id",
                "candidate_set_id",
                "resolved_interpretation_id",
                "source_observation_id",
                "source_frame_id",
                "control_window_id",
            )
        )
        and _sha256_string(request.route_digest)
        and _sha256_string(request.speed_digest)
    )


def _context_identity_valid(context: CandidateExecutionContext) -> bool:
    return (
        all(
            _nonempty_string(getattr(context, name))
            for name in (
                "current_candidate_id",
                "current_candidate_set_id",
                "current_resolved_interpretation_id",
                "current_source_observation_id",
                "current_source_frame_id",
            )
        )
        and _sha256_string(context.current_route_digest)
        and _sha256_string(context.current_speed_digest)
    )


def _request_matches_context(
    request: CandidateActAuthorityRequest,
    context: CandidateExecutionContext,
) -> bool:
    return (
        request.candidate_id == context.current_candidate_id
        and request.candidate_set_id == context.current_candidate_set_id
        and request.resolved_interpretation_id
        == context.current_resolved_interpretation_id
        and request.source_observation_id == context.current_source_observation_id
        and request.source_frame_id == context.current_source_frame_id
        and request.route_digest == context.current_route_digest
        and request.speed_digest == context.current_speed_digest
    )


def _receipt_matches_context(
    receipt: CandidateActAuthorityReceipt,
    context: CandidateExecutionContext,
) -> bool:
    return (
        receipt.candidate_id == context.current_candidate_id
        and receipt.candidate_set_id == context.current_candidate_set_id
        and receipt.resolved_interpretation_id
        == context.current_resolved_interpretation_id
        and receipt.source_observation_id == context.current_source_observation_id
        and receipt.source_frame_id == context.current_source_frame_id
        and receipt.route_digest == context.current_route_digest
        and receipt.speed_digest == context.current_speed_digest
    )


def _receipt_unsigned_payload(receipt: CandidateActAuthorityReceipt) -> dict[str, Any]:
    value = receipt.to_dict()
    value.pop("receipt_id")
    value.pop("receipt_digest")
    return value


def _receipt_identity_payload(receipt: CandidateActAuthorityReceipt) -> dict[str, Any]:
    return {
        "authority_version": receipt.authority_version,
        "candidate_id": receipt.candidate_id,
        "candidate_set_id": receipt.candidate_set_id,
        "resolved_interpretation_id": receipt.resolved_interpretation_id,
        "source_observation_id": receipt.source_observation_id,
        "source_frame_id": receipt.source_frame_id,
        "route_digest": receipt.route_digest,
        "speed_digest": receipt.speed_digest,
        "control_window_id": receipt.control_window_id,
        "max_control_ticks": receipt.max_control_ticks,
        "requested_lifetime_s": receipt.requested_lifetime_s,
        "simulation_only": receipt.simulation_only,
        "m3_transition_id": receipt.m3_transition_id,
        "m3_lifecycle_state": receipt.m3_lifecycle_state,
        "m3_act_contract_digest": receipt.m3_act_contract_digest,
    }


class CandidateLiveActAuthorityResolverV0:
    """Issue and single-use consume candidate-specific simulation receipts."""

    def __init__(self, *, enabled: bool = False) -> None:
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        self.enabled = enabled
        self._consumed_receipts: set[str] = set()
        self._revoked_receipts: dict[str, AuthorityReason] = {}
        self._receipt_use_count: dict[str, int] = {}
        self._issued_receipts: dict[str, str] = {}
        self._ledger_lock = threading.RLock()

    @classmethod
    def from_environment(
        cls, environ: Optional[Mapping[str, str]] = None
    ) -> "CandidateLiveActAuthorityResolverV0":
        values = os.environ if environ is None else environ
        return cls(enabled=values.get(FEATURE_FLAG_NAME) == "1")

    @property
    def consumed_receipt_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._consumed_receipts))

    @property
    def revoked_receipt_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._revoked_receipts))

    def _reject(self, reason: AuthorityReason) -> CandidateActAuthorityDecision:
        return CandidateActAuthorityDecision(False, reason, None)

    def _context_gate(
        self,
        context: Any,
        *,
        receipt: Optional[CandidateActAuthorityReceipt] = None,
    ) -> Optional[AuthorityReason]:
        if not isinstance(context, CandidateExecutionContext):
            return AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
        if not _known_context_booleans(context) or not _finite_real(
            context.current_monotonic_time
        ):
            return AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
        if context.simulation_runtime != "CARLA":
            return AuthorityReason.SIMULATION_SCOPE_REQUIRED
        if not _context_identity_valid(context):
            return AuthorityReason.CANDIDATE_IDENTITY_MISMATCH
        if context.candidate_freshness != "FRESH":
            return AuthorityReason.CANDIDATE_STALE
        if context.candidate_invalidated is not False:
            return AuthorityReason.CANDIDATE_INVALIDATED
        if context.active_query is not False:
            return AuthorityReason.ACTIVE_QUERY_BLOCKS_ACT
        if context.active_holding_lease is not False:
            return AuthorityReason.ACTIVE_HOLDING_LEASE_BLOCKS_ACT
        if context.independent_safety_guard_active is not False:
            return AuthorityReason.HIGHER_PRIORITY_AUTHORITY_ACTIVE
        if receipt is not None and not _receipt_matches_context(receipt, context):
            return AuthorityReason.CANDIDATE_IDENTITY_MISMATCH
        return None

    def issue(
        self,
        request: Any,
        existing_m3_result: Any,
        context: Any,
    ) -> CandidateActAuthorityDecision:
        if not self.enabled:
            return self._reject(AuthorityReason.CANDIDATE_ACT_FEATURE_DISABLED)
        if not isinstance(request, CandidateActAuthorityRequest):
            return self._reject(AuthorityReason.CANDIDATE_IDENTITY_MISMATCH)
        if not _request_identity_valid(request):
            return self._reject(AuthorityReason.CANDIDATE_IDENTITY_MISMATCH)
        context_reason = self._context_gate(context)
        if context_reason is not None:
            return self._reject(context_reason)
        if not _request_matches_context(request, context):
            return self._reject(AuthorityReason.CANDIDATE_IDENTITY_MISMATCH)
        if (
            type(request.max_control_ticks) is not int
            or request.max_control_ticks != MAX_CONTROL_TICKS_V0
            or not _finite_real(request.requested_lifetime_s)
            or not 0.0 < float(request.requested_lifetime_s) <= MAX_RECEIPT_LIFETIME_S_V0
        ):
            return self._reject(AuthorityReason.CONTROL_WINDOW_INVALID)
        m3_accepted, m3_digest = _m3_act_gate(
            existing_m3_result, candidate_set_id=request.candidate_set_id
        )
        if not m3_accepted or m3_digest is None:
            return self._reject(AuthorityReason.M3_ACT_CONTRACT_NOT_ACCEPTED)

        issued = float(context.current_monotonic_time)
        expires = issued + float(request.requested_lifetime_s)
        if not math.isfinite(expires) or not issued < expires:
            return self._reject(AuthorityReason.CONTROL_WINDOW_INVALID)
        identity_payload = {
            "authority_version": AUTHORITY_VERSION,
            "candidate_id": request.candidate_id,
            "candidate_set_id": request.candidate_set_id,
            "resolved_interpretation_id": request.resolved_interpretation_id,
            "source_observation_id": request.source_observation_id,
            "source_frame_id": request.source_frame_id,
            "route_digest": request.route_digest,
            "speed_digest": request.speed_digest,
            "control_window_id": request.control_window_id,
            "max_control_ticks": request.max_control_ticks,
            "requested_lifetime_s": float(request.requested_lifetime_s),
            "simulation_only": True,
            "m3_transition_id": "MC-T002",
            "m3_lifecycle_state": LifecycleState.RESUME_READY.value,
            "m3_act_contract_digest": m3_digest,
        }
        identity_digest = canonical_sha256(identity_payload)
        unsigned = {
            "candidate_id": request.candidate_id,
            "candidate_set_id": request.candidate_set_id,
            "resolved_interpretation_id": request.resolved_interpretation_id,
            "source_observation_id": request.source_observation_id,
            "source_frame_id": request.source_frame_id,
            "route_digest": request.route_digest,
            "speed_digest": request.speed_digest,
            "issued_monotonic_time": issued,
            "expires_monotonic_time": expires,
            "requested_lifetime_s": float(request.requested_lifetime_s),
            "control_window_id": request.control_window_id,
            "max_control_ticks": request.max_control_ticks,
            "simulation_only": True,
            "authority_version": AUTHORITY_VERSION,
            "m3_transition_id": "MC-T002",
            "m3_lifecycle_state": LifecycleState.RESUME_READY.value,
            "m3_act_contract_digest": m3_digest,
            "identity_digest": identity_digest,
        }
        receipt_digest = canonical_sha256(unsigned)
        receipt = CandidateActAuthorityReceipt(
            receipt_id="candidate-act-receipt-" + receipt_digest[:24],
            candidate_id=request.candidate_id,
            candidate_set_id=request.candidate_set_id,
            resolved_interpretation_id=request.resolved_interpretation_id,
            source_observation_id=request.source_observation_id,
            source_frame_id=request.source_frame_id,
            route_digest=request.route_digest,
            speed_digest=request.speed_digest,
            issued_monotonic_time=issued,
            expires_monotonic_time=expires,
            requested_lifetime_s=float(request.requested_lifetime_s),
            control_window_id=request.control_window_id,
            max_control_ticks=request.max_control_ticks,
            simulation_only=True,
            authority_version=AUTHORITY_VERSION,
            m3_transition_id="MC-T002",
            m3_lifecycle_state=LifecycleState.RESUME_READY.value,
            m3_act_contract_digest=m3_digest,
            identity_digest=identity_digest,
            receipt_digest=receipt_digest,
        )
        with self._ledger_lock:
            self._issued_receipts[receipt.receipt_id] = receipt.receipt_digest
        return CandidateActAuthorityDecision(
            True, AuthorityReason.CANDIDATE_ACT_AUTHORITY_GRANTED, receipt
        )

    def revoke(
        self,
        receipt: Optional[CandidateActAuthorityReceipt],
        reason: AuthorityReason,
    ) -> None:
        if isinstance(receipt, CandidateActAuthorityReceipt):
            with self._ledger_lock:
                self._revoked_receipts.setdefault(receipt.receipt_id, reason)

    def consume(
        self,
        receipt: Any,
        existing_m3_result: Any,
        context: Any,
    ) -> CandidateActAuthorityDecision:
        if not isinstance(receipt, CandidateActAuthorityReceipt):
            return self._reject(AuthorityReason.RECEIPT_INTEGRITY_FAILURE)
        with self._ledger_lock:
            if receipt.receipt_id in self._consumed_receipts:
                return self._reject(AuthorityReason.RECEIPT_ALREADY_CONSUMED)
            if receipt.receipt_id in self._revoked_receipts:
                return self._reject(AuthorityReason.RECEIPT_REVOKED)
            if (
                receipt.simulation_only is not True
                or receipt.authority_version != AUTHORITY_VERSION
                or receipt.max_control_ticks != MAX_CONTROL_TICKS_V0
                or not _finite_real(receipt.requested_lifetime_s)
                or receipt.requested_lifetime_s <= 0.0
                or receipt.requested_lifetime_s > MAX_RECEIPT_LIFETIME_S_V0
                or not math.isclose(
                    receipt.expires_monotonic_time - receipt.issued_monotonic_time,
                    receipt.requested_lifetime_s,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                or canonical_sha256(_receipt_identity_payload(receipt))
                != receipt.identity_digest
                or canonical_sha256(_receipt_unsigned_payload(receipt))
                != receipt.receipt_digest
                or receipt.receipt_id
                != "candidate-act-receipt-" + receipt.receipt_digest[:24]
                or self._issued_receipts.get(receipt.receipt_id)
                != receipt.receipt_digest
            ):
                self.revoke(receipt, AuthorityReason.RECEIPT_INTEGRITY_FAILURE)
                return self._reject(AuthorityReason.RECEIPT_INTEGRITY_FAILURE)
            if not self.enabled:
                self.revoke(receipt, AuthorityReason.CANDIDATE_ACT_FEATURE_DISABLED)
                return self._reject(AuthorityReason.CANDIDATE_ACT_FEATURE_DISABLED)
            if not isinstance(context, CandidateExecutionContext) or not _finite_real(
                context.current_monotonic_time
            ):
                self.revoke(receipt, AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED)
                return self._reject(AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED)
            if float(context.current_monotonic_time) >= receipt.expires_monotonic_time:
                self.revoke(receipt, AuthorityReason.RECEIPT_EXPIRED)
                return self._reject(AuthorityReason.RECEIPT_EXPIRED)
            if self._receipt_use_count.get(receipt.receipt_id, 0) >= receipt.max_control_ticks:
                self.revoke(receipt, AuthorityReason.CONTROL_WINDOW_EXHAUSTED)
                return self._reject(AuthorityReason.CONTROL_WINDOW_EXHAUSTED)
            context_reason = self._context_gate(context, receipt=receipt)
            if context_reason is not None:
                self.revoke(receipt, context_reason)
                return self._reject(context_reason)
            m3_accepted, _ = _m3_act_gate(
                existing_m3_result,
                candidate_set_id=receipt.candidate_set_id,
                expected_digest=receipt.m3_act_contract_digest,
            )
            if not m3_accepted:
                self.revoke(receipt, AuthorityReason.M3_ACT_CONTRACT_NOT_ACCEPTED)
                return self._reject(AuthorityReason.M3_ACT_CONTRACT_NOT_ACCEPTED)

            self._receipt_use_count[receipt.receipt_id] = (
                self._receipt_use_count.get(receipt.receipt_id, 0) + 1
            )
            self._consumed_receipts.add(receipt.receipt_id)
            return CandidateActAuthorityDecision(
                True, AuthorityReason.CANDIDATE_ACT_AUTHORITY_GRANTED, receipt
            )


class FinalExecutionAuthorityResolverV0:
    """The repository's sole final owner decision point for future submission.

    Precedence mirrors frozen M3 semantics and inserts the candidate receipt
    only below safety and valid holding authority:
    safety > M3 holding > candidate receipt > baseline > no authority.
    """

    def __init__(self, candidate_resolver: CandidateLiveActAuthorityResolverV0):
        if not isinstance(candidate_resolver, CandidateLiveActAuthorityResolverV0):
            raise TypeError("candidate_resolver has invalid type")
        self.candidate_resolver = candidate_resolver

    def _decision(
        self,
        *,
        owner: FinalExecutionAuthority,
        authorized: bool,
        reason: AuthorityReason,
        receipt: Optional[CandidateActAuthorityReceipt],
        bind_candidate_source: bool,
        m3_result: Any,
        context: Any,
    ) -> FinalExecutionAuthorityDecision:
        source = receipt if bind_candidate_source else None
        payload = {
            "owner": owner.value,
            "authorized": authorized,
            "reason_code": reason.value,
            "candidate_receipt_id": None if receipt is None else receipt.receipt_id,
            "control_window_id": None if source is None else source.control_window_id,
            "candidate_id": None if source is None else source.candidate_id,
            "candidate_set_id": None if source is None else source.candidate_set_id,
            "resolved_interpretation_id": (
                None if source is None else source.resolved_interpretation_id
            ),
            "source_observation_id": (
                None if source is None else source.source_observation_id
            ),
            "source_frame_id": None if source is None else source.source_frame_id,
            "route_digest": None if source is None else source.route_digest,
            "speed_digest": None if source is None else source.speed_digest,
            "m3_result_digest": (
                _m3_contract_digest(m3_result)
                if isinstance(m3_result, ReductionResult)
                else None
            ),
            "execution_context": (
                context.__dict__
                if isinstance(context, CandidateExecutionContext)
                else None
            ),
        }
        return FinalExecutionAuthorityDecision(
            owner=owner,
            authorized=authorized,
            reason_code=reason,
            candidate_receipt_id=payload["candidate_receipt_id"],
            control_window_id=payload["control_window_id"],
            candidate_id=payload["candidate_id"],
            candidate_set_id=payload["candidate_set_id"],
            resolved_interpretation_id=payload["resolved_interpretation_id"],
            source_observation_id=payload["source_observation_id"],
            source_frame_id=payload["source_frame_id"],
            route_digest=payload["route_digest"],
            speed_digest=payload["speed_digest"],
            decision_digest=canonical_sha256(payload),
        )

    def resolve(
        self,
        *,
        existing_m3_result: Any,
        candidate_authority_receipt: Optional[CandidateActAuthorityReceipt],
        current_execution_context: Any,
    ) -> FinalExecutionAuthorityDecision:
        state = (
            existing_m3_result.state
            if isinstance(existing_m3_result, ReductionResult)
            and isinstance(existing_m3_result.state, MinimalM3State)
            else None
        )
        context = current_execution_context
        if state is None or not isinstance(context, CandidateExecutionContext):
            return self._decision(
                owner=FinalExecutionAuthority.NO_CONTROL_AUTHORITY,
                authorized=False,
                reason=AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED,
                receipt=candidate_authority_receipt,
                bind_candidate_source=False,
                m3_result=existing_m3_result,
                context=context,
            )
        if not _known_context_booleans(context) or not _finite_real(
            context.current_monotonic_time
        ):
            return self._decision(
                owner=FinalExecutionAuthority.NO_CONTROL_AUTHORITY,
                authorized=False,
                reason=AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED,
                receipt=candidate_authority_receipt,
                bind_candidate_source=False,
                m3_result=existing_m3_result,
                context=context,
            )

        safety_active = (
            context.independent_safety_guard_active is True
            or state.safety_guard_active is True
            or state.authority is ControlAuthority.INDEPENDENT_SAFETY_GUARD
        )
        if safety_active:
            self.candidate_resolver.revoke(
                candidate_authority_receipt,
                AuthorityReason.HIGHER_PRIORITY_AUTHORITY_ACTIVE,
            )
            return self._decision(
                owner=FinalExecutionAuthority.INDEPENDENT_SAFETY_GUARD,
                authorized=True,
                reason=AuthorityReason.SAFETY_GUARD_SELECTED,
                receipt=candidate_authority_receipt,
                bind_candidate_source=False,
                m3_result=existing_m3_result,
                context=context,
            )

        valid_holding = (
            state.authority is ControlAuthority.M3_HOLDING_CONTROL
            and state.holding_lease is not None
            and lease_temporally_valid(
                state.holding_lease, context.current_monotonic_time
            )
        )
        if valid_holding:
            self.candidate_resolver.revoke(
                candidate_authority_receipt,
                AuthorityReason.ACTIVE_HOLDING_LEASE_BLOCKS_ACT,
            )
            return self._decision(
                owner=FinalExecutionAuthority.M3_HOLDING_CONTROL,
                authorized=True,
                reason=AuthorityReason.M3_HOLDING_CONTROL_SELECTED,
                receipt=candidate_authority_receipt,
                bind_candidate_source=False,
                m3_result=existing_m3_result,
                context=context,
            )
        if context.active_holding_lease is True:
            self.candidate_resolver.revoke(
                candidate_authority_receipt,
                AuthorityReason.ACTIVE_HOLDING_LEASE_BLOCKS_ACT,
            )
            return self._decision(
                owner=FinalExecutionAuthority.NO_CONTROL_AUTHORITY,
                authorized=False,
                reason=AuthorityReason.ACTIVE_HOLDING_LEASE_BLOCKS_ACT,
                receipt=candidate_authority_receipt,
                bind_candidate_source=False,
                m3_result=existing_m3_result,
                context=context,
            )

        candidate_failure: Optional[AuthorityReason] = None
        if candidate_authority_receipt is not None:
            consumed = self.candidate_resolver.consume(
                candidate_authority_receipt, existing_m3_result, context
            )
            if consumed.authorized:
                return self._decision(
                    owner=FinalExecutionAuthority.DRIVECLARIFY_CANDIDATE_CONTROL,
                    authorized=True,
                    reason=AuthorityReason.CANDIDATE_ACT_AUTHORITY_GRANTED,
                    receipt=candidate_authority_receipt,
                    bind_candidate_source=True,
                    m3_result=existing_m3_result,
                    context=context,
                )
            candidate_failure = consumed.reason_code

        if (
            state.authority is ControlAuthority.BASELINE_CONTROL
            and context.baseline_available is True
        ):
            return self._decision(
                owner=FinalExecutionAuthority.BASELINE_CONTROL,
                authorized=True,
                reason=candidate_failure or AuthorityReason.BASELINE_CONTROL_SELECTED,
                receipt=candidate_authority_receipt,
                bind_candidate_source=False,
                m3_result=existing_m3_result,
                context=context,
            )
        return self._decision(
            owner=FinalExecutionAuthority.NO_CONTROL_AUTHORITY,
            authorized=False,
            reason=candidate_failure or AuthorityReason.NO_CONTROL_AUTHORITY_AVAILABLE,
            receipt=candidate_authority_receipt,
            bind_candidate_source=False,
            m3_result=existing_m3_result,
            context=context,
        )
