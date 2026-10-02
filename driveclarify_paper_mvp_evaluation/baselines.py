"""Executable, side-effect-free decision contracts for the eight methods."""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    BaselineRuntimeInput,
    ContractError,
    GateStatus,
    InteractionPhase,
    METHOD_ORDER,
    MethodDecision,
    MethodId,
    RuntimeAction,
    RuntimeCandidate,
)
from .freeze import FROZEN_BASELINE_CONFIG, validate_baseline_config


def _decision(
    method_id: str,
    value: BaselineRuntimeInput,
    action: RuntimeAction,
    *reasons: str,
    selected: str | None = None,
    compute_budget_case: str,
) -> MethodDecision:
    return MethodDecision(
        method_id=method_id,
        action=action,
        selected_candidate_id=selected,
        reason_codes=tuple(reasons),
        interaction_phase=value.phase,
        compute_budget_case=compute_budget_case,
        query_requested=action is RuntimeAction.ASK,
        holding_requested=action is RuntimeAction.WAIT,
        stop_requested=action is RuntimeAction.STOP,
    )


def _fallback(
    method_id: str,
    value: BaselineRuntimeInput,
    *reasons: str,
    compute_budget_case: str,
) -> MethodDecision:
    return _decision(
        method_id,
        value,
        RuntimeAction.FALLBACK,
        *reasons,
        compute_budget_case=compute_budget_case,
    )


def _act_gates_pass(value: BaselineRuntimeInput) -> bool:
    return (
        value.hard_safety_status is GateStatus.PASS
        and value.hard_rule_status is GateStatus.PASS
    )


def _opaque_tie_key(episode_id: str, candidate_id: str) -> str:
    return hashlib.sha256(f"{episode_id}\0{candidate_id}".encode("utf-8")).hexdigest()


def _select_extreme(
    value: BaselineRuntimeInput,
    *,
    field: str,
    highest: bool,
) -> RuntimeCandidate | None:
    if not value.candidates:
        return None
    scored: list[tuple[float, RuntimeCandidate]] = []
    for candidate in value.candidates:
        score = getattr(candidate, field)
        if score is None:
            return None
        scored.append((float(score), candidate))
    extreme = (max if highest else min)(score for score, _ in scored)
    tied = [candidate for score, candidate in scored if score == extreme]
    return min(tied, key=lambda item: _opaque_tie_key(value.episode_id, item.candidate_id))


def _candidate_act(
    method_id: str,
    value: BaselineRuntimeInput,
    candidate_id: str | None,
    reason: str,
    *,
    compute_budget_case: str,
) -> MethodDecision:
    if candidate_id is None or candidate_id not in value.candidate_ids:
        return _fallback(
            method_id,
            value,
            "RUNTIME_SELECTED_CANDIDATE_UNKNOWN",
            compute_budget_case=compute_budget_case,
        )
    if not _act_gates_pass(value):
        return _fallback(
            method_id,
            value,
            "ACT_HARD_GATE_NOT_PASS",
            f"SAFETY_{value.hard_safety_status.value}",
            f"RULE_{value.hard_rule_status.value}",
            compute_budget_case=compute_budget_case,
        )
    return _decision(
        method_id,
        value,
        RuntimeAction.ACT,
        reason,
        selected=candidate_id,
        compute_budget_case=compute_budget_case,
    )


def _post_query_lifecycle(
    method_id: str,
    value: BaselineRuntimeInput,
    *,
    answer_compute_budget_case: str,
    no_replan_compute_budget_case: str,
) -> MethodDecision | None:
    """Resolve phases after a method has asked; return None only for INITIAL."""

    if value.phase is InteractionPhase.INITIAL:
        return None
    if value.phase is InteractionPhase.ANSWER_RECEIVED:
        return _candidate_act(
            method_id,
            value,
            value.answer_candidate_id,
            "ANSWER_SELECTED_RUNTIME_CANDIDATE",
            compute_budget_case=answer_compute_budget_case,
        )
    if value.phase is InteractionPhase.QUERY_PENDING:
        if value.holding_verified and value.future_information_before_deadline:
            return _decision(
                method_id,
                value,
                RuntimeAction.WAIT,
                "ACTIVE_QUERY_NO_SECOND_ASK",
                "VERIFIED_HOLDING_UNTIL_ON_TIME_ANSWER",
                compute_budget_case=no_replan_compute_budget_case,
            )
        return _fallback(
            method_id,
            value,
            "ACTIVE_QUERY_NO_SECOND_ASK",
            "VERIFIED_HOLDING_OR_ON_TIME_ANSWER_UNAVAILABLE",
            compute_budget_case=no_replan_compute_budget_case,
        )
    return _fallback(
        method_id,
        value,
        "QUERY_LIFECYCLE_TERMINAL_WITHOUT_VALID_ANSWER",
        compute_budget_case=no_replan_compute_budget_case,
    )


