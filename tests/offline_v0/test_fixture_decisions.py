from __future__ import annotations

import pytest

from driveclarify_offline.evaluator import evaluate_input
from driveclarify_offline.fixtures import load_fixture_document, resolve_case


DOCUMENT = load_fixture_document()
CASES = DOCUMENT["cases"]
REQUIRED_OUTPUT_FIELDS = {
    "decision",
    "reason_code",
    "current_state",
    "next_state",
    "fields_used",
    "unknown_fields",
    "cache_status",
    "timing_status",
    "question_dispatch",
    "holding_required",
    "holding_summary",
    "trace",
}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["fixture_id"])
def test_hand_authored_fixture_matches_frozen_expected(case):
    resolved = resolve_case(DOCUMENT, case)
    actual = evaluate_input(resolved).to_dict()
    assert actual["decision"] == case["expected"]["decision"]
    assert actual["reason_code"] == case["expected"]["reason_code"]
    assert actual["next_state"] == case["expected"]["state"]
    assert REQUIRED_OUTPUT_FIELDS <= actual.keys()
    assert actual["trace"]
    assert [step["index"] for step in actual["trace"]] == list(
        range(1, len(actual["trace"]) + 1)
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["fixture_id"])
def test_every_expected_label_is_preregistered(case):
    assert case["label_author"] == "DRIVECLARIFY_V0_1_DESIGN_CONTRACT"
    assert case["label_basis"] == "HAND_AUTHORED_FROM_FROZEN_RULE_TABLE"
    assert case["reviewed_before_execution"] is True


def test_k3_is_explicitly_outside_core_v0(resolved_cases):
    _, resolved = resolved_cases["K3_SENSITIVITY_OUTSIDE_CORE"]
    assert len(resolved["consequences"]) == 3
    assert resolved["diagnostics"]["core_v0"] is False
    assert resolved["diagnostics"]["scope"] == "OUTSIDE_CORE_V0"


def test_ask_and_wait_output_contract(resolved_cases):
    for fixture_id in ("TIMING_GT_3_60", "TIMING_EQ_3_50", "WAIT_AFTER_QUESTION"):
        _, resolved = resolved_cases[fixture_id]
        record = evaluate_input(resolved)
        assert record.holding_required is True
        assert record.holding_summary is not None
        assert record.holding_summary.holding_id == resolved["holding"]["holding_id"]
        if record.decision.value == "ASK":
            assert record.question_dispatch == "SEND_QUESTION"
        else:
            assert record.decision.value == "WAIT"
            assert record.question_dispatch == "NONE"


def test_fallback_never_claims_holding_or_question_dispatch(resolved_cases):
    for _, resolved in resolved_cases.values():
        record = evaluate_input(resolved)
        if record.decision.value == "FALLBACK":
            assert record.question_dispatch == "NONE"
            assert record.holding_required is False
            assert record.holding_summary is None


def test_act_never_claims_holding(resolved_cases):
    for _, resolved in resolved_cases.values():
        record = evaluate_input(resolved)
        if record.decision.value == "ACT":
            assert record.holding_required is False
            assert record.holding_summary is None
