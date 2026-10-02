"""Persistent, fail-closed pre-PID authority for Stage 6A.

The frozen V1 activation is still invoked by the orchestrator.  Its internal
one-call resolver result is not treated as an executable plan receipt.  This
adapter owns a persistent resolver ledger and consumes a freshly revalidated,
identity-bound receipt exactly once at the pre-PID boundary.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_m3_live_authority import (
    AuthorityReason,
    CandidateActAuthorityReceipt,
    CandidateActAuthorityRequest,
    CandidateExecutionContext,
    CandidateLiveActAuthorityResolverV0,
    FinalExecutionAuthority,
    FinalExecutionAuthorityResolverV0,
)
from driveclarify_m3_offline_replay.serialization import canonical_sha256
from driveclarify_m3_runtime_shadow.contracts import ShadowObservationSnapshot
from driveclarify_m3_runtime_shadow.m2b_binding import ShadowM2BDecisionResult
from driveclarify_m3_runtime_shadow.runtime_authority_evidence_v1 import (
    RuntimeDecisionAuthorityEvidenceV1,
    produce_runtime_decision_authority_evidence_v1,
)
from driveclarify_m3_runtime_shadow.runtime_decision_authority_v1 import (
    RuntimeDecisionAuthorityActivationV1,
)
from driveclarify_m3_runtime_shadow.speed_consequence import (
    evaluate_pid_desired_speed_v0,
)

from .contracts import (
    CandidatePlan,
    ExecutionBoundaryFacts,
    HardRuleEvidence,
    Stage6AContractError,
    canonical_sha256 as stage6a_sha256,
)


@dataclass(frozen=True)
class AuthorityArmResult:
    armed: bool
    reason_code: str
    candidate_id: str | None
    candidate_set_id: str | None
    route_digest: str | None
    speed_digest: str | None
    receipt_id: str | None
    receipt_digest: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "armed": self.armed,
            "reason_code": self.reason_code,
            "candidate_id": self.candidate_id,
            "candidate_set_id": self.candidate_set_id,
            "route_digest": self.route_digest,
            "speed_digest": self.speed_digest,
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
        }


@dataclass(frozen=True)
class PrePidPlanSelection:
    plan_source: str
    selected_plan: CandidatePlan | None
    candidate_set_id: str | None
    receipt_id: str | None
    fail_closed_reason: str | None
    final_authority: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_source": self.plan_source,
            "selected_candidate_id": (
                None if self.selected_plan is None else self.selected_plan.candidate_id
            ),
            "selected_plan_output_sha256": (
                None if self.selected_plan is None else self.selected_plan.output_digest
            ),
            "candidate_set_id": self.candidate_set_id,
            "receipt_id": self.receipt_id,
            "fail_closed_reason": self.fail_closed_reason,
            "final_authority": dict(self.final_authority),
            "pid_invocation_count": 0,
            "control_write_count": 0,
        }


@dataclass(frozen=True)
class _PendingAuthority:
    receipt: CandidateActAuthorityReceipt
    m3_result: Any
    snapshot: ShadowObservationSnapshot
    candidate_ids: tuple[str, str]
    selected_plan: CandidatePlan
    selected_interpretation_id: str


def _physical_authorizes_act(
    evidence: RuntimeDecisionAuthorityEvidenceV1,
) -> bool:
    physical = evidence.physical_safety
    return bool(
        physical.safety_status in {"PASS", "CLEAR"}
        and physical.availability == "AVAILABLE_VERIFIED"
        and physical.evidence_grade == "VERIFIED_FROM_CONTROLLED_PROBE"
        and physical.source_kind == "INDEPENDENT_PHYSICAL_SAFETY_MONITOR"
        and physical.usage_purpose == "PHYSICAL_CONTROL_AUTHORIZATION"
        and physical.safety_critical_eligible is True
    )


def _rule_matches_boundary(
    evidence: HardRuleEvidence,
    snapshot: ShadowObservationSnapshot,
    current_monotonic_time: float,
) -> bool:
    return bool(
        evidence.authorizes_progress
        and evidence.source_observation_id == snapshot.observation_id
        and str(evidence.source_frame_id) == str(snapshot.frame_id)
        and math.isclose(
            float(evidence.observed_monotonic_time),
            float(current_monotonic_time),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    )


def _safety_signal_identity_matches(
    signal: Mapping[str, Any] | None,
    snapshot: ShadowObservationSnapshot,
) -> bool:
    if signal is None:
        return False
    return bool(
        str(signal.get("source_observation_id", "")) == snapshot.observation_id
        and str(signal.get("source_frame_id", "")) == str(snapshot.frame_id)
        and signal.get("source_kind") == "INDEPENDENT_PHYSICAL_SAFETY_MONITOR"
    )


def _plan_digests(plan: CandidatePlan) -> tuple[str, str]:
    route_digest = canonical_sha256(plan.route)
    speed_digest = evaluate_pid_desired_speed_v0(plan.speed).source_digest
    return route_digest, speed_digest


def _selected_runtime_candidate_id(
    producer: ShadowM2BDecisionResult,
) -> str | None:
    if producer.selected_candidate_id is not None:
        return producer.selected_candidate_id
    audit = producer.decision_audit
    if not isinstance(audit, Mapping) or audit.get("act_target_type") != "EQUIVALENCE_CLASS":
        return None
    members = tuple(str(item) for item in audit.get("equivalence_class_candidate_ids", ()))
    if not members or not set(members).issubset(set(producer.candidate_ids)):
        return None
    digest_by_id = {
        candidate_id: (input_digest, output_digest, candidate_id)
        for candidate_id, input_digest, output_digest in zip(
            producer.candidate_ids,
            producer.candidate_input_digests,
            producer.candidate_output_digests,
        )
    }
    return min(members, key=lambda candidate_id: digest_by_id[candidate_id])


def _context(
    candidate_set_id: str,
    resolved_interpretation_id: str,
    plan: CandidatePlan,
    facts: ExecutionBoundaryFacts,
) -> CandidateExecutionContext:
    route_digest, speed_digest = _plan_digests(plan)
    return CandidateExecutionContext(
        current_candidate_id=plan.candidate_id,
        current_candidate_set_id=candidate_set_id,
        current_resolved_interpretation_id=resolved_interpretation_id,
        current_source_observation_id=plan.source_observation_id,
        current_source_frame_id=str(plan.source_frame_id),
        current_route_digest=route_digest,
        current_speed_digest=speed_digest,
        candidate_freshness=facts.candidate_freshness,
        candidate_invalidated=facts.candidate_invalidated,
        active_query=facts.active_query,
        active_holding_lease=facts.active_holding_lease,
        independent_safety_guard_active=facts.independent_safety_guard_active,
        baseline_available=facts.baseline_available,
        simulation_runtime=facts.simulation_runtime,
        current_monotonic_time=float(facts.current_monotonic_time),
    )


def _source_for_owner(owner: str) -> str:
    return {
        FinalExecutionAuthority.DRIVECLARIFY_CANDIDATE_CONTROL.value: (
            "AUTHORIZED_CANDIDATE_PLAN"
        ),
        FinalExecutionAuthority.BASELINE_CONTROL.value: "CURRENT_VALID_BASELINE_PLAN",
        FinalExecutionAuthority.M3_HOLDING_CONTROL.value: "M3_HOLDING_CONTROL",
        FinalExecutionAuthority.INDEPENDENT_SAFETY_GUARD.value: (
            "INDEPENDENT_SAFETY_GUARD"
        ),
        FinalExecutionAuthority.NO_CONTROL_AUTHORITY.value: "NO_CONTROL_AUTHORITY",
    }[owner]


class PersistentPrePidAuthority:
    """Issue at decision time and consume once at the actual plan boundary."""

    def __init__(self, *, enabled: bool = False) -> None:
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        self._candidate_resolver = CandidateLiveActAuthorityResolverV0(
            enabled=enabled
        )
        self._final_resolver = FinalExecutionAuthorityResolverV0(
            self._candidate_resolver
        )
        self._pending: dict[str, _PendingAuthority] = {}
        self._lock = threading.RLock()

    @property
    def pending_candidate_set_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._pending))

    def arm(
        self,
        *,
        producer: ShadowM2BDecisionResult,
        activation: RuntimeDecisionAuthorityActivationV1,
        runtime_evidence: RuntimeDecisionAuthorityEvidenceV1,
        hard_rule_evidence: HardRuleEvidence,
        snapshot: ShadowObservationSnapshot,
        plans: tuple[CandidatePlan, CandidatePlan],
        issuance_facts: ExecutionBoundaryFacts,
    ) -> AuthorityArmResult:
        if producer.producer_action != "ACT":
            return AuthorityArmResult(
                False,
                "M2B_ACTION_IS_NOT_ACT",
                None,
                producer.candidate_set_id,
                None,
                None,
                None,
                None,
            )
        if activation.m2b_action != "ACT" or activation.m3_result.transition_ids != (
            "MC-T002",
        ):
            return AuthorityArmResult(
                False,
                "FROZEN_M3_ACT_CONTRACT_NOT_ACCEPTED",
                producer.selected_candidate_id,
                producer.candidate_set_id,
                None,
                None,
                None,
                None,
            )
        if not _physical_authorizes_act(runtime_evidence):
            return AuthorityArmResult(
                False,
                "PHYSICAL_SAFETY_NOT_VERIFIED",
                producer.selected_candidate_id,
                producer.candidate_set_id,
                None,
                None,
                None,
                None,
            )
        if not _rule_matches_boundary(
            hard_rule_evidence,
            snapshot,
            issuance_facts.current_monotonic_time,
        ):
            return AuthorityArmResult(
                False,
                "HARD_RULE_NOT_VERIFIED",
                producer.selected_candidate_id,
                producer.candidate_set_id,
                None,
                None,
                None,
                None,
            )
        if not math.isclose(
            float(runtime_evidence.decision_monotonic_time),
            float(issuance_facts.current_monotonic_time),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise Stage6AContractError("AUTHORITY_ISSUANCE_TIME_MISMATCH")
        selected_id = _selected_runtime_candidate_id(producer)
        selected_plan = next(
            (plan for plan in plans if plan.candidate_id == selected_id), None
        )
        selected_evidence = next(
            (
                item
                for item in producer.counterfactual_evidence
                if item.candidate_id == selected_id
            ),
            None,
        )
        if selected_plan is None or selected_evidence is None:
            return AuthorityArmResult(
                False,
                "SELECTED_PLAN_IDENTITY_NOT_AVAILABLE",
                selected_id,
                producer.candidate_set_id,
                None,
                None,
                None,
                None,
            )
        route_digest, speed_digest = _plan_digests(selected_plan)
        if (
            route_digest != selected_evidence.candidate_route_digest
            or speed_digest != selected_evidence.candidate_speed_digest
        ):
            return AuthorityArmResult(
                False,
                "SELECTED_PLAN_DIGEST_MISMATCH",
                selected_id,
                producer.candidate_set_id,
                route_digest,
                speed_digest,
                None,
                None,
            )
        context = _context(
            producer.candidate_set_id,
            selected_evidence.interpretation_id,
            selected_plan,
            issuance_facts,
        )
        request = CandidateActAuthorityRequest(
            candidate_id=selected_plan.candidate_id,
            candidate_set_id=producer.candidate_set_id,
            resolved_interpretation_id=selected_evidence.interpretation_id,
            source_observation_id=snapshot.observation_id,
            source_frame_id=str(snapshot.frame_id),
            route_digest=route_digest,
            speed_digest=speed_digest,
            control_window_id="stage6a-window-"
            + stage6a_sha256(
                {
                    "candidate_set_id": producer.candidate_set_id,
                    "candidate_id": selected_plan.candidate_id,
                    "route_digest": route_digest,
                    "speed_digest": speed_digest,
                    "issued_monotonic_time": issuance_facts.current_monotonic_time,
                }
            )[:24],
        )
        issued = self._candidate_resolver.issue(
            request, activation.m3_result, context
        )
        if not issued.authorized or issued.receipt is None:
            return AuthorityArmResult(
                False,
                issued.reason_code.value,
                selected_plan.candidate_id,
                producer.candidate_set_id,
                route_digest,
                speed_digest,
                None,
                None,
            )
        pending = _PendingAuthority(
            receipt=issued.receipt,
            m3_result=activation.m3_result,
            snapshot=snapshot,
            candidate_ids=producer.candidate_ids,
            selected_plan=selected_plan,
            selected_interpretation_id=selected_evidence.interpretation_id,
        )
        with self._lock:
            if producer.candidate_set_id in self._pending:
                self._candidate_resolver.revoke(
                    issued.receipt, AuthorityReason.CONTROL_WINDOW_INVALID
                )
                return AuthorityArmResult(
                    False,
                    "PENDING_AUTHORITY_ALREADY_EXISTS",
                    selected_plan.candidate_id,
                    producer.candidate_set_id,
                    route_digest,
                    speed_digest,
                    None,
                    None,
                )
            self._pending[producer.candidate_set_id] = pending
        return AuthorityArmResult(
            True,
            issued.reason_code.value,
            selected_plan.candidate_id,
            producer.candidate_set_id,
            route_digest,
            speed_digest,
            issued.receipt.receipt_id,
            issued.receipt.receipt_digest,
        )

    def _without_candidate(
        self,
        pending: _PendingAuthority,
        context: CandidateExecutionContext,
        reason: str,
    ) -> PrePidPlanSelection:
        self._candidate_resolver.revoke(
            pending.receipt, AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
        )
        final = self._final_resolver.resolve(
            existing_m3_result=pending.m3_result,
            candidate_authority_receipt=None,
            current_execution_context=context,
        )
        final_dict = final.to_dict()
        return PrePidPlanSelection(
            plan_source=_source_for_owner(final_dict["owner"]),
            selected_plan=None,
            candidate_set_id=pending.receipt.candidate_set_id,
            receipt_id=pending.receipt.receipt_id,
            fail_closed_reason=reason,
            final_authority=final_dict,
        )

    def resolve_pre_pid(
        self,
        *,
        candidate_set_id: str,
        current_plan: CandidatePlan,
        execution_facts: ExecutionBoundaryFacts,
        physical_safety_signal: Mapping[str, Any] | None,
        hard_rule_evidence: HardRuleEvidence | None,
    ) -> PrePidPlanSelection:
        with self._lock:
            pending = self._pending.pop(candidate_set_id, None)
        if pending is None:
            owner = (
                FinalExecutionAuthority.INDEPENDENT_SAFETY_GUARD.value
                if execution_facts.independent_safety_guard_active
                else FinalExecutionAuthority.BASELINE_CONTROL.value
                if execution_facts.baseline_available
                else FinalExecutionAuthority.NO_CONTROL_AUTHORITY.value
            )
            return PrePidPlanSelection(
                plan_source=_source_for_owner(owner),
                selected_plan=None,
                candidate_set_id=candidate_set_id,
                receipt_id=None,
                fail_closed_reason="NO_PENDING_STAGE6A_RECEIPT",
                final_authority={
                    "owner": owner,
                    "authorized": (
                        execution_facts.independent_safety_guard_active
                        or execution_facts.baseline_available
                    ),
                    "reason_code": "NO_PENDING_STAGE6A_RECEIPT",
                },
            )

        context = _context(
            pending.receipt.candidate_set_id,
            pending.selected_interpretation_id,
            current_plan,
            execution_facts,
        )
        if execution_facts.independent_safety_guard_active:
            final = self._final_resolver.resolve(
                existing_m3_result=pending.m3_result,
                candidate_authority_receipt=pending.receipt,
                current_execution_context=context,
            )
            final_dict = final.to_dict()
            return PrePidPlanSelection(
                plan_source=_source_for_owner(final_dict["owner"]),
                selected_plan=None,
                candidate_set_id=candidate_set_id,
                receipt_id=pending.receipt.receipt_id,
                fail_closed_reason="INDEPENDENT_SAFETY_GUARD_ACTIVE",
                final_authority=final_dict,
            )

        if not _safety_signal_identity_matches(
            physical_safety_signal, pending.snapshot
        ):
            return self._without_candidate(
                pending, context, "PHYSICAL_SAFETY_IDENTITY_NOT_CURRENT"
            )
        try:
            current_safety = produce_runtime_decision_authority_evidence_v1(
                pending.snapshot,
                pending.candidate_ids,
                decision_monotonic_time=execution_facts.current_monotonic_time,
                physical_safety_signal=physical_safety_signal,
            )
        except (KeyError, TypeError, ValueError):
            return self._without_candidate(
                pending, context, "PHYSICAL_SAFETY_REVALIDATION_FAILED"
            )
        if not _physical_authorizes_act(current_safety):
            return self._without_candidate(
                pending, context, "PHYSICAL_SAFETY_NOT_VERIFIED_AT_PRE_PID"
            )
        if hard_rule_evidence is None or not _rule_matches_boundary(
            hard_rule_evidence,
            pending.snapshot,
            execution_facts.current_monotonic_time,
        ):
            return self._without_candidate(
                pending, context, "HARD_RULE_NOT_VERIFIED_AT_PRE_PID"
            )
        final = self._final_resolver.resolve(
            existing_m3_result=pending.m3_result,
            candidate_authority_receipt=pending.receipt,
            current_execution_context=context,
        )
        final_dict = final.to_dict()
        candidate_selected = (
            final.owner is FinalExecutionAuthority.DRIVECLARIFY_CANDIDATE_CONTROL
            and final.candidate_id == current_plan.candidate_id
            and final.route_digest == _plan_digests(current_plan)[0]
            and final.speed_digest == _plan_digests(current_plan)[1]
        )
        return PrePidPlanSelection(
            plan_source=_source_for_owner(final_dict["owner"]),
            selected_plan=current_plan if candidate_selected else None,
            candidate_set_id=candidate_set_id,
            receipt_id=pending.receipt.receipt_id,
            fail_closed_reason=(
                None if candidate_selected else final.reason_code.value
            ),
            final_authority=final_dict,
        )


__all__ = [
    "AuthorityArmResult",
    "PersistentPrePidAuthority",
    "PrePidPlanSelection",
]