def original_simlingo(
    value: BaselineRuntimeInput, config: Mapping[str, Any]
) -> MethodDecision:
    method_id = MethodId.ORIGINAL_SIMLINGO.value
    compute_case = "NORMAL_AMBIGUOUS_INSTRUCTION_FORWARD"
    if not value.original_simlingo_plan_available:
        return _fallback(
            method_id,
            value,
            "ORIGINAL_SIMLINGO_PLAN_UNAVAILABLE",
            compute_budget_case=compute_case,
        )
    if not _act_gates_pass(value):
        return _fallback(
            method_id,
            value,
            "ORIGINAL_PLAN_HARD_GATE_NOT_PASS",
            f"SAFETY_{value.hard_safety_status.value}",
            f"RULE_{value.hard_rule_status.value}",
            compute_budget_case=compute_case,
        )
    return _decision(
        method_id,
        value,
        RuntimeAction.ACT,
        "IGNORE_DRIVECLARIFY_AMBIGUOUS_UTTERANCE",
        "CONTINUE_SINGLE_ORIGINAL_SIMLINGO_PLAN",
        compute_budget_case=compute_case,
    )


def driveclarify(
    value: BaselineRuntimeInput, config: Mapping[str, Any]
) -> MethodDecision:
    method_id = MethodId.DRIVECLARIFY.value
    compute_case = {
        InteractionPhase.INITIAL: "INITIAL_K2_CANDIDATE_DECISION",
        InteractionPhase.ANSWER_RECEIVED: "POST_ANSWER_SINGLE_CANDIDATE_REPLAN",
        InteractionPhase.FUTURE_INFORMATION_ARRIVED: (
            "POST_INFORMATION_SINGLE_CANDIDATE_REPLAN"
        ),
        InteractionPhase.QUERY_PENDING: "QUERY_HOLD_OR_TERMINAL_NO_REPLAN",
        InteractionPhase.DEADLINE_EXPIRED: "QUERY_HOLD_OR_TERMINAL_NO_REPLAN",
    }[value.phase]
    if not value.authority_resolver_applied:
        return _fallback(
            method_id,
            value,
            "LIVE_AUTHORITY_RESOLVER_NOT_APPLIED",
            compute_budget_case=compute_case,
        )
    action = value.authoritative_driveclarify_action
    if action is None:
        return _fallback(
            method_id,
            value,
            "AUTHORITATIVE_DECISION_UNKNOWN",
            compute_budget_case=compute_case,
        )
    if action is RuntimeAction.ACT:
        return _candidate_act(
            method_id,
            value,
            value.authoritative_driveclarify_candidate_id,
            "PRESERVED_AUTHORITY_RESOLVER_ACT",
            compute_budget_case=compute_case,
        )
    if action is RuntimeAction.ASK:
        return _decision(
            method_id,
            value,
            action,
            "PRESERVED_AUTHORITY_RESOLVER_ASK",
            compute_budget_case=compute_case,
        )
    if action is RuntimeAction.WAIT:
        if not value.holding_verified:
            return _fallback(
                method_id,
                value,
                "AUTHORITY_WAIT_WITHOUT_VERIFIED_HOLDING",
                compute_budget_case=compute_case,
            )
        return _decision(
            method_id,
            value,
            action,
            "PRESERVED_AUTHORITY_RESOLVER_WAIT",
            compute_budget_case=compute_case,
        )
    if action is RuntimeAction.FALLBACK:
        return _fallback(
            method_id,
            value,
            "PRESERVED_AUTHORITY_RESOLVER_FALLBACK",
            compute_budget_case=compute_case,
        )
    return _fallback(
        method_id,
        value,
        "DRIVECLARIFY_STOP_OUTPUT_OUTSIDE_ACT_ASK_WAIT_AUTHORITY",
        compute_budget_case=compute_case,
    )


