from __future__ import annotations

import pytest

from driveclarify_offline.fixtures import load_fixture_document, resolve_case


@pytest.fixture(scope="session")
def fixture_document():
    return load_fixture_document()


@pytest.fixture(scope="session")
def resolved_cases(fixture_document):
    return {
        case["fixture_id"]: (case, resolve_case(fixture_document, case))
        for case in fixture_document["cases"]
    }
