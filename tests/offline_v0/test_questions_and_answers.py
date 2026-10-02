from __future__ import annotations

from copy import deepcopy

from driveclarify_offline.answers import validate_answer
from driveclarify_offline.models import PolicyState, TriValue
from driveclarify_offline.questions import check_template_question


def test_template_question_relevance_redundancy_answerability(fixture_document):
    question = fixture_document["base_policy_input"]["question"]
    result = check_template_question(
        question,
        episode_id="episode-001",
        candidate_ids=["z1", "z2"],
        history_signatures=[],
    )
    assert result.relevant is TriValue.TRUE
    assert result.non_redundant is TriValue.TRUE
    assert result.answerable is TriValue.TRUE
    assert result.allowed is True


def test_repeated_question_is_rejected(fixture_document):
    question = fixture_document["base_policy_input"]["question"]
    result = check_template_question(
        question,
        episode_id="episode-001",
        candidate_ids=["z1", "z2"],
        history_signatures=[question["signature"]],
    )
    assert result.non_redundant is TriValue.FALSE
    assert result.reason_code == "QUESTION_REDUNDANT"


def test_question_with_duplicate_options_is_not_answerable(fixture_document):
    question = deepcopy(fixture_document["base_policy_input"]["question"])
    question["options"] = ["LEFT", "LEFT"]
    result = check_template_question(
        question,
        episode_id="episode-001",
        candidate_ids=["z1", "z2"],
        history_signatures=[],
    )
    assert result.answerable is TriValue.FALSE


def test_answer_at_deadline_is_stale():
    answer = {
        "episode_id": "ep",
        "question_id": "q",
        "arrived_at_monotonic_s": 5.0,
        "classification": "VALID_SELECTION",
        "selected_candidate_id": "z1",
    }
    result = validate_answer(
        answer,
        active_episode_id="ep",
        active_question_id="q",
        answer_deadline_s=5.0,
        interpretation_valid_until_s=6.0,
        current_state=PolicyState.HOLDING,
        valid_candidate_ids=["z1", "z2"],
    )
    assert result.accepted is False
    assert result.reason_code == "STALE_ANSWER"


def test_wrong_oracle_answer_is_runtime_valid_but_logged():
    answer = {
        "episode_id": "ep",
        "question_id": "q",
        "arrived_at_monotonic_s": 4.0,
        "classification": "VALID_SELECTION",
        "selected_candidate_id": "z2",
        "oracle_mismatch": True,
    }
    result = validate_answer(
        answer,
        active_episode_id="ep",
        active_question_id="q",
        answer_deadline_s=5.0,
        interpretation_valid_until_s=6.0,
        current_state=PolicyState.HOLDING,
        valid_candidate_ids=["z1", "z2"],
    )
    assert result.accepted is True
    assert result.oracle_mismatch is True


def test_committed_late_answer_never_reopens_episode():
    answer = {
        "episode_id": "ep",
        "question_id": "q",
        "arrived_at_monotonic_s": 4.0,
        "classification": "VALID_SELECTION",
        "selected_candidate_id": "z2",
    }
    result = validate_answer(
        answer,
        active_episode_id="ep",
        active_question_id="q",
        answer_deadline_s=5.0,
        interpretation_valid_until_s=6.0,
        current_state=PolicyState.COMMITTED,
        valid_candidate_ids=["z1", "z2"],
    )
    assert result.reason_code == "LATE_ANSWER_IGNORED"
    assert result.opens_new_episode is False
