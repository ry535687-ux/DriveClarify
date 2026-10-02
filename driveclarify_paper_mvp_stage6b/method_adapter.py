"""One lifecycle adapter for all eight frozen Stage 6B methods."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from driveclarify_paper_mvp_evaluation.baselines import execute_method
from driveclarify_paper_mvp_evaluation.contracts import (
    BaselineRuntimeInput,
    GateStatus,
    InteractionPhase,
    MethodDecision,
    MethodId,
    RuntimeAction,
    RuntimeCandidate,
)
from driveclarify_paper_mvp_evaluation.freeze import FROZEN_BASELINE_CONFIG

from .contracts import (
    ForwardAccounting,
    Freshness,
    MethodOutput,
    PlanSource,
    Stage6BContractError,
)


SINGLE_INTERPRETATION_COMPUTE_CASE = "SINGLE_EFFECTIVE_INTERPRETATION_RUNTIME_PLAN"


def _receipt(episode_id: str, method_id: str, candidate_id: str) -> str:
    return "authority-" + hashlib.sha256(
        f"{episode_id}\0{method_id}\0{candidate_id}".encode("utf-8")
    ).hexdigest()[:24]


def _plan_source(decision: MethodDecision) -> PlanSource:
    if decision.action is RuntimeAction.ACT:
        return (
            PlanSource.ORIGINAL_SIMLINGO_PLAN
            if decision.method_id == MethodId.ORIGINAL_SIMLINGO.value
            else PlanSource.CANDIDATE_CONDITIONED_PLAN
        )
    if decision.action in {RuntimeAction.ASK, RuntimeAction.WAIT}:
        return PlanSource.CURRENT_VALID_HOLDING_PLAN
    if decision.action is RuntimeAction.STOP:
        return PlanSource.POLICY_STOP_SPEED_PLAN
    return PlanSource.BASELINE_FALLBACK_PLAN


@dataclass
class AdapterState:
    episode_id: str
    method_id: str
    raw_instruction: str
    effective_k: int
    phase: InteractionPhase = InteractionPhase.INITIAL
    current_observation_id: str = "not-observed"
    answer_candidate_id: str | None = None
    information_candidate_id: str | None = None
    candidate_cache_invalidated: bool = False
    replan_count: int = 0
    query_count: int = 0
    wait_entry_count: int = 0
    outputs: list[MethodOutput] = field(default_factory=list)
    terminal: bool = False
    termination_reason: str | None = None


class UnifiedMethodAdapter:
    """Lifecycle facade; the only method-specific code is the frozen reducer."""

    def __init__(
        self,
        method_id: str,
        *,
        config: Mapping[str, Any] = FROZEN_BASELINE_CONFIG,
    ) -> None:
        try:
            MethodId(method_id)
        except ValueError as exc:
            raise Stage6BContractError("UNKNOWN_METHOD_ID:" + method_id) from exc
        self.method_id = method_id
        self.config = config
        self.state: AdapterState | None = None

    def initialize_episode(
        self,
        *,
        episode_id: str,
        raw_instruction: str,
        effective_k: int,
    ) -> None:
        if self.state is not None:
            raise Stage6BContractError("ADAPTER_ALREADY_INITIALIZED")
        if effective_k not in {0, 1, 2}:
            raise Stage6BContractError("EFFECTIVE_K_OUT_OF_SUPPORTED_RANGE")
        self.state = AdapterState(
            episode_id=episode_id,
            method_id=self.method_id,
            raw_instruction=raw_instruction,
            effective_k=effective_k,
        )

    def observe(self, *, observation_id: str) -> None:
        state = self._state()
        if not observation_id:
            raise Stage6BContractError("OBSERVATION_ID_REQUIRED")
        state.current_observation_id = observation_id

    def required_initial_candidate_forwards(
        self, *, language_uncertainty: float | None
    ) -> int:
        state = self._state()
        if state.effective_k == 0:
            return 0
        if state.effective_k == 1:
            return 1 if self.method_id in {
                MethodId.DRIVECLARIFY.value,
                MethodId.NEVER_ASK.value,
                MethodId.LANGUAGE_ONLY_UNCERTAINTY.value,
                MethodId.RISK_ONLY.value,
            } else 0
        if self.method_id in {
            MethodId.DRIVECLARIFY.value,
            MethodId.NEVER_ASK.value,
            MethodId.RISK_ONLY.value,
        }:
            return 2
        if self.method_id == MethodId.LANGUAGE_ONLY_UNCERTAINTY.value:
            if language_uncertainty is None:
                return 0
            return 0 if float(language_uncertainty) >= 0.5 else 2
        return 0

    def on_answer(self, *, selected_candidate_id: str | None) -> None:
        state = self._state()
        if state.phase not in {
            InteractionPhase.INITIAL,
            InteractionPhase.QUERY_PENDING,
        }:
            raise Stage6BContractError("ANSWER_PHASE_INVALID")
        state.phase = InteractionPhase.ANSWER_RECEIVED
        state.answer_candidate_id = selected_candidate_id
        state.candidate_cache_invalidated = True
        state.replan_count += 1

    def on_information_update(self, *, selected_candidate_id: str | None) -> None:
        state = self._state()
        if state.phase not in {
            InteractionPhase.INITIAL,
            InteractionPhase.QUERY_PENDING,
        }:
            raise Stage6BContractError("INFORMATION_PHASE_INVALID")
        state.phase = InteractionPhase.FUTURE_INFORMATION_ARRIVED
        state.information_candidate_id = selected_candidate_id
        state.candidate_cache_invalidated = True
        state.replan_count += 1

    def mark_query_pending(self) -> None:
        state = self._state()
        state.phase = InteractionPhase.QUERY_PENDING

    def mark_deadline_expired(self) -> None:
        self._state().phase = InteractionPhase.DEADLINE_EXPIRED

    def decide(
        self,
        *,
        candidates: Sequence[RuntimeCandidate],
        language_uncertainty: float | None,
        holding_verified: bool,
        future_information_before_deadline: bool,
        hard_safety_status: GateStatus,
        hard_rule_status: GateStatus,
        authoritative_driveclarify_action: RuntimeAction | None = None,
        authoritative_driveclarify_candidate_id: str | None = None,
        authority_resolver_applied: bool = False,
        observed_candidate_forwards: int = 0,
        observed_normal_forwards: int = 1,
        candidate_forward_input_sha256: tuple[str, ...] = (),
        candidate_forward_output_sha256: tuple[str, ...] = (),
        candidate_forward_latencies_seconds: tuple[float, ...] = (),
        runtime_evidence_ids: tuple[str, ...] = (),
    ) -> MethodOutput:
        state = self._state()
        if state.terminal:
            raise Stage6BContractError("DECISION_AFTER_TERMINATION_FORBIDDEN")
        if len(candidates) != state.effective_k and state.phase is InteractionPhase.INITIAL:
            raise Stage6BContractError("ADAPTER_EFFECTIVE_K_CANDIDATE_COUNT_MISMATCH")
        if state.effective_k == 1 and state.phase is InteractionPhase.INITIAL:
            output = self._single_interpretation_decision(
                candidates=candidates,
                holding_verified=holding_verified,
                future_information_before_deadline=future_information_before_deadline,
                hard_safety_status=hard_safety_status,
                hard_rule_status=hard_rule_status,
                observed_candidate_forwards=observed_candidate_forwards,
                observed_normal_forwards=observed_normal_forwards,
                candidate_forward_input_sha256=candidate_forward_input_sha256,
                candidate_forward_output_sha256=candidate_forward_output_sha256,
                candidate_forward_latencies_seconds=candidate_forward_latencies_seconds,
                runtime_evidence_ids=runtime_evidence_ids,
            )
            self._record(output)
            return output

        value = BaselineRuntimeInput(
            episode_id=state.episode_id,
            raw_instruction=state.raw_instruction,
            observation_id=state.current_observation_id,
            candidates=tuple(candidates),
            phase=state.phase,
            language_uncertainty=language_uncertainty,
            answer_candidate_id=state.answer_candidate_id,
            future_information_candidate_id=state.information_candidate_id,
            holding_verified=holding_verified,
            future_information_before_deadline=future_information_before_deadline,
            hard_safety_status=hard_safety_status,
            hard_rule_status=hard_rule_status,
            original_simlingo_plan_available=True,
            authoritative_driveclarify_action=authoritative_driveclarify_action,
            authoritative_driveclarify_candidate_id=(
                authoritative_driveclarify_candidate_id
            ),
            authority_resolver_applied=authority_resolver_applied,
            runtime_provenance={
                "policy_expected_decision_reads": 0,
                "policy_gold_candidate_index_reads": 0,
                "policy_evaluator_annotation_reads": 0,
            },
        )
        decision = execute_method(self.method_id, value, self.config)
        accounting = ForwardAccounting(
            compute_budget_case=decision.compute_budget_case,
            required_candidate_forwards=decision.candidate_model_forward_budget,
            observed_candidate_forwards=observed_candidate_forwards,
            observed_normal_forwards=observed_normal_forwards,
            candidate_forward_input_sha256=candidate_forward_input_sha256,
            candidate_forward_output_sha256=candidate_forward_output_sha256,
            candidate_forward_latencies_seconds=candidate_forward_latencies_seconds,
        )
        output = MethodOutput(
            method_id=self.method_id,
            action=decision.action,
            selected_candidate_id=decision.selected_candidate_id,
            plan_source=_plan_source(decision),
            interaction_phase=state.phase,
            decision_reason=decision.reason_codes,
            runtime_evidence_ids=runtime_evidence_ids,
            freshness=(
                Freshness.FRESH
                if decision.action is RuntimeAction.ACT
                else Freshness.INVALIDATED
                if state.candidate_cache_invalidated
                else Freshness.NOT_APPLICABLE
            ),
            forward_accounting=accounting,
            query_request=decision.query_requested,
            wait_request=decision.holding_requested,
            stop_request=decision.stop_requested,
            authority_receipt_id=(
                _receipt(state.episode_id, self.method_id, decision.selected_candidate_id)
                if decision.action is RuntimeAction.ACT
                and decision.selected_candidate_id is not None
                else None
            ),
        )
        self._record(output)
        return output

    def _single_interpretation_decision(
        self,
        *,
        candidates: Sequence[RuntimeCandidate],
        holding_verified: bool,
        future_information_before_deadline: bool,
        hard_safety_status: GateStatus,
        hard_rule_status: GateStatus,
        observed_candidate_forwards: int,
        observed_normal_forwards: int,
        candidate_forward_input_sha256: tuple[str, ...],
        candidate_forward_output_sha256: tuple[str, ...],
        candidate_forward_latencies_seconds: tuple[float, ...],
        runtime_evidence_ids: tuple[str, ...],
    ) -> MethodOutput:
        state = self._state()
        candidate_id = candidates[0].candidate_id if candidates else None
        if self.method_id == MethodId.ORIGINAL_SIMLINGO.value:
            action = RuntimeAction.ACT
            selected = None
            source = PlanSource.ORIGINAL_SIMLINGO_PLAN
            required = 0
            reasons = ("ORIGINAL_SIMLINGO_RAW_INSTRUCTION_NORMAL_PLAN",)
        elif self.method_id == MethodId.ALWAYS_ASK.value:
            action, selected, source, required = (
                RuntimeAction.ASK,
                None,
                PlanSource.CURRENT_VALID_HOLDING_PLAN,
                0,
            )
            reasons = ("FROZEN_ALWAYS_ASK_INITIAL_POLICY",)
        elif self.method_id == MethodId.ALWAYS_STOP.value:
            action, selected, source, required = (
                RuntimeAction.STOP,
                None,
                PlanSource.POLICY_STOP_SPEED_PLAN,
                0,
            )
            reasons = ("FROZEN_ALWAYS_STOP_POLICY",)
        elif self.method_id == MethodId.ALWAYS_WAIT.value:
            if holding_verified and future_information_before_deadline:
                action, source = RuntimeAction.WAIT, PlanSource.CURRENT_VALID_HOLDING_PLAN
                reasons = ("FROZEN_ALWAYS_WAIT_POLICY",)
            else:
                action, source = RuntimeAction.FALLBACK, PlanSource.BASELINE_FALLBACK_PLAN
                reasons = ("ALWAYS_WAIT_PRECONDITION_UNKNOWN_OR_UNAVAILABLE",)
            selected, required = None, 0
        elif hard_safety_status is GateStatus.PASS and hard_rule_status is GateStatus.PASS:
            action, selected, source, required = (
                RuntimeAction.ACT,
                candidate_id,
                PlanSource.CANDIDATE_CONDITIONED_PLAN,
                1,
            )
            reasons = (
                "CANDIDATE_SET_COLLAPSED_TO_SINGLE_INTERPRETATION",
                "NO_MATERIAL_AMBIGUITY_SINGLE_INTERPRETATION_ACT",
            )
        else:
            action, selected, source, required = (
                RuntimeAction.FALLBACK,
                None,
                PlanSource.BASELINE_FALLBACK_PLAN,
                1,
            )
            reasons = ("SINGLE_INTERPRETATION_HARD_GATE_NOT_PASS",)
        accounting = ForwardAccounting(
            compute_budget_case=SINGLE_INTERPRETATION_COMPUTE_CASE,
            required_candidate_forwards=required,
            observed_candidate_forwards=observed_candidate_forwards,
            observed_normal_forwards=observed_normal_forwards,
            candidate_forward_input_sha256=candidate_forward_input_sha256,
            candidate_forward_output_sha256=candidate_forward_output_sha256,
            candidate_forward_latencies_seconds=candidate_forward_latencies_seconds,
        )
        return MethodOutput(
            method_id=self.method_id,
            action=action,
            selected_candidate_id=selected,
            plan_source=source,
            interaction_phase=state.phase,
            decision_reason=reasons,
            runtime_evidence_ids=runtime_evidence_ids,
            freshness=(Freshness.FRESH if action is RuntimeAction.ACT else Freshness.NOT_APPLICABLE),
            forward_accounting=accounting,
            query_request=action is RuntimeAction.ASK,
            wait_request=action is RuntimeAction.WAIT,
            stop_request=action is RuntimeAction.STOP,
            authority_receipt_id=(
                _receipt(state.episode_id, self.method_id, selected)
                if selected is not None
                else None
            ),
        )

    def select_plan(self, output: MethodOutput) -> PlanSource:
        if output.method_id != self.method_id:
            raise Stage6BContractError("PLAN_SELECTION_METHOD_ID_MISMATCH")
        return output.plan_source

    def termination_state(self) -> Mapping[str, Any]:
        state = self._state()
        return {
            "terminal": state.terminal,
            "termination_reason": state.termination_reason,
            "phase": state.phase.value,
            "query_count": state.query_count,
            "wait_entry_count": state.wait_entry_count,
            "replan_count": state.replan_count,
        }

    def terminate(self, reason: str) -> None:
        state = self._state()
        state.terminal = True
        state.termination_reason = reason

    def finalize_episode(self) -> Mapping[str, Any]:
        state = self._state()
        return {
            "episode_id": state.episode_id,
            "method_id": state.method_id,
            "phase": state.phase.value,
            "effective_k": state.effective_k,
            "candidate_cache_invalidated": state.candidate_cache_invalidated,
            "query_count": state.query_count,
            "wait_entry_count": state.wait_entry_count,
            "replan_count": state.replan_count,
            "terminal": state.terminal,
            "termination_reason": state.termination_reason,
            "outputs": [item.to_dict() for item in state.outputs],
        }

    def _record(self, output: MethodOutput) -> None:
        state = self._state()
        state.outputs.append(output)
        if output.action is RuntimeAction.ASK:
            state.query_count += 1
        if output.action is RuntimeAction.WAIT:
            state.wait_entry_count += 1

    def _state(self) -> AdapterState:
        if self.state is None:
            raise Stage6BContractError("ADAPTER_NOT_INITIALIZED")
        return self.state


__all__ = [
    "AdapterState",
    "SINGLE_INTERPRETATION_COMPUTE_CASE",
    "UnifiedMethodAdapter",
]
