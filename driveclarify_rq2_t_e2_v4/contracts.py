"""Prospective typed contracts for E2_V4_DOMAIN_INVARIANT_ASSOCIATION."""

from __future__ import annotations

from typing import Any, Mapping

from driveclarify_rq2_t_e2_v3.contracts import (  # preserved parser/schema lineage
    AcquisitionSchedule,
    CandidateObjectSpec,
    UNKNOWN,
    UNSPECIFIED,
    assert_no_forbidden_candidate_keys,
    canonical_sha256,
    parse_certified_candidate,
    validate_candidate_set,
)


METHOD_ID = "E2_V4_DOMAIN_INVARIANT_ASSOCIATION"
SEMANTIC_FAMILIES = (
    "REFERENTIAL",
    "LANDMARK",
    "ORDER",
    "UNDERSPECIFIED_CONSTRAINT",
)


def certified_semantic_family(value: str) -> str:
    family = str(value).strip().upper()
    if family not in SEMANTIC_FAMILIES:
        raise ValueError("E2_V4_UNCERTIFIED_SEMANTIC_FAMILY:" + family)
    return family


def assert_family_selection_is_scene_independent(
    family: str, *, scene_id: str | None = None, route_id: str | None = None
) -> str:
    """Family selection accepts only the certified semantic type.

    Scene and route identifiers are accepted for audit call sites but are
    intentionally neither inspected nor returned.
    """

    del scene_id, route_id
    return certified_semantic_family(family)


def assert_no_runtime_oracle(value: Any, path: str = "runtime") -> None:
    assert_no_forbidden_candidate_keys(value, path)
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in {"actor_id", "carla_actor_id", "reveal_frame", "reveal_time"}:
                raise PermissionError("E2_V4_RUNTIME_ORACLE_KEY_FORBIDDEN:" + path + "." + str(key))
            assert_no_runtime_oracle(child, path + "." + str(key))
    elif isinstance(value, (tuple, list)):
        for index, child in enumerate(value):
            assert_no_runtime_oracle(child, path + "[{}]".format(index))


__all__ = [
    "AcquisitionSchedule",
    "CandidateObjectSpec",
    "METHOD_ID",
    "SEMANTIC_FAMILIES",
    "UNKNOWN",
    "UNSPECIFIED",
    "assert_family_selection_is_scene_independent",
    "assert_no_runtime_oracle",
    "canonical_sha256",
    "certified_semantic_family",
    "parse_certified_candidate",
    "validate_candidate_set",
]
