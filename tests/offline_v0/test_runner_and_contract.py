from __future__ import annotations

import json
from pathlib import Path

from driveclarify_offline.runner import run_fixtures


TRANSITION_CONTRACT_PATH = (
    Path(__file__).resolve().parent
    / "contracts"
    / "expected_state_transitions_v0_1.json"
)
TRANSITION_CONTRACT = json.loads(
    TRANSITION_CONTRACT_PATH.read_text(encoding="utf-8")
)


def test_machine_readable_results_are_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    one = run_fixtures(output_dir=first)
    two = run_fixtures(output_dir=second)
    assert one == two
    for name in (
        "fixture_results.json",
        "decision_summary.json",
        "state_transition_coverage.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
        json.loads((first / name).read_text(encoding="utf-8"))


def test_all_fixture_results_pass(tmp_path):
    output = run_fixtures(output_dir=tmp_path)
    results = output["fixture_results"]
    assert results["status"] == "PASS"
    assert results["passed"] == results["total"]
    assert results["failed"] == 0


def test_state_coverage_is_computed_from_independent_contract(tmp_path):
    coverage = run_fixtures(output_dir=tmp_path)["state_transition_coverage"]
    expected_count = TRANSITION_CONTRACT["expected_transition_count"]

    assert coverage["expected_transition_count"] == expected_count
    assert coverage["implemented_transition_count"] == expected_count
    assert coverage["verified_transition_count"] == expected_count
    assert coverage["missing_transitions"] == []
    assert coverage["unexpected_transitions"] == []
    assert coverage["contract_match"] is True
    assert coverage["seven_states_verified_by_independent_tests"] == (
        TRANSITION_CONTRACT["seven_state_contract"]
    )
    assert coverage["fallback_states_verified"] == (
        TRANSITION_CONTRACT["seven_state_contract"]
    )
    assert "fixture_states_observed" in coverage
    assert "fixture_transitions_observed" in coverage
    assert "implementation_declared_transitions" in coverage
    assert "reducer_transitions_exercised_by_pytest" not in coverage
    assert "global_fallback_from_every_state_exercised_by_pytest" not in coverage
    assert "all_seven_states_covered_by_pytest" not in coverage


def test_no_scalar_weighted_score_implementation():
    package = Path(__file__).resolve().parents[2] / "driveclarify_offline"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package.glob("*.py"))
        if path.name != "fixtures.py"
    )
    forbidden = "weighted" + "_total_score"
    assert forbidden not in source


def test_no_live_integration_imports():
    package = Path(__file__).resolve().parents[2] / "driveclarify_offline"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))
    )
    assert "import carla" not in source.lower()
    assert "import torch" not in source.lower()
    assert "simlingo" not in source.lower()
    assert "control_pid" not in source