def always_ask(
    value: BaselineRuntimeInput, config: Mapping[str, Any]
) -> MethodDecision:
    method_id = MethodId.ALWAYS_ASK.value
    lifecycle = _post_query_lifecycle(
        method_id,
        value,
        answer_compute_budget_case="POST_ANSWER_SINGLE_CANDIDATE_REPLAN",
        no_replan_compute_budget_case="QUERY_HOLD_OR_TERMINAL_NO_REPLAN",
    )
    if lifecycle is not None:
        return lifecycle
    return _decision(
        method_id,
        value,
        RuntimeAction.ASK,
        "FROZEN_ALWAYS_ASK_INITIAL_POLICY",
        compute_budget_case="INITIAL_CONSTANT_ASK_NO_CANDIDATE_PLAN",
    )


def always_stop(
    value: BaselineRuntimeInput, config: Mapping[str, Any]
) -> MethodDecision:
    return _decision(
        MethodId.ALWAYS_STOP.value,
        value,
        RuntimeAction.STOP,
        "FROZEN_ALWAYS_STOP_POLICY",
        "STOP_NOT_WAIT_AND_NOT_DECISION_SCORE_ALIAS",
        compute_budget_case="CONSTANT_STOP_NO_CANDIDATE_PLAN",
    )


def always_wait(
    value: BaselineRuntimeInput, config: Mapping[str, Any]
) -> MethodDecision:
    method_id = MethodId.ALWAYS_WAIT.value
    if value.phase is InteractionPhase.FUTURE_INFORMATION_ARRIVED:
        return _candidate_act(
            method_id,
            value,
            value.future_information_candidate_id,
            "FUTURE_INFORMATION_SELECTED_RUNTIME_CANDIDATE",
            compute_budget_case="POST_INFORMATION_SINGLE_CANDIDATE_REPLAN",
        )
    if value.phase in {InteractionPhase.ANSWER_RECEIVED, InteractionPhase.DEADLINE_EXPIRED}:
        return _fallback(
            method_id,
            value,
            "ALWAYS_WAIT_LIFECYCLE_ENDED_WITHOUT_FUTURE_SELECTION",
            compute_budget_case="WAIT_OR_TERMINAL_NO_REPLAN",
        )
    if value.holding_verified and value.future_information_before_deadline:
        return _decision(
            method_id,
            value,
            RuntimeAction.WAIT,
            "FROZEN_ALWAYS_WAIT_POLICY",
            "VERIFIED_HOLDING_AND_ON_TIME_INFORMATION",
            compute_budget_case="WAIT_OR_TERMINAL_NO_REPLAN",
        )
    return _fallback(
        method_id,
        value,
        "ALWAYS_WAIT_PRECONDITION_UNKNOWN_OR_UNAVAILABLE",
        "WAIT_NOT_ALIASED_TO_STOP",
        compute_budget_case="WAIT_OR_TERMINAL_NO_REPLAN",
    )


def never_ask(
    value: BaselineRuntimeInput, config: Mapping[str, Any]
) -> MethodDecision:
    method_id = MethodId.NEVER_ASK.value
    selected = _select_extreme(value, field="rank_score", highest=True)
    if selected is None:
        return _fallback(
            method_id,
            value,
            "RUNTIME_CANDIDATE_RANK_UNKNOWN",
            compute_budget_case="K2_RUNTIME_RANK_DECISION",
        )
    return _candidate_act(
        method_id,
        value,
        selected.candidate_id,
        "TOP_RUNTIME_RANK_OPAQUE_TIE_BREAK",
        compute_budget_case="K2_RUNTIME_RANK_DECISION",
    )


def _threshold(config: Mapping[str, Any], method_id: str) -> float:
    method = next(item for item in config["methods"] if item["id"] == method_id)
    threshold = method["threshold"]
    if not isinstance(threshold, Mapping):
        raise ContractError(f"THRESHOLD_REQUIRED:{method_id}")
    return float(threshold["value"])


