from __future__ import annotations

from copy import deepcopy

import pytest

from driveclarify_offline.cache import (
    check_cache,
    derive_plan_age,
    map_slow_trigger,
    validate_cache_cross_fields,
    validate_cache_links,
)
from driveclarify_offline.models import TriValue


def _cache(fixture_document):
    cache = deepcopy(fixture_document["base_policy_input"]["candidate_cache"])
    cache["candidate_ids"] = ["z1"]
    return cache


def test_plan_age_uses_monotonic_time(fixture_document):
    cache = _cache(fixture_document)
    assert derive_plan_age(cache, 10.5) == 0.5


@pytest.mark.parametrize(
    ("now", "status", "executable", "reason"),
    [
        (11.999, "VALID", TriValue.TRUE, "CACHE_EXECUTABLE"),
        (12.0, "VALID", TriValue.FALSE, "CACHE_EXPIRED"),
        (10.5, "EXPIRED", TriValue.FALSE, "CACHE_EXPIRED"),
        (10.5, "INVALIDATED", TriValue.FALSE, "CACHE_INVALIDATED"),
        (10.5, "UNKNOWN", TriValue.UNKNOWN, "UNKNOWN_CACHE_VALIDITY"),
    ],
)
def test_cache_execution_gate(fixture_document, now, status, executable, reason):
    cache = _cache(fixture_document)
    cache["validity_status"] = status
    cache["plan_age_s"] = now - 10.0
    result = check_cache(cache, now, "episode-001")
    assert result.executable is executable
    assert result.reason_code == reason


def test_cache_episode_mismatch(fixture_document):
    cache = _cache(fixture_document)
    result = check_cache(cache, 10.5, "episode-other")
    assert result.reason_code == "CACHE_EPISODE_MISMATCH"


def test_cache_plan_age_mismatch_fails(fixture_document):
    cache = _cache(fixture_document)
    cache["plan_age_s"] = 0.4
    with pytest.raises(ValueError, match="CACHE_PLAN_AGE_MISMATCH"):
        validate_cache_cross_fields(cache, 10.5)


def test_cache_validity_interval_must_be_strict(fixture_document):
    cache = _cache(fixture_document)
    cache["valid_until_monotonic_s"] = cache["generated_at_monotonic_s"]
    with pytest.raises(ValueError, match="CACHE_INVALID_VALIDITY_INTERVAL"):
        validate_cache_cross_fields(cache, 10.5)


@pytest.mark.parametrize(
    ("trigger", "reason"),
    [
        ("EPISODE_OPEN", "EPISODE_OPEN"),
        ("CANDIDATE_EXPIRED", "CANDIDATE_EXPIRED"),
        ("MATERIAL_SCENE_CHANGE", "MATERIAL_SCENE_CHANGE"),
        ("TIMELY_ANSWER_RECEIVED", "ANSWER_RECEIVED"),
        ("CANDIDATE_INVALIDATED", "PLAN_INVALIDATED"),
        ("EXPLICIT_REFRESH_REQUEST", "MANUAL_TEST_REFRESH"),
    ],
)
def test_slow_trigger_refresh_reason_mapping(trigger, reason):
    assert map_slow_trigger(trigger) == reason


def test_illegal_slow_trigger_rejected():
    with pytest.raises(ValueError, match="ILLEGAL_REFRESH_TRIGGER"):
        map_slow_trigger("EVERY_TICK")


def test_cache_links_detect_mismatch(resolved_cases):
    _, resolved = resolved_cases["CACHE_LINK_MISMATCH"]
    valid, mismatches = validate_cache_links(
        resolved["candidate_cache"], resolved["consequences"]
    )
    assert valid is False
    assert "consequences[0].cache_id" in mismatches
