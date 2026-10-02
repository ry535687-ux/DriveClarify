"""结构化 oracle 回答的 episode、question 与过期校验。"""

from __future__ import annotations

from typing import Any

from .models import AnswerResult, PolicyState


def validate_answer(
    answer: dict[str, Any] | None,
    *,
    active_episode_id: str,
    active_question_id: str,
    answer_deadline_s: float,
    interpretation_valid_until_s: float,
    current_state: PolicyState | str,
    valid_candidate_ids: list[str],
) -> AnswerResult:
    """验证回答绑定关系；返回 accepted 仍不代表可以直接执行旧计划。"""

    fields = (
        "answer.episode_id",
        "answer.question_id",
        "answer.arrived_at_monotonic_s",
        "answer.classification",
        "answer.selected_candidate_id",
        "answer.oracle_mismatch",
        "answer_deadline_s",
        "interpretation_valid_until_s",
    )
    state = PolicyState(current_state)
    if answer is None:
        return AnswerResult("NO_ANSWER", False, "NO_ANSWER", None, False, False, fields, ())

    # COMMITTED 后的旧回答只记录，永远不能重新打开旧 episode。
    if state is PolicyState.COMMITTED:
        return AnswerResult(
            answer.get("classification", "UNKNOWN"),
            False,
            "LATE_ANSWER_IGNORED",
            None,
            False,
            bool(answer.get("oracle_mismatch", False)),
            fields,
            (),
        )
    if answer.get("episode_id") != active_episode_id:
        return AnswerResult(
            answer.get("classification", "UNKNOWN"),
            False,
            "ANSWER_EPISODE_MISMATCH",
            None,
            False,
            False,
            fields,
            ("answer.episode_id",),
        )
    if answer.get("question_id") != active_question_id:
        return AnswerResult(
            answer.get("classification", "UNKNOWN"),
            False,
            "ANSWER_QUESTION_MISMATCH",
            None,
            False,
            False,
            fields,
            ("answer.question_id",),
        )
    arrived = answer.get("arrived_at_monotonic_s")
    if not isinstance(arrived, (int, float)) or isinstance(arrived, bool):
        return AnswerResult(
            answer.get("classification", "UNKNOWN"),
            False,
            "ANSWER_TIME_INVALID",
            None,
            False,
            False,
            fields,
            ("answer.arrived_at_monotonic_s",),
        )
    # 与 cache 一致采用严格边界：等于任一截止时间即视为过期。
    if arrived >= answer_deadline_s or arrived >= interpretation_valid_until_s:
        return AnswerResult(
            answer.get("classification", "UNKNOWN"),
            False,
            "STALE_ANSWER",
            None,
            False,
            bool(answer.get("oracle_mismatch", False)),
            fields,
            (),
        )

    classification = answer.get("classification")
    # 改变指令会开启全新 episode，而不是给旧候选换标签。
    if classification == "CHANGED_INSTRUCTION":
        if not answer.get("new_instruction_id"):
            return AnswerResult(
                classification,
                False,
                "INVALID_CHANGED_INSTRUCTION",
                None,
                False,
                False,
                fields,
                ("answer.new_instruction_id",),
            )
        return AnswerResult(
            classification,
            True,
            "ANSWER_CHANGED_INSTRUCTION",
            None,
            True,
            False,
            fields,
            (),
        )
    if classification == "STILL_AMBIGUOUS":
        return AnswerResult(
            classification,
            False,
            "ANSWER_STILL_AMBIGUOUS",
            None,
            False,
            False,
            fields,
            (),
        )
    if classification != "VALID_SELECTION":
        return AnswerResult(
            classification or "INVALID",
            False,
            "INVALID_ANSWER",
            None,
            False,
            False,
            fields,
            (),
        )

    selected = answer.get("selected_candidate_id")
    if selected not in valid_candidate_ids:
        return AnswerResult(
            classification,
            False,
            "ANSWER_SELECTION_INVALID",
            selected,
            False,
            bool(answer.get("oracle_mismatch", False)),
            fields,
            ("answer.selected_candidate_id",),
        )
    return AnswerResult(
        classification,
        True,
        "ANSWER_ACCEPTED_FOR_REVALIDATION",
        selected,
        False,
        bool(answer.get("oracle_mismatch", False)),
        fields,
        (),
    )
