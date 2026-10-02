from __future__ import annotations

from copy import deepcopy

import pytest

from driveclarify_offline.fixtures import load_fixture_document, resolve_case
from driveclarify_offline.schemas import (
    RecordValidationError,
    load_schema,
    validate_candidate_cache,
    validate_consequence,
)
from driveclarify_offline.timing import apply_timing_result, compute_ask_timing_status


def test_frozen_schemas_are_valid_draft_2020_12():
    assert load_schema("candidate_cache")["$schema"].endswith("2020-12/schema")
    assert load_schema("consequence")["$schema"].endswith("2020-12/schema")


def test_resolved_policy_records_are_schema_complete():
    document = load_fixture_document()
    for case in document["cases"]:
        if case.get("kind", "policy") != "policy":
            continue
        resolved = resolve_case(document, case)
        validate_candidate_cache(resolved["candidate_cache"])
        timing = compute_ask_timing_status(
            resolved["timing"], resolved.get("timing_metadata")
        )
        for consequence in resolved["consequences"]:
            consequence = deepcopy(consequence)
            consequence["timing"].update(resolved["timing"])
            apply_timing_result(consequence, timing)
            validate_consequence(consequence)


def test_missing_required_consequence_field_fails_explicitly(fixture_document):
    record = deepcopy(fixture_document["base_consequence"])
    del record["safety"]["safety_class"]
    with pytest.raises(RecordValidationError, match="safety_class"):
        validate_consequence(record)


def test_invalid_additional_consequence_field_fails(fixture_document):
    record = deepcopy(fixture_document["base_consequence"])
    record["weighted_total_score"] = 1.0
    with pytest.raises(RecordValidationError, match="Additional properties"):
        validate_consequence(record)


def test_missing_required_cache_field_fails(fixture_document):
    record = deepcopy(fixture_document["base_policy_input"]["candidate_cache"])
    record["candidate_ids"] = ["z1"]
    del record["valid_until_monotonic_s"]
    with pytest.raises(RecordValidationError, match="valid_until_monotonic_s"):
        validate_candidate_cache(record)


def test_valid_cache_requires_at_least_one_candidate(fixture_document):
    record = deepcopy(fixture_document["base_policy_input"]["candidate_cache"])
    with pytest.raises(RecordValidationError, match="should be non-empty"):
        validate_candidate_cache(record)
