"""fixture 级分派，以及统一的确定性决策记录。"""

from __future__ import annotations

from typing import Any

from .answers import validate_answer
from .models import (
    AskTimingStatus,
    CommitReentryCause,
    Decision,
    DecisionRecord,
    PolicyState,
    TriValue,
    trace_step,
)
from .policy import evaluate_policy, validate_holding
from .state_machine import derive_commit_reentry_cause, reduce_committed_reentry


def _answer_decision(case_input: dict[str, Any]) -> DecisionRecord:
    """把回答校验结果映射为不绕过 revalidation 的语义决策。"""

    state = PolicyState(case_input["current_state"])
    result = validate_answer(
        case_input.get("answer"),
        active_episode_id=case_input["active_episode_id"],
        active_question_id=case_input["active_question_id"],
        answer_deadline_s=float(case_input["answer_deadline_s"]),
        interpretation_valid_until_s=float(case_input["interpretation_valid_until_s"]),
        current_state=state,
        valid_candidate_ids=case_input["valid_candidate_ids"],
    )
    fields = list(result.fields_used)
    unknown = list(result.unknown_fields)
    trace = [trace_step(1, "answer_validation", "PASS" if result.accepted else "REJECT", result.reason_code)]
    holding_summary = None
    wait_reasons = {
        "NO_ANSWER": "WAITING_FOR_ANSWER",
        "INVALID_ANSWER": "INVALID_ANSWER_CONTINUE_WAIT",
        "ANSWER_STILL_AMBIGUOUS": "ANSWER_STILL_AMBIGUOUS_WAIT",
    }

    if result.reason_code in wait_reasons:
        now = case_input.get("now_monotonic_s")
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            holding_status = TriValue.UNKNOWN
            holding_reason = "UNKNOWN_HOLDING_DEADLINE"
            holding_fields = ["now_monotonic_s"]
            holding_unknown = ["now_monotonic_s"]
        else:
            (
                holding_status,
                holding_reason,
                holding_fields,
                holding_unknown,
                holding_summary,
            ) = validate_holding(case_input.get("holding"), float(now))
        fields.extend(holding_fields)
        unknown.extend(holding_unknown)
        if holding_status is TriValue.TRUE:
            decision, reason, next_state, holding_required = (
                Decision.WAIT,
                wait_reasons[result.reason_code],
                PolicyState.HOLDING,
                True,
            )
        else:
            decision, reason, next_state, holding_required = (
                Decision.FALLBACK,
                holding_reason,
                PolicyState.FALLBACK,
                False,
            )
            holding_summary = None
    # 只有 fixture 明确给出当前状态下重新校验通过，回答才可导向 ACT。
    elif result.reason_code == "ANSWER_ACCEPTED_FOR_REVALIDATION" and case_input.get("revalidation_passed") is True:
        decision, reason, next_state, holding_required = (
            Decision.ACT,
            "ANSWER_REVALIDATED_ACT",
            PolicyState.COMMITTED,
            False,
        )
    elif result.opens_new_episode:
        decision, reason, next_state, holding_required = (
            Decision.FALLBACK,
            "NEW_INSTRUCTION_REQUIRES_REFRESH",
            PolicyState.AMBIGUITY_ACTIVE,
            False,
        )
    elif result.reason_code == "LATE_ANSWER_IGNORED":
        decision, reason, next_state, holding_required = (
            Decision.ACT,
            "LATE_ANSWER_IGNORED",
            PolicyState.COMMITTED,
            False,
        )
    else:
        decision, reason, next_state, holding_required = (
            Decision.FALLBACK,
            result.reason_code,
            PolicyState.FALLBACK,
            False,
        )
    return DecisionRecord(
        decision=decision,
        reason_code=reason,
        current_state=state,
        next_state=next_state,
        fields_used=fields,
        unknown_fields=unknown,
        cache_status="NOT_APPLICABLE",
        timing_status=AskTimingStatus.UNKNOWN,
        question_dispatch="NONE",
        holding_required=holding_required,
        holding_summary=holding_summary,
        trace=trace,
        selected_candidate_id=result.selected_candidate_id if decision is Decision.ACT else None,
        diagnostics={
            "answer_classification": result.classification,
            "oracle_mismatch": result.oracle_mismatch,
            "answer_requires_revalidation": result.accepted and not result.opens_new_episode,
        },
    )


