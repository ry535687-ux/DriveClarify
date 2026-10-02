"""Thin runtime adapter from natural M2B decisions to frozen M3 authority.

The adapter creates no plan or control quantity.  It invokes the existing M3
reducer and the existing authority resolvers, preserving their precedence and
receipt contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_m3_live_authority import (
    CandidateActAuthorityRequest,
    CandidateExecutionContext,
    CandidateLiveActAuthorityResolverV0,
    FinalExecutionAuthorityResolverV0,
)
from driveclarify_m3_minimal_core import (
    ControlAuthority,
    EventType,
    EvidenceGrade,
    LifecycleState,
    MinimalM3Event,
    MinimalM3State,
    ReductionResult,
    reduce_event,
)

from .m2b_binding import ShadowM2BDecisionResult


RUNTIME_DECISION_AUTHORITY_ADAPTER = (
    "DRIVECLARIFY_RUNTIME_DECISION_AUTHORITY_ADAPTER_V1"
)
RUNTIME_DECISION_AUTHORITY_SCHEMA = (
    "driveclarify.runtime_decision_authority_activation.v1"
)


def _verified_physical_safety(result: ShadowM2BDecisionResult) -> bool:
    bundle = result.runtime_authority_evidence
    if not isinstance(bundle, Mapping):
        return False
    safety = bundle.get("physical_safety")
    return bool(
        isinstance(safety, Mapping)
        and safety.get("safety_status") in {"PASS", "CLEAR"}
        and safety.get("availability") == "AVAILABLE_VERIFIED"
        and safety.get("evidence_grade")
        == EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE.value
        and safety.get("usage_purpose") == "PHYSICAL_CONTROL_AUTHORIZATION"
        and safety.get("safety_critical_eligible") is True
    )


def _verified_holding(result: ShadowM2BDecisionResult) -> bool:
    bundle = result.runtime_authority_evidence
    holding = bundle.get("holding") if isinstance(bundle, Mapping) else None
    lease = result.holding_lease
    return bool(
        isinstance(holding, Mapping)
        and holding.get("evidence_grade")
        == EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE.value
        and holding.get("holding_capability_status")
        == "AVAILABLE_CONTRACT_ONLY"
        and holding.get("closed_loop_behavior")
        == "MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR"
        and holding.get("emergency_stop_semantics") is False
        and holding.get("low_level_controller_owner") == "EXISTING_BASELINE_PID"
        and isinstance(lease, Mapping)
    )


def _event_type(action: str) -> EventType:
    return {
        "ACT": EventType.DECISION_ACT,
        "ASK": EventType.DECISION_ASK,
        "WAIT": EventType.DECISION_WAIT,
        "FALLBACK_RECOMMENDED": EventType.DECISION_FALLBACK,
    }[action]


def _selected_evidence(result: ShadowM2BDecisionResult) -> Any | None:
    return next(
        (
            item
            for item in result.counterfactual_evidence
            if item.candidate_id == result.selected_candidate_id
        ),
        None,
    )


@dataclass(frozen=True)
class RuntimeDecisionAuthorityActivationV1:
    m2b_action: str
    m3_result: ReductionResult
    authority_issuance: Mapping[str, Any] | None
    final_authority: Mapping[str, Any]
    pid_ownership: Mapping[str, Any]
    manual_decision_override_used: bool
    forced_action_used: bool
    extra_planner_count: int
    hidden_controller_count: int
    adapter: str = RUNTIME_DECISION_AUTHORITY_ADAPTER
    schema_version: str = RUNTIME_DECISION_AUTHORITY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter": self.adapter,
            "m2b_action": self.m2b_action,
            "m3_result": self.m3_result.to_dict(),
            "authority_issuance": (
                None
                if self.authority_issuance is None
                else dict(self.authority_issuance)
            ),
            "final_authority": dict(self.final_authority),
            "pid_ownership": dict(self.pid_ownership),
            "manual_decision_override_used": self.manual_decision_override_used,
            "forced_action_used": self.forced_action_used,
            "extra_planner_count": self.extra_planner_count,
            "hidden_controller_count": self.hidden_controller_count,
            "m3_reducer_modified": False,
            "authority_resolver_precedence_modified": False,
            "receipt_contract_modified": False,
        }


def activate_runtime_decision_authority_v1(
    result: ShadowM2BDecisionResult,
) -> RuntimeDecisionAuthorityActivationV1:
    """Apply one producer-native decision to frozen M3 and existing authority."""

    if not isinstance(result, ShadowM2BDecisionResult):
        raise TypeError("RUNTIME_AUTHORITY_REQUIRES_SHADOW_M2B_RESULT")
    now = float(result.decision_monotonic_time)
    baseline_eligible = True
    initial = MinimalM3State(
        lifecycle_state=LifecycleState.DECISION_READY,
        candidate_set_id=result.candidate_set_id,
        candidate_freshness=result.candidate_freshness,
        answer_deadline_monotonic=result.answer_deadline_monotonic,
        decision_deadline_monotonic=result.answer_deadline_monotonic,
        authority=ControlAuthority.BASELINE_CONTROL,
        baseline_authority_eligible=baseline_eligible,
        current_time_monotonic=now,
    )
    verified_safety = _verified_physical_safety(result)
    verified_holding = _verified_holding(result)
    payload = {
        "candidate_set_id": result.candidate_set_id,
        "candidate_freshness": result.candidate_freshness,
        "act_evidence_grade": (
            EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE.value
            if verified_safety
            else EvidenceGrade.UNRESOLVED_REQUIRES_ADDITIONAL_PROBE.value
        ),
        "holding_evidence_grade": (
            EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE.value
            if verified_holding
            else EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value
        ),
        "lease": result.holding_lease if verified_holding else None,
        "baseline_authority_eligible": baseline_eligible,
        "model_forward_requested": False,
        "low_level_control_requested": False,
    }
    event = MinimalM3Event.create(
        event_id="RUNTIME-AUTHORITY-" + result.decision_id,
        event_type=_event_type(result.producer_action),
        query_episode_id=(
            result.producer_query_id
            if result.producer_action in {"ASK", "WAIT"}
            else None
        ),
        source_component=RUNTIME_DECISION_AUTHORITY_ADAPTER,
        observed_monotonic_time=now,
        payload=payload,
    )
    m3_result = reduce_event(initial, event, now)

    resolver = CandidateLiveActAuthorityResolverV0(enabled=True)
    final_resolver = FinalExecutionAuthorityResolverV0(resolver)
    selected = _selected_evidence(result)
    execution_context = CandidateExecutionContext(
        current_candidate_id=(
            result.selected_candidate_id or result.candidate_ids[0]
        ),
        current_candidate_set_id=result.candidate_set_id,
        current_resolved_interpretation_id=(
            selected.interpretation_id if selected is not None else "UNRESOLVED"
        ),
        current_source_observation_id=result.source_observation_id,
        current_source_frame_id=str(result.source_frame_id),
        current_route_digest=(
            selected.candidate_route_digest if selected is not None else "0" * 64
        ),
        current_speed_digest=(
            selected.candidate_speed_digest if selected is not None else "0" * 64
        ),
        candidate_freshness=result.candidate_freshness,
        candidate_invalidated=False,
        active_query=result.producer_action == "ASK",
        active_holding_lease=result.producer_action == "WAIT" and verified_holding,
        independent_safety_guard_active=False,
        baseline_available=True,
        simulation_runtime="CARLA",
        current_monotonic_time=now,
    )
    issuance = None
    receipt = None
    if result.producer_action == "ACT" and selected is not None:
        request = CandidateActAuthorityRequest(
            candidate_id=result.selected_candidate_id,
            candidate_set_id=result.candidate_set_id,
            resolved_interpretation_id=selected.interpretation_id,
            source_observation_id=result.source_observation_id,
            source_frame_id=str(result.source_frame_id),
            route_digest=selected.candidate_route_digest,
            speed_digest=selected.candidate_speed_digest,
            control_window_id="RUNTIME-AUTHORITY-WINDOW-" + result.decision_id,
        )
        issued = resolver.issue(request, m3_result, execution_context)
        issuance = issued.to_dict()
        receipt = issued.receipt

    final = final_resolver.resolve(
        existing_m3_result=m3_result,
        candidate_authority_receipt=receipt,
        current_execution_context=execution_context,
    )
    if result.producer_action == "ACT" and verified_safety and selected is not None:
        expected_transition = ("MC-T002",)
    elif result.producer_action == "WAIT" and verified_holding:
        expected_transition = ("MC-T006",)
    elif result.producer_action == "ASK":
        expected_transition = ("MC-T004",)
    elif result.producer_action == "FALLBACK_RECOMMENDED":
        expected_transition = ("MC-T008",)
    else:
        expected_transition = m3_result.transition_ids
    if m3_result.transition_ids != expected_transition:
        raise RuntimeError("FROZEN_M3_RUNTIME_AUTHORITY_TRANSITION_MISMATCH")

    pid_ownership = {
        "owner": "EXISTING_SIMLINGO_PID",
        "plan_source": (
            "AUTHORIZED_CANDIDATE_PLAN"
            if final.owner.value == "DRIVECLARIFY_CANDIDATE_CONTROL"
            else "CURRENT_VALID_BASELINE_PLAN"
        ),
        "existing_pid_instance_count": 1,
        "new_pid_instance_count": 0,
        "pid_invocations_during_authority_activation": 0,
        "control_writes_during_authority_activation": 0,
    }
    return RuntimeDecisionAuthorityActivationV1(
        m2b_action=result.producer_action,
        m3_result=m3_result,
        authority_issuance=issuance,
        final_authority=final.to_dict(),
        pid_ownership=pid_ownership,
        manual_decision_override_used=False,
        forced_action_used=False,
        extra_planner_count=0,
        hidden_controller_count=0,
    )
