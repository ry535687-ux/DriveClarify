from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from driveclarify_offline.evaluator import evaluate_input


CONTRACT_PATH = (
    Path(__file__).resolve().parent
    / "contracts"
    / "f2_early_act_invariant_expected.json"
)
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _assert_frozen_case(case: dict[str, object]) -> None:
    """确认 expected 是 F2 实现修改前人工冻结的字面合同。"""

    assert case["fix_gate_id"] == "F2_EARLY_ACT_INVARIANT"
    assert case["label_basis"] == "HAND_AUTHORED_BEFORE_F2_CLOSURE"
    assert case["reviewed_before_execution"] is True
    assert case["case_id"]
    assert case["contract_reference"]


@pytest.mark.parametrize(
    "case",
    CONTRACT["cases"],
    ids=lambda case: case["case_id"],
)
def test_f2_query_episode_invariant_precedes_early_act(case, resolved_cases):
    """用独立 expected 检查所有 early-ACT 分支之前的 episode 不变量。"""

    _assert_frozen_case(case)
    _, base_input = resolved_cases[case["base_fixture_id"]]
    case_input = deepcopy(base_input)
    for field, value in case["input_overrides"].items():
        case_input[field] = deepcopy(value)

    actual = evaluate_input(case_input).to_dict()
    expected = case["expected"]
    assert actual["decision"] == expected["decision"]
    assert actual["reason_code"] == expected["reason_code"]
    assert actual["next_state"] == expected["state"]
    assert actual["question_dispatch"] == expected["question_dispatch"]

    query_steps = [
        step for step in actual["trace"] if step["gate"] == "query_episode_invariant"
    ]
    assert len(query_steps) == 1
    assert query_steps[0]["outcome"] == expected["query_gate_outcome"]

    if expected["query_gate_outcome"] == "FAIL":
        # 非法 episode 状态必须在任何语义动作授权前成为终止 gate。
        assert actual["trace"][-1] == query_steps[0]
    else:
        later_gate = expected["query_gate_must_precede"]
        later_steps = [step for step in actual["trace"] if step["gate"] == later_gate]
        assert len(later_steps) == 1
        assert query_steps[0]["index"] < later_steps[0]["index"]