def _reentry_decision(case_input: dict[str, Any]) -> DecisionRecord:
    cause = derive_commit_reentry_cause(case_input["event"])
    next_state, clears_cache, resets_budget = reduce_committed_reentry(cause)
    if cause is CommitReentryCause.NONE:
        decision = Decision.ACT
        reason = "COMMIT_REENTRY_NONE"
    else:
        decision = Decision.FALLBACK
        reason = f"REENTRY_{cause.value}"
    return DecisionRecord(
        decision=decision,
        reason_code=reason,
        current_state=PolicyState.COMMITTED,
        next_state=next_state,
        fields_used=[
            "event.new_instruction_id",
            "event.new_ambiguity",
            "event.detector",
            "event.provenance",
            "event.reason_code",
            "event.material_invalidation",
            "event.invalidation_reason",
        ],
        unknown_fields=[],
        cache_status="CLEARED" if clears_cache else "UNCHANGED",
        timing_status=AskTimingStatus.UNKNOWN,
        question_dispatch="NONE",
        holding_required=False,
        trace=[
            trace_step(1, "commit_reentry_guard", "PASS", cause.value),
            trace_step(
                2,
                "episode_effect",
                "OPEN_NEW_EPISODE" if resets_budget else "KEEP_EPISODE",
                reason,
            ),
        ],
        diagnostics={
            "commit_reentry_cause": cause.value,
            "cache_cleared": clears_cache,
            "query_budget_reset_for_new_episode": resets_budget,
        },
    )


def _refresh_decision(case_input: dict[str, Any]) -> DecisionRecord:
    """验证 slow refresh 期间是否存在仍有效的旧控制；不实现实时循环。"""

    state = PolicyState(case_input["current_state"])
    old_control = case_input.get("old_control", {})
    now = float(case_input["now_monotonic_s"])
    holding_summary = None
    valid = (
        old_control.get("validity_status") == "VALID"
        and now < float(old_control.get("valid_until_monotonic_s", 0))
        and old_control.get("hard_fields_known_feasible") is True
    )
    # 旧 HOLDING 也必须携带可验证身份；不能由 type 字段伪造摘要。
    if valid and old_control.get("type") == "HOLDING":
        holding_status, _, _, _, holding_summary = validate_holding(
            old_control.get("holding"),
            now,
        )
        valid = holding_status is TriValue.TRUE
    if valid:
        decision = Decision.ACT if old_control.get("type") == "COMMITTED_PLAN" else Decision.WAIT
        reason = "VALID_OLD_CONTROL_WHILE_REFRESHING"
        next_state = PolicyState.COMMITTED if decision is Decision.ACT else PolicyState.HOLDING
        holding_required = decision is Decision.WAIT
    else:
        decision = Decision.FALLBACK
        reason = "NO_VALID_CONTROL_WHILE_REFRESHING"
        next_state = PolicyState.FALLBACK
        holding_required = False
    return DecisionRecord(
        decision=decision,
        reason_code=reason,
        current_state=state,
        next_state=next_state,
        fields_used=[
            "old_control.type",
            "old_control.validity_status",
            "old_control.valid_until_monotonic_s",
            "old_control.hard_fields_known_feasible",
            "now_monotonic_s",
        ],
        unknown_fields=[],
        cache_status="REFRESH_IN_PROGRESS",
        timing_status=AskTimingStatus.UNKNOWN,
        question_dispatch="NONE",
        holding_required=holding_required,
        holding_summary=holding_summary if decision is Decision.WAIT else None,
        trace=[
            trace_step(
                1,
                "refresh_nonblocking_control",
                "PASS" if valid else "FAIL",
                reason,
            )
        ],
    )


def evaluate_input(case_input: dict[str, Any]) -> DecisionRecord:
    kind = case_input.get("kind", "policy")
    if kind == "policy":
        return evaluate_policy(case_input)
    if kind == "answer":
        return _answer_decision(case_input)
    if kind == "reentry":
        return _reentry_decision(case_input)
    if kind == "refresh":
        return _refresh_decision(case_input)
    raise ValueError(f"UNKNOWN_FIXTURE_KIND:{kind}")
