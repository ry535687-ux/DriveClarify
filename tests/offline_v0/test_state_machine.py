from __future__ import annotations

import json
from pathlib import Path

import pytest

from driveclarify_offline.models import CommitReentryCause, PolicyState
from driveclarify_offline.state_machine import (
    LEGAL_SLOW_TRIGGERS,
    TRANSITIONS,
    derive_commit_reentry_cause,
    reduce_committed_reentry,
    reduce_state,
    validate_slow_trigger,
)


CONTRACT_PATH = (
    Path(__file__).resolve().parent
    / "contracts"
    / "expected_state_transitions_v0_1.json"
)
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _assert_frozen_label(case):
    assert case["label_basis"] == "HAND_AUTHORED_BEFORE_FIX_IMPLEMENTATION"
    assert case["reviewed_before_execution"] is True
    assert case["fix_gate_id"]
    assert case["contract_reference"]


@pytest.mark.parametrize(
    "case",
    CONTRACT["transitions"],
    ids=lambda case: case["fix_gate_id"],
)
def test_independently_frozen_reducer_transitions(case):
    _assert_frozen_label(case)
    assert (
        reduce_state(case["from_state"], case["event"]).value
        == case["to_state"]
    )


def test_implementation_transition_table_exactly_matches_independent_contract():
    expected = {
        (case["from_state"], case["event"], case["to_state"])
        for case in CONTRACT["transitions"]
    }
    implemented = {
        (source.value, event, target.value)
        for (source, event), target in TRANSITIONS.items()
    }
    assert len(expected) == CONTRACT["expected_transition_count"] == 11
    assert implemented == expected


@pytest.mark.parametrize(
    "case",
    CONTRACT["global_fallback_cases"],
    ids=lambda case: case["fix_gate_id"],
)
def test_every_state_has_global_fallback(case):
    _assert_frozen_label(case)
    assert reduce_state(case["from_state"], case["event"]).value == case["to_state"]


def test_all_seven_states_are_present():
    assert {state.value for state in PolicyState} == set(
        CONTRACT["seven_state_contract"]
    )


@pytest.mark.parametrize(
    "case",
    CONTRACT["illegal_transition_cases"],
    ids=lambda case: case["fix_gate_id"],
)
def test_illegal_transition_is_rejected(case):
    _assert_frozen_label(case)
    with pytest.raises(ValueError, match=case["expected_error_prefix"]):
        reduce_state(case["from_state"], case["event"])


@pytest.mark.parametrize("trigger_type", sorted(LEGAL_SLOW_TRIGGERS))
def test_each_legal_slow_trigger_requires_complete_metadata(trigger_type):
    trigger = {
        "type": trigger_type,
        "episode_id": "ep",
        "monotonic_time_s": 1.0,
        "source": "SYNTHETIC_TEST_ONLY",
        "provenance": "SYNTHETIC_TEST_ONLY",
        "reason_code": "TEST_TRIGGER",
    }
    assert validate_slow_trigger(trigger, "ep") == "VALID_REFRESH_TRIGGER"


def test_every_tick_is_not_a_legal_slow_trigger():
    trigger = {
        "type": "EVERY_TICK",
        "episode_id": "ep",
        "monotonic_time_s": 1.0,
        "source": "test",
        "provenance": "test",
        "reason_code": "test",
    }
    assert validate_slow_trigger(trigger, "ep") == "ILLEGAL_REFRESH_TRIGGER"


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            {"new_instruction_id": "i2", "instruction_valid": True},
            CommitReentryCause.NEW_INSTRUCTION,
        ),
        (
            {
                "new_ambiguity": True,
                "detector": "d",
                "provenance": "p",
                "reason_code": "r",
            },
            CommitReentryCause.NEW_AMBIGUITY,
        ),
        (
            {"material_invalidation": True, "invalidation_reason": "x"},
            CommitReentryCause.MATERIAL_INVALIDATION,
        ),
        ({"late_answer": True}, CommitReentryCause.NONE),
        ({"cosmetic_paraphrase": True}, CommitReentryCause.NONE),
        ({"ordinary_scene_update": True}, CommitReentryCause.NONE),
    ],
)
def test_commit_reentry_causes(event, expected):
    assert derive_commit_reentry_cause(event) is expected


def test_new_ambiguity_without_provenance_is_none():
    assert (
        derive_commit_reentry_cause({"new_ambiguity": True})
        is CommitReentryCause.NONE
    )


def test_none_reentry_preserves_cache_and_budget():
    state, clears_cache, resets_budget = reduce_committed_reentry(
        CommitReentryCause.NONE
    )
    assert state is PolicyState.COMMITTED
    assert clears_cache is False
    assert resets_budget is False


@pytest.mark.parametrize(
    "cause",
    [
        CommitReentryCause.NEW_INSTRUCTION,
        CommitReentryCause.NEW_AMBIGUITY,
        CommitReentryCause.MATERIAL_INVALIDATION,
    ],
)
def test_legal_reentry_clears_cache_and_resets_new_episode_budget(cause):
    state, clears_cache, resets_budget = reduce_committed_reentry(cause)
    assert state is PolicyState.AMBIGUITY_ACTIVE
    assert clears_cache is True
    assert resets_budget is True
