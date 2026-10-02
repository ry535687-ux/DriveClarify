from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from driveclarify_offline.evaluator import evaluate_input
from driveclarify_offline.models import AskTimingStatus
from driveclarify_offline.timing import compute_ask_timing_status


CONTRACT_PATH = (
    Path(__file__).resolve().parent
    / "contracts"
    / "fix_gate_expected_cases.json"
)
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
BASE_TIMING = {
    "time_to_decision_s": 3.6,
    "branch_forward_latency_s": 1.25,
    "question_latency_s": 0.25,
    "estimated_response_latency_s": 1.0,
    "replanning_latency_s": 0.5,
    "safety_margin_s": 0.5,
}


def _assert_frozen_label(case: dict[str, object]) -> None:
    """证明本轮 expected 是实现修改前人工冻结的字面合同。"""

    assert case["label_basis"] == "HAND_AUTHORED_BEFORE_FIX_IMPLEMENTATION"
    assert case["reviewed_before_execution"] is True
    assert case["fix_gate_id"]
    assert case["contract_reference"]


@pytest.mark.parametrize(
    "case",
    CONTRACT["timing_cases"],
    ids=lambda case: case["fix_gate_id"],
)
def test_fix_gate_timing_metadata_is_fail_closed(case):
    _assert_frozen_label(case)
    metadata = None if case["metadata_mode"] == "ABSENT" else deepcopy(case["metadata"])
    result = compute_ask_timing_status(deepcopy(BASE_TIMING), metadata)
    assert result.status is AskTimingStatus(case["expected_status"])
    assert result.reason_code == case["expected_reason_code"]


@pytest.mark.parametrize(
    "case",
    CONTRACT["question_cases"],
    ids=lambda case: case["fix_gate_id"],
)
def test_fix_gate_one_successful_question_per_episode(case, resolved_cases):
    _assert_frozen_label(case)
    _, base_input = resolved_cases[case["base_fixture_id"]]
    case_input = deepcopy(base_input)
    case_input["history_signatures"] = deepcopy(case["history_signatures"])
    case_input["query_budget_remaining"] = case["query_budget_remaining"]
    case_input["pending_question"] = case["pending_question"]

    actual = evaluate_input(case_input).to_dict()
    assert actual["decision"] == case["expected_decision"]
    assert actual["reason_code"] == case["expected_reason_code"]
    assert actual["question_dispatch"] == case["expected_question_dispatch"]

    expected_trace_fields = case.get("expected_trace_fields")
    if expected_trace_fields:
        ask_steps = [step for step in actual["trace"] if step["gate"] == "ask_gate"]
        assert len(ask_steps) == 1
        for field, expected in expected_trace_fields.items():
            assert ask_steps[0][field] == expected


@pytest.mark.parametrize(
    "case",
    CONTRACT["holding_cases"],
    ids=lambda case: case["fix_gate_id"],
)
def test_fix_gate_decision_holding_binding(case, resolved_cases):
    _assert_frozen_label(case)
    _, case_input = resolved_cases[case["base_fixture_id"]]
    actual = evaluate_input(deepcopy(case_input)).to_dict()

    assert actual["decision"] == case["expected_decision"]
    assert actual["holding_required"] is case["expected_holding_required"]
    summary = actual["holding_summary"]
    assert (summary is not None) is case["expected_holding_summary_present"]
    if summary is not None:
        assert summary["holding_id"] == case["expected_holding_id"]
        assert summary["holding_id"] == case_input["holding"]["holding_id"]
        assert set(summary) == {
            "holding_id",
            "holding_type",
            "version",
            "validity_status",
            "deadline_status",
            "valid_until_monotonic_s",
            "hard_feasible",
            "can_extend_or_preserve",
            "provenance",
        }
    else:
        assert case["expected_holding_id"] is None
