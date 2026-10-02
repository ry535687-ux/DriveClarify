"""结构化模板问题检查；本模块明确不包含自由文本解析。"""

from __future__ import annotations

from typing import Any

from .models import QuestionCheck, TriValue


ALLOWED_SLOTS = {"maneuver", "lane", "target_object", "timing", "distance", "speed"}


def check_template_question(
    question: dict[str, Any] | None,
    *,
    episode_id: str,
    candidate_ids: list[str],
    history_signatures: list[str],
) -> QuestionCheck:
    """检查问题相关性、防重复和固定选项可回答性。"""

    fields = (
        "question.episode_id",
        "question.contrasted_slot",
        "question.candidate_ids",
        "question.options",
        "question.signature",
        "question.action_changing",
        "interaction_history.question_signatures",
    )
    if question is None:
        return QuestionCheck(
            TriValue.UNKNOWN,
            TriValue.UNKNOWN,
            TriValue.UNKNOWN,
            "QUESTION_MISSING",
            None,
            fields,
            ("question",),
        )
    required = {
        "episode_id",
        "contrasted_slot",
        "candidate_ids",
        "options",
        "signature",
        "action_changing",
    }
    missing = sorted(required - question.keys())
    if missing:
        return QuestionCheck(
            TriValue.UNKNOWN,
            TriValue.UNKNOWN,
            TriValue.UNKNOWN,
            "QUESTION_FIELDS_UNKNOWN",
            question.get("signature"),
            fields,
            tuple(f"question.{name}" for name in missing),
        )

    # 相关性只读预先落地的槽位和候选顺序，不从自然语言猜测。
    relevant = (
        TriValue.TRUE
        if question["episode_id"] == episode_id
        and question["contrasted_slot"] in ALLOWED_SLOTS
        and question["action_changing"] is True
        and question["candidate_ids"] == candidate_ids
        else TriValue.FALSE
    )
    signature = question["signature"]
    # v0 的预算属于整个 episode；任意成功问题历史都会关闭后续语义提问，
    # 不能靠换一个 signature 绕过一次提问限制。
    non_redundant = TriValue.FALSE if history_signatures else TriValue.TRUE
    options = question["options"]
    answerable = (
        TriValue.TRUE
        if isinstance(options, list)
        and len(options) == 2
        and all(isinstance(option, str) and option.strip() for option in options)
        and options[0] != options[1]
        else TriValue.FALSE
    )
    if relevant is not TriValue.TRUE:
        reason = "QUESTION_NOT_RELEVANT"
    elif non_redundant is not TriValue.TRUE:
        reason = "QUESTION_REDUNDANT"
    elif answerable is not TriValue.TRUE:
        reason = "QUESTION_NOT_ANSWERABLE"
    else:
        reason = "QUESTION_ELIGIBLE"
    return QuestionCheck(
        relevant,
        non_redundant,
        answerable,
        reason,
        signature,
        fields,
        (),
    )
