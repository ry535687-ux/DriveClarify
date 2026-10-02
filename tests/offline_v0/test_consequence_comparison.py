from __future__ import annotations

from driveclarify_offline.consequences import compare_consequences, hard_feasibility
from driveclarify_offline.models import TriValue


def test_large_route_divergence_does_not_create_safety_difference(resolved_cases):
    _, resolved = resolved_cases["EQUIVALENT_LARGE_ROUTE_DIVERGENCE"]
    result = compare_consequences(resolved["consequences"])
    assert resolved["diagnostics"]["route_l2"] == 99.0
    assert result.d_s is TriValue.FALSE
    assert result.critical is TriValue.FALSE
    assert result.equivalent is TriValue.TRUE


def test_small_route_divergence_can_coexist_with_critical_consequence(resolved_cases):
    _, resolved = resolved_cases["CRITICAL_SMALL_ROUTE_DIVERGENCE"]
    result = compare_consequences(resolved["consequences"])
    assert resolved["diagnostics"]["route_l2"] == 0.001
    assert result.d_g is TriValue.TRUE
    assert result.d_i is TriValue.TRUE
    assert result.critical is TriValue.TRUE


def test_known_unsafe_and_severe_rule_are_not_hard_feasible(resolved_cases):
    _, unsafe = resolved_cases["ALL_CANDIDATES_UNSAFE"]
    _, severe = resolved_cases["ALL_CANDIDATES_SEVERE_RULE"]
    assert all(hard_feasibility(item)[0] is TriValue.FALSE for item in unsafe["consequences"])
    assert all(hard_feasibility(item)[0] is TriValue.FALSE for item in severe["consequences"])


def test_unknown_hard_fields_remain_unknown(resolved_cases):
    _, safety = resolved_cases["UNKNOWN_HARD_SAFETY"]
    _, rule = resolved_cases["UNKNOWN_HARD_RULE"]
    assert hard_feasibility(safety["consequences"][0])[0] is TriValue.UNKNOWN
    assert hard_feasibility(rule["consequences"][0])[0] is TriValue.UNKNOWN
