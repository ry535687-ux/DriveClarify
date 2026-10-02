"""确定性执行 fixtures 并生成机器可读结果。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .evaluator import evaluate_input
from .fixtures import DEFAULT_FIXTURE_PATH, iter_resolved_cases
from .models import PolicyState
from .state_machine import TRANSITIONS, reduce_state


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRANSITION_CONTRACT_PATH = (
    PROJECT_ROOT
    / "tests/offline_v0/contracts/expected_state_transitions_v0_1.json"
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _transition_dict(item: tuple[str, str, str]) -> dict[str, str]:
    source, event, target = item
    return {"from": source, "event": event, "to": target}


def _build_state_coverage(
    results: list[dict[str, Any]],
    fixture_transitions: Counter[tuple[str, str]],
) -> dict[str, Any]:
    """从独立冻结合同执行 reducer 校验，不把实现声明冒充测试覆盖。"""

    contract = json.loads(TRANSITION_CONTRACT_PATH.read_text(encoding="utf-8"))
    expected_order = [
        (case["from_state"], case["event"], case["to_state"])
        for case in contract["transitions"]
    ]
    expected = set(expected_order)
    implemented_order = [
        (source.value, event, target.value)
        for (source, event), target in sorted(
            TRANSITIONS.items(),
            key=lambda item: (item[0][0].value, item[0][1], item[1].value),
        )
    ]
    implemented = set(implemented_order)

    verified: list[tuple[str, str, str]] = []
    verification_failures: list[dict[str, str]] = []
    for source, event, target in expected_order:
        try:
            actual = reduce_state(source, event).value
        except ValueError as exc:
            verification_failures.append(
                {
                    "from": source,
                    "event": event,
                    "expected_to": target,
                    "actual": str(exc),
                }
            )
            continue
        if actual == target:
            verified.append((source, event, target))
        else:
            verification_failures.append(
                {
                    "from": source,
                    "event": event,
                    "expected_to": target,
                    "actual": actual,
                }
            )

    contract_states = list(contract["seven_state_contract"])
    implemented_states = {state.value for state in PolicyState}
    seven_states_verified = [
        state for state in contract_states if state in implemented_states
    ]
    fallback_states_verified: list[str] = []
    for case in contract["global_fallback_cases"]:
        try:
            actual = reduce_state(case["from_state"], case["event"]).value
        except ValueError:
            continue
        if actual == case["to_state"]:
            fallback_states_verified.append(case["from_state"])

    fixture_states = sorted(
        {
            state
            for item in results
            for state in (
                item["actual"]["current_state"],
                item["actual"]["next_state"],
            )
        }
    )
    missing = sorted(expected - implemented)
    unexpected = sorted(implemented - expected)
    contract_match = (
        not missing
        and not unexpected
        and not verification_failures
        and len(verified) == contract["expected_transition_count"]
        and seven_states_verified == contract_states
        and fallback_states_verified == contract_states
    )

    return {
        "result_schema_version": "driveclarify.state_coverage.v0.2",
        "contract_source": str(TRANSITION_CONTRACT_PATH.relative_to(PROJECT_ROOT)),
        "expected_transition_count": contract["expected_transition_count"],
        "implemented_transition_count": len(implemented),
        "verified_transition_count": len(verified),
        "missing_transitions": [_transition_dict(item) for item in missing],
        "unexpected_transitions": [_transition_dict(item) for item in unexpected],
        "verification_failures": verification_failures,
        "contract_match": contract_match,
        "fixture_states_observed": fixture_states,
        "fixture_transitions_observed": [
            {"from": source, "to": target, "count": count}
            for (source, target), count in sorted(fixture_transitions.items())
        ],
        "seven_states_verified_by_independent_tests": seven_states_verified,
        "fallback_states_verified": fallback_states_verified,
        "independently_verified_transitions": [
            _transition_dict(item) for item in verified
        ],
        "implementation_declared_transitions": [
            _transition_dict(item) for item in implemented_order
        ],
    }


def run_fixtures(
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """逐条比较人工 expected 与 actual，并输出稳定排序的 JSON。"""

    results = []
    decision_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    transitions: Counter[tuple[str, str]] = Counter()

    for definition, resolved_input in iter_resolved_cases(fixture_path):
        record = evaluate_input(resolved_input)
        actual = record.to_dict()
        expected = definition["expected"]
        # expected 是只读输入；这里只做比较，从不覆盖或建议新标签。
        passed = (
            actual["decision"] == expected["decision"]
            and actual["reason_code"] == expected["reason_code"]
            and actual["next_state"] == expected["state"]
        )
        results.append(
            {
                "fixture_id": definition["fixture_id"],
                "kind": definition.get("kind", "policy"),
                "synthetic_provenance": resolved_input["synthetic_provenance"],
                "label_author": definition["label_author"],
                "label_basis": definition["label_basis"],
                "reviewed_before_execution": definition["reviewed_before_execution"],
                "expected": expected,
                "actual": actual,
                "passed": passed,
            }
        )
        decision_counts[actual["decision"]] += 1
        reason_counts[actual["reason_code"]] += 1
        transitions[(actual["current_state"], actual["next_state"])] += 1

    total = len(results)
    passed_count = sum(item["passed"] for item in results)
    fixture_results = {
        "result_schema_version": "driveclarify.fixture_results.v0.1",
        "status": "PASS" if passed_count == total else "FAIL",
        "total": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "results": results,
    }
    decision_summary = {
        "result_schema_version": "driveclarify.decision_summary.v0.1",
        "status": fixture_results["status"],
        "total_fixtures": total,
        "passed_fixtures": passed_count,
        "decision_counts": dict(sorted(decision_counts.items())),
        "fallback_reason_counts": {
            reason: count
            for reason, count in sorted(reason_counts.items())
            if any(
                item["actual"]["decision"] == "FALLBACK"
                and item["actual"]["reason_code"] == reason
                for item in results
            )
        },
        "all_reason_counts": dict(sorted(reason_counts.items())),
    }

    state_coverage = _build_state_coverage(results, transitions)

    if output_dir is not None:
        _write_json(output_dir / "fixture_results.json", fixture_results)
        _write_json(output_dir / "decision_summary.json", decision_summary)
        _write_json(output_dir / "state_transition_coverage.json", state_coverage)

    return {
        "fixture_results": fixture_results,
        "decision_summary": decision_summary,
        "state_transition_coverage": state_coverage,
    }
