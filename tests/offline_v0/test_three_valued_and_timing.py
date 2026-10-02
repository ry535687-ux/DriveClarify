from __future__ import annotations

import math

import pytest

from driveclarify_offline.models import AskTimingStatus, TriValue
from driveclarify_offline.three_valued import (
    tri_and,
    tri_from_optional_bool,
    tri_not,
    tri_or,
)
from driveclarify_offline.timing import compute_ask_timing_status


BASE = {
    "time_to_decision_s": 3.6,
    "branch_forward_latency_s": 1.25,
    "question_latency_s": 0.25,
    "estimated_response_latency_s": 1.0,
    "replanning_latency_s": 0.5,
    "safety_margin_s": 0.5,
}
VALID_METADATA = {
    "source": "SYNTHETIC_TEST_ONLY",
    "unit": "s",
    "trusted": True,
    "fresh": True,
}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([TriValue.FALSE, TriValue.FALSE], TriValue.FALSE),
        ([TriValue.FALSE, TriValue.UNKNOWN], TriValue.UNKNOWN),
        ([TriValue.UNKNOWN, TriValue.TRUE], TriValue.TRUE),
    ],
)
def test_explicit_three_valued_or(values, expected):
    assert tri_or(values) is expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([TriValue.TRUE, TriValue.TRUE], TriValue.TRUE),
        ([TriValue.TRUE, TriValue.UNKNOWN], TriValue.UNKNOWN),
        ([TriValue.UNKNOWN, TriValue.FALSE], TriValue.FALSE),
    ],
)
def test_explicit_three_valued_and(values, expected):
    assert tri_and(values) is expected


def test_unknown_is_not_false():
    assert tri_from_optional_bool(None) is TriValue.UNKNOWN
    assert tri_from_optional_bool("UNKNOWN") is TriValue.UNKNOWN
    assert tri_not(TriValue.UNKNOWN) is TriValue.UNKNOWN
    assert TriValue.UNKNOWN is not TriValue.FALSE


@pytest.mark.parametrize(
    ("deadline", "status", "reason", "slack"),
    [
        (3.6, AskTimingStatus.FEASIBLE, "TIMING_FEASIBLE", pytest.approx(0.1)),
        (3.5, AskTimingStatus.INFEASIBLE_KNOWN, "ASK_TOO_LATE_OR_EQUAL", 0.0),
        (3.4, AskTimingStatus.INFEASIBLE_KNOWN, "ASK_TOO_LATE_OR_EQUAL", pytest.approx(-0.1)),
        (None, AskTimingStatus.UNKNOWN, "UNKNOWN_DECISION_DEADLINE", None),
    ],
)
def test_strict_timing_boundaries(deadline, status, reason, slack):
    timing = {**BASE, "time_to_decision_s": deadline}
    result = compute_ask_timing_status(timing, VALID_METADATA)
    assert result.status is status
    assert result.reason_code == reason
    assert result.slack_s == slack
    assert result.t_clarify_s == (None if deadline is None else 3.5)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("time_to_decision_s", "UNKNOWN_DECISION_DEADLINE"),
        ("branch_forward_latency_s", "UNKNOWN_BRANCH_LATENCY"),
        ("question_latency_s", "UNKNOWN_QUESTION_LATENCY"),
        ("estimated_response_latency_s", "UNKNOWN_RESPONSE_LATENCY"),
        ("replanning_latency_s", "UNKNOWN_REPLAN_LATENCY"),
        ("safety_margin_s", "UNKNOWN_SAFETY_MARGIN"),
    ],
)
def test_each_missing_timing_field_has_specific_reason(field, reason):
    timing = {**BASE, field: None}
    result = compute_ask_timing_status(timing, VALID_METADATA)
    assert result.status is AskTimingStatus.UNKNOWN
    assert result.reason_code == reason
    assert result.unknown_fields == (field,)


@pytest.mark.parametrize("bad_value", [-1, math.nan, math.inf, "bad", True])
def test_invalid_timing_is_unknown_not_infeasible(bad_value):
    result = compute_ask_timing_status(
        {**BASE, "question_latency_s": bad_value},
        VALID_METADATA,
    )
    assert result.status is AskTimingStatus.UNKNOWN
    assert result.reason_code == "TIMING_INVALID"


def test_untrusted_and_stale_timing_are_distinct():
    untrusted = compute_ask_timing_status(
        BASE,
        {**VALID_METADATA, "trusted": False},
    )
    stale = compute_ask_timing_status(
        BASE,
        {**VALID_METADATA, "fresh": False},
    )
    assert untrusted.reason_code == "TIMING_SOURCE_UNTRUSTED"
    assert stale.reason_code == "TIMING_STALE"


def test_consequence_latency_is_not_double_counted():
    timing = {**BASE, "consequence_latency_s": 1000.0}
    result = compute_ask_timing_status(timing, VALID_METADATA)
    assert result.t_clarify_s == 3.5
    assert result.status is AskTimingStatus.FEASIBLE
