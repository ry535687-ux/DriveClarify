"""七状态纯 reducer、slow trigger 和 COMMITTED 重入 guard。"""

from __future__ import annotations

from typing import Any

from .models import CommitReentryCause, PolicyState


LEGAL_SLOW_TRIGGERS = {
    "EPISODE_OPEN",
    "CANDIDATE_EXPIRED",
    "MATERIAL_SCENE_CHANGE",
    "TIMELY_ANSWER_RECEIVED",
    "CANDIDATE_INVALIDATED",
    "EXPLICIT_REFRESH_REQUEST",
}


TRANSITIONS: dict[tuple[PolicyState, str], PolicyState] = {
    (PolicyState.NORMAL, "OPEN_AMBIGUITY"): PolicyState.AMBIGUITY_ACTIVE,
    (PolicyState.AMBIGUITY_ACTIVE, "ACT_COMMIT"): PolicyState.COMMITTED,
    (PolicyState.AMBIGUITY_ACTIVE, "QUESTION_DISPATCHED"): PolicyState.QUESTION_SENT,
    (PolicyState.AMBIGUITY_ACTIVE, "HOLD_SELECTED"): PolicyState.HOLDING,
    (PolicyState.QUESTION_SENT, "HOLD_SELECTED"): PolicyState.HOLDING,
    (PolicyState.QUESTION_SENT, "VALID_ANSWER"): PolicyState.ANSWER_RECEIVED,
    (PolicyState.HOLDING, "VALID_ANSWER"): PolicyState.ANSWER_RECEIVED,
    (PolicyState.HOLDING, "ACT_COMMIT"): PolicyState.COMMITTED,
    (PolicyState.ANSWER_RECEIVED, "ANSWER_REVALIDATED"): PolicyState.COMMITTED,
    (PolicyState.ANSWER_RECEIVED, "NEW_INSTRUCTION"): PolicyState.AMBIGUITY_ACTIVE,
    (PolicyState.FALLBACK, "RECOVER_NORMAL"): PolicyState.NORMAL,
}


def reduce_state(current_state: PolicyState | str, event: str) -> PolicyState:
    """执行一条显式状态转移；任意状态都允许进入 FALLBACK。"""

    current = PolicyState(current_state)
    if event == "FALLBACK":
        return PolicyState.FALLBACK
    try:
        return TRANSITIONS[(current, event)]
    except KeyError as exc:
        raise ValueError(f"ILLEGAL_STATE_TRANSITION:{current.value}:{event}") from exc


def validate_slow_trigger(trigger: dict[str, Any], active_episode_id: str) -> str:
    if trigger.get("type") not in LEGAL_SLOW_TRIGGERS:
        return "ILLEGAL_REFRESH_TRIGGER"
    if trigger.get("episode_id") != active_episode_id:
        return "STALE_REFRESH_TRIGGER"
    for field in ("monotonic_time_s", "source", "provenance", "reason_code"):
        if trigger.get(field) in (None, ""):
            return "INVALID_REFRESH_TRIGGER_METADATA"
    return "VALID_REFRESH_TRIGGER"


def derive_commit_reentry_cause(event: dict[str, Any]) -> CommitReentryCause:
    """按固定优先级推导重入原因，缺少 provenance 时 fail closed 为 NONE。"""

    if event.get("new_instruction_id") and event.get("instruction_valid") is True:
        return CommitReentryCause.NEW_INSTRUCTION
    if (
        event.get("new_ambiguity") is True
        and event.get("detector")
        and event.get("provenance")
        and event.get("reason_code")
    ):
        return CommitReentryCause.NEW_AMBIGUITY
    if event.get("material_invalidation") is True and event.get("invalidation_reason"):
        return CommitReentryCause.MATERIAL_INVALIDATION
    return CommitReentryCause.NONE


def reduce_committed_reentry(
    cause: CommitReentryCause | str,
) -> tuple[PolicyState, bool, bool]:
    """返回下一状态，以及是否清 cache、是否为新 episode 恢复预算。"""

    cause = CommitReentryCause(cause)
    if cause is CommitReentryCause.NONE:
        return PolicyState.COMMITTED, False, False
    return PolicyState.AMBIGUITY_ACTIVE, True, True