def language_only_uncertainty(
    value: BaselineRuntimeInput, config: Mapping[str, Any]
) -> MethodDecision:
    method_id = MethodId.LANGUAGE_ONLY_UNCERTAINTY.value
    lifecycle = _post_query_lifecycle(
        method_id,
        value,
        answer_compute_budget_case="POST_ANSWER_SINGLE_CANDIDATE_REPLAN",
        no_replan_compute_budget_case="QUERY_HOLD_OR_TERMINAL_NO_REPLAN",
    )
    if lifecycle is not None:
        return lifecycle
    if value.language_uncertainty is None:
        return _fallback(
            method_id,
            value,
            "LANGUAGE_UNCERTAINTY_UNKNOWN",
            compute_budget_case="INITIAL_LANGUAGE_GATE_NO_CANDIDATE_PLAN",
        )
    if float(value.language_uncertainty) >= _threshold(config, method_id):
        return _decision(
            method_id,
            value,
            RuntimeAction.ASK,
            "LANGUAGE_UNCERTAINTY_GTE_FROZEN_THRESHOLD",
            compute_budget_case="INITIAL_LANGUAGE_GATE_NO_CANDIDATE_PLAN",
        )
    selected = _select_extreme(value, field="rank_score", highest=True)
    if selected is None:
        return _fallback(
            method_id,
            value,
            "RUNTIME_CANDIDATE_RANK_UNKNOWN",
            compute_budget_case="INITIAL_LOW_UNCERTAINTY_K2_RUNTIME_RANK",
        )
    return _candidate_act(
        method_id,
        value,
        selected.candidate_id,
        "LANGUAGE_UNCERTAINTY_BELOW_FROZEN_THRESHOLD",
        compute_budget_case="INITIAL_LOW_UNCERTAINTY_K2_RUNTIME_RANK",
    )


def risk_only(
    value: BaselineRuntimeInput, config: Mapping[str, Any]
) -> MethodDecision:
    method_id = MethodId.RISK_ONLY.value
    lifecycle = _post_query_lifecycle(
        method_id,
        value,
        answer_compute_budget_case="POST_ANSWER_SINGLE_CANDIDATE_REPLAN",
        no_replan_compute_budget_case="QUERY_HOLD_OR_TERMINAL_NO_REPLAN",
    )
    if lifecycle is not None:
        return lifecycle
    if not value.candidates or any(item.risk_score is None for item in value.candidates):
        return _fallback(
            method_id,
            value,
            "RUNTIME_CANDIDATE_RISK_UNKNOWN",
            compute_budget_case="INITIAL_K2_RISK_COMPARISON",
        )
    risks = [float(item.risk_score) for item in value.candidates if item.risk_score is not None]
    divergence = max(risks) - min(risks)
    if divergence >= _threshold(config, method_id):
        return _decision(
            method_id,
            value,
            RuntimeAction.ASK,
            "RUNTIME_RISK_DIVERGENCE_GTE_FROZEN_THRESHOLD",
            compute_budget_case="INITIAL_K2_RISK_COMPARISON",
        )
    selected = _select_extreme(value, field="risk_score", highest=False)
    assert selected is not None
    return _candidate_act(
        method_id,
        value,
        selected.candidate_id,
        "RUNTIME_RISK_DIVERGENCE_BELOW_FROZEN_THRESHOLD",
        compute_budget_case="INITIAL_K2_RISK_COMPARISON",
    )


_ENTRYPOINTS: dict[
    str, Callable[[BaselineRuntimeInput, Mapping[str, Any]], MethodDecision]
] = {
    MethodId.ORIGINAL_SIMLINGO.value: original_simlingo,
    MethodId.DRIVECLARIFY.value: driveclarify,
    MethodId.ALWAYS_ASK.value: always_ask,
    MethodId.ALWAYS_STOP.value: always_stop,
    MethodId.ALWAYS_WAIT.value: always_wait,
    MethodId.NEVER_ASK.value: never_ask,
    MethodId.LANGUAGE_ONLY_UNCERTAINTY.value: language_only_uncertainty,
    MethodId.RISK_ONLY.value: risk_only,
}


def execute_method(
    method_id: str,
    value: BaselineRuntimeInput,
    config: Mapping[str, Any] = FROZEN_BASELINE_CONFIG,
) -> MethodDecision:
    """Execute exactly one frozen method reducer without runtime side effects."""

    validated = validate_baseline_config(config)
    if method_id not in _ENTRYPOINTS or method_id not in METHOD_ORDER:
        raise ContractError(f"UNKNOWN_METHOD_ID:{method_id}")
    return _ENTRYPOINTS[method_id](value, validated)


def implementation_entrypoints() -> Sequence[str]:
    return tuple(_ENTRYPOINTS)
